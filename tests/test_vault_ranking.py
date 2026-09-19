"""Tests for the v0.9 vault ranking helper.

`rank_vault_items` wraps the per-dungeon ΔeHP scorer + a winner picker so
Surface 1's vault panel has one canonical call. Returns a list of VaultRow,
sorted best-first, with the headline + verdict sentence pre-formatted.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from simf.core.character import Character
from simf.optimizer.vault_ranking import (
    VaultRow,
    compute_delta_dps,
    dead_count_sentence,
    headline_for_winner,
    rank_vault_items,
)


@dataclass
class _Item:
    slot: str
    item_id: int
    name: str
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
def dungeons_3():
    return [
        {"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.90}},
        {"id": "fg", "abbrev": "FG", "school_mix": {"physical": 0.70}},
        {"id": "alg", "abbrev": "Algaz", "school_mix": {"physical": 0.10}},
    ]


def test_rank_returns_one_row_per_vault_item(marginals_phys, dungeons_3):
    vault = [
        _Item(slot="head", item_id=1, name="Helm"),
        _Item(slot="neck", item_id=2, name="Pendant"),
    ]
    stats = {
        1: {"stamina": 1200, "armor_from_gear": 300},  # helm: big phys upgrade
        2: {"stamina": 900},  # pendant: stamina only
        10: {"stamina": 800, "armor_from_gear": 200},  # old helm
        11: {"stamina": 700},  # old neck
    }
    equipped = {
        "head": _Item(slot="head", item_id=10, name="OldHelm"),
        "neck": _Item(slot="neck", item_id=11, name="OldNeck"),
    }
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(rows) == 2
    assert {r.item.name for r in rows} == {"Helm", "Pendant"}


def test_rank_sorted_best_first(marginals_phys, dungeons_3):
    vault = [
        _Item(slot="head", item_id=1, name="SmallHelm"),
        _Item(slot="head", item_id=2, name="BigHelm"),
    ]
    stats = {
        1: {"stamina": 900, "armor_from_gear": 150},
        2: {"stamina": 1500, "armor_from_gear": 400},
        10: {"stamina": 800, "armor_from_gear": 100},
    }
    rows = rank_vault_items(
        vault_items=vault,
        equipped={"head": _Item(slot="head", item_id=10, name="OldHelm")},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert rows[0].item.name == "BigHelm"
    assert rows[1].item.name == "SmallHelm"
    assert rows[0].avg_delta_ehp > rows[1].avg_delta_ehp


def test_row_carries_per_dungeon_scores_and_verdict(marginals_phys, dungeons_3):
    vault = [_Item(slot="head", item_id=1, name="ArmorHelm")]
    stats = {
        1: {"armor_from_gear": 200},
        10: {"armor_from_gear": 0},
    }
    rows = rank_vault_items(
        vault_items=vault,
        equipped={"head": _Item(slot="head", item_id=10, name="Old")},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    r = rows[0]
    assert len(r.per_dungeon) == 3
    # Pure-armor in phys-heavy dungeon: 200 × 8 × 0.90 = 1440 vs 0.10 → 160
    by_abbrev = {s.abbrev: s.delta_ehp for s in r.per_dungeon}
    assert by_abbrev["WR"] > by_abbrev["Algaz"]
    assert "WR" in r.verdict_sentence


def test_per_dungeon_score_carries_name_and_school_mix(marginals_phys):
    """DungeonScore plumbs the dungeon's full `name` and `school_mix` so the
    slot-dialog expander can render `Windrunner Spire +2,081 eHP` with a
    damage-mix tooltip, rather than the cryptic abbrev (`WR`)."""
    from simf.optimizer.per_dungeon import score_item_across_dungeons

    dungeons = [
        {
            "id": "wr",
            "abbrev": "WR",
            "name": "Windrunner Spire",
            "school_mix": {"physical": 0.65, "shadow": 0.25, "nature": 0.10},
        },
        {"id": "fg", "abbrev": "FG", "school_mix": {"physical": 0.70}},  # no name
    ]
    scores = score_item_across_dungeons(
        new_stats={"stamina": 1000},
        equipped_stats=None,
        marginals=marginals_phys,
        dungeons=dungeons,
    )
    by_id = {s.dungeon_id: s for s in scores}
    assert by_id["wr"].name == "Windrunner Spire"
    assert by_id["wr"].school_mix == {"physical": 0.65, "shadow": 0.25, "nature": 0.10}
    assert by_id["fg"].name == "FG"  # falls back to abbrev when name absent


def test_winner_headline_leads_with_item_name():
    """The headline must name the *item*, not the slot — otherwise two
    main-hand vault picks both produce "Take the Main hand." and the user
    can't tell which one to take. The item name is the disambiguator; the
    slot is already shown on the row below."""
    winner = VaultRow(
        item=_Item(slot="head", item_id=1, name="Crown of the Worldforge"),
        slot="head",
        per_dungeon=[],
        avg_delta_ehp=420.0,
        verdict_sentence="Wins WR (+420 eHP).",
    )
    assert headline_for_winner(winner) == "Take Crown of the Worldforge."


def test_winner_headline_disambiguates_same_slot_picks():
    """Two main-hand vault rows must produce two distinct headlines."""
    a = VaultRow(
        item=_Item(slot="main_hand", item_id=1, name="Aldrachi Warblades"),
        slot="main_hand",
        per_dungeon=[],
        avg_delta_ehp=420.0,
        verdict_sentence="",
    )
    b = VaultRow(
        item=_Item(slot="main_hand", item_id=2, name="Twin Lords' Blades"),
        slot="main_hand",
        per_dungeon=[],
        avg_delta_ehp=400.0,
        verdict_sentence="",
    )
    assert headline_for_winner(a) != headline_for_winner(b)


def test_winner_headline_falls_back_to_slot_label_when_name_missing():
    """Older SimC exports don't include the item-name comment. With no
    name available, fall back to the slot label — better than ``Item #1``."""
    winner = VaultRow(
        item=_Item(slot="head", item_id=1, name=""),
        slot="head",
        per_dungeon=[],
        avg_delta_ehp=420.0,
        verdict_sentence="",
    )
    assert headline_for_winner(winner) == "Take the Helm."


def test_winner_headline_handles_unknown_slot():
    winner = VaultRow(
        item=_Item(slot="some_weird_slot", item_id=1, name="X"),
        slot="some_weird_slot",
        per_dungeon=[],
        avg_delta_ehp=100.0,
        verdict_sentence="Wins X.",
    )
    h = headline_for_winner(winner)
    # Fallback: use the item name itself rather than fabricate a slot label.
    assert "X" in h


def test_winner_headline_unknown_slot_and_no_name_uses_item_id():
    """Worst-case input: neither the slot nor the name is usable. Headline
    must still be safe (no None, no broken format)."""
    winner = VaultRow(
        item=_Item(slot="???", item_id=42, name=""),
        slot="???",
        per_dungeon=[],
        avg_delta_ehp=100.0,
        verdict_sentence="",
    )
    h = headline_for_winner(winner)
    assert h.startswith("Take ")
    assert h.endswith(".")
    assert "None" not in h


def test_rank_skips_vault_items_without_slot(marginals_phys, dungeons_3):
    """Malformed vault entries (e.g. unparsed) should be skipped, not crash."""
    vault = [
        _Item(slot="head", item_id=1, name="Helm"),
        _Item(slot="", item_id=2, name="Borked"),
    ]
    stats = {1: {"stamina": 1000}, 10: {"stamina": 800}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped={"head": _Item(slot="head", item_id=10, name="Old")},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(rows) == 1
    assert rows[0].item.name == "Helm"


def test_rank_no_vault_items_returns_empty(marginals_phys, dungeons_3):
    rows = rank_vault_items(
        vault_items=[],
        equipped={},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: None,
    )
    assert rows == []


# ─── ΔDPS ──────────────────────────────────────────────────────────────────────


_DPS_WEIGHTS = {
    "haste_rating": 1.40,
    "crit_rating": 1.10,
    "mastery_rating": 1.05,
    "versatility_rating": 0.80,
}


def test_delta_dps_zero_when_new_stats_missing():
    assert compute_delta_dps(None, {"haste_rating": 100}, _DPS_WEIGHTS) == 0.0


def test_delta_dps_positive_when_upgrade_adds_haste():
    new = {"haste_rating": 1000}
    eq = {"haste_rating": 500}
    # (1000 - 500) * 1.40 / 1000 == 0.70
    assert compute_delta_dps(new, eq, _DPS_WEIGHTS) == pytest.approx(0.70)


def test_delta_dps_negative_when_downgrade():
    new = {"haste_rating": 200}
    eq = {"haste_rating": 700}
    assert compute_delta_dps(new, eq, _DPS_WEIGHTS) == pytest.approx(-0.70)


def test_delta_dps_treats_missing_equipped_as_zero():
    new = {"crit_rating": 1000}
    assert compute_delta_dps(new, None, _DPS_WEIGHTS) == pytest.approx(1.10)


def test_delta_dps_only_counts_weighted_stats():
    new = {"haste_rating": 1000, "stamina": 5000}  # stamina has no DPS weight
    eq = {"haste_rating": 500}
    assert compute_delta_dps(new, eq, _DPS_WEIGHTS) == pytest.approx(0.70)


def test_rank_populates_delta_dps_from_weights(marginals_phys, dungeons_3):
    vault = [_Item(slot="head", item_id=1, name="HasteHelm")]
    stats = {
        1: {"stamina": 800, "haste_rating": 1000},
        10: {"stamina": 800, "haste_rating": 200},
    }
    rows = rank_vault_items(
        vault_items=vault,
        equipped={"head": _Item(slot="head", item_id=10, name="OldHelm")},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        dps_weights=_DPS_WEIGHTS,
    )
    # (1000 - 200) * 1.40 / 1000 = 1.12
    assert rows[0].delta_dps == pytest.approx(1.12)


def test_rank_delta_dps_defaults_to_zero_without_weights(marginals_phys, dungeons_3):
    """Backwards-compatible: callers that didn't pass dps_weights see 0.0."""
    vault = [_Item(slot="head", item_id=1, name="Helm")]
    stats = {1: {"stamina": 800, "haste_rating": 1000}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped={},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert rows[0].delta_dps == 0.0


# ─── Prog filter (selected_dungeon_ids) ────────────────────────────────────────


def test_rank_filtered_avg_uses_only_selected_dungeons(marginals_phys, dungeons_3):
    """ΔeHP avg differs across phys-heavy vs phys-light dungeons. Filtering
    to only the phys-heavy keys should change the headline avg."""
    vault = [_Item(slot="head", item_id=1, name="ArmorHelm")]
    stats = {1: {"armor_from_gear": 200}, 10: {"armor_from_gear": 0}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped={"head": _Item(slot="head", item_id=10, name="Old")},
        marginals=marginals_phys,
        dungeons=dungeons_3,  # WR phys=0.90, FG phys=0.70, Algaz phys=0.10
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        selected_dungeon_ids=["wr"],
    )
    r = rows[0]
    # filtered avg over just WR should be much higher than the 3-dungeon mean
    by_id = {s.dungeon_id: s.delta_ehp for s in r.per_dungeon}
    assert r.avg_delta_ehp == pytest.approx(by_id["wr"])
    assert r.all_avg_delta_ehp == pytest.approx(sum(by_id.values()) / 3)
    assert r.avg_delta_ehp > r.all_avg_delta_ehp


def test_rank_per_dungeon_keeps_full_catalog_under_filter(marginals_phys, dungeons_3):
    """Filtering selects a subset for avg/verdict; per_dungeon list must
    still expose the full catalog so the UI can show both numbers."""
    vault = [_Item(slot="head", item_id=1, name="ArmorHelm")]
    stats = {1: {"armor_from_gear": 200}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped={},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        selected_dungeon_ids=["wr"],
    )
    r = rows[0]
    assert len(r.per_dungeon) == 3
    assert r.selected_dungeon_count == 1
    assert r.total_dungeon_count == 3


def test_rank_unknown_selected_ids_falls_back_to_all(marginals_phys, dungeons_3):
    """Stale selected_dungeon_ids from a prior catalog must not zero out
    the verdict — fall back to scoring across every available dungeon."""
    vault = [_Item(slot="head", item_id=1, name="Helm")]
    stats = {1: {"stamina": 1000}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped={},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        selected_dungeon_ids=["xxx_removed_dungeon"],
    )
    r = rows[0]
    assert r.selected_dungeon_count == 3  # filtered_scores fallback uses full set
    assert r.avg_delta_ehp == pytest.approx(r.all_avg_delta_ehp)


def test_rank_selected_dungeon_ids_default_matches_all(marginals_phys, dungeons_3):
    """No selection arg → behavior unchanged: filtered avg == all avg, all
    dungeons considered selected (back-compat with the v0.9.1 callers)."""
    vault = [_Item(slot="head", item_id=1, name="Helm")]
    stats = {1: {"stamina": 1000}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped={},
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    r = rows[0]
    assert r.avg_delta_ehp == pytest.approx(r.all_avg_delta_ehp)
    assert r.selected_dungeon_count == r.total_dungeon_count == 3


# ── upgrade-normalized comparison (target_ilvl_fn) ────────────────────────────
#
# A maxed player's vault picks all sit below their equipped ilvl, so as-dropped
# every choice reads as a downgrade ("No vault upgrade this week"). target_ilvl_fn
# rescales BOTH the vault candidate and its slot's equipped baseline to a common
# per-slot ilvl, so the ΔeHP + the displayed stats reflect itemization.


def test_target_ilvl_fn_none_is_as_dropped(marginals_phys, dungeons_3):
    """No target_ilvl_fn → today's behaviour: a lower-ilvl vault chest with
    fewer raw stats loses, and shown_at_ilvl stays None."""
    vault = [_Item(slot="chest", item_id=2, name="VaultChest", ilvl=259)]
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    stats = {10: {"stamina": 1000}, 2: {"stamina": 950}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert rows[0].shown_at_ilvl is None
    assert rows[0].avg_delta_ehp < 0  # 950 < 1000 raw


def test_target_ilvl_fn_normalizes_both_sides_and_flips(marginals_phys, dungeons_3):
    """Normalized to the equipped ilvl, a vault chest with better stat-per-ilvl
    overtakes equipped — and the displayed stats are the scaled values."""
    vault = [_Item(slot="chest", item_id=2, name="VaultChest", ilvl=259)]
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    stats = {10: {"stamina": 1000}, 2: {"stamina": 950}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        target_ilvl_fn=lambda slot: 289,
    )
    r = rows[0]
    assert r.shown_at_ilvl == 289
    assert r.avg_delta_ehp > 0  # 950×289/259 ≈ 1060 > 1000
    # The displayed (new_stats) are scaled to the target, so the cell shows
    # the piece at the compared level — what the player asked to see.
    assert r.new_stats["stamina"] == round(950 * 289 / 259)


def test_target_ilvl_fn_equal_distribution_is_a_wash(marginals_phys, dungeons_3):
    """Same stamina-per-ilvl on both sides → normalized ΔeHP collapses to ~0
    (the ilvl gap was the only difference)."""
    vault = [_Item(slot="chest", item_id=2, name="VaultChest", ilvl=259)]
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    stats = {10: {"stamina": 1000}, 2: {"stamina": round(1000 * 259 / 289)}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        target_ilvl_fn=lambda slot: 289,
    )
    assert abs(rows[0].avg_delta_ehp) < 30.0


def test_target_ilvl_fn_none_per_slot_keeps_that_row_as_dropped(marginals_phys, dungeons_3):
    """A target_ilvl_fn that returns None for a slot leaves that row scored
    as-dropped (graceful per-slot opt-out)."""
    vault = [_Item(slot="chest", item_id=2, name="VaultChest", ilvl=259)]
    equipped = {"chest": _Item(slot="chest", item_id=10, name="Equipped", ilvl=289)}
    stats = {10: {"stamina": 1000}, 2: {"stamina": 950}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        target_ilvl_fn=lambda slot: None,
    )
    assert rows[0].shown_at_ilvl is None
    assert rows[0].avg_delta_ehp < 0


# ── track-aware verdict: dead picks, ceiling upgrades, paired baselines ───────
#
# DEAD: the player owns the offer's item_id (equipped or bag) at an ilvl >=
# the offer's reachable ceiling — taking it can never beat the copy in hand.
# CEILING UPGRADE: owned below the offer's reachable ceiling — the offer can
# walk higher than the owned copy sits today.
# PAIRED BASELINE: a trinket1/finger1 offer is scored against EACH paired
# incumbent; the row keeps the most favorable swap and names the slot.


def test_dead_when_owned_equipped_at_offer_ceiling(marginals_phys, dungeons_3):
    vault = [_Item(slot="head", item_id=1, name="Cranium", ilvl=272)]
    equipped = {"head": _Item(slot="head", item_id=1, name="Cranium", ilvl=289)}
    stats = {1: {"stamina": 900}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        ceiling_fn=lambda slot: 289,
    )
    r = rows[0]
    assert r.dead is True
    assert r.ceiling_upgrade is False
    assert r.owned_max_ilvl == 289
    assert r.offer_ceiling == 289


def test_dead_uses_max_owned_copy_across_equipped_and_bag(marginals_phys, dungeons_3):
    """A low-ilvl bag spare must not mask the maxed equipped copy (real
    case: Bifurcation Band equipped at 289 + a 266 copy still bagged)."""
    vault = [_Item(slot="finger1", item_id=7, name="Band", ilvl=272)]
    equipped = {"finger1": _Item(slot="finger1", item_id=7, name="Band", ilvl=289)}
    bag = {"finger1": [_Item(slot="finger1", item_id=7, name="Band", ilvl=266)]}
    stats = {7: {"stamina": 600}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        bag=bag,
        ceiling_fn=lambda slot: 289,
    )
    r = rows[0]
    assert r.dead is True
    assert r.owned_max_ilvl == 289  # max over copies, not the 266 spare


def test_dead_from_bag_copy_alone(marginals_phys, dungeons_3):
    """Ownership scans the bag too — an unequipped maxed copy still kills
    the offer."""
    vault = [_Item(slot="head", item_id=3, name="Helm", ilvl=272)]
    equipped = {"head": _Item(slot="head", item_id=4, name="OtherHelm", ilvl=289)}
    bag = {"head": [_Item(slot="head", item_id=3, name="Helm", ilvl=289)]}
    stats = {3: {"stamina": 900}, 4: {"stamina": 950}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        bag=bag,
        ceiling_fn=lambda slot: 289,
    )
    assert rows[0].dead is True
    assert rows[0].owned_max_ilvl == 289


def test_not_dead_when_item_not_owned(marginals_phys, dungeons_3):
    vault = [_Item(slot="trinket1", item_id=5, name="HeartOfWind", ilvl=272)]
    equipped = {"trinket1": _Item(slot="trinket1", item_id=10, name="Prism", ilvl=298)}
    stats = {5: {"stamina": 900}, 10: {"stamina": 950}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        ceiling_fn=lambda slot: 298,
    )
    r = rows[0]
    assert r.dead is False
    assert r.ceiling_upgrade is False
    assert r.owned_max_ilvl is None
    assert r.offer_ceiling == 298


def test_unknown_ceiling_disables_dead_and_ceiling_flags(marginals_phys, dungeons_3):
    """ceiling_fn → None (online loads with no watermark + no constants)
    must no-op gracefully: never call an offer dead on unknown data."""
    vault = [_Item(slot="head", item_id=1, name="Helm", ilvl=272)]
    equipped = {"head": _Item(slot="head", item_id=1, name="Helm", ilvl=289)}
    stats = {1: {"stamina": 900}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        ceiling_fn=lambda slot: None,
    )
    r = rows[0]
    assert r.dead is False
    assert r.ceiling_upgrade is False
    assert r.offer_ceiling is None


def test_no_ceiling_fn_keeps_legacy_defaults(marginals_phys, dungeons_3):
    """Default kwargs keep dead/ceiling detection off entirely.

    Unpaired-slot rows are bit-identical to the legacy path. Paired
    ring/trinket rows DO baseline against both incumbents even with default
    kwargs (deliverable 3 applies unconditionally) — only the ownership
    flags are gated on the new kwargs."""
    vault = [_Item(slot="head", item_id=1, name="Helm", ilvl=272)]
    equipped = {"head": _Item(slot="head", item_id=1, name="Helm", ilvl=289)}
    stats = {1: {"stamina": 900}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    r = rows[0]
    assert r.dead is False
    assert r.ceiling_upgrade is False
    # Ownership is still observed (equipped same item_id at 289), but with
    # no ceiling to compare against neither flag may fire.
    assert r.owned_max_ilvl == 289
    assert r.offer_ceiling is None
    assert r.replaces_slot is None
    assert r.replaces_item is None


def test_ceiling_upgrade_when_owned_below_offer_ceiling(marginals_phys, dungeons_3):
    """Owned hero-track copy at 276; the offer's track reaches 298 — flag
    the ceiling upgrade (we can't read the owned copy's own track cap, so
    the flag fires on owned < offer ceiling, exactly as specced)."""
    vault = [_Item(slot="trinket1", item_id=5, name="HeartOfWind", ilvl=272)]
    equipped = {"trinket1": _Item(slot="trinket1", item_id=10, name="Prism", ilvl=298)}
    bag = {"trinket1": [_Item(slot="trinket1", item_id=5, name="HeartOfWind", ilvl=276)]}
    stats = {5: {"stamina": 900}, 10: {"stamina": 950}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        bag=bag,
        ceiling_fn=lambda slot: 298,
    )
    r = rows[0]
    assert r.dead is False
    assert r.ceiling_upgrade is True
    assert r.owned_max_ilvl == 276
    assert r.offer_ceiling == 298


def test_dead_rows_demoted_below_live_rows(marginals_phys, dungeons_3):
    """A dead offer sorts below every live one even when its ΔeHP is
    higher — it can't be the headline pick."""
    vault = [
        _Item(slot="head", item_id=1, name="DeadBigHelm", ilvl=272),
        _Item(slot="neck", item_id=2, name="LiveSmallNeck", ilvl=272),
    ]
    equipped = {
        "head": _Item(slot="head", item_id=9, name="WeakHelm", ilvl=272),
        "neck": _Item(slot="neck", item_id=11, name="OldNeck", ilvl=280),
    }
    # The helm offer is a big stat win over the weak equipped helm — but a
    # maxed copy already sits in the bag, so it's dead anyway.
    bag = {"head": [_Item(slot="head", item_id=1, name="DeadBigHelm", ilvl=289)]}
    stats = {
        1: {"stamina": 5000},
        2: {"stamina": 900},
        9: {"stamina": 100},
        11: {"stamina": 850},
    }
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        bag=bag,
        ceiling_fn=lambda slot: 289 if slot == "head" else 298,
    )
    assert [r.item.name for r in rows] == ["LiveSmallNeck", "DeadBigHelm"]
    assert rows[0].dead is False
    assert rows[1].dead is True
    # The dead row's ΔeHP really is larger — demotion, not re-scoring.
    assert rows[1].avg_delta_ehp > rows[0].avg_delta_ehp


def test_ownership_reads_owned_equipped_not_trial_equipped(marginals_phys, dungeons_3):
    """The app passes the parsed-from-SimC baseline as owned_equipped —
    a trial overlay containing the offer itself must not make the offer
    look owned (and dead)."""
    offer = _Item(slot="head", item_id=3, name="VaultHelm", ilvl=272)
    trial_equipped = {"head": _Item(slot="head", item_id=3, name="VaultHelm", ilvl=272)}
    real_equipped = {"head": _Item(slot="head", item_id=4, name="RealHelm", ilvl=272)}
    stats = {3: {"stamina": 900}, 4: {"stamina": 850}}
    rows = rank_vault_items(
        vault_items=[offer],
        equipped=trial_equipped,  # trial view: the offer is "worn"
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        owned_equipped=real_equipped,
        ceiling_fn=lambda slot: 272,
    )
    assert rows[0].dead is False
    assert rows[0].owned_max_ilvl is None


def test_paired_trinket_offer_baselines_against_weaker_incumbent(marginals_phys, dungeons_3):
    """A trinket1 offer must compare against BOTH equipped trinkets and
    keep the most favorable swap — replacing the weaker piece — naming
    which one it takes out. ΔDPS follows the same incumbent."""
    vault = [_Item(slot="trinket1", item_id=1, name="NewTrink", ilvl=280)]
    weak = _Item(slot="trinket2", item_id=11, name="WeakTrink", ilvl=280)
    equipped = {
        "trinket1": _Item(slot="trinket1", item_id=10, name="StrongTrink", ilvl=280),
        "trinket2": weak,
    }
    stats = {
        1: {"stamina": 1000, "haste_rating": 500},
        10: {"stamina": 1100, "haste_rating": 800},
        11: {"stamina": 600, "haste_rating": 100},
    }
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        dps_weights=_DPS_WEIGHTS,
    )
    r = rows[0]
    assert r.replaces_slot == "trinket2"
    assert r.replaces_item is weak
    assert r.avg_delta_ehp > 0  # vs the weak incumbent, not the strong one
    # ΔDPS vs the SAME incumbent: (500 - 100) × 1.40 / 1000 = 0.56
    assert r.delta_dps == pytest.approx(0.56)


def test_paired_target_resolves_for_chosen_incumbent(marginals_phys, dungeons_3):
    """'Match my gear' must normalize to the ilvl of the incumbent the swap
    actually replaces — not the offered slot's incumbent."""
    vault = [_Item(slot="trinket1", item_id=1, name="NewTrink", ilvl=272)]
    equipped = {
        "trinket1": _Item(slot="trinket1", item_id=10, name="StrongTrink", ilvl=298),
        "trinket2": _Item(slot="trinket2", item_id=11, name="WeakTrink", ilvl=280),
    }
    stats = {
        1: {"stamina": 900},
        10: {"stamina": 1200},
        11: {"stamina": 700},
    }
    targets = {"trinket1": 298, "trinket2": 280}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        target_ilvl_fn=lambda slot: targets[slot],
    )
    r = rows[0]
    assert r.replaces_slot == "trinket2"
    assert r.shown_at_ilvl == 280  # the chosen incumbent's target, not 298
    # new_stats scaled to the chosen target: 900 × 280/272
    assert r.new_stats["stamina"] == round(900 * 280 / 272)


def test_paired_offer_prefers_empty_partner_slot(marginals_phys, dungeons_3):
    """With the partner slot empty, filling it beats replacing anything —
    the row says so (replaces_slot set, replaces_item None)."""
    vault = [_Item(slot="trinket1", item_id=1, name="NewTrink", ilvl=272)]
    equipped = {"trinket1": _Item(slot="trinket1", item_id=10, name="Worn", ilvl=298)}
    stats = {1: {"stamina": 900}, 10: {"stamina": 950}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    r = rows[0]
    assert r.replaces_slot == "trinket2"
    assert r.replaces_item is None


def test_paired_tie_keeps_offered_slot(marginals_phys, dungeons_3):
    """Identical incumbents → deterministic: the offered slot wins the tie."""
    vault = [_Item(slot="trinket1", item_id=1, name="NewTrink", ilvl=272)]
    equipped = {
        "trinket1": _Item(slot="trinket1", item_id=10, name="TwinA", ilvl=280),
        "trinket2": _Item(slot="trinket2", item_id=10, name="TwinB", ilvl=280),
    }
    stats = {1: {"stamina": 900}, 10: {"stamina": 700}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert rows[0].replaces_slot == "trinket1"


def test_unpaired_slot_has_no_replaces_fields(marginals_phys, dungeons_3):
    vault = [_Item(slot="head", item_id=1, name="Helm", ilvl=272)]
    equipped = {"head": _Item(slot="head", item_id=10, name="OldHelm", ilvl=289)}
    stats = {1: {"stamina": 900}, 10: {"stamina": 800}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert rows[0].replaces_slot is None
    assert rows[0].replaces_item is None


# ─── dead_count_sentence ───────────────────────────────────────────────────────


def _row(dead: bool) -> VaultRow:
    return VaultRow(
        item=_Item(slot="head", item_id=1, name="X"),
        slot="head",
        per_dungeon=[],
        avg_delta_ehp=0.0,
        verdict_sentence="",
        dead=dead,
    )


def test_dead_count_sentence_empty_when_no_dead_rows():
    assert dead_count_sentence([_row(False), _row(False)]) == ""
    assert dead_count_sentence([]) == ""


def test_dead_count_sentence_singular():
    assert (
        dead_count_sentence([_row(True), _row(False), _row(False)])
        == "1 of 3 offers duplicates an item you already own at its ceiling."
    )


def test_dead_count_sentence_plural():
    assert (
        dead_count_sentence([_row(True), _row(True), _row(False)])
        == "2 of 3 offers duplicate items you already own at their ceiling."
    )


def test_paired_caption_identity_stable_under_trial_overlay(marginals_phys, dungeons_3):
    """Trialing an offer that beats BOTH paired incumbents must not flip
    replaces_slot to the other piece on the rerun. Incumbent SELECTION and
    the replaces_* caption identity read owned_equipped (the stable parsed
    baseline); only the displayed scores track the live overlay (caught by
    the 2026-06-10 pre-merge adversarial review)."""
    offer = _Item(slot="trinket1", item_id=1, name="NewTrink", ilvl=280)
    weak = _Item(slot="trinket1", item_id=10, name="WeakTrink", ilvl=280)
    strong = _Item(slot="trinket2", item_id=11, name="StrongTrink", ilvl=280)
    owned = {"trinket1": weak, "trinket2": strong}
    # Active trial: the offer itself already overlaid into trinket1. Under
    # the old selection-on-overlay logic the most favorable incumbent became
    # trinket2 (offer-vs-itself scores 0; offer beats StrongTrink), so the
    # caption flipped to the wrong piece.
    overlaid = {"trinket1": offer, "trinket2": strong}
    stats = {
        1: {"stamina": 2000, "haste_rating": 900},
        10: {"stamina": 600, "haste_rating": 100},
        11: {"stamina": 1100, "haste_rating": 800},
    }
    rows = rank_vault_items(
        vault_items=[offer],
        equipped=overlaid,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        dps_weights=_DPS_WEIGHTS,
        owned_equipped=owned,
    )
    r = rows[0]
    assert r.replaces_slot == "trinket1"
    assert r.replaces_item is weak
    # Displayed delta tracks the LIVE overlay: offer vs itself ≈ 0.
    assert r.avg_delta_ehp == pytest.approx(0.0)


# ─── Trinket-effect-aware scoring (vault_ranker_trinket_effect_gap, 2026-07-08) ─
#
# Before this, `rank_vault_items` scored every offer — including trinkets —
# off raw passive stats only, unlike the Gear tab's cross-slot ranker (which
# already routes trinket comparisons through `optimizer/trinket_db.py`'s
# proc/on-use-aware model via `trinket_swap_per_dungeon`). Real item ids below
# (Rotting Globule 252421, Solar Core Igniter 252418 — both real on-use-absorb
# M+ trinkets with a genuine, non-zero eHP contribution) are in the curated
# registry — see tests/test_trinket_swap_per_dungeon.py, which this mirrors.


def _trinket_brutoh() -> Character:
    return Character(
        name="Test",
        race="earthen",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=34176,
        armor_from_gear=5015,
        haste_rating=2318,
        crit_rating=1391,
        mastery_rating=1608,
        versatility_rating=296,
    )


def test_trinket_row_uses_registry_when_char_provided(marginals_phys, dungeons_3):
    """With `char=`, a known-vs-known trinket comparison must route through
    trinket_db, NOT the naive stats-only path — the two give different
    numbers here because the vault items carry only a throwaway Strength
    stat line (zero eHP marginal for this spec) that the naive path can't
    see past, while the registry credits the real proc/on-use effect.

    Both paired trinket slots are populated (with the SAME known incumbent)
    so the paired-slot "most favorable incumbent" logic can't pick the
    empty partner slot instead — a real empty trinket2 would otherwise win
    the comparison (upgrading from nothing scores higher than upgrading
    from an already-good absorb trinket) and drag trinket_warning back to
    True via that unrelated path, which isn't what this test is about."""
    vault = [_Item(slot="trinket1", item_id=252421, name="Rotting Globule", ilvl=298)]
    equipped = {
        "trinket1": _Item(slot="trinket1", item_id=252418, name="Solar Core Igniter", ilvl=298),
        "trinket2": _Item(slot="trinket2", item_id=252418, name="Solar Core Igniter", ilvl=298),
    }
    stats = {252421: {"strength": 50}, 252418: {"strength": 50}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        char=_trinket_brutoh(),
    )
    r = rows[0]
    assert r.trinket_warning is False
    # Naive stats-only comparison of two identical Strength lines is exactly
    # zero; the registry-based comparison must not be — that's the gap.
    assert r.avg_delta_ehp != 0.0


def test_trinket_row_falls_back_to_stats_only_without_char(marginals_phys, dungeons_3):
    """No `char` passed → every trinket row stays on the pre-fix stats-only
    path exactly (backward compatible for any caller that doesn't have a
    Character handy)."""
    vault = [_Item(slot="trinket1", item_id=252421, name="Rotting Globule", ilvl=298)]
    equipped = {
        "trinket1": _Item(slot="trinket1", item_id=252418, name="Solar Core Igniter", ilvl=298)
    }
    stats = {252421: {"strength": 50}, 252418: {"strength": 50}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    r = rows[0]
    assert r.trinket_warning is True
    assert r.avg_delta_ehp == pytest.approx(0.0)


def test_trinket_row_warns_when_candidate_unregistered_even_with_char(marginals_phys, dungeons_3):
    """`char` provided but the candidate trinket isn't in the registry →
    falls back to stats-only for THIS row, warning stays on."""
    vault = [_Item(slot="trinket1", item_id=99999999, name="UnknownTrink", ilvl=298)]
    equipped = {
        "trinket1": _Item(slot="trinket1", item_id=252418, name="Solar Core Igniter", ilvl=298)
    }
    stats = {99999999: {"stamina": 500}, 252418: {"strength": 50}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        char=_trinket_brutoh(),
    )
    r = rows[0]
    assert r.trinket_warning is True


def test_trinket_row_warns_when_equipped_unregistered_but_uses_registry(marginals_phys, dungeons_3):
    """Candidate known, equipped slot's trinket unknown → asymmetric
    comparison: still routed through the registry (real number, not a
    silent zero), but the warning stays on since only one side is modeled."""
    vault = [_Item(slot="trinket1", item_id=252421, name="Rotting Globule", ilvl=298)]
    equipped = {"trinket1": _Item(slot="trinket1", item_id=88888888, name="Unknown", ilvl=298)}
    stats = {252421: {"strength": 50}, 88888888: {"strength": 50}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        char=_trinket_brutoh(),
    )
    r = rows[0]
    assert r.trinket_warning is True
    assert r.avg_delta_ehp != 0.0


def test_non_trinket_row_never_carries_trinket_warning(marginals_phys, dungeons_3):
    """A non-trinket slot must never set trinket_warning, char or not."""
    vault = [_Item(slot="head", item_id=1, name="Helm", ilvl=298)]
    equipped = {"head": _Item(slot="head", item_id=10, name="OldHelm", ilvl=272)}
    stats = {1: {"stamina": 1200}, 10: {"stamina": 800}}
    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals_phys,
        dungeons=dungeons_3,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
        char=_trinket_brutoh(),
    )
    assert rows[0].trinket_warning is False
