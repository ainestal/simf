"""Turn a real talent selection source (COMBATANT_INFO entry ids, or an
in-game ``talents=`` export string) into the flat ``frozenset[str]`` of
modeled talent keys ``Character._talent_set()`` already consumes.

Two decode paths:

- ``decode_combatant_info_entry_ids`` — a real ACL-on combat log's
  COMBATANT_INFO talent block carries ``(node_id, entry_id, rank)``
  triples; matching ``entry_id`` against ``talent_tree_data``'s
  ``entry_id_to_modeled_talent`` table is a plain set-membership join,
  independent of node order. Verified byte-for-byte against a real log
  (see ``talent_tree_data.py``'s module docstring).
- ``decode_simc_talent_string`` — decodes a pasted ``talents=`` export
  string bit-for-bit via ``talent_string_codec.decode_full_loadout``,
  fixed 2026-08-31 (see that module's docstring for what was wrong before
  and how it's now verified against real ground truth). ``ok=False``
  when the string doesn't decode cleanly for this spec (wrong spec,
  garbage input, or no committed node data yet) — callers fall back to
  the existing preset-name-match behavior, same discipline as before this
  path worked.

``decode_hero_tree_name`` is a separate, narrower query over the same
decode — which hero-talent tree (e.g. "Elune's Chosen") a character
picked — for specs whose survivability model is hero-tree-gated but has
no modeled-talent catalog at all (Guardian Druid's Ironfur haste model;
see ``character.py:_guardian_ironfur_avg_stacks``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from simf.io.talent_string_codec import (
    TalentStringError,
    decode_full_loadout,
    decode_header,
    hero_tree_choice,
    load_string_nodes,
)
from simf.io.talent_tree_data import CatalogEntry, load_protection_warrior_tree

_PROTECTION_WARRIOR_SPEC_ID = 73


@dataclass(frozen=True)
class DecodedBuild:
    """Result of a decode attempt. ``ok=False`` means "don't trust
    ``modeled``/``all_talents`` for anything" — callers must fall back, not
    render them. ``warnings`` explains why, in a form suitable for a debug
    caption. ``all_talents`` is every REAL selected talent recognized (not
    just the modeled subset); ``modeled`` (a subset of it) is what the sim
    actually consumes — the only field with a live consumer today."""

    ok: bool
    modeled: frozenset[str] = frozenset()
    all_talents: tuple[CatalogEntry, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


def decode_combatant_info_entry_ids(entry_ids: frozenset[int]) -> DecodedBuild:
    """Decode a real log's COMBATANT_INFO talent entry-id set into both the
    modeled talent names it contains AND the full list of every recognized
    real talent (for display). Always ``ok=True`` (an empty/no-match result
    is a legitimate "no modeled talents observed," not a failure) — the
    caller does not need a fallback path just to handle zero matches.
    """
    tree = load_protection_warrior_tree()
    modeled = {
        tree.entry_id_to_modeled_talent[eid]
        for eid in entry_ids
        if eid in tree.entry_id_to_modeled_talent
    }
    all_talents = tuple(
        tree.entry_catalog[eid] for eid in sorted(entry_ids) if eid in tree.entry_catalog
    )
    return DecodedBuild(ok=True, modeled=frozenset(modeled), all_talents=all_talents)


def decode_simc_talent_string(talents_str: str) -> DecodedBuild:
    """Decode a real Protection Warrior ``talents=`` export string into its
    real selected talents, via ``talent_string_codec.decode_full_loadout``.

    ``ok=False`` (with ``modeled``/``all_talents`` empty) for: a
    malformed/non-base64 string, a string for a different spec, or a
    string that doesn't decode cleanly (see ``decode_full_loadout``'s
    ``ok`` contract) — a caller must fall back to the existing preset-
    name-match behavior in every one of those cases, never render a
    partial guess.

    Hero-tree body-node entries are dropped unless they belong to the
    character's own decoded hero-tree choice — see
    ``talent_string_codec.py``'s "known remaining narrow limitation" for
    why a raw decode can otherwise include a stray entry from a hero tree
    the character did not pick.
    """
    try:
        header = decode_header(talents_str)
    except TalentStringError as e:
        return DecodedBuild(ok=False, warnings=(f"not a decodable talent string: {e}",))

    if header.spec_id != _PROTECTION_WARRIOR_SPEC_ID:
        return DecodedBuild(
            ok=False,
            warnings=(f"string is for spec_id {header.spec_id}, not Protection Warrior (73)",),
        )

    nodes = load_string_nodes(_PROTECTION_WARRIOR_SPEC_ID)
    if nodes is None:
        return DecodedBuild(
            ok=False, warnings=("no committed talent-string node data for this spec",)
        )

    result = decode_full_loadout(talents_str, nodes)
    if not result.ok:
        return DecodedBuild(ok=False, warnings=result.warnings)

    chosen_hero_tree = hero_tree_choice(talents_str, nodes)
    entry_ids = frozenset(
        s.entry_id
        for s in result.selected
        if s.entry_id is not None
        and (s.hero_tree_name is None or s.hero_tree_name == chosen_hero_tree)
    )

    tree = load_protection_warrior_tree()
    modeled = frozenset(
        tree.entry_id_to_modeled_talent[eid]
        for eid in entry_ids
        if eid in tree.entry_id_to_modeled_talent
    )
    all_talents = tuple(
        tree.entry_catalog[eid] for eid in sorted(entry_ids) if eid in tree.entry_catalog
    )
    return DecodedBuild(ok=True, modeled=modeled, all_talents=all_talents)


def decode_hero_tree_name(talents_str: str, expected_spec_id: int) -> str | None:
    """The character's real hero-talent tree name (e.g. "Elune's Chosen"),
    decoded from a pasted SimC ``talents=`` string for ``expected_spec_id``.

    For specs like Guardian Druid whose survivability model is hero-tree-
    gated (``character.py:_guardian_ironfur_avg_stacks``) but which have
    no modeled-talent catalog at all, so ``decode_simc_talent_string``
    doesn't apply. Returns ``None`` on any decode failure, spec mismatch,
    missing node data, or no hero tree chosen yet — never a guess.
    """
    try:
        header = decode_header(talents_str)
    except TalentStringError:
        return None
    if header.spec_id != expected_spec_id:
        return None
    nodes = load_string_nodes(expected_spec_id)
    if nodes is None:
        return None
    return hero_tree_choice(talents_str, nodes)
