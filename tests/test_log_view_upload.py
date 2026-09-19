"""Regression tests for the log-upload pipeline.

The user can drop their own `WoWCombatLog-*.txt` into the "Why did I die?"
surface. Uploads land in `~/.simf/logs/` (the user-writable sink, kept
out of the repo's `examples/` directory).

These tests pin the contract that:
  - `list_logs()` aggregates both EXAMPLES_DIR and UPLOADS_DIR
  - `_resolve_log_path` prefers UPLOADS_DIR on a basename collision
  - the file-uploader widget is wired into `render_surface_log`

Without these, an empty `examples/` setup (any user who isn't Brutoh)
silently bricks the surface even after they pick their log.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from simf.ui import log_data, log_view


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect EXAMPLES_DIR and UPLOADS_DIR to disposable tmpdirs and
    clear the cached `list_logs` so each test sees a clean slate."""
    examples = tmp_path / "examples"
    uploads = tmp_path / "uploads"
    examples.mkdir()
    uploads.mkdir()
    # `EXAMPLES_DIR` / `UPLOADS_DIR` and `list_logs` / `_resolve_log_path` now
    # live in `log_data`; the functions read those constants via log_data's
    # globals, so the patch target is `log_data` (patching the log_view facade
    # would be inert). `list_logs` is the same @st.cache_data object either way
    # (facade re-export is a name-rebind), so `.clear()` works on log_data too.
    monkeypatch.setattr(log_data, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(log_data, "UPLOADS_DIR", uploads)
    log_data.list_logs.clear()
    yield examples, uploads
    log_data.list_logs.clear()


def test_list_logs_aggregates_examples_and_uploads(isolated_dirs):
    """A log in either directory should appear in the dropdown."""
    examples, uploads = isolated_dirs
    (examples / "WoWCombatLog-010125_120000.txt").write_text("")
    (uploads / "WoWCombatLog-051625_180000.txt").write_text("")

    logs = log_data.list_logs()

    assert "WoWCombatLog-010125_120000.txt" in logs
    assert "WoWCombatLog-051625_180000.txt" in logs
    # Reverse-alphabetical → newest filename first.
    assert logs[0] == "WoWCombatLog-051625_180000.txt"


def test_list_logs_dedupes_collisions(isolated_dirs):
    """If the same basename exists in both dirs, the dropdown lists it once."""
    examples, uploads = isolated_dirs
    name = "WoWCombatLog-051625_180000.txt"
    (examples / name).write_text("from-examples")
    (uploads / name).write_text("from-uploads")

    logs = log_data.list_logs()

    assert logs.count(name) == 1


def test_resolve_log_path_prefers_uploads_on_collision(isolated_dirs):
    """On a basename collision, the upload wins — the user just put it there."""
    examples, uploads = isolated_dirs
    name = "WoWCombatLog-051625_180000.txt"
    (examples / name).write_text("from-examples")
    (uploads / name).write_text("from-uploads")

    resolved = log_data._resolve_log_path(name)

    assert resolved == uploads / name
    assert resolved.read_text() == "from-uploads"


def test_resolve_log_path_falls_back_to_examples(isolated_dirs):
    """Bundled demo logs (only in `examples/`) must still resolve."""
    examples, _uploads = isolated_dirs
    name = "WoWCombatLog-bundled.txt"
    (examples / name).write_text("demo")

    resolved = log_data._resolve_log_path(name)

    assert resolved == examples / name


def test_resolve_log_path_missing_falls_back_to_examples(isolated_dirs):
    """An unknown name falls through to EXAMPLES_DIR. The caller (cached
    helper) then fails at the parser layer — we don't paper over a
    missing file with a confusing fallback path."""
    examples, _uploads = isolated_dirs

    resolved = log_data._resolve_log_path("WoWCombatLog-missing.txt")

    assert resolved == examples / "WoWCombatLog-missing.txt"
    assert not resolved.exists()


def test_render_surface_log_has_file_uploader_widget():
    """The Why-did-I-die surface must include a file_uploader widget so
    users without write access to the repo's `examples/` can analyze
    their own logs. Pins the wiring so a future refactor doesn't quietly
    drop the upload path."""
    src = inspect.getsource(log_view.render_surface_log) + inspect.getsource(
        log_view._handle_log_upload
    )
    assert "st.file_uploader" in src, (
        "Log surface lost its file_uploader — users can no longer upload their own combat logs."
    )


def test_uploads_dir_default_is_under_dot_simf():
    """Sanity-check the default location — `~/.simf/logs/` is the repo's
    user-writable convention (alongside `blizzard.yaml`, `wcl_config.yaml`).
    Pinning it prevents an accidental refactor to a hard-coded tmpdir or
    a path inside the repo."""
    # Re-import to bypass the monkeypatched fixture, if any. UPLOADS_DIR now
    # lives in `log_data` (the log_view facade re-exports it), so pin it at the
    # definition site.
    from importlib import reload

    from simf.ui import log_data as fresh

    reload(fresh)
    assert Path.home() / ".simf" / "logs" == fresh.UPLOADS_DIR
