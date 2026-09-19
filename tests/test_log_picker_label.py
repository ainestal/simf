"""Regression tests for the log-picker dropdown label.

`_log_picker_label(log_name)` enriches the picker rows with the first
run's dungeon + key + outcome glyph so the user doesn't have to remember
which `WoWCombatLog-*.txt` corresponds to which key. Two-line UX win.

The cap (`_LOG_PICKER_LABEL_CAP`) is the safety net against the perf
foot-gun documented in `list_logs()`: an earlier version that
filter-parsed every file cost 30s+ on Pi. `_cached_runs` is cheaper but
not free cold; capping enrichment to the top-10 most-recent logs keeps
the picker render bounded.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from simf.ui import log_data, log_surface, log_view


@dataclass
class _StubRun:
    """Minimal stand-in for `ChallengeModeRun` — only the fields the
    label helper reads. Real `ChallengeModeRun` carries timing fields
    we don't need here."""

    map_name: str
    key_level: int
    success: bool | None
    duration_ms: int | None = None
    par_time_ms: int | None = None


@pytest.fixture
def fake_runs(monkeypatch):
    """Patch `list_logs`, `_cached_runs`, and `_cached_run_death_count`
    to return scripted data so the test exercises the label helper
    without touching disk."""
    state: dict = {"logs": [], "runs_by_log": {}, "deaths_by_log": {}}

    def _fake_list_logs():
        return list(state["logs"])

    def _fake_cached_runs(log_name: str):
        return list(state["runs_by_log"].get(log_name, []))

    def _fake_cached_run_death_count(log_name: str, run_index: int) -> int:
        # Default to 0 (clean) when a test doesn't set deaths_by_log.
        return state["deaths_by_log"].get(log_name, 0)

    # `_log_picker_label` now lives in `log_data` and resolves these three
    # helpers via that module's globals (bare-name late binding), so the
    # patch target is `log_data`, not the `log_view` facade that re-exports
    # them. Patching the facade would be inert — the function reads log_data.
    monkeypatch.setattr(log_data, "list_logs", _fake_list_logs)
    monkeypatch.setattr(log_data, "_cached_runs", _fake_cached_runs)
    monkeypatch.setattr(log_data, "_cached_run_death_count", _fake_cached_run_death_count)
    # `_is_bundled_example_log` checks the REAL filesystem (UPLOADS_DIR).
    # These fixture filenames are pure fakes that exist nowhere on disk, so
    # unmocked they'd all register as "bundled example" and pick up the new
    # " (sample)" tag, breaking every untagged assertion below. Default to
    # "not a sample" (a real upload) so the existing label-formatting
    # assertions stay about formatting, not filesystem state; the dedicated
    # sample-tag tests below override this explicitly.
    monkeypatch.setattr(log_data, "_is_bundled_example_log", lambda _name: False)
    return state


def test_label_no_runs_returns_plain_filename(fake_runs):
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {name: []}

    assert log_view._log_picker_label(name) == name


def test_label_single_timed_run_appends_check_glyph_and_clean_badge(fake_runs):
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {
        name: [_StubRun(map_name="Ara-Kara", key_level=18, success=True)],
    }
    # 0 deaths → "clean" badge after the glyph.

    assert log_view._log_picker_label(name) == f"{name} — Ara-Kara +18 ✓ · clean"


def test_label_single_run_with_one_death_uses_singular_badge(fake_runs):
    """One-death runs read "1 death" (singular), not "1 deaths"."""
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {
        name: [_StubRun(map_name="Ara-Kara", key_level=18, success=True)],
    }
    fake_runs["deaths_by_log"] = {name: 1}

    assert log_view._log_picker_label(name) == f"{name} — Ara-Kara +18 ✓ · 1 death"


def test_label_single_run_with_multiple_deaths_uses_plural_badge(fake_runs):
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {
        name: [_StubRun(map_name="Ara-Kara", key_level=18, success=True)],
    }
    fake_runs["deaths_by_log"] = {name: 3}

    assert log_view._log_picker_label(name) == f"{name} — Ara-Kara +18 ✓ · 3 deaths"


def test_label_single_failed_run_uses_cross_glyph(fake_runs):
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    # An over-time completion still has `success=True` in WoW's log
    # schema — but an abandoned / incomplete run carries `success=False`
    # or `success=None`. The picker label collapses both failure shapes
    # to ✗ since the dropdown doesn't have room for the nuance.
    fake_runs["runs_by_log"] = {
        name: [_StubRun(map_name="Halls of Atonement", key_level=16, success=False)],
    }
    fake_runs["deaths_by_log"] = {name: 2}

    assert log_view._log_picker_label(name) == f"{name} — Halls of Atonement +16 ✗ · 2 deaths"


def test_label_multi_run_summarises_first_plus_n_more(fake_runs):
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {
        name: [
            _StubRun(map_name="Ara-Kara", key_level=18, success=True),
            _StubRun(map_name="The MOTHERLODE!!", key_level=17, success=True),
            _StubRun(map_name="Operation: Floodgate", key_level=16, success=False),
        ],
    }
    # Death-count badge reflects the *first* run only — the badge stays
    # consistent with the displayed run identity ("Ara-Kara +18"). The
    # "+N more" suffix tags the siblings without re-tallying their deaths.
    fake_runs["deaths_by_log"] = {name: 0}

    assert log_view._log_picker_label(name) == f"{name} — Ara-Kara +18 ✓ · clean +2 more"


def test_label_cap_falls_back_to_plain_filename(fake_runs):
    """Logs past `_LOG_PICKER_LABEL_CAP` in `list_logs()` order get no
    enrichment — even when `_cached_runs` would otherwise yield data.
    Pins the perf safety net: the dropdown must never trigger an
    unbounded number of CHALLENGE_MODE scans on render."""
    cap = log_view._LOG_PICKER_LABEL_CAP
    # `list_logs()` returns most-recent first. Build a list where the
    # target log sits past the cap.
    enriched = [f"WoWCombatLog-recent-{i:02d}.txt" for i in range(cap)]
    stale_name = "WoWCombatLog-ancient.txt"
    fake_runs["logs"] = [*enriched, stale_name]
    # If the cap is honoured the runs map is never consulted; pre-seed
    # it with data the test would otherwise expect to surface, so a
    # missing cap would fail loud.
    fake_runs["runs_by_log"] = {
        stale_name: [_StubRun(map_name="Ara-Kara", key_level=18, success=True)],
    }

    assert log_view._log_picker_label(stale_name) == stale_name


def test_label_unknown_log_falls_back_to_plain_filename(fake_runs):
    """A log name not present in `list_logs()` (e.g. a stale session
    pick after a directory wipe) gets no enrichment. Belt-and-braces:
    the helper must not raise when the rank lookup misses."""
    fake_runs["logs"] = ["WoWCombatLog-other.txt"]
    fake_runs["runs_by_log"] = {}

    assert log_view._log_picker_label("WoWCombatLog-missing.txt") == ("WoWCombatLog-missing.txt")


def test_label_tags_bundled_example_log_as_sample(fake_runs, monkeypatch):
    """A cold visitor with no logs of their own lands on this picker
    defaulted to the first bundled `examples/` log — with nothing marking
    it as sample data, that read as a bug or a privacy leak ("whose AnonTank1
    is this?", round-1 novice-tank review, 2026-07-05)."""
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {
        name: [_StubRun(map_name="Ara-Kara", key_level=18, success=True)],
    }
    monkeypatch.setattr(log_data, "_is_bundled_example_log", lambda _name: True)

    assert log_view._log_picker_label(name) == f"{name} (sample) — Ara-Kara +18 ✓ · clean"


def test_label_omits_sample_tag_for_a_real_upload(fake_runs):
    """A real uploaded log (the common case once a user has their own
    data) gets no tag — the fixture's default (see `fake_runs`)."""
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {
        name: [_StubRun(map_name="Ara-Kara", key_level=18, success=True)],
    }

    assert log_view._log_picker_label(name) == f"{name} — Ara-Kara +18 ✓ · clean"


def test_label_no_runs_still_tags_sample(fake_runs, monkeypatch):
    """The no-runs early return must not lose the sample tag — a cold
    visitor's default log commonly has no parseable runs before they pick
    a real one."""
    name = "WoWCombatLog-052525_174633.txt"
    fake_runs["logs"] = [name]
    fake_runs["runs_by_log"] = {name: []}
    monkeypatch.setattr(log_data, "_is_bundled_example_log", lambda _name: True)

    assert log_view._log_picker_label(name) == f"{name} (sample)"


def test_is_bundled_example_log_mirrors_resolve_precedence(tmp_path, monkeypatch):
    """Must agree with `_resolve_log_path`'s own upload-wins precedence —
    a file present in UPLOADS_DIR is never tagged as a sample, regardless
    of whether the same basename also exists in EXAMPLES_DIR."""
    monkeypatch.setattr(log_data, "UPLOADS_DIR", tmp_path)
    name = "WoWCombatLog-real-upload.txt"
    (tmp_path / name).write_text("fake log contents")

    assert log_data._is_bundled_example_log(name) is False
    assert log_data._is_bundled_example_log("WoWCombatLog-never-uploaded.txt") is True


def test_format_death_badge_zero_renders_clean():
    assert log_view._format_death_badge(0) == "clean"


def test_format_death_badge_negative_renders_clean():
    """Defensive: a negative count shouldn't crash the picker render
    even if a future caller mis-routes a sentinel value into the badge.
    "clean" is the safe fallback."""
    assert log_view._format_death_badge(-1) == "clean"


def test_format_death_badge_singular_pluralization():
    """1 death uses the singular noun — pluralization at this surface
    matters because the badge is right next to the dungeon name and
    misreading the row identity at a glance is exactly the friction
    the badge is trying to remove."""
    assert log_view._format_death_badge(1) == "1 death"


def test_format_death_badge_plural():
    assert log_view._format_death_badge(2) == "2 deaths"
    assert log_view._format_death_badge(11) == "11 deaths"


def test_selectbox_wires_label_helper_into_log_picker():
    """The picker must wire `_log_picker_label` through `format_func`.
    The call site uses a lambda that only enriches the currently-selected
    row (siblings render as bare filenames) — see the comment in
    `_render_local_log_picker` for the perf rationale. Mutation-verified:
    drop the `_log_picker_label(` reference at the call site and this
    fails.
    """
    import inspect

    # `_log_picker_label`'s *definition* lives in `log_data`; the picker *call
    # site* lives in `_render_local_log_flow`, which moved to `log_surface` in
    # the log_view split. So any `_log_picker_label(` occurrence in log_surface
    # source IS the call site.
    src = inspect.getsource(log_surface)
    assert "_log_picker_label(" in src, (
        "Log picker lost its `_log_picker_label` wiring at the selectbox "
        "call site — the dropdown will fall back to raw filenames."
    )
