"""Guard against runtime data files being silently dropped from the wheel.

The whole `src/simf/data/` tree ships in the wheel (hatchling's `packages` walk
+ the `shared-data` map). Hatchling applies `.gitignore` during that walk, so a
tracked-but-gitignored data file is SILENTLY excluded from the built wheel even
though it's committed to the repo — and the installed app then crashes at
runtime with `FileNotFoundError` the moment it loads that file. This is exactly
how an over-broad `profiles/` ignore rule (meant for local py-spy/cProfile
dumps) knocked `data/profiles/` out of the public deploy and broke every sim.

Source-tree existence checks (e.g. test_demo_packaging) can't catch this — the
file IS in the dev tree; it's only missing from the WHEEL. So assert directly
that nothing under `src/simf/data/` is gitignored.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_REL = "src/simf/data/"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def test_no_packaged_data_file_is_gitignored():
    """Every tracked file under src/simf/data/ must ship in the wheel — i.e.
    none may be matched by .gitignore (which hatchling honours when building)."""
    tracked = _git("ls-files", DATA_REL)
    if tracked.returncode != 0 or not tracked.stdout.strip():
        pytest.skip("not a git checkout / no tracked data files")
    files = tracked.stdout.split()
    # `git check-ignore` prints the paths it WOULD ignore (exit 0 if any match).
    ignored = _git("check-ignore", *files).stdout.split()
    assert not ignored, (
        "These tracked data files are gitignored, so hatchling drops them from "
        "the wheel and the installed app crashes (FileNotFoundError) at runtime: "
        f"{ignored}"
    )


def test_sim_profiles_are_present():
    """The damage/healing profiles the engine loads exist in the data tree
    (paired with the gitignore guard above, this means they reach the wheel)."""
    prof = REPO_ROOT / "src" / "simf" / "data" / "profiles"
    assert (prof / "damage" / "m+_boss_tankbuster.yaml").exists()
    assert (prof / "healing" / "m+_high_key_healer.yaml").exists()
