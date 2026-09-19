#!/usr/bin/env python3
"""One-time generation script: build ``data/talent_trees/protection_warrior.json``
— the real talent catalog ``io/talent_decoder.py`` joins a real ACL-on log's
COMBATANT_INFO entry ids against, and ``talent_build_explain.py``'s "Your
talents, explained" panel displays.

Sourced from SimulationCraft's committed ``trait_data.inc`` (``midnight``
branch) — the only source found that carries ``entry_id`` (SimC's
``id_trait_node_entry``) alongside a spell id and display name in the same
row. A real ACL-on combat log's talent block carries ``(node_id, entry_id,
rank)`` triples where ``entry_id`` is this exact id — confirmed live: a grep
of ``examples/WoWCombatLog-050626_153703.txt`` found the literal triple
``(90368,112235,2)``, matching Reinforced Plates' entry_id (112235) exactly.
No Blizzard API credentials needed — ``trait_data.inc`` is a public GitHub
raw file.

This script is NOT a runtime dependency — ``talent_tree_data.py`` loads the
committed JSON output, never re-fetches. Matches this repo's existing
"static seed, not live-fetched" convention for ``data/talents.yaml``.

Re-run only when Midnight's talents change (a new season/patch):

    python3 scripts/fetch_talent_tree.py

Historical note (2026-07-15): this script used to ALSO fetch Blizzard's
Game Data API (``/data/wow/talent-tree/850/playable-specialization/73``)
for the real tree's spatial layout (node row/col, prerequisite edges, hero
sub-tree structure, point budgets) to power an interactive talent-tree
builder. That feature was removed after a unanimous multi-persona review
found it structurally unfixable — see ``talent_tree_builder_3phase_2026_07_13``
memory note. That fetch path (and the credentials it needed) was deleted
with it, since nothing else ever consumed that data. ``scripts/
fetch_talent_icons.py`` (a bulk icon backfill for the same removed feature)
was deleted outright, not just trimmed.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

_TRAIT_DATA_URL = (
    "https://raw.githubusercontent.com/simulationcraft/simc/midnight/"
    "engine/dbc/generated/trait_data.inc"
)
# Row schema inferred from the fetched file (SimC ships no separate struct
# header for this generated file) — see docs/validation/
# talent_string_decoder_2026_07_13.md for how this was derived and verified
# (node_id/spell_id cross-checked against a live Blizzard API pull; the
# id_trait_node_entry column verified byte-for-byte against a real ACL-on
# combat log's COMBATANT_INFO talent block).
_SIMC_ROW_RE = re.compile(
    r"\{\s*(?P<tree_index>\d+),\s*(?P<id_class>\d+),\s*(?P<entry_id>\d+),\s*\d+,\s*\d+,\s*\d+,\s*"
    r"\d+,\s*(?P<spell_id>\d+),\s*\d+,\s*\d+,\s*\d+,\s*\d+,\s*\d+,\s*\"(?P<name>[^\"]*)\","
    r"\s*\{[^}]*\},\s*\{[^}]*\},\s*\d+,\s*\d+\s*\}"
)
_WARRIOR_CLASS_ID = 1
# tree_index 1=class, 2=spec, 3/4=hero (both bucketed "hero" here — this
# catalog is DISPLAY-only, so the class/spec/hero split just groups "Your
# talents, explained"'s output; it doesn't need the finer per-hero-tree
# distinction the removed interactive tree once did).
_TREE_INDEX_BUCKET = {1: "class", 2: "spec", 3: "hero", 4: "hero"}

_TALENTS_YAML = Path(__file__).resolve().parent.parent / "src" / "simf" / "data" / "talents.yaml"
_OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "simf"
    / "data"
    / "talent_trees"
    / "protection_warrior.json"
)


def fetch_entry_catalog(modeled_by_spell_id: dict[int, str]) -> dict[int, dict[str, Any]]:
    """entry_id (SimC's ``id_trait_node_entry``) -> {name, spell_id, tree,
    modeled_talent_id}, for EVERY real Warrior talent entry — not just the
    modeled ones.

    This is what the "Your talents, explained" panel needs to show a
    player's FULL real build (every node they have, not just the subset
    simf models) — ``entry_id_to_modeled_talent`` (a strict subset) can't
    do that alone since it only knows entry ids that map to a modeled
    talent, dropping everything else silently.
    """
    with urllib.request.urlopen(_TRAIT_DATA_URL, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    out: dict[int, dict[str, Any]] = {}
    for m in _SIMC_ROW_RE.finditer(text):
        if int(m.group("id_class")) != _WARRIOR_CLASS_ID:
            continue
        spell_id = int(m.group("spell_id"))
        out[int(m.group("entry_id"))] = {
            "name": m.group("name"),
            "spell_id": spell_id,
            "tree": _TREE_INDEX_BUCKET.get(int(m.group("tree_index")), "unknown"),
            "modeled_talent_id": modeled_by_spell_id.get(spell_id),
        }
    return out


def load_modeled_spell_ids() -> dict[int, str]:
    """spell_id -> modeled talent key, from the existing talents.yaml (the
    join key already used by ``optimizer/talent_icons.py``)."""
    with _TALENTS_YAML.open() as f:
        data = yaml.safe_load(f) or {}
    out: dict[int, str] = {}
    for talent_id, meta in data.get("protection_warrior", {}).items():
        sid = meta.get("wowhead_spell_id")
        if sid:
            out[int(sid)] = talent_id
    return out


def main() -> int:
    modeled = load_modeled_spell_ids()

    print("Fetching SimC trait_data.inc for the entry catalog...", file=sys.stderr)
    entry_catalog = fetch_entry_catalog(modeled)
    entry_id_to_modeled_talent = {
        eid: info["modeled_talent_id"]
        for eid, info in entry_catalog.items()
        if info["modeled_talent_id"]
    }
    print(
        f"entry catalog covers {len(entry_catalog)} real talent entries; "
        f"{len(entry_id_to_modeled_talent)} distinct entry ids map to "
        f"{len(set(entry_id_to_modeled_talent.values()))}/{len(modeled)} modeled talents",
        file=sys.stderr,
    )
    missing = set(modeled.values()) - set(entry_id_to_modeled_talent.values())
    if missing:
        print(f"WARNING: modeled talents not found in the tree data: {missing}", file=sys.stderr)

    tree_json = {
        "entry_catalog": entry_catalog,
        "entry_id_to_modeled_talent": entry_id_to_modeled_talent,
        "source": "SimulationCraft trait_data.inc (midnight branch), joined against "
        "data/talents.yaml spell ids — see scripts/fetch_talent_tree.py",
        "generated": "2026-07-15",
    }

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(tree_json, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {_OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
