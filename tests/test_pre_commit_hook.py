"""Regression tests for scripts/hooks/pre-commit.

Each test invokes the hook script directly against a tmp_path fake repo and
asserts on exit code / stderr / stub-binary side effects. The goal is to lock
in the venv-detection contract introduced when the hook stopped requiring a
manual `.venv` symlink in `.worktrees/` checkouts:

  1. Local .venv/bin/ruff → use it.
  2. No local .venv but in a worktree where the parent has .venv → use parent.
  3. No .venv anywhere but `command -v ruff` resolves → use system ruff.
  4. Nothing available → skip with a warning, exit 0 (do NOT block the commit).
  5. Real ruff violations still get caught when ruff is found.
  6. No Python files staged → exit 0 without touching ruff.

The hook calls `git rev-parse --show-toplevel`, so each test sets up a tiny
git repo. It then calls `python3 scripts/detect_venv.py` to find the venv,
so we copy detect_venv.py into the fake repo too.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SRC = REPO_ROOT / "scripts" / "hooks" / "pre-commit"
DETECT_VENV_SRC = REPO_ROOT / "scripts" / "detect_venv.py"


def _clean_git_env() -> dict[str, str]:
    """Strip GIT_* env vars so in-test `git -C <path>` honours its -C arg.

    When pytest runs under `git push` (pre-push hook), git exports
    GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE, which override -C and steer
    the in-test git commands into the OUTER repo. Strip them.
    """
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("GIT_"):
            env.pop(k, None)
    return env


def _make_fake_repo(root: Path) -> Path:
    """Initialise a git repo at `root` and copy the hook + detect_venv in."""
    root.mkdir(parents=True, exist_ok=True)
    env = _clean_git_env()
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.t"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True, env=env)
    scripts = root / "scripts"
    (scripts / "hooks").mkdir(parents=True)
    shutil.copy2(HOOK_SRC, scripts / "hooks" / "pre-commit")
    (scripts / "hooks" / "pre-commit").chmod(0o755)
    shutil.copy2(DETECT_VENV_SRC, scripts / "detect_venv.py")
    return root


def _make_stub_ruff(bin_dir: Path, *, fail_on_check: bool = False) -> Path:
    """Write a stub `ruff` binary into `bin_dir` and return its path."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "ruff"
    log = bin_dir.parent / "ruff_invocations.log"
    body = f"""#!/usr/bin/env bash
echo "$@" >> "{log}"
if [[ "$1" == "check" && "{int(fail_on_check)}" == "1" ]]; then
  echo "fake-ruff: violation" >&2
  exit 1
fi
exit 0
"""
    stub.write_text(body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _stage_python_file(repo: Path, name: str = "demo.py") -> None:
    f = repo / name
    f.write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", name], check=True, env=_clean_git_env())


def _run_hook(repo: Path, *, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = _clean_git_env()
    # Strip the test process's venv off PATH so stray system ruff doesn't leak in.
    env["PATH"] = "/usr/bin:/bin"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(repo / "scripts" / "hooks" / "pre-commit")],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_local_venv_ruff_is_used(tmp_path: Path) -> None:
    """When repo/.venv/bin/ruff exists, the hook uses it."""
    repo = _make_fake_repo(tmp_path / "repo")
    stub = _make_stub_ruff(repo / ".venv" / "bin")
    _stage_python_file(repo)

    result = _run_hook(repo)

    assert result.returncode == 0, result.stderr
    log = (repo / ".venv" / "ruff_invocations.log").read_text()
    assert "check --fix" in log
    assert "format" in log
    # Sanity: stub path was actually invoked.
    assert stub.exists()


def test_worktree_parent_venv_is_used(tmp_path: Path) -> None:
    """In a `.worktrees/<id>/` repo, the parent's .venv is found."""
    parent = tmp_path / "parent"
    parent.mkdir()
    _make_stub_ruff(parent / ".venv" / "bin")

    worktree = parent / ".worktrees" / "agent-xyz"
    repo = _make_fake_repo(worktree)
    _stage_python_file(repo)

    result = _run_hook(repo)

    assert result.returncode == 0, result.stderr
    log = (parent / ".venv" / "ruff_invocations.log").read_text()
    assert "check --fix" in log


def test_falls_back_to_system_ruff(tmp_path: Path) -> None:
    """No .venv anywhere, but `command -v ruff` resolves → use system ruff."""
    repo = _make_fake_repo(tmp_path / "repo")
    _stage_python_file(repo)

    # Stage a stub ruff on a PATH dir we control.
    sys_bin = tmp_path / "sysbin"
    _make_stub_ruff(sys_bin)

    env = {"PATH": f"{sys_bin}:/usr/bin:/bin"}
    result = _run_hook(repo, extra_env=env)

    assert result.returncode == 0, result.stderr
    log = (sys_bin.parent / "ruff_invocations.log").read_text()
    assert "check --fix" in log


def test_skip_when_no_ruff_available(tmp_path: Path) -> None:
    """No .venv, no system ruff → hook prints a warning and exits 0."""
    repo = _make_fake_repo(tmp_path / "repo")
    _stage_python_file(repo)

    result = _run_hook(repo)

    assert result.returncode == 0, result.stderr
    assert "ruff not found" in result.stderr
    assert "skipping lint" in result.stderr


def test_ruff_violation_still_blocks_commit(tmp_path: Path) -> None:
    """When ruff IS found, a real violation must still fail the hook."""
    repo = _make_fake_repo(tmp_path / "repo")
    _make_stub_ruff(repo / ".venv" / "bin", fail_on_check=True)
    _stage_python_file(repo)

    result = _run_hook(repo)

    assert result.returncode != 0
    assert "fake-ruff: violation" in result.stderr


def test_no_python_staged_skips_ruff(tmp_path: Path) -> None:
    """No staged .py files → hook exits 0 without invoking ruff."""
    repo = _make_fake_repo(tmp_path / "repo")
    _make_stub_ruff(repo / ".venv" / "bin")
    # Stage a non-Python file.
    f = repo / "notes.txt"
    f.write_text("hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True, env=_clean_git_env())

    result = _run_hook(repo)

    assert result.returncode == 0, result.stderr
    log_path = repo / ".venv" / "ruff_invocations.log"
    # Stub never ran, so the log file does not exist.
    assert not log_path.exists()


@pytest.mark.parametrize("missing_detect", [True, False])
def test_handles_missing_detect_venv_helper(tmp_path: Path, missing_detect: bool) -> None:
    """If detect_venv.py is removed, fall back via system ruff (or skip)."""
    repo = _make_fake_repo(tmp_path / "repo")
    if missing_detect:
        (repo / "scripts" / "detect_venv.py").unlink()
    _stage_python_file(repo)

    # No .venv ruff, no system ruff → skip with exit 0.
    result = _run_hook(repo)
    assert result.returncode == 0, result.stderr
