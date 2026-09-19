"""Regression tests for src/simf/core/profiles.py's scale_damage_profile.

Covers the magic-cast scaling gap: scale_damage_profile scaled a mob's
swing_damage_mean and tank-buster damage by the key-level multiplier but
never scaled MobSpec.casts, because dataclasses.replace() silently no-ops
on any field you don't name explicitly. See
docs/validation/magic_cast_scaling_gap_2026_07_09.md for the full root
cause and CONTRIBUTING.md's "Next priorities" item #11.
"""

import pytest

from simf.core.profiles import DamageProfile, MobSpec, TankBuster, scale_damage_profile


def _make_profile() -> DamageProfile:
    mob = MobSpec(
        count=1,
        swing_timer_s=2.0,
        swing_damage_mean=10000.0,
        swing_damage_variance=0.1,
        school="physical",
        attack_type="melee",
        casts=[
            {
                "spell": "boss_arcane_blast",
                "cadence_s": 8.0,
                "damage_mean": 180000.0,
                "school": "arcane",
            }
        ],
    )
    tb = TankBuster(time_s=30.0, damage=50000.0, school="fire", attack_type="spell")
    return DamageProfile(
        profile="test_profile",
        duration_s=300.0,
        mobs=[mob],
        tank_busters=[tb],
    )


def _magic_fraction(profile: DamageProfile, swings_per_cast_window: float = 1.0) -> float:
    """Compute magic-damage share of total damage for a profile.

    Uses a fixed number of swing "ticks" per profile so the ratio is a
    stable, comparable quantity across scaled/unscaled profiles (the actual
    swing cadence doesn't matter for an invariance check -- only that the
    same tick count is used before and after scaling).
    """
    physical_total = 0.0
    magic_total = 0.0
    for mob in profile.mobs:
        # A fixed number of swings, independent of multiplier, to represent
        # "physical damage dealt over the fight" for this ratio check.
        physical_total += mob.swing_damage_mean * swings_per_cast_window * mob.count
        for cast in mob.casts:
            magic_total += cast["damage_mean"] * mob.count
    for tb in profile.tank_busters:
        if tb.school == "physical":
            physical_total += tb.damage
        else:
            magic_total += tb.damage
    total = physical_total + magic_total
    return magic_total / total


def test_scale_damage_profile_scales_cast_damage_mean():
    """Regression test: casts must scale, not pass through unscaled."""
    profile = _make_profile()
    multiplier = 2.0

    scaled = scale_damage_profile(profile, multiplier)

    original_cast = profile.mobs[0].casts[0]["damage_mean"]
    scaled_cast = scaled.mobs[0].casts[0]["damage_mean"]
    assert scaled_cast == original_cast * multiplier


def test_scale_damage_profile_preserves_school_mix_ratio():
    """The physical/magic damage-share ratio must be invariant under scaling.

    This is the property the bug violated: modeled magic-damage share
    silently shrank as the key-level multiplier rose, because swings/
    tank-busters scaled but casts didn't. Scaling every damage source by the
    same multiplier must leave the physical/magic split unchanged.
    """
    profile = _make_profile()
    baseline_ratio = _magic_fraction(profile)

    for multiplier in (1.0, 1.3, 2.0):
        scaled = scale_damage_profile(profile, multiplier)
        scaled_ratio = _magic_fraction(scaled)
        assert scaled_ratio == pytest.approx(baseline_ratio, rel=1e-9)


def test_scale_damage_profile_scales_swing_and_tank_buster_damage():
    """Don't regress the swing/tank-buster scaling while fixing casts."""
    profile = _make_profile()
    multiplier = 1.5

    scaled = scale_damage_profile(profile, multiplier)

    assert scaled.mobs[0].swing_damage_mean == profile.mobs[0].swing_damage_mean * multiplier
    assert scaled.tank_busters[0].damage == profile.tank_busters[0].damage * multiplier
