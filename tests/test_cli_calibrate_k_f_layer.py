"""Tests for calibrate-k's F-layer diagnostic (the fast-follow named in
docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md — measure
each run's F, the base-to-applied damage multiplier from
scripts/measure_run_f.py, and report it alongside the sweep, never bake it
into constants.yaml).

Covers the ``F=n/a`` branch of ``measure_run_f``'s "no evidence" contract
(a log with the *minimal* 10-field suffix used elsewhere in this test suite,
no advanced info — same pattern as
``tests/test_cli_calibrate_corpus_manifest.py``'s synthetic logs), plus
loader wiring and the WCL-branch no-op.

NOTE: the measurable-F branch (an ACL-on log actually yielding a computed F)
previously had a dedicated test here plus a formula test in
``tests/test_measure_run_f.py``, both built on real captured lines from a
2026 pre-Season-2 log. Both were removed 2026-09-15 (real/stale fixture
data) without a synthetic replacement — ``measure_run_f``'s formula and this
CLI branch's wiring are currently untested. Worth a fresh synthetic-fixture
test if this code changes again.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from simf.cli import _load_measure_run_f
from simf.cli import app as cli_app

_TARGET = "Brutoh-Uldum-EU"


def _start(ts: str, name: str, map_id: int, key_level: int, affixes: str = "[]") -> str:
    return f'{ts}  CHALLENGE_MODE_START,"{name}",{map_id},{map_id},{key_level},{affixes}\n'


def _end(ts: str, map_id: int, success: int, key_level: int, duration_ms: int) -> str:
    return f"{ts}  CHALLENGE_MODE_END,{map_id},{success},{key_level},{duration_ms},1,{key_level},0,0,0\n"


def _minimal_hit(ts: str, target: str) -> str:
    # No advanced-logging block — mirrors test_cli_calibrate_corpus_manifest.py's
    # _hit helper exactly, deliberately carrying no per-hit armor field.
    return (
        f"{ts}  SPELL_DAMAGE,0x0,Boss,0x1,0x0,"
        f"Player-1234,{target},0x512,0x0,123,Fireball,0x4,"
        "1000,1000,0,0,0,0,0,0,0,0\n"
    )


def _minimal_character(tmp_path, name: str = "TestChar"):
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


def _run_calibrate_k(logs_dir, character, target: str, *extra_args):
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


def test_load_measure_run_f_resolves_in_this_checkout():
    """Sanity check the dynamic loader against the real scripts/ directory —
    the graceful-None branch (missing script) is exercised indirectly by the
    n/a test below, which never depends on the loader itself failing."""
    fn = _load_measure_run_f()
    assert fn is not None
    assert callable(fn)


def test_f_layer_reports_na_without_advanced_logging(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log = (
        _start("5/11/2026 12:00:00.000", "Pit of Saron", 658, 12)
        + _minimal_hit("5/11/2026 12:01:00.000", _TARGET)
        + _minimal_hit("5/11/2026 12:02:00.000", _TARGET)
        + _end("5/11/2026 12:05:00.000", 658, 1, 12, 300_000)
    )
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(log)
    char = _minimal_character(tmp_path)

    result = _run_calibrate_k(logs_dir, char, _TARGET, "--full-scan")

    assert result.exit_code == 0, result.output
    assert "F=n/a (no per-hit live-armor data" in result.output
    assert "=== F-layer diagnostic (canonical K=3430)" in result.output
    assert "F measurable on 0/1 runs" in result.output
    assert "No F-consistency or F-corrected residual possible — 0 runs measurable." in result.output


def test_wcl_branch_omits_f_layer_section(tmp_path):
    """The WCL branch never has per-hit live armor to measure — calibrate-k
    must not print the F-layer section (or crash) for it. Mirrors
    test_cli_calibrate_wcl_buffs.py's own mocking pattern exactly (same
    fixture shape), adding only the F-layer-specific assertion rather than
    re-testing WCL plumbing that file already covers."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from simf.core.events import DamageEvent
    from simf.io.log_replay import ReplayData

    char_yaml = _minimal_character(tmp_path)

    def _fake_replay() -> ReplayData:
        events = [
            DamageEvent(
                time_s=1.0,
                source_id="trash",
                raw_amount=50_000.0,
                school="physical",
                is_avoidable=False,
                is_blockable=False,
                attack_type="melee",
                is_log_replay=True,
            )
        ]
        run = SimpleNamespace(map_name="Test Spire", key_level=19, success=True)
        return ReplayData(
            run=run,
            duration_s=60.0,
            events=events,
            actual_dealt=1_000_000,
            actual_blocked=0,
            actual_absorbed=0,
            actual_resisted=0,
            event_count=len(events),
        )

    runner = CliRunner()
    with (
        patch("simf.io.wcl_combatant_info.character_from_wcl", return_value=None),
        patch("simf.io.wcl_replay.wcl_to_replay_data", lambda *a, **k: _fake_replay()),
    ):
        result = runner.invoke(
            cli_app,
            [
                "calibrate-k",
                "--character",
                str(char_yaml),
                "--wcl-url",
                "https://www.warcraftlogs.com/reports/testcode123abc#fight=1",
                "--wcl-target",
                _TARGET,
                "--k-min",
                "3430",
                "--k-max",
                "3430",
                "--k-step",
                "10",
                "--iterations",
                "1",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "=== F-layer diagnostic" not in result.output
