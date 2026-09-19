"""Versioned calibration-corpus manifests — pin a ratified log set by name.

`calibrate-k` (see cli.py) used to default to recursively scanning all of
`examples/` for any log containing the target's name. As the user's personal
log archive grew, that silently redefined "the ratified 16-log corpus" run
over run — new root-level sessions, other tanks' dedicated subdirectories,
even byte-identical duplicate files all qualified with no guard. See the
2026-07-12 RMSE-drift investigation for the full incident writeup.

A manifest pins the corpus to an explicit ``file[run_index]`` list instead,
so directory growth can never again change what "the ratified corpus" means.
Directory scanning still exists (`--full-scan`) for exploring new logs before
they earn their own ratified manifest.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..core.constants import DATA_DIR

CORPUS_DIR = DATA_DIR / "calibration_corpora"

_FILENAME_DATE_RE = re.compile(r"WoWCombatLog-(\d{2})(\d{2})(\d{2})_")


@dataclass
class CalibrationCorpus:
    """A ratified, versioned log set for one spec's calibration."""

    manifest_path: Path
    spec: str
    character_path: str
    log_target: str
    replays: list[tuple[str, int]]  # (filename, run_index)
    character_snapshot_date: _dt.date | None = None
    k_constant: int | None = None
    expected_rmse: float | None = None


def load_calibration_corpus(manifest_path: Path) -> CalibrationCorpus:
    with manifest_path.open() as f:
        d = yaml.safe_load(f)

    snapshot_date = None
    raw_date = d.get("character_snapshot_date")
    if raw_date is not None:
        # YAML parses an unquoted ISO date as a datetime.date already.
        snapshot_date = (
            raw_date if isinstance(raw_date, _dt.date) else _dt.date.fromisoformat(str(raw_date))
        )

    replays = [(entry["file"], int(entry["run_index"])) for entry in d["replays"]]

    return CalibrationCorpus(
        manifest_path=manifest_path,
        spec=d["spec"],
        character_path=d["character"],
        log_target=d["log_target"],
        replays=replays,
        character_snapshot_date=snapshot_date,
        k_constant=d.get("k_constant"),
        expected_rmse=d.get("expected_rmse"),
    )


def find_manifest_for_character(character_path: str) -> Path | None:
    """Return the ratified manifest whose ``character`` field matches, if any.

    Matches by resolved absolute path so callers can pass a relative or
    absolute ``--character`` value interchangeably. Returns ``None`` (not an
    error) when no manifest exists yet for this character — calibrate-k falls
    back to an explicit-opt-in directory scan in that case.
    """
    if not CORPUS_DIR.is_dir():
        return None
    target = Path(character_path).resolve()
    repo_root = DATA_DIR.parent.parent.parent  # src/simf/data -> repo root
    for manifest_path in sorted(CORPUS_DIR.glob("*.yaml")):
        # A malformed/incomplete manifest elsewhere in the corpus directory
        # (e.g. a half-finished one being drafted for another spec) must not
        # break calibration for a character it has nothing to do with — skip
        # it here. The manifest that actually matches --character, if any,
        # still gets parsed for real (and raises loudly if broken) by
        # load_calibration_corpus once this function returns its path.
        try:
            with manifest_path.open() as f:
                d = yaml.safe_load(f)
            candidate = Path(d["character"])
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            continue
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        if candidate.resolve() == target:
            return manifest_path
    return None


def filename_embedded_date(filename: str) -> _dt.date | None:
    """Parse the MMDDYY date WoW encodes in its combat-log filenames.

    Returns None for names that don't match the pattern (e.g. hand-renamed
    files) rather than raising — this is a best-effort hygiene check, not a
    correctness-critical parse.
    """
    m = _FILENAME_DATE_RE.search(filename)
    if not m:
        return None
    mm, dd, yy = (int(g) for g in m.groups())
    try:
        return _dt.date(2000 + yy, mm, dd)
    except ValueError:
        return None
