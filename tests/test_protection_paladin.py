"""Protection Paladin spec tests — SotR, AD, cheat-death, Holy Power, Sentinel."""

import random

from simf.classes.protection_paladin import (
    ProtPalPolicy,
    apply_protection_paladin_mitigation,
)
from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState, apply_mitigation
from simf.core.profiles import HealingProfile
from simf.core.runner import run_simulation


def make_pal(armor: int = 5000, vers: int = 0, mastery: int = 0) -> Character:
    return Character(
        name="test_pal",
        race="human",
        class_spec="protection_paladin",
        talents="default-paladin",
        strength=2000,
        stamina=80000,
        armor_from_gear=armor,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=mastery,
        versatility_rating=vers,
        max_hp_override=8_000_000,
    )


def phys_event(amount: float = 1_000_000, blockable: bool = False) -> DamageEvent:
    return DamageEvent(
        time_s=0.0,
        source_id="test",
        school="physical",
        raw_amount=amount,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=blockable,
    )


def magic_event(amount: float = 1_000_000) -> DamageEvent:
    return DamageEvent(
        time_s=0.0,
        source_id="test",
        school="magic",
        raw_amount=amount,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )


def test_dispatch_routes_to_paladin():
    """apply_mitigation should route to the paladin handler for the prot pal spec."""
    char = make_pal()
    state = MitigationState(char)
    result = apply_mitigation(state, magic_event(amount=1_000_000), random.Random(0))
    # Magic event takes vers + consecration + mastery DR — non-zero, less than raw
    assert 0 < result["dealt"] < 1_000_000


def test_sotr_physical_dr_window():
    """SotR active should reduce physical damage by the configured phys_dr fraction
    on top of normal armor + vers reductions."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal(armor=5000)

    # Baseline phys hit (no SotR)
    state = MitigationState(char)
    base = apply_protection_paladin_mitigation(state, phys_event(), random.Random(0))

    # Same hit with SotR active
    state2 = MitigationState(char)
    state2.sotr_until = 10.0  # active
    with_sotr = apply_protection_paladin_mitigation(state2, phys_event(), random.Random(0))

    # SotR should significantly reduce damage (DR ~20% on top of base)
    ratio = with_sotr["dealt"] / base["dealt"]
    expected_ratio = 1 - spec_cfg["sotr_physical_dr"]
    assert abs(ratio - expected_ratio) < 0.001


def test_ardent_defender_dr_reduces_damage():
    """AD active should reduce all damage by the configured DR fraction."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()

    state = MitigationState(char)
    base = apply_protection_paladin_mitigation(state, magic_event(), random.Random(0))

    state2 = MitigationState(char)
    state2.ardent_defender_until = 10.0
    with_ad = apply_protection_paladin_mitigation(state2, magic_event(), random.Random(0))

    ratio = with_ad["dealt"] / base["dealt"]
    assert abs(ratio - (1 - spec_cfg["ardent_defender_dr"])) < 0.001


def test_holy_power_generation_and_cap():
    """Policy.tick should generate Holy Power over time, capped at holy_power_max."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()
    state = MitigationState(char)
    policy = ProtPalPolicy(char)

    policy.tick(state, 3.0)  # 3 seconds in
    expected = spec_cfg["holy_power_per_second_base"] * 3.0
    assert abs(state.holy_power - expected) < 0.01

    # Tick a long time later — should cap
    policy.tick(state, 100.0)
    assert state.holy_power == float(spec_cfg["holy_power_max"])


def test_sotr_consumes_holy_power():
    """When 3 HP available and SotR is near expiry, decide() spends HP and extends SotR."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()
    state = MitigationState(char)
    state.holy_power = 3.0
    policy = ProtPalPolicy(char)

    policy.decide(state, now=0.0, recent_dtps=0.0)
    assert state.holy_power == 0.0
    assert state.sotr_until == spec_cfg["sotr_duration_s"]


def test_word_of_glory_emergency_heals():
    """When HP < wog_threshold and mana is available, decide() casts WoG: it
    heals, spends MANA, and leaves Holy Power untouched — Midnight WoG is
    mana-funded (log-verified 2026-07-04, see
    docs/validation/phase4_protpal_holy_power_economy_2026_07_04.md). The
    decision tick is consumed, so SotR is not pressed the same tick."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()
    state = MitigationState(char)
    state.hp = state.max_hp * 0.20  # below threshold
    state.holy_power = 3.0
    policy = ProtPalPolicy(char)

    policy.decide(state, now=0.0, recent_dtps=0.0)
    assert state.hp > state.max_hp * 0.20
    # Holy Power must NOT be consumed — WoG never competes with SotR for it.
    assert state.holy_power == 3.0
    # Mana pays for it instead.
    assert state.mana == float(spec_cfg["mana_max"]) - spec_cfg["wog_mana_cost"]
    # One spender per decision tick: SotR untouched on the WoG tick.
    assert state.sotr_until == -1.0


def test_word_of_glory_without_mana_falls_through_to_sotr():
    """With mana exhausted, low HP no longer starves SotR: the WoG branch
    fails its mana gate and the same decision tick presses SotR. This is the
    structural half of the 2026-07-04 economy fix — the old model spent Holy
    Power on WoG first, collapsing SotR coverage exactly in the low-HP
    (high-key) regime."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()
    state = MitigationState(char)
    state.hp = state.max_hp * 0.20
    state.holy_power = 3.0
    state.mana = 0.0
    policy = ProtPalPolicy(char)

    policy.decide(state, now=0.0, recent_dtps=0.0)
    assert state.hp == state.max_hp * 0.20  # no WoG heal happened
    assert state.holy_power == 0.0  # spent on SotR instead
    assert state.sotr_until == spec_cfg["sotr_duration_s"]


def test_sotr_uptime_matches_generation_rate_when_healthy():
    """Emergent-economy invariant: with full HP (WoG silent) and a dense
    tick/decide stream, realized SotR uptime converges to
    generation_rate × sotr_duration_s / sotr_holy_power_cost — the exact
    relationship `holy_power_per_second_base` was derived from
    (docs/validation/phase4_protpal_holy_power_economy_2026_07_04.md: base
    back-solved from Bruttah's measured buff uptime). A policy change that
    breaks the press-near-expiry ↔ smooth-income coupling would silently
    invalidate that derivation — this trips instead."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()  # haste_rating=0 → generation at the raw base rate
    state = MitigationState(char)
    policy = ProtPalPolicy(char)

    dt, horizon = 0.25, 600.0
    t, covered = 0.0, 0.0
    while t < horizon:
        t += dt
        policy.tick(state, t)
        if t < state.sotr_until:
            covered += dt
        policy.decide(state, t, recent_dtps=0.0)

    expected = (
        spec_cfg["holy_power_per_second_base"]
        * spec_cfg["sotr_duration_s"]
        / spec_cfg["sotr_holy_power_cost"]
    )
    realized = covered / horizon
    assert abs(realized - expected) < 0.05, f"uptime {realized:.3f} vs expected {expected:.3f}"


def test_mana_starts_full_regenerates_and_caps():
    """The mana pool starts full, tick() regenerates it at mana_regen_per_s,
    and it caps at mana_max."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()
    state = MitigationState(char)
    assert state.mana == float(spec_cfg["mana_max"])

    state.mana = 0.0
    policy = ProtPalPolicy(char)
    policy.tick(state, 4.0)
    assert abs(state.mana - spec_cfg["mana_regen_per_s"] * 4.0) < 1e-6

    policy.tick(state, 1_000_000.0)  # far future — regen must not overshoot
    assert state.mana == float(spec_cfg["mana_max"])


def test_ardent_defender_no_cheat_death_when_buff_never_pressed():
    """A lethal hit before AD is EVER pressed must kill, not be saved.

    Pins the fix for a validator-confirmed bug (2026-07-03): the old gate
    checked cooldown-readiness (`now >= ardent_defender_cd_until`), which
    starts at -1.0 — "always ready" — before the first ever press. That
    granted a free cheat-death to the very first lethal hit of a fight
    with zero button presses, the opposite of SimC's real
    `buffs.ardent_defender->check()` gate (must be inside the 12s window
    you already opened). This single-hit-with-no-setup case is exactly
    where the two gates disagree — this test would have failed before
    the fix and must pass after it."""
    char = make_pal(armor=0, vers=0)
    # One huge magic hit (avoids armor) that would otherwise one-shot the pal.
    # Nothing preceded it, so AD's buff was never opened.
    events = [
        DamageEvent(
            time_s=1.0,
            source_id="test",
            school="magic",
            raw_amount=20_000_000,
            attack_type="spell",
        )
    ]
    healing = HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)
    result = run_simulation(
        char,
        damage_profile=None,
        healing_profile=healing,
        events_override=events,
        iterations=1,
        compute_metrics=False,
    )
    assert result.deaths == 1


def test_ardent_defender_cheat_death_when_buff_active():
    """A lethal hit WHILE the AD buff window is open must be saved, even
    though the ability just went on cooldown (cd_until = press_time + 90s).

    This is the other half of the same bug: the old gate required
    cooldown-readiness, which is FALSE for 90s immediately after any real
    press — so it would have refused to save a hit landing well inside
    the 12s buff window it just opened.

    Getting the policy to actually press AD is the fiddly part: Word of
    Glory fires first in `decide()` and `return`s before the AD check ever
    runs, whenever HP < 55% and mana is available (see
    ProtPalPolicy.decide, step 1) — so a single chip hit just gets healed
    back up without AD ever being considered. 16 identical 900k chip hits
    (verified empirically) grind HP down in a slow net bleed that costs
    WoG 50k mana each time it fires, exhausting the 250k pool (5 casts)
    by the last few hits — only once mana runs dry does a decide() call
    with HP already <40% finally reach the AD press. The 17th hit is
    lethal, landing 1s after the press, comfortably inside the 12s
    window."""
    char = make_pal(armor=0, vers=0)
    events = [
        DamageEvent(
            time_s=float(i),
            source_id="test",
            school="magic",
            raw_amount=900_000,
            attack_type="spell",
        )
        for i in range(1, 17)
    ] + [
        DamageEvent(  # lethal, 1s after the 16th hit presses AD (buff good until +12s)
            time_s=17.0,
            source_id="test",
            school="magic",
            raw_amount=20_000_000,
            attack_type="spell",
        )
    ]
    healing = HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)
    result = run_simulation(
        char,
        damage_profile=None,
        healing_profile=healing,
        events_override=events,
        iterations=1,
        compute_metrics=False,
    )
    assert result.deaths == 0


def test_sentinel_absorb_accumulates():
    """Policy.tick should accumulate Sentinel absorb over time, capped at sentinel_max."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    char = make_pal()
    state = MitigationState(char)
    policy = ProtPalPolicy(char)

    policy.tick(state, 10.0)
    expected = state.max_hp * spec_cfg["sentinel_absorb_pct_per_10s"]
    assert abs(state.sentinel_absorb - expected) < 1.0

    # Run far in the future — should hit cap
    policy.tick(state, 10000.0)
    cap = state.max_hp * spec_cfg["sentinel_max_pct_of_max_hp"]
    assert abs(state.sentinel_absorb - cap) < 1.0


def test_consecration_reduces_all_damage():
    """Consecration averaged DR should reduce both physical and magic damage."""
    c = load_constants()
    spec_cfg = c["specs"]["protection_paladin"]
    cons_dr = spec_cfg["consecration_dr"] * spec_cfg["consecration_avg_uptime"]
    # The pal mitigation chain applies cons_dr unconditionally; verify magic damage
    # falls by at least the cons_dr fraction relative to raw input (post-vers).
    char = make_pal(vers=0)
    state = MitigationState(char)
    result = apply_protection_paladin_mitigation(state, magic_event(1_000_000), random.Random(0))
    # No vers/mastery → only consecration + AD-related mastery DR reduces the magic hit
    assert result["dealt"] < 1_000_000 * (1 - cons_dr) + 1
