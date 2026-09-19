"""Ratified calibration-corpus manifests — pin a log set by name, not by scan.

Guards the fix for the 2026-07-12 RMSE-drift incident: `calibrate-k` used to
default to a recursive rglob of `examples/`, so the documented 16-log Prot
Warrior baseline (RMSE 0.068) silently grew to 79 replays (RMSE 0.124) as the
user's personal log archive filled `examples/` — no engine change, pure
corpus scope-creep + duplicate files + gear-drift-vs-frozen-character. See
docs/validation/ and the memory note on the investigation.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from simf.io.calibration_corpus import (
    CORPUS_DIR,
    filename_embedded_date,
    find_manifest_for_character,
    load_calibration_corpus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_MANIFEST = CORPUS_DIR / "prot_warrior_2026_05.yaml"


def _write_manifest(path: Path, **overrides) -> Path:
    d = {
        "spec": "protection_warrior",
        "character": "src/simf/data/characters/brutoh-calibration-2026-05.yaml",
        "character_snapshot_date": dt.date(2026, 5, 6),
        "log_target": "Brutoh-Uldum-EU",
        "k_constant": 3430,
        "expected_rmse": 0.068,
        "replays": [
            {"file": "a.txt", "run_index": 0},
            {"file": "b.txt", "run_index": 2},
        ],
    }
    d.update(overrides)
    path.write_text(yaml.safe_dump(d))
    return path


def test_shipped_manifest_pins_exactly_the_16_ratified_replays():
    """This is the exact file[run_index] list docs/validation/k_calibration_2026_05_18.md
    measured RMSE 0.068 against. Changing this file's replay list silently
    invalidates that headline number — this test exists so that can't
    happen by accident."""
    corpus = load_calibration_corpus(SHIPPED_MANIFEST)
    assert corpus.spec == "protection_warrior"
    assert corpus.log_target == "Brutoh-Uldum-EU"
    assert corpus.k_constant == 3430
    assert corpus.character_snapshot_date == dt.date(2026, 5, 6)
    assert len(corpus.replays) == 16
    # The known-excluded fragment (the tiny partial the original 2026-05-18
    # curation left out) must stay excluded.
    assert ("WoWCombatLog-051526_210245.txt", 1) not in corpus.replays
    # But its siblings from the same file (0, 2, 3) are in.
    assert ("WoWCombatLog-051526_210245.txt", 0) in corpus.replays
    assert ("WoWCombatLog-051526_210245.txt", 2) in corpus.replays
    assert ("WoWCombatLog-051526_210245.txt", 3) in corpus.replays


def test_shipped_manifest_character_matches_the_frozen_calibration_yaml():
    corpus = load_calibration_corpus(SHIPPED_MANIFEST)
    assert (REPO_ROOT / corpus.character_path).exists()
    assert "brutoh-calibration-2026-05.yaml" in corpus.character_path


def test_find_manifest_for_character_resolves_the_shipped_manifest():
    frozen_char = str(
        REPO_ROOT / "src" / "simf" / "data" / "characters" / "brutoh-calibration-2026-05.yaml"
    )
    found = find_manifest_for_character(frozen_char)
    assert found == SHIPPED_MANIFEST


def test_find_manifest_for_character_matches_relative_and_absolute_paths_the_same():
    rel = "src/simf/data/characters/brutoh-calibration-2026-05.yaml"
    abs_path = str(REPO_ROOT / rel)
    assert find_manifest_for_character(rel) == find_manifest_for_character(abs_path)


def test_find_manifest_for_character_returns_none_for_an_uncatalogued_character(tmp_path):
    other_char = tmp_path / "some_other_tank.yaml"
    other_char.write_text(yaml.safe_dump({"name": "SomeoneElse"}))
    assert find_manifest_for_character(str(other_char)) is None


def test_load_calibration_corpus_round_trips_a_manifest(tmp_path):
    manifest = _write_manifest(tmp_path / "test_manifest.yaml")
    corpus = load_calibration_corpus(manifest)
    assert corpus.replays == [("a.txt", 0), ("b.txt", 2)]
    assert corpus.character_snapshot_date == dt.date(2026, 5, 6)
    assert corpus.expected_rmse == 0.068


def test_load_calibration_corpus_accepts_a_quoted_iso_date_string(tmp_path):
    """YAML parses an unquoted date as a real date already; a quoted string
    (e.g. because someone hand-edited the file) must still work."""
    manifest = _write_manifest(
        tmp_path / "test_manifest.yaml", character_snapshot_date="2026-05-06"
    )
    corpus = load_calibration_corpus(manifest)
    assert corpus.character_snapshot_date == dt.date(2026, 5, 6)


def test_load_calibration_corpus_tolerates_missing_optional_fields(tmp_path):
    manifest = tmp_path / "minimal.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "spec": "protection_warrior",
                "character": "whatever.yaml",
                "log_target": "Someone-Realm",
                "replays": [{"file": "a.txt", "run_index": 0}],
            }
        )
    )
    corpus = load_calibration_corpus(manifest)
    assert corpus.character_snapshot_date is None
    assert corpus.k_constant is None
    assert corpus.expected_rmse is None


def test_filename_embedded_date_parses_wow_log_naming():
    assert filename_embedded_date("WoWCombatLog-070326_074225.txt") == dt.date(2026, 7, 3)
    assert filename_embedded_date("WoWCombatLog-050626_172823.txt") == dt.date(2026, 5, 6)


def test_filename_embedded_date_handles_a_prefixed_variant():
    """Some archived files carry an extra prefix (e.g. Archive-, Split-)."""
    assert filename_embedded_date("Archive-WoWCombatLog-050326_002249.txt") == dt.date(2026, 5, 3)


def test_filename_embedded_date_returns_none_for_unrecognized_names():
    assert filename_embedded_date("brutoh-vault.simc") is None
    assert filename_embedded_date("some_random_file.txt") is None
