"""Regression tests for `scripts/hooks/pre-push`.

Two contracts under test:

1. SKIP_PUSH_TESTS=1 short-circuits BEFORE any pytest probe (PR #110) — so
   worktrees that haven't symlinked `.venv` can still opt out and push.
2. The pytest binary is resolved via `scripts/detect_venv.py`'s walk-up
   (PR #120 pattern, mirrored from pre-commit). Worktrees without a local
   `.venv` find the parent clone's `.venv` automatically.

These tests simulate tiny git repos in temp dirs and invoke the hook with
different combinations of (`SKIP_PUSH_TESTS`, fake `.venv/bin/pytest`
present / absent, system pytest present / absent, fake pytest's exit code).
They never run the real test suite — only the hook's control flow.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SRC = REPO_ROOT / "scripts" / "hooks" / "pre-push"
DETECT_VENV_SRC = REPO_ROOT / "scripts" / "detect_venv.py"


def _clean_git_env() -> dict[str, str]:
    """Return a copy of os.environ with all GIT_* leakage stripped.

    When pytest is invoked from inside a `git push` (i.e. the pre-push
    hook running the suite), git exports GIT_DIR / GIT_WORK_TREE /
    GIT_INDEX_FILE / GIT_PREFIX into the child environment. Those vars
    override `-C <path>` for in-test `git add` / `git init` calls and
    make every test that builds a fake repo blow up. Strip them.
    """
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("GIT_"):
            env.pop(k, None)
    return env


def _init_fake_repo(root: Path) -> Path:
    """Initialise a git repo at `root` and copy the hook + detect_venv in.

    The hook is invoked directly (`bash <path>`) for tests, so we don't have
    to wire `.git/hooks/`. But the script calls `git rev-parse
    --show-toplevel`, so this still needs to be a real git repo.
    """
    root.mkdir(parents=True, exist_ok=True)
    env = _clean_git_env()
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.t"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True, env=env)
    scripts = root / "scripts"
    (scripts / "hooks").mkdir(parents=True)
    shutil.copy2(HOOK_SRC, scripts / "hooks" / "pre-push")
    (scripts / "hooks" / "pre-push").chmod(0o755)
    shutil.copy2(DETECT_VENV_SRC, scripts / "detect_venv.py")
    return root


def _make_stub_pytest(bin_dir: Path, *, exit_code: int = 0) -> Path:
    """Write a stub `pytest` binary into `bin_dir` and return its path."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "pytest"
    log = bin_dir.parent / "pytest_invocations.log"
    body = f"""#!/usr/bin/env bash
echo "$@" >> "{log}"
exit {exit_code}
"""
    stub.write_text(body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _run_hook(
    repo: Path, *, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = _clean_git_env()
    # Strip variables that might leak in from the parent pytest run, and pin
    # PATH so the test's own venv doesn't leak a system `pytest` in.
    env.pop("SKIP_PUSH_TESTS", None)
    env["PATH"] = "/usr/bin:/bin"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(repo / "scripts" / "hooks" / "pre-push")],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hook_file_is_executable_bash() -> None:
    """Sanity: the tracked hook exists and is a bash script."""
    assert HOOK_SRC.exists(), f"hook not found at {HOOK_SRC}"
    first_line = HOOK_SRC.read_text().splitlines()[0]
    assert "bash" in first_line, f"unexpected shebang: {first_line!r}"


def test_skip_flag_bypasses_missing_pytest(tmp_path: Path) -> None:
    """SKIP_PUSH_TESTS=1 must short-circuit BEFORE the pytest probe.

    Regression for PR #110: prior to that fix, the hook errored out with
    "pytest not found" even when the operator had explicitly opted out of
    tests via the skip flag.
    """
    repo = _init_fake_repo(tmp_path / "repo")
    # No .venv, no system pytest.
    result = _run_hook(repo, env_extra={"SKIP_PUSH_TESTS": "1"})
    assert result.returncode == 0, (
        f"hook must exit 0 when SKIP_PUSH_TESTS=1 even without .venv\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "skipping tests" in result.stdout.lower()


def test_no_skip_no_pytest_anywhere_fails_with_install_hint(tmp_path: Path) -> None:
    """Without skip AND without any pytest, the hook exits non-zero with
    a hint to either install or set SKIP_PUSH_TESTS=1."""
    repo = _init_fake_repo(tmp_path / "repo")
    result = _run_hook(repo)
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "not found" in combined
    assert "skip_push_tests" in combined, (
        f"error message must mention the escape hatch; got: {combined!r}"
    )


def test_local_venv_pytest_is_used(tmp_path: Path) -> None:
    """When repo/.venv/bin/pytest exists, the hook uses it (happy path)."""
    repo = _init_fake_repo(tmp_path / "repo")
    _make_stub_pytest(repo / ".venv" / "bin", exit_code=0)
    result = _run_hook(repo)
    assert result.returncode == 0, (
        f"hook must succeed when pytest passes\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    log = (repo / ".venv" / "pytest_invocations.log").read_text()
    assert "-q" in log


def test_worktree_parent_venv_is_used(tmp_path: Path) -> None:
    """In a `.worktrees/<id>/` repo without its own .venv, the
    parent clone's .venv is found via scripts/detect_venv.py — no manual
    symlink required. This is the PR #120 walk-up contract."""
    parent = tmp_path / "parent"
    parent.mkdir()
    _make_stub_pytest(parent / ".venv" / "bin", exit_code=0)

    worktree = parent / ".worktrees" / "agent-xyz"
    repo = _init_fake_repo(worktree)
    result = _run_hook(repo)

    assert result.returncode == 0, (
        f"hook must find parent's .venv from a worktree\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    log = (parent / ".venv" / "pytest_invocations.log").read_text()
    assert "-q" in log


def test_falls_back_to_system_pytest(tmp_path: Path) -> None:
    """No .venv anywhere, but `command -v pytest` resolves → use system."""
    repo = _init_fake_repo(tmp_path / "repo")
    sys_bin = tmp_path / "sysbin"
    _make_stub_pytest(sys_bin, exit_code=0)

    env = {"PATH": f"{sys_bin}:/usr/bin:/bin"}
    result = _run_hook(repo, env_extra=env)

    assert result.returncode == 0, (
        f"hook must fall back to system pytest\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    log = (sys_bin.parent / "pytest_invocations.log").read_text()
    assert "-q" in log


def test_failing_pytest_blocks_push(tmp_path: Path) -> None:
    """Hook MUST exit non-zero when pytest fails — bypassing tests
    remains an explicit opt-in via SKIP_PUSH_TESTS=1."""
    repo = _init_fake_repo(tmp_path / "repo")
    _make_stub_pytest(repo / ".venv" / "bin", exit_code=1)
    result = _run_hook(repo)
    assert result.returncode != 0, (
        f"hook must NOT swallow pytest failures\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_skip_flag_wins_over_failing_pytest(tmp_path: Path) -> None:
    """If SKIP_PUSH_TESTS=1 is set, the hook must not even execute pytest
    (it's the escape hatch — its whole job is to bypass)."""
    repo = _init_fake_repo(tmp_path / "repo")
    _make_stub_pytest(repo / ".venv" / "bin", exit_code=1)
    result = _run_hook(repo, env_extra={"SKIP_PUSH_TESTS": "1"})
    assert result.returncode == 0
    assert "skipping tests" in result.stdout.lower()


def test_handles_missing_detect_venv_helper(tmp_path: Path) -> None:
    """If detect_venv.py is missing, fall back via system pytest (or fail
    cleanly). The hook must not crash on `python3 detect_venv.py` errors."""
    repo = _init_fake_repo(tmp_path / "repo")
    (repo / "scripts" / "detect_venv.py").unlink()

    # No .venv pytest, no system pytest → exit non-zero with install hint.
    result = _run_hook(repo)
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "not found" in combined


def test_local_venv_preferred_over_system(tmp_path: Path) -> None:
    """When both `<repo>/.venv/bin/pytest` and `command -v pytest` exist,
    the local venv MUST win — system pytest could be the wrong Python
    version or have unrelated plugins installed."""
    repo = _init_fake_repo(tmp_path / "repo")
    _make_stub_pytest(repo / ".venv" / "bin", exit_code=0)
    sys_bin = tmp_path / "sysbin"
    # System stub exits NON-zero so we can detect if it was wrongly picked.
    _make_stub_pytest(sys_bin, exit_code=99)

    env = {"PATH": f"{sys_bin}:/usr/bin:/bin"}
    result = _run_hook(repo, env_extra=env)
    assert result.returncode == 0, (
        f"local .venv pytest must take precedence over system\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


@pytest.fixture(autouse=True)
def _require_bash() -> None:
    """Skip the whole module if bash isn't available (e.g., Windows CI)."""
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
