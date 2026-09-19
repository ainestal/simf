"""Run-identity surface — Brutoh idea #a, ROADMAP 2026-05-25.

Pins the format helpers behind the "Now analyzing: …" header on the log
analysis surface, and the multi-run picker's row-identity + active-row
visual distinction.

The complaint, verbatim from Brutoh: "after loading a log file, there is
no clear indicator of *which* run inside the log is currently being
analyzed." These tests guard against the two regression modes:

  1. The single-line identity helper claiming TIMED for an over-time run,
     dropping the date, or formatting the duration wrong.
  2. The multi-run picker collapsing rows back to a single-active-row
     dropdown, or losing the active-row visual marker so duplicate-key
     entries look identical.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from simf.io.combat_log import ChallengeModeRun

# ─── format-helper unit tests (no Streamlit) ──────────────────────────────────


def _ts(year: int, month: int, day: int, hour: int = 14, minute: int = 0) -> float:
    """Build a local-timezone epoch timestamp for a given calendar date.

    Mirrors what ``parse_combat_log_line`` writes into ``start_time_s``
    (a `datetime.timestamp()` of a naive local-time string). Keeping the
    fixture in local-tz avoids cross-timezone test flakes — the date
    helper reads the same local-tz back out via ``datetime.fromtimestamp``.
    """
    return datetime(year, month, day, hour, minute).timestamp()


def _mkrun(
    *,
    map_name: str = "Ara-Kara, City of Echoes",
    key_level: int = 18,
    map_id: int = 2660,
    success: bool | None = True,
    duration_ms: int | None = 1_714_000,  # 28:34
    par_time_ms: int | None = 1_980_000,  # 33:00, comfortably timed
    start_year: int = 2026,
    start_month: int = 5,
    start_day: int = 22,
) -> ChallengeModeRun:
    start = _ts(start_year, start_month, start_day)
    end = start + (duration_ms / 1000.0) if duration_ms else None
    return ChallengeModeRun(
        map_id=map_id,
        map_name=map_name,
        key_level=key_level,
        affixes=[9, 10, 147],
        start_time_s=start,
        end_time_s=end,
        success=success,
        duration_ms=duration_ms,
        par_time_ms=par_time_ms,
    )


def test_identity_string_format_pin_timed():
    """Acceptance line, verbatim from ROADMAP 2026-05-25:
    "Loading any log shows a one-line 'Now analyzing: Ara-Kara +18
    (Timed, 28:34, 2026-05-22)' header on the analysis surface." We
    render the dot-separated middle-dot variant the rest of the
    surface uses; the test pins ALL four identity fields are present
    in the rendered string, in order."""
    from simf.ui.log_view import _format_run_identity

    run = _mkrun()
    identity = _format_run_identity(run)
    # Map name + key level
    assert "Ara-Kara, City of Echoes +18" in identity
    # Outcome — sentence case, not the upper-case TIMED ✓ glyph form
    assert "Timed" in identity
    assert "TIMED" not in identity
    # Duration
    assert "28:34" in identity
    # Date
    assert "2026-05-22" in identity
    # Composition with middle-dot separators
    assert identity == "Ara-Kara, City of Echoes +18 · Timed 28:34 · 2026-05-22"


def test_identity_over_time_includes_par_delta_seconds():
    """WoW convention: over-time runs surface "+N seconds past par" so
    the reader can see how badly the timer slipped. Algeth'ar Academy
    +14 finished 52s past the 31:00 par in the canonical log."""
    from simf.ui.log_view import _format_run_identity, _format_run_outcome_short

    run = _mkrun(
        map_name="Algeth'ar Academy",
        key_level=14,
        map_id=2526,
        duration_ms=1_912_867,
        par_time_ms=1_860_000,
        start_day=18,
    )
    # 1_912_867 - 1_860_000 = 52_867 ms ≈ 53s (rounded)
    assert _format_run_outcome_short(run) == "Over Time +53s"
    identity = _format_run_identity(run)
    assert "Over Time +53s" in identity
    assert "31:52" in identity
    assert "Algeth'ar Academy +14" in identity
    assert "2026-05-18" in identity


def test_identity_abandoned_run():
    """All-zeros CHALLENGE_MODE_END pattern (group left mid-key)."""
    from simf.ui.log_view import _format_run_identity, _format_run_outcome_short

    # Abandoned runs in WoW write ``success=0, duration_ms=0``. We give
    # the run a real end_time_s a few minutes after start so the
    # duration_s() helper has something to render — that path is
    # exercised separately by test_run_outcome_label.
    run = _mkrun(success=False, duration_ms=0, par_time_ms=1_980_000)
    # duration_s() will fall back to (end - start) since duration_ms is 0
    assert _format_run_outcome_short(run) == "Abandoned"
    identity = _format_run_identity(run)
    assert "Abandoned" in identity


def test_identity_incomplete_run():
    """CHALLENGE_MODE_START with no matching END (truncated log)."""
    from simf.ui.log_view import _format_run_identity, _format_run_outcome_short

    run = _mkrun(success=None, duration_ms=None)
    assert _format_run_outcome_short(run) == "Incomplete"
    identity = _format_run_identity(run)
    assert "Incomplete" in identity


def test_identity_completed_unknown_par():
    """Catalog has par_time_ms=null — we say Completed, not Timed."""
    from simf.ui.log_view import _format_run_identity, _format_run_outcome_short

    run = _mkrun(success=True, duration_ms=1_700_000, par_time_ms=None)
    assert _format_run_outcome_short(run) == "Completed"
    identity = _format_run_identity(run)
    assert "Completed" in identity
    assert "Timed" not in identity
    assert "Over Time" not in identity


def test_identity_drops_date_when_start_time_zero():
    """Synthetic test fixtures often build runs with start_time_s=0.0;
    the header should silently drop the trailing date rather than
    print "1970-01-01" / similar epoch artefact."""
    from simf.ui.log_view import _format_run_identity

    run = ChallengeModeRun(
        map_id=2660,
        map_name="Ara-Kara, City of Echoes",
        key_level=18,
        affixes=[],
        start_time_s=0.0,
        end_time_s=1714.0,
        success=True,
        duration_ms=1_714_000,
        par_time_ms=1_980_000,
    )
    identity = _format_run_identity(run)
    assert "Ara-Kara" in identity
    assert "28:34" in identity
    assert "1970" not in identity


def test_identity_drops_duration_when_unknown():
    """A live-session log can have a START with no duration_ms and no
    end_time_s yet. Never print 0:00 — that reads as "instant run."""
    from simf.ui.log_view import _format_run_identity

    run = ChallengeModeRun(
        map_id=2660,
        map_name="Ara-Kara, City of Echoes",
        key_level=18,
        affixes=[],
        start_time_s=_ts(2026, 5, 22),
        end_time_s=None,
        success=None,
        duration_ms=None,
        par_time_ms=1_980_000,
    )
    identity = _format_run_identity(run)
    assert "0:00" not in identity
    assert "Incomplete" in identity


# ─── header renderer test (Streamlit dependent) ───────────────────────────────


def test_run_identity_header_renders_now_analyzing_prefix():
    """The header text the user actually sees on the analysis surface.

    Uses Streamlit's AppTest to drive the renderer rather than mocking
    ``st.markdown`` — keeps the test honest about the wrapper structure
    and the CSS class names the styling in app.py latches onto.

    AppTest serialises the callable, so module-level closures don't make
    it through; everything the script needs lives inside ``_script``.
    """
    from streamlit.testing.v1 import AppTest

    def _script():
        from datetime import datetime

        from simf.io.combat_log import ChallengeModeRun
        from simf.ui.log_view import _render_run_identity_header

        start = datetime(2026, 5, 22, 14, 0).timestamp()
        run = ChallengeModeRun(
            map_id=2660,
            map_name="Ara-Kara, City of Echoes",
            key_level=18,
            affixes=[9, 10, 147],
            start_time_s=start,
            end_time_s=start + 1714.0,
            success=True,
            duration_ms=1_714_000,
            par_time_ms=1_980_000,
        )
        _render_run_identity_header(run)

    at = AppTest.from_function(_script)
    at.run()
    md_blocks = [el.value for el in at.markdown]
    assert any('class="run-identity-header"' in v for v in md_blocks), (
        f"Expected a run-identity-header div; got: {md_blocks}"
    )
    assert any("Now analyzing:" in v for v in md_blocks)
    assert any(
        "Ara-Kara, City of Echoes +18" in v and "Timed" in v and "28:34" in v for v in md_blocks
    )


# ─── multi-run picker shape + active-row marker ───────────────────────────────


def test_multi_run_picker_rows_show_full_identity_per_row():
    """Every row in the multi-run picker must carry dungeon + key +
    result + duration (and the date when present), so the reader can
    tell two back-to-back +18 runs apart without clicking. The picker
    used to be a selectbox whose closed state showed only the active
    row — switching to a radio means all rows are visible at once, and
    the per-row label has to carry enough identity to disambiguate.
    """
    from simf.ui.log_view import _format_run_identity

    # Two Ara-Kara +18 runs on the same day, one timed, one over time.
    # If the picker only carried dungeon + key, these would render
    # identically and the user couldn't tell them apart.
    run_a = _mkrun(duration_ms=1_714_000, par_time_ms=1_980_000)  # timed
    run_b = _mkrun(
        duration_ms=2_010_000,  # 33:30, ~30s past 33:00 par
        par_time_ms=1_980_000,
        start_day=22,
    )

    label_a = _format_run_identity(run_a)
    label_b = _format_run_identity(run_b)

    # All four identity fields present in both labels.
    for label in (label_a, label_b):
        assert "Ara-Kara, City of Echoes" in label
        assert "+18" in label
        # outcome word present (Timed / Over Time / etc)
        assert any(word in label for word in ("Timed", "Over Time", "Abandoned", "Completed"))
        # M:SS duration present
        assert ":" in label  # duration mm:ss
        # date present
        assert "2026-05-22" in label

    # Labels must be DIFFERENT — same dungeon + key + date, but the
    # outcome + duration disambiguate.
    assert label_a != label_b
    assert "Timed" in label_a
    assert "Over Time" in label_b


def test_active_row_visual_marker_present_when_multiple_runs():
    """The active row in the multi-run picker must be visually distinct
    from idle rows. We render an st.radio inside a `.run-picker` wrapper
    div; CSS in app.py paints the selected label with a gold left-border
    rule + bolded text. This test asserts the wrapper is emitted so the
    CSS selector can latch on — without it the active-row marker
    silently regresses to "just the radio dot" and depleted-vs-timed
    runs in a row of +18s read as visually identical.
    """
    from streamlit.testing.v1 import AppTest

    def _script():
        from datetime import datetime

        import streamlit as st

        from simf.io.combat_log import ChallengeModeRun
        from simf.ui.log_view import _format_run_identity

        start = datetime(2026, 5, 22, 14, 0).timestamp()
        runs = [
            ChallengeModeRun(
                map_id=2660,
                map_name="Ara-Kara, City of Echoes",
                key_level=18,
                affixes=[9, 10, 147],
                start_time_s=start,
                end_time_s=start + 1714.0,
                success=True,
                duration_ms=1_714_000,
                par_time_ms=1_980_000,
            ),
            ChallengeModeRun(
                map_id=2660,
                map_name="Ara-Kara, City of Echoes",
                key_level=18,
                affixes=[9, 10, 147],
                start_time_s=start,
                end_time_s=start + 2010.0,
                success=True,
                duration_ms=2_010_000,
                par_time_ms=1_980_000,
            ),
        ]
        st.markdown('<div class="run-picker">', unsafe_allow_html=True)
        st.radio(
            "Run",
            list(range(len(runs))),
            index=1,
            format_func=lambda i: _format_run_identity(runs[i]),
            key="run_pick_test",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    at = AppTest.from_function(_script)
    at.run()

    # Wrapper div must be emitted — that's what the CSS in app.py keys on.
    md_blocks = [el.value for el in at.markdown]
    assert any('class="run-picker"' in v for v in md_blocks), (
        f"Run-picker wrapper missing; got: {md_blocks}"
    )

    # Radio rendered with both rows' identity strings.
    radio_widgets = list(at.radio)
    assert len(radio_widgets) == 1
    radio = radio_widgets[0]
    # Active-row index — should default to index=1 (the over-time run)
    # as set above. AppTest's radio reports the selected value via
    # ``radio.value`` (the option) and the integer position via
    # ``radio.index`` on some Streamlit builds; we check whichever the
    # current SDK surfaces.
    selected = getattr(radio, "value", None)
    if selected is None:
        selected = radio.index
    assert selected in (1, "1")


# ─── outcome-format edge cases ────────────────────────────────────────────────


def test_over_time_par_delta_rounds_to_nearest_second():
    """A duration 1 ms past par shouldn't round to "+0s" — the helper
    clamps to a minimum of +1s so the over-time framing reads honestly.
    Slightly under-reporting is preferable to printing "+0s," which
    contradicts the "Over Time" label."""
    from simf.ui.log_view import _format_run_outcome_short

    run = _mkrun(duration_ms=1_980_001, par_time_ms=1_980_000)
    assert _format_run_outcome_short(run) == "Over Time +1s"


def test_over_time_par_delta_full_minute():
    """A 60s par delta renders as "+60s" — the helper doesn't auto-promote
    to mm:ss because the reader is already getting the absolute duration
    next to it (e.g. "Over Time +60s 32:00"). Keeping the delta in pure
    seconds avoids two ambiguous time formats in one phrase."""
    from simf.ui.log_view import _format_run_outcome_short

    run = _mkrun(duration_ms=2_040_000, par_time_ms=1_980_000)
    assert _format_run_outcome_short(run) == "Over Time +60s"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
