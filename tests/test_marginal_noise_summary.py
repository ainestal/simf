"""Tests for `ui.marginals.summarize_marginal_noise` (Active Triage Queue
Batch F, 2026-07-08) — the pure formatter that turns a per-stat bootstrap CI
into the "Calibration details" popover's noise-summary line.
"""

from __future__ import annotations

from simf.ui.marginals import summarize_marginal_noise

_MARGINALS = {
    "stamina": {"p": 1.5, "m": 1.2},
    "versatility_rating": {"p": 500.0, "m": 90.0},
    "haste_rating": {"p": 180.0, "m": 1.8},
    "crit_rating": {"p": 26.0, "m": 1.0},
    "mastery_rating": {"p": 0.0, "m": 0.0},
    "armor_from_gear": {"p": 4000.0, "m": 0.0},
}


def test_returns_none_when_ci_is_none():
    assert summarize_marginal_noise(_MARGINALS, None) is None


def test_returns_none_when_ci_is_empty_dict():
    assert summarize_marginal_noise(_MARGINALS, {}) is None


def test_formats_relative_half_width_widest_first():
    ci = {
        "versatility_rating": {"p": (458.3, 669.1), "m": None},  # ±21% of 500
        "crit_rating": {"p": (21.8, 31.8), "m": None},  # ±19% of 26, narrower abs but similar %
        "haste_rating": {"p": (140.6, 227.1), "m": None},  # ±24% of 180
    }
    out = summarize_marginal_noise(_MARGINALS, ci)
    assert out is not None
    # haste has the widest relative half-width (~24%) — must lead.
    assert out.startswith("haste ±24%")
    assert "versatility ±21%" in out
    assert "crit ±19%" in out


def test_skips_stats_with_none_ci():
    ci = {
        "versatility_rating": {"p": (458.3, 669.1), "m": None},
        "haste_rating": {"p": None, "m": None},
    }
    out = summarize_marginal_noise(_MARGINALS, ci)
    assert out == "versatility ±21%"


def test_skips_stat_with_zero_point_estimate():
    """A noise-floor-zeroed marginal (point == 0) has an undefined relative
    width — must never divide by zero or claim a bogus ±inf%."""
    ci = {"mastery_rating": {"p": (-5.0, 5.0), "m": None}}
    assert summarize_marginal_noise(_MARGINALS, ci) is None


def test_returns_none_when_every_stat_is_suppressed():
    ci = {"haste_rating": {"p": None, "m": None}, "mastery_rating": {"p": (-5.0, 5.0), "m": None}}
    assert summarize_marginal_noise(_MARGINALS, ci) is None


def test_uses_human_readable_stat_labels():
    ci = {"versatility_rating": {"p": (458.3, 669.1), "m": None}}
    out = summarize_marginal_noise(_MARGINALS, ci)
    assert out is not None
    assert "versatility" in out
    assert "versatility_rating" not in out
