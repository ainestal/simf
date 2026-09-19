"""Tests for calibrate-k's leave-one-out cross-validation (LOO-CV) gate —
the fourth `calibrated`-promotion criterion named in ``docs/calibration.md``
and ``core/constants.py``'s promotion bar, previously only runnable via the
separate ``scripts/calibrate_spec_from_logs.py`` tool (whose own directory
scan finds a different, smaller corpus than the ratified manifest — see
``docs/validation/phase4_tiered_calibration_loo_cv_2026_07_06.md``'s "corpus
definitions" note). This wires the SAME ``_run_loo_cv`` implementation
(reused via ``_load_run_loo_cv``, mirroring ``_load_measure_run_f``) into
``calibrate-k`` itself, so it runs against whatever corpus (ratified
manifest or full-scan) that command already resolved — no second,
possibly-inconsistent corpus definition.

The gate's own math (stability anchoring, held-out prediction, hard-fail)
is already unit-tested against real data in
``tests/test_calibrate_spec_loo_cv.py`` — these tests only cover the wiring:
does ``calibrate-k`` load the gate, build its input table correctly from the
sweep it already ran, and print the result (including the graceful
"skipped, need >=3 folds" branch for small/WCL corpora).
"""

from __future__ import annotations

from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from simf.cli import _load_run_loo_cv
from simf.cli import app as cli_app

_TARGET = "Test-Realm-EU"


def _start(ts: str, name: str, map_id: int, key_level: int, affixes: str = "[]") -> str:
    return f'{ts}  CHALLENGE_MODE_START,"{name}",{map_id},{map_id},{key_level},{affixes}\n'


def _end(ts: str, map_id: int, success: int, key_level: int, duration_ms: int) -> str:
    return f"{ts}  CHALLENGE_MODE_END,{map_id},{success},{key_level},{duration_ms},1,{key_level},0,0,0\n"


def _hit(ts: str, target: str) -> str:
    # 10-field minimal suffix, no advanced-logging block — same shape as
    # test_cli_calibrate_corpus_manifest.py's own ``_hit`` helper.
    return (
        f"{ts}  SPELL_DAMAGE,0x0,Boss,0x1,0x0,"
        f"Player-1234,{target},0x512,0x0,123,Fireball,0x4,"
        "1000,1000,0,0,0,0,0,0,0,0\n"
    )


def _synthetic_log(start_ts: str, end_ts: str, map_name: str = "Pit of Saron") -> str:
    return (
        _start(start_ts, map_name, 658, 12)
        + _hit(start_ts, _TARGET)
        + _hit(start_ts, _TARGET)
        + _hit(start_ts, _TARGET)
        + _end(end_ts, 658, 1, 12, 300_000)
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


def _run_calibrate_k(logs_dir, character, *extra_args):
    runner = CliRunner()
    args = [
        "calibrate-k",
        "--character",
        str(character),
        "--log-target",
        _TARGET,
        "--logs-dir",
        str(logs_dir),
        "--iterations",
        "1",
        *extra_args,
    ]
    return runner.invoke(cli_app, args)


def test_load_run_loo_cv_resolves_in_this_checkout():
    """Sanity check the dynamic loader against the real scripts/ directory —
    mirrors test_load_measure_run_f_resolves_in_this_checkout in the sibling
    F-layer test file."""
    fn = _load_run_loo_cv()
    assert fn is not None
    assert callable(fn)


def test_loo_cv_gate_runs_against_a_three_run_corpus(tmp_path):
    """3 synthetic runs across 3 files is exactly the >=3-fold floor —
    the gate must actually run (not skip), and its verdict line must print.
    Also exercises the canonical-K union: --k-min/--k-max/--k-step here
    (2000/3000/500) never lands on 3430, but the F-layer/LOO-CV table must
    still carry a K=3430 row because _run_k_sweep unions it in."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(
        _synthetic_log("5/11/2026 12:00:00.000", "5/11/2026 12:05:00.000")
    )
    (logs_dir / "WoWCombatLog-051226_120000.txt").write_text(
        _synthetic_log("5/12/2026 12:00:00.000", "5/12/2026 12:05:00.000")
    )
    (logs_dir / "WoWCombatLog-051326_120000.txt").write_text(
        _synthetic_log("5/13/2026 12:00:00.000", "5/13/2026 12:05:00.000")
    )
    char = _minimal_character(tmp_path)

    result = _run_calibrate_k(
        logs_dir, char, "--full-scan", "--k-min", "2000", "--k-max", "3000", "--k-step", "500"
    )

    assert result.exit_code == 0, result.output
    assert "=== LOO-CV gate (3 folds)" in result.output
    assert "LOO-CV GATE: PASS" in result.output or "LOO-CV GATE: FAIL" in result.output
    assert "held out " in result.output
    assert "K stability:" in result.output
    assert "Held-out prediction:" in result.output
    # canonical K unioned into the grid despite the requested range stopping at 3000
    assert "K=  3430:" in result.output


def test_loo_cv_gate_skips_with_fewer_than_three_runs(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "WoWCombatLog-051126_120000.txt").write_text(
        _synthetic_log("5/11/2026 12:00:00.000", "5/11/2026 12:05:00.000")
    )
    char = _minimal_character(tmp_path)

    result = _run_calibrate_k(logs_dir, char, "--full-scan")

    assert result.exit_code == 0, result.output
    assert "LOO-CV: skipped (1 run(s) — need >=3 for a meaningful fold)" in result.output
    assert "LOO-CV GATE:" not in result.output


def test_wcl_branch_reports_loo_cv_skip_for_single_fight(tmp_path):
    """A single WCL fight is exactly the n=1 case the gate's own skip branch
    exists for — must print the graceful skip, never crash on a 1-entry
    table. Mirrors test_wcl_branch_omits_f_layer_section's fixture shape."""
    from types import SimpleNamespace

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
    assert "LOO-CV: skipped (1 run(s) — need >=3 for a meaningful fold)" in result.output
