"""Blood Death Knight mitigation.

Mitigation layers pinned to SimC (midnight branch) + Wowhead DBC — see
``docs/validation/phase4_blood_dk_characterization_2026_07_03.md`` (first
characterization, 5 public zone-47 WCL fights):

- **Bone Shield** (195181): bonus armor = **180% of Strength while ≥1
  charge** (SimC ``composite_bonus_armor``: ``ba += buff_value ×
  strength``; the buff's other effects parse ``IGNORE_STACKS``). Stacks
  are *charges* consumed by auto-attacks, not intensity — the armor does
  NOT scale per stack. The old flat 3%/stack physical DR was a
  placeholder mechanism that doesn't exist in Midnight. Measured ≥1-charge
  uptime on the corpus: 97.4–99.9% wall-clock, 99.6–100% damage-weighted
  (avg ~10 stacks) → replay gates on the log's real buff windows
  (``event.active_buffs``); the synthetic sim treats it as always-on.
- **Rune Carved Plates** (Deathbringer hero talent; buffs 440289 physical
  / 440290 magical): 1.5% less damage of that type per stack, max 5;
  physical stacks on runes generated, magical on runes spent. Replay-only
  and gated on the buff windows — a San'layn log has no RCP auras and gets
  no credit (the honest failure mode, per the Brewmaster PT precedent).
  ``active_buffs`` carries presence, not stacks, so the credit is
  per-stack × the measured damage-weighted avg stacks while up (3.5).
- **Blood-Soaked Ground** (San'layn hero talent 434033; buff 434034): flat
  5% less physical damage taken while standing in your own Death and
  Decay — no stacks, no magical branch (unlike RCP). Replay-only and
  gated on the buff window — a Deathbringer log has no Blood-Soaked
  Ground aura and gets no credit, the symmetric gap to RCP's own above.
  San'layn now gets its own hero-talent credit, exactly as Deathbringer
  gets RCP.
- **Death Strike**: heals max(`min_heal_pct × max_hp`, `0.25 ×
  recent_damage_5s`). Fires on a periodic tick when HP drops below a
  threshold (handled in `BloodDKPolicy`). The `recent_damage_window`
  field on `MitigationState` already tracks the rolling 5s sum.
- **Vampiric Blood**: temporary max-HP inflation + healing-received buff
  for 10s on 90s CD — HP-side, does not change damage taken. Reuses the
  Last Stand pattern (`last_stand_until` / `last_stand_base_max_hp`).
- **Icebound Fortitude**: emergency 30% all-school DR for 8s on 120s CD
  (Wowhead 48792). Reuses the `shield_wall_until` slot.
- **Blood Shield / Anti-Magic Shell / Will of the Necropolis**: all
  logged as absorbs → already subtracted via ``log_absorbed`` in replay
  (WotN's DR is implemented as an absorb effect). Synthetic Blood Shield
  feeds the healer-absorb pool from Death Strike heals.

`calibrated: false` — first characterization only. Known deferred gaps:
party_dr_by_school parity (warrior path applies it, this one doesn't),
Foul Bulwark per-stack max HP, synthetic-path RCP, the bleed real-mit
anomaly, COMBATANT_INFO armor-snapshot pollution when Bone Shield is up
at fight start (~2 of 7 probed WCL snapshots — detect/de-pollute is a
named follow-up). The shipped ``calibrate-k --wcl-url`` CLI DOES pass
``buff_ability_ids`` (it collects every spec's ``*_spell_id`` constants
generically — the PR #262 VDH fix shape — so Bone Shield/RCP window-gating
fires there too). **The gap is the LOCAL-log replay path**:
``io/log_replay.load_replay`` never stamps ``event.active_buffs`` at all,
so any local ``.txt`` Blood DK replay credits Bone Shield/RCP ZERO — worse
than the old always-on placeholder. No Blood DK local logs exist in-repo
today, so nothing shipped is wrong yet, but this must be wired (or a
no-buff-window fallback added) before a local Blood DK corpus is trusted.
"""

from __future__ import annotations

import random

from ..core.constants import load_constants
from ..core.events import DamageEvent
from ..core.mitigation import MitigationState


def apply_blood_dk_mitigation(
    state: MitigationState, event: DamageEvent, rng: random.Random
) -> dict:
    """Apply the Blood DK mitigation chain. Follows the structural pattern
    of `apply_protection_paladin_mitigation`: avoidance → armor → vers →
    spec DRs → emergency CDs → absorbs."""
    c = load_constants()
    char = state.character
    spec_cfg = c["specs"]["blood_death_knight"]
    now = event.time_s
    raw = event.raw_amount

    result = {
        "raw": raw,
        "dealt": 0.0,
        "was_avoided": False,
        "was_reflected": False,
        "was_blocked": False,
        "was_crit_blocked": False,
        "absorbed_by_ignore_pain": 0.0,
        "absorbed_by_healer": 0.0,
    }

    # 1. Avoidance (dodge/parry) — physical only. No shield = no block;
    #    DKs trade block for higher base parry from strength scaling.
    if (
        event.is_avoidable
        and event.school == "physical"
        and rng.random() < (char.base_dodge() + char.base_parry())
    ):
        result["was_avoided"] = True
        return result

    damage = raw

    # Bone Shield: bonus armor = 180% of Strength while >=1 charge (SimC
    # midnight composite_bonus_armor; stack-count-independent). In replay
    # it is window-gated on the log's real buff windows; in the synthetic
    # sim the rotational refresh keeps it ~always-on (measured 99.6-100%
    # damage-weighted uptime on the characterization corpus).
    if event.is_log_replay:
        bone_shield_active = spec_cfg["bone_shield_spell_id"] in event.active_buffs
    else:
        bone_shield_active = True

    # 2. Armor DR (physical only) — class-agnostic curve at level 90.
    # Bleeds bypass armor (ROADMAP 3.9.3); skip the curve for them.
    if event.school == "physical" and not event.is_bleed:
        armor = char.total_armor()
        if bone_shield_active:
            armor += spec_cfg["bone_shield_armor_from_strength"] * char.strength
        K = c["armor"]["k_constant"]
        dr = min(armor / (armor + K), c["armor"]["max_armor_dr"])
        damage *= 1 - dr

    # 3. Versatility (all damage)
    damage *= 1 - char.versatility_dr()

    # 4. Rune Carved Plates (Deathbringer): -1.5%/stack (max 5) of the
    #    matching damage type while the school's buff is up. Replay-only:
    #    gated on the log's buff windows so a San'layn build gets no
    #    credit. Presence-gated x measured avg stacks while up (see
    #    constants.yaml). Physical-school bleeds DO benefit (the tooltip
    #    reduces "physical damage taken"; armor bypass is irrelevant here).
    if event.is_log_replay:
        rcp_key = (
            "rune_carved_plates_physical_spell_id"
            if event.school == "physical"
            else "rune_carved_plates_magical_spell_id"
        )
        if spec_cfg[rcp_key] in event.active_buffs:
            rcp_dr = (
                spec_cfg["rune_carved_plates_dr_per_stack"]
                * spec_cfg["rune_carved_plates_avg_stacks_while_up"]
            )
            damage *= 1 - rcp_dr

    # 4b. Blood-Soaked Ground (San'layn): flat -5% physical damage taken
    #     while the buff is up (standing in your own Death and Decay). No
    #     stacks, physical-only — unlike RCP there is no magical branch.
    #     Replay-only: gated on the log's buff window so a Deathbringer
    #     build gets no credit (the symmetric honest gap to RCP's own
    #     San'layn gap above).
    if (
        event.is_log_replay
        and event.school == "physical"
        and spec_cfg["blood_soaked_ground_spell_id"] in event.active_buffs
    ):
        damage *= 1 - spec_cfg["blood_soaked_ground_physical_dr"]

    # 5. Icebound Fortitude emergency CD (reuses shield_wall slot)
    if now < state.shield_wall_until:
        damage *= 1 - spec_cfg["icebound_fortitude_dr"]

    # 6. Healer DR external (party buffs, Spirit Link, etc.)
    if now < state.healer_dr_until:
        damage *= 1 - state.healer_dr_amount

    # 7. Absorbs (Blood Shield generated from Death Strike feeds the same
    #    healer_absorb pool the policy already maintains)
    if event.is_log_replay:
        # Replay: use the log's absorbed field (captures everything)
        absorbed = min(damage, event.log_absorbed)
        damage -= absorbed
        result["absorbed_by_ignore_pain"] = absorbed
    else:
        # Synthetic: blood-shield absorb sits on healer_absorb, IP-style
        if state.healer_absorb > 0 and now < state.healer_absorb_until:
            absorbed = min(damage, state.healer_absorb)
            state.healer_absorb -= absorbed
            damage -= absorbed
            result["absorbed_by_healer"] = absorbed
            if state.healer_absorb <= 1e-6:
                state.healer_absorb = 0.0

    result["dealt"] = damage
    state.add_recent_damage(now, damage)
    return result


class BloodDKPolicy:
    """Decides when to press Death Strike, Vampiric Blood, Icebound Fort.

    Pressed roughly proportional to incoming damage pressure:
      - Death Strike: when HP < 70% and a DS would heal meaningfully
      - Vampiric Blood: when HP < 50% and the CD is up
      - Icebound Fortitude: at HP < 25% (emergency)
    """

    def __init__(self, character):
        self.character = character
        self.last_tick_t = 0.0
        self.last_ds_t = -100.0

    def tick(self, state: MitigationState, now: float) -> None:
        """No-op for now — DK rune/RP regen would happen here if the
        synthetic sim needed it. The averaged Bone Shield model and the
        periodic Death Strike heal don't require ticked resources."""
        self.last_tick_t = now

    def decide(
        self,
        state: MitigationState,
        now: float,
        recent_dtps: float,
        incoming_event: DamageEvent | None = None,
        upcoming_events: list[DamageEvent] | None = None,
    ) -> None:
        _ = upcoming_events  # adaptive-lookahead Phase A plumbing; see policy.py
        c = load_constants()
        spec_cfg = c["specs"]["blood_death_knight"]
        hp_pct = state.hp / state.max_hp if state.max_hp > 0 else 1.0

        # 1. Death Strike — fires periodically when HP is below comfortable.
        #    Rate-limited to spec_cfg["death_strike_avg_per_minute"].
        min_ds_interval = 60.0 / spec_cfg["death_strike_avg_per_minute"]
        if hp_pct < 0.70 and (now - self.last_ds_t) >= min_ds_interval:
            recent = state.recent_damage_total(now, window=5.0)
            heal_from_dmg = recent * spec_cfg["death_strike_heal_pct_of_recent_dmg"]
            min_heal = state.max_hp * spec_cfg["death_strike_min_heal_pct_of_max_hp"]
            mastery_mult = 1.0 + (
                state.character.mastery_pct() * spec_cfg["mastery_ds_heal_scaling"]
            )
            heal = max(heal_from_dmg, min_heal) * mastery_mult

            # Vampiric Blood boosts heal by 30% if active.
            if now < state.last_stand_until:
                heal *= 1 + spec_cfg["vampiric_blood_healing_increase"]

            state.hp = min(state.max_hp, state.hp + heal)
            # Blood Shield absorb (50% of heal) feeds the healer_absorb pool.
            shield = heal * spec_cfg["blood_shield_pct_of_ds_heal"]
            state.healer_absorb = max(state.healer_absorb, shield)
            state.healer_absorb_until = max(state.healer_absorb_until, now + 6.0)
            self.last_ds_t = now

        # 2. Vampiric Blood — emergency at <50% HP with CD up. Inflates
        #    max_hp by 30% for 10s, restored on expiry (stale "35%" comment
        #    fixed 2026-07-17 — the code already used the correct 0.30
        #    constant; Wowhead spell 55233 confirms base Vampiric Blood is
        #    +30% max HP, not +35%).
        if hp_pct < 0.50 and now >= state.last_stand_cd_until and state.last_stand_until <= now:
            state.last_stand_base_max_hp = state.max_hp
            state.max_hp *= 1 + spec_cfg["vampiric_blood_max_hp_increase"]
            state.hp *= 1 + spec_cfg["vampiric_blood_max_hp_increase"]
            state.last_stand_until = now + spec_cfg["vampiric_blood_duration_s"]
            state.last_stand_cd_until = now + spec_cfg["vampiric_blood_cooldown_s"]

        # 3. Icebound Fortitude — emergency 30% DR at <25% HP.
        if hp_pct < 0.25 and now >= state.shield_wall_cd_until and state.shield_wall_until <= now:
            state.shield_wall_until = now + spec_cfg["icebound_fortitude_duration_s"]
            state.shield_wall_cd_until = now + spec_cfg["icebound_fortitude_cooldown_s"]

        # Vampiric Blood expiry restoration (mirror of Last Stand)
        if (
            state.last_stand_base_max_hp > 0
            and now >= state.last_stand_until
            and state.max_hp > state.last_stand_base_max_hp
        ):
            ratio = state.last_stand_base_max_hp / state.max_hp
            state.hp *= ratio
            state.max_hp = state.last_stand_base_max_hp
            state.last_stand_base_max_hp = 0.0
