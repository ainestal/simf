"""Damage-taken event parsing — the DamageTakenEvent model + its extractors."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from simf.io.combat_log_core import (
    DAMAGE_EVENTS,
    npc_id_from_guid,
    parse_combat_log_line,
    school_name,
)


@dataclass
class DamageTakenEvent:
    time_s: float  # absolute Unix timestamp seconds
    event_type: str
    source_name: str
    spell_name: str
    school: str
    amount: int  # post-mitigation damage to HP
    base_amount: int  # pre-mitigation damage attempt
    overkill: int
    blocked: int
    absorbed: int
    resisted: int
    is_critical: bool
    is_glancing: bool
    # Source/ability identifiers — defaulted so callers that construct
    # synthetic events (tests, WCL-API shim) don't need to supply them.
    source_guid: str = ""
    source_npc_id: int | None = (
        None  # parsed from Creature/Vehicle GUID; None for player/pet/unknown
    )
    spell_id: int | None = None  # numeric spell ID for SPELL_* events; None for SWING auto-attacks
    # True iff the source event was a periodic-damage tick. The local .txt
    # parser doesn't populate this (it drives DoT classification from
    # ``event_type == "SPELL_PERIODIC_DAMAGE"`` instead); the WCL ingest
    # path populates it from the raw ``tick`` boolean WCL attaches to
    # periodic-damage events. Adapters that need a DoT-tick signal off the
    # WCL path can read this directly without re-deriving from event_type
    # (which `_map_event` collapses to SWING/SPELL_DAMAGE only).
    tick_flag: bool = False
    # Position fields from the advanced-logging block. None when the log
    # was captured with ACL off or the fields couldn't be parsed.
    pos_x: float | None = None
    pos_y: float | None = None
    map_id: int | None = None
    facing: float | None = None
    # currentHP/maxHP from the SAME advanced-logging block, at a fixed
    # offset from its front (unlike position, which is anchored from the
    # back — see the parsing comment below for why). None on ACL-off logs,
    # AND on any event where the block turned out to describe the attacker
    # rather than this DamageTakenEvent's target (see below) — never a
    # stale or wrong-unit's HP.
    current_hp: int | None = None
    max_hp: int | None = None


def parse_damage_event(
    time_s: float, event_type: str, fields: list[str], target_name: str
) -> DamageTakenEvent | None:
    if event_type not in DAMAGE_EVENTS:
        return None
    if len(fields) < 12:
        return None
    if fields[5] != target_name:
        return None

    source_guid = fields[0]
    source_name = fields[1]

    # Spell info appears at fields[8..10] for SPELL_*; for SWING there's no spell info.
    spell_id: int | None = None
    if event_type.startswith("SWING"):
        spell_name = "auto-attack"
        school_str = "physical"
    else:
        if len(fields) < 11:
            return None
        try:
            spell_id = int(fields[8])
        except ValueError:
            spell_id = None
        spell_name = fields[9]
        try:
            school_str = school_name(int(fields[10], 16))
        except ValueError:
            school_str = "unknown"

    # Parse damage suffix from the END (variable-length advanced block in the middle).
    # SPELL_*_DAMAGE often has a trailing "ST" or "AOE" marker; strip it.
    has_marker = fields[-1] in ("ST", "AOE")
    suffix = fields[-11:-1] if has_marker else fields[-10:]
    if len(suffix) < 9:
        return None

    try:
        amount = int(suffix[0])
        base_amount = int(suffix[1])
        overkill = int(suffix[2])
        # suffix[3] is the numeric school (already captured via spell school flag)
        resisted = int(suffix[4])
        blocked = int(suffix[5])
        absorbed = int(suffix[6])
        is_critical = suffix[7] not in ("nil", "0", "")
        is_glancing = suffix[8] not in ("nil", "0", "")
    except (ValueError, IndexError):
        return None

    # The 5 fields preceding the damage suffix carry the advanced-logging
    # position block: posX, posY, mapID, facing, level. ACL-off logs omit the
    # whole block (the suffix is the entire trailing payload) — fall back to None.
    pos_x: float | None = None
    pos_y: float | None = None
    map_id: int | None = None
    facing: float | None = None
    pos_end = -11 if has_marker else -10
    pos_start = pos_end - 5
    if len(fields) >= -pos_start:
        try:
            pos_x = float(fields[pos_start])
            pos_y = float(fields[pos_start + 1])
            map_id = int(fields[pos_start + 2])
            facing = float(fields[pos_start + 3])
        except (ValueError, IndexError):
            pos_x = pos_y = facing = None
            map_id = None

    # currentHP/maxHP sit near the FRONT of the same advanced-logging
    # block, at a fixed offset from infoGUID — infoGUID, ownerGUID,
    # currentHP, maxHP, ... — unlike position (anchored from the back
    # because a variable number of power-related fields sit in between).
    # infoGUID starts the block at fields[8] for SWING_* (no spell-info
    # fields ahead of it) or fields[11] for everything else.
    #
    # The block does NOT always describe THIS event's destination, though.
    # Confirmed against a real log (examples/WoWCombatLog-051726_134819.txt):
    # for SWING_DAMAGE, Blizzard attaches the block to the ATTACKER (the
    # companion SWING_DAMAGE_LANDED event carries the victim's info
    # instead, which this parser doesn't consume) — infoGUID matched the
    # attacking mob's own GUID, and currentHP/maxHP were boss-scale
    # (millions), not the target's. Every other damage event type attaches
    # the block to the destination — infoGUID matched dest_guid, and
    # currentHP/maxHP were player-scale. Rather than special-case SWING by
    # name, cross-check infoGUID against dest_guid directly: correct for
    # SWING today and self-validating against any other event type with
    # the same asymmetry.
    current_hp: int | None = None
    max_hp: int | None = None
    if pos_x is not None:
        info_guid_index = 8 if event_type.startswith("SWING") else 11
        try:
            if fields[info_guid_index] == fields[4]:
                current_hp = int(fields[info_guid_index + 2])
                max_hp = int(fields[info_guid_index + 3])
        except (ValueError, IndexError):
            current_hp = max_hp = None

    return DamageTakenEvent(
        time_s=time_s,
        event_type=event_type,
        source_name=source_name,
        spell_name=spell_name,
        school=school_str,
        amount=amount,
        base_amount=base_amount,
        overkill=overkill,
        blocked=blocked,
        absorbed=absorbed,
        resisted=resisted,
        is_critical=is_critical,
        is_glancing=is_glancing,
        source_guid=source_guid,
        source_npc_id=npc_id_from_guid(source_guid),
        spell_id=spell_id,
        pos_x=pos_x,
        pos_y=pos_y,
        map_id=map_id,
        facing=facing,
        current_hp=current_hp,
        max_hp=max_hp,
    )


def iter_damage_events(
    log_path: Path,
    target_name: str,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
) -> Iterator[DamageTakenEvent]:
    """Yield damage-taken events on `target_name`, optionally bounded by a time window."""
    with log_path.open() as f:
        for line in f:
            # Cheap pre-filter to skip 95% of lines that don't target our character.
            if target_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if start_time_s is not None and time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                continue
            evt = parse_damage_event(time_s, event_type, fields, target_name)
            if evt is not None:
                yield evt
