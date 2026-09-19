"""Brewmaster Monk mitigation — Phase 4.4 explicit stagger DoT model.

Replaces the previous "flat DR" approximation with an actual stagger
pool tracked on `MitigationState`. A fraction of every incoming hit
(40% phys / 10% magic) is *delayed* into a DoT that drains over 10s.
Purifying Brew clears 50% of the current pool. Ironskin Brew adds an
extra stagger fraction while active.

Replay mode (`event.is_log_replay == True`) preserves the original
behaviour — the log's `absorbed` field already captures stagger so the
synthetic pool would double-count. Synthetic sims (predictive path) get
the explicit pool + drain + purify dynamics.
"""

from __future__ import annotations

import random

from ..core.constants import load_constants
from ..core.events import DamageEvent
from ..core.mitigation import MitigationState


def _drain_stagger(state: MitigationState, now: float, duration_s: float) -> None:
    """Tick the stagger DoT.

    Stagger delays incoming damage into a pool that drains linearly over
    `duration_s`. Each tick:
      1. Removes a portion of the pool proportional to elapsed time
      2. Subtracts that amount from `state.hp` (the drain IS damage taken)
      3. Appends to `recent_damage_window` so DTPS / death-detection see it

    Validator Phase 4 audit Bug 3 (2026-05-16): the original implementation
    grew the pool and appended drain to recent_damage_window but never
    decremented `state.hp` — 40% of physical damage simply vanished.
    The math was also exponential-decay (`pool × dt / duration`) instead
    of linear-from-pool-peak. Fixed to linear by tracking the per-tick
    drain rate at apply time and decrementing the pool by `rate × dt`.

    The pool conceptually contains buckets that each tick down to zero
    over `duration_s`; we approximate by maintaining a single effective
    drain rate (pool / duration_s) updated each apply.
    """
    if state.stagger_pool <= 0.0:
        state.last_stagger_tick_t = now
        state.stagger_pool_drain_rate = 0.0
        return
    dt = max(0.0, now - state.last_stagger_tick_t)
    if dt <= 0.0:
        return
    # Linear drain at the current rate, capped at pool size.
    drain = min(state.stagger_pool, state.stagger_pool_drain_rate * dt)
    if drain <= 0.0:
        state.last_stagger_tick_t = now
        return
    state.stagger_pool -= drain
    if state.stagger_pool < 1e-6:
        state.stagger_pool = 0.0
        state.stagger_pool_drain_rate = 0.0
    state.last_stagger_tick_t = now
    # Apply the drained damage to HP and to the damage window so death
    # detection and DTPS metrics see it.
    state.hp = max(0.0, state.hp - drain)
    state.add_recent_damage(now, drain)


def apply_brewmaster_mitigation(
    state: MitigationState, event: DamageEvent, rng: random.Random
) -> dict:
    c = load_constants()
    char = state.character
    spec_cfg = c["specs"]["brewmaster_monk"]
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

    damage = raw

    if event.is_self_inflicted:
        # Stagger DoT ticks fed via mitigation chain — raw_amount is post-DR.
        result["dealt"] = damage
        state.add_recent_damage(now, damage)
        return result

    # Drain any pre-event stagger before applying the new hit's portion.
    _drain_stagger(state, now, spec_cfg["stagger_dot_duration_s"])

    # 1. Armor DR (physical only) — bleeds bypass armor (ROADMAP 3.9.3).
    if event.school == "physical" and not event.is_bleed:
        armor = char.total_armor()
        K = c["armor"]["k_constant"]
        dr = min(armor / (armor + K), c["armor"]["max_armor_dr"])
        damage *= 1 - dr

    # 2. Versatility (all damage)
    damage *= 1 - char.versatility_dr()

    # 2b. Hero-talent flat mitigation ledger (e.g. Shado-Pan Predictive Training
    # 8% all-school × ~0.88 uptime). Returns 1.0 when no ledger buff is detected,
    # so a build without the talent is bit-identical. Applied PRE-absorb (before
    # step 4): the log's fixed `absorbed` (Stagger) is subtracted AFTER, so the
    # effective cut becomes a much larger NET reduction (the leverage that takes
    # AnonBrewmaster2 +27% -> +11.8%). NOTE: that fixed-absorb leverage over-credits PT
    # (~24% of physical to-HP); see the magic-layers doc's post-audit section.
    damage *= char._always_on_dr()

    # 3. Fortifying Brew emergency CD (reuses shield_wall slot)
    if now < state.shield_wall_until:
        damage *= 1 - spec_cfg["fortifying_brew_dr"]

    # 4. Stagger fraction → DoT pool. Ironskin Brew averaged uptime adds
    #    extra stagger on top of the base fraction.
    if event.is_log_replay:
        # Replay: log's absorbed field already captures stagger; use the
        # original flat-DR approximation so the chain doesn't double-count.
        absorbed = min(damage, event.log_absorbed)
        damage -= absorbed
        result["absorbed_by_ignore_pain"] = absorbed
    else:
        base_pct = (
            spec_cfg["stagger_pct_physical"]
            if event.school == "physical"
            else spec_cfg["stagger_pct_magic"]
        )
        ironskin_bonus = (
            spec_cfg["ironskin_brew_stagger_increase"] * spec_cfg["ironskin_brew_avg_uptime"]
        )
        stagger_pct = min(base_pct + ironskin_bonus, 0.85)
        staggered = damage * stagger_pct
        damage -= staggered
        state.stagger_pool += staggered
        # Update the linear drain rate so the new fraction empties over
        # `stagger_dot_duration_s`. Adding to an existing pool refreshes
        # the rate against the larger total — approximates the in-game
        # behaviour where a new hit's stagger contribution extends the
        # tail rather than resetting it.
        state.stagger_pool_drain_rate = state.stagger_pool / spec_cfg["stagger_dot_duration_s"]

        # Healer absorb (Celestial Brew + healer shields) tier
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


class BrewmasterPolicy:
    """Purifying Brew when stagger pool > threshold; Celestial Brew on CD."""

    def __init__(self, character):
        self.character = character
        self.last_purify_t = -100.0
        self.last_celestial_t = -100.0

    def tick(self, state: MitigationState, now: float) -> None:
        c = load_constants()
        spec_cfg = c["specs"]["brewmaster_monk"]
        _drain_stagger(state, now, spec_cfg["stagger_dot_duration_s"])

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
        spec_cfg = c["specs"]["brewmaster_monk"]

        # 1. Purifying Brew — fires on charge cadence (PB has 2 charges,
        #    ~10s recharge → ~12 presses/min). Each press clears 50% of
        #    the current pool. Real Brewmasters press on charge availability
        #    rather than waiting for a threshold; this approximation has the
        #    same averaged effect over a fight.
        min_purify_interval = 60.0 / spec_cfg["purifying_brew_avg_per_minute"]
        threshold = state.max_hp * spec_cfg["purifying_brew_threshold_pct_of_max_hp"]
        # Two conditions, OR'd: hit the threshold immediately, OR press on
        # cadence to chip down the pool even at low stagger pressure.
        cadence_ready = (now - self.last_purify_t) >= min_purify_interval
        pool_pressure = state.stagger_pool > threshold
        if cadence_ready and (pool_pressure or state.stagger_pool > 0):
            cleared = state.stagger_pool * spec_cfg["purifying_brew_clear_pct"]
            state.stagger_pool -= cleared
            if state.stagger_pool < 1e-6:
                state.stagger_pool = 0.0
            # Rate-limit by updating the drain rate to match the smaller pool
            state.stagger_pool_drain_rate = state.stagger_pool / spec_cfg["stagger_dot_duration_s"]
            self.last_purify_t = now

        # 2. Celestial Brew — absorb shield on cooldown. Press when HP < 70%.
        # This HP-reactive gate means a geared tank whose HP never actually
        # drops below 70% gets ZERO Celestial Brew credit here regardless of
        # the cooldown value — see
        # docs/validation/phase4_brewmaster_celestial_brew_cooldown_fix_2026_08_07.md.
        #
        # Known gap, not silently guessed at: this block runs unconditionally
        # for every Brewmaster, but Celestial Brew and Celestial Infusion are
        # mutually-exclusive choice-node talents for the SAME absorb slot —
        # a character who took Infusion instead gets credited Celestial
        # Brew's numbers regardless. Reliable per-player detection needs a
        # COMBATANT_INFO entry-id-to-talent join table for this spec, which
        # doesn't exist (only Protection Warrior has one,
        # data/talent_trees/protection_warrior.json) — building one for a
        # single ability risks the same one-option-wired/other-defaulted
        # over-crediting bug class VDH's Fel Flame Fortification hit. Given
        # this block is already dead for any geared character (see the doc
        # above), the practical blast radius today is narrow: undergeared
        # Infusion-talented characters only. See
        # docs/validation/coaching_registry_audit_2026_07_02.md's own note on
        # Celestial Infusion for the coaching-side twin of this gap.
        hp_pct = state.hp / state.max_hp if state.max_hp > 0 else 1.0
        if hp_pct < 0.70 and (now - self.last_celestial_t) >= spec_cfg["celestial_brew_cooldown_s"]:
            absorb = state.max_hp * spec_cfg["celestial_brew_absorb_pct_of_max_hp"]
            state.healer_absorb = max(state.healer_absorb, absorb)
            state.healer_absorb_until = max(state.healer_absorb_until, now + 8.0)
            self.last_celestial_t = now
