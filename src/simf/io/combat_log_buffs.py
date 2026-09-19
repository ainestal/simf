"""BUFF/DEBUFF window reconstruction — the input to the defensive-coverage join."""

from pathlib import Path

from simf.io.combat_log_core import parse_combat_log_line

_AURA_APPLY_EVENTS = frozenset(
    {"SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH", "SPELL_AURA_APPLIED_DOSE"}
)


def detect_active_buffs(
    log_path: Path,
    target_name: str,
    buff_spell_ids: frozenset[int] | set[int],
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> frozenset[int]:
    """Return the subset of `buff_spell_ids` seen applied to `target_name` as a
    BUFF within the run window.

    Used to gate the hero-talent mitigation ledger on a talent's BUFF aura:
    COMBATANT_INFO carries trait-node-ENTRY ids (not spell ids), so the buff
    aura is the only reliable replay signal that a passive-DR talent is active.
    Single forward pass; breaks early once every requested id is seen.
    """
    if not buff_spell_ids:
        return frozenset()
    wanted = {int(s) for s in buff_spell_ids}
    found: set[int] = set()
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            # Cheap prefilters before the CSV parse.
            if "SPELL_AURA_" not in line or target_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if parsed is None:
                continue
            time_s, event_type, fields = parsed
            if event_type not in _AURA_APPLY_EVENTS:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            if start_time_s is not None and time_s < start_time_s:
                continue
            # fields: [srcGUID, srcName, srcFlags, srcRaidFlags, destGUID,
            #          destName(5), destFlags, destRaidFlags, spellId(8),
            #          spellName, spellSchool, auraType(11)]
            if len(fields) < 12 or fields[5] != target_name or fields[11] != "BUFF":
                continue
            try:
                sid = int(fields[8])
            except ValueError:
                continue
            if sid in wanted:
                found.add(sid)
                if found == wanted:
                    break
    return frozenset(found)


# Aura events that OPEN or extend a window; these CLOSE one. SPELL_AURA_REMOVED
# is the only close that matters for tank defensives — they expire or are
# cancelled, never CC-broken — and it carries auraType at the standard field
# index 11. SPELL_AURA_BROKEN (melee break) shares that layout and is included
# for completeness. SPELL_AURA_BROKEN_SPELL is deliberately EXCLUDED: it inserts
# an extra (spellId, spellName, school) triple before auraType, so the field-11
# BUFF/DEBUFF check below would silently reject it and never close the window
# (an over-credit) — and it cannot legitimately fire on a defensive buff anyway.
# SPELL_AURA_REMOVED_DOSE is a stack decrement, NOT a close — the buff is still up.
_AURA_REMOVE_EVENTS = frozenset({"SPELL_AURA_REMOVED", "SPELL_AURA_BROKEN"})


def parse_self_buff_windows(
    log_path: Path,
    target_name: str,
    buff_spell_ids: frozenset[int] | set[int],
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> dict[int, list[tuple[float, float]]]:
    """Reconstruct [(start_s, end_s), …] BUFF windows on `target_name`.

    Unlike :func:`detect_active_buffs` (which only reports *presence* and
    early-exits), this builds the full interval list per spell — the input to
    the defensive-coverage join (``core.coaching``). APPLIED/REFRESH/DOSE open
    (or keep open) a window; REMOVED/BROKEN close it; an aura still open at
    ``end_time_s`` is closed there. A REMOVED with no matching open (buff was
    up before the window started) is skipped — we never fabricate a window, so
    coverage can only ever be under-credited, never over-credited.

    Single forward pass; only BUFF auras on `target_name` with a requested
    spell id are considered.
    """
    wanted = {int(s) for s in buff_spell_ids}
    if not wanted:
        return {}
    windows: dict[int, list[tuple[float, float]]] = {sid: [] for sid in wanted}
    open_at: dict[int, float] = {}
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            if "SPELL_AURA_" not in line or target_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if parsed is None:
                continue
            time_s, event_type, fields = parsed
            if end_time_s is not None and time_s > end_time_s:
                break
            if start_time_s is not None and time_s < start_time_s:
                continue
            if len(fields) < 12 or fields[5] != target_name or fields[11] != "BUFF":
                continue
            try:
                sid = int(fields[8])
            except ValueError:
                continue
            if sid not in wanted:
                continue
            if event_type in _AURA_APPLY_EVENTS:
                open_at.setdefault(sid, time_s)
            elif event_type in _AURA_REMOVE_EVENTS:
                start = open_at.pop(sid, None)
                if start is not None:
                    windows[sid].append((start, time_s))
    # Close any aura still open at the run boundary.
    if end_time_s is not None:
        for sid, start in open_at.items():
            windows[sid].append((start, end_time_s))
    return windows


def parse_source_debuff_windows(
    log_path: Path,
    applier_name: str,
    debuff_spell_id: int,
    *,
    duration_s: float | None = None,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> dict[str, list[tuple[float, float]]]:
    """Reconstruct per-enemy DEBUFF windows for a debuff `applier_name` applies.

    Keyed by the *enemy* GUID the debuff landed on, so the coverage join can
    ask "did the mob that hit me have this debuff active?" — never a blanket
    window. Used for Demoralizing Shout (1160): it reduces the *enemy's*
    damage, so it only "covers" a hit if that hit's own source carried it.

    `duration_s`, when given, caps an unclosed window (some logs drop the
    REMOVED on a mob that dies) at ``open + duration_s``. Single forward pass.
    """
    sid_wanted = int(debuff_spell_id)
    windows: dict[str, list[tuple[float, float]]] = {}
    open_at: dict[str, float] = {}
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        for line in f:
            if "SPELL_AURA_" not in line or applier_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if parsed is None:
                continue
            time_s, event_type, fields = parsed
            if end_time_s is not None and time_s > end_time_s:
                break
            if start_time_s is not None and time_s < start_time_s:
                continue
            # fields: srcGUID(0) srcName(1) … destGUID(4) destName(5) …
            #         spellId(8) spellName(9) school(10) auraType(11)
            if len(fields) < 12 or fields[1] != applier_name or fields[11] != "DEBUFF":
                continue
            try:
                sid = int(fields[8])
            except ValueError:
                continue
            if sid != sid_wanted:
                continue
            enemy_guid = fields[4]
            if event_type in _AURA_APPLY_EVENTS:
                open_at.setdefault(enemy_guid, time_s)
            elif event_type in _AURA_REMOVE_EVENTS:
                start = open_at.pop(enemy_guid, None)
                if start is not None:
                    windows.setdefault(enemy_guid, []).append((start, time_s))
    # Close still-open debuffs at open+duration (preferred) or the run end.
    for enemy_guid, start in open_at.items():
        if duration_s is not None:
            close = start + duration_s
        elif end_time_s is not None:
            close = end_time_s
        else:
            continue
        windows.setdefault(enemy_guid, []).append((start, close))
    return windows
