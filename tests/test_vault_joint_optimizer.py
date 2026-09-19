"""Vault × Bag joint loadout optimizer tests."""

from dataclasses import dataclass

from simf.optimizer.vault_joint_optimizer import (
    _score_delta_for_dungeon,
    _stat_delta,
    enumerate_joint_loadouts,
)


@dataclass
class FakeSpec:
    slot: str
    item_id: int
    name: str = ""
    ilvl: int = 0


def _marg():
    """Simplified eHP marginals — easy to reason about. Stam → 22 phys/mag,
    armor → 5 phys, vers → 2/3 phys/mag, others zero."""
    return {
        "stamina": {"p": 22.0, "m": 22.0},
        "armor_from_gear": {"p": 5.0, "m": 0.0},
        "versatility_rating": {"p": 3.0, "m": 2.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
    }


def test_stat_delta_returns_only_changed_stats():
    a = {"stamina": 100, "armor_from_gear": 50}
    b = {"stamina": 120, "armor_from_gear": 50, "haste_rating": 30}
    d = _stat_delta(a, b)
    assert d == {"stamina": 20, "haste_rating": 30}


def test_stat_delta_handles_none():
    assert _stat_delta(None, {"stamina": 100}) == {"stamina": 100}
    assert _stat_delta({"stamina": 50}, None) == {"stamina": -50}


def test_score_phys_heavy_dungeon_weights_armor():
    """A pure-armor upgrade scores higher in a physical-heavy dungeon than magic."""
    delta = {"armor_from_gear": 100}
    phys_dungeon = {"physical": 0.90}  # 90% physical
    magic_dungeon = {"physical": 0.10}  # 90% magic
    phys_score = _score_delta_for_dungeon(delta, _marg(), phys_dungeon)
    magic_score = _score_delta_for_dungeon(delta, _marg(), magic_dungeon)
    assert phys_score > magic_score
    # 100 armor × 5 marginal × 0.9 = 450 vs × 0.1 = 50
    assert abs(phys_score - 450) < 0.1
    assert abs(magic_score - 50) < 0.1


def test_enumerate_picks_best_bag_swap_per_vault():
    """Given a vault chest + two bag rings (one upgrade, one downgrade),
    the optimizer pairs the chest with the upgrade ring."""
    vault = [FakeSpec(slot="chest", item_id=1, name="VaultChest", ilvl=276)]
    bag = {
        "finger1": [
            FakeSpec(slot="finger1", item_id=2, name="UpgradeRing", ilvl=272),
            FakeSpec(slot="finger1", item_id=3, name="DowngradeRing", ilvl=200),
        ],
    }
    equipped = {
        "chest": FakeSpec(slot="chest", item_id=10, name="OldChest", ilvl=240),
        "finger1": FakeSpec(slot="finger1", item_id=11, name="OldRing", ilvl=240),
    }
    stats = {
        1: {"stamina": 1000, "armor_from_gear": 200},  # VaultChest
        2: {"stamina": 500},  # UpgradeRing
        3: {"stamina": 100},  # DowngradeRing
        10: {"stamina": 800, "armor_from_gear": 150},  # OldChest
        11: {"stamina": 400},  # OldRing
    }

    dungeons = [{"id": "workshop", "school_mix": {"physical": 0.7}}]
    results = enumerate_joint_loadouts(
        vault_items=vault,
        bag_items_by_slot=bag,
        equipped=equipped,
        selected_dungeons=dungeons,
        marginals=_marg(),
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )

    assert len(results) == 1
    r = results[0]
    assert r.vault_spec.name == "VaultChest"
    assert r.bag_swap is not None
    assert r.bag_swap.name == "UpgradeRing"
    assert r.bag_swap_slot == "finger1"


def test_enumerate_no_bag_swap_when_nothing_helps():
    """If no bag item improves over equipped, the optimizer returns vault-only."""
    vault = [FakeSpec(slot="chest", item_id=1, ilvl=276, name="VaultChest")]
    bag = {"finger1": [FakeSpec(slot="finger1", item_id=2, ilvl=200, name="WorseRing")]}
    equipped = {
        "chest": FakeSpec(slot="chest", item_id=10, ilvl=240),
        "finger1": FakeSpec(slot="finger1", item_id=11, ilvl=240, name="GoodRing"),
    }
    stats = {
        1: {"stamina": 1000},
        2: {"stamina": 100},
        10: {"stamina": 800},
        11: {"stamina": 600},
    }

    results = enumerate_joint_loadouts(
        vault_items=vault,
        bag_items_by_slot=bag,
        equipped=equipped,
        selected_dungeons=[{"id": "x", "school_mix": {"physical": 0.5}}],
        marginals=_marg(),
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(results) == 1
    assert results[0].bag_swap is None  # no improvement, vault-only


def test_enumerate_ranks_vault_picks_by_avg_dungeon_score():
    """Vault picks returned in descending avg score across selected dungeons."""
    vault = [
        FakeSpec(slot="chest", item_id=1, name="Small", ilvl=250),
        FakeSpec(slot="chest", item_id=2, name="Big", ilvl=276),
    ]
    stats = {
        1: {"stamina": 500},
        2: {"stamina": 1500},
        10: {"stamina": 800},
    }
    equipped = {"chest": FakeSpec(slot="chest", item_id=10, ilvl=240)}

    results = enumerate_joint_loadouts(
        vault_items=vault,
        bag_items_by_slot={},
        equipped=equipped,
        selected_dungeons=[{"id": "x", "school_mix": {"physical": 0.5}}],
        marginals=_marg(),
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert results[0].vault_spec.name == "Big"
    assert results[1].vault_spec.name == "Small"


def test_enumerate_with_no_dungeons_falls_back_to_generic():
    """No selected dungeons → scores against single generic 65/35 split."""
    vault = [FakeSpec(slot="chest", item_id=1, name="V", ilvl=276)]
    stats = {1: {"stamina": 1000}, 10: {"stamina": 800}}
    equipped = {"chest": FakeSpec(slot="chest", item_id=10, ilvl=240)}

    results = enumerate_joint_loadouts(
        vault_items=vault,
        bag_items_by_slot={},
        equipped=equipped,
        selected_dungeons=[],
        marginals=_marg(),
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(results) == 1
    assert "generic" in results[0].score_per_dungeon
