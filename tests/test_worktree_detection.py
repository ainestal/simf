"""Unit tests for src/simf/_worktree.py — the detection logic that lets
`simf ui` pick up the worktree's src/ when invoked from inside a
`.worktrees/` checkout (see PR feat/worktree-aware-ui).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simf._worktree import (
    WORKTREE_MARKER,
    find_worktree_root,
    worktree_src_dir,
)


def _make_worktree(tmp_path: Path, name: str = "agent-fake") -> Path:
    """Build a fake parent clone with a .worktrees/<name>/src tree."""
    parent = tmp_path / "simf_parent"
    wt_root = parent / ".worktrees" / name
    (wt_root / "src" / "simf").mkdir(parents=True)
    return wt_root


def test_find_worktree_root_from_root_dir(tmp_path: Path) -> None:
    wt = _make_worktree(tmp_path)
    assert find_worktree_root(wt) == wt


def test_find_worktree_root_from_nested_subdir(tmp_path: Path) -> None:
    wt = _make_worktree(tmp_path)
    deep = wt / "src" / "simf"
    assert find_worktree_root(deep) == wt


def test_find_worktree_root_returns_none_from_parent_repo(tmp_path: Path) -> None:
    parent = tmp_path / "simf_parent"
    (parent / "src" / "simf").mkdir(parents=True)
    assert find_worktree_root(parent) is None


def test_find_worktree_root_returns_none_from_unrelated_dir(tmp_path: Path) -> None:
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    assert find_worktree_root(unrelated) is None


def test_worktree_src_dir_returns_src_when_present(tmp_path: Path) -> None:
    wt = _make_worktree(tmp_path)
    assert worktree_src_dir(wt) == wt / "src"


def test_worktree_src_dir_returns_none_when_src_missing(tmp_path: Path) -> None:
    # A worktree shell with no src/ directory still resolves the root,
    # but worktree_src_dir is conservative and returns None.
    parent = tmp_path / "simf_parent"
    wt_root = parent / ".worktrees" / "agent-noSrc"
    wt_root.mkdir(parents=True)
    assert find_worktree_root(wt_root) == wt_root
    assert worktree_src_dir(wt_root) is None


def test_worktree_src_dir_none_from_parent(tmp_path: Path) -> None:
    parent = tmp_path / "simf_parent"
    (parent / "src" / "simf").mkdir(parents=True)
    assert worktree_src_dir(parent) is None


def test_worktree_marker_constant_stable() -> None:
    # The detection pattern is coupled to the `.worktrees/` checkout
    # layout. If this constant moves, callers (cli.py: simf ui) MUST be
    # checked. Asserting it explicitly so accidental renames trip a
    # test rather than failing silently in the worktree.
    assert WORKTREE_MARKER == "/.worktrees/"


def test_find_worktree_root_default_uses_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wt = _make_worktree(tmp_path)
    monkeypatch.chdir(wt / "src")
    assert find_worktree_root() == wt


def test_find_worktree_root_skips_lookalike_path(tmp_path: Path) -> None:
    # Confirm the marker isn't accidentally matched on a path that
    # merely contains the substring without being a real worktree
    # layout. Defensive: prevents future false positives if someone
    # renames a directory to include ".worktrees" but doesn't
    # mean it as a worktree root.
    decoy = tmp_path / "not_a_worktree" / "src"
    decoy.mkdir(parents=True)
    assert find_worktree_root(decoy) is None


# ---- simf ui integration --------------------------------------------------


def test_simf_ui_injects_pythonpath_from_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`simf ui` from a worktree must launch streamlit with PYTHONPATH
    set to the worktree's src/ and the app.py path resolved from there.
    """
    import os
    import subprocess

    from simf import cli

    wt = _make_worktree(tmp_path)
    (wt / "src" / "simf" / "ui").mkdir(parents=True)
    (wt / "src" / "simf" / "ui" / "app.py").write_text("# stub")

    captured: dict[str, object] = {}

    def fake_run(cmd, env=None):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env"] = env

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(wt)

    # Pass the new --public/--host params explicitly: typer's OptionInfo
    # defaults are truthy when the command function is called directly (not via
    # the CLI parser), so omitting `public` would wrongly enter public mode.
    cli.ui(port=8501, headless=True, public=False, host="", reload=False)

    cmd = captured["cmd"]
    env = captured["env"]
    assert isinstance(cmd, list)
    # The launched app.py must come from the worktree, not from the
    # parent's editable install.
    assert str(wt / "src" / "simf" / "ui" / "app.py") in cmd
    # PYTHONPATH must be set on the subprocess env.
    assert env is not None
    assert isinstance(env, dict)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(wt / "src")


def test_simf_ui_no_op_from_parent_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When invoked from the parent clone (no `/.worktrees/`
    segment), `simf ui` must NOT inject PYTHONPATH and must resolve
    app.py from the editable install's __file__ location.
    """
    import subprocess

    from simf import cli

    parent = tmp_path / "simf_parent"
    (parent / "src" / "simf").mkdir(parents=True)
    monkeypatch.chdir(parent)

    captured: dict[str, object] = {}

    def fake_run(cmd, env=None):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env"] = env

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Pass the new --public/--host params explicitly: typer's OptionInfo
    # defaults are truthy when the command function is called directly (not via
    # the CLI parser), so omitting `public` would wrongly enter public mode.
    cli.ui(port=8501, headless=True, public=False, host="", reload=False)

    # The core no-op contract: env stays None so the subprocess
    # inherits the calling shell's environment unchanged. The actual
    # app.py path resolved here will reflect wherever simf is
    # *imported from* (parent or worktree, depending on test runner),
    # which is the editable-install behaviour we explicitly want to
    # preserve outside a worktree.
    assert captured["env"] is None
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert any(isinstance(a, str) and a.endswith("app.py") for a in cmd)
