#!/usr/bin/env python3
"""Resolve the .venv directory for simf.

Prefers a local `.venv` at the current working directory. When invoked
from a `.worktrees/<id>/` checkout that lacks its
own `.venv`, falls back to the parent clone's `.venv`. Prints the
absolute path, suitable for the Makefile's `VENV_DIR := $(shell ...)`.

Exits non-zero only on truly unexpected errors; the Makefile then
defaults to `<cwd>/.venv` and the user gets the "binary not found"
error they would have seen anyway.
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKTREE_MARKER = "/.worktrees/"


def detect_venv_dir(start: Path | None = None) -> Path:
    """Return the .venv directory to use, falling back to parent if in a worktree."""
    cwd = (start or Path.cwd()).resolve()
    local = cwd / ".venv"
    if local.is_dir():
        return local

    s = f"{cwd}/"
    if WORKTREE_MARKER in s:
        # cwd looks like .../<parent>/.worktrees/<id>/[...]
        idx = s.find(WORKTREE_MARKER)
        parent = Path(s[:idx])
        parent_venv = parent / ".venv"
        if parent_venv.is_dir():
            return parent_venv

    return local  # last resort; caller will surface missing binary errors


def main() -> int:
    sys.stdout.write(str(detect_venv_dir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
