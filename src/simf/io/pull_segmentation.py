"""Phase B trash-pull inference — split a trash segment's damage events
into named pulls.

Today's `RunSegment(kind="trash", label="Trash before Gemellus", ...)` is
coarse: it spans every event between two bosses regardless of how many
pulls happened in that gap. Phase B clusters events on:

  1. A combat gap of >= ``min_gap_s`` (default 8s) closes one pull and
     starts the next.
  2. A pull is only "named" once >= ``min_distinct_sources`` (default 3)
     distinct hostile creatures hit the tank in it — solo-mob taps
     between real pulls stay unlabelled.

The output is ``list[TrashPull]`` with a stable fingerprint
(``sources``: frozenset of source names) so identical packs across runs
get the same id. UI integration (predictive per-pull risk, named labels
in the per-segment view) is left to a follow-up commit — this module
ships the data structure + tests only.

Engaged-tank brief: "Voidling pack before Selin will kill you 18% of the
time at +14 MGT" — that user-facing line requires the per-pull bucketing
this module produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .combat_log import DamageTakenEvent, RunSegment


@dataclass(frozen=True)
class TrashPull:
    """One inferred trash-pull within a run's trash segment.

    ``index`` is 1-based within the parent segment. ``sources`` is a
    frozenset of hostile-creature names so the same pack across runs
    fingerprints to the same value (useful for cross-log aggregation in
    a future community-corpus pass).

    ``mob_guids`` is the set of distinct creature instances seen — 5
    Voidlings in one pack each have distinct GUIDs even though they
    share a name. Counting names alone (`len(sources)`) collapses them
    to 1, which under-reports the actual pull size.
    """

    index: int
    start_time_s: float
    end_time_s: float
    sources: frozenset[str]
    mob_guids: frozenset[str]
    n_events: int
    total_damage: int
    # Reference to the containing trash segment label (e.g. "Trash before
    # Gemellus") so the UI can render `"Voidling pack · trash before
    # Gemellus"` without re-walking encounters.
    parent_segment_label: str = ""

    def duration_s(self) -> float:
        return self.end_time_s - self.start_time_s

    def label(self) -> str:
        """Human-readable pull name. Heuristic: pick the source that did
        the most damage as the namesake. Falls back to source count when
        the pack is too sparse to single out one mob."""
        if not self.sources:
            return f"Pull {self.index}"
        # Without per-source damage breakdown stored on the pull, fall
        # back to "{N}-mob pack". A follow-up may carry per-source
        # totals to upgrade this to "Voidling pack" semantics.
        return f"{len(self.sources)}-mob pack"


@dataclass
class _PullBuilder:
    start_time_s: float
    end_time_s: float
    sources: set[str] = field(default_factory=set)
    mob_guids: set[str] = field(default_factory=set)
    n_events: int = 0
    total_damage: int = 0

    def add(self, e: DamageTakenEvent) -> None:
        self.end_time_s = e.time_s
        if e.source_name:
            self.sources.add(e.source_name)
        # GUIDs identify individual creature instances — distinct from
        # names, which collapse same-typed mobs (5 Voidlings → 1 name,
        # 5 GUIDs). We only record creature/vehicle GUIDs; player and
        # pet sources don't belong in a pull's mob count.
        if e.source_guid and (
            e.source_guid.startswith("Creature-") or e.source_guid.startswith("Vehicle-")
        ):
            self.mob_guids.add(e.source_guid)
        self.n_events += 1
        self.total_damage += max(int(e.amount), 0)


def cluster_trash_pulls(
    events: list[DamageTakenEvent],
    segment: RunSegment,
    *,
    min_gap_s: float = 8.0,
    min_distinct_sources: int = 3,
) -> list[TrashPull]:
    """Cluster trash damage events into pulls.

    Args:
        events: damage-taken events that fall within ``segment``'s time
            range. Assumed already sorted by ``time_s`` ascending.
        segment: the trash segment they belong to (used for the parent
            label only).
        min_gap_s: idle duration that closes one pull and starts the next.
        min_distinct_sources: a pull must have at least this many distinct
            source names to count as a real pack. Below this threshold the
            cluster is dropped (single mob taps between real pulls).

    Returns:
        ``TrashPull`` list, ordered by start time. Empty when no cluster
        meets the source-count floor.
    """
    if segment.kind != "trash" or not events:
        return []

    in_window = [e for e in events if segment.start_time_s <= e.time_s <= segment.end_time_s]
    if not in_window:
        return []

    builders: list[_PullBuilder] = []
    current: _PullBuilder | None = None
    for e in in_window:
        if current is None:
            current = _PullBuilder(start_time_s=e.time_s, end_time_s=e.time_s)
            current.add(e)
            continue
        gap = e.time_s - current.end_time_s
        if gap >= min_gap_s:
            builders.append(current)
            current = _PullBuilder(start_time_s=e.time_s, end_time_s=e.time_s)
        current.add(e)
    if current is not None:
        builders.append(current)

    pulls: list[TrashPull] = []
    pull_idx = 0
    for b in builders:
        if len(b.sources) < min_distinct_sources:
            continue
        pull_idx += 1
        pulls.append(
            TrashPull(
                index=pull_idx,
                start_time_s=b.start_time_s,
                end_time_s=b.end_time_s,
                sources=frozenset(b.sources),
                mob_guids=frozenset(b.mob_guids),
                n_events=b.n_events,
                total_damage=b.total_damage,
                parent_segment_label=segment.label,
            )
        )
    return pulls
