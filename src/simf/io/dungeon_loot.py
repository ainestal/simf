"""Loader for ``data/dungeon_loot.yaml`` — M+ dungeon loot tables.

Reads the curated YAML once per process, returns ``slot -> dungeon_id ->
list[ItemSpec]`` so the slot dialog can pull "items the user could chase
from a prog-filtered dungeon" with the same shape it uses for bag and
vault alternatives. ItemSpec is deliberate — every downstream consumer
(Wowhead stat lookup, display_name, _icon_for_item, ΔeHP scoring)
already knows that shape.

The YAML is keyed by ``dungeon_id`` because that's what the prog-filter
multiselect emits. ``items_for_slot()`` flips the view so the slot
dialog can scan a single slot quickly.

Paired slots (finger1/finger2, trinket1/trinket2): the YAML stores
items under the canonical-named slot (``finger1``, ``trinket1``); the
``alternatives_for_slot`` caller handles the partner merge the same way
it does for bag + vault.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from simf.io.simc_import import ItemSpec

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "dungeon_loot.yaml"


@lru_cache(maxsize=1)
def load_dungeon_loot() -> dict[str, list[ItemSpec]]:
    """Parse the YAML once and cache. Maps ``dungeon_id -> list[ItemSpec]``.

    Empty dict if the file is missing or malformed — the slot dialog
    treats "no loot data" the same as "no items for this slot."
    """
    if not _DATA_PATH.exists():
        return {}
    try:
        with open(_DATA_PATH) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}
    dungeons = data.get("dungeons") or {}
    result: dict[str, list[ItemSpec]] = {}
    for dungeon_id, payload in dungeons.items():
        items = (payload or {}).get("items") or []
        specs: list[ItemSpec] = []
        for raw in items:
            try:
                specs.append(
                    ItemSpec(
                        slot=str(raw["slot"]),
                        item_id=int(raw["id"]),
                        name=raw.get("name"),
                        ilvl=int(raw["ilvl"]) if raw.get("ilvl") else None,
                        bonus_ids=list(raw.get("bonus_ids") or []),
                    )
                )
            except (KeyError, TypeError, ValueError):
                # Tolerate malformed individual rows — drop them rather
                # than crashing the loader.
                continue
        result[str(dungeon_id)] = specs
    return result


def items_for_slot(
    slots: str | set[str],
    dungeon_ids: list[str],
    *,
    loot: dict[str, list[ItemSpec]] | None = None,
) -> list[tuple[str, ItemSpec]]:
    """Return ``(dungeon_id, item)`` pairs matching ``slots`` from each named dungeon.

    ``slots`` accepts a single slot name or a set — pass
    ``equivalent_slots("trinket1")`` to honor the paired-slot rule the
    way ``alternatives_for_slot`` does for bag + vault. The pairing
    preserves dungeon attribution so the slot dialog can label each row
    with which dungeon drops it. Order: iterate ``dungeon_ids`` in
    caller order, then items in YAML order.
    """
    wanted = {slots} if isinstance(slots, str) else set(slots)
    table = loot if loot is not None else load_dungeon_loot()
    out: list[tuple[str, ItemSpec]] = []
    for d_id in dungeon_ids:
        for item in table.get(d_id, []):
            if item.slot in wanted:
                out.append((d_id, item))
    return out
