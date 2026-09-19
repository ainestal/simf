"""Bit-level codec for WoW's talent-loadout export string (the ``talents=``
value in a ``/simc`` paste, or the string behind a "Copy loadout" button).

Format per Blizzard's own shipped addon source (``Blizzard_ClassTalent
ImportExport.lua`` / ``ExportUtil.lua``, fetched directly from
github.com/tomrus88/BlizzardInterfaceCode, current hero-talent-era version
under ``Blizzard_PlayerSpells/ClassTalents/``):

- Base64 (custom alphabet ``A-Za-z0-9+/``, no padding), 6 bits/char, bits
  packed **LSB-first per character**, characters concatenated in string
  order. Confirmed exactly against ``ExportUtil.lua``'s real
  ``ConvertToBase64``/``ExtractValue`` implementation (not just inferred).
- Header: 8-bit version, 16-bit spec ID, 128-bit tree hash (ignorable for
  a decoder — "third-party sites... can omit and zero-fill it").
- Per node (ascending node ID order, per ``C_Traits.GetTreeNodes``):
  1 bit is-selected → if selected: 1 bit is-purchased → if purchased:
  1 bit is-partially-ranked → if partial: 6 bits ranks-purchased;
  1 bit is-choice-node → if choice: 2 bits chosen entry index.

**2026-08-31: fixed — this module's own docstring previously (as of the
2026-07-13 investigation) omitted the "Is Node Purchased" bit above
entirely**, reading the partial-rank/choice bits right after "is
selected" instead of after "is selected THEN is purchased". A node that's
selected-but-granted-for-free (common — every tree has a few auto-granted
starter nodes) writes ONLY the two bits (selected=1, purchased=0) and
nothing more; the old (never-shipped) decode attempt would misread the
NEXT node's is-selected bit as this node's is-partially-ranked bit,
desyncing everything after it — exactly the "corrupts almost immediately"
symptom that investigation saw, regardless of which candidate node list
it tried. Verified against real ground truth: Brutoh's real talents=
string now decodes with ZERO leftover bits and a 78/79 exact match
against his COMBATANT_INFO-derived real build from the same day
(2026-05-06); AnonGuardian1's decodes with 3 leftover bits (harmless base64
padding) and correctly identifies their hero-talent pick (Elune's Chosen).
See ``docs/validation/talent_string_decoder_2026_08_31.md``.

Known remaining narrow limitation: the per-node walk is per-CLASS, not
per-spec (see ``scripts/fetch_talent_string_nodes.py``'s docstring) — it
includes body nodes for hero trees the character did NOT pick, and a
handful of those spuriously read as "granted" for reasons not fully
understood (root cause not pinned down; possibly a duplicate/shared
node-id interaction between a class's hero trees). ``decode_full_loadout``
does not attempt to fix this at the bit level — callers MUST filter any
hero-tree entry to the character's own decoded hero-tree choice (from the
``SUB_TREE_SELECTION`` node) before trusting it, which is a correct
invariant regardless of this raw-read noise (a character cannot really
have points in a hero tree they didn't pick). Tiered/"Apex" nodes (e.g.
Warrior's Phalanx, a 4-point cluster split across 3 DBC rows) decode their
selected/purchased/rank bits correctly but do not resolve to one single
entry_id — ``SelectedNode.entry_id`` is ``None`` for those, matching this
module's existing "no claim, not a guess" convention.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_CHAR_TO_VALUE = {c: i for i, c in enumerate(_ALPHABET)}

_VERSION_BITS = 8
_SPEC_ID_BITS = 16
_TREE_HASH_BITS = 128
_RANKS_PURCHASED_BITS = 6
_CHOICE_INDEX_BITS = 2

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "talent_trees"


class TalentStringError(ValueError):
    """Raised when a string isn't valid base64 in this alphabet, or is
    too short to contain a header."""


@dataclass(frozen=True)
class TalentStringHeader:
    version: int
    spec_id: int


class NodeType(IntEnum):
    """Mirrors SimC's ``trait_data_t::node_type`` / Blizzard's
    ``Enum.TraitNodeType`` — confirmed against real trait_data.inc rows
    and ``Blizzard_ClassTalentImportExport.lua``'s ``isChoiceNode`` check
    (``Selection or SubTreeSelection``, both 1-bit-choice + 2-bit-index,
    identical width — the two are NOT distinguished at the bit level, only
    by which real node_id they land on)."""

    NORMAL = 0
    TIERED = 1
    SELECTION = 2
    SUB_TREE_SELECTION = 3


@dataclass(frozen=True)
class TraitEntry:
    entry_id: int
    name: str
    spell_id: int
    id_sub_tree: int | None
    hero_tree_name: str | None = None


@dataclass(frozen=True)
class TraitNode:
    node_id: int
    node_type: NodeType
    max_ranks: int
    entries: tuple[TraitEntry, ...]


@dataclass(frozen=True)
class SelectedNode:
    """One selected node from a decoded loadout. ``entry_id``/``name`` are
    ``None`` when the node has more than one entry and isn't a
    choice/subtree-selection node (a tiered/Apex node — see module
    docstring) — the bits still decode cleanly, there's just no single
    entry to attribute them to."""

    node_id: int
    purchased: bool
    ranks: int | None
    entry_id: int | None
    name: str | None
    hero_tree_name: str | None


@dataclass(frozen=True)
class FullLoadoutResult:
    """Result of a full per-node decode. ``ok=False`` means the bit stream
    did not decode cleanly (implausible rank/choice values, or more than a
    handful of leftover bits) — callers must not trust ``selected`` at
    all in that case, same "don't guess" discipline as
    ``decode_simc_talent_string``."""

    ok: bool
    header: TalentStringHeader
    selected: tuple[SelectedNode, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    leftover_bits: int = 0


# A real string's length is only ever a multiple of 6 bits (one base64
# char); the true content can end mid-char, leaving up to 5 padding bits
# that carry no information. More than that means nodes were missed/
# misordered, not padding.
_MAX_HARMLESS_LEFTOVER_BITS = 5


class _BitReader:
    """LSB-first-per-character bit reader, matching Blizzard's own
    ``ExportDataStreamMixin``/``ImportDataStreamMixin`` (``ExportUtil.lua``)
    exactly — verified by hand-tracing its ``ExtractValue`` against this
    implementation before trusting either.
    """

    def __init__(self, talents_str: str) -> None:
        bits: list[int] = []
        for ch in talents_str:
            if ch not in _CHAR_TO_VALUE:
                raise TalentStringError(f"character {ch!r} is not valid base64 in this alphabet")
            value = _CHAR_TO_VALUE[ch]
            for i in range(6):
                bits.append((value >> i) & 1)
        self._bits = bits
        self._pos = 0

    def read(self, n_bits: int) -> int:
        if self._pos + n_bits > len(self._bits):
            raise TalentStringError(
                f"ran out of bits at position {self._pos} (wanted {n_bits} more, "
                f"had {len(self._bits) - self._pos})"
            )
        value = 0
        for i in range(n_bits):
            value |= self._bits[self._pos] << i
            self._pos += 1
        return value

    def remaining_bits(self) -> int:
        return len(self._bits) - self._pos


def decode_header(talents_str: str) -> TalentStringHeader:
    """Decode just the version + spec ID — the reliable part. Raises
    ``TalentStringError`` on a too-short or non-base64 string."""
    reader = _BitReader(talents_str)
    version = reader.read(_VERSION_BITS)
    spec_id = reader.read(_SPEC_ID_BITS)
    return TalentStringHeader(version=version, spec_id=spec_id)


_TREE_FILE_STEM = {
    73: "protection_warrior",
    104: "guardian_druid",
}


def load_string_nodes(spec_id: int) -> tuple[TraitNode, ...] | None:
    """Load the committed ordered node list for ``spec_id`` (generated by
    ``scripts/fetch_talent_string_nodes.py``), or ``None`` for a spec with
    no committed data yet."""
    stem = _TREE_FILE_STEM.get(spec_id)
    if stem is None:
        return None
    path = _DATA_DIR / f"{stem}_string_nodes.json"
    with path.open() as f:
        raw = json.load(f)
    nodes = []
    for n in raw["nodes"]:
        entries = tuple(
            TraitEntry(
                entry_id=e["entry_id"],
                name=e["name"],
                spell_id=e["spell_id"],
                id_sub_tree=e["id_sub_tree"],
                hero_tree_name=e.get("hero_tree_name"),
            )
            for e in n["entries"]
        )
        nodes.append(
            TraitNode(
                node_id=n["node_id"],
                node_type=NodeType(n["node_type"]),
                max_ranks=n["max_ranks"],
                entries=entries,
            )
        )
    return tuple(nodes)


def decode_full_loadout(talents_str: str, nodes: tuple[TraitNode, ...]) -> FullLoadoutResult:
    """Decode every selected node from a real talents= string, walking
    ``nodes`` in the order given (must already be ascending by node_id —
    ``load_string_nodes`` returns them pre-sorted).

    ``ok`` is True only when the walk consumed bits cleanly for every node
    (no premature EOF — see below), every rank/choice-index read was in
    bounds, AND at most ``_MAX_HARMLESS_LEFTOVER_BITS`` bits remain
    unconsumed — the same "don't guess" discipline every other decoder in
    this module follows. A caller that gets ``ok=False`` must fall back,
    not render ``selected`` partially.

    An older real string (exported before a talent-tree change — nodes get
    added/removed/reordered across patches) can run genuinely short of
    bits partway through this walk, since ``nodes`` reflects only the
    CURRENT tree. That's a real, expected limitation (this decoder has no
    way to know a string's origin patch), not a bug — it fails closed via
    ``ok=False`` with a warning rather than letting the underlying
    ``TalentStringError`` (EOF) propagate as an uncaught exception, which
    a real ``examples/`` fixture (a 2026-06-10 Brutoh export, before this
    exact node set existed) surfaced during this fix's own validation.
    """
    header = decode_header(talents_str)
    reader = _BitReader(talents_str)
    reader.read(_VERSION_BITS + _SPEC_ID_BITS)
    reader.read(_TREE_HASH_BITS)

    def _read_node(node: TraitNode) -> SelectedNode | None:
        if not reader.read(1):
            return None
        is_purchased = bool(reader.read(1))
        if not is_purchased:
            entry = node.entries[0] if len(node.entries) == 1 else None
            return SelectedNode(
                node_id=node.node_id,
                purchased=False,
                ranks=None,
                entry_id=entry.entry_id if entry else None,
                name=entry.name if entry else None,
                hero_tree_name=entry.hero_tree_name if entry else None,
            )

        is_partial = reader.read(1)
        ranks = reader.read(_RANKS_PURCHASED_BITS) if is_partial else node.max_ranks
        if not (1 <= ranks <= node.max_ranks):
            warnings.append(
                f"node {node.node_id}: implausible ranks={ranks} (max={node.max_ranks})"
            )

        is_choice = reader.read(1)
        entry = None
        if is_choice:
            idx = reader.read(_CHOICE_INDEX_BITS)
            if idx >= len(node.entries):
                warnings.append(
                    f"node {node.node_id}: choice index {idx} out of bounds "
                    f"({len(node.entries)} entries)"
                )
            else:
                entry = node.entries[idx]
        elif len(node.entries) == 1:
            entry = node.entries[0]

        return SelectedNode(
            node_id=node.node_id,
            purchased=True,
            ranks=ranks,
            entry_id=entry.entry_id if entry else None,
            name=entry.name if entry else None,
            hero_tree_name=entry.hero_tree_name if entry else None,
        )

    selected: list[SelectedNode] = []
    warnings: list[str] = []
    try:
        for node in nodes:
            result_node = _read_node(node)
            if result_node is not None:
                selected.append(result_node)
    except TalentStringError as e:
        warnings.append(f"ran out of bits partway through the walk: {e}")
        return FullLoadoutResult(
            ok=False,
            header=header,
            selected=tuple(selected),
            warnings=tuple(warnings),
            leftover_bits=0,
        )

    leftover = reader.remaining_bits()
    if leftover > _MAX_HARMLESS_LEFTOVER_BITS:
        warnings.append(f"{leftover} leftover bits after walking every node (expected <=5)")

    return FullLoadoutResult(
        ok=not warnings,
        header=header,
        selected=tuple(selected),
        warnings=tuple(warnings),
        leftover_bits=leftover,
    )


def hero_tree_choice(talents_str: str, nodes: tuple[TraitNode, ...]) -> str | None:
    """The character's real hero-talent tree choice (e.g. "Elune's
    Chosen"), read from the SUB_TREE_SELECTION picker node specifically —
    not the (noisier) hero-tree body nodes, which can include a stray
    phantom read from a hero tree the character did NOT pick (see module
    docstring's "known remaining narrow limitation"). Returns ``None`` if
    the decode wasn't clean, or no SUB_TREE_SELECTION node was selected
    (e.g. a low-level character with no hero talent chosen yet)."""
    result = decode_full_loadout(talents_str, nodes)
    if not result.ok:
        return None
    selection_node_ids = {n.node_id for n in nodes if n.node_type == NodeType.SUB_TREE_SELECTION}
    for s in result.selected:
        if s.node_id in selection_node_ids and s.hero_tree_name:
            return s.hero_tree_name
    return None
