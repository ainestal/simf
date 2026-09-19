"""Tests for the segment-anchored CD-plan label rendering.

The CD-plan candidate table and press timeline used to render times as
absolute timestamps (``Shield Wall at 4:21``). A real player doesn't
watch the clock during a key, so the timestamp was useless as a
prescription. These tests pin the new framing — `early in Hadrox`,
`midway through the trash before Gemellus`, `late in Hadrox` — and the
fallback to `at M:SS` when segment context is missing (CLI path,
pre-pull padding, malformed log).
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.ui.log_view import (
    _fmt_anchor_when,
    _fmt_candidate_label,
    _segment_for_time,
    _segment_position_phrase,
)


@dataclass
class _Seg:
    """Minimal RunSegment stand-in for the helper tests — only the
    attributes the helpers actually read."""

    kind: str
    label: str
    start_time_s: float
    end_time_s: float

    def duration_s(self) -> float:
        return self.end_time_s - self.start_time_s


def _segments():
    """Boss → trash → boss sequence. Boundary at 240s exercises the
    half-open match (no double-counting)."""
    return [
        _Seg(kind="boss", label="Hadrox", start_time_s=0.0, end_time_s=120.0),
        _Seg(
            kind="trash",
            label="Trash before Gemellus",
            start_time_s=120.0,
            end_time_s=240.0,
        ),
        _Seg(kind="boss", label="Gemellus", start_time_s=240.0, end_time_s=360.0),
    ]


def test_segment_for_time_returns_containing_segment():
    segs = _segments()
    assert _segment_for_time(60.0, segs).label == "Hadrox"
    assert _segment_for_time(180.0, segs).label == "Trash before Gemellus"
    assert _segment_for_time(300.0, segs).label == "Gemellus"


def test_segment_for_time_uses_half_open_match():
    """At an exact boundary like t=120.0 the earlier segment's `end_time_s`
    equals the later one's `start_time_s`. Half-open `[start, end)` picks
    the LATER segment — t=120 belongs to the trash, not the boss that
    just ended."""
    segs = _segments()
    assert _segment_for_time(120.0, segs).label == "Trash before Gemellus"
    assert _segment_for_time(240.0, segs).label == "Gemellus"


def test_segment_for_time_returns_none_outside_segments():
    segs = _segments()
    assert _segment_for_time(-5.0, segs) is None
    assert _segment_for_time(500.0, segs) is None
    assert _segment_for_time(0.0, []) is None
    assert _segment_for_time(0.0, None) is None


def test_position_phrase_three_buckets_boundary_low():
    """Fractions on either side of 0.33 split early-in from midway-through."""
    segs = _segments()  # Hadrox is 0..120, duration 120
    # 32% in = early
    assert _segment_position_phrase(120.0 * 0.32, segs) == "early in Hadrox"
    # 34% in = midway
    assert _segment_position_phrase(120.0 * 0.34, segs) == "midway through Hadrox"


def test_position_phrase_three_buckets_boundary_high():
    """Fractions on either side of 0.66 split midway-through from late-in."""
    segs = _segments()
    assert _segment_position_phrase(120.0 * 0.65, segs) == "midway through Hadrox"
    assert _segment_position_phrase(120.0 * 0.67, segs) == "late in Hadrox"


def test_position_phrase_trash_label_is_articled_and_lowercased():
    """Trash labels lead with `Trash` capital — `the trash before Gemellus`
    composes with `early in / midway through / late in` cleanly."""
    segs = _segments()
    # t=140 → 20s into a 120s trash segment = 16.6% → early
    assert _segment_position_phrase(140.0, segs) == "early in the trash before Gemellus"
    # t=180 → halfway through the trash
    assert _segment_position_phrase(180.0, segs) == "midway through the trash before Gemellus"


def test_position_phrase_returns_none_outside_segments():
    segs = _segments()
    assert _segment_position_phrase(-1.0, segs) is None
    assert _segment_position_phrase(1000.0, segs) is None


def test_fmt_anchor_when_falls_back_to_mm_ss_without_segments():
    """CLI callers (no log segments) get the pre-existing `at M:SS`."""
    assert _fmt_anchor_when(261.0, None) == "at 4:21"
    assert _fmt_anchor_when(261.0, []) == "at 4:21"


def test_fmt_anchor_when_uses_positional_phrase_with_segments():
    segs = _segments()
    assert _fmt_anchor_when(60.0, segs) == "midway through Hadrox"


def test_fmt_candidate_label_composes_segment_phrase_per_anchor():
    """When two abilities share an anchor, the abilities collapse into
    one ` early in / midway through / late in <where>` clause."""

    @dataclass
    class _Candidate:
        label: str
        press_anchors: tuple

    # Both Shield Wall and Last Stand anchored at t=60 → midway in Hadrox
    c = _Candidate(
        label="Shield Wall + Last Stand at 1:00",
        press_anchors=((60.0, ("Shield Wall", "Last Stand")),),
    )
    out = _fmt_candidate_label(c, _segments())
    assert out == "Shield Wall + Last Stand midway through Hadrox"


def test_fmt_candidate_label_handles_anchors_in_different_segments():
    @dataclass
    class _Candidate:
        label: str
        press_anchors: tuple

    c = _Candidate(
        label="Shield Wall at 1:00 · Last Stand at 4:00",
        press_anchors=(
            (60.0, ("Shield Wall",)),
            (240.0, ("Last Stand",)),
        ),
    )
    out = _fmt_candidate_label(c, _segments())
    # t=240 lands on the segment boundary — half-open match picks Gemellus
    assert out == "Shield Wall midway through Hadrox · Last Stand early in Gemellus"


def test_fmt_candidate_label_falls_back_when_no_anchors():
    """Baselines (no_plan / naive) have empty press_anchors — the
    pre-built label string is returned untouched."""

    @dataclass
    class _Candidate:
        label: str
        press_anchors: tuple

    c = _Candidate(label="no_plan", press_anchors=())
    assert _fmt_candidate_label(c, _segments()) == "no_plan"

    c2 = _Candidate(label="naive (CD-on-CD from first spike)", press_anchors=())
    assert _fmt_candidate_label(c2, _segments()) == "naive (CD-on-CD from first spike)"


def test_fmt_candidate_label_falls_back_when_segments_missing():
    """CLI path: segments=None → the optimizer's `at M:SS` label is the
    final form. UI without segments behaves the same."""

    @dataclass
    class _Candidate:
        label: str
        press_anchors: tuple

    c = _Candidate(
        label="Shield Wall at 4:21",
        press_anchors=((261.0, ("Shield Wall",)),),
    )
    assert _fmt_candidate_label(c, None) == "Shield Wall at 4:21"
    assert _fmt_candidate_label(c, []) == "Shield Wall at 4:21"
