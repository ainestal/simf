"""Protection Paladin mitigation.

Key buttons modeled:
- Shield of the Righteous (SotR): 3 Holy Power → ~4.5s phys DR + 100% block window.
- Ardent Defender (AD): 30% DR for 12s on 90s CD; cheat-death heals to 20% HP at <20% once per CD.
- Sentinel: stacking passive absorb (~3% max HP / 10s, caps at 30% max HP).
- Consecration: always-on 5% damage reduction averaged at 90% uptime.
- Word of Glory (WoG): emergency heal when low — funded by MANA in Midnight
  12.0.5 (50k of a 250k pool) for 93.5% of real casts; a rare 6.5% hybrid
  cast also draws 3 Holy Power (uncharacterized mechanism, negligible
  materiality). Log-proven 2026-07-04 by pool conservation against 919
  pre-cast pool readings (909 paid SotR + 10 hybrid WoG); see
  docs/validation/phase4_protpal_holy_power_economy_2026_07_04.md. Modeled
  as purely mana-funded — the sim's WoG never draws Holy Power.

Holy Power is generated passively (rotation abstraction) and spent by the
policy on SotR; the modeled WoG draws on the mana pool instead, so the two
don't compete for Holy Power the way the pre-2026-07-04 model made them.
"""

import random

from ..core.constants import load_constants
from ..core.events import DamageEvent
from ..core.mitigation import MitigationState, calculate_armor_resist


def apply_protection_paladin_mitigation(
    state: MitigationState, event: DamageEvent, rng: random.Random
) -> dict:
    c = load_constants()
    char = state.character
    spec_cfg = c["specs"]["protection_paladin"]
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

    # 1. Avoidance (dodge/parry) — physical only
    if (
        event.is_avoidable
        and event.school == "physical"
        and rng.random() < (char.base_dodge() + char.base_parry())
    ):
        result["was_avoided"] = True
        return result

    damage = raw

    # 2. Block (physical, blockable). SotR grants 100% block during window.
    if event.is_blockable:
        sotr_active = now < state.sotr_until
        if sotr_active:
            block_chance = spec_cfg["sotr_block_chance_during"]
        else:
            # Base block + mastery-scaled block chance
            mastery_block = char.mastery_pct() * spec_cfg["mastery_block_chance_scaling"]
            block_chance = spec_cfg["base_block_chance"] + mastery_block
        if rng.random() < block_chance:
            result["was_blocked"] = True
            # F15: block value is shield-armor-derived and runs through the same
            # armor curve as Warrior (SimC midnight player.cpp:1661-1678 shares
            # one init branch for PALADIN/WARRIOR). No crit-block roll — Paladin
            # has no critical-block mechanic in SimC midnight (crit block is
            # implemented warrior-side only, off Unwavering Sentinel mastery);
            # multiplier is always 1.0.
            K = c["armor"]["k_constant"]
            block_dr = calculate_armor_resist(state.cached_block_value_rating, K, 1.0)
            damage *= 1 - block_dr

    # 3. Armor DR (physical) — bleeds bypass armor (ROADMAP 3.9.3).
    if event.school == "physical" and not event.is_bleed:
        armor = char.total_armor()
        K = c["armor"]["k_constant"]
        dr = min(armor / (armor + K), c["armor"]["max_armor_dr"])
        damage *= 1 - dr

    # 4. Versatility
    damage *= 1 - char.versatility_dr()

    # 5. SotR physical DR window (separate from block)
    if event.school == "physical" and now < state.sotr_until:
        damage *= 1 - spec_cfg["sotr_physical_dr"]

    # 6. Consecration averaged DR
    damage *= 1 - spec_cfg["consecration_dr"] * spec_cfg["consecration_avg_uptime"]

    # 7. Ardent Defender DR
    if now < state.ardent_defender_until:
        damage *= 1 - spec_cfg["ardent_defender_dr"]

    # 8. Sentinel absorb (sim-only; in replay mode, log_absorbed captures real absorbs)
    if event.is_log_replay:
        absorbed = min(damage, event.log_absorbed)
        damage -= absorbed
        result["absorbed_by_ignore_pain"] = absorbed
    else:
        if state.sentinel_absorb > 0:
            absorbed = min(damage, state.sentinel_absorb)
            state.sentinel_absorb -= absorbed
            damage -= absorbed
            result["absorbed_by_ignore_pain"] = absorbed
            if state.sentinel_absorb <= 1e-6:
                state.sentinel_absorb = 0.0

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


class ProtPalPolicy:
    """Decides when to press Holy Power spenders, Ardent Defender, Word of Glory.

    Called from runner via the standard tick/decide cycle when the active spec is
    protection_paladin. Holy Power generation and Sentinel absorb tick here.
    """

    def __init__(self, character):
        self.character = character
        self.last_tick_t = 0.0

    def tick(self, state: MitigationState, now: float) -> None:
        c = load_constants()
        spec_cfg = c["specs"]["protection_paladin"]
        dt = max(0.0, now - self.last_tick_t)
        self.last_tick_t = now

        # Holy Power generation (haste-scaled rotation speed)
        hp_per_s = spec_cfg["holy_power_per_second_base"] * (1 + self.character.haste_pct())
        state.holy_power = min(
            float(spec_cfg["holy_power_max"]),
            state.holy_power + hp_per_s * dt,
        )

        # Mana regen — funds Word of Glory (Midnight WoG costs mana, not Holy
        # Power; log-verified 2026-07-04). Pool starts full (MitigationState).
        state.mana = min(
            float(spec_cfg["mana_max"]),
            state.mana + spec_cfg["mana_regen_per_s"] * dt,
        )

        # Sentinel passive absorb generation
        sentinel_rate = state.max_hp * spec_cfg["sentinel_absorb_pct_per_10s"] / 10.0
        sentinel_cap = state.max_hp * spec_cfg["sentinel_max_pct_of_max_hp"]
        state.sentinel_absorb = min(sentinel_cap, state.sentinel_absorb + sentinel_rate * dt)

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
        spec_cfg = c["specs"]["protection_paladin"]

        # 1. Word of Glory — emergency heal when low HP and mana available.
        #    WoG is overwhelmingly MANA-funded in Midnight (log-proven
        #    2026-07-04: 93.5% of WoG cast lines read powerType=0 cost=50000;
        #    a rare 6.5% hybrid cast also draws 3 Holy Power). Pool
        #    conservation over 919 pre-cast pool readings (909 SotR + 10
        #    hybrid WoG) is 99.8% clean, so modeling WoG as purely
        #    mana-funded means it does NOT compete with SotR for Holy Power
        #    the way the pre-fix model made it. Free-proc WoGs (cost=0 in
        #    every resource, 6.5% of her casts, 10/155) are not modeled — the
        #    sim's WoG is slightly scarcer than the real one. The low-HP
        #    trigger + threshold are the sim's own policy heuristic,
        #    deliberately NOT retuned to Bruttah's press pattern.
        if (
            state.hp / state.max_hp < spec_cfg["wog_threshold_hp_pct"]
            and state.mana >= spec_cfg["wog_mana_cost"]
        ):
            heal = state.max_hp * spec_cfg["wog_heal_pct_of_max_hp"]
            state.hp = min(state.max_hp, state.hp + heal)
            state.mana -= spec_cfg["wog_mana_cost"]
            return  # one spender per decision tick (GCD-ish serialization)

        # 2. Shield of the Righteous — maintain coverage; press near expiry if we have HP
        if state.holy_power >= spec_cfg["sotr_holy_power_cost"] and now >= state.sotr_until - 1.5:
            state.sotr_until = max(now, state.sotr_until) + spec_cfg["sotr_duration_s"]
            state.holy_power -= spec_cfg["sotr_holy_power_cost"]

        # 3. Ardent Defender — emergency CD at <40% HP. Cheat-death handled on damage.
        if state.hp / state.max_hp < 0.40 and now >= state.ardent_defender_cd_until:
            state.ardent_defender_until = now + spec_cfg["ardent_defender_duration_s"]
            state.ardent_defender_cd_until = now + spec_cfg["ardent_defender_cooldown_s"]
