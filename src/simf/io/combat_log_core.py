"""Core combat-log line parsing — the foundation every other combat_log_* module imports.

Targets COMBAT_LOG_VERSION 22 (Midnight 12.0.5). Parses the timestamp + comma-separated
fields, then anchors damage-suffix parsing from the END of each line — the middle
"advanced logging" block has a variable number of power-related fields that we don't
need anyway.
"""

import csv
import io
from datetime import datetime

DAMAGE_EVENTS = {
    "SWING_DAMAGE",
    "SPELL_DAMAGE",
    "SPELL_PERIODIC_DAMAGE",
    "RANGE_DAMAGE",
    "SPELL_BUILDING_DAMAGE",
}

# Heal-event types we count for role detection. We don't care about amounts —
# just that a player is casting heals at all. Healers cast 5-10× more heal
# events than any DPS spec, so a simple count separates them cleanly.
HEAL_EVENTS = {
    "SPELL_HEAL",
    "SPELL_PERIODIC_HEAL",
}

# WoW spell school flags. Combine via bitwise OR; we report the lowest matching school.
SCHOOL_FLAGS = [
    (0x01, "physical"),
    (0x02, "holy"),
    (0x04, "fire"),
    (0x08, "nature"),
    (0x10, "frost"),
    (0x20, "shadow"),
    (0x40, "arcane"),
]


def school_name(flags: int) -> str:
    for flag, name in SCHOOL_FLAGS:
        if flags & flag:
            return name
    return "unknown"


def npc_id_from_guid(guid: str) -> int | None:
    """Decode the NPC ID from a WoW combat-log unit GUID.

    Creature/Vehicle GUIDs encode the NPC ID as the 6th hyphen-separated
    field, base 10. Example:

        ``Creature-0-4244-2915-97430-248373-00008889FF`` → ``248373``

    Player, Pet, and Item GUIDs return None — there's no stable
    "NPC ID" for a player, and pet IDs don't cross-reference Wowhead's
    NPC catalog the way creature IDs do.
    """
    if not guid:
        return None
    parts = guid.split("-")
    if len(parts) < 7:
        return None
    if parts[0] not in ("Creature", "Vehicle"):
        return None
    try:
        return int(parts[5])
    except ValueError:
        return None


def parse_combat_log_line(line: str) -> tuple[float, str, list[str]] | None:
    """Returns (timestamp_seconds, event_type, fields_after_event_type) or None."""
    # The combat log uses two spaces between timestamp and event payload.
    sep = line.find("  ")
    if sep < 0:
        return None
    timestamp_str = line[:sep].strip()
    rest = line[sep + 2 :].strip()
    if not rest:
        return None

    try:
        dt = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S.%f")
    except ValueError:
        return None

    reader = csv.reader(io.StringIO(rest))
    try:
        fields = next(reader)
    except StopIteration:
        return None
    if not fields:
        return None

    return dt.timestamp(), fields[0], fields[1:]
