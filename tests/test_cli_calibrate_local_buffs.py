"""The shipped `simf calibrate-k` LOCAL-LOG path must pass the spec's
`*_spell_id` constants to ``load_replay`` as ``buff_ability_ids``.

Mirrors ``tests/test_cli_calibrate_wcl_buffs.py`` for the non-`--wcl-url`
path. Window-gated mitigation layers (Keep Your Feet on the Ground, spell
438591) are credited off ``event.active_buffs``, stamped from the log's real
buff windows — before this wiring, the LOCAL replay path had no mechanism to
populate ``active_buffs`` at all, so the ratified Prot Warrior corpus would
have silently zeroed this layer even after `mitigation.py` learned to read it.
This is the same gap class the WCL `--wcl-url` fix closed for VDH's
Metamorphosis credit.
"""

from __future__ import annotations

import pathlib
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simf.io.log_replay import ReplayData

MGT_LOG = (
    pathlib.Path(__file__).resolve().parents[1] / "examples" / "WoWCombatLog-051526_210245.txt"
)


def _fake_replay() -> ReplayData:
    run = SimpleNamespace(map_name="Test MGT", key_level=12, success=True)
    return ReplayData(
        run=run,
        duration_s=60.0,
        events=[],
        actual_dealt=1_000_000,
        actual_blocked=0,
        actual_absorbed=0,
        actual_resisted=0,
        event_count=1,
    )


def test_calibrate_k_local_passes_spec_buff_ids(tmp_path):
    if not MGT_LOG.exists():
        pytest.skip(f"calibration corpus log not present at {MGT_LOG}")

    from typer.testing import CliRunner

    from simf.cli import app as cli_app

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    shutil.copy(MGT_LOG, logs_dir / MGT_LOG.name)

    captured: dict = {}

    def fake_load_replay(log_path, target_name, run_index=-1, *, runs=None, buff_ability_ids=None):
        captured["buff_ability_ids"] = buff_ability_ids
        return _fake_replay()

    runner = CliRunner()
    with patch("simf.io.log_replay.load_replay", fake_load_replay):
        result = runner.invoke(
            cli_app,
            [
                "calibrate-k",
                "--logs-dir",
                str(logs_dir),
                "--full-scan",
                "--k-min",
                "3430",
                "--k-max",
                "3430",
                "--k-step",
                "25",
                "--iterations",
                "1",
            ],
        )

    assert result.exit_code == 0, result.output
    got = captured.get("buff_ability_ids")
    assert got, "CLI passed no buff_ability_ids — window-gated layers silently zeroed"
    assert 438591 in got  # Keep Your Feet on the Ground
    assert 71 in got  # Vanguard's spell_id (harmless: not window-gated, just auto-collected)
