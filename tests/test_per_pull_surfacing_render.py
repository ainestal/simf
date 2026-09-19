"""Smoke-test the per-pull-first hierarchy renderer.

Stubs `st.*` primitives, invokes `render_per_segment_risk` against a
Brutoh-shaped clean run (no deaths, one giant trash segment with 3 pulls),
and asserts the emitted markdown shows per-pull cards as primary and the
aggregate footer.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.io.combat_log import ChallengeModeRun, DamageTakenEvent, RunSegment
from simf.ui import log_segment_risk


@dataclass
class _FakeExpander:
    """Stand-in for `st.expander` — captures title + expanded flag, behaves
    as a context manager so the renderer's `with st.expander(...)` works."""

    title: str
    expanded: bool

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubStreamlit:
    """Captures the renderer's Streamlit output."""

    def __init__(self) -> None:
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.successes: list[str] = []
        self.warnings: list[str] = []
        self.expanders: list[_FakeExpander] = []
        self.metrics: list[tuple[str, str]] = []

    def expander(self, title, *, expanded=False):
        exp = _FakeExpander(title=title, expanded=expanded)
        self.expanders.append(exp)
        return exp

    def markdown(self, content, *args, **kwargs):
        self.markdowns.append(content)

    def caption(self, content, *args, **kwargs):
        self.captions.append(content)

    def success(self, content, *args, **kwargs):
        self.successes.append(content)

    def warning(self, content, *args, **kwargs):
        self.warnings.append(content)

    def columns(self, n):
        cols = []
        outer = self

        class _Col:
            def metric(self_inner, label, value, *args, **kwargs):
                outer.metrics.append((label, str(value)))

        for _ in range(n):
            cols.append(_Col())
        return cols


def _evt(t: float, source: str, amount: int = 5000, guid_seq: int = 0) -> DamageTakenEvent:
    """Damage event with distinct creature GUIDs so pulls see real mob counts."""
    return DamageTakenEvent(
        time_s=t,
        event_type="SPELL_DAMAGE",
        source_name=source,
        spell_name="thwack",
        school="physical",
        amount=amount,
        base_amount=amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
        source_guid=f"Creature-0-0-0-0-0-{guid_seq}",
    )


def _clean_run_fixture():
    """Build a Brutoh-shaped clean run: one trash segment spanning the full
    log, three distinct pulls, no deaths.
    """
    run = ChallengeModeRun(
        map_id=0,
        map_name="Windrunner Spire",
        key_level=19,
        affixes=[],
        start_time_s=0.0,
        end_time_s=1800.0,
        success=True,
    )
    # 30-minute trash segment covering ≥ 80% of the run.
    trash = RunSegment(kind="trash", label="Trash", start_time_s=10.0, end_time_s=1790.0)
    events: list[DamageTakenEvent] = []
    # Pull 1: t=100, three mob types
    for i, name in enumerate(("Voidling", "Voidcaller", "Voidweaver")):
        events.append(_evt(100.0 + i * 0.5, name, guid_seq=i))
    # Pull 2: t=400 (well past the 8s gap), three mob types
    for i, name in enumerate(("Hexer", "Brute", "Whisperer")):
        events.append(_evt(400.0 + i * 0.5, name, guid_seq=10 + i))
    # Pull 3: t=900
    for i, name in enumerate(("Reaver", "Shrieker", "Crawler")):
        events.append(_evt(900.0 + i * 0.5, name, guid_seq=20 + i))
    return run, [trash], events


def _render_with_stub():
    run, segments, events = _clean_run_fixture()
    stub = _StubStreamlit()
    # render_per_segment_risk now lives in log_segment_risk and reads `st` from
    # that module's globals, so the stub must be swapped there (not on the
    # log_view facade that merely re-exports the function).
    real_st = log_segment_risk.st
    log_segment_risk.st = stub
    try:
        log_segment_risk.render_per_segment_risk(
            run=run,
            events=events,
            deaths=[],
            segments=segments,
            death_events=[],
        )
    finally:
        log_segment_risk.st = real_st
    return stub


def test_clean_run_flips_to_per_pull_view():
    """Whole-run trash + multiple pulls → per-pull-first hierarchy fires."""
    stub = _render_with_stub()

    # The trash expander must be force-opened, not collapsed.
    assert stub.expanders, "no expander rendered"
    trash_exp = stub.expanders[-1]
    assert trash_exp.expanded is True, (
        f"Trash expander should be force-expanded when the per-pull flip "
        f"fires; got expanded={trash_exp.expanded}"
    )

    # The flip caption explains the switch.
    flip_caption = next(
        (c for c in stub.captions if "one continuous trash window" in c),
        None,
    )
    assert flip_caption is not None, f"Per-pull flip caption missing. Captions: {stub.captions}"

    # Per-pull cards rendered as primary view (markdown headlined with "Pull N").
    pull_headlines = [m for m in stub.markdowns if m.lstrip().startswith("#### Pull ")]
    assert len(pull_headlines) == 3, (
        f"Expected 3 per-pull card headlines, got {len(pull_headlines)}: {pull_headlines}"
    )

    # Aggregate appears as a footer summary (single markdown line), not as
    # 4 columned metrics. The 4-metric branch is the non-flipped path.
    # So `stub.metrics` should NOT contain the segment-level "DTPS / Mit /
    # Hits / Deaths" 4-column row.
    metric_labels = [label for label, _ in stub.metrics]
    assert metric_labels.count("Deaths") == 0, (
        "Per-pull-first view should not emit the 4-column segment metrics; "
        f"found Deaths metric — full metric labels: {metric_labels}"
    )

    # Footer aggregate line is present.
    footer = next(
        (m for m in stub.markdowns if m.startswith("**Whole-window totals**")),
        None,
    )
    assert footer is not None, (
        f"Whole-window aggregate footer missing. Markdowns starts: "
        f"{[m[:50] for m in stub.markdowns]}"
    )


def test_clean_run_titlebar_advertises_pull_count():
    """When the flip fires, the expander title carries the pull count so a
    collapsed-by-history user still sees the promise of per-pull detail."""
    stub = _render_with_stub()
    trash_exp = stub.expanders[-1]
    assert "3 pulls" in trash_exp.title, (
        f"Pull count missing from trash expander title: {trash_exp.title!r}"
    )
