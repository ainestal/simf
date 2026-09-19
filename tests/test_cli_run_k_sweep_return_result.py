"""Tests for `_run_k_sweep`'s two purely-additive params (`return_result`,
`skip_loo_cv`) added alongside `scripts/cross_player_validation.py` (see
docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md) —
the cross-player validation gate reuses `_run_k_sweep` for its per-fight sim
math instead of re-deriving it, and needs a structured return value plus a
way to skip the same-corpus LOO-CV section (meaningless when each "replay"
is a different player, not a repeated run of one player).

`_run_k_sweep` itself (the sweep math, RMSE, F-layer, LOO-CV wiring) is
already covered by `test_cli_calibrate_k_f_layer.py` / `_loo_cv.py` via the
CLI. These tests call it directly to isolate just the two new params.
"""

from __future__ import annotations

from simf.cli import _run_k_sweep
from simf.core.events import DamageEvent
from simf.core.profiles import HealingProfile
from simf.io.log_replay import ReplayData


def _heal() -> HealingProfile:
    return HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0)


class _FakeRun:
    map_name = "Test Spire"
    key_level = 15
    success = True


def _fake_replay(dtps: float = 20_000.0, duration: float = 60.0) -> ReplayData:
    events = [
        DamageEvent(
            time_s=1.0,
            source_id="trash",
            raw_amount=dtps * duration,
            school="physical",
            is_avoidable=False,
            is_blockable=False,
            attack_type="melee",
            is_log_replay=True,
        )
    ]
    return ReplayData(
        run=_FakeRun(),
        duration_s=duration,
        events=events,
        actual_dealt=int(dtps * duration),
        actual_blocked=0,
        actual_absorbed=0,
        actual_resisted=0,
        event_count=len(events),
    )


def _replays_for(brutoh, labels_and_dtps: list[tuple[str, float]]):
    return [(_fake_replay(dtps=dtps), dtps, label, brutoh) for label, dtps in labels_and_dtps]


def test_return_result_false_by_default_returns_none(brutoh):
    replays = _replays_for(brutoh, [("run-a", 20_000.0)])
    result = _run_k_sweep(
        replays=replays,
        heal=_heal(),
        k_min=3430,
        k_max=3430,
        k_step=10,
        iterations=1,
        seed=42,
        skip_loo_cv=True,
    )
    assert result is None


def test_return_result_true_returns_expected_shape(brutoh):
    replays = _replays_for(brutoh, [("run-a", 20_000.0), ("run-b", 25_000.0)])
    result = _run_k_sweep(
        replays=replays,
        heal=_heal(),
        k_min=3430,
        k_max=3430,
        k_step=10,
        iterations=1,
        seed=42,
        skip_loo_cv=True,
        return_result=True,
    )
    assert result is not None
    assert set(result.keys()) == {"best_k", "best_rmse", "deltas", "labels"}
    assert result["best_k"] == 3430
    assert isinstance(result["best_rmse"], float)
    assert result["labels"] == ["run-a", "run-b"]
    assert len(result["deltas"]) == 2
    for d in result["deltas"]:
        assert isinstance(d, float)


def test_skip_loo_cv_true_omits_loo_cv_section(brutoh, capsys):
    """3 replays is >=3 folds — the LOO-CV section would normally run and
    print. skip_loo_cv=True (the param cross_player_validation.py always
    passes) must suppress it entirely."""
    replays = _replays_for(brutoh, [("run-a", 20_000.0), ("run-b", 21_000.0), ("run-c", 19_000.0)])
    _run_k_sweep(
        replays=replays,
        heal=_heal(),
        k_min=3430,
        k_max=3430,
        k_step=10,
        iterations=1,
        seed=42,
        skip_loo_cv=True,
    )
    out = capsys.readouterr().out
    assert "LOO-CV" not in out


def test_skip_loo_cv_false_runs_loo_cv_section_for_three_plus_replays(brutoh, capsys):
    """Sanity check the inverse — without skip_loo_cv, >=3 replays does print
    an LOO-CV section (existing behaviour, unchanged by this param)."""
    replays = _replays_for(brutoh, [("run-a", 20_000.0), ("run-b", 21_000.0), ("run-c", 19_000.0)])
    _run_k_sweep(
        replays=replays,
        heal=_heal(),
        k_min=3430,
        k_max=3430,
        k_step=10,
        iterations=1,
        seed=42,
    )
    out = capsys.readouterr().out
    assert "LOO-CV" in out
