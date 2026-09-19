"""ΔeHP framing helper — `+0.50% (+5,000 eHP)`, percentage first.

Three independent usability reviews (novice_tank, engaged_tank,
ui-craft-critic — 2026-07-04) converged on the same complaint: the raw eHP
number reads as unparseable noise next to a verdict, while the percentage
is what a player actually judges significance by. The prior version led
with raw eHP and DROPPED the percentage entirely below `threshold_pct`
(engaged_tank Round 2, 2026-05-16) — but that made one card in a same-grid
comparison look broken next to siblings that kept their percentage.
`_format_ehp_delta` now always shows a percentage (rounding to "≈0%"
instead of vanishing) and leads with it; the raw eHP moves to a muted
parenthetical.
"""

from __future__ import annotations

from unittest.mock import patch


def _with_baseline(baseline: float):
    """Stub the session-state baseline so we can test the formatter purely."""
    return patch("simf.ui.state._baseline_ehp", return_value=baseline)


def test_meaningful_positive_pct_leads():
    """A delta that's ~0.5% of pool renders percentage-first, raw eHP in parens."""
    from simf.ui.app import _format_ehp_delta

    with _with_baseline(1_000_000.0):
        # 5000 / 1M = 0.50% — above default 0.10% threshold
        assert _format_ehp_delta(5000) == "+0.50% (+5,000 eHP)"


def test_negative_delta_renders_negative_pct():
    from simf.ui.app import _format_ehp_delta

    with _with_baseline(1_000_000.0):
        assert _format_ehp_delta(-1500) == "-0.15% (-1,500 eHP)"


def test_below_threshold_pct_shows_approx_zero_not_missing():
    """Below the noise floor the exact % reads as precision theatre, but the
    percentage must still be PRESENT — a same-grid sibling card with a real
    percentage next to a card with none reads as a bug (ui-craft-critic,
    2026-07-04), not as intentional noise suppression."""
    from simf.ui.app import _format_ehp_delta

    with _with_baseline(1_000_000.0):
        # 660 / 1M = 0.066% — below default 0.10% threshold
        assert _format_ehp_delta(660) == "≈0% (+660 eHP)"
    with _with_baseline(10_000_000.0):
        # 1 eHP on 10M = 0.00001% — also rounds to the noise floor
        assert _format_ehp_delta(1) == "≈0% (+1 eHP)"


def test_thousands_separator_in_raw():
    """Big swings need readable commas — 14,550 not 14550."""
    from simf.ui.app import _format_ehp_delta

    with _with_baseline(1_000_000.0):
        assert "-14,550" in _format_ehp_delta(-14_550)


def test_implausible_baseline_falls_back_to_ehp_only():
    """A near-zero baseline (gear stats didn't resolve) must never print a
    nonsense percentage off that denominator — fall back to the raw eHP
    delta with no percentage at all, whatever the delta's own magnitude."""
    from simf.ui.app import _format_ehp_delta

    with _with_baseline(0.0):
        assert _format_ehp_delta(100) == "+100 eHP"


def test_threshold_pct_argument_overrides_default():
    from simf.ui.app import _format_ehp_delta

    with _with_baseline(1_000_000.0):
        # 1000 eHP on 1M = 0.10% — at this raised threshold, it still rounds away
        assert _format_ehp_delta(1000, threshold_pct=0.20) == "≈0% (+1,000 eHP)"
        # Lower threshold lets the exact % through
        assert _format_ehp_delta(1000, threshold_pct=0.05) == "+0.10% (+1,000 eHP)"
