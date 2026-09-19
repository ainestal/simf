"""Tests for Phase 2.10 — the SkillLadder sweep layer.

The skill ladder runs `run_simulation` once per `skill_tier` at a fixed
key level and returns the four results packaged for the verdict-card UI.
Pinning here:

1. The ladder produces one point per tier, in the same order as the
   constants file (top tier first).
2. The top point's death rate equals what `run_simulation` returns at
   the same seed without `skill_modifier` — the ladder doesn't perturb
   the baseline number the headline verdict shows.
3. Lower tiers produce higher death rates in expectation (statistical;
   small noise budget against a mid-key profile).
4. Tier metadata round-trips: focus + description + label end up on
   each point so the UI can render coaching content without re-loading
   constants.
"""

from __future__ import annotations

import pytest

from simf.core.character import Character
from simf.core.constants import load_skill_tiers
from simf.core.profiles import DamageProfile, HealingProfile, MobSpec
from simf.core.runner import run_simulation
from simf.core.skill_ladder import compute_skill_ladder


@pytest.fixture
def heavy_tankbuster_profile() -> DamageProfile:
    """A profile lethal enough that lower tiers produce visible deaths even
    at 50 iter, without being so brutal that the top tier dies too. Tuned
    once and pinned — the tests pivot on differential signals, not on
    exact death rates."""
    return DamageProfile(
        profile="test_lethal_tankbuster",
        duration_s=60.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=1.5,
                swing_damage_mean=85_000.0,
                swing_damage_variance=10_000.0,
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


def test_ladder_produces_one_point_per_tier(
    brutoh: Character, heavy_tankbuster_profile, healing_profile
):
    ladder = compute_skill_ladder(
        character=brutoh,
        damage_profile=heavy_tankbuster_profile,
        healing_profile=healing_profile,
        key_level=14,
        iterations=30,  # smoke — small for speed
    )
    tiers = load_skill_tiers()
    assert len(ladder.points) == len(tiers)
    assert [p.tier_id for p in ladder.points] == [t["id"] for t in tiers]


def test_ladder_top_point_matches_baseline_run(
    brutoh: Character, heavy_tankbuster_profile, healing_profile
):
    """The top of the ladder (modifier=1.0) at seed=42 must match a direct
    `run_simulation` call at the same seed — the ladder doesn't perturb
    the headline death rate the rest of the UI shows."""
    ladder = compute_skill_ladder(
        character=brutoh,
        damage_profile=heavy_tankbuster_profile,
        healing_profile=healing_profile,
        key_level=14,
        iterations=40,
        seed=42,
    )
    # Reproduce the same scaled-profile sim outside the ladder. The ladder
    # scales the damage profile by the key-level multiplier internally; do
    # the same here so we compare apples to apples.
    from simf.core.key_level_verdict import key_level_multiplier
    from simf.core.profiles import scale_damage_profile

    mult = key_level_multiplier(14)
    scaled = scale_damage_profile(heavy_tankbuster_profile, mult)
    baseline = run_simulation(
        character=brutoh,
        damage_profile=scaled,
        healing_profile=healing_profile,
        iterations=40,
        seed=42,
    )
    top = ladder.points[0]
    assert top.modifier == 1.0
    assert top.death_rate == baseline.death_rate
    assert top.mean_dtps == baseline.mean_dtps


def test_ladder_lower_tier_has_higher_or_equal_death_rate(
    brutoh: Character, heavy_tankbuster_profile, healing_profile
):
    """Sanity gradient — bottom of the ladder dies more than top. Strict
    monotonicity is too brittle at 50 iter (RNG noise); the weaker
    bottom-vs-top claim is reliable."""
    ladder = compute_skill_ladder(
        character=brutoh,
        damage_profile=heavy_tankbuster_profile,
        healing_profile=healing_profile,
        key_level=18,  # high enough that the modifier matters
        iterations=60,
    )
    top = ladder.points[0]
    bottom = ladder.points[-1]
    assert bottom.death_rate >= top.death_rate, (
        f"bottom tier should die at least as often as top tier; "
        f"got top={top.death_rate:.3f} bottom={bottom.death_rate:.3f}"
    )


def test_ladder_tier_metadata_round_trips(
    brutoh: Character, heavy_tankbuster_profile, healing_profile
):
    """Label, focus, and description from constants.yaml must surface on
    each `SkillLadderPoint` so the UI can render coaching content without
    re-loading constants."""
    ladder = compute_skill_ladder(
        character=brutoh,
        damage_profile=heavy_tankbuster_profile,
        healing_profile=healing_profile,
        key_level=14,
        iterations=10,
    )
    tiers = load_skill_tiers()
    for point, tier in zip(ladder.points, tiers, strict=True):
        assert point.label == tier["label"]
        assert point.focus == tier["focus"]
        assert point.description == tier["description"]


def test_ladder_with_empty_tiers_returns_empty(
    brutoh: Character, heavy_tankbuster_profile, healing_profile
):
    """Defensive: an empty `skill_tiers` config returns an empty ladder
    rather than raising — UI should render 'no ladder available'."""
    ladder = compute_skill_ladder(
        character=brutoh,
        damage_profile=heavy_tankbuster_profile,
        healing_profile=healing_profile,
        key_level=14,
        iterations=10,
        tiers=[],
    )
    assert ladder.points == []
    assert ladder.key_level == 14


def test_bottom_tier_relabels_at_high_keys(
    brutoh: Character, heavy_tankbuster_profile, healing_profile
):
    """At key ≥ `high_key_min_level` (15 today), the bottom tier should
    surface its high-key label ("Off your usual game") rather than the
    base "Learning the buttons" label. The base label describes a player
    population that doesn't queue +15+ keys — they got filtered by the
    +14-17 progression gate (multi-agent review 2026-05-22).

    `tier_id` must remain stable across the relabel so inferred-tier
    matching keyed on `id` still works.
    """
    low_key = compute_skill_ladder(
        character=brutoh,
        damage_profile=heavy_tankbuster_profile,
        healing_profile=healing_profile,
        key_level=10,  # below relabel threshold
        iterations=10,
    )
    high_key = compute_skill_ladder(
        character=brutoh,
        damage_profile=heavy_tankbuster_profile,
        healing_profile=healing_profile,
        key_level=18,  # above relabel threshold
        iterations=10,
    )
    # Bottom tier copy differs across the threshold.
    assert low_key.points[-1].label == "Learning the buttons"
    assert high_key.points[-1].label == "Off your usual game"
    # tier_id is stable so inferred-tier matching still works.
    assert low_key.points[-1].tier_id == high_key.points[-1].tier_id == "learning"
    # Focus + description swap too — the high-key versions don't talk
    # about "physical trash" coaching that doesn't apply at +18.
    assert "tired night" in high_key.points[-1].description
    assert "Cycle Shield Block" in high_key.points[-1].focus
    # Other tiers are unaffected — only `learning` opts into high-key
    # copy in the YAML today.
    for low, high in zip(low_key.points[:-1], high_key.points[:-1], strict=True):
        assert low.label == high.label
        assert low.focus == high.focus
        assert low.description == high.description


def test_relabel_threshold_boundary():
    """The threshold is `key_level >= high_key_min_level` — at exactly
    the threshold value, the relabel applies. Pin the boundary so a
    future off-by-one in the comparison surfaces here."""
    from simf.core.skill_ladder import _label_for_key_level

    tier = {
        "id": "learning",
        "label": "Learning the buttons",
        "description": "low",
        "focus": "low focus",
        "high_key_min_level": 15,
        "high_key_label": "Off your usual game",
        "high_key_description": "high",
        "high_key_focus": "high focus",
    }
    assert _label_for_key_level(tier, 14)[0] == "Learning the buttons"
    assert _label_for_key_level(tier, 15)[0] == "Off your usual game"
    assert _label_for_key_level(tier, 18)[0] == "Off your usual game"


def test_tier_without_high_key_fields_unchanged():
    """A tier with no `high_key_*` fields returns its base copy at any
    key — defensive against partial YAML migrations."""
    from simf.core.skill_ladder import _label_for_key_level

    tier = {"id": "in_the_zone", "label": "In the zone", "focus": "f", "description": "d"}
    assert _label_for_key_level(tier, 5)[0] == "In the zone"
    assert _label_for_key_level(tier, 20)[0] == "In the zone"
