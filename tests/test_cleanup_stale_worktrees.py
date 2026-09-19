"""Unit tests for ``scripts/cleanup_stale_worktrees.py``.

Pure-logic only — the classifier and the porcelain parser are exercised
in isolation. Real git calls are NEVER made; the hooks on ``classify``
let us drive the decision tree from synthetic inputs. This is the
mirror image of ``test_prune_merged_branches.py``.

The script lives outside the package, so we load it with importlib the
same way the prune-script tests do.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import time
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "cleanup_stale_worktrees.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("cleanup_stale_worktrees", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cleanup_stale_worktrees"] = mod
    spec.loader.exec_module(mod)
    return mod


csw = _load_module()


# -- porcelain parser ---------------------------------------------------------


def test_parse_porcelain_skips_bare_record():
    """The bare repo entry is a container, not a deletable worktree.

    Killing it would nuke the user's whole checkout, so the parser MUST
    drop it before classify ever sees it.
    """
    text = textwrap.dedent(
        """\
        worktree /home/user/simf
        bare

        worktree /home/user/simf/.worktrees/agent-aaaa
        HEAD abc123
        branch refs/heads/feat/foo
        locked automation agent-aaaa (pid 1)
        """
    )
    records = csw.parse_worktree_porcelain(text)
    paths = [w.path for w in records]
    assert "/home/user/simf" not in paths
    assert records[0].path.endswith("agent-aaaa")
    assert records[0].branch == "feat/foo"
    assert records[0].head == "abc123"
    assert records[0].locked is True


def test_parse_porcelain_handles_detached_head():
    text = textwrap.dedent(
        """\
        worktree /tmp/wt
        HEAD deadbeef
        detached
        """
    )
    records = csw.parse_worktree_porcelain(text)
    assert len(records) == 1
    assert records[0].detached is True
    assert records[0].branch is None
    assert records[0].head == "deadbeef"


def test_parse_porcelain_handles_multiple_records_and_locked_flag():
    """Two real records, one locked one not, must come out independently."""
    text = textwrap.dedent(
        """\
        worktree /tmp/a
        HEAD aaaa
        branch refs/heads/feat/a

        worktree /tmp/b
        HEAD bbbb
        branch refs/heads/worktree-agent-bbbb
        locked
        """
    )
    records = csw.parse_worktree_porcelain(text)
    assert len(records) == 2
    assert records[0].locked is False
    assert records[1].locked is True
    assert records[1].branch == "worktree-agent-bbbb"


# -- classifier ---------------------------------------------------------------


def _wt(
    path: str,
    *,
    head: str = "deadbeef",
    branch: str | None = "worktree-agent-x",
    detached: bool = False,
    locked: bool = True,
):
    return csw.Worktree(path=path, head=head, branch=branch, detached=detached, locked=locked)


def test_paths_outside_loop_prefix_are_protected(tmp_path):
    """The user's main checkout (not under .worktrees/) must never
    be flagged stale, even if every other condition would match."""
    repo_root = str(tmp_path)
    user_checkout = str(tmp_path / "src")
    (tmp_path / "src").mkdir()

    classification = csw.classify(
        _wt(user_checkout),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,  # would otherwise allow delete
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 100.0,
    )
    assert classification.stale is False
    assert "outside" in classification.reason


def test_current_worktree_is_never_stale(tmp_path):
    """The worktree the script is being run FROM must survive — the
    iteration is still using it. Realpath comparison so symlinks can't
    sneak around the guard."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-self"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir)),
        repo_root=repo_root,
        current_worktree_path=str(loop_dir),
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 100.0,
    )
    assert classification.stale is False
    assert "current worktree" in classification.reason


def test_recent_worktree_is_kept_even_when_clean_and_merged(tmp_path):
    """Age gate: a freshly-active worktree under the threshold must
    survive so an in-flight iteration is never disturbed."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-fresh"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir)),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 1.5,  # 1.5h old, under 4h threshold
    )
    assert classification.stale is False
    assert "too recent" in classification.reason
    assert classification.age_hours == 1.5


def test_dirty_worktree_is_kept(tmp_path):
    """Uncommitted/untracked changes block deletion — might be work the
    user wants. This is the conservative-classifier invariant in action."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-dirty"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir)),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,
        has_local_changes_fn=lambda p: True,
        mtime_hours_fn=lambda p: 24.0,
    )
    assert classification.stale is False
    assert classification.has_local_changes is True
    assert "uncommitted" in classification.reason


def test_unmerged_branch_is_kept(tmp_path):
    """If HEAD isn't an ancestor of master, the work hasn't been merged
    (or was squash-merged and the original tip diverged). Conservative
    posture: keep, regardless of age + clean tree."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-unmerged"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir), head="aaaaaaaa"),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: False,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 24.0,
    )
    assert classification.stale is False
    assert classification.branch_merged is False
    assert "not reachable" in classification.reason


def test_merged_old_clean_worktree_is_stale(tmp_path):
    """All five gates pass → STALE. This is the only path that yields a
    deletion candidate, so it pins the happy path."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-stale"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir), head="abcd1234"),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 24.0,
    )
    assert classification.stale is True
    assert classification.branch_merged is True
    assert classification.age_hours == 24.0
    assert "ancestor of origin/master" in classification.reason


def test_detached_head_without_head_sha_is_kept(tmp_path):
    """Defensive: if the porcelain didn't record a HEAD sha, we can't
    do the ancestry check. Don't guess — keep."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-noheaad"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir), head=None, branch=None, detached=True),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 24.0,
    )
    assert classification.stale is False
    assert "no HEAD" in classification.reason


def test_prefix_guard_rejects_sibling_lookalike_dirs(tmp_path):
    """``.worktreesX/agent-foo`` is NOT under ``.worktrees/``
    even though the prefix-as-substring would match. The realpath +
    separator-anchored check must reject it."""
    repo_root = str(tmp_path)
    lookalike = tmp_path / ".worktreesX" / "agent-foo"
    lookalike.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(lookalike)),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 24.0,
    )
    assert classification.stale is False
    assert "outside" in classification.reason


def test_age_threshold_boundary_is_inclusive_at_or_above(tmp_path):
    """A worktree whose age EQUALS the threshold should pass the age
    gate — otherwise a worktree that's been around for exactly the
    threshold would never get cleaned. The check is strict-less-than."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-boundary"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir)),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: True,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 4.0,  # exactly threshold
    )
    assert classification.stale is True


def test_loop_branch_pattern_matches_expected_shape():
    """``worktree-agent-<hex>`` is the loop's auto-name; other shapes
    must not match (or we'd nuke feat/* refs too)."""
    assert csw.LOOP_BRANCH_PATTERN.match("worktree-agent-a5cdac0701c6d04ce")
    assert csw.LOOP_BRANCH_PATTERN.match("worktree-agent-deadbeef")
    assert not csw.LOOP_BRANCH_PATTERN.match("feat/foo")
    assert not csw.LOOP_BRANCH_PATTERN.match("worktree-agent-NOT-HEX")
    assert not csw.LOOP_BRANCH_PATTERN.match("agent-deadbeef")
    assert not csw.LOOP_BRANCH_PATTERN.match("worktree-agent-")


def test_mtime_hours_returns_positive_for_just_touched_file(tmp_path):
    """Sanity check on the real-mtime helper. The age of a file just
    created should be small but non-negative (clock skew tolerated)."""
    p = tmp_path / "touched"
    p.write_text("x")
    age = csw._mtime_hours(str(p), now=time.time() + 60)  # 1 min later
    assert age is not None
    assert 0.0 <= age < 1.0  # well under an hour


def test_mtime_hours_returns_none_for_missing_path():
    age = csw._mtime_hours("/nonexistent/path/should/not/exist")
    assert age is None


# -- ignore-paths allowlist ---------------------------------------------------


def test_path_is_ignorable_matches_directory_entries():
    """Directory ignores match the dir itself and any path inside it.

    This is the core fix for the ``?? .venv`` false-positive: every
    accumulated worktree shows the venv as untracked, and we must NOT
    treat that as "user work in progress."
    """
    ignore = frozenset({".venv/", "__pycache__/"})
    assert csw._path_is_ignorable(".venv", ignore)
    assert csw._path_is_ignorable(".venv/lib/python/site-packages/foo.py", ignore)
    assert csw._path_is_ignorable("__pycache__", ignore)
    assert csw._path_is_ignorable("__pycache__/foo.cpython-313.pyc", ignore)


def test_path_is_ignorable_does_not_match_sibling_prefix():
    """``.venvX`` is NOT ``.venv/`` — the directory ignore is anchored
    on a separator. Otherwise we'd swallow a real ``.venvfoo`` directory
    that the user created on purpose."""
    ignore = frozenset({".venv/"})
    assert csw._path_is_ignorable(".venv", ignore)
    assert not csw._path_is_ignorable(".venvfoo", ignore)
    assert not csw._path_is_ignorable("venv-other", ignore)


def test_path_is_ignorable_matches_exact_file_entries():
    """File entries (no trailing slash) must match exactly — partial
    matches would let arbitrary files past the gate."""
    ignore = frozenset({".coverage"})
    assert csw._path_is_ignorable(".coverage", ignore)
    assert not csw._path_is_ignorable(".coverage.bak", ignore)
    assert not csw._path_is_ignorable("src/.coverage", ignore)


def test_parse_porcelain_status_handles_renames():
    """Status lines for renames have the form ``R  old -> new``.
    The path we care about is the SOURCE — the new location is
    derived from the same content, so we want to gate on the change
    being non-trivial regardless of where it ended up."""
    text = "R  old/path -> new/path\n?? .venv\n M src/foo.py\n"
    paths = csw._parse_porcelain_status_paths(text)
    assert "old/path" in paths
    assert ".venv" in paths
    assert "src/foo.py" in paths


def test_has_local_changes_filters_venv_noise_against_real_helper(tmp_path, monkeypatch):
    """When ``git status`` returns ONLY ignored paths, the helper must
    report clean. When even one real path is present, it must report
    dirty. This pins the actual filter end-to-end."""

    def fake_git(args, *, cwd):
        # `_git` is called as `_git([...], cwd=path)`. Return ``?? .venv`` —
        # the universal false positive in every accumulated worktree.
        class R:
            returncode = 0
            stdout = "?? .venv\n"
            stderr = ""

        return R()

    monkeypatch.setattr(csw, "_git", fake_git)
    assert csw._has_local_changes(str(tmp_path)) is False

    def fake_git_dirty(args, *, cwd):
        class R:
            returncode = 0
            stdout = "?? .venv\n M src/real_change.py\n"
            stderr = ""

        return R()

    monkeypatch.setattr(csw, "_git", fake_git_dirty)
    assert csw._has_local_changes(str(tmp_path)) is True


def test_has_local_changes_treats_git_error_as_dirty(tmp_path, monkeypatch):
    """If ``git status`` itself errors, we don't know the state —
    refuse to delete. This is the conservative-default invariant."""

    def fake_git(args, *, cwd):
        class R:
            returncode = 1
            stdout = ""
            stderr = "fatal: not a git repository"

        return R()

    monkeypatch.setattr(csw, "_git", fake_git)
    assert csw._has_local_changes(str(tmp_path)) is True


# -- PR-merge fallback --------------------------------------------------------


def _pr(number: int, head: str, *, merged: bool):
    return {
        "number": number,
        "head": {"ref": head},
        "merged_at": "2026-05-27T00:00:00Z" if merged else None,
        "state": "closed" if merged else "open",
    }


def test_branch_has_merged_pr_true_when_newest_pr_merged():
    prs = {"feat/foo": [_pr(42, "feat/foo", merged=True)]}
    assert csw.branch_has_merged_pr("feat/foo", prs) is True


def test_branch_has_merged_pr_false_when_no_matching_pr():
    assert csw.branch_has_merged_pr("feat/missing", {}) is False


def test_branch_has_merged_pr_false_for_open_pr():
    prs = {"feat/foo": [_pr(42, "feat/foo", merged=False)]}
    assert csw.branch_has_merged_pr("feat/foo", prs) is False


def test_branch_has_merged_pr_picks_newest_pr_by_number():
    """If the same branch had multiple PRs (rare), the NEWEST decides —
    same rule as ``prune_merged_branches.classify_branch``. A merged
    older PR followed by an open newer PR means the branch is NOT
    safe to delete."""
    prs = {
        "feat/foo": [
            _pr(1, "feat/foo", merged=True),
            _pr(2, "feat/foo", merged=False),
        ]
    }
    assert csw.branch_has_merged_pr("feat/foo", prs) is False


def test_branch_has_merged_pr_none_branch_is_false():
    """Detached worktrees have no branch — the PR check must return
    False so the caller falls back to ancestry-only."""
    assert csw.branch_has_merged_pr(None, {}) is False


def test_classify_uses_pr_merge_signal_when_ancestor_check_fails(tmp_path):
    """The squash-merge case: HEAD is NOT reachable from master, but
    the branch's PR was merged. The classifier must mark STALE so the
    worktree gets cleaned up. Without this signal, squash-merged
    worktrees would accumulate forever — which is exactly the bug
    that motivated this script."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-squashed"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir), branch="feat/squashed", head="aaaa1111"),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: False,  # squash dropped ancestry
        branch_merged_fn=lambda b: b == "feat/squashed",
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 24.0,
    )
    assert classification.stale is True
    assert "PR for feat/squashed merged" in classification.reason


def test_classify_unmerged_when_neither_signal_fires(tmp_path):
    """The genuinely-unfinished case: ancestor check fails AND no
    merged PR for the branch. Must KEEP to avoid trashing user work."""
    repo_root = str(tmp_path)
    loop_dir = tmp_path / ".worktrees" / "agent-wip"
    loop_dir.mkdir(parents=True)

    classification = csw.classify(
        _wt(str(loop_dir), branch="feat/wip", head="bbbb2222"),
        repo_root=repo_root,
        current_worktree_path="/elsewhere",
        master_ref="origin/master",
        age_threshold_hours=4.0,
        is_ancestor_fn=lambda sha: False,
        branch_merged_fn=lambda b: False,
        has_local_changes_fn=lambda p: False,
        mtime_hours_fn=lambda p: 24.0,
    )
    assert classification.stale is False
    assert "not reachable" in classification.reason
    assert "no merged PR" in classification.reason


def test_default_ignore_paths_includes_venv_and_loop_artifacts():
    """The defaults are visible at module scope so the test can pin
    them. ``.venv/`` is the load-bearing entry — that's the universal
    false-positive without which the script's real-repo dry-run shows
    0 candidates."""
    assert ".venv/" in csw.DEFAULT_IGNORE_PATHS
    assert "__pycache__/" in csw.DEFAULT_IGNORE_PATHS
    assert "loop-summary.md" in csw.DEFAULT_IGNORE_PATHS
