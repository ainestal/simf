"""Cast + power-gain event extraction — skill inference (SPELL_CAST_SUCCESS) and
rage-timeline reconstruction (SPELL_ENERGIZE)."""

from dataclasses import dataclass
from pathlib import Path

from simf.io.combat_log_core import parse_combat_log_line


@dataclass(frozen=True)
class CastEvent:
    """A single SPELL_CAST_SUCCESS event attributed to one source.

    Used by `core/skill_inference.py` to bucket the player's playstyle
    from a real log (Phase 2.10b). Lightweight on purpose — we only need
    `(time_s, spell_id, source_name)` to compute Shield Block coverage,
    so we don't pull the full advanced-logging tail.
    """

    time_s: float
    spell_id: int
    source_name: str


def parse_cast_events(
    log_path: Path,
    *,
    source_name: str,
    spell_name: str,
    spell_id: int | None = None,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> list[CastEvent]:
    """Pull SPELL_CAST_SUCCESS events for a named spell by a named source.

    Separate from the damage hot path on purpose — the `_DAMAGE,` pre-filter
    in `iter_damage_events` is load-bearing on 200 MB session logs, and
    interleaving cast detection would gut it. This function does its own
    pass with a `spell_name` string-presence pre-filter (cheap, matches
    only lines we care about — `"Shield Block"` hits ~one line every few
    seconds, not every event).

    ``spell_id`` is optional but recommended — when provided, also filters
    by exact spell ID to avoid false positives (a Druid's "Shield Block"
    quest-text or item name would slip through pure name-matching). When
    None, name match alone decides.

    Returns events sorted by time_s ascending.
    """
    events: list[CastEvent] = []
    name_quoted = f'"{spell_name}"'  # CSV-escaped — the form combat logs write
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            # Two-tier pre-filter: cast-line + spell-name. Both string-in-line
            # checks before the CSV parse pays off — the damage walk handles
            # ~95% of events, only ~1 in 20 lines makes it past these two.
            if "SPELL_CAST_SUCCESS," not in line:
                continue
            if name_quoted not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if event_type != "SPELL_CAST_SUCCESS":
                continue
            if start_time_s is not None and time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            # Fields layout for SPELL_CAST_SUCCESS:
            #   0=sourceGUID, 1=sourceName, 2=sourceFlags, 3=sourceRaidFlags,
            #   4=destGUID, 5=destName, 6=destFlags, 7=destRaidFlags,
            #   8=spellId, 9=spellName, 10=spellSchool, …
            if len(fields) < 10:
                continue
            if fields[1] != source_name:
                continue
            try:
                line_spell_id = int(fields[8])
            except (ValueError, IndexError):
                continue
            if spell_id is not None and line_spell_id != spell_id:
                continue
            if fields[9] != spell_name:
                continue
            events.append(CastEvent(time_s=time_s, spell_id=line_spell_id, source_name=source_name))
    events.sort(key=lambda e: e.time_s)
    return events


def parse_interrupted_spell_ids(
    log_path: Path,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> frozenset[int]:
    """Spell IDs proven interruptible by a real ``SPELL_INTERRUPT`` in this log.

    Used by the coaching-coverage join to flag "this hit was a cast you (or
    anyone in the party) kicked elsewhere in this run — it wasn't immune,
    a kick prevents the damage entirely." Deliberately evidence-only: WoW
    doesn't mark casts interruptible in the combat log, and hand-curating a
    per-boss-ability list would be exactly the kind of external assumption
    this feature's honesty charter avoids (see ``core/coaching.py``). A
    spell that's interruptible but never actually got interrupted in the
    available log is silently not flagged — under-shows, never lies.

    ``SPELL_INTERRUPT`` fields (after the standard 8 source/dest columns):
    ``spellId, spellName, spellSchool`` (the interrupting ability, e.g.
    Pummel) then ``extraSpellId, extraSpellName, extraSpellSchool`` (the
    spell that got interrupted) — it's ``extraSpellId`` (fields[11]) we want.
    """
    interrupted: set[int] = set()
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            if "SPELL_INTERRUPT," not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if event_type != "SPELL_INTERRUPT":
                continue
            if start_time_s is not None and time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            if len(fields) < 12:
                continue
            try:
                interrupted.add(int(fields[11]))
            except ValueError:
                continue
    return frozenset(interrupted)


@dataclass(frozen=True)
class EnergizeEvent:
    """A single SPELL_ENERGIZE event — the actor's power gain from a spell.

    Used by `core/death_analysis.py` to reconstruct a rage timeline for the
    Why-died panel. Format (after the standard 8 source/dest header fields
    and the spell triple, then advanced-logging block):

        ..., amount, overEnergize, powerType, maxPower

    `amount` is in DISPLAYED units in modern WoW logs — verified against
    Brutoh's WoWCombatLog-051026_073906.txt (Shield Slam → 17, Shield Charge
    → 20, etc., matching in-game tooltips). No /10 divide.

    `power_type` is the WoW PowerType enum:
        0 = mana, 1 = rage, 2 = focus, 3 = energy, 6 = runic_power, ...
    Tank survivability sim filters powerType == 1 (rage) for warriors;
    callers needing other power types can ignore the filter.
    """

    time_s: float
    spell_id: int
    spell_name: str
    amount: float  # displayed rage units (already divided by game)
    over_energize: float  # amount lost because the power bar was full
    power_type: int  # 1 = rage
    source_name: str


def parse_energize_events(
    log_path: Path,
    actor_name: str,
    *,
    power_type: int | None = 1,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> list[EnergizeEvent]:
    """Pull SPELL_ENERGIZE events on `actor_name` (both source AND target — the
    actor is gaining the power).

    The combat-log convention for ENERGIZE is `sourceGUID == destGUID == actor`
    (e.g. Shield Slam energising the warrior who cast it). We match on the
    destName at fields[5] because that's the recipient of the power tick.

    `power_type` defaults to 1 (rage). Pass None to disable the filter.

    The energise suffix is at the END of the line — the middle advanced-logging
    block is variable-length, so we anchor from the tail like `parse_damage_event`
    does. Suffix layout: `amount, overEnergize, powerType, maxPower` (4 fields).

    Returns events sorted by time_s ascending.
    """
    events: list[EnergizeEvent] = []
    name_quoted = f'"{actor_name}"'
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            # Two-tier pre-filter — keeps the scan cheap on 200 MB session logs.
            if "SPELL_ENERGIZE," not in line:
                continue
            if name_quoted not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if event_type != "SPELL_ENERGIZE":
                continue
            if start_time_s is not None and time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            # Fields layout: 0=srcGUID, 1=srcName, ..., 4=dstGUID, 5=dstName,
            # ..., 8=spellId, 9=spellName, 10=spellSchool, [advanced block],
            # then trailing: amount, overEnergize, powerType, maxPower.
            if len(fields) < 11:
                continue
            if fields[5] != actor_name:
                continue
            try:
                spell_id = int(fields[8])
            except ValueError:
                continue
            spell_name = fields[9]
            # Tail-anchored parse — the 4 fields are at fields[-4:].
            try:
                amount = float(fields[-4])
                over_energize = float(fields[-3])
                pt = int(fields[-2])
                # fields[-1] is maxPower; we don't need it.
            except (ValueError, IndexError):
                continue
            if power_type is not None and pt != power_type:
                continue
            events.append(
                EnergizeEvent(
                    time_s=time_s,
                    spell_id=spell_id,
                    spell_name=spell_name,
                    amount=amount,
                    over_energize=over_energize,
                    power_type=pt,
                    source_name=fields[1],
                )
            )
    events.sort(key=lambda e: e.time_s)
    return events
