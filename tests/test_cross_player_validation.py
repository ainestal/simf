"""Tests for ``scripts/cross_player_validation.py`` — the cross-player
validation gate added alongside Prot Warrior's `calibrated` -> `characterized`
downgrade (2026-07-25; see
docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md).

The script lives outside ``src/`` by the same convention as
``scripts/measure_run_f.py`` / ``scripts/calibrate_spec_from_wcl.py`` — a
path-based loader, no install step (see ``tests/test_measure_run_f.py``).

Covers: manifest loading + ACL filtering, the gate math itself (PASS/FAIL
against the ratified thresholds, human sign-off 2026-07-25), and — the specific
regression this test file exists to guard — that ``buff_ability_ids`` is
computed from the character's own spec constants and threaded through to
``wcl_to_replay_data``, mirroring ``cli.py``'s ``--wcl-url`` branch. The
original script omitted this, silently zeroing KYFOTG (or any future spec's
window-gated credit) for every fight — confirmed empirically before the fix,
see this module's own history in
``docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md``.

``_run_k_sweep`` itself is mocked throughout (it's covered directly by
``test_cli_run_k_sweep_return_result.py``) — these tests only check that
this script builds its inputs correctly and reduces its output correctly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cross_player_validation.py"


def _load():
    spec = importlib.util.spec_from_file_location("cross_player_validation", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cross_player_validation"] = mod
    spec.loader.exec_module(mod)
    return mod


cpv = _load()


# ─── manifest loading / ACL filtering ─────────────────────────────────────────


def test_load_fights_from_manifest_filters_acl_false(tmp_path, capsys):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "fights": [
                    {"player": "A", "report_code": "code1", "fight_id": 1, "acl": True},
                    {"player": "B", "report_code": "code2", "fight_id": 2, "acl": False},
                    {"player": "C", "report_code": "code3", "fight_id": 3},
                ]
            }
        )
    )
    fights = cpv.load_fights_from_manifest(manifest)
    assert [f["player"] for f in fights] == ["A", "C"]
    assert "Skipping 1 fight(s) with acl: false" in capsys.readouterr().out


def test_load_fights_from_manifest_no_skip_message_when_all_usable(tmp_path, capsys):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"fights": [{"player": "A", "report_code": "code1", "fight_id": 1, "acl": True}]}
        )
    )
    cpv.load_fights_from_manifest(manifest)
    assert "Skipping" not in capsys.readouterr().out


# ─── gate math (print_gate_report) ────────────────────────────────────────────


def _stats(n, mean_bias, rmse, within_15pct):
    return {
        "n": n,
        "mean_bias": mean_bias,
        "rmse": rmse,
        "within_15pct": within_15pct,
        "deltas": [(f"player{i}", 1.0) for i in range(n)],
    }


def test_print_gate_report_zero_fights_fails_without_crashing(capsys):
    result = cpv.print_gate_report({"n": 0, "mean_bias": None, "rmse": None, "within_15pct": None})
    assert result is False
    assert "cannot evaluate the gate" in capsys.readouterr().out


def test_print_gate_report_all_pass():
    stats = _stats(n=6, mean_bias=3.0, rmse=0.1, within_15pct=0.80)
    assert cpv.print_gate_report(stats) is True


def test_print_gate_report_fails_on_bias(capsys):
    stats = _stats(n=6, mean_bias=11.0, rmse=0.15, within_15pct=0.80)
    assert cpv.print_gate_report(stats) is False
    out = capsys.readouterr().out
    assert "mean |bias|:      +11.0% (bar <=8%) FAIL" in out


def test_print_gate_report_fails_on_within_15pct(capsys):
    stats = _stats(n=6, mean_bias=3.0, rmse=0.1, within_15pct=0.50)
    assert cpv.print_gate_report(stats) is False
    out = capsys.readouterr().out
    assert "within +/-15%:    50% (bar >=70%) FAIL" in out


def test_print_gate_report_fails_on_n_below_min_players():
    stats = _stats(n=4, mean_bias=1.0, rmse=0.05, within_15pct=1.0)
    assert cpv.print_gate_report(stats) is False


def test_print_gate_report_known_downgrade_numbers_fail_the_ratified_gate():
    """Consistency check on the ratified thresholds themselves (human
    sign-off 2026-07-25; see the script's own module docstring): today's
    known-bad Prot Warrior aggregate (+11.0% bias, 67% within +/-15%) must
    still fail the gate, or the thresholds were chosen too loosely to catch
    the exact generalization gap they exist to catch."""
    stats = _stats(n=15, mean_bias=11.0, rmse=0.150, within_15pct=0.667)
    assert cpv.print_gate_report(stats) is False


# ─── run_cross_player_validation — wiring + aggregate reduction ──────────────


def _fake_wcl_char(class_spec: str = "protection_warrior"):
    return SimpleNamespace(
        char_data={
            "name": "TestChar",
            "race": "human",
            "class_spec": class_spec,
            "talents": "brutoh-actual",
            "strength": 2000,
            "stamina": 30000,
            "armor_from_gear": 5000,
        },
        source="wcl_combatant_info",
        summary="test summary",
    )


def _fake_replay(event_count: int = 3, dtps: float = 20_000.0, duration: float = 60.0):
    return SimpleNamespace(
        event_count=event_count,
        actual_dealt=dtps * duration,
        duration_s=duration,
        events=[],
        run=SimpleNamespace(map_name="Test Spire", key_level=18, success=True),
    )


def test_run_cross_player_validation_passes_real_spec_buff_ids_through(monkeypatch):
    """The regression this file exists to guard: buff_ability_ids must be
    computed from the character's own spec constants (protection_warrior's
    real `*_spell_id` entries in constants.yaml, e.g. Vanguard/KYFOTG) and
    threaded into wcl_to_replay_data — omitting it silently zeroes any
    window-gated mitigation credit for every fight in the corpus."""
    from simf.core.constants import load_constants

    expected_spec_cfg = load_constants().get("specs", {}).get("protection_warrior", {})
    expected_ids = {int(v) for k, v in expected_spec_cfg.items() if k.endswith("_spell_id") and v}
    assert expected_ids, "protection_warrior must carry at least one *_spell_id constant"

    captured_kwargs = {}

    def _fake_wcl_to_replay_data(code, fight_id, player, **kwargs):
        captured_kwargs.update(kwargs)
        return _fake_replay()

    monkeypatch.setattr(cpv, "character_from_wcl", lambda *a, **k: _fake_wcl_char())
    monkeypatch.setattr(cpv, "wcl_to_replay_data", _fake_wcl_to_replay_data)
    monkeypatch.setattr(
        cpv,
        "_run_k_sweep",
        lambda **k: {"best_k": 3430, "best_rmse": 0.1, "deltas": [5.0], "labels": ["p"]},
    )

    fights = [{"player": "AnonPlayerX4", "report_code": "abc123", "fight_id": 1}]
    cpv.run_cross_player_validation(
        fights, k=3430, iterations=1, seed=42, healing_profile="m+_high_key_healer", cache_dir=None
    )

    assert captured_kwargs.get("buff_ability_ids") == expected_ids


def test_run_cross_player_validation_reduces_aggregate_stats(monkeypatch):
    monkeypatch.setattr(cpv, "character_from_wcl", lambda *a, **k: _fake_wcl_char())
    monkeypatch.setattr(cpv, "wcl_to_replay_data", lambda *a, **k: _fake_replay())
    monkeypatch.setattr(
        cpv,
        "_run_k_sweep",
        lambda **k: {
            "best_k": 3430,
            "best_rmse": 0.1,
            "deltas": [10.0, -10.0, 20.0],
            "labels": ["p1", "p2", "p3"],
        },
    )

    fights = [{"player": f"p{i}", "report_code": f"code{i}", "fight_id": i} for i in range(1, 4)]
    stats = cpv.run_cross_player_validation(
        fights, k=3430, iterations=1, seed=42, healing_profile="m+_high_key_healer", cache_dir=None
    )

    assert stats["n"] == 3
    assert stats["mean_bias"] == pytest.approx((10.0 - 10.0 + 20.0) / 3)
    assert stats["within_15pct"] == pytest.approx(2 / 3)
    assert stats["deltas"] == [("p1", 10.0), ("p2", -10.0), ("p3", 20.0)]


def test_run_cross_player_validation_skips_fight_with_no_combatant_info(monkeypatch, capsys):
    monkeypatch.setattr(cpv, "character_from_wcl", lambda *a, **k: None)
    monkeypatch.setattr(cpv, "wcl_to_replay_data", lambda *a, **k: _fake_replay())

    fights = [{"player": "NoACL", "report_code": "abc", "fight_id": 1}]
    stats = cpv.run_cross_player_validation(
        fights, k=3430, iterations=1, seed=42, healing_profile="m+_high_key_healer", cache_dir=None
    )

    assert stats == {"n": 0, "mean_bias": None, "rmse": None, "within_15pct": None, "deltas": []}
    assert "SKIP NoACL" in capsys.readouterr().out


def test_run_cross_player_validation_skips_fight_with_zero_events(monkeypatch, capsys):
    monkeypatch.setattr(cpv, "character_from_wcl", lambda *a, **k: _fake_wcl_char())
    monkeypatch.setattr(cpv, "wcl_to_replay_data", lambda *a, **k: _fake_replay(event_count=0))

    fights = [{"player": "ZeroEvents", "report_code": "abc", "fight_id": 1}]
    stats = cpv.run_cross_player_validation(
        fights, k=3430, iterations=1, seed=42, healing_profile="m+_high_key_healer", cache_dir=None
    )

    assert stats["n"] == 0
    assert "SKIP ZeroEvents" in capsys.readouterr().out
