"""Batch D — key-level risk timeline helper tests.

Pins the pure-data math (``compute_segment_risk_bars``), mirroring the
``tail_risk_chart`` / ``pareto_scatter`` split: render-layer coverage is a
no-op-on-empty smoke check plus a WCAG 1.4.1 pattern-fill regression check
here, with the DTPS numbers cross-checked against ``render_per_segment_
risk``'s own inline computation (via the same ``_StubStreamlit`` capture
pattern ``test_per_pull_surfacing_render.py`` uses) rather than
re-asserting against this module's own formula.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.io.combat_log import ChallengeModeRun, DamageTakenEvent, DeathRecord, RunSegment
from simf.ui import log_segment_risk
from simf.ui.helpers.segment_risk_timeline import (
    SegmentRiskBar,
    compute_segment_risk_bars,
    render_segment_risk_timeline,
)


def _evt(t: float, amount: int, base_amount: int | None = None) -> DamageTakenEvent:
    return DamageTakenEvent(
        time_s=t,
        event_type="SPELL_DAMAGE",
        source_name="Boss",
        spell_name="Hit",
        school="physical",
        amount=amount,
        base_amount=base_amount if base_amount is not None else amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
    )


def _three_segment_fixture():
    """Boss / trash / boss run, offset from t=0 so rel_start_s math is
    exercised (not a no-op when run.start_time_s happens to be 0)."""
    run = ChallengeModeRun(
        map_id=1,
        map_name="Test Dungeon",
        key_level=10,
        affixes=[],
        start_time_s=1000.0,
        end_time_s=1180.0,
        success=True,
    )
    segments = [
        RunSegment(kind="boss", label="Boss A", start_time_s=1000.0, end_time_s=1060.0),
        RunSegment(kind="trash", label="Trash between", start_time_s=1060.0, end_time_s=1120.0),
        RunSegment(kind="boss", label="Boss B", start_time_s=1120.0, end_time_s=1180.0),
    ]
    events = [
        _evt(1030.0, 6000),  # Boss A: 6000 / 60s = 100 DTPS
        _evt(1090.0, 3000),  # Trash: 3000 / 60s = 50 DTPS
        _evt(1150.0, 12000),  # Boss B: 12000 / 60s = 200 DTPS
    ]
    deaths = [DeathRecord(time_s=1090.0, rel_time_s=90.0)]  # lands in the trash segment
    return run, events, deaths, segments


# ─── compute_segment_risk_bars ─────────────────────────────────────────────


def test_empty_segments_returns_empty():
    run = ChallengeModeRun(
        map_id=1, map_name="X", key_level=1, affixes=[], start_time_s=0.0, end_time_s=10.0
    )
    assert compute_segment_risk_bars(run, [], [], []) == []


def test_chronological_ordering_and_rel_start():
    run, events, deaths, segments = _three_segment_fixture()
    bars = compute_segment_risk_bars(run, events, deaths, segments)
    assert [b.label for b in bars] == ["Boss A", "Trash between", "Boss B"]
    # rel_start_s is relative to run.start_time_s (1000.0), not raw start_time_s.
    assert [b.rel_start_s for b in bars] == [0.0, 60.0, 120.0]
    assert [b.duration_s for b in bars] == [60.0, 60.0, 60.0]
    assert [b.kind for b in bars] == ["boss", "trash", "boss"]


def test_death_flagging_only_marks_the_containing_segment():
    run, events, deaths, segments = _three_segment_fixture()
    bars = compute_segment_risk_bars(run, events, deaths, segments)
    assert [b.n_deaths for b in bars] == [0, 1, 0]


def test_dtps_values():
    run, events, deaths, segments = _three_segment_fixture()
    bars = compute_segment_risk_bars(run, events, deaths, segments)
    assert [b.dtps for b in bars] == [100.0, 50.0, 200.0]


def test_bar_is_frozen_dataclass_instance():
    run, events, deaths, segments = _three_segment_fixture()
    bars = compute_segment_risk_bars(run, events, deaths, segments)
    assert all(isinstance(b, SegmentRiskBar) for b in bars)


# ─── Cross-check against render_per_segment_risk's own inline math ────────
#
# `test_per_pull_surfacing_render.py` establishes the `_StubStreamlit` +
# module-attribute-swap pattern for capturing `render_per_segment_risk`'s
# output without a real Streamlit script context. Reused here (not
# imported, to keep this file self-contained) to confirm the pure
# `compute_segment_risk_bars` DTPS numbers match what the existing
# per-segment expander cards actually render for the SAME fixture — not
# just this module's own re-implementation of the formula.


@dataclass
class _FakeExpander:
    title: str
    expanded: bool

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, str]] = []

    def expander(self, title, *, expanded=False):
        return _FakeExpander(title=title, expanded=expanded)

    def markdown(self, *args, **kwargs):
        pass

    def caption(self, *args, **kwargs):
        pass

    def success(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def columns(self, n):
        outer = self

        class _Col:
            def metric(self_inner, label, value, *args, **kwargs):
                outer.metrics.append((label, str(value)))

        return [_Col() for _ in range(n)]


def test_dtps_matches_render_per_segment_risk_metric():
    """Cross-check: the DTPS this module computes must equal the DTPS
    ``render_per_segment_risk`` itself renders in the segment's metric row,
    for the exact same fixture — not a re-derivation, a live comparison."""
    run, events, deaths, segments = _three_segment_fixture()
    bars = compute_segment_risk_bars(run, events, deaths, segments)

    stub = _StubStreamlit()
    real_st = log_segment_risk.st
    log_segment_risk.st = stub
    try:
        log_segment_risk.render_per_segment_risk(
            run=run,
            events=events,
            deaths=deaths,
            segments=segments,
            death_events=[],
        )
    finally:
        log_segment_risk.st = real_st

    rendered_dtps = [value for label, value in stub.metrics if label == "DTPS"]
    assert rendered_dtps == [f"{b.dtps:,.0f}" for b in bars]


def test_render_segment_risk_timeline_noop_on_empty():
    """No-op early return — must not raise or require a Streamlit context."""
    assert render_segment_risk_timeline([], key="empty") is None


# ─── WCAG 1.4.1 (Use of Color) — death segments need a non-color signal ────
#
# The bar color (steel/bronze) was the ONLY way to tell a death segment
# apart from a clean one; the death COUNT was hover-only text, unreachable
# without a pointer. A crosshatch `marker.pattern` on death bars gives a
# second, color-independent channel that doesn't require hovering.


def test_death_bars_get_a_pattern_fill_non_death_bars_dont(monkeypatch):
    import sys

    captured: dict = {}

    class _FakeSt:
        @staticmethod
        def plotly_chart(fig, **kw):
            captured["fig"] = fig

        @staticmethod
        def caption(*args, **kwargs):
            pass

    monkeypatch.setitem(sys.modules, "streamlit", _FakeSt)

    bars = [
        SegmentRiskBar(
            label="Clean", rel_start_s=0.0, duration_s=60.0, dtps=10.0, n_deaths=0, kind="boss"
        ),
        SegmentRiskBar(
            label="Died", rel_start_s=60.0, duration_s=60.0, dtps=20.0, n_deaths=1, kind="trash"
        ),
    ]
    render_segment_risk_timeline(bars, key="pattern_test")

    fig = captured.get("fig")
    assert fig is not None, "render_segment_risk_timeline did not call st.plotly_chart"
    shapes = fig.data[0].marker.pattern.shape
    assert shapes == ("", "x"), (
        "Death bars must carry a non-empty marker.pattern.shape so a death "
        "segment is distinguishable without perceiving bar color — a "
        "clean segment must stay unpatterned."
    )


def test_caption_names_the_pattern_not_just_the_color():
    """The caption is the one place the color/pattern meaning is spelled
    out as text — it must mention the pattern, not just the color, now
    that the pattern is the WCAG-required non-color signal."""
    import inspect

    from simf.ui.helpers import segment_risk_timeline as srt

    src = inspect.getsource(srt.render_segment_risk_timeline)
    assert "crosshatch" in src.lower()
