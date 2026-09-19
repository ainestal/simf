"""Tests for Phase 3: death reconstruction, mitigation audit, danger ranking."""

from pathlib import Path
from unittest.mock import patch

import pytest

from simf.io.combat_log import (
    ChallengeModeRun,
    DamageTakenEvent,
    DeathRecord,
    iter_death_events,
    summarize_run,
)
from simf.io.death_analysis import reconstruct_deaths
from simf.io.mitigation_audit import AbilityMitigationStats, audit_replay

# ── Helpers ────────────────────────────────────────────────────────────────────


def _evt(time_s, spell, amount, base=None, blocked=0, absorbed=0, resisted=0, crit=False):
    return DamageTakenEvent(
        time_s=time_s,
        event_type="SPELL_DAMAGE",
        source_name="Boss",
        spell_name=spell,
        school="physical",
        amount=amount,
        base_amount=base if base is not None else amount,
        overkill=0,
        blocked=blocked,
        absorbed=absorbed,
        resisted=resisted,
        is_critical=crit,
        is_glancing=False,
    )


def _run(start=0.0, end=60.0):
    return ChallengeModeRun(
        map_id=1,
        map_name="Test",
        key_level=10,
        affixes=[],
        start_time_s=start,
        end_time_s=end,
        success=True,
        duration_ms=int((end - start) * 1000),
    )


# ── DeathRecord / iter_death_events ────────────────────────────────────────────

FAKE_LOG_UNIT_DIED = """\
5/11/2026 12:00:00.000  UNIT_DIED,0x0000000000000001,Boss,0x1,0x0,Player-1234-ABCD1234,Brutoh-Uldum-EU,0x512,0x0,0
"""

FAKE_LOG_NO_DEATH = """\
5/11/2026 12:00:00.000  SPELL_DAMAGE,0x0,Boss,0x1,0x0,Player-1234,Brutoh-Uldum-EU,0x512,0x0,123,Fireball,0x4,0,0,1000,1000,0,0,100,0,0,nil,nil
"""


def test_iter_death_events_finds_death(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(FAKE_LOG_UNIT_DIED)
    _run(start=0.0, end=3600.0)
    # The absolute timestamp is 2026-05-11 12:00:00 UTC — compute roughly
    records = list(iter_death_events(log, "Brutoh-Uldum-EU"))
    assert len(records) == 1
    assert isinstance(records[0], DeathRecord)


def test_iter_death_events_wrong_target(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(FAKE_LOG_UNIT_DIED)
    records = list(iter_death_events(log, "SomeOtherChar"))
    assert records == []


def test_iter_death_events_no_deaths(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(FAKE_LOG_NO_DEATH)
    records = list(iter_death_events(log, "Brutoh-Uldum-EU"))
    assert records == []


# ── reconstruct_deaths ─────────────────────────────────────────────────────────


def test_reconstruct_deaths_window_selection():
    """DeathEvent.preceding should contain only events in the 5s before death."""
    base_ts = 1000.0
    death_ts = base_ts + 30.0
    run = _run(start=base_ts, end=base_ts + 60.0)

    evts = [
        _evt(base_ts + 10.0, "Fireball", 5000),  # 20s before death — outside
        _evt(base_ts + 25.0, "Cleave", 8000),  # 5s before death — on boundary
        _evt(base_ts + 27.0, "Smash", 12000),  # 3s before — inside
        _evt(base_ts + 29.5, "Auto", 4000, crit=True),  # 0.5s before — inside
    ]
    deaths = [DeathRecord(time_s=death_ts, rel_time_s=30.0)]

    with (
        patch("simf.io.death_analysis.iter_damage_events", return_value=iter(evts)),
        patch("simf.io.death_analysis.iter_death_events", return_value=iter(deaths)),
    ):
        result = reconstruct_deaths(Path("fake.log"), "Brutoh-Uldum-EU", run)

    assert len(result) == 1
    de = result[0]
    assert de.num_hits == 3  # 25, 27, 29.5 — all within 5s; 10.0 is exactly 20s before
    assert de.max_hit == 12000
    assert de.total_damage_window == 8000 + 12000 + 4000


def test_reconstruct_deaths_no_deaths():
    run = _run()
    with (
        patch("simf.io.death_analysis.iter_damage_events", return_value=iter([])),
        patch("simf.io.death_analysis.iter_death_events", return_value=iter([])),
    ):
        result = reconstruct_deaths(Path("fake.log"), "Brutoh-Uldum-EU", run)
    assert result == []


def test_reconstruct_deaths_empty_window():
    base_ts = 1000.0
    death_ts = base_ts + 5.0
    run = _run(start=base_ts, end=base_ts + 60.0)
    deaths = [DeathRecord(time_s=death_ts, rel_time_s=5.0)]
    # All damage events before the window
    evts = [_evt(base_ts, "Auto", 1000)]

    with (
        patch("simf.io.death_analysis.iter_damage_events", return_value=iter(evts)),
        patch("simf.io.death_analysis.iter_death_events", return_value=iter(deaths)),
    ):
        result = reconstruct_deaths(Path("fake.log"), "Brutoh-Uldum-EU", run)

    assert len(result) == 1
    assert result[0].num_hits == 1  # base_ts = death_ts - 5s is exactly on the boundary


# ── AbilityMitigationStats ─────────────────────────────────────────────────────


def test_ability_mitigation_stats_properties():
    s = AbilityMitigationStats(
        ability="Fireball",
        hits=4,
        total_base=40000,
        total_to_hp=28000,
        total_blocked=4000,
        total_absorbed=8000,
        total_resisted=0,
    )
    assert s.avg_base == pytest.approx(10000.0)
    assert s.avg_to_hp == pytest.approx(7000.0)
    assert s.blocked_pct == pytest.approx(0.1)
    assert s.absorbed_pct == pytest.approx(0.2)
    assert s.resisted_pct == pytest.approx(0.0)
    assert s.mitigated_pct == pytest.approx(0.3)
    assert s.unmitigated_pct == pytest.approx(0.7)


def test_ability_mitigation_stats_zero_base():
    s = AbilityMitigationStats(
        ability="Ghost",
        hits=0,
        total_base=0,
        total_to_hp=0,
        total_blocked=0,
        total_absorbed=0,
        total_resisted=0,
    )
    assert s.avg_base == 0.0
    assert s.unmitigated_pct == 1.0


# ── audit_replay ──────────────────────────────────────────────────────────────


def test_audit_replay_aggregates_correctly():
    run = _run()
    evts = [
        _evt(10.0, "Fireball", 7000, base=10000, absorbed=3000),
        _evt(20.0, "Fireball", 8000, base=10000, absorbed=2000),
        _evt(30.0, "Cleave", 5000, base=6000, blocked=1000),
    ]
    with patch("simf.io.mitigation_audit.iter_damage_events", return_value=iter(evts)):
        result = audit_replay(Path("fake.log"), "Brutoh-Uldum-EU", run)

    assert len(result) == 2
    # Fireball should be first (highest total_base = 20000)
    fb = result[0]
    assert fb.ability == "Fireball"
    assert fb.hits == 2
    assert fb.total_base == 20000
    assert fb.total_to_hp == 15000
    assert fb.total_absorbed == 5000
    assert fb.absorbed_pct == pytest.approx(0.25)

    cl = result[1]
    assert cl.ability == "Cleave"
    assert cl.blocked_pct == pytest.approx(1000 / 6000)


def test_audit_replay_sorted_by_total_base():
    run = _run()
    evts = [
        _evt(5.0, "Auto", 1000, base=2000),
        _evt(10.0, "Smash", 9000, base=15000),
        _evt(15.0, "Fireball", 5000, base=8000),
    ]
    with patch("simf.io.mitigation_audit.iter_damage_events", return_value=iter(evts)):
        result = audit_replay(Path("fake.log"), "T", run)
    assert [s.ability for s in result] == ["Smash", "Fireball", "Auto"]


# ── LogSummary ability_max_hit / ability_spike_score ──────────────────────────


def _make_summary_from_events(evts, run):
    """Build a LogSummary by calling summarize_run with mocked iter_damage_events."""
    with (
        patch("simf.io.combat_log_summary.iter_damage_events", return_value=iter(evts)),
        patch("simf.io.combat_log_summary.iter_death_events", return_value=iter([])),
    ):
        return summarize_run(Path("fake.log"), "T", run)


def test_summary_ability_max_hit():
    run = _run()
    evts = [
        _evt(5.0, "Fireball", 3000),
        _evt(10.0, "Fireball", 8000),
        _evt(15.0, "Fireball", 5000),
    ]
    summary = _make_summary_from_events(evts, run)
    assert summary.ability_max_hit["Fireball"] == 8000


def test_summary_spike_score_formula():
    run = _run()
    evts = [
        _evt(5.0, "Fireball", 2000),
        _evt(10.0, "Fireball", 2000),
        _evt(15.0, "Fireball", 8000),  # spike
    ]
    summary = _make_summary_from_events(evts, run)
    # mean = (2000+2000+8000)/3 = 4000; max = 8000; spike = 2.0
    assert summary.ability_spike_score["Fireball"] == pytest.approx(2.0)


def test_summary_spike_score_uniform_is_one():
    run = _run()
    evts = [_evt(float(i), "Auto", 5000) for i in range(5)]
    summary = _make_summary_from_events(evts, run)
    assert summary.ability_spike_score["Auto"] == pytest.approx(1.0)


def test_summary_deaths_populated():
    run = _run(start=1000.0, end=1060.0)
    death = DeathRecord(time_s=1030.0, rel_time_s=30.0)
    evts = [_evt(1010.0, "Auto", 1000)]
    with (
        patch("simf.io.combat_log_summary.iter_damage_events", return_value=iter(evts)),
        patch("simf.io.combat_log_summary.iter_death_events", return_value=iter([death])),
    ):
        summary = summarize_run(Path("fake.log"), "T", run)
    assert len(summary.deaths) == 1
    assert summary.deaths[0].rel_time_s == pytest.approx(30.0)
