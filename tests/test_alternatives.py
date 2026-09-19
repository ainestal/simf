"""Tests for the slot-alternatives ranker.

For a given equipped slot, collect every item in the user's bag + vault that
could go there (including the paired ring/trinket slot) and rank them by
per-dungeon ΔeHP. Powers the v0.9.1 slot-click dialog.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from simf.optimizer.alternatives import (
    alternatives_for_slot,
    equivalent_slots,
)


@dataclass
class _Item:
    slot: str
    item_id: int
    name: str = ""
    ilvl: int = 0


@pytest.fixture
def marginals_phys():
    return {
        "stamina": {"p": 30.0, "m": 22.0},
        "armor_from_gear": {"p": 8.0, "m": 0.0},
        "versatility_rating": {"p": 5.0, "m": 4.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
    }


@pytest.fixture
def dungeons_2():
    return [
        {"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.90}},
        {"id": "alg", "abbrev": "Algaz", "school_mix": {"physical": 0.10}},
    ]


# ── slot equivalence ──────────────────────────────────────────────────────────


def test_equivalent_slots_finger():
    """Rings are paired — finger1 + finger2 share alternatives."""
    assert equivalent_slots("finger1") == {"finger1", "finger2"}
    assert equivalent_slots("finger2") == {"finger1", "finger2"}


def test_equivalent_slots_trinket():
    assert equivalent_slots("trinket1") == {"trinket1", "trinket2"}
    assert equivalent_slots("trinket2") == {"trinket1", "trinket2"}


def test_equivalent_slots_singleton_for_unique_slots():
    """head/chest/etc. are unique — no paired slot."""
    assert equivalent_slots("head") == {"head"}
    assert equivalent_slots("chest") == {"chest"}


# ── alternatives ranking ──────────────────────────────────────────────────────


def test_alternatives_returns_bag_items_for_slot(marginals_phys, dungeons_2):
    equipped = {"head": _Item(slot="head", item_id=10, name="Old", ilvl=658)}
    bag = {
        "head": [
            _Item(slot="head", item_id=1, name="BagHelm", ilvl=665),
        ]
    }
    stats = {
        10: {"stamina": 800, "armor_from_gear": 100},
        1: {"stamina": 1200, "armor_from_gear": 300},
    }
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(alts) == 1
    assert alts[0].item.name == "BagHelm"
    assert alts[0].source == "bag"
    assert alts[0].avg_delta_ehp > 0


def test_alternatives_includes_vault_items(marginals_phys, dungeons_2):
    equipped = {"head": _Item(slot="head", item_id=10, name="Old", ilvl=658)}
    vault = {"head": [_Item(slot="head", item_id=2, name="VaultHelm", ilvl=665)]}
    stats = {10: {"stamina": 800}, 2: {"stamina": 1200}}
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag={},
        vault=vault,
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(alts) == 1
    assert alts[0].item.name == "VaultHelm"
    assert alts[0].source == "vault"


def test_alternatives_sorted_best_first(marginals_phys, dungeons_2):
    equipped = {"head": _Item(slot="head", item_id=10, name="Old")}
    bag = {
        "head": [
            _Item(slot="head", item_id=1, name="SmallHelm"),
            _Item(slot="head", item_id=2, name="BigHelm"),
        ]
    }
    stats = {10: {"stamina": 600}, 1: {"stamina": 800}, 2: {"stamina": 1500}}
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert alts[0].item.name == "BigHelm"
    assert alts[1].item.name == "SmallHelm"


def test_alternatives_for_finger1_merges_finger2_bag_items(marginals_phys, dungeons_2):
    """A ring in 'finger2' bag bucket is a valid finger1 alternative."""
    equipped = {"finger1": _Item(slot="finger1", item_id=10, name="OldRing")}
    bag = {
        "finger1": [_Item(slot="finger1", item_id=1, name="F1Ring")],
        "finger2": [_Item(slot="finger2", item_id=2, name="F2Ring")],
    }
    stats = {10: {"stamina": 500}, 1: {"stamina": 700}, 2: {"stamina": 900}}
    alts = alternatives_for_slot(
        slot="finger1",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    names = {a.item.name for a in alts}
    assert names == {"F1Ring", "F2Ring"}


def test_alternatives_excludes_currently_equipped(marginals_phys, dungeons_2):
    """The currently-equipped item must not appear in its own alternatives list."""
    equipped_helm = _Item(slot="head", item_id=10, name="Equipped")
    equipped = {"head": equipped_helm}
    # The same item also lives in 'bag' — quirky but happens in raw SimC exports.
    bag = {"head": [equipped_helm, _Item(slot="head", item_id=1, name="Other")]}
    stats = {10: {"stamina": 800}, 1: {"stamina": 1000}}
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(alts) == 1
    assert alts[0].item.name == "Other"


def test_alternatives_empty_when_no_candidates(marginals_phys, dungeons_2):
    equipped = {"head": _Item(slot="head", item_id=10, name="Solo")}
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag={},
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: None,
    )
    assert alts == []


def test_alternative_carries_per_dungeon(marginals_phys, dungeons_2):
    """The Alternative carries the per-dungeon scores for the UI to render."""
    equipped = {"head": _Item(slot="head", item_id=10, name="Old")}
    bag = {"head": [_Item(slot="head", item_id=1, name="ArmorHelm")]}
    stats = {10: {"armor_from_gear": 0}, 1: {"armor_from_gear": 200}}
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(alts[0].per_dungeon) == 2
    # Pure armor: phys-heavy WR scores higher than magic-heavy Algaz
    by_abbrev = {s.abbrev: s.delta_ehp for s in alts[0].per_dungeon}
    assert by_abbrev["WR"] > by_abbrev["Algaz"]


def test_alternative_carries_delta_dps_when_weights_supplied(marginals_phys, dungeons_2):
    """Slot-dialog ΔDPS surface — Brutoh user-feedback 2026-05-21:
    "when it suggests me pieces of equipment I only see the eHP, but
    I don't see how much dmg I'd do." A haste-only upgrade scores
    positive ΔDPS against the same weights vault_ranking uses."""
    equipped = {"head": _Item(slot="head", item_id=10, name="Old")}
    bag = {"head": [_Item(slot="head", item_id=1, name="HasteHelm")]}
    stats = {
        10: {"haste_rating": 0},
        1: {"haste_rating": 1000},
    }
    # Constants-yaml convention: full rating-stat names. compute_delta_dps
    # looks up `new_stats[key]` directly, so the weight key must match the
    # stat key on the item (haste_rating, not haste).
    weights = {
        "haste_rating": 1.40,
        "crit_rating": 1.10,
        "mastery_rating": 1.05,
        "versatility_rating": 0.80,
    }
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        dps_weights=weights,
    )
    # +1000 haste × 1.40 weight / 1000 scale = +1.40 ΔDPS.
    assert alts[0].delta_dps == pytest.approx(1.40, rel=1e-6)


def test_alternative_delta_dps_defaults_zero_without_weights(marginals_phys, dungeons_2):
    """No dps_weights passed → field stays 0.0 (callers that don't
    care don't pay)."""
    equipped = {"head": _Item(slot="head", item_id=10, name="Old")}
    bag = {"head": [_Item(slot="head", item_id=1, name="HasteHelm")]}
    stats = {10: {"haste_rating": 0}, 1: {"haste_rating": 1000}}
    alts = alternatives_for_slot(
        slot="head",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert alts[0].delta_dps == 0.0


# ── upgrade-normalized comparison (target_ilvl) ───────────────────────────────
#
# The Tuesday-vault problem: every vault choice sits below the player's fully
# upgraded equipped ilvl, so as-dropped every option reads as a downgrade and
# the comparison teaches nothing. ``target_ilvl`` rescales BOTH the equipped
# baseline and each candidate to a common ilvl, so the player compares the
# *itemization* (stat spread per ilvl) rather than the raw ilvl gap.


def test_target_ilvl_none_is_as_dropped_default(marginals_phys, dungeons_2):
    """Omitting target_ilvl preserves today's behaviour exactly: a lower-ilvl
    vault piece with fewer raw stats reads as a downgrade, and shown_at_ilvl
    stays None so the UI renders it as-dropped."""
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    vault = {"chest": [_Item(slot="chest", item_id=2, name="VaultChest", ilvl=259)]}
    stats = {10: {"stamina": 1000}, 2: {"stamina": 950}}
    alts = alternatives_for_slot(
        slot="chest",
        equipped=equipped,
        bag={},
        vault=vault,
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(alts) == 1
    assert alts[0].shown_at_ilvl is None
    # 950 < 1000 raw → strict downgrade as-dropped.
    assert alts[0].avg_delta_ehp < 0


def test_target_ilvl_normalizes_both_sides_and_can_flip_verdict(marginals_phys, dungeons_2):
    """A vault piece whose *itemization* beats equipped wins once both are
    normalized to a common ilvl, even though it loses as-dropped."""
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    vault = {"chest": [_Item(slot="chest", item_id=2, name="VaultChest", ilvl=259)]}
    # As dropped: 950 < 1000 → downgrade. Scaled to 289: 950 * 289/259 ≈ 1060
    # vs equipped 1000 → upgrade. The vault piece has more stamina *per ilvl*.
    stats = {10: {"stamina": 1000}, 2: {"stamina": 950}}

    as_dropped = alternatives_for_slot(
        slot="chest",
        equipped=equipped,
        bag={},
        vault=vault,
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    normalized = alternatives_for_slot(
        slot="chest",
        equipped=equipped,
        bag={},
        vault=vault,
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        target_ilvl=289,
    )
    assert as_dropped[0].avg_delta_ehp < 0  # loses as-dropped
    assert normalized[0].avg_delta_ehp > 0  # wins once normalized
    assert normalized[0].shown_at_ilvl == 289


def test_target_ilvl_equal_distribution_is_zero_when_normalized(marginals_phys, dungeons_2):
    """Two pieces with the SAME stat-per-ilvl distribution are a wash once
    normalized — the ΔeHP collapses to ~0 (the ilvl gap was the only
    difference)."""
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    vault = {"chest": [_Item(slot="chest", item_id=2, name="VaultChest", ilvl=259)]}
    # Same stamina-per-ilvl: 1000/289 == ~896/259 (896 ≈ 1000 * 259/289).
    stats = {10: {"stamina": 1000}, 2: {"stamina": round(1000 * 259 / 289)}}
    normalized = alternatives_for_slot(
        slot="chest",
        equipped=equipped,
        bag={},
        vault=vault,
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        target_ilvl=289,
    )
    # Within one stamina point of rounding — itemization is identical.
    assert abs(normalized[0].avg_delta_ehp) < 30.0


def test_target_ilvl_candidate_without_ilvl_scored_unscaled(marginals_phys, dungeons_2):
    """A candidate missing its base ilvl can't be normalized — it's scored
    unscaled and left shown_at_ilvl=None so the UI flags it rather than
    inventing a number."""
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    vault = {"chest": [_Item(slot="chest", item_id=2, name="NoIlvl", ilvl=0)]}
    stats = {10: {"stamina": 1000}, 2: {"stamina": 1200}}
    normalized = alternatives_for_slot(
        slot="chest",
        equipped=equipped,
        bag={},
        vault=vault,
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        target_ilvl=289,
    )
    assert normalized[0].shown_at_ilvl is None
    # Equipped (ilvl known) scales 289→289 = no-op; candidate stays raw 1200.
    assert normalized[0].avg_delta_ehp > 0


# ── paired-slot exclusion (no double-equip of an item you already wear) ───────
# Brutoh 2026-05-31: opening the Trinket 2 dialog offered a bag copy of the
# Solarflare Prism he already had equipped (and maxed) in Trinket 1. Candidates
# are gathered from BOTH paired slots, but the old code only excluded the
# *opened* slot's equipped item — so the partner-slot piece sailed through.


def test_alternatives_excludes_item_worn_in_paired_trinket_slot(marginals_phys, dungeons_2):
    """A copy of a trinket already worn in the *paired* slot must not be
    offered when the user opens the other trinket. Exact reproduction of
    Brutoh's report: Solarflare Prism (id 252420) equipped in trinket1, a
    lower-ilvl bag copy of the same id, opening trinket2 (Mark of Light)."""
    equipped = {
        "trinket1": _Item(slot="trinket1", item_id=252420, name="Solarflare Prism", ilvl=298),
        "trinket2": _Item(slot="trinket2", item_id=250241, name="Mark of Light", ilvl=298),
    }
    # Bag carries a lower-ilvl spare of the equipped Solarflare Prism (same id)
    # plus a genuinely new trinket the user does NOT own.
    bag = {
        "trinket1": [_Item(slot="trinket1", item_id=252420, name="Solarflare Prism", ilvl=272)],
        "trinket2": [_Item(slot="trinket2", item_id=999, name="New Trinket", ilvl=280)],
    }
    stats = {
        252420: {"stamina": 1500},
        250241: {"stamina": 1500},
        999: {"stamina": 1400},
    }
    alts = alternatives_for_slot(
        slot="trinket2",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    offered_ids = {a.item.item_id for a in alts}
    assert 252420 not in offered_ids, (
        "Solarflare Prism is already worn in trinket1 — a bag copy must not be "
        "offered as a trinket2 swap (can't double-equip; lower copy is strictly worse)."
    )
    # The genuinely-new trinket still surfaces.
    assert 999 in offered_ids


def test_alternatives_excludes_item_worn_in_paired_finger_slot(marginals_phys, dungeons_2):
    """Same rule for rings: a ring worn in finger1 isn't offered for finger2."""
    equipped = {
        "finger1": _Item(slot="finger1", item_id=500, name="Worn Ring", ilvl=289),
        "finger2": _Item(slot="finger2", item_id=501, name="Other Ring", ilvl=289),
    }
    bag = {"finger1": [_Item(slot="finger1", item_id=500, name="Worn Ring", ilvl=272)]}
    stats = {500: {"stamina": 900}, 501: {"stamina": 900}}
    alts = alternatives_for_slot(
        slot="finger2",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert all(a.item.item_id != 500 for a in alts)


# ── two-handed weapon exclusion (shield specs) ─────────────────────────────────


def test_excludes_two_handed_weapon_for_shield_spec(marginals_phys, dungeons_2):
    """A two-handed weapon must never be offered as a main-hand swap for a
    shield spec — equipping one unequips the off-hand shield, a loss the
    slot-scoped ΔeHP compare can't see. Without the filter, an unpaired 2H
    weapon's much larger raw stat budget always beats a 1H weapon, so it was
    scoring as a false "upgrade" (found 2026-07-04 on a Brutoh demo Gear-tab
    read: a 272 Two-Handed Sword outscored an equipped 298 one-hand weapon)."""
    equipped = {"main_hand": _Item(slot="main_hand", item_id=10, name="Equipped 1H", ilvl=298)}
    bag = {
        "main_hand": [
            _Item(slot="main_hand", item_id=1, name="Bag 2H Greatsword", ilvl=272),
        ]
    }
    # The 2H "candidate" carries a far bigger stat budget than the equipped 1H
    # weapon — exactly the shape that made the false upgrade look real.
    stats = {10: {"stamina": 1000}, 1: {"stamina": 3000}}
    alts = alternatives_for_slot(
        slot="main_hand",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        exclude_two_handed=True,
        item_is_two_handed_fn=lambda spec: spec.item_id == 1,
    )
    assert alts == []


def test_two_handed_weapon_offered_when_not_excluded(marginals_phys, dungeons_2):
    """Non-shield specs (e.g. Blood DK, VDH) don't request the filter — a 2H
    weapon stays a valid main-hand candidate for them."""
    equipped = {"main_hand": _Item(slot="main_hand", item_id=10, name="Equipped 1H", ilvl=298)}
    bag = {"main_hand": [_Item(slot="main_hand", item_id=1, name="Bag 2H Greatsword", ilvl=272)]}
    stats = {10: {"stamina": 1000}, 1: {"stamina": 3000}}
    alts = alternatives_for_slot(
        slot="main_hand",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(alts) == 1
    assert alts[0].item.name == "Bag 2H Greatsword"


def test_two_handed_filter_fails_open_on_unknown_handedness(marginals_phys, dungeons_2):
    """A ``None`` verdict (Wowhead fetch failed / offline) must not silently
    drop the candidate — fail open, matching every other Wowhead-lookup
    fallback in this codebase."""
    equipped = {"main_hand": _Item(slot="main_hand", item_id=10, name="Equipped 1H", ilvl=298)}
    bag = {"main_hand": [_Item(slot="main_hand", item_id=1, name="Unknown Weapon", ilvl=272)]}
    stats = {10: {"stamina": 1000}, 1: {"stamina": 1200}}
    alts = alternatives_for_slot(
        slot="main_hand",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals_phys,
        dungeons=dungeons_2,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        exclude_two_handed=True,
        item_is_two_handed_fn=lambda spec: None,
    )
    assert len(alts) == 1
