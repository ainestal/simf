"""Real Protection Warrior talent catalog — every real talent entry's name/
spell/tree/modeled-status, sourced from SimulationCraft's committed
``trait_data.inc`` and joined against ``data/talents.yaml``'s modeled-talent
spell ids, committed as a static seed: this data changes far less often
than gear, so a fixed fallback means decode never blocks on a network
call. Regenerate via ``scripts/fetch_talent_tree.py`` when Midnight's
talents change.

The ``entry_id_to_modeled_talent`` table is what
``io/talent_decoder.decode_combatant_info_entry_ids`` actually uses — a
real ACL-on combat log's COMBATANT_INFO talent block carries
``(node_id, entry_id, rank)`` triples, and ``entry_id`` (SimC calls it
``id_trait_node_entry``) is confirmed byte-for-byte against a real log
(Reinforced Plates' entry_id 112235 appears verbatim in
``examples/WoWCombatLog-050626_153703.txt``). This lookup is a plain set-
membership join, independent of node ORDER — unlike decoding the in-game
``talents=`` loadout STRING, which needs the exact node sequence and is
NOT yet reliable (see ``talent_string_codec.py``'s module docstring).
``entry_catalog`` is the same join's superset (every real entry, not just
the modeled ones) — it powered a "Your talents, explained" UI panel that
was removed 2026-07-19 (talent UI cleanup); no live consumer reads it
today, kept as-is since it's cheap, static, and still a faithful decode.

This module used to also carry the real tree's spatial layout (node
row/col, prerequisite edges, hero sub-tree structure, point budgets, and a
bulk icon backfill for non-modeled nodes) for an interactive talent-tree
builder. That feature was removed 2026-07-15 after a unanimous multi-
persona review found it structurally unfixable (only 13 of ~250 rendered
nodes ever affected a simulated number) — see
``docs/validation/talent_string_decoder_2026_07_13.md`` and the
``talent_tree_builder_3phase_2026_07_13`` memory note for the full story.
That data was deleted with it (from both this loader and the committed
JSON) since nothing else ever consumed it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "talent_trees"
_PROTECTION_WARRIOR_FILE = _DATA_DIR / "protection_warrior.json"


@dataclass(frozen=True)
class CatalogEntry:
    """One real talent entry — every one simf knows the name of, whether or
    not it's mechanically modeled. Powers "Your talents, explained": a
    player's real build should be fully explained, not silently truncated
    to the subset the sim happens to use."""

    name: str
    spell_id: int | None
    tree: str  # "class" | "spec" | "hero"
    modeled_talent_id: str | None


@dataclass(frozen=True)
class TalentTree:
    entry_id_to_modeled_talent: dict[int, str]
    entry_catalog: dict[int, CatalogEntry]


@lru_cache(maxsize=1)
def load_protection_warrior_tree() -> TalentTree:
    with _PROTECTION_WARRIOR_FILE.open() as f:
        raw = json.load(f)
    return TalentTree(
        entry_id_to_modeled_talent={
            int(eid): name for eid, name in raw["entry_id_to_modeled_talent"].items()
        },
        entry_catalog={
            int(eid): CatalogEntry(
                name=info["name"],
                spell_id=info.get("spell_id"),
                tree=info["tree"],
                modeled_talent_id=info.get("modeled_talent_id"),
            )
            for eid, info in raw["entry_catalog"].items()
        },
    )
