"""Boss encounter windows + trash/boss run segmentation."""

from dataclasses import dataclass
from pathlib import Path

from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_runs import ChallengeModeRun


@dataclass
class EncounterWindow:
    encounter_id: int
    name: str
    start_time_s: float
    end_time_s: float
    success: bool
    # When a boss is pulled, wiped, and re-pulled in the same run, attempt_index
    # counts from 1 for the first pull. Lets the UI distinguish "Degentrius (try 1)"
    # from "Degentrius (kill)".
    attempt_index: int = 1

    def duration_s(self) -> float:
        return self.end_time_s - self.start_time_s

    def label(self, total_attempts: int) -> str:
        if total_attempts <= 1:
            return self.name
        suffix = "kill" if self.success else f"try {self.attempt_index}"
        return f"{self.name} ({suffix})"


def parse_encounters(
    log_path: Path,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
) -> list[EncounterWindow]:
    """Pair ENCOUNTER_START / ENCOUNTER_END events into closed boss windows.

    Optionally bound by [start_time_s, end_time_s] — pass a ChallengeModeRun's
    bounds to get just that run's encounters. Re-pulls of the same boss within
    the window are tagged with incrementing attempt_index.

    Format:
        ENCOUNTER_START,<id>,<name>,<difficulty>,<size>,<map_id>
        ENCOUNTER_END,<id>,<name>,<difficulty>,<size>,<success>,<duration_ms>
    """
    encounters: list[EncounterWindow] = []
    pending: tuple[int, str, float] | None = None  # (id, name, start_t)
    attempts_by_id: dict[int, int] = {}

    with log_path.open() as f:
        for line in f:
            if "ENCOUNTER_" not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if start_time_s is not None and time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                continue

            if event_type == "ENCOUNTER_START":
                if len(fields) < 2:
                    continue
                try:
                    enc_id = int(fields[0])
                except ValueError:
                    continue
                name = fields[1]
                pending = (enc_id, name, time_s)
            elif event_type == "ENCOUNTER_END" and pending is not None:
                enc_id, name, start_t = pending
                pending = None
                if len(fields) < 5:
                    continue
                try:
                    success = bool(int(fields[4]))
                except ValueError:
                    success = False
                attempts_by_id[enc_id] = attempts_by_id.get(enc_id, 0) + 1
                encounters.append(
                    EncounterWindow(
                        encounter_id=enc_id,
                        name=name,
                        start_time_s=start_t,
                        end_time_s=time_s,
                        success=success,
                        attempt_index=attempts_by_id[enc_id],
                    )
                )

    return encounters


def find_encounter_at(time_s: float, encounters: list[EncounterWindow]) -> EncounterWindow | None:
    """Return the encounter window containing time_s, or None if it's trash."""
    for enc in encounters:
        if enc.start_time_s <= time_s <= enc.end_time_s:
            return enc
    return None


@dataclass
class RunSegment:
    """A slice of an M+ run — either a boss encounter or the trash between bosses.

    Time ranges are absolute Unix seconds; relative offsets are computed from
    the containing run's start_time_s.
    """

    kind: str  # "boss" or "trash"
    label: str  # human-readable: "Arcanotron Custos" or "Trash before Gemellus"
    start_time_s: float
    end_time_s: float
    encounter: EncounterWindow | None = None  # populated for kind == "boss"

    def duration_s(self) -> float:
        return self.end_time_s - self.start_time_s


def segment_run(run: ChallengeModeRun, encounters: list[EncounterWindow]) -> list[RunSegment]:
    """Slice a run into ordered boss + trash segments.

    Trash segments fill the gaps between encounters (and before the first / after
    the last). When the same boss is pulled multiple times in a run, each attempt
    becomes its own boss segment with attempt_index in the label.
    """
    end_time = run.end_time_s if run.end_time_s is not None else run.start_time_s
    sorted_encs = sorted(encounters, key=lambda e: e.start_time_s)
    multi_pull_ids = {e.encounter_id for e in encounters if e.attempt_index > 1} | {
        e.encounter_id
        for e in encounters
        if sum(1 for x in encounters if x.encounter_id == e.encounter_id) > 1
    }

    segments: list[RunSegment] = []
    cursor = run.start_time_s
    next_name = sorted_encs[0].name if sorted_encs else None

    for enc in sorted_encs:
        if enc.start_time_s > cursor:
            label = f"Trash before {next_name}" if next_name else "Trash"
            segments.append(
                RunSegment(
                    kind="trash",
                    label=label,
                    start_time_s=cursor,
                    end_time_s=enc.start_time_s,
                )
            )
        attempts_for_boss = sum(1 for x in encounters if x.encounter_id == enc.encounter_id)
        boss_label = (
            enc.label(attempts_for_boss) if enc.encounter_id in multi_pull_ids else enc.name
        )
        segments.append(
            RunSegment(
                kind="boss",
                label=boss_label,
                start_time_s=enc.start_time_s,
                end_time_s=enc.end_time_s,
                encounter=enc,
            )
        )
        cursor = enc.end_time_s
        idx = sorted_encs.index(enc)
        next_name = sorted_encs[idx + 1].name if idx + 1 < len(sorted_encs) else None

    if end_time > cursor:
        label = "Trash after last boss" if sorted_encs else "Trash"
        segments.append(
            RunSegment(
                kind="trash",
                label=label,
                start_time_s=cursor,
                end_time_s=end_time,
            )
        )

    # Drop zero-or-near-zero trash slivers that come from boss-end == run-end
    # alignment — they carry no info and clutter the timeline.
    return [s for s in segments if not (s.kind == "trash" and s.duration_s() < 1.0)]
