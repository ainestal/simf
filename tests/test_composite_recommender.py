"""Unit tests for the gear-tab composite scorer.

`_composite_score` is the math behind the Survivability ↔ DPS slider —
linear blend of ΔeHP and (ΔDPS × _dps_to_ehp_ratio()) under a 0-100% bias.
"""

from __future__ import annotations

import pytest

from simf.ui.app import _composite_score, _dps_to_ehp_ratio


def test_pure_survivability_ignores_dps():
    """surv=100 → composite is exactly ΔeHP; ΔDPS contribution is zero."""
    assert _composite_score(1000, 5.0, 100) == pytest.approx(1000.0)
    assert _composite_score(-500, 99.0, 100) == pytest.approx(-500.0)


def test_pure_dps_ignores_ehp():
    """surv=0 → composite is ΔDPS × _dps_to_ehp_ratio(); ΔeHP contribution is zero."""
    assert _composite_score(99_999, 1.0, 0) == pytest.approx(_dps_to_ehp_ratio())
    assert _composite_score(-99_999, -0.5, 0) == pytest.approx(-0.5 * _dps_to_ehp_ratio())


def test_balanced_50_50_averages_the_axes():
    assert _composite_score(1000, 1.0, 50) == pytest.approx(0.5 * 1000 + 0.5 * _dps_to_ehp_ratio())


def test_default_70_surv_biases_toward_ehp():
    """Default tank bias — a +1000 eHP / +0 DPS swap should outscore a
    +0 eHP / +1 DPS swap, since vers-style ΔDPS ≈ 1 maps to ~_dps_to_ehp_ratio()
    pseudo-eHP at 30% weight and ΔeHP gets 70%."""
    ehp_only = _composite_score(1000, 0.0, 70)
    dps_only = _composite_score(0, 1.0, 70)
    assert ehp_only > dps_only


def test_weight_outside_range_clamps():
    """Out-of-range surv_pct must clamp to [0, 100] — the slider's UI
    constrains this, but defensive clamp prevents bad URL params from
    flipping the sign."""
    assert _composite_score(1000, 0.0, 150) == _composite_score(1000, 0.0, 100)
    assert _composite_score(1000, 0.0, -10) == _composite_score(1000, 0.0, 0)


def test_negative_delta_passes_through():
    """A downgrade in both axes should produce a negative composite — the
    UI relies on `composite > baseline_zero` to gate 'is this a swap?'."""
    assert _composite_score(-1000, -1.0, 50) < 0


def test_slider_flips_recommendation_when_axes_disagree():
    """Two candidates, one wins survivability and the other wins DPS.
    Sliding the bias must flip which one wins composite."""
    # Candidate A: +1000 eHP, -0.5 DPS-score
    # Candidate B: -200 eHP, +1.0 DPS-score
    pure_surv_a = _composite_score(1000, -0.5, 100)
    pure_surv_b = _composite_score(-200, 1.0, 100)
    assert pure_surv_a > pure_surv_b  # A wins under 100% surv

    pure_dps_a = _composite_score(1000, -0.5, 0)
    pure_dps_b = _composite_score(-200, 1.0, 0)
    assert pure_dps_b > pure_dps_a  # B wins under 0% surv (pure DPS)
