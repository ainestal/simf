"""Tests for `io/talent_tree_data.py` — the loader for the committed
Protection Warrior talent catalog (entry_id -> name/spell/tree/modeled
status), sourced from SimC's `trait_data.inc`.
"""

from __future__ import annotations

from simf.core.constants import load_constants
from simf.io.talent_tree_data import load_protection_warrior_tree


def test_entry_id_to_modeled_talent_covers_every_modeled_talent():
    tree = load_protection_warrior_tree()
    modeled = set(load_constants().get("modeled_talents", {}).get("protection_warrior", []))
    covered = set(tree.entry_id_to_modeled_talent.values())
    assert covered == modeled, (
        f"entry_id join should cover every modeled talent; missing={modeled - covered}, "
        f"extra={covered - modeled}"
    )


def test_entry_catalog_is_a_superset_of_the_modeled_join():
    """The full display catalog (used by "Your talents, explained") must
    contain every entry_id the modeled-only join does, with agreeing
    modeled_talent_id — the two must never drift apart since a UI bug
    where one says "modeled" and the other doesn't would be a real
    trust-surface contradiction."""
    tree = load_protection_warrior_tree()
    for entry_id, modeled_name in tree.entry_id_to_modeled_talent.items():
        assert entry_id in tree.entry_catalog
        assert tree.entry_catalog[entry_id].modeled_talent_id == modeled_name


def test_entry_catalog_covers_all_three_trees():
    tree = load_protection_warrior_tree()
    trees_seen = {entry.tree for entry in tree.entry_catalog.values()}
    assert trees_seen == {"class", "spec", "hero"}


def test_load_is_cached_returns_same_object():
    a = load_protection_warrior_tree()
    b = load_protection_warrior_tree()
    assert a is b


def test_fueled_by_violence_is_registered_unmodeled_not_modeled():
    """Fueled by Violence heals on a Spell-Reflect deflect, but every M+
    tank-buster profile is melee/not-reflectable (data/talents.yaml's own
    `trigger` note already documented this) — the credit can never fire in
    any modeled scenario. Registry must say so, not claim it's modeled."""
    modeled = set(load_constants().get("modeled_talents", {}).get("protection_warrior", []))
    unmodeled = set(
        load_constants().get("known_unmodeled_talents", {}).get("protection_warrior", [])
    )
    assert "fueled_by_violence" not in modeled
    assert "fueled_by_violence" in unmodeled
    # The talent tree join must agree — no entry_id should still claim
    # `modeled_talent_id: "fueled_by_violence"` (the exact bug: the registry
    # and the tree catalog silently disagreeing).
    tree = load_protection_warrior_tree()
    assert "fueled_by_violence" not in set(tree.entry_id_to_modeled_talent.values())
    assert all(
        entry.modeled_talent_id != "fueled_by_violence" for entry in tree.entry_catalog.values()
    )


def test_no_stranger_to_pain_is_registered_unmodeled():
    unmodeled = set(
        load_constants().get("known_unmodeled_talents", {}).get("protection_warrior", [])
    )
    assert "no_stranger_to_pain" in unmodeled
