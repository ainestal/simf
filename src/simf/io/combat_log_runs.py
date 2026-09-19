"""M+ run boundaries — ChallengeModeRun + CHALLENGE_MODE_START/END pairing."""

import re
from dataclasses import dataclass
from pathlib import Path

from simf.io.combat_log_core import parse_combat_log_line


@dataclass
class ChallengeModeRun:
    """Single M+ run extracted from a combat log.

    On WoW's CHALLENGE_MODE_END semantics — ``success`` is the literal
    log field, which encodes "did the group complete the key?" (not
    "did they time it?"):

    - ``success=True``, ``duration_ms > 0``  → completed (may be timed
      OR over-time; compare ``duration_ms`` to ``par_time_ms``).
    - ``success=False``, ``duration_ms == 0`` → abandoned (left dungeon
      without finishing); WoW writes the all-zeros pattern here.
    - ``success=None`` → an unmatched CHALLENGE_MODE_START with no END of
      its own — either the log truncated mid-run, or a later START for a
      different run superseded it before its END ever arrived (WoW doesn't
      always emit CHALLENGE_MODE_END for every way of leaving a key).

    ``par_time_ms`` is populated post-parse from ``dungeons.yaml`` when
    we have a verified par for the map; ``None`` means "don't know,
    don't make claims about timing."
    """

    map_id: int
    map_name: str
    key_level: int
    affixes: list[int]
    start_time_s: float
    end_time_s: float | None = None
    success: bool | None = None
    duration_ms: int | None = None
    par_time_ms: int | None = None
    # Byte offsets into the source log, captured during parse_challenge_modes.
    # `start_byte_offset` points at the CHALLENGE_MODE_START line; downstream
    # scans (player-name detection in particular) can seek there directly to
    # skip ~80% of a multi-run session log. Defaults to 0 so the field is
    # optional for synthetic ChallengeModeRun instances built in tests.
    start_byte_offset: int = 0

    def duration_s(self) -> float:
        if self.end_time_s is not None:
            return self.end_time_s - self.start_time_s
        if self.duration_ms is not None:
            return self.duration_ms / 1000.0
        return 0.0

    def is_abandoned(self) -> bool:
        """Group left the dungeon without completing the key.

        WoW writes ``CHALLENGE_MODE_END,<id>,0,0,0,...`` for this — a
        completed-over-time run is ``success=1`` with a real duration,
        so the all-zeros pattern is the only honest abandon signal.
        """
        return self.success is False and (self.duration_ms or 0) == 0

    def is_timed(self) -> bool | None:
        """True if completed within the dungeon's par timer. Returns
        ``None`` when we lack information — abandoned runs, runs with
        no recorded ``duration_ms``, or maps where ``par_time_ms`` is
        null in ``dungeons.yaml``.
        """
        if self.success is not True:
            return None
        if self.duration_ms is None or self.par_time_ms is None:
            return None
        return self.duration_ms <= self.par_time_ms


def _last_event_time(log_path: Path, after: float) -> float | None:
    """Return the timestamp of the last parseable line in the log at or after `after`."""
    last_t: float | None = None
    with log_path.open() as f:
        for line in f:
            parsed = parse_combat_log_line(line)
            if parsed and parsed[0] >= after:
                last_t = parsed[0]
    return last_t


def _par_time_for_map(map_id: int) -> int | None:
    """Look up the dungeon's M+ par time (in ms) from dungeons.yaml.

    Imported lazily to avoid a top-of-module cycle — ``constants.py``
    has the loader; pulling it eagerly here would import the data
    layer from the parser. None when the map isn't in the catalog
    or the catalog has ``par_time_ms: null`` (unverified par).
    """
    from ..core.constants import load_dungeon_catalog

    for entry in load_dungeon_catalog():
        if entry.get("map_id") == map_id:
            return entry.get("par_time_ms")
    return None


def parse_challenge_modes(log_path: Path) -> list[ChallengeModeRun]:
    """Find all CHALLENGE_MODE_START / CHALLENGE_MODE_END pairs in the log.

    Incomplete runs (a START with no matching END of its own) are included
    with `success`/`duration_ms` left None — end boundary is the last parsed
    event's timestamp for a run still open at end-of-file, or the next run's
    start time if a later START supersedes it first. An END is only matched
    to the currently-pending run when its own leading field agrees with that
    run's `map_id`; a mismatched END is ignored rather than misapplied.
    """
    runs: list[ChallengeModeRun] = []
    pending: ChallengeModeRun | None = None
    # Binary mode so file.tell() returns byte offsets we can later seek to.
    # Text mode's tell() returns an opaque cookie that's only safe to round-trip,
    # not arithmetic on. We decode each line ourselves.
    offset = 0
    with log_path.open("rb") as f:
        for line_bytes in f:
            line_offset = offset
            offset += len(line_bytes)
            if b"CHALLENGE_MODE" not in line_bytes:
                continue
            line = line_bytes.decode("utf-8", errors="replace")
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed

            if event_type == "CHALLENGE_MODE_START":
                if len(fields) < 4:
                    continue
                if pending is not None:
                    # A new START while a run is still open means the prior run
                    # never got its own matching END logged (e.g. abandoned via
                    # a path that doesn't emit CHALLENGE_MODE_END). Close it
                    # honestly as unmatched — `success=None` — rather than
                    # silently dropping it (which lost real runs) or leaving it
                    # `pending` to catch a stray END that actually belongs to
                    # this new run (which mislabeled runs with the wrong data).
                    pending.end_time_s = time_s
                    runs.append(pending)
                map_name = fields[0]
                map_id = int(fields[1])
                key_level = int(fields[3])
                # The affix list is a bracketed array — e.g. ``[9,10,147]`` — whose
                # internal commas the CSV reader splits across fields[4:]. Rejoin
                # from field 4 (the bracket is always the terminal field) before
                # extracting ids, or we'd keep only the first affix ("[9" → [9]).
                affixes_raw = ",".join(fields[4:]) if len(fields) > 4 else "[]"
                affixes = [int(x) for x in re.findall(r"\d+", affixes_raw)]
                pending = ChallengeModeRun(
                    map_id=map_id,
                    map_name=map_name,
                    key_level=key_level,
                    affixes=affixes,
                    start_time_s=time_s,
                    start_byte_offset=line_offset,
                    par_time_ms=_par_time_for_map(map_id),
                )
            elif event_type == "CHALLENGE_MODE_END":
                if pending is None:
                    continue
                # CHALLENGE_MODE_END's own leading field is the same map/instance
                # id CHALLENGE_MODE_START carried — verify it matches the run
                # we're tracking before closing it. A mismatch means this END
                # belongs to a different run we never captured a START for (nothing to
                # close it against); ignore it and keep waiting for `pending`'s own END,
                # rather than stamping the wrong run's success/duration onto it.
                try:
                    end_id = int(fields[0]) if fields else None
                except ValueError:
                    end_id = None
                if end_id is not None and end_id != pending.map_id:
                    continue
                if len(fields) >= 4:
                    try:
                        pending.success = bool(int(fields[1]))
                        pending.duration_ms = int(fields[3])
                    except ValueError:
                        pass
                pending.end_time_s = time_s
                runs.append(pending)
                pending = None

    if pending is not None:
        last_t = _last_event_time(log_path, pending.start_time_s)
        if last_t is not None:
            pending.end_time_s = last_t
        runs.append(pending)

    return runs
