"""Guardian Druid mitigation — Phase 4.4 improvements.

Adds on top of the original Ironfur+Barkskin+SI baseline:

- **Tooth and Claw** passive heal — periodic 6% max HP heal at ~12
  procs/min. Modeled as a `GuardianPolicy.tick` heal cadence.
- **Incarnation: Guardian of Ursoc** — +30% max HP + 30% armor for 30s
  on 180s CD. Reuses the Last Stand pattern.
- **Higher base dodge** — Guardian baseline 5% (vs Warrior 3%).
"""

from __future__ import annotations

import random

from ..core.constants import load_constants
from ..core.events import DamageEvent
from ..core.mitigation import MitigationState


def apply_guardian_mitigation(
    state: MitigationState, event: DamageEvent, rng: random.Random
) -> dict:
    c = load_constants()
    char = state.character
    spec_cfg = c["specs"]["guardian_druid"]
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

    # 1. Avoidance — dodge only (Druid bears can't parry); higher base.
    if event.is_avoidable and event.school == "physical" and rng.random() < char.base_dodge():
        result["was_avoided"] = True
        return result

    damage = raw

    # 2. Armor DR — Ironfur factored into total_armor() already. Computed once
    # (school-independent property of the tank): used for physical mitigation
    # below AND for Ursol's Warding (magic DR = 10% of armor DR) in step 3d.
    armor = char.total_armor()
    # Incarnation armor multiplier rides on top of the Ironfur stack.
    if now < state.last_stand_until and state.last_stand_base_max_hp > 0:
        armor *= spec_cfg["incarnation_armor_multiplier"]
    K = c["armor"]["k_constant"]
    armor_dr = min(armor / (armor + K), c["armor"]["max_armor_dr"])
    # Armor mitigates physical only; bleeds bypass armor (ROADMAP 3.9.3).
    if event.school == "physical" and not event.is_bleed:
        damage *= 1 - armor_dr

    # 3. Versatility
    damage *= 1 - char.versatility_dr()

    # 3b. Baseline flat all-school DR (Thick Hide × Bear Form passive) — the
    # Defensive-Stance analog the Guardian path previously skipped. Single
    # source of truth in Character._always_on_dr (also drives the eHP/marginals
    # math), mirroring the Brewmaster path. Returns 1.0 for any other spec.
    damage *= char._always_on_dr()

    # 3c. Party-aura DR (healer + party magic auras) — parity with the warrior
    # path (mitigation.py). Replay opt-in via party_magic_dr_active; `all`
    # applies to every event, `magic` stacks multiplicatively on non-physical.
    if state.party_magic_dr_active:
        party_cfg = spec_cfg.get("party_dr_by_school", {})
        dr_all = party_cfg.get("all", 0.0)
        if dr_all:
            damage *= 1 - dr_all
        if event.school != "physical":
            dr_magic = party_cfg.get("magic", 0.0)
            if dr_magic:
                damage *= 1 - dr_magic

    # 3d. Guardian magic-school DR the chain omitted until 2026-06-29 (validated
    # against 11 WCL logs — closed a ~15pp under-mitigation of every magic school;
    # see docs/validation/phase4_guardian_wcl_validation_2026_06_28.md). Both layers
    # baseline-on (ratified — near-universal, WCL can't gate on talents). Physical
    # (incl. bleeds) is unaffected: this whole block is gated on a non-physical school.
    if event.school != "physical":
        # Ursol's Warding (471492): magic DR = 10% of the tank's armor DR.
        ursols = spec_cfg.get("ursols_warding_magic_dr_pct_of_armor", 0.0) * armor_dr
        if ursols:
            damage *= 1 - ursols
        # Bear Form (5487 Effect #14): extra -6% ARCANE on top of the -3% all-school.
        if event.school == "arcane":
            damage *= 1 - spec_cfg.get("bear_form_arcane_dr", 0.0)

    # 4. Barkskin averaged DR
    damage *= 1 - spec_cfg["barkskin_avg_dr"]

    # NOTE (2026-06-29): the old "Rage of the Sleeper" averaged-DR step was removed
    # here — RotS was deleted from the game in patch 12.0.0 (verified vs SimC source
    # + Warcraft Wiki), so the ~3.3% all-school baseline was a phantom layer crediting
    # an ability no 12.0.7 Guardian has. See phase4_guardian_magic_mit_2026_06_29.md.

    # 6. Survival Instincts emergency CD (reuses shield_wall slot)
    if now < state.shield_wall_until:
        damage *= 1 - spec_cfg["survival_instincts_dr"]

    # 7. Healer DR external
    if now < state.healer_dr_until:
        damage *= 1 - state.healer_dr_amount

    # 8. Absorbs
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


class GuardianPolicy:
    """Tooth-and-Claw periodic heal + Incarnation emergency CD."""

    def __init__(self, character):
        self.character = character
        self.last_tnc_heal_t = -100.0
        self.last_tick_t = 0.0
        # Frenzied Regeneration charge tracker: one ready-time per charge, all
        # available at start. Empty when the constants block is absent (no-op).
        frr = load_constants()["specs"]["guardian_druid"].get("frenzied_regeneration", {})
        self.frr_charges_ready_at = [-100.0] * int(frr.get("charges", 0))

    def tick(self, state: MitigationState, now: float) -> None:
        c = load_constants()
        spec_cfg = c["specs"]["guardian_druid"]
        # Tooth-and-Claw proc heal — fires once per interval when not at full HP.
        if state.hp < state.max_hp:
            interval = 60.0 / spec_cfg["tooth_and_claw_procs_per_minute"]
            if (now - self.last_tnc_heal_t) >= interval:
                # Tooth & Claw is a self-heal = healing received, so Mastery:
                # Nature's Guardian's healing-taken aura scales it (×1+0.7·mastery).
                heal = (
                    state.max_hp
                    * spec_cfg["tooth_and_claw_heal_pct_max_hp"]
                    * self.character.incoming_healing_multiplier()
                )
                state.apply_self_heal(now, heal)
                self.last_tnc_heal_t = now
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
        spec_cfg = c["specs"]["guardian_druid"]
        hp_pct = state.hp / state.max_hp if state.max_hp > 0 else 1.0

        # Frenzied Regeneration (22842) — reactive %-max-HP self-heal, pressed when
        # low and a charge is up. MASTERY-NEUTRAL: it is a percent-of-max-HP heal,
        # which the 227034 mastery proc excludes, so it is deliberately NOT scaled
        # by incoming_healing_multiplier(). Mutates state.hp directly (same as the
        # Tooth & Claw heal); clamp-to-missing-HP records only the effective amount.
        frr = spec_cfg.get("frenzied_regeneration", {})
        if frr and self.frr_charges_ready_at and hp_pct <= frr["trigger_hp_pct"]:
            ready_idx = None
            for i, ready_at in enumerate(self.frr_charges_ready_at):
                if ready_at <= now and (
                    ready_idx is None or ready_at < self.frr_charges_ready_at[ready_idx]
                ):
                    ready_idx = i
            if ready_idx is not None and state.hp < state.max_hp:
                # Innate Resolve: heal scales up to +120% the lower your health.
                heal = (
                    state.max_hp
                    * frr["base_pct_max_hp"]
                    * (1.0 + frr["innate_resolve_low_hp_bonus"] * (1.0 - hp_pct))
                )
                state.apply_self_heal(now, heal)
                self.frr_charges_ready_at[ready_idx] = now + frr["recharge_s"]

        # Incarnation — emergency at <40% HP, mirrors Last Stand pattern.
        if hp_pct < 0.40 and now >= state.last_stand_cd_until and state.last_stand_until <= now:
            state.last_stand_base_max_hp = state.max_hp
            state.max_hp *= 1 + spec_cfg["incarnation_max_hp_increase"]
            state.hp *= 1 + spec_cfg["incarnation_max_hp_increase"]
            state.last_stand_until = now + spec_cfg["incarnation_duration_s"]
            state.last_stand_cd_until = now + spec_cfg["incarnation_cooldown_s"]

        # Incarnation expiry restoration
        if (
            state.last_stand_base_max_hp > 0
            and now >= state.last_stand_until
            and state.max_hp > state.last_stand_base_max_hp
        ):
            ratio = state.last_stand_base_max_hp / state.max_hp
            state.hp *= ratio
            state.max_hp = state.last_stand_base_max_hp
            state.last_stand_base_max_hp = 0.0
