#!/usr/bin/env python3
"""Print the absolute path of the current worktree's `src/` directory,
or nothing if we're not inside a `.worktrees/` checkout.

Used by the Makefile to inject PYTHONPATH so `make ui` / `make
screenshot` (and any other target invoking the parent .venv's
`simf` console-script) pick up the worktree's source code instead of
the parent clone's. See simf._worktree for the matching Python
detection contract and tests/test_worktree_detection.py for coverage.

Usage:
    PYTHONPATH=$(scripts/detect_worktree_src.py) python -m simf.cli ...
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKTREE_MARKER = "/.worktrees/"


def detect_worktree_src(start: Path | None = None) -> Path | None:
    """Return `<worktree>/src` if `start` (default: cwd) is inside one."""
    here = (start or Path.cwd()).resolve()
    if WORKTREE_MARKER not in f"{here}/":
        return None
    cur = here
    while cur != cur.parent:
        if cur.parent.as_posix().endswith("/.worktrees"):
            src = cur / "src"
            return src if src.is_dir() else None
        cur = cur.parent
    return None


def main() -> int:
    src = detect_worktree_src()
    if src is not None:
        sys.stdout.write(str(src))
    return 0


if __name__ == "__main__":
    sys.exit(main())
