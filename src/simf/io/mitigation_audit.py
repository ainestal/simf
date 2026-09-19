"""Per-ability mitigation breakdown from a combat log run."""

from dataclasses import dataclass
from pathlib import Path

from .combat_log import ChallengeModeRun, DamageTakenEvent, iter_damage_events


@dataclass
class AbilityMitigationStats:
    ability: str
    hits: int
    total_base: int
    total_to_hp: int
    total_blocked: int
    total_absorbed: int
    total_resisted: int

    @property
    def avg_base(self) -> float:
        return self.total_base / self.hits if self.hits else 0.0

    @property
    def avg_to_hp(self) -> float:
        return self.total_to_hp / self.hits if self.hits else 0.0

    @property
    def blocked_pct(self) -> float:
        return self.total_blocked / self.total_base if self.total_base else 0.0

    @property
    def absorbed_pct(self) -> float:
        return self.total_absorbed / self.total_base if self.total_base else 0.0

    @property
    def resisted_pct(self) -> float:
        return self.total_resisted / self.total_base if self.total_base else 0.0

    @property
    def mitigated_pct(self) -> float:
        total_mit = self.total_blocked + self.total_absorbed + self.total_resisted
        return total_mit / self.total_base if self.total_base else 0.0

    @property
    def unmitigated_pct(self) -> float:
        return self.total_to_hp / self.total_base if self.total_base else 1.0


def audit_events_direct(events: list[DamageTakenEvent]) -> list[AbilityMitigationStats]:
    """Return per-ability mitigation stats for a list of events, sorted by total_base descending."""
    stats: dict[str, AbilityMitigationStats] = {}
    for e in events:
        key = e.spell_name
        if key not in stats:
            stats[key] = AbilityMitigationStats(
                ability=key,
                hits=0,
                total_base=0,
                total_to_hp=0,
                total_blocked=0,
                total_absorbed=0,
                total_resisted=0,
            )
        s = stats[key]
        s.hits += 1
        s.total_base += e.base_amount
        s.total_to_hp += e.amount
        s.total_blocked += e.blocked
        s.total_absorbed += e.absorbed
        s.total_resisted += e.resisted

    return sorted(stats.values(), key=lambda s: -s.total_base)


def audit_replay(
    log_path: Path,
    target_name: str,
    run: ChallengeModeRun,
) -> list[AbilityMitigationStats]:
    """Return per-ability mitigation stats for the run, sorted by total_base descending."""
    events = list(iter_damage_events(log_path, target_name, run.start_time_s, run.end_time_s))
    return audit_events_direct(events)
