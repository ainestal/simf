"""Slot-grouped gear list for Surface 1.

Pure data-shaping helper: takes a dict[slot, ItemSpec] and produces 16
SlotRow records in Blizzard paperdoll order. The Streamlit layer reads these
rows and emits one row widget per entry.

Warnings (missing enchant, empty socket, sub-optimal stat pair) are computed
by an optional callback so the data model stays slim and the rule set can
evolve without changing this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

CANONICAL_SLOT_ORDER: list[str] = [
    "head",
    "neck",
    "shoulder",
    "back",
    "chest",
    "wrist",
    "hands",
    "waist",
    "legs",
    "feet",
    "finger1",
    "finger2",
    "trinket1",
    "trinket2",
    "main_hand",
    "off_hand",
]

SLOT_LABELS: dict[str, str] = {
    "head": "Helm",
    "neck": "Neck",
    "shoulder": "Shoulders",
    "back": "Cloak",
    "chest": "Chest",
    # Cosmetic slots — never carry stats, so they're not in CANONICAL_SLOT_ORDER
    # (no rows/picks/alternatives). The dark paperdoll renders them as empty
    # placeholder cards purely to balance the left rail to 8/8, matching the
    # in-game character sheet (examples/screenshots/ui.png).
    "shirt": "Shirt",
    "tabard": "Tabard",
    "wrist": "Bracers",
    "hands": "Gloves",
    "waist": "Belt",
    "legs": "Legs",
    "feet": "Boots",
    "finger1": "Ring 1",
    "finger2": "Ring 2",
    "trinket1": "Trinket 1",
    "trinket2": "Trinket 2",
    "main_hand": "Main hand",
    "off_hand": "Off hand",
}


@dataclass(frozen=True)
class SlotRow:
    slot: str
    slot_label: str
    item: object | None  # ItemSpec or None
    name: str | None
    ilvl: int | None
    warnings: list[str] = field(default_factory=list)


def display_name(item: object) -> str:
    """Item name with a stable fallback for items missing a resolved name."""
    name = getattr(item, "name", None)
    if name:
        return name
    return f"Item #{getattr(item, 'item_id', '?')}"


_WOWHEAD_ICON_BASE = "https://wow.zamimg.com/images/wow/icons"


def wowhead_icon_url(icon_name: str | None, size: str = "medium") -> str | None:
    """Build a zamimg icon URL from a Wowhead icon slug.

    Sizes: ``small`` (18px), ``medium`` (36px), ``large`` (56px). Returns
    ``None`` for empty input so callers don't have to short-circuit.
    """
    if not icon_name:
        return None
    return f"{_WOWHEAD_ICON_BASE}/{size}/{icon_name}.jpg"


def wowhead_url(item: object) -> str | None:
    """Build a Wowhead tooltip URL for an ItemSpec. ``None`` if item has no id.

    Bonus IDs scale the tooltip to the player's actual ilvl. Without them
    Wowhead shows base stats, which mislead by 10-20 ilvls on raid drops.
    """
    item_id = getattr(item, "item_id", 0)
    if not item_id:
        return None
    bonus_ids = getattr(item, "bonus_ids", None) or []
    url = f"https://www.wowhead.com/item={item_id}"
    if bonus_ids:
        url += "?bonus=" + ":".join(str(b) for b in bonus_ids)
    return url


_STAT_DISPLAY: list[tuple[str, str]] = [
    ("strength", "Str"),
    ("agility", "Agi"),
    ("stamina", "Sta"),
    ("armor_from_gear", "Arm"),
    ("haste_rating", "Hst"),
    ("crit_rating", "Crit"),
    ("mastery_rating", "Mst"),
    ("versatility_rating", "Vers"),
    # Tertiaries — render after secondaries so they don't crowd the
    # primary stat line. Brutoh's in-game Bifurcation Band tooltip
    # surfaces +43 Avoidance; the slot-dialog card now matches.
    ("avoidance_rating", "Avd"),
    ("leech_rating", "Lch"),
    ("speed_rating", "Spd"),
]


def format_item_stats(stats: dict[str, int] | None) -> str:
    """Compact ' · '-separated stat line. Empty string if stats is missing.

    Order is fixed (primary → armor → secondaries) so two same-slot items
    line up visually for at-a-glance comparison.
    """
    if not stats:
        return ""
    parts = [f"{label} {stats[key]:,}" for key, label in _STAT_DISPLAY if stats.get(key)]
    return " · ".join(parts)


def build_slot_rows(
    equipped: dict[str, object],
    warnings_fn: Callable[[str, object | None], list[str]] | None = None,
) -> list[SlotRow]:
    """Build 16 SlotRow records in Blizzard paperdoll order.

    Args:
        equipped: dict[slot, ItemSpec]. Missing slots render as empty.
        warnings_fn: optional callable(slot, item) -> list[str]. Called for
            every slot, including empty ones — caller decides whether an
            empty slot is itself a warning.
    """
    rows: list[SlotRow] = []
    for slot in CANONICAL_SLOT_ORDER:
        item = equipped.get(slot)
        warnings = warnings_fn(slot, item) if warnings_fn else []
        rows.append(
            SlotRow(
                slot=slot,
                slot_label=SLOT_LABELS[slot],
                item=item,
                name=getattr(item, "name", None) if item else None,
                ilvl=getattr(item, "ilvl", None) if item else None,
                warnings=list(warnings),
            )
        )
    return rows
