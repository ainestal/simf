"""CLI-level tests for calibrate-k's manifest-first corpus selection.

Guards the fix for the 2026-07-12 RMSE-drift incident (see
tests/test_calibration_corpus.py's module docstring for the full incident
summary). These tests use small synthetic combat logs (tmp_path-based, same
pattern as test_combat_log_runs.py) rather than the real examples/ corpus —
those files are gitignored and hundreds of MB each, unavailable in CI.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from typer.testing import CliRunner

import simf.io.calibration_corpus as calibration_corpus_mod
from simf.cli import app as cli_app

REPO_ROOT = Path(__file__).resolve().parent.parent


def _start(ts: str, name: str, map_id: int, key_level: int, affixes: str = "[]") -> str:
    return f'{ts}  CHALLENGE_MODE_START,"{name}",{map_id},{map_id},{key_level},{affixes}\n'


def _end(ts: str, map_id: int, success: int, key_level: int, duration_ms: int) -> str:
    return f"{ts}  CHALLENGE_MODE_END,{map_id},{success},{key_level},{duration_ms},1,{key_level},0,0,0\n"


def _hit(ts: str, target: str) -> str:
    # Suffix is exactly 10 fields (no ST/AOE marker): amount, base_amount,
    # overkill, school(unused, already read from the hex field above),
    # resisted, blocked, absorbed, is_critical, is_glancing, padding.
    return (
        f"{ts}  SPELL_DAMAGE,0x0,Boss,0x1,0x0,"
        f"Player-1234,{target},0x512,0x0,123,Fireball,0x4,"
        "1000,1000,0,0,0,0,0,0,0,0\n"
    )


def _synthetic_log(target: str = "Test-Realm-EU", map_name: str = "Pit of Saron") -> str:
    return (
        _start("5/11/2026 12:00:00.000", map_name, 658, 12)
        + _hit("5/11/2026 12:01:00.000", target)
        + _hit("5/11/2026 12:02:00.000", target)
        + _hit("5/11/2026 12:03:00.000", target)
        + _end("5/11/2026 12:05:00.000", 658, 1, 12, 300_000)
    )


def _minimal_character(tmp_path: Path, name: str = "TestChar") -> Path:
    char_yaml = tmp_path / f"{name}.yaml"
    char_yaml.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "race": "human",
                "class_spec": "protection_warrior",
                "talents": "brutoh-actual",
                "strength": 2000,
                "stamina": 30000,
                "armor_from_gear": 5000,
            }
        )
    )
    return char_yaml


def _run_calibrate_k(logs_dir: Path, character: Path, target: str, *extra_args) -> object:
    runner = CliRunner()
    args = [
        "calibrate-k",
        "--character",
        str(character),
        "--log-target",
        target,
        "--logs-dir",
        str(logs_dir),
        "--k-min",
        "3430",
        "--k-max",
        "3430",
        "--k-step",
        "10",
        "--iterations",
        "1",
        *extra_args,
    ]
    return runner.invoke(cli_app, args)


def test_no_manifest_falls_back_to_full_scan_and_says_so(tmp_path):
    """A character with no ratified manifest must behave exactly like the
    old default (recursive scan), just with a visible notice explaining why."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(_synthetic_log())
    char = _minimal_character(tmp_path)

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU")

    assert result.exit_code == 0, result.output
    assert "No ratified manifest found" in result.output
    assert "against 1 log(s)" in result.output


def test_full_scan_flag_bypasses_a_matching_manifest(tmp_path, monkeypatch):
    """--full-scan is an explicit opt-out even when a manifest exists for
    this exact --character — it must never be silently ignored."""
    corpora_dir = tmp_path / "corpora"
    corpora_dir.mkdir()
    char = _minimal_character(tmp_path)
    (corpora_dir / "test_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "protection_warrior",
                "character": str(char),
                "log_target": "Test-Realm-EU",
                "replays": [{"file": "does-not-exist.txt", "run_index": 0}],
            }
        )
    )
    monkeypatch.setattr(calibration_corpus_mod, "CORPUS_DIR", corpora_dir)

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(_synthetic_log())

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU", "--full-scan")

    assert result.exit_code == 0, result.output
    assert "Using ratified corpus manifest" not in result.output
    assert "against 1 log(s)" in result.output


def test_matching_manifest_is_used_by_default_over_a_directory_scan(tmp_path, monkeypatch):
    corpora_dir = tmp_path / "corpora"
    corpora_dir.mkdir()
    char = _minimal_character(tmp_path)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(_synthetic_log())
    # A second file that would ALSO qualify under a blind scan — must be
    # ignored because the manifest doesn't list it.
    (logs_dir / "WoWCombatLog-060126_120000.txt").write_text(
        _synthetic_log(map_name="Windrunner Spire")
    )
    (corpora_dir / "test_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "protection_warrior",
                "character": str(char),
                "log_target": "Test-Realm-EU",
                "replays": [{"file": "WoWCombatLog-051126_120000.txt", "run_index": 0}],
            }
        )
    )
    monkeypatch.setattr(calibration_corpus_mod, "CORPUS_DIR", corpora_dir)

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU")

    assert result.exit_code == 0, result.output
    assert "Using ratified corpus manifest" in result.output
    assert "against 1 log(s)" in result.output
    assert "Windrunner Spire" not in result.output


def test_manifest_entry_missing_from_logs_dir_is_skipped_not_a_crash(tmp_path, monkeypatch):
    corpora_dir = tmp_path / "corpora"
    corpora_dir.mkdir()
    char = _minimal_character(tmp_path)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (corpora_dir / "test_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "protection_warrior",
                "character": str(char),
                "log_target": "Test-Realm-EU",
                "replays": [{"file": "WoWCombatLog-ghost.txt", "run_index": 0}],
            }
        )
    )
    monkeypatch.setattr(calibration_corpus_mod, "CORPUS_DIR", corpora_dir)

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU")

    assert "manifest entry missing" in result.output
    assert result.exit_code != 0  # no usable logs found
    assert "No usable logs found" in result.output


def test_full_scan_dedups_byte_identical_files_across_subdirectories(tmp_path):
    logs_dir = tmp_path / "logs"
    (logs_dir / "nested").mkdir(parents=True)
    content = _synthetic_log()
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(content)
    (logs_dir / "nested" / "WoWCombatLog-051126_120000.txt").write_text(content)
    char = _minimal_character(tmp_path)

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU", "--full-scan")

    assert result.exit_code == 0, result.output
    assert "duplicate content of" in result.output
    assert "against 1 log(s)" in result.output


def test_full_scan_flags_replays_from_a_subdirectory(tmp_path):
    logs_dir = tmp_path / "logs"
    (logs_dir / "someone-elses-corpus").mkdir(parents=True)
    (logs_dir / "someone-elses-corpus" / "WoWCombatLog-051126_120000.txt").write_text(
        _synthetic_log()
    )
    char = _minimal_character(tmp_path)

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU", "--full-scan")

    assert result.exit_code == 0, result.output
    assert "someone-elses-corpus/" in result.output
    assert "verify this isn't another character's dedicated calibration corpus" in result.output


def test_full_scan_warns_when_log_date_is_far_from_character_snapshot(tmp_path, monkeypatch):
    corpora_dir = tmp_path / "corpora"
    corpora_dir.mkdir()
    char = _minimal_character(tmp_path)
    (corpora_dir / "test_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "protection_warrior",
                "character": str(char),
                "character_snapshot_date": dt.date(2026, 5, 6),
                "log_target": "Test-Realm-EU",
                "replays": [{"file": "unused.txt", "run_index": 0}],
            }
        )
    )
    monkeypatch.setattr(calibration_corpus_mod, "CORPUS_DIR", corpora_dir)

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    # July log — 2 months from the May 6 snapshot.
    (logs_dir / "WoWCombatLog-070326_120000.txt").write_text(_synthetic_log())

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU", "--full-scan")

    assert result.exit_code == 0, result.output
    assert "days from the calibration character's gear snapshot" in result.output
    assert "gear drift, not model error" in result.output


def test_full_scan_no_date_warning_when_log_is_close_to_snapshot(tmp_path, monkeypatch):
    corpora_dir = tmp_path / "corpora"
    corpora_dir.mkdir()
    char = _minimal_character(tmp_path)
    (corpora_dir / "test_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "protection_warrior",
                "character": str(char),
                "character_snapshot_date": dt.date(2026, 5, 6),
                "log_target": "Test-Realm-EU",
                "replays": [{"file": "unused.txt", "run_index": 0}],
            }
        )
    )
    monkeypatch.setattr(calibration_corpus_mod, "CORPUS_DIR", corpora_dir)

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(_synthetic_log())

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU", "--full-scan")

    assert result.exit_code == 0, result.output
    assert "gear snapshot" not in result.output


def test_partial_run_visibility_line_appears_only_when_a_partial_exists(tmp_path):
    """A run with no CHALLENGE_MODE_END (success=None) must be tagged
    [partial] and surfaced in a secondary RMSE line, per the 2026-07-12
    investigation's recommendation (visibility, not a silent numeric
    coverage-floor guess)."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    partial_log = (
        _start("5/11/2026 12:00:00.000", "Pit of Saron", 658, 12)
        + _hit("5/11/2026 12:01:00.000", "Test-Realm-EU")
        + _hit("5/11/2026 12:02:00.000", "Test-Realm-EU")
        # No CHALLENGE_MODE_END — truncated log, success stays None.
    )
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(partial_log)
    char = _minimal_character(tmp_path)

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU", "--full-scan")

    assert result.exit_code == 0, result.output
    assert "[partial]" in result.output
    assert "replays are partial/truncated runs" in result.output


def test_manifest_path_still_excludes_a_failed_run(tmp_path, monkeypatch):
    """A manifest is hand-curated, but a manifest entry pointing at a real
    death-downtime/failed run (success=False) must still be excluded, not
    silently folded into the RMSE — code-review finding: the original
    implementation only applied this filter on the --full-scan path."""
    corpora_dir = tmp_path / "corpora"
    corpora_dir.mkdir()
    char = _minimal_character(tmp_path)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    failed_log = (
        _start("5/11/2026 12:00:00.000", "Pit of Saron", 658, 12)
        + _hit("5/11/2026 12:01:00.000", "Test-Realm-EU")
        + _end("5/11/2026 12:05:00.000", 658, 0, 12, 0)  # success=0, duration=0 -> abandoned
    )
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(failed_log)
    (corpora_dir / "test_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "protection_warrior",
                "character": str(char),
                "log_target": "Test-Realm-EU",
                "replays": [{"file": "WoWCombatLog-051126_120000.txt", "run_index": 0}],
            }
        )
    )
    monkeypatch.setattr(calibration_corpus_mod, "CORPUS_DIR", corpora_dir)

    result = _run_calibrate_k(logs_dir, char, "Test-Realm-EU")

    assert "failed run (death downtime)" in result.output
    assert "No usable logs found" in result.output
    assert result.exit_code != 0


def test_malformed_manifest_does_not_break_lookup_for_an_unrelated_character(tmp_path, monkeypatch):
    """One broken manifest in data/calibration_corpora/ (bad YAML, or missing
    a required key) must not crash calibration for a character it has
    nothing to do with — code-review finding: find_manifest_for_character
    scans and parses every manifest to find a match, unconditionally."""
    from simf.io.calibration_corpus import find_manifest_for_character

    corpora_dir = tmp_path / "corpora"
    corpora_dir.mkdir()
    (corpora_dir / "broken.yaml").write_text("spec: protection_warrior\ncharacter: [unterminated")
    (corpora_dir / "incomplete.yaml").write_text(yaml.safe_dump({"spec": "guardian_druid"}))
    monkeypatch.setattr(calibration_corpus_mod, "CORPUS_DIR", corpora_dir)

    assert find_manifest_for_character("/some/unrelated/character.yaml") is None
