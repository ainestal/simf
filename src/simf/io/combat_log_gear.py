"""Full COMBATANT_INFO parser (gear + ratings, for character hydration)."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CombatantInfoEquippedItem:
    """One equipped slot as carried by COMBATANT_INFO.

    Mirrors the fields downstream consumers (simc_import.ItemSpec, gear surface
    pickers) need to render the slot. Slot order in the COMBATANT_INFO gear
    block is fixed by Blizzard (verified 2026-05-25 against
    examples/WoWCombatLog-051026_073906.txt vs examples/brutoh.simc, every
    item_id + enchant_id + bonus_ids matches exactly): HEAD, NECK, SHOULDER,
    SHIRT, CHEST, WAIST, LEGS, FEET, WRIST, HANDS, FINGER1, FINGER2, TRINKET1,
    TRINKET2, BACK, MAIN_HAND, OFF_HAND, TABARD — 18 slots.
    """

    slot: str
    item_id: int
    ilvl: int
    enchant_id: int | None
    bonus_ids: tuple[int, ...]
    gem_ids: tuple[int, ...]


@dataclass(frozen=True)
class CombatantInfoFull:
    """Everything COMBATANT_INFO carries for one player, post-parse.

    Field positions verified empirically against Brutoh's ACL log
    (examples/WoWCombatLog-051026_073906.txt, COMBATANT_INFO line for
    Brutoh-Uldum-EU = Protection Warrior, spec 73):

      [3]    strength       (2182, matches brutoh.yaml)
      [4]    agility        (428)
      [5]    stamina        (35884, ≈ brutoh.yaml 34176 + raid buffs)
      [9]    dodge_rating   (623)
      [10]   parry_rating   (0, matches brutoh.yaml: 0)
      [11-13] crit_rating  (melee/ranged/spell variants, always equal in
              Midnight — confirmed across 386 COMBATANT_INFO lines /
              multiple specs in examples/, cross-checked against Brutoh's
              own character-sheet crit%: log 623-640 rating vs. sheet's
              640 (13.91% x 46 rating-per-pct). Previously misread from
              [14-15], which are small tertiary rolls (speed/leech
              territory, 0-300ish) — nowhere near a tank's real crit
              rating.
      [14-15] speed/leech (tertiary ratings, not consumed here)
      [16-18] haste_rating (melee/ranged/spell variants — we pick max)
      [20]   mastery_rating (396)
      [21-23] versatility_rating (damage_done/taken/heals — we pick max)
      [24]   total_armor    (5517, matches brutoh.yaml 5015 × 1.10 Earthen)
      [25]   spec_id        (73 = Protection Warrior)

    NOTE: [24] is *post-racial* total armor — for `Character.armor_from_gear`
    callers should back out Earthen 1.10× when race=earthen. We expose the
    raw value and let the hydrator choose; this struct stays close to the
    log.
    """

    guid: str
    spec_id: int
    talent_spell_ids: frozenset[int]
    strength: int
    agility: int
    stamina: int
    intellect: int
    dodge_rating: int
    parry_rating: int
    crit_rating: int
    haste_rating: int
    mastery_rating: int
    versatility_rating: int
    total_armor: int
    equipped: tuple[CombatantInfoEquippedItem, ...]


# Slot order in the COMBATANT_INFO gear block (fixed by Blizzard). See
# CombatantInfoEquippedItem docstring for the empirical verification.
_COMBATANT_INFO_SLOT_ORDER = (
    "head",
    "neck",
    "shoulder",
    "shirt",
    "chest",
    "waist",
    "legs",
    "feet",
    "wrist",
    "hands",
    "finger1",
    "finger2",
    "trinket1",
    "trinket2",
    "back",
    "main_hand",
    "off_hand",
    "tabard",
)


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split a string on `sep` only at top level of () and [] nesting.

    Combat-log COMBATANT_INFO uses parenthesised tuples nested inside
    bracketed lists; a naive `.split(',')` mangles them. This handles both
    bracket types so a single pass works for prefix fields, gear blocks
    (`[(id,ilvl,(enchants),(bonuses),(gems)),…]`) and tuple items alike.
    """
    out: list[str] = []
    depth = 0
    start = 0
    for idx, ch in enumerate(s):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(s[start:idx])
            start = idx + 1
    out.append(s[start:])
    return out


def _parse_int_tuple(s: str) -> tuple[int, ...]:
    """Parse `(a,b,c)` → (a,b,c). Empty `()` → ()."""
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    if not s.strip():
        return ()
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(out)


def _parse_gem_ids(s: str) -> tuple[int, ...]:
    """Parse the gear block's gem sub-tuple: `(gemId,gemIlvl,...)` pairs.

    Blizzard logs each socketed gem as a (gemId, gemIlvl) PAIR, not a bare
    id — confirmed empirically against examples/ (every observed gem
    sub-tuple across the corpus has length 0, 2, or 4, never odd; e.g. a
    single ilvl-295 gem logs as `(240983,295)`, a double-gemmed item as
    `(240983,295,240898,295)`). Only the even-indexed slot of each pair is
    the actual gem id — the odd slot is the gem's item level, which is not
    itself a gem and must not be treated as a second socketed gem.
    """
    ids = _parse_int_tuple(s)
    return ids[::2]


def _parse_combatant_info_gear_block(block: str) -> tuple[CombatantInfoEquippedItem, ...]:
    """Parse the gear `[(id,ilvl,(enchants),(bonuses),(gems)),...]` block.

    Returns one CombatantInfoEquippedItem per slot in _COMBATANT_INFO_SLOT_ORDER.
    Empty slots (item_id=0) are preserved with empty bonus/gem tuples so the
    slot indexing aligns with the canonical order. Off-by-one in the slot
    table would silently misplace every item, so we keep this strict.
    """
    block = block.strip()
    if not (block.startswith("[") and block.endswith("]")):
        return ()
    inner = block[1:-1]
    item_strs = _split_top_level(inner, ",")
    items: list[CombatantInfoEquippedItem] = []
    for i, raw in enumerate(item_strs):
        raw = raw.strip()
        if not raw.startswith("(") or not raw.endswith(")"):
            continue
        # Strip outer parens, split inner at top-level commas only.
        fields = _split_top_level(raw[1:-1], ",")
        if len(fields) < 2:
            continue
        try:
            item_id = int(fields[0].strip())
            ilvl = int(fields[1].strip())
        except ValueError:
            continue
        enchants = _parse_int_tuple(fields[2]) if len(fields) > 2 else ()
        bonus_ids = _parse_int_tuple(fields[3]) if len(fields) > 3 else ()
        gems = _parse_gem_ids(fields[4]) if len(fields) > 4 else ()
        # First enchant slot is the meaningful one; permanent enchants live
        # at index 0, temporary enchants at 1/2 (oils/runes). Match SimC
        # /simc export semantics which writes `enchant_id=<first>`.
        enchant_id: int | None = enchants[0] if enchants and enchants[0] else None
        slot = (
            _COMBATANT_INFO_SLOT_ORDER[i] if i < len(_COMBATANT_INFO_SLOT_ORDER) else f"unknown_{i}"
        )
        items.append(
            CombatantInfoEquippedItem(
                slot=slot,
                item_id=item_id,
                ilvl=ilvl,
                enchant_id=enchant_id,
                bonus_ids=bonus_ids,
                gem_ids=gems,
            )
        )
    return tuple(items)


def iter_combatant_info_full(
    log_path: Path,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> Iterator[CombatantInfoFull]:
    """Full COMBATANT_INFO parse — gear + ratings, not just spec_id.

    Used by the character-hydrator path (`io/character_from_combatant_info.py`)
    to populate a Character from the log directly, skipping the SimC paste.
    The lightweight ``iter_combatant_info`` triple is kept for hot paths
    (party-role detection, calibrate-k) that don't need gear.

    Each COMBATANT_INFO line carries a fixed-position prefix (26 simple
    comma-separated fields) followed by four bracket/paren blocks:
      [talents]  (pvp_talents)  [gear]  [auras]
    The talent block uses `[(node,spell,rank),...]` and is comma-internal;
    the PvP-talents tuple is single-deep; the gear block is two-deep
    `[(id,ilvl,(enchants),(bonuses),(gems)),...]`. A general split-on-
    top-level-comma walker handles all three with no special-cases.
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
            fields = _split_top_level(rest, ",")
            # Need at least 29 fields (up through gear block). Anything
            # shorter is malformed; skip rather than crash.
            if len(fields) < 29:
                continue
            try:
                guid = fields[1]
                strength = int(fields[3])
                agility = int(fields[4])
                stamina = int(fields[5])
                intellect = int(fields[6])
                dodge_rating = int(fields[9])
                parry_rating = int(fields[10])
                # Three crit / haste / vers fields each (different sub-stats
                # per school in WoW's internal accounting). For a M+ tank
                # they're effectively equal; pick the max defensively in
                # case a future spec splits them.
                crit_rating = max(int(fields[11]), int(fields[12]), int(fields[13]))
                haste_rating = max(int(fields[16]), int(fields[17]), int(fields[18]))
                mastery_rating = int(fields[20])
                versatility_rating = max(int(fields[21]), int(fields[22]), int(fields[23]))
                total_armor = int(fields[24])
                spec_id = int(fields[25])
            except (ValueError, IndexError):
                continue
            talent_spell_ids: frozenset[int] = frozenset(
                int(m.group(2)) for m in _talent_re.finditer(fields[26])
            )
            equipped = _parse_combatant_info_gear_block(fields[28])
            yield CombatantInfoFull(
                guid=guid,
                spec_id=spec_id,
                talent_spell_ids=talent_spell_ids,
                strength=strength,
                agility=agility,
                stamina=stamina,
                intellect=intellect,
                dodge_rating=dodge_rating,
                parry_rating=parry_rating,
                crit_rating=crit_rating,
                haste_rating=haste_rating,
                mastery_rating=mastery_rating,
                versatility_rating=versatility_rating,
                total_armor=total_armor,
                equipped=equipped,
            )
