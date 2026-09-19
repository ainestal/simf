"""Tests for Phase 2.10f — reaction-lag + Bernoulli on reactive/emergency CDs.

Three new gates land here on top of the 2.10d edge-triggered SB/DS sampler:

1. **Spell Reflect — Bernoulli per tank-buster.** SR is reactive (fires on
   an incoming spell tank-buster, not on a CD-elapsed tick), so the
   "opportunity" is the event itself. One Bernoulli draw per tank-buster.
2. **Shield Wall + Last Stand — reaction-lag.** Emergency CDs ALWAYS fire
   under sustained pressure (an emergency that never presses isn't a skill
   question — it's a different game), but a tired tank presses them late.
   Each HP-threshold cross samples a half-normal lag; the press fires when
   `now` reaches the schedule.

Bit-identity at modifier=1.0 must hold across all three. Pinned-seed tests
predating 2.10f assume zero new RNG consumption on the modifier=1.0 path.
"""

from __future__ import annotations

import random
from statistics import mean

import pytest

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.mitigation import MitigationState
from simf.core.policy import ActiveMitigationPolicy
from simf.core.profiles import (
    DamageProfile,
    HealingProfile,
    MobSpec,
    TankBuster,
)
from simf.core.runner import run_simulation

# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def spell_tankbuster_profile() -> DamageProfile:
    """Profile with periodic spell tank-busters so SR has opportunities to
    fire. Six tank-busters at 10s intervals over 60s; light filler damage
    in between to drive normal mitigation pressure without dominating."""
    return DamageProfile(
        profile="test_spell_tb",
        duration_s=60.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=2.0,
                swing_damage_mean=40_000.0,
                swing_damage_variance=3_000.0,
                school="physical",
            )
        ],
        tank_busters=[
            TankBuster(time_s=t, damage=200_000.0, school="shadow", attack_type="spell")
            for t in (8.0, 18.0, 28.0, 38.0, 48.0, 58.0)
        ],
    )


@pytest.fixture
def lethal_pressure_profile() -> DamageProfile:
    """Heavier swings — forces HP below 40% repeatedly so SW/LS reaction-
    lag matters. Tuned so SW + LS keep a Brutoh-tier tank alive most
    iterations even at modifier=0.0, matching the existing
    `test_modifier_zero_still_survives_via_emergency_cds` invariant."""
    return DamageProfile(
        profile="test_lethal",
        duration_s=60.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=2.0,
                swing_damage_mean=80_000.0,
                swing_damage_variance=6_000.0,
                school="physical",
            )
        ],
    )


@pytest.fixture
def healing_profile() -> HealingProfile:
    return HealingProfile(
        profile="test_healer",
        baseline_hps_pct_of_dtps=0.40,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.20,
        reactive_cooldown_s=30.0,
        externals=[],
    )


# ─── Reaction-lag helper unit tests ────────────────────────────────────────


def test_reaction_lag_is_zero_at_modifier_one(brutoh: Character):
    """At modifier=1.0 the lag short-circuits to 0.0 without consuming
    RNG. If this fails, bit-identity for every pinned-seed test breaks."""
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=1.0, skill_rng=None)
    c = load_constants()
    for _ in range(20):
        assert policy._roll_reaction_lag("shield_wall", c) == 0.0
        assert policy._roll_reaction_lag("last_stand", c) == 0.0


def test_reaction_lag_is_zero_when_rng_missing(brutoh: Character):
    """Guard against accidentally producing a non-zero lag when the runner
    didn't construct a skill_rng (which it skips at modifier>=1.0). Also
    a defensive check — any caller that forgets to pass an RNG gets
    bit-identical behaviour."""
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=0.4, skill_rng=None)
    c = load_constants()
    assert policy._roll_reaction_lag("shield_wall", c) == 0.0


def test_reaction_lag_mean_scales_with_modifier(brutoh: Character):
    """Half-normal mean scales linearly with (1 - modifier). A 0.4 tank
    reacts slower on average than a 0.85 tank. Direction matters; exact
    magnitude is calibration."""
    c = load_constants()
    means = {}
    for mod in (0.85, 0.65, 0.40):
        rng = random.Random(42)
        policy = ActiveMitigationPolicy(brutoh, skill_modifier=mod, skill_rng=rng)
        means[mod] = mean(policy._roll_reaction_lag("shield_wall", c) for _ in range(500))
    assert means[0.40] > means[0.65] > means[0.85] > 0.0, (
        f"reaction-lag mean should fall with rising modifier; got {means}"
    )


# ─── Bit-identity invariants ───────────────────────────────────────────────


def test_modifier_1_with_spell_tankbusters_still_bit_identical(
    brutoh: Character, spell_tankbuster_profile, healing_profile
):
    """The 2.10f SR Bernoulli must not consume RNG at modifier=1.0. If it
    did, pinned-seed results would silently shift the moment any spell
    tank-buster fires. Same shape as `test_modifier_1_is_bit_identical`
    in test_skill_modifier.py but exercises the SR gate explicitly via
    a profile that has tank-busters."""
    baseline = run_simulation(
        character=brutoh,
        damage_profile=spell_tankbuster_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
    )
    with_modifier = run_simulation(
        character=brutoh,
        damage_profile=spell_tankbuster_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
        skill_modifier=1.0,
    )
    assert baseline.death_rate == with_modifier.death_rate
    assert baseline.mean_dtps == with_modifier.mean_dtps
    assert baseline.p99_5s_window == with_modifier.p99_5s_window


def test_modifier_1_with_lethal_pressure_still_bit_identical(
    brutoh: Character, lethal_pressure_profile, healing_profile
):
    """SW + LS reaction-lag must not perturb the modifier=1.0 path. The
    half-normal short-circuits to 0 without an RNG draw — verified by
    end-to-end equality against the no-kwarg baseline on a profile that
    forces emergency CDs to fire."""
    baseline = run_simulation(
        character=brutoh,
        damage_profile=lethal_pressure_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
    )
    with_modifier = run_simulation(
        character=brutoh,
        damage_profile=lethal_pressure_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
        skill_modifier=1.0,
    )
    assert baseline.death_rate == with_modifier.death_rate
    assert baseline.mean_dtps == with_modifier.mean_dtps
    assert baseline.p99_5s_window == with_modifier.p99_5s_window


# ─── Behavioural shifts at modifier < 1.0 ──────────────────────────────────


def test_lower_modifier_raises_dtps_on_spell_tankbuster_profile(
    brutoh: Character, spell_tankbuster_profile, healing_profile
):
    """SR's Bernoulli means tank-busters that would have been reflected
    at modifier=1.0 now sometimes land in full. With six 200k spell
    tank-busters in the profile, the difference is measurable.

    Direction-only assertion — exact magnitude depends on which mitigation
    layers happen to overlap each tank-buster in each iteration."""
    iters = 120
    high = run_simulation(
        character=brutoh,
        damage_profile=spell_tankbuster_profile,
        healing_profile=healing_profile,
        iterations=iters,
        seed=42,
        skill_modifier=1.0,
    )
    low = run_simulation(
        character=brutoh,
        damage_profile=spell_tankbuster_profile,
        healing_profile=healing_profile,
        iterations=iters,
        seed=42,
        skill_modifier=0.0,
    )
    assert low.mean_dtps > high.mean_dtps, (
        f"modifier=0.0 should produce higher DTPS (SR doesn't fire); "
        f"got high={high.mean_dtps:.0f}, low={low.mean_dtps:.0f}"
    )


# ─── Direct policy-level timing — SW/LS reaction-lag ───────────────────────


def _make_low_hp_state(character: Character, hp_pct: float) -> MitigationState:
    """Build a MitigationState with HP at the requested fraction of max
    so the SW (<40%) or LS (<25%) threshold is crossed deterministically."""
    state = MitigationState(character)
    state.hp = state.max_hp * hp_pct
    return state


def test_sw_fires_immediately_at_modifier_1(brutoh: Character):
    """Bit-identity at modifier=1.0 means SW fires the same frame the
    threshold crosses (lag=0). The policy short-circuits before any RNG
    draw, so even with HP pinned at 30% (below the 40% trigger), the
    very first `decide()` call sets `shield_wall_until` in the future."""
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=1.0, skill_rng=None)
    state = _make_low_hp_state(brutoh, 0.30)
    now = 5.0
    policy.decide(state, now, recent_dtps=0.0, incoming_event=None)
    assert state.shield_wall_until > now, (
        f"SW should fire immediately at modifier=1.0; got "
        f"shield_wall_until={state.shield_wall_until}"
    )


def test_sw_fires_delayed_at_modifier_below_1(brutoh: Character):
    """At modifier < 1.0, the first `decide()` call schedules a press but
    does NOT fire SW. SW fires on a later call once `now` advances past
    the scheduled time. Pinned-seed RNG makes the lag deterministic.

    Concretely: at modifier=0.0 the half-normal pulls from sigma=2.0 →
    expected lag ~1.6s. seed=42 produces a finite, positive lag that
    we read back via `_sw_pending_press_at`."""
    rng = random.Random(42)
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=0.0, skill_rng=rng)
    state = _make_low_hp_state(brutoh, 0.30)
    t0 = 5.0
    policy.decide(state, t0, recent_dtps=0.0, incoming_event=None)
    # SW must NOT have fired yet — only scheduled.
    assert state.shield_wall_until < 0.0, (
        f"SW fired immediately at modifier=0.0; got shield_wall_until={state.shield_wall_until}"
    )
    assert policy._sw_pending_press_at is not None and policy._sw_pending_press_at > t0
    scheduled_at = policy._sw_pending_press_at
    # Advance past the scheduled time; SW should now fire.
    policy.decide(state, scheduled_at + 0.1, recent_dtps=0.0, incoming_event=None)
    assert state.shield_wall_until > scheduled_at, (
        f"SW failed to fire after scheduled time; got "
        f"shield_wall_until={state.shield_wall_until}, "
        f"scheduled={scheduled_at}"
    )


def test_sw_pending_press_clears_on_hp_recovery(brutoh: Character):
    """Stale-state safety. If HP recovers above 40% before the lag
    elapses, the scheduled press clears. Otherwise the next dip below
    40% (after the SW CD elapses) would trivially satisfy `now >=
    pending_press_at` and fire SW with no lag at all — defeating the
    skill gate (advisor flag, 2026-05-22)."""
    rng = random.Random(42)
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=0.0, skill_rng=rng)
    state = _make_low_hp_state(brutoh, 0.30)
    # Drop below threshold → schedule press
    policy.decide(state, 5.0, recent_dtps=0.0, incoming_event=None)
    assert policy._sw_pending_press_at is not None
    # Recover above threshold → schedule must clear
    state.hp = state.max_hp * 0.80
    policy.decide(state, 5.5, recent_dtps=0.0, incoming_event=None)
    assert policy._sw_pending_press_at is None, (
        "SW pending press should clear when HP recovers above the threshold"
    )


def test_ls_fires_immediately_at_modifier_1(brutoh: Character):
    """LS counterpart to SW immediate-press. HP < 25% with SW on CD
    AND SW's DR window already expired — LS fires on the first
    decide() at modifier=1.0.

    Pre-LS-reachability-fix this test would not pass — the LS condition
    used `now >= shield_wall_cd_until` which only allowed LS when SW
    was OFF cooldown (the exact state in which LS isn't needed). The
    fix uses a dual gate: SW window done + SW still on CD."""
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=1.0, skill_rng=None)
    state = _make_low_hp_state(brutoh, 0.20)
    # Force SW on cooldown AND window expired so LS is the natural choice.
    state.shield_wall_until = 0.0  # SW DR window already ended
    state.shield_wall_cd_until = 9999.0
    now = 5.0
    policy.decide(state, now, recent_dtps=0.0, incoming_event=None)
    assert state.last_stand_until > now, (
        f"LS should fire immediately at modifier=1.0; got last_stand_until={state.last_stand_until}"
    )


def test_ls_fires_delayed_at_modifier_below_1(brutoh: Character):
    """LS reaction-lag schedule matches SW. At modifier < 1.0 the first
    decide() schedules a press; the press fires once `now` advances
    past the scheduled time."""
    rng = random.Random(42)
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=0.0, skill_rng=rng)
    state = _make_low_hp_state(brutoh, 0.20)
    state.shield_wall_until = 0.0  # SW window already ended
    state.shield_wall_cd_until = 9999.0
    t0 = 5.0
    policy.decide(state, t0, recent_dtps=0.0, incoming_event=None)
    assert state.last_stand_until < 0.0, (
        f"LS fired immediately at modifier=0.0; got last_stand_until={state.last_stand_until}"
    )
    assert policy._ls_pending_press_at is not None and policy._ls_pending_press_at > t0
    scheduled_at = policy._ls_pending_press_at
    policy.decide(state, scheduled_at + 0.1, recent_dtps=0.0, incoming_event=None)
    assert state.last_stand_until > scheduled_at


def test_ls_does_not_fire_when_sw_window_is_active(brutoh: Character):
    """LS is a *last-resort* — it should not fire while SW's DR window
    is active. At modifier=1.0 with HP < 25% and SW off CD, the SW
    branch fires first and puts SW into its DR window; LS does NOT
    fire on the same decide() call. Pins the post-fix dual-gate
    reachability semantics ("SW spent AND its window ended")."""
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=1.0, skill_rng=None)
    state = _make_low_hp_state(brutoh, 0.20)
    # SW is OFF cooldown (-1.0 is initial state).
    now = 5.0
    policy.decide(state, now, recent_dtps=0.0, incoming_event=None)
    # SW fired first; LS did NOT (SW window still active = no LS).
    assert state.shield_wall_until > now, "SW should fire when HP<40% and SW off CD"
    assert state.last_stand_until < 0.0, (
        f"LS should NOT fire while SW's DR window is active; got "
        f"last_stand_until={state.last_stand_until}"
    )


def test_ls_fires_after_sw_window_expires(brutoh: Character):
    """End-to-end of the LS-as-true-last-resort flow. SW fires at HP<40%,
    its 8s DR window passes, HP stays low, LS then fires. Pre-fix this
    sequence was unreachable — `now >= shield_wall_cd_until` blocked LS
    for the next 240s, by which point the iteration would have ended."""
    policy = ActiveMitigationPolicy(brutoh, skill_modifier=1.0, skill_rng=None)
    state = _make_low_hp_state(brutoh, 0.20)
    t0 = 5.0
    policy.decide(state, t0, recent_dtps=0.0, incoming_event=None)
    assert state.shield_wall_until > t0 and state.last_stand_until < 0.0
    sw_window_end = state.shield_wall_until
    # Advance past SW window; HP still < 25%.
    state.hp = state.max_hp * 0.20
    policy.decide(state, sw_window_end + 0.1, recent_dtps=0.0, incoming_event=None)
    assert state.last_stand_until > sw_window_end, (
        f"LS should fire after SW window expires; got "
        f"last_stand_until={state.last_stand_until}, sw_window_end={sw_window_end}"
    )


# ─── Constants schema ──────────────────────────────────────────────────────


def test_skill_modifier_reaction_lag_constants_loaded():
    """The YAML block must exist; sigma values are positive floats. Pins
    against a constants_version drift that removes the block silently."""
    c = load_constants()
    cfg = c["skill_modifier_reaction_lag"]
    assert float(cfg["shield_wall_sigma_at_zero_s"]) > 0.0
    assert float(cfg["last_stand_sigma_at_zero_s"]) > 0.0
