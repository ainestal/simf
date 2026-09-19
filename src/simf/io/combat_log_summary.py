"""Run-level aggregation — deaths, per-ability/source damage rollups, LogSummary."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_damage import DamageTakenEvent, iter_damage_events
from simf.io.combat_log_runs import ChallengeModeRun


@dataclass
class DeathRecord:
    time_s: float  # absolute timestamp of UNIT_DIED
    rel_time_s: float  # seconds into the run


@dataclass
class LogSummary:
    run: ChallengeModeRun
    duration_s: float
    event_count: int
    total_amount: int
    total_base_amount: int
    total_blocked: int
    total_absorbed: int
    total_resisted: int
    by_school: dict[str, int]
    by_source_amount: dict[str, int]
    by_source_count: dict[str, int]
    by_ability_amount: dict[str, int]
    by_ability_count: dict[str, int]
    ability_max_hit: dict[str, int] = field(default_factory=dict)
    ability_spike_score: dict[str, float] = field(default_factory=dict)
    deaths: list[DeathRecord] = field(default_factory=list)


def iter_death_events(
    log_path: Path,
    target_name: str,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
) -> Iterator[DeathRecord]:
    """Yield DeathRecord for each UNIT_DIED event where destName matches target_name."""
    with log_path.open() as f:
        for line in f:
            if "UNIT_DIED" not in line:
                continue
            if target_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if event_type != "UNIT_DIED":
                continue
            if len(fields) < 6:
                continue
            if fields[5] != target_name:
                continue
            if start_time_s is not None and time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                continue
            yield DeathRecord(
                time_s=time_s,
                rel_time_s=time_s - (start_time_s or time_s),
            )


def summarize_events_direct(
    events: list[DamageTakenEvent],
    run: ChallengeModeRun,
    deaths: list["DeathRecord"] | None = None,
) -> "LogSummary":
    """Build a LogSummary directly from event objects (no file I/O)."""
    total_amount = sum(e.amount for e in events)
    total_base = sum(e.base_amount for e in events)
    total_blocked = sum(e.blocked for e in events)
    total_absorbed = sum(e.absorbed for e in events)
    total_resisted = sum(e.resisted for e in events)

    by_school: dict[str, int] = {}
    by_source_amount: dict[str, int] = {}
    by_source_count: dict[str, int] = {}
    by_ability_amount: dict[str, int] = {}
    by_ability_count: dict[str, int] = {}
    ability_max_hit: dict[str, int] = {}
    for e in events:
        by_school[e.school] = by_school.get(e.school, 0) + e.amount
        by_source_amount[e.source_name] = by_source_amount.get(e.source_name, 0) + e.amount
        by_source_count[e.source_name] = by_source_count.get(e.source_name, 0) + 1
        key = e.spell_name
        by_ability_amount[key] = by_ability_amount.get(key, 0) + e.amount
        by_ability_count[key] = by_ability_count.get(key, 0) + 1
        if e.amount > ability_max_hit.get(key, 0):
            ability_max_hit[key] = e.amount

    ability_spike_score: dict[str, float] = {
        key: ability_max_hit[key] / (by_ability_amount[key] / by_ability_count[key])
        for key in ability_max_hit
        if by_ability_count.get(key, 0) > 0
    }

    return LogSummary(
        run=run,
        duration_s=run.duration_s(),
        event_count=len(events),
        total_amount=total_amount,
        total_base_amount=total_base,
        total_blocked=total_blocked,
        total_absorbed=total_absorbed,
        total_resisted=total_resisted,
        by_school=by_school,
        by_source_amount=by_source_amount,
        by_source_count=by_source_count,
        by_ability_amount=by_ability_amount,
        by_ability_count=by_ability_count,
        ability_max_hit=ability_max_hit,
        ability_spike_score=ability_spike_score,
        deaths=deaths or [],
    )


def summarize_run(log_path: Path, target_name: str, run: ChallengeModeRun) -> LogSummary:
    events = list(iter_damage_events(log_path, target_name, run.start_time_s, run.end_time_s))
    deaths = list(iter_death_events(log_path, target_name, run.start_time_s, run.end_time_s))
    return summarize_events_direct(events, run, deaths)
