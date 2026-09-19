"""Regression tests for CHALLENGE_MODE_START/END pairing in combat_log_runs.py.

Field-tested 2026-06-29 on a real AnonGuardian1 log (memory: coaching_feature_fieldtest):
a Pit of Saron +17 run that never got its own logged END was mislabeled with
Algeth'ar Academy's later abandon-END's success/duration, because
`parse_challenge_modes` closed whatever was `pending` using the next
CHALLENGE_MODE_END it saw, with no check that the END actually belonged to
that run. A second, related bug: a new CHALLENGE_MODE_START while a run was
still `pending` silently discarded it with no trace at all.

These tests pin the fix: an END only closes `pending` when its own leading
field matches `pending.map_id`; a new START first finalizes whatever was
still open (as an honest `success=None` "incomplete" run) instead of
dropping it.
"""

from __future__ import annotations

from simf.io.combat_log import parse_challenge_modes


def _start(ts: str, name: str, map_id: int, key_level: int, affixes: str = "[]") -> str:
    return f'{ts}  CHALLENGE_MODE_START,"{name}",{map_id},{map_id},{key_level},{affixes}\n'


def _end(ts: str, map_id: int, success: int, key_level: int, duration_ms: int) -> str:
    return f"{ts}  CHALLENGE_MODE_END,{map_id},{success},{key_level},{duration_ms},1,{key_level},0,0,0\n"


def test_end_with_wrong_map_id_is_ignored_not_misapplied(tmp_path):
    """A stray END for a different map must not stamp its data onto `pending`."""
    log = tmp_path / "test.log"
    log.write_text(
        _start("6/29/2026 13:44:26.295", "Pit of Saron", 658, 17, "[9,10,147]")
        # Stray END for an unrelated run (2526) — must be ignored, not applied to 658.
        + _end("6/29/2026 14:00:00.000", 2526, 0, 16, 0)
        # The real END for 658, arriving later.
        + _end("6/29/2026 14:30:00.000", 658, 1, 17, 1_800_000)
    )

    runs = parse_challenge_modes(log)

    assert len(runs) == 1
    run = runs[0]
    assert run.map_id == 658
    assert run.map_name == "Pit of Saron"
    assert run.success is True
    assert run.duration_ms == 1_800_000  # from 658's own END, not 2526's


def test_new_start_supersedes_open_pending_as_incomplete(tmp_path):
    """A new START while a run is still open finalizes the old one honestly
    (`success=None`, boundary = the new run's start time) instead of
    silently dropping it."""
    log = tmp_path / "test.log"
    log.write_text(
        _start("6/29/2026 13:44:26.295", "Pit of Saron", 658, 17)
        # No END ever logged for 658 — the group leaves without one.
        + _start("6/29/2026 14:08:02.453", "Algeth'ar Academy", 2526, 16, "[9]")
        + _end("6/29/2026 14:30:00.000", 2526, 1, 16, 1_800_000)
    )

    runs = parse_challenge_modes(log)

    assert len(runs) == 2
    pos, academy = runs
    assert pos.map_id == 658
    assert pos.success is None
    assert pos.duration_ms is None
    assert pos.end_time_s == academy.start_time_s  # closed by the superseding START

    assert academy.map_id == 2526
    assert academy.success is True
    assert academy.duration_ms == 1_800_000


def test_anonguardian1_style_scenario_matches_field_reported_bug(tmp_path):
    """End-to-end regression for the exact field-test scenario: a PoS run with
    no logged END, followed by a properly-timed Academy run. Before the fix,
    PoS came out mislabeled "depleted" with Academy's 1,800,000ms duration;
    after the fix it's honestly INCOMPLETE and Academy is untouched."""
    log = tmp_path / "test.log"
    log.write_text(
        _start("6/29/2026 13:44:26.295", "Pit of Saron", 658, 17, "[9,10,147]")
        + _start("6/29/2026 14:08:02.453", "Algeth'ar Academy", 2526, 16, "[9]")
        + _end("6/29/2026 14:30:00.000", 2526, 1, 16, 1_800_000)
    )

    runs = parse_challenge_modes(log)

    assert [r.map_name for r in runs] == ["Pit of Saron", "Algeth'ar Academy"]
    pos, academy = runs
    # PoS must NOT inherit Academy's data.
    assert pos.success is None
    assert pos.duration_ms is None
    assert pos.key_level == 17
    assert pos.affixes == [9, 10, 147]
    # Academy is correct and untouched by the fix.
    assert academy.success is True
    assert academy.duration_ms == 1_800_000
    assert academy.key_level == 16
