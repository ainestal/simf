"""Tests for Phase 2.10 — engine layer of the Skill-Adjusted Verdict.

Three guarantees this file pins:

1. **Bit-identity at modifier=1.0.** Adding `skill_modifier` to the engine
   must not perturb any existing pinned-seed test. The policy short-circuits
   before any RNG draw when modifier >= 1.0, and the runner only constructs
   the separate skill_rng when modifier < 1.0. Without this guard, all 530
   existing tests would silently regress.
2. **Modifier semantics.** Lower modifier → lower mean Shield Block uptime.
   At modifier=0.0, opportunistic SB presses never fire.
3. **Emergency CDs are out of scope.** Shield Wall / Last Stand still fire
   when HP drops — those are NOT gated by skill_modifier. Verified by a
   smoke that exercises the lethal-HP branch with modifier=0.0.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from simf.core.character import Character
from simf.core.constants import load_skill_tiers
from simf.core.profiles import DamageProfile, HealingProfile, MobSpec
from simf.core.runner import run_simulation


@pytest.fixture
def tankbuster_profile() -> DamageProfile:
    """Moderate physical pressure — enough that SB uptime materially changes
    DTPS and produces measurable differences across tiers, but not so lethal
    that emergency CDs can't keep the tank alive at modifier=0. Mean damage
    tuned so a Brutoh-tier character survives most iterations at the
    bottom of the ladder."""
    return DamageProfile(
        profile="test_tankbuster",
        duration_s=60.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=2.0,
                swing_damage_mean=55_000.0,
                swing_damage_variance=5_000.0,
                school="physical",
            )
        ],
    )


@pytest.fixture
def healing_profile() -> HealingProfile:
    """Light healing — enough to keep alive, not so much that SB uptime is
    irrelevant to survival."""
    return HealingProfile(
        profile="test_healer",
        baseline_hps_pct_of_dtps=0.40,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.20,
        reactive_cooldown_s=30.0,
        externals=[],
    )


def test_modifier_1_is_bit_identical(brutoh: Character, tankbuster_profile, healing_profile):
    """The core safety claim — at modifier=1.0, results must match a call
    that omits the kwarg entirely. If this fails, the new RNG path is
    consuming randomness it shouldn't, and every existing pinned-seed
    test is at risk of silent drift."""
    baseline = run_simulation(
        character=brutoh,
        damage_profile=tankbuster_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
    )
    with_modifier = run_simulation(
        character=brutoh,
        damage_profile=tankbuster_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
        skill_modifier=1.0,
    )
    assert baseline.death_rate == with_modifier.death_rate
    assert baseline.mean_dtps == with_modifier.mean_dtps
    assert baseline.mean_sb_uptime == with_modifier.mean_sb_uptime
    assert baseline.p99_5s_window == with_modifier.p99_5s_window


def test_lower_modifier_reduces_sb_uptime(brutoh: Character, tankbuster_profile, healing_profile):
    """SB uptime falls as the modifier shrinks. Direction matters more than
    exact magnitude — small iter counts have noise — so the assertion is
    ordered comparison, not a fixed threshold."""
    uptimes = {}
    for mod in (1.0, 0.5, 0.0):
        result = run_simulation(
            character=brutoh,
            damage_profile=tankbuster_profile,
            healing_profile=healing_profile,
            iterations=80,
            seed=42,
            skill_modifier=mod,
        )
        uptimes[mod] = result.mean_sb_uptime

    assert uptimes[1.0] > uptimes[0.5], (
        f"modifier=1.0 should produce higher SB uptime than 0.5; "
        f"got {uptimes[1.0]:.3f} vs {uptimes[0.5]:.3f}"
    )
    assert uptimes[0.5] > uptimes[0.0], (
        f"modifier=0.5 should produce higher SB uptime than 0.0; "
        f"got {uptimes[0.5]:.3f} vs {uptimes[0.0]:.3f}"
    )


def test_modifier_zero_disables_sb_presses(brutoh: Character, tankbuster_profile, healing_profile):
    """At modifier=0.0, opportunistic SB presses never fire — uptime is
    pinned at zero (no initial-state coverage in this profile). This is
    the strict lower bound that proves the gate is operating, not just
    biasing."""
    result = run_simulation(
        character=brutoh,
        damage_profile=tankbuster_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
        skill_modifier=0.0,
    )
    assert result.mean_sb_uptime == 0.0, (
        f"modifier=0.0 should produce zero SB uptime; got {result.mean_sb_uptime:.4f}"
    )


def test_modifier_effect_is_invariant_to_event_density(brutoh: Character, healing_profile):
    """**Phase 2.10d regression** — the per-opportunity sampler must produce
    the same effective press rate regardless of damage-event density.

    The bug this test pins (validator flag 2026-05-20, user-reported
    2026-05-22): the per-`decide()` Bernoulli draw collapsed effective
    press rate to `1 - (1 - m) ^ K` for K events per pressable window.
    Two profiles with the same `skill_modifier` but 5× different event
    densities used to produce wildly different SB uptimes — the
    high-density scenario degenerated toward optimal play.

    Post-fix, one roll per logical press opportunity → SB uptime should
    match within sampling noise across event densities.
    """
    # Same total swing damage per second (mean × 1/timer) so DTPS is equal;
    # only the event count differs. 5× event density gap is enough to make
    # the per-event sampling bug visible (`1 - 0.6 ^ 5 = 92%` vs nominal 40%).
    sparse = DamageProfile(
        profile="sparse_events",
        duration_s=60.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=2.5,
                swing_damage_mean=80_000.0,
                swing_damage_variance=5_000.0,
                school="physical",
            )
        ],
    )
    dense = DamageProfile(
        profile="dense_events",
        duration_s=60.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=0.5,
                swing_damage_mean=16_000.0,
                swing_damage_variance=1_000.0,
                school="physical",
            )
        ],
    )
    modifier = 0.4
    sparse_result = run_simulation(
        character=brutoh,
        damage_profile=sparse,
        healing_profile=healing_profile,
        iterations=120,
        seed=42,
        skill_modifier=modifier,
    )
    dense_result = run_simulation(
        character=brutoh,
        damage_profile=dense,
        healing_profile=healing_profile,
        iterations=120,
        seed=42,
        skill_modifier=modifier,
    )
    # Tolerance: 10pp gap is the slack that absorbs Monte Carlo noise at
    # 120 iter without leaving the door open for the bug (which would
    # have given ~50pp gap — dense ≈ optimal, sparse ≈ nominal).
    assert abs(sparse_result.mean_sb_uptime - dense_result.mean_sb_uptime) < 0.10, (
        f"SB uptime differs by event density — sparse={sparse_result.mean_sb_uptime:.3f}, "
        f"dense={dense_result.mean_sb_uptime:.3f}. The per-event Bernoulli bug "
        f"may have regressed."
    )


def test_modifier_zero_still_survives_via_emergency_cds(
    brutoh: Character, tankbuster_profile, healing_profile
):
    """Even at modifier=0.0 (no SB / no Demo Shout presses), Shield Wall +
    Last Stand still fire at <40% / <25% HP. The character should not die
    instantly across all 50 iterations — emergency CDs hold them up. If
    this fails, the modifier accidentally gated an emergency CD it
    shouldn't have."""
    result = run_simulation(
        character=brutoh,
        damage_profile=tankbuster_profile,
        healing_profile=healing_profile,
        iterations=50,
        seed=42,
        skill_modifier=0.0,
    )
    assert result.death_rate < 1.0, (
        "modifier=0.0 produced 100% death rate — emergency CDs may be "
        "incorrectly gated by skill_modifier"
    )


# ─── Constants invariants ──────────────────────────────────────────────────


def test_skill_tiers_yaml_loads_with_four_tiers():
    tiers = load_skill_tiers()
    assert len(tiers) == 4
    assert all("id" in t and "label" in t and "modifier" in t for t in tiers)


def test_skill_tier_modifiers_strictly_decreasing():
    """Display order is top-tier-first; modifiers must walk monotonically
    down so 'higher rung = better play' is preserved everywhere."""
    tiers = load_skill_tiers()
    modifiers = [float(t["modifier"]) for t in tiers]
    for prev, nxt in pairwise(modifiers):
        assert prev > nxt, f"skill_tier modifiers not strictly decreasing: {modifiers}"


def test_skill_tier_top_modifier_is_one():
    """The top tier must be modifier=1.0 so the headline number on the
    verdict card (which uses optimal play) sits at the same value users
    see at the top of the ladder."""
    tiers = load_skill_tiers()
    assert float(tiers[0]["modifier"]) == 1.0


def test_skill_tier_ids_unique():
    tiers = load_skill_tiers()
    ids = [t["id"] for t in tiers]
    assert len(ids) == len(set(ids)), f"duplicate skill_tier ids: {ids}"


def test_skill_tier_focus_strings_non_empty():
    """The coaching layer is anchored on a non-empty focus string per
    tier — empty here would render a blank line under the ladder."""
    tiers = load_skill_tiers()
    for t in tiers:
        focus = t.get("focus", "")
        assert focus and len(focus.strip()) > 5, (
            f"tier {t['id']!r} has missing or trivially-short focus string"
        )
