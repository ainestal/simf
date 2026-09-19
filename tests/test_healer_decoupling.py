"""Tests that baseline_hps_abs decouples healer model from mitigation calibration."""

from dataclasses import replace

from simf.core.character import Character
from simf.core.profiles import load_damage_profile, load_healing_profile
from simf.core.runner import run_simulation


def _make_brutoh() -> Character:
    return Character(
        name="t",
        race="earthen",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=34176,
        armor_from_gear=5015,
        haste_rating=2318,
        crit_rating=1391,
        mastery_rating=1608,
        versatility_rating=296,
        max_hp_override=751872,
    )


def test_abs_hps_overrides_coefficient():
    """sim_dtps is stable when baseline_hps_pct changes but baseline_hps_abs is set."""
    char = _make_brutoh()
    dmg = load_damage_profile("m+_pull_caster")
    heal_base = load_healing_profile("m+_high_key_healer")

    abs_hps = 50_000.0  # fixed HPS regardless of coefficient

    heal_a = replace(heal_base, baseline_hps_abs=abs_hps, baseline_hps_pct_of_dtps=0.10)
    heal_b = replace(heal_base, baseline_hps_abs=abs_hps, baseline_hps_pct_of_dtps=0.90)

    result_a = run_simulation(char, dmg, heal_a, iterations=500, seed=42)
    result_b = run_simulation(char, dmg, heal_b, iterations=500, seed=42)

    # With abs override, changing the coefficient should not move DTPS by more than 2%
    rel_diff = abs(result_a.mean_dtps - result_b.mean_dtps) / max(
        result_a.mean_dtps, result_b.mean_dtps
    )
    assert rel_diff < 0.02, (
        f"DTPS changed by {rel_diff:.1%} when only baseline_hps_pct changed "
        f"(a={result_a.mean_dtps:.0f}, b={result_b.mean_dtps:.0f}) — abs override not working"
    )


def test_abs_hps_zero_uses_coefficient_not_abs():
    """When baseline_hps_abs == 0, the sim uses the coefficient path, not a
    fixed HPS. Verified by checking that setting abs to a large value changes
    the computed HPS — if abs were ignored, DTPS would be identical."""
    char = _make_brutoh()
    dmg = load_damage_profile("m+_pull_caster")
    heal_base = load_healing_profile("m+_high_key_healer")

    # abs=0 → coefficient drives HPS (low healing)
    # abs=500_000 → flat 500k HPS (much more healing than coefficient would give)
    heal_coeff = replace(heal_base, baseline_hps_abs=0.0, baseline_hps_pct_of_dtps=0.10)
    heal_abs = replace(heal_base, baseline_hps_abs=500_000.0)

    result_coeff = run_simulation(char, dmg, heal_coeff, iterations=200, seed=42)
    result_abs = run_simulation(char, dmg, heal_abs, iterations=200, seed=42)

    # 500k HPS should produce noticeably lower DTPS than coefficient-only HPS
    # (more healing → higher HP → IP triggers earlier → better absorb timing)
    assert result_abs.mean_dtps != result_coeff.mean_dtps, (
        "abs override and coefficient path produced identical DTPS — abs may be ignored"
    )
