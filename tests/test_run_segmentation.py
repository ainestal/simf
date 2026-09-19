"""Tests for ENCOUNTER_START/END parsing and run segmentation.

Anchored to examples/WoWCombatLog-051526_210245.txt — Brutoh's Magisters' Terrace +12.
4 bosses, 5 encounter windows (Degentrius wiped + re-pulled), 3 deaths bucketing
as 1 boss / 2 trash.
"""

from pathlib import Path

import pytest

from simf.io.combat_log import (
    ChallengeModeRun,
    EncounterWindow,
    find_encounter_at,
    iter_death_events,
    parse_challenge_modes,
    parse_encounters,
    segment_run,
)

MGT_LOG = Path(__file__).resolve().parents[1] / "examples" / "WoWCombatLog-051526_210245.txt"


def _has_mgt_log() -> bool:
    return MGT_LOG.exists()


pytestmark = pytest.mark.skipif(not _has_mgt_log(), reason="MGT validation log not present")


# ── Unit-level: synthetic encounters / segments ──────────────────────────────


def _run(start=0.0, end=600.0):
    return ChallengeModeRun(
        map_id=1,
        map_name="Test",
        key_level=12,
        affixes=[],
        start_time_s=start,
        end_time_s=end,
        success=True,
        duration_ms=int((end - start) * 1000),
    )


def _enc(start, end, name="Boss", eid=1, success=True, attempt=1):
    return EncounterWindow(
        encounter_id=eid,
        name=name,
        start_time_s=start,
        end_time_s=end,
        success=success,
        attempt_index=attempt,
    )


def test_segment_run_empty_encounters_returns_single_trash_segment():
    run = _run(0.0, 600.0)
    segments = segment_run(run, [])
    assert len(segments) == 1
    assert segments[0].kind == "trash"
    assert segments[0].duration_s() == 600.0


def test_segment_run_interleaves_boss_and_trash():
    run = _run(0.0, 600.0)
    encs = [_enc(100, 200, "A"), _enc(400, 500, "B", eid=2)]
    segs = segment_run(run, encs)
    assert [s.kind for s in segs] == ["trash", "boss", "trash", "boss", "trash"]
    assert segs[0].duration_s() == 100.0
    assert segs[1].label == "A"
    assert segs[3].label == "B"


def test_segment_run_drops_zero_duration_trash_at_end():
    run = _run(0.0, 200.0)
    encs = [_enc(100, 200, "A")]  # ends exactly when run ends
    segs = segment_run(run, encs)
    assert [s.kind for s in segs] == ["trash", "boss"]


def test_segment_run_multi_pull_boss_labels():
    """Same boss pulled twice → labels indicate try/kill."""
    run = _run(0.0, 1000.0)
    encs = [
        _enc(100, 200, "Degentrius", eid=42, success=False, attempt=1),
        _enc(400, 500, "Degentrius", eid=42, success=True, attempt=2),
    ]
    segs = segment_run(run, encs)
    boss_labels = [s.label for s in segs if s.kind == "boss"]
    assert boss_labels == ["Degentrius (try 1)", "Degentrius (kill)"]


def test_find_encounter_at_returns_none_for_trash_time():
    encs = [_enc(100, 200), _enc(400, 500, eid=2)]
    assert find_encounter_at(300.0, encs) is None
    assert find_encounter_at(150.0, encs).name == "Boss"
    assert find_encounter_at(500.0, encs) is not None  # inclusive end boundary


# ── Integration: against the real MGT log ───────────────────────────────────


@pytest.fixture(scope="module")
def mgt_run():
    return parse_challenge_modes(MGT_LOG)[0]


def test_mgt_parse_finds_five_encounters(mgt_run):
    """4 unique bosses in MGT; Degentrius was wiped and re-pulled → 5 windows total."""
    encs = parse_encounters(MGT_LOG, mgt_run.start_time_s, mgt_run.end_time_s)
    assert len(encs) == 5
    names = {e.name for e in encs}
    assert names == {"Arcanotron Custos", "Seranel Sunlash", "Gemellus", "Degentrius"}


def test_mgt_degentrius_has_two_attempts(mgt_run):
    encs = parse_encounters(MGT_LOG, mgt_run.start_time_s, mgt_run.end_time_s)
    degens = [e for e in encs if e.name == "Degentrius"]
    assert len(degens) == 2
    # First attempt failed; second succeeded.
    assert degens[0].success is False
    assert degens[0].attempt_index == 1
    assert degens[1].success is True
    assert degens[1].attempt_index == 2


def test_mgt_segments_produce_alternating_boss_trash(mgt_run):
    encs = parse_encounters(MGT_LOG, mgt_run.start_time_s, mgt_run.end_time_s)
    segs = segment_run(mgt_run, encs)
    # We expect at least one trash before each boss + boss + trash after last.
    boss_segs = [s for s in segs if s.kind == "boss"]
    assert len(boss_segs) == 5
    # Boss labels should include the multi-attempt suffix for Degentrius only.
    degentrius_labels = [s.label for s in boss_segs if "Degentrius" in s.label]
    assert "Degentrius (try 1)" in degentrius_labels
    assert "Degentrius (kill)" in degentrius_labels
    # Bosses pulled once stay un-suffixed.
    assert any(s.label == "Arcanotron Custos" for s in boss_segs)


def test_mgt_brutoh_deaths_bucket_one_boss_two_trash(mgt_run):
    """Brutoh died 3 times: 1 trash + 1 Degentrius (try 1) + 1 trash."""
    encs = parse_encounters(MGT_LOG, mgt_run.start_time_s, mgt_run.end_time_s)
    deaths = list(
        iter_death_events(MGT_LOG, "Brutoh-Uldum-EU", mgt_run.start_time_s, mgt_run.end_time_s)
    )
    assert len(deaths) == 3
    buckets = [find_encounter_at(d.time_s, encs) for d in deaths]
    enc_count = sum(1 for b in buckets if b is not None)
    trash_count = sum(1 for b in buckets if b is None)
    assert enc_count == 1
    assert trash_count == 2
    boss_death_enc = next(b for b in buckets if b is not None)
    assert boss_death_enc.name == "Degentrius"
    assert boss_death_enc.success is False  # the wipe attempt, not the kill
