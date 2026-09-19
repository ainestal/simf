"""Unit tests for the M+ dungeon loot loader + slot filter."""

from __future__ import annotations

from simf.io.dungeon_loot import items_for_slot, load_dungeon_loot
from simf.io.simc_import import ItemSpec
from simf.optimizer.alternatives import alternatives_for_slot, equivalent_slots


def test_loader_returns_dungeon_keyed_items() -> None:
    table = load_dungeon_loot()
    # Seed data shipped with this branch includes Pit of Saron's Rotting Globule.
    assert "pit_of_saron" in table
    items = table["pit_of_saron"]
    assert any(i.item_id == 252421 and i.name == "Rotting Globule" for i in items)


def test_loader_skips_malformed_rows(tmp_path, monkeypatch) -> None:
    """A row missing a required field shouldn't take the whole loader down."""
    yaml_text = """
dungeons:
  test_dungeon:
    items:
      - id: 12345
        name: "Real Item"
        slot: head
        ilvl: 660
      - id: not_an_int
        name: "Broken"
        slot: chest
        ilvl: 660
"""
    fake = tmp_path / "dungeon_loot.yaml"
    fake.write_text(yaml_text)
    monkeypatch.setattr("simf.io.dungeon_loot._DATA_PATH", fake)
    load_dungeon_loot.cache_clear()
    try:
        table = load_dungeon_loot()
        assert len(table["test_dungeon"]) == 1
        assert table["test_dungeon"][0].item_id == 12345
    finally:
        load_dungeon_loot.cache_clear()


def test_items_for_slot_filters_by_slot() -> None:
    """Only items whose ``slot`` matches the wanted set come back."""
    fake_loot = {
        "dungeon_a": [
            ItemSpec(slot="trinket1", item_id=1, name="T1"),
            ItemSpec(slot="head", item_id=2, name="Helm"),
        ],
        "dungeon_b": [
            ItemSpec(slot="trinket1", item_id=3, name="T3"),
        ],
    }
    pairs = items_for_slot("trinket1", ["dungeon_a", "dungeon_b"], loot=fake_loot)
    assert [(d, i.item_id) for d, i in pairs] == [("dungeon_a", 1), ("dungeon_b", 3)]


def test_items_for_slot_honors_paired_slots() -> None:
    """Passing the equivalent-slot set picks up trinket1-keyed items even
    when the user is browsing trinket2 — same rule bag/vault follow."""
    fake_loot = {
        "dungeon_a": [ItemSpec(slot="trinket1", item_id=1, name="T1")],
    }
    pairs = items_for_slot(equivalent_slots("trinket2"), ["dungeon_a"], loot=fake_loot)
    assert [i.item_id for _, i in pairs] == [1]


def test_alternatives_includes_extra_sources() -> None:
    """An item passed via ``extra_sources`` should appear in the ranked list
    with its caller-provided source label."""
    equipped = {"trinket1": ItemSpec(slot="trinket1", item_id=1, name="Equipped")}
    loot_item = ItemSpec(slot="trinket1", item_id=99, name="LootCandidate")
    fake_stats = {1: {"stamina": 1000}, 99: {"stamina": 1500}}
    marginals = {"stamina": {"p": 71.0, "m": 71.0}}
    dungeons = [{"id": "x", "abbrev": "X", "school_mix": {"physical": 1.0}}]

    def _stats_fn(spec):
        return fake_stats.get(getattr(spec, "item_id", 0))

    alts = alternatives_for_slot(
        slot="trinket1",
        equipped=equipped,
        bag={},
        vault={},
        marginals=marginals,
        dungeons=dungeons,
        item_stats_fn=_stats_fn,
        extra_sources=[("m+ X", loot_item)],
    )
    assert len(alts) == 1
    assert alts[0].source == "m+ X"
    assert alts[0].item.item_id == 99


def test_alternatives_dedupes_bag_vs_extra_sources_on_same_item_id() -> None:
    """If the user has Ampoule of Pure Void in their bag AND it's in the
    M+ loot table, only one row should appear (bag wins, since callers
    list it first)."""
    item_in_bag = ItemSpec(slot="trinket1", item_id=151312, name="Ampoule (bag)")
    item_in_loot = ItemSpec(slot="trinket1", item_id=151312, name="Ampoule (loot)")
    marginals = {"stamina": {"p": 71.0, "m": 71.0}}
    dungeons = [{"id": "x", "abbrev": "X", "school_mix": {"physical": 1.0}}]

    alts = alternatives_for_slot(
        slot="trinket1",
        equipped={},
        bag={"trinket1": [item_in_bag]},
        vault={},
        marginals=marginals,
        dungeons=dungeons,
        item_stats_fn=lambda spec: {"stamina": 1000},
        extra_sources=[("m+ X", item_in_loot)],
    )
    assert len(alts) == 1
    assert alts[0].source == "bag"
    assert alts[0].item.name == "Ampoule (bag)"


def test_alternatives_dedupes_equipped_from_extra_sources() -> None:
    """If a loot item is already equipped, it shouldn't appear as a
    candidate — same dedup rule that bag/vault follow."""
    equipped = {"trinket1": ItemSpec(slot="trinket1", item_id=99, name="Equipped")}
    loot_item = ItemSpec(slot="trinket1", item_id=99, name="SameItem")
    marginals = {"stamina": {"p": 71.0, "m": 71.0}}
    dungeons = [{"id": "x", "abbrev": "X", "school_mix": {"physical": 1.0}}]

    alts = alternatives_for_slot(
        slot="trinket1",
        equipped=equipped,
        bag={},
        vault={},
        marginals=marginals,
        dungeons=dungeons,
        item_stats_fn=lambda spec: {"stamina": 1000},
        extra_sources=[("m+ X", loot_item)],
    )
    assert alts == []


# ─── leather expansion (Guardian / Brewmaster / VDH chase targets) ───────────


# Item IDs sourced from conquestcapped.com 2026-05-23. Pinning them here
# keeps the data file honest: if a future edit drops one of these by
# accident, the test fails and we know the chase-target surface
# regressed for that dungeon.
_LEATHER_BY_DUNGEON = {
    "windrunner_spire": [
        251092,
        251082,
        251087,
    ],  # Fallen Grunt's Mantle, Snapvine Cinch, Legwraps of Lingering Legacies
    "nexus_point_xenas": [
        251204,
        251205,
        251210,
    ],  # Corewright's Zappers, Leyline Leggings, Eclipse Espadrilles
    "algeth_ar_academy": [193714],  # Frenzyroot Cuffs
    "pit_of_saron": [50264, 49817],  # Chewed Leather Wristguards, Shaggy Wyrmleather Leggings
    "skyreach": [
        258581,
        258586,
        258577,
    ],  # Bloodfeather Mantle / Chestguard, Boots of Burning Focus
    "seat_of_the_triumvirate": [151336, 151315, 151318, 151316, 151314, 151317],
    "maisara_caverns": [
        251177,
        251171,
        251166,
    ],  # Fetid Vilecrown, Enthralled Bonespines, Falconer's Cinch
    "magisters_terrace": [251109, 251103, 251113, 251121],
}


def test_every_dungeon_has_leather_drops() -> None:
    """Coverage tripwire: every Midnight S1 M+ dungeon must include at least
    one leather item so non-plate tank specs (Guardian Druid, Brewmaster
    Monk, Vengeance DH) get a chase-target surface in the slot dialog,
    same as plate tanks already do."""
    table = load_dungeon_loot()
    for dungeon_id, expected_ids in _LEATHER_BY_DUNGEON.items():
        assert dungeon_id in table, f"leather coverage missing dungeon {dungeon_id}"
        actual_ids = {i.item_id for i in table[dungeon_id]}
        missing = set(expected_ids) - actual_ids
        assert not missing, f"{dungeon_id} dropped leather item(s) {missing}"


def test_leather_items_route_through_items_for_slot() -> None:
    """Sanity: a leather chest item dropped by Skyreach surfaces when the
    slot dialog filters Skyreach's loot for the ``chest`` slot. Without
    this, the YAML rows would be dead data."""
    table = load_dungeon_loot()
    pairs = items_for_slot("chest", ["skyreach"], loot=table)
    chest_ids = {item.item_id for _, item in pairs}
    assert 258586 in chest_ids  # Bloodfeather Chestguard (leather)


def test_leather_slot_distribution_covers_non_plate_tank_slot_types() -> None:
    """Across the 8 dungeons, leather coverage hits every armor slot a
    non-plate tank cares about. This catches an accidental copy-paste that
    fills 8 dungeons with only legs+wrist and forgets head/chest/etc."""
    table = load_dungeon_loot()
    leather_ids = {i for items in _LEATHER_BY_DUNGEON.values() for i in items}
    leather_slots = {
        item.slot for items in table.values() for item in items if item.item_id in leather_ids
    }
    # All 8 armor slots a leather wearer can occupy must be represented.
    expected_slots = {"head", "shoulder", "chest", "hands", "wrist", "waist", "legs", "feet"}
    assert leather_slots == expected_slots
