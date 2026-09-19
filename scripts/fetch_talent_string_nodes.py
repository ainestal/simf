#!/usr/bin/env python3
"""One-time generation script: build ``data/talent_trees/{spec}_string_nodes.json``
for Protection Warrior and Guardian Druid — the ordered per-node data
``io/talent_string_codec.decode_full_loadout`` needs to walk a real
``talents=`` export string bit-for-bit.

This is a SEPARATE dataset from ``protection_warrior.json``
(``talent_tree_data.py``'s ``entry_catalog``): that file only needs
entry_id -> name (order-independent, sourced from a narrower row
extraction). Decoding the STRING itself needs the full per-node structure
Blizzard's own client walks — node id (for ordering), node type (whether
it's a plain/tiered/choice/hero-subtree-pick node), max ranks, and each
choice node's entries in on-screen order — none of which the older script
captured.

Sourced from SimulationCraft's committed ``trait_data.inc`` (``midnight``
branch), whose ``trait_data_t`` struct (``engine/dbc/trait_data.hpp``)
carries every field needed:

    tree_index, id_class, id_trait_node_entry, id_node, max_ranks,
    req_points, id_trait_definition, id_spell, id_replace_spell,
    id_override_spell, row, col, selection_index, name,
    id_spec[4], id_spec_starter[4], id_sub_tree, node_type

See docs/validation/talent_string_decoder_2026_08_31.md for the full
investigation (this reopens and fixes the 2026-07-13 attempt, which was
missing the "Is Node Purchased" bit Blizzard's own
``Blizzard_ClassTalentImportExport.lua`` writes between "is selected" and
"is partially ranked / is choice node" — a granted-but-unpurchased node
silently desynced every bit after it, which is what actually blocked that
investigation, not the node list or SubTreeSelection width as suspected
at the time).

Tree membership per class_id — the walk is per-CLASS, not per-spec,
confirmed empirically (not assumed from WoW theorycraft knowledge, which
can go stale across patches): a real Brutoh string only decodes with ZERO
leftover bits and a 78/79 exact match against his real COMBATANT_INFO
ground truth when EVERY spec's spec-tree rows (tree_index 2) and EVERY
hero tree's body-node rows (tree_index 3) are included unconditionally —
not just the target spec's own. Blizzard's client evidently uses one
shared, class-wide node ordering regardless of which spec/hero-tree is
actually active; only the reachable subset for a given character ever
reads as selected. Filtering down to "this spec only" (tried first)
undercounts nodes and desyncs the decode almost immediately — the same
failure shape the 2026-07-13 investigation saw.

So for a given class_id: tree_index 1 (class), 2 (spec, ALL specs), 3
(hero body nodes, ALL hero trees), and 4 (hero-tree SELECTION picker
nodes, ALL of them) are ALL included, unconditionally, ordered by
ascending node_id. NOTE: this means body nodes for a hero tree the
character did NOT pick are also walked, and a real decode against
Brutoh's string reads several of his non-chosen hero tree's passives as
spuriously "granted" (see the validation doc's "known narrow limitation"
section — root cause not fully understood, possibly a duplicate/shared
node-id interaction between hero trees). ``decode_full_loadout``'s caller
filters hero-tree entries to the character's own DECODED hero-tree choice
(from the SELECTION node) before trusting any of them, which is a correct
invariant regardless of this raw-read noise — a character definitionally
cannot have real points in a hero tree they didn't pick.

Re-run only when Midnight's talents change (a new season/patch):

    python3 scripts/fetch_talent_string_nodes.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

_TRAIT_DATA_URL = (
    "https://raw.githubusercontent.com/simulationcraft/simc/midnight/"
    "engine/dbc/generated/trait_data.inc"
)

_ROW_RE = re.compile(
    r"\{\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),"
    r"\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*\"([^\"]*)\",\s*\{([^}]*)\},\s*\{([^}]*)\},\s*(\d+),\s*(\d+)\s*\}"
)
_SUB_TREE_RE = re.compile(r'\{\s*(\d+),\s*"([^"]*)",\s*(\d+)\s*\}')

_OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "simf" / "data" / "talent_trees"

# (out filename stem, class_id, spec_id) — WoW's internal class/spec ids,
# confirmed against this same fetched file (id_class 1 = Warrior via known
# Warrior-only talent names; id_class 11 = Druid via "Ironfur"; spec_id 73
# confirmed by decode_header on two real Brutoh strings; spec_id 104
# confirmed by decode_header on AnonGuardian1's real string).
_TARGETS = (
    ("protection_warrior", 1, 73),
    ("guardian_druid", 11, 104),
)


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _parse_rows(text: str) -> list[dict[str, Any]]:
    rows = []
    for m in _ROW_RE.finditer(text):
        g = m.groups()
        rows.append(
            {
                "tree_index": int(g[0]),
                "id_class": int(g[1]),
                "entry_id": int(g[2]),
                "node_id": int(g[3]),
                "max_ranks": int(g[4]),
                "id_spell": int(g[7]),
                "selection_index": int(g[12]),
                "name": g[13],
                "id_spec": tuple(int(x) for x in g[14].split(",")),
                "id_spec_starter": tuple(int(x) for x in g[15].split(",")),
                "id_sub_tree": int(g[16]),
                "node_type": int(g[17]),
            }
        )
    return rows


def _parse_sub_tree_names(text: str) -> dict[int, str]:
    """id_sub_tree -> hero tree display name (e.g. 24 -> "Elune's Chosen"),
    from the ``__trait_sub_tree_data`` tuple array at the bottom of the same
    file."""
    tail = text[text.index("__trait_sub_tree_data") :]
    return {int(m.group(1)): m.group(2) for m in _SUB_TREE_RE.finditer(tail)}


def _nodes_for_class(rows: list[dict[str, Any]], class_id: int) -> list[dict[str, Any]]:
    # The walk is per-CLASS, not per-spec: confirmed empirically (real
    # Brutoh + AnonGuardian1 strings decode with ZERO leftover bits and near-100%
    # ground-truth match only when EVERY spec's spec-tree rows and EVERY
    # hero tree's body-node rows are included unconditionally, not just
    # this spec's own — Blizzard's client evidently uses one shared,
    # class-wide node ordering regardless of which spec/hero-tree is
    # actually active, and only the reachable subset ever reads as
    # selected. Filtering down to "this spec only" here (tried first,
    # before this was understood) undercounts nodes and desyncs the
    # decode almost immediately, the same failure shape the 2026-07-13
    # investigation saw.
    class_rows = [r for r in rows if r["tree_index"] == 1 and r["id_class"] == class_id]
    spec_rows = [r for r in rows if r["tree_index"] == 2 and r["id_class"] == class_id]
    hero_rows = [r for r in rows if r["tree_index"] == 3 and r["id_class"] == class_id]
    selection_rows = [r for r in rows if r["tree_index"] == 4 and r["id_class"] == class_id]

    by_node: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in class_rows + spec_rows + hero_rows + selection_rows:
        by_node[r["node_id"]].append(r)

    nodes = []
    for node_id in sorted(by_node):
        group = sorted(by_node[node_id], key=lambda r: r["selection_index"])
        node_type = max(r["node_type"] for r in group)
        max_ranks = sum(r["max_ranks"] for r in group) if node_type == 1 else group[0]["max_ranks"]
        nodes.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "max_ranks": max_ranks,
                "entries": [
                    {
                        "entry_id": r["entry_id"],
                        "name": r["name"],
                        "spell_id": r["id_spell"],
                        "id_sub_tree": r["id_sub_tree"] or None,
                    }
                    for r in group
                ],
            }
        )
    return nodes


def main() -> int:
    print("Fetching SimC trait_data.inc for talent-string node structure...", file=sys.stderr)
    text = _fetch_text(_TRAIT_DATA_URL)
    rows = _parse_rows(text)
    sub_tree_names = _parse_sub_tree_names(text)
    print(f"parsed {len(rows)} trait rows, {len(sub_tree_names)} hero sub-trees", file=sys.stderr)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stem, class_id, spec_id in _TARGETS:
        nodes = _nodes_for_class(rows, class_id)
        for node in nodes:
            for entry in node["entries"]:
                if entry["id_sub_tree"] is not None:
                    entry["hero_tree_name"] = sub_tree_names.get(entry["id_sub_tree"])
        out = {
            "class_id": class_id,
            "spec_id": spec_id,
            "nodes": nodes,
            "source": "SimulationCraft trait_data.inc (midnight branch) — "
            "see scripts/fetch_talent_string_nodes.py",
            "generated": "2026-08-31",
        }
        out_path = _OUT_DIR / f"{stem}_string_nodes.json"
        out_path.write_text(json.dumps(out, indent=2) + "\n")
        print(f"Wrote {out_path} ({len(nodes)} nodes)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
