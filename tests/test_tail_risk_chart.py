"""Top-5 #2 (2026-07-06 retrospective) — tail-risk chart helper tests.

Pins the pure-data math (``compute_window_risk_bars``). Render-layer
coverage is deferred to AppTest smoke, mirroring the pareto_scatter
helper's split.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.ui.helpers.tail_risk_chart import WindowRiskBar, compute_window_risk_bars


@dataclass
class _FakePoint:
    """Minimal KeyLevelPoint-shape stub — only the fields the chart reads."""

    p99_5s_window: float = 0.0
    p99_10s_window: float = 0.0
    p99_15s_window: float = 0.0
    sample_max_hp: float = 0.0
    p5_min_hp_pct: float | None = None


def test_empty_when_sample_max_hp_missing():
    """Zero/absent max_hp must not divide-by-zero or fabricate a ratio —
    an empty chart is a cleaner failure than either."""
    pt = _FakePoint(p99_10s_window=1000.0, sample_max_hp=0.0)
    assert compute_window_risk_bars(pt) == []


def test_bars_expressed_as_pct_of_max_hp():
    pt = _FakePoint(
        p99_5s_window=200_000.0,
        p99_10s_window=400_000.0,
        p99_15s_window=600_000.0,
        sample_max_hp=800_000.0,
    )
    bars = compute_window_risk_bars(pt)
    # Window sums here are all "window_s * 40,000" by construction, so
    # peak_incoming_hps comes out equal (40,000) across all three bars —
    # a flat-rate sanity check that the HPS derivation is independent of
    # window length, not just a copy of pct_of_max_hp under a new name.
    assert bars == [
        WindowRiskBar("5s p99", 25.0, 5.0, 40_000.0),
        WindowRiskBar("10s p99", 50.0, 10.0, 40_000.0),
        WindowRiskBar("15s p99", 75.0, 15.0, 40_000.0),
    ]


def test_peak_incoming_hps_is_window_sum_over_window_length():
    """A real tapering burst profile — HPS must differ per window, not
    just inherit the same ratio as pct_of_max_hp."""
    pt = _FakePoint(
        p99_5s_window=250_000.0,  # 250,000 / 5 = 50,000 HPS
        p99_10s_window=400_000.0,  # 400,000 / 10 = 40,000 HPS
        p99_15s_window=450_000.0,  # 450,000 / 15 = 30,000 HPS
        sample_max_hp=800_000.0,
    )
    bars = {b.label: b for b in compute_window_risk_bars(pt)}
    assert bars["5s p99"].peak_incoming_hps == 50_000.0
    assert bars["10s p99"].peak_incoming_hps == 40_000.0
    assert bars["15s p99"].peak_incoming_hps == 30_000.0


def test_bars_can_exceed_100_pct_at_high_keys():
    """A worst-case window bigger than the whole health bar is a real,
    honest outcome at high keys (it's a burst-size measurement, not a
    claim about surviving it) — must not be clamped or hidden."""
    pt = _FakePoint(p99_10s_window=1_200_000.0, sample_max_hp=800_000.0)
    bars = compute_window_risk_bars(pt)
    ten_s = next(b for b in bars if b.label == "10s p99")
    assert ten_s.pct_of_max_hp == 150.0


def test_ordering_is_5s_10s_15s():
    pt = _FakePoint(sample_max_hp=100.0)
    bars = compute_window_risk_bars(pt)
    assert [b.label for b in bars] == ["5s p99", "10s p99", "15s p99"]
