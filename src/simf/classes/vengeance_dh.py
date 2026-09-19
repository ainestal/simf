"""Vengeance Demon Hunter mitigation.

Mitigation layers pinned to SimC (midnight branch, build 12.0.5.67823 — see
``docs/validation/phase4_vdh_characterization_2026_06_09.md``):

- **Demon Spikes** (203819): +8% parry + armor = +75% of Agility for 12s. Has
  NO flat damage reduction (the old 20% was a phantom). On landed hits in
  replay the parry is moot (avoided hits never appear); the agi-armor bump is
  the real mitigation. Policy maintains ~100% uptime.
- **Metamorphosis** (187827): ×3.0 base+gear armor (the biggest layer) + 40%
  max HP for 15s on 120s CD; no innate leech. The armor is credited
  **window-gated** (only while the buff is actually up — averaging a non-linear
  armor multiplier over its ~50% M+ uptime is wrong): in replay from the log's
  ``event.active_buffs``, in live from the policy's emergency Meta state.
- **Infernal Armor** (talent 320331): +20% armor while the Immolation Aura
  buff (258920) is up — activates IA's own dormant armor effect, which is
  base 0% without the talent. Window-gated the same way as Metamorphosis's
  armor multiplier (stacks multiplicatively with it). PHYSICAL ONLY. Modeled
  unconditionally (VDH has no per-player talent-detection plumbing yet;
  confirmed rank 2/2 on all 3 corpus fighters 2026-07-18). Live/synthetic
  mode gets no credit (no Immolation-Aura-cast model in the policy).
- **Demonic Wards** (203513): flat −12% all-school always-on DR (the Defensive-
  Stance analog). Thick Skin's armor/stamina is already in the hydrated snapshot.
- **Soul Cleave**: spends 30 Fury to heal 25% missing HP. Plus passive Soul
  Fragment heals (~6 fragments/min, each 6% max HP) as periodic policy ticks.
- **Fiery Brand** (207744): −40% target-source DR for 12s, averaged for
  multi-mob M+ coverage. ATTACKER-SIDE (a debuff on the branded mob, reducing
  what IT deals) — skipped in replay (2026-07-17): the log's ``unmitigatedAmount``
  already reflects it, same bug class as Prot Warrior's Demo Shout/Phalanx
  double-count (see ``docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md``).
  Still applied in live/synthetic mode, where the damage profile carries raw
  mob output.
- **Painbringer** (buff 212988, talent 207387): flat −3% all-school DR while
  the buff is up (refreshed per Soul Fragment consumed, 8s, non-stacking in
  Midnight — ``SpellAuraOptions.CumulativeAura = 1``). Window-gated off
  ``event.active_buffs`` in replay, like Metamorphosis; the live path gets no
  credit yet (the policy has no Soul-Fragment-consume model).
- **Void Reaver's Frailty** (talent 268175, debuff 247456): −5% all-school
  "damage to caster" from a Frailty-debuffed mob. ATTACKER-SIDE, confirmed
  empirically 2026-07-18 by per-hit forensics on the WCL corpus (same
  ability+mob, base_amount consistently ~5-15% lower while Frailty is up) —
  already baked into logged damage, same bug class as Fiery Brand. Modeled
  live/synthetic-only (flat averaged, like Fiery Brand); never in replay. Value pinned
  from live Wowhead + DB2 12.0.7.68367 — the buff's effect base is 0 and the
  −3 arrives via the talent's ``A_ADD_FLAT_LABEL_MODIFIER`` (label 2406),
  which is why the earlier audit's static DB2 read found 0 and deferred. A
  follow-up validator pass found SimC likely resolves this at runtime anyway —
  ``sc_demon_hunter.cpp`` builds the buff via
  ``set_default_value_from_effect_type(A_MOD_DAMAGE_PERCENT_TAKEN)``, the same
  pattern used for Fiery Brand (which SimC does credit) — so the earlier "0"
  was a static-inspection artifact, not confirmed SimC omission. Either way
  the 3% here is pinned from Wowhead + DB2, independent of SimC's own read.
- **Party/healer aura DR** (``party_dr_by_school``): parity with the warrior +
  Guardian paths; replay opt-in via ``state.party_magic_dr_active`` (off in
  Monte-Carlo calibration runs).

`calibrated: false` until the re-run lands within ±15% AND an Aldrachi Reaver
log + dual-validator sign off (the 3 audited logs are all Fel-Scarred).
"""

from __future__ import annotations

import random

from ..core.constants import load_constants
from ..core.events import DamageEvent
from ..core.mitigation import MitigationState


def apply_vengeance_dh_mitigation(
    state: MitigationState, event: DamageEvent, rng: random.Random
) -> dict:
    c = load_constants()
    char = state.character
    spec_cfg = c["specs"]["vengeance_demon_hunter"]
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

    # Demon Spikes (policy maintains ~100% uptime in both live + replay) and
    # Metamorphosis. Meta's armor multiplier is non-linear over its ~50% M+
    # uptime, so it is credited window-gated, not averaged: in REPLAY from the
    # log's actual buff windows (event.active_buffs), in LIVE from the policy's
    # emergency Meta state. (Using both at once would double-count, hence the
    # is_log_replay split.) See the SimC audit in the characterization doc.
    demon_spikes_active = now < state.bsv_active_until  # reusing the bsv slot
    if event.is_log_replay:
        meta_active = spec_cfg["metamorphosis_spell_id"] in event.active_buffs
    else:
        meta_active = now < state.last_stand_until and state.last_stand_base_max_hp > 0

    # 1. Avoidance — VDH has higher base dodge and gets a parry boost
    #    while Demon Spikes is active.
    if event.is_avoidable and event.school == "physical":
        dodge = char.base_dodge()
        parry = char.base_parry()
        if demon_spikes_active:
            parry += spec_cfg["demon_spikes_parry_chance_during"]
        if rng.random() < (dodge + parry):
            result["was_avoided"] = True
            return result

    damage = raw

    # 2. Armor DR (physical only) — bleeds bypass armor (ROADMAP 3.9.3).
    #    Metamorphosis multiplies base+gear armor (SimC: composite_base_armor_
    #    multiplier, ×3.0); Demon Spikes adds +75% of Agility as bonus armor
    #    (applied AFTER the base multiplier, matching SimC's armor pipeline).
    #    Demon Spikes has NO flat physical DR (the old 0.20 was a phantom).
    if event.school == "physical" and not event.is_bleed:
        armor = char.total_armor()
        if meta_active:
            armor *= spec_cfg["metamorphosis_armor_multiplier"]
        if spec_cfg["immolation_aura_spell_id"] in event.active_buffs:
            armor *= 1 + spec_cfg["infernal_armor_multiplier"]
        if demon_spikes_active:
            armor += spec_cfg["demon_spikes_armor_from_agility"] * char.agility
        K = c["armor"]["k_constant"]
        dr = min(armor / (armor + K), c["armor"]["max_armor_dr"])
        damage *= 1 - dr

    # 3. Versatility (all damage)
    damage *= 1 - char.versatility_dr()

    # 4. Demonic Wards — flat all-school always-on DR (SimC: -12% magic AND
    #    -12% physical baseline; the Defensive-Stance analog). Thick Skin's
    #    armor/stamina is already in the hydrated armor snapshot — not here.
    damage *= 1 - spec_cfg["demonic_wards_dr"]

    # 4b. Painbringer — flat all-school DR while buff 212988 is up (non-
    #     stacking in Midnight; −3% per DB2 12.0.7.68367, see constants.yaml).
    #     Window-gated like Metamorphosis: replay events carry the log's real
    #     buff windows in event.active_buffs; live events carry an empty set,
    #     so the live path takes no credit (the policy has no Soul-Fragment-
    #     consume model — calibration-path-first staging).
    if spec_cfg["painbringer_spell_id"] in event.active_buffs:
        damage *= 1 - spec_cfg["painbringer_dr"]

    # 5. Fiery Brand averaged DR — flat across the chain (single-target
    #    focus diluted by multi-mob content).
    #
    # ATTACKER-SIDE DEBUFF — skip in log replay. Fiery Brand marks the mob
    # (target-source DR: it deals less, not "you take less"), so its reduction
    # is already inside the log's `unmitigatedAmount`/`raw_amount` by the time
    # simf sees it — re-applying it here during replay double-counts it. Same
    # bug class, same fix, as Prot Warrior's Demo Shout/Phalanx (2026-07-17;
    # see docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md).
    # Live/synthetic mode is untouched: that damage profile carries raw mob
    # output, so modeling the reduction there is legitimate (is_log_replay
    # defaults False).
    if not event.is_log_replay:
        damage *= (
            1 - spec_cfg["fiery_brand_target_dr"] * spec_cfg["fiery_brand_avg_uptime_in_m_plus"]
        )

    # 5c. Void Reaver's Frailty averaged DR — SAME bug class as Fiery Brand,
    #     confirmed empirically 2026-07-18, not just by SimC-source reasoning
    #     (which was genuinely ambiguous — see the doc below). Frailty is a
    #     debuff the player applies to a mob; Void Reaver adds -5% all-school
    #     "damage to caster" on it. Per-hit forensics on all 3 corpus WCL
    #     fights (same ability + same mob, Frailty-active vs not) showed
    #     unmitigatedAmount itself is ~5-15% LOWER while Frailty is up —
    #     i.e. it's ATTACKER-SIDE, already baked into the log before simf's
    #     replay sees it. Modeled live/synthetic-only, exactly like Fiery
    #     Brand — do NOT window-gate this into replay, that would double-count.
    #     See docs/validation/phase4_vdh_magic_residual_leads_2026_07_18.md.
    if not event.is_log_replay:
        damage *= 1 - spec_cfg["void_reaver_dr"] * spec_cfg["void_reaver_avg_uptime_in_m_plus"]

    # 5b. Party-aura DR (healer + party magic auras) — parity with the warrior
    #     path (mitigation.py) and Guardian (guardian_druid.py). Replay opt-in
    #     via party_magic_dr_active; `all` applies to every event, `magic`
    #     stacks multiplicatively on non-physical.
    if state.party_magic_dr_active:
        party_cfg = spec_cfg.get("party_dr_by_school", {})
        dr_all = party_cfg.get("all", 0.0)
        if dr_all:
            damage *= 1 - dr_all
        if event.school != "physical":
            dr_magic = party_cfg.get("magic", 0.0)
            if dr_magic:
                damage *= 1 - dr_magic

    # 6. Healer DR external
    if now < state.healer_dr_until:
        damage *= 1 - state.healer_dr_amount

    # 7. Absorbs
    if event.is_log_replay:
        absorbed = min(damage, event.log_absorbed)
        damage -= absorbed
        result["absorbed_by_ignore_pain"] = absorbed
    else:
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


class VengeanceDHPolicy:
    """Demon Spikes maintenance + Soul Cleave heal + Metamorphosis emergency.

    Soul Cleave + Soul Fragment passive heals tick at the fragments-per-min
    rate from constants. Demon Spikes presses near expiry with sufficient
    Fury. Metamorphosis fires below 35% HP.
    """

    def __init__(self, character):
        self.character = character
        self.last_tick_t = 0.0
        self.last_soul_heal_t = 0.0

    def tick(self, state: MitigationState, now: float) -> None:
        c = load_constants()
        spec_cfg = c["specs"]["vengeance_demon_hunter"]
        dt = max(0.0, now - self.last_tick_t)
        self.last_tick_t = now

        # Soul Fragment passive ticks — heal once per fragment interval
        if state.hp < state.max_hp:
            interval = 60.0 / spec_cfg["soul_fragments_per_minute"]
            if (now - self.last_soul_heal_t) >= interval:
                heal = state.max_hp * spec_cfg["soul_fragment_heal_pct_max_hp"]
                state.hp = min(state.max_hp, state.hp + heal)
                self.last_soul_heal_t = now
        _ = dt  # tick interval available if we add Fury accounting later

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
        spec_cfg = c["specs"]["vengeance_demon_hunter"]
        hp_pct = state.hp / state.max_hp if state.max_hp > 0 else 1.0

        # 1. Demon Spikes — refresh near expiry (Fury cost abstracted away)
        if now >= state.bsv_active_until - 1.0:
            state.bsv_active_until = now + spec_cfg["demon_spikes_duration_s"]

        # 2. Soul Cleave heal — fires when HP < 60% (heals 25% missing HP)
        if hp_pct < 0.60:
            missing = state.max_hp - state.hp
            heal = missing * spec_cfg["soul_cleave_heal_pct_missing_hp"]
            state.hp = min(state.max_hp, state.hp + heal)

        # 3. Metamorphosis — emergency at <35% HP, mirror Last Stand
        if hp_pct < 0.35 and now >= state.last_stand_cd_until and state.last_stand_until <= now:
            state.last_stand_base_max_hp = state.max_hp
            state.max_hp *= 1 + spec_cfg["metamorphosis_max_hp_increase"]
            state.hp *= 1 + spec_cfg["metamorphosis_max_hp_increase"]
            state.last_stand_until = now + spec_cfg["metamorphosis_duration_s"]
            state.last_stand_cd_until = now + spec_cfg["metamorphosis_cooldown_s"]

        # Metamorphosis expiry — restore base max_hp
        if (
            state.last_stand_base_max_hp > 0
            and now >= state.last_stand_until
            and state.max_hp > state.last_stand_base_max_hp
        ):
            ratio = state.last_stand_base_max_hp / state.max_hp
            state.hp *= ratio
            state.max_hp = state.last_stand_base_max_hp
            state.last_stand_base_max_hp = 0.0
