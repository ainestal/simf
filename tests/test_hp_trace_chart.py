"""HP-over-time chart helper tests (2026-07-28 scoping workflow).

Pins the pure-data math (``compute_hp_trace``). Render-layer coverage is
deferred to AppTest smoke, mirroring ``tail_risk_chart``'s own split
(see ``test_tail_risk_chart.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from simf.ui.helpers.hp_trace_chart import HpTrace, compute_hp_trace


@dataclass
class _FakePoint:
    """Minimal KeyLevelPoint-shape stub — only the fields the chart reads."""

    sample_max_hp: float = 0.0
    sample_duration_s: float = 0.0
    sample_damage_timeline: list[tuple[float, float]] = field(default_factory=list)
    sample_heal_timeline: list[tuple[float, float]] = field(default_factory=list)
    sample_died: bool = False
    sample_time_to_die_s: float | None = None


def test_none_when_sample_max_hp_missing():
    pt = _FakePoint(
        sample_max_hp=0.0,
        sample_damage_timeline=[(1.0, 1000.0)],
    )
    assert compute_hp_trace(pt) is None


def test_none_when_both_timelines_empty():
    """A stub/legacy point with no recorded events must not fabricate a
    flat 100% line — that would misrepresent 'no data' as 'no damage.'"""
    pt = _FakePoint(sample_max_hp=100_000.0)
    assert compute_hp_trace(pt) is None


def test_simple_damage_then_heal_reconstructs_hp_pct():
    pt = _FakePoint(
        sample_max_hp=100_000.0,
        sample_duration_s=10.0,
        sample_damage_timeline=[(2.0, 40_000.0)],
        sample_heal_timeline=[(5.0, 10_000.0)],
    )
    trace = compute_hp_trace(pt)
    assert trace is not None
    assert trace.died is False
    assert trace.death_time_s is None
    # Starts full, drops to 60% at t=2, heals to 70% at t=5, then holds
    # flat to the full duration (10s) since nothing else happens.
    assert trace.times_s == [0.0, 2.0, 5.0, 10.0]
    assert trace.hp_pct == [100.0, 60.0, 70.0, 70.0]


def test_events_out_of_order_are_sorted_by_time():
    pt = _FakePoint(
        sample_max_hp=100_000.0,
        sample_duration_s=10.0,
        sample_damage_timeline=[(5.0, 20_000.0)],
        sample_heal_timeline=[(2.0, 5_000.0)],
    )
    trace = compute_hp_trace(pt)
    assert trace is not None
    # Heal at t=2 first (capped at max_hp, so no visible change from 100),
    # then damage at t=5 drops to 80%.
    assert trace.times_s == [0.0, 2.0, 5.0, 10.0]
    assert trace.hp_pct == [100.0, 100.0, 80.0, 80.0]


def test_damage_clamped_to_zero_not_negative():
    pt = _FakePoint(
        sample_max_hp=100_000.0,
        sample_damage_timeline=[(1.0, 150_000.0)],
    )
    trace = compute_hp_trace(pt)
    assert trace is not None
    assert trace.hp_pct[-1] == 0.0


def test_died_sample_truncates_at_death_not_cosmetic_reset():
    """The engine resets HP to a near-zero absolute value after death to
    keep window-stat accumulation running (see runner.py) — replaying
    events past that point would fabricate a recovery the tank never had.
    A later heal event must NOT appear in the reconstructed curve."""
    pt = _FakePoint(
        sample_max_hp=100_000.0,
        sample_duration_s=20.0,
        sample_damage_timeline=[(10.0, 100_000.0)],
        sample_heal_timeline=[(15.0, 50_000.0)],  # would-be post-death heal
        sample_died=True,
        sample_time_to_die_s=10.0,
    )
    trace = compute_hp_trace(pt)
    assert trace is not None
    assert trace.died is True
    assert trace.death_time_s == 10.0
    assert trace.times_s == [0.0, 10.0]
    assert trace.hp_pct == [100.0, 0.0]


def test_surviving_sample_extends_flat_to_full_duration():
    pt = _FakePoint(
        sample_max_hp=100_000.0,
        sample_duration_s=30.0,
        sample_damage_timeline=[(1.0, 10_000.0)],
    )
    trace = compute_hp_trace(pt)
    assert trace is not None
    assert trace.times_s[-1] == 30.0
    assert trace.hp_pct[-1] == 90.0


def test_died_sample_does_not_extend_past_death():
    pt = _FakePoint(
        sample_max_hp=100_000.0,
        sample_duration_s=400.0,
        sample_damage_timeline=[(50.0, 100_000.0)],
        sample_died=True,
        sample_time_to_die_s=50.0,
    )
    trace = compute_hp_trace(pt)
    assert trace is not None
    assert trace.times_s[-1] == 50.0


def test_returns_hp_trace_dataclass_instance():
    pt = _FakePoint(sample_max_hp=1.0, sample_damage_timeline=[(0.5, 0.5)])
    trace = compute_hp_trace(pt)
    assert isinstance(trace, HpTrace)


# ─── Render-layer tests (streamlit-module monkeypatch, mirrors
# test_plotly_codex.py's render_pareto_scatter convention) ─────────────────


class _FakeSt:
    markdown_calls: ClassVar[list] = []
    caption_calls: ClassVar[list] = []
    captured: ClassVar[dict] = {}

    @staticmethod
    def markdown(*a, **kw):
        _FakeSt.markdown_calls.append(a[0] if a else "")

    @staticmethod
    def caption(*a, **kw):
        _FakeSt.caption_calls.append(a[0] if a else "")

    @staticmethod
    def plotly_chart(fig, **kw):
        _FakeSt.captured["fig"] = fig


def _reset_fake_st():
    _FakeSt.markdown_calls = []
    _FakeSt.caption_calls = []
    _FakeSt.captured = {}


def test_render_smoke_shows_headline_chart_and_healer_caveat(monkeypatch):
    import sys

    from simf.ui.helpers.hp_trace_chart import render_hp_trace_chart

    _reset_fake_st()
    monkeypatch.setitem(sys.modules, "streamlit", _FakeSt)

    pt = _FakePoint(
        sample_max_hp=100_000.0,
        sample_duration_s=30.0,
        sample_damage_timeline=[(2.0, 40_000.0), (20.0, 100_000.0)],
        sample_heal_timeline=[(5.0, 10_000.0)],
        sample_died=True,
        sample_time_to_die_s=20.0,
    )
    render_hp_trace_chart(pt, 18)

    assert any("HP over time at +18" in m for m in _FakeSt.markdown_calls)
    assert any("healer coefficient model" in c for c in _FakeSt.caption_calls)
    fig = _FakeSt.captured.get("fig")
    assert fig is not None, "render_hp_trace_chart did not call st.plotly_chart"
    from simf.ui.helpers.plotly_codex import CODEX_COLORWAY as _CODEX

    assert fig.layout.paper_bgcolor == "#f4f1ea", "Codex layout not applied"
    # HP line + the death marker trace.
    assert len(fig.data) == 2
    assert fig.data[1].marker.color == _CODEX[1]  # bronze death marker


def test_render_no_op_when_no_data(monkeypatch):
    import sys

    from simf.ui.helpers.hp_trace_chart import render_hp_trace_chart

    _reset_fake_st()
    monkeypatch.setitem(sys.modules, "streamlit", _FakeSt)

    render_hp_trace_chart(_FakePoint(sample_max_hp=0.0), 18)

    assert not _FakeSt.markdown_calls
    assert not _FakeSt.caption_calls
    assert not _FakeSt.captured
