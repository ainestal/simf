"""Tests for the per-pull-first surfacing heuristic on the Why-died view.

Pins the narrow condition under which the trash-segment hierarchy flips —
per-pull cards become primary, aggregate-window stats become a footer.

User-feedback origin: Brutoh's WCL Windrunner Spire +19 (timed, no deaths)
rendered ONE giant "Trash · 30:11" segment with the actionable per-pull
breakdown buried below the aggregate.
"""

from __future__ import annotations

from simf.io.combat_log import RunSegment
from simf.ui.log_view import (
    _PER_PULL_RUN_FRACTION_THRESHOLD,
    _should_flip_to_per_pull_view,
)


def _trash(start: float, end: float, label: str = "Trash") -> RunSegment:
    return RunSegment(kind="trash", label=label, start_time_s=start, end_time_s=end)


def _boss(start: float, end: float, label: str = "Boss") -> RunSegment:
    return RunSegment(kind="boss", label=label, start_time_s=start, end_time_s=end)


def test_flip_fires_on_giant_trash_segment_with_multiple_pulls():
    """A trash segment covering ≥80% of the run with ≥2 pulls should flip."""
    # 30-minute run, one 28-minute trash segment, 5 pulls — Brutoh's case.
    run_total = 1800.0
    seg = _trash(0.0, 1700.0)  # 1700/1800 ≈ 94% of run
    assert _should_flip_to_per_pull_view(seg, run_total, n_pulls=5) is True


def test_flip_skipped_when_below_pull_threshold():
    """Same giant trash window but only 1 pull → no flip."""
    run_total = 1800.0
    seg = _trash(0.0, 1700.0)
    assert _should_flip_to_per_pull_view(seg, run_total, n_pulls=1) is False
    assert _should_flip_to_per_pull_view(seg, run_total, n_pulls=0) is False


def test_flip_skipped_on_boss_segment():
    """Boss segments are never flipped — death-anchored hierarchy is correct
    for fights."""
    run_total = 1800.0
    seg = _boss(0.0, 1700.0)
    assert _should_flip_to_per_pull_view(seg, run_total, n_pulls=5) is False


def test_flip_skipped_when_trash_is_short_fraction_of_run():
    """Death-anchored runs typically have trash gaps that are 5–20% of the
    total run. These keep the current aggregate-first hierarchy."""
    run_total = 1800.0
    # 30% — well under 80% threshold
    seg = _trash(0.0, 540.0)
    assert _should_flip_to_per_pull_view(seg, run_total, n_pulls=5) is False


def test_flip_threshold_boundary_strict_inequality():
    """Threshold is strict-greater-than: exactly 80% does NOT flip."""
    run_total = 1000.0
    # duration == threshold * run_total → not strictly greater
    seg = _trash(0.0, _PER_PULL_RUN_FRACTION_THRESHOLD * run_total)
    assert _should_flip_to_per_pull_view(seg, run_total, n_pulls=5) is False
    # Just above threshold should flip
    seg_above = _trash(0.0, _PER_PULL_RUN_FRACTION_THRESHOLD * run_total + 1.0)
    assert _should_flip_to_per_pull_view(seg_above, run_total, n_pulls=5) is True


def test_flip_skipped_on_zero_run_duration():
    """Defensive: zero / negative run duration should never flip."""
    seg = _trash(0.0, 100.0)
    assert _should_flip_to_per_pull_view(seg, 0.0, n_pulls=5) is False
    assert _should_flip_to_per_pull_view(seg, -1.0, n_pulls=5) is False
