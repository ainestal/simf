"""Tests for the danger-pull cheat sheet (Batch G / Season 2 Readiness
bucket A) — `core.constants.load_danger_pulls` + `ui.helpers.
danger_pull_cheatsheet.render_danger_pull_cheatsheet`.

The shell is real and wired end-to-end today; there is no real pull-by-pull
content yet (data/danger_pulls.yaml seeds every Season 2 dungeon with an
empty list) — these tests cover the wiring (loader, honest-placeholder
rendering, real-content rendering) rather than any specific dungeon's
content, which doesn't exist yet.

`AppTest.from_function` runs each `_render_*` helper's SOURCE CODE in an
isolated script context — module-level imports in THIS file are not carried
over, so every helper below imports what it needs inside its own body
(matching the pattern `tests/test_ehp_gloss.py::_render_script` already
uses).
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from simf.core.constants import load_danger_pulls

# Mirrors tests/test_dungeons_yaml_invariants.py's _SEASON_2_IDS — kept as an
# independent literal (not an import) so a change to either file's id set
# surfaces as a real test failure in BOTH places, not a silently-shared
# fixture that could drift alongside a single edit.
_SEASON_2_IDS = {
    "altar_of_fangs",
    "murder_row",
    "den_of_nalorakk",
    "the_blinding_vale",
    "voidscar_arena",
    "ruby_life_pools",
    "temple_of_sethraliss",
    "kings_rest",
}


def test_load_danger_pulls_has_every_season_2_dungeon():
    data = load_danger_pulls()
    assert set(data) == _SEASON_2_IDS


def test_load_danger_pulls_season_2_entries_are_empty_today():
    """Every Season 2 entry is an empty list right now — there is no real
    content yet, and this test is the tripwire: once someone actually adds
    real pull data for a dungeon, THIS assertion should start failing,
    which is the signal to update it (and celebrate that content shipped),
    not a bug to silence."""
    data = load_danger_pulls()
    for dungeon_id, pulls in data.items():
        assert pulls == [], f"{dungeon_id} unexpectedly has content: {pulls}"


def test_load_danger_pulls_missing_id_returns_none_not_keyerror():
    data = load_danger_pulls()
    assert data.get("some_dungeon_not_in_the_file") is None
    # The real caller-facing contract is via .get(...) or [] fallback, not a
    # bare [] on a missing key from load_danger_pulls() itself — confirmed
    # by render_danger_pull_cheatsheet's own `or []` below.


def _render_no_selection() -> None:
    from simf.ui.helpers.danger_pull_cheatsheet import render_danger_pull_cheatsheet

    render_danger_pull_cheatsheet([])


def test_render_nothing_when_no_dungeons_selected():
    at = AppTest.from_function(_render_no_selection)
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    assert len(at.expander) == 0


def _render_known_empty_dungeon() -> None:
    from simf.ui.helpers.danger_pull_cheatsheet import render_danger_pull_cheatsheet

    render_danger_pull_cheatsheet([{"id": "altar_of_fangs", "name": "Altar of Fangs"}])


def test_render_shows_honest_placeholder_for_a_dungeon_with_no_data():
    at = AppTest.from_function(_render_known_empty_dungeon)
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    assert len(at.expander) == 1
    body = "\n".join(str(c.value) for c in at.expander[0].caption)
    assert "No danger-pull data yet for Altar of Fangs" in body


def _render_unknown_dungeon_id() -> None:
    """A dungeon id not present in danger_pulls.yaml at all (e.g. a Season 1
    dungeon, or a future id the data file hasn't been updated for yet) must
    degrade to the same honest placeholder, not crash."""
    from simf.ui.helpers.danger_pull_cheatsheet import render_danger_pull_cheatsheet

    render_danger_pull_cheatsheet([{"id": "windrunner_spire", "name": "Windrunner Spire"}])


def test_render_handles_a_dungeon_id_entirely_absent_from_the_data_file():
    at = AppTest.from_function(_render_unknown_dungeon_id)
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    body = "\n".join(str(c.value) for c in at.expander[0].caption)
    assert "No danger-pull data yet for Windrunner Spire" in body


def _render_two_dungeons_one_empty_one_unknown() -> None:
    """One sub-section per selected dungeon — proves the expander loops
    over the full selection, not just the first entry."""
    from simf.ui.helpers.danger_pull_cheatsheet import render_danger_pull_cheatsheet

    render_danger_pull_cheatsheet(
        [
            {"id": "altar_of_fangs", "name": "Altar of Fangs"},
            {"id": "murder_row", "name": "Murder Row"},
        ]
    )


def test_render_shows_one_section_per_selected_dungeon():
    at = AppTest.from_function(_render_two_dungeons_one_empty_one_unknown)
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    body = "\n".join(str(m.value) for m in at.expander[0].markdown)
    assert "Altar of Fangs" in body
    assert "Murder Row" in body
    captions = "\n".join(str(c.value) for c in at.expander[0].caption)
    assert captions.count("No danger-pull data yet for") == 2


def test_render_shows_real_pull_content_when_present(monkeypatch):
    """Proves the shell actually renders real content correctly once it
    exists — not just the empty-state path. `AppTest.from_function` can't
    take pytest fixtures inside the script, so the monkeypatch is applied
    directly to the imported module object here, before the script runs —
    `AppTest` re-executes the function's source in its own process-local
    namespace, but the module IT imports (`sys.modules` entry) is the same
    one this test file patched."""
    import simf.ui.helpers.danger_pull_cheatsheet as mod

    fake_pulls = {
        "murder_row": [
            {
                "pull": "The Butcher's opener",
                "danger": "Cleaves the whole party for heavy physical damage.",
                "tip": "Position with your back to a wall before pulling.",
            }
        ]
    }
    monkeypatch.setattr(mod, "load_danger_pulls", lambda: fake_pulls)

    def _render() -> None:
        from simf.ui.helpers.danger_pull_cheatsheet import render_danger_pull_cheatsheet

        render_danger_pull_cheatsheet([{"id": "murder_row", "name": "Murder Row"}])

    at = AppTest.from_function(_render)
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    body = "\n".join(str(m.value) for m in at.expander[0].markdown)
    assert "The Butcher's opener" in body
    assert "Cleaves the whole party for heavy physical damage." in body
    assert "Position with your back to a wall before pulling." in body
