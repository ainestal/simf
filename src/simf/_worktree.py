"""Worktree detection so `simf ui` (and `make screenshot`) picks up the
src/ of the *current* working tree rather than whatever editable install
the .venv was originally created against.

Background: simf's `.venv` is created once in the parent clone and
editable-installs from that clone's `src/simf`. Git worktrees created
under `.worktrees/<id>/` share that .venv but each has its own
`src/simf`. Without intervention, `streamlit run` from inside a worktree
still imports the parent's modules, so UI changes made in the worktree
are invisible until merged.

The helper detects whether `cwd` (or an explicit override) lives inside
a worktree by looking for the `/.worktrees/` path segment, and
returns the local `src/` directory to be injected onto PYTHONPATH for
any Streamlit subprocess. Detection is pattern-based (no shelling out
to `git`) so it's cheap and easy to unit-test.

If cwd is the parent repo, the helper returns None and callers should
behave exactly as before — no PYTHONPATH manipulation, no surprises.
"""

from __future__ import annotations

from pathlib import Path

# Path segment that marks a worktree checkout. Coupled to the current
# `.worktrees/<id>/` layout — if that ever moves, update here.
WORKTREE_MARKER = "/.worktrees/"


def find_worktree_root(start: Path | None = None) -> Path | None:
    """Return the worktree root if `start` (default: cwd) lives in one.

    A worktree root is the directory immediately under
    `.worktrees/`. Returns None when called from outside a
    worktree (the parent repo, an unrelated dir, etc.).

    Walks up the filesystem; stops at the worktree root marker or at /.
    Stable against being called from any depth inside the worktree.
    """
    here = (start or Path.cwd()).resolve()
    if WORKTREE_MARKER not in f"{here}/":
        return None
    # Walk up until the parent is `.../.worktrees`.
    cur = here
    while cur != cur.parent:
        if cur.parent.as_posix().endswith("/.worktrees"):
            return cur
        cur = cur.parent
    return None


def worktree_src_dir(start: Path | None = None) -> Path | None:
    """Return `<worktree>/src` if we're in a worktree and it exists.

    Returns None from the parent repo, so callers can no-op cleanly.
    Also None if the worktree somehow lacks a `src/` directory (defensive).
    """
    root = find_worktree_root(start)
    if root is None:
        return None
    src = root / "src"
    return src if src.is_dir() else None
