"""Party-member detection — who's in this log, their role, and their race.

Two role-inference paths: authoritative (COMBATANT_INFO.spec_id, ACL-on only)
and behavioural fallback (most damage taken = tank, most heals cast = healer).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from simf.io.combat_log_core import DAMAGE_EVENTS, HEAL_EVENTS, parse_combat_log_line
from simf.io.spec_ids import SPEC_ID_TO_CLASS_SPEC, spec_to_role


def count_party_deaths_in_run(
    log_path: Path,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> int:
    """Count Player-* UNIT_DIED events inside a run window.

    Cheap one-pass scan that only inspects lines containing ``UNIT_DIED,``
    — orders of magnitude faster than ``detect_party_roles`` (which also
    tallies damage + heal events to derive role). Used by the log-picker
    label to show a "had deaths" badge without paying the full party-role
    detection cost.

    Honours ``start_byte_offset`` so a deep run inside a large session log
    skips the leading bytes; honours ``end_time_s`` to short-circuit once
    the scan walks past the run boundary.

    The Player- prefix filter is deliberate: NPC / pet deaths are very
    common in M+ (every trash mob death emits UNIT_DIED) and they don't
    belong in a "did the *party* die" indicator.
    """
    count = 0
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            if "UNIT_DIED," not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if event_type != "UNIT_DIED":
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            if start_time_s is not None and time_s < start_time_s:
                continue
            # UNIT_DIED layout: destGUID, destName at fields[4..5].
            if len(fields) >= 6 and fields[4].startswith("Player-"):
                count += 1
    return count


def detect_destination_player_names(
    log_path: Path,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
    max_bytes: int = 20_000_000,
    early_exit_count: int = 5,
) -> list[tuple[str, int]]:
    """Detect player names that take damage in this log, ranked by hit count.

    In an M+ log, the tank takes ~70-80% of all damage, so the top entry is
    almost always the tank — which is what the UI wants to pre-select.

    Returns ``[(name, hit_count), …]`` sorted by hit_count descending. When
    ``start_time_s``/``end_time_s`` are passed, only events inside that
    window are considered — important for logs that contain back-to-back
    keys with different parties (otherwise detection would lock onto the
    *first* party's tank, not the one for the run the user picked).

    ``start_byte_offset`` lets the caller skip the sequential bytes before
    the run begins — `parse_challenge_modes` populates this on each
    ``ChallengeModeRun`` so detection on the 4th run of a 239 MB session log
    doesn't pay 200 MB of sequential read just to reach the right window.

    ``max_bytes`` caps the file read so a 400 MB session log doesn't stall
    the Streamlit rerun. ``early_exit_count`` short-circuits once that many
    distinct ``Player-``-GUID destinations have all been hit > 10 times.

    The cheap pre-filter `"_DAMAGE,"` skips ~half the lines (the aura /
    cast / heal events) without paying parse cost.
    """
    counts: dict[str, int] = {}
    bytes_read = 0
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            bytes_read += len(line)
            # Byte cap only applies when the caller hasn't bounded by time;
            # otherwise the time window is the natural budget. Without this
            # gate, scoping detection to a run that begins past the byte
            # cap (back-to-back keys, 200 MB+ session log) would terminate
            # before reading a single event from the picked run.
            if end_time_s is None and bytes_read >= max_bytes:
                break
            if "_DAMAGE," not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if event_type not in DAMAGE_EVENTS:
                continue
            # Window check — keeps the file scan bounded by the picked run.
            if end_time_s is not None and time_s > end_time_s:
                break
            if start_time_s is not None and time_s < start_time_s:
                continue
            if len(fields) < 6:
                continue
            # fields[4] is dest GUID; player-targeted events start with "Player-".
            # Pets, totems, NPCs use other prefixes and get filtered out.
            if not fields[4].startswith("Player-"):
                continue
            name = fields[5]
            if not name or "-" not in name:
                continue
            counts[name] = counts.get(name, 0) + 1
            # Once we've seen a full M+ party's worth and each has > 10 hits,
            # the ranking is stable enough to bail. The ">10 hits" guard
            # avoids early-exiting on five players who each took one stray
            # cleave before the run started.
            if len(counts) >= early_exit_count and all(c > 10 for c in counts.values()):
                break
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class PartyMember:
    """Auto-detected party member with inferred (or log-stated) role.

    `role` is one of: "tank", "healer", "dps". Two inference paths:
      - **Authoritative**: `COMBATANT_INFO.spec_id` from the log directly
        (only when Advanced Combat Logging is on). Carries the actual spec.
      - **Behavioural fallback**: tank = most damage-taken hits, healer =
        most heal-events cast, rest = DPS. Used when ACL is off and the
        log emits zero COMBATANT_INFO lines.

    `class_spec` is set to the simf-internal slug (e.g. "protection_warrior")
    when known, else None. `role_source` tracks how the role was determined,
    so the UI can label `Protection Warrior` vs generic `Tank` accordingly.
    """

    name: str
    role: str
    damage_taken_hits: int
    healing_done_hits: int
    deaths: int = 0  # count of UNIT_DIED on this player in the run window
    class_spec: str | None = None  # e.g. "protection_warrior"; None if ACL off
    role_source: str = "behavioural"  # "combatant_info" or "behavioural"


def iter_combatant_info(
    log_path: Path,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> Iterator[tuple[str, int, frozenset[int]]]:
    """Yield (player_guid, spec_id, talent_spell_ids) for every COMBATANT_INFO line.

    COMBATANT_INFO is emitted once per player at CHALLENGE_MODE_START /
    ENCOUNTER_START — but *only when Advanced Combat Logging is enabled*.
    Logs with `ADVANCED_LOG_ENABLED,0` in the header emit zero of these events.

    talent_spell_ids is a frozenset of the middle values of each
    `(node_id, X, rank)` tuple in the talent block. CAUTION — despite the
    name, X is a trait-node-ENTRY id (~80k-137k observed across every ACL
    log in examples/), NOT a spell id: cross-referencing it against
    `talents.<name>.spell_id` in constants.yaml can never match (the id
    spaces are disjoint — same mismatch constants.yaml's Brewmaster ledger
    note documents), which is why `Character.total_armor()` falls back to
    the YAML talent loadout when the detected-id match applies nothing.
    Name and parsing behavior kept as-is for existing callers.

    Custom parse (not csv.reader) because the talent block uses
    `[(node,talent,rank),...]` syntax with commas inside brackets that
    confuse a naive csv split. The first 26 fields before the first `[`
    are all simple comma-separated values; spec_id is the 26th.
    """
    import re as _re

    _talent_re = _re.compile(r"\((\d+),(\d+),(\d+)\)")

    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            if "COMBATANT_INFO," not in line:
                continue
            sep = line.find("  ")
            if sep < 0:
                continue
            timestamp_str = line[:sep].strip()
            rest = line[sep + 2 :].strip()
            if not rest.startswith("COMBATANT_INFO,"):
                continue
            try:
                line_t = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S.%f").timestamp()
            except ValueError:
                continue
            if end_time_s is not None and line_t > end_time_s:
                break
            if start_time_s is not None and line_t < start_time_s:
                continue
            # The talent block opens with `[`. Everything before it is
            # comma-separated simple values. We need fields[1] (GUID) and
            # fields[25] (spec_id) from this prefix.
            bracket = rest.find("[")
            if bracket < 0:
                continue
            prefix = rest[:bracket].rstrip(",")
            parts = prefix.split(",")
            if len(parts) < 26:
                continue
            guid = parts[1]
            try:
                spec_id = int(parts[25])
            except ValueError:
                continue
            # Extract talent spell IDs from the first [...] block (talent tree).
            # Format: [(node_id, spell_id, rank), ...]. We want the middle value.
            # There are multiple bracket blocks (talents, PvP talents, gear...);
            # the first one is always the talent tree.
            end_bracket = rest.find("]", bracket)
            talent_block = rest[bracket : end_bracket + 1] if end_bracket > bracket else ""
            talent_spell_ids: frozenset[int] = frozenset(
                int(m.group(2)) for m in _talent_re.finditer(talent_block)
            )
            yield guid, spec_id, talent_spell_ids


# Signature racial ABILITY cast → simf race string (matches constants.yaml
# `racials.*` and the `Character.race` checks). Only races simf models a racial
# for are listed — and only those whose racial is a non-armor effect (HP / vers /
# DR) that the COMBATANT_INFO snapshot does NOT already include. Earthen's +10%
# armor is deliberately omitted: it's a passive (no cast signature), it's already
# folded into the snapshot's total armor, and the `human` default + `_backout_armor`
# keep its *effective* armor correct — detecting it would buy nothing.
_RACE_BY_RACIAL_CAST: dict[int, str] = {
    20549: "tauren",  # War Stomp        — Endurance +5% max HP
    255654: "highmountain_tauren",  # Bull Rush        — ~1% damage taken reduction
    20594: "dwarf",  # Stoneform        — Stoneform physical DR
    287712: "kul_tiran",  # Haymaker         — Brush It Off +1% versatility
}


def detect_race(
    log_path: Path,
    target_name: str,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
    max_bytes: int = 30_000_000,
) -> str | None:
    """Infer a player's race from a signature racial-ability cast in the window.

    COMBATANT_INFO carries no race field (it's a packed bit that varies by client
    build), so the only replay signal is the player CASTING their racial. Returns
    the simf race string the first time ``target_name`` is seen as the source of a
    mapped racial (``_RACE_BY_RACIAL_CAST``), else ``None`` — the caller falls back
    to ``"human"`` (today's behaviour).

    Best-effort by nature: racials are situational (War Stomp is a 2-min-CD stun),
    so a missing cast is NOT proof of race — it just means we can't improve on the
    default. Bounded by the run window and ``max_bytes`` so a racial-free run can't
    drive an unbounded scan. Single forward pass; returns on first match.
    """
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        bytes_read = 0
        for line in f:
            bytes_read += len(line)
            if bytes_read > max_bytes:
                break
            # Cheap prefilters before the CSV parse.
            if "SPELL_CAST_SUCCESS" not in line or target_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if parsed is None:
                continue
            time_s, event_type, fields = parsed
            if event_type != "SPELL_CAST_SUCCESS":
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            if start_time_s is not None and time_s < start_time_s:
                continue
            # fields: [srcGUID, srcName(1), srcFlags, srcRaidFlags, destGUID,
            #          destName, destFlags, destRaidFlags, spellId(8), ...]
            if len(fields) < 9 or fields[1] != target_name:
                continue
            try:
                sid = int(fields[8])
            except ValueError:
                continue
            race = _RACE_BY_RACIAL_CAST.get(sid)
            if race is not None:
                return race
    return None


def detect_party_roles(
    log_path: Path,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
    max_bytes: int = 30_000_000,
    early_exit_count: int = 5,
) -> list[PartyMember]:
    """Detect party members + their roles for a run window.

    Two-source classification:

    1. **Authoritative — COMBATANT_INFO.** When ACL is on, the log states
       each player's spec_id directly. We parse those, map spec → role,
       and label members `role_source="combatant_info"`.

    2. **Behavioural fallback.** When ACL is off (no COMBATANT_INFO emitted)
       we fall back to: tank = most damage-taken, healer = most heals cast,
       rest = DPS. Members get `role_source="behavioural"`.

    A single COMBATANT_INFO scan + a single damage/heal scan happen in
    sequence. Both honour `start_byte_offset` so detection on a deep run
    (167 MB into a 239 MB session log) skips the leading bytes.
    """
    # Pass 1 — COMBATANT_INFO. Cheap (5-ish lines per run). Builds a
    # guid → spec_id map authoritative for the in-window party.
    guid_to_spec: dict[str, int] = {}
    for guid, spec_id, _ in iter_combatant_info(
        log_path,
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        start_byte_offset=start_byte_offset,
    ):
        guid_to_spec[guid] = spec_id

    # Pass 2 — damage + heal events (+ UNIT_DIED). We track guid alongside
    # name because COMBATANT_INFO uses guid as the stable identifier and
    # damage events are how we translate guid → name + role-supporting
    # counts. Death tallying piggy-backs on the same scan but is NOT
    # affected by the early-exit below — once early-exit fires we
    # continue the loop scanning ONLY for UNIT_DIED so the death count
    # stays full-run accurate even though hit counts may be truncated.
    damage_counts: dict[str, int] = {}
    heal_counts: dict[str, int] = {}
    death_counts: dict[str, int] = {}
    name_for_guid: dict[str, str] = {}
    guid_for_name: dict[str, str] = {}
    classification_done = False
    bytes_read = 0
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            bytes_read += len(line)
            if end_time_s is None and bytes_read >= max_bytes:
                break
            is_unit_died = "UNIT_DIED," in line
            if classification_done and not is_unit_died:
                continue  # death-only fast path past early-exit
            is_damage = "_DAMAGE," in line
            is_heal = "_HEAL," in line
            if not (is_damage or is_heal or is_unit_died):
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if end_time_s is not None and time_s > end_time_s:
                break
            if start_time_s is not None and time_s < start_time_s:
                continue
            if event_type == "UNIT_DIED":
                # UNIT_DIED layout: destGUID, destName at fields[4..5].
                if len(fields) >= 6 and fields[4].startswith("Player-"):
                    name = fields[5]
                    if name and "-" in name:
                        death_counts[name] = death_counts.get(name, 0) + 1
                continue
            if len(fields) < 6:
                continue
            if event_type in DAMAGE_EVENTS:
                if fields[4].startswith("Player-"):
                    guid = fields[4]
                    name = fields[5]
                    if name and "-" in name:
                        damage_counts[name] = damage_counts.get(name, 0) + 1
                        name_for_guid.setdefault(guid, name)
                        guid_for_name.setdefault(name, guid)
            elif event_type in HEAL_EVENTS and fields[0].startswith("Player-"):
                guid = fields[0]
                name = fields[1]
                if name and "-" in name:
                    heal_counts[name] = heal_counts.get(name, 0) + 1
                    name_for_guid.setdefault(guid, name)
                    guid_for_name.setdefault(name, guid)
            if (
                len(damage_counts) >= early_exit_count
                and all(c > 10 for c in damage_counts.values())
                and any(c > 20 for c in heal_counts.values())
            ):
                classification_done = True

    # COMBATANT_INFO may name a player who logged no damage/heals in window
    # (e.g. healer in a long peaceful trash gap). Surface them anyway.
    all_names = (
        set(damage_counts)
        | set(heal_counts)
        | {name_for_guid[g] for g in guid_to_spec if g in name_for_guid}
    )
    if not all_names:
        return []

    # Authoritative roles via spec_id (when available).
    name_to_spec: dict[str, int] = {}
    name_to_role_from_spec: dict[str, str] = {}
    for name in all_names:
        name_guid = guid_for_name.get(name)
        if name_guid and name_guid in guid_to_spec:
            spec_id = guid_to_spec[name_guid]
            name_to_spec[name] = spec_id
            name_to_role_from_spec[name] = spec_to_role(spec_id)

    # Behavioural fallback only applies to names we couldn't spec-resolve.
    behavioural_tank: str | None = None
    behavioural_healer: str | None = None
    if any(n not in name_to_role_from_spec for n in all_names):
        unresolved = [n for n in all_names if n not in name_to_role_from_spec]
        # Tank picked from full set if no spec-tank exists; otherwise we
        # already have one and only fill remaining slots with behavioural DPS.
        already_have_tank = any(r == "tank" for r in name_to_role_from_spec.values())
        already_have_healer = any(r == "healer" for r in name_to_role_from_spec.values())
        if not already_have_tank and unresolved:
            candidate = max(unresolved, key=lambda n: damage_counts.get(n, 0))
            if damage_counts.get(candidate, 0) > 0:
                behavioural_tank = candidate
        if not already_have_healer:
            healer_pool = [
                n for n in unresolved if n != behavioural_tank and heal_counts.get(n, 0) >= 10
            ]
            if healer_pool:
                behavioural_healer = max(healer_pool, key=lambda n: heal_counts.get(n, 0))

    members: list[PartyMember] = []
    used: set[str] = set()

    def _emit(name: str, role: str, source: str) -> None:
        if name in used:
            return
        spec_id = name_to_spec.get(name)
        class_spec = SPEC_ID_TO_CLASS_SPEC.get(spec_id) if spec_id is not None else None
        members.append(
            PartyMember(
                name=name,
                role=role,
                damage_taken_hits=damage_counts.get(name, 0),
                healing_done_hits=heal_counts.get(name, 0),
                deaths=death_counts.get(name, 0),
                class_spec=class_spec,
                role_source=source,
            )
        )
        used.add(name)

    # Order: tank → healer → DPS-by-damage-taken.
    tanks_from_spec = [n for n, r in name_to_role_from_spec.items() if r == "tank"]
    healers_from_spec = [n for n, r in name_to_role_from_spec.items() if r == "healer"]
    dps_from_spec = [n for n, r in name_to_role_from_spec.items() if r == "dps"]

    for n in tanks_from_spec:
        _emit(n, "tank", "combatant_info")
    if behavioural_tank and behavioural_tank not in used:
        _emit(behavioural_tank, "tank", "behavioural")
    for n in healers_from_spec:
        _emit(n, "healer", "combatant_info")
    if behavioural_healer and behavioural_healer not in used:
        _emit(behavioural_healer, "healer", "behavioural")
    for n in sorted(dps_from_spec, key=lambda n: -damage_counts.get(n, 0)):
        _emit(n, "dps", "combatant_info")
    # Remaining unresolved names = behavioural DPS, by damage taken.
    remaining = sorted(
        (n for n in all_names if n not in used),
        key=lambda n: -damage_counts.get(n, 0),
    )
    for n in remaining:
        _emit(n, "dps", "behavioural")

    return members
