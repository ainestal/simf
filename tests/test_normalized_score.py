"""Tests for Phase 6.2 / 6.3 — HRPS + Normalized Tank Score."""

from __future__ import annotations

import pytest

from simf.core.normalized_score import (
    BASELINE_DTPS,
    BASELINE_HRPS,
    compute_hrps,
    compute_normalized_tank_score,
)


def test_hrps_subtracts_self_sustain():
    """HRPS = (damage - self_heal) / duration. Self-sustain reduces the
    healer's required throughput."""
    # 100k damage / sec, 30k self-sustain / sec → 70k HRPS
    hrps = compute_hrps(total_damage_dealt=10_000_000, total_self_heal=3_000_000, duration_s=100)
    assert hrps == pytest.approx(70_000)


def test_hrps_clamps_to_zero_when_self_heal_exceeds_damage():
    """Defensive: self-sustain can't go negative HRPS."""
    hrps = compute_hrps(total_damage_dealt=100_000, total_self_heal=200_000, duration_s=10)
    assert hrps == 0.0


def test_hrps_zero_duration_returns_zero():
    """Defensive: duration_s == 0 must not divide-by-zero."""
    assert compute_hrps(1_000_000, 100_000, 0) == 0.0


def test_score_perfect_tank_returns_one():
    """No deaths + zero HRPS + zero DTPS → score 1.0."""
    score = compute_normalized_tank_score(death_rate=0.0, mean_hrps=0.0, mean_dtps=0.0)
    assert score == pytest.approx(1.0)


def test_score_hopeless_tank_returns_zero():
    """Death rate 1.0 + HRPS/DTPS at baseline → score is purely the
    weight share of the non-death components below their thresholds."""
    score = compute_normalized_tank_score(
        death_rate=1.0,
        mean_hrps=BASELINE_HRPS,
        mean_dtps=BASELINE_DTPS,
    )
    # All three components hit floor → score 0.0
    assert score == pytest.approx(0.0)


def test_score_weights_match_module_constants():
    """If hrps_norm and dtps_norm are 0 (both well under baseline), the
    score reduces to 1 - death_rate × WEIGHT_DEATH × (-1)... actually:
    death_component is `1 - death_rate` weighted by 0.40. With
    death_rate=0.5, score = 0.5×0.40 + 1.0×0.30 + 1.0×0.30 = 0.80."""
    score = compute_normalized_tank_score(death_rate=0.5, mean_hrps=0.0, mean_dtps=0.0)
    assert score == pytest.approx(0.5 * 0.40 + 1.0 * 0.30 + 1.0 * 0.30)


def test_score_clamps_to_zero_when_hrps_exceeds_baseline():
    """Above-baseline HRPS doesn't drag the score below 0 via that
    component alone — each component clamps independently."""
    score = compute_normalized_tank_score(
        death_rate=0.0,
        mean_hrps=BASELINE_HRPS * 3,  # way above baseline
        mean_dtps=0.0,
    )
    # death=1×0.40, hrps clamps to 0, dtps=1×0.30 → 0.70
    assert score == pytest.approx(0.40 + 0.0 + 0.30)


def test_score_in_unit_interval_for_realistic_inputs():
    """Sweep some realistic Brutoh-class inputs — every output must be
    in [0, 1]."""
    for dr in [0.0, 0.05, 0.20, 0.50]:
        for hrps in [10_000, 60_000, 120_000]:
            for dtps in [40_000, 80_000, 150_000]:
                score = compute_normalized_tank_score(dr, hrps, dtps)
                assert 0.0 <= score <= 1.0, (
                    f"score out of bounds at dr={dr}, hrps={hrps}, dtps={dtps}: {score}"
                )
