"""The shipped `simf calibrate-k --wcl-url` path must pass the spec's
`*_spell_id` constants to ``wcl_to_replay_data`` as ``buff_ability_ids``.

Window-gated mitigation layers (VDH Metamorphosis ×3 armor since #143,
Painbringer since v38) are credited off ``event.active_buffs``, stamped from
the log's real buff windows. ``scripts/calibrate_spec_from_wcl.py`` collects
those ids; before this pin the shipped CLI did NOT — so the same fight run
through `simf calibrate-k --wcl-url` silently zeroed those layers and read a
different K than the characterization tooling. This is the gap class the
Painbringer wiring closed alongside itself.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import yaml

from simf.core.events import DamageEvent
from simf.io.log_replay import ReplayData

VDH_CHAR = {
    "name": "TestVDH",
    "race": "night_elf",
    "class_spec": "vengeance_demon_hunter",
    "talents": "brutoh-actual",
    "strength": 2000,
    "agility": 2000,
    "stamina": 50000,
    "armor_from_gear": 4000,
}


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


def test_calibrate_k_wcl_url_passes_spec_buff_ids(tmp_path):
    from typer.testing import CliRunner

    from simf.cli import app as cli_app

    char_yaml = tmp_path / "vdh.yaml"
    char_yaml.write_text(yaml.safe_dump(VDH_CHAR))

    captured: dict = {}

    def fake_wcl_to_replay_data(
        code, fight_id, target, *, target_actor_id=None, buff_ability_ids=None, cache_dir=None
    ):
        captured["buff_ability_ids"] = buff_ability_ids
        return _fake_replay()

    runner = CliRunner()
    with (
        # ACL-off path: CLI falls back to the --character YAML (our VDH).
        patch("simf.io.wcl_combatant_info.character_from_wcl", return_value=None),
        patch("simf.io.wcl_replay.wcl_to_replay_data", fake_wcl_to_replay_data),
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
                "TestVDH",
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
    # The VDH window-gated layers: Metamorphosis + Demon Spikes + Painbringer.
    assert {187827, 203819, 212988} <= set(got)
