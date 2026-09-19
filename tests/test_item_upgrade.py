"""Unit tests for the upgrade-impact scorer.

These tests pin the v1 math:
- linear ilvl ratio for stat scaling (consistent with trinket_db);
- closed-form ΔeHP against survivability marginals;
- rank order is by ΔeHP (the survivability question), with ΔDPS surfaced
  but not used for sorting;
- defensive skipping when ilvl is unknown or zero, when stats are empty,
  or when ``ilvl_delta`` ≤ 0 (callers must guard, but the function must
  not raise).
"""

from __future__ import annotations

from simf.optimizer.item_upgrade import (
    UpgradeRow,
    compute_upgrade_impact,
    scale_stats,
)


def test_scale_stats_linear_ratio() -> None:
    base = {"stamina": 1000, "armor_from_gear": 500, "haste_rating": 200}
    # +6 ilvls on a 660 → 666 base item: scale = 666/660 = 1.00909...
    scaled = scale_stats(base, 660, 666)
    assert scaled["stamina"] == round(1000 * 666 / 660)
    assert scaled["armor_from_gear"] == round(500 * 666 / 660)
    assert scaled["haste_rating"] == round(200 * 666 / 660)


def test_scale_stats_no_op_when_ilvls_match() -> None:
    base = {"stamina": 1000}
    assert scale_stats(base, 660, 660) == {"stamina": 1000}


def test_scale_stats_no_op_on_unknown_ilvl() -> None:
    base = {"stamina": 1000}
    # Refuses to extrapolate when base ilvl is unknown.
    assert scale_stats(base, 0, 666) == {"stamina": 1000}


def _fake_marginals() -> dict:
    """Realistic shape for survivability marginals — eHP per unit rating.

    Numbers chosen so a +1000 stamina upgrade scores +71k eHP under a
    100% physical school mix, matching the documented Brutoh order of
    magnitude.
    """
    return {
        "stamina": {"p": 71.0, "m": 71.0},
        "armor_from_gear": {"p": 246.0, "m": 0.0},
        "haste_rating": {"p": 81.0, "m": 81.0},
        "crit_rating": {"p": 9.0, "m": 9.0},
        "mastery_rating": {"p": 80.0, "m": 80.0},
        "versatility_rating": {"p": 311.0, "m": 311.0},
    }


def _fake_dungeons(phys: float = 1.0) -> list[dict]:
    return [{"id": "test", "abbrev": "TST", "school_mix": {"physical": phys}}]


def _dps_weights() -> dict[str, float]:
    return {"haste": 1.40, "crit": 1.10, "mastery": 1.05, "vers": 0.80}


def test_compute_upgrade_impact_orders_by_delta_ehp() -> None:
    """Two equipped pieces: an armor-heavy chest and a haste-only neck.

    Under all-physical school mix, the chest should rank above the neck
    because armor is the highest physical marginal in the fake table.
    """
    equipped = {
        "chest": {"item_id": 1, "ilvl": 660},
        "neck": {"item_id": 2, "ilvl": 660},
    }
    item_stats = {
        "chest": {"stamina": 1000, "armor_from_gear": 4000},
        "neck": {"haste_rating": 800},
    }
    item_ilvls = {"chest": 660, "neck": 660}
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats=item_stats,
        item_ilvls=item_ilvls,
        item_names={"chest": "Chest", "neck": "Neck"},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
    )
    assert [r.slot for r in rows] == ["chest", "neck"]
    assert rows[0].delta_ehp > rows[1].delta_ehp
    assert rows[0].current_ilvl == 660
    assert rows[0].upgraded_ilvl == 666


def test_compute_upgrade_impact_skips_unknown_ilvl() -> None:
    equipped = {
        "chest": {"item_id": 1, "ilvl": 660},
        "neck": {"item_id": 2, "ilvl": None},
    }
    item_stats = {
        "chest": {"stamina": 1000},
        "neck": {"haste_rating": 800},
    }
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats=item_stats,
        item_ilvls={"chest": 660, "neck": 0},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
    )
    assert {r.slot for r in rows} == {"chest"}


def test_compute_upgrade_impact_returns_empty_on_zero_delta() -> None:
    rows = compute_upgrade_impact(
        equipped={"chest": {"item_id": 1, "ilvl": 660}},
        item_stats={"chest": {"stamina": 1000}},
        item_ilvls={"chest": 660},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=0,
    )
    assert rows == []


def test_compute_upgrade_impact_marks_trinkets() -> None:
    equipped = {
        "trinket1": {"item_id": 100, "ilvl": 660},
        "chest": {"item_id": 1, "ilvl": 660},
    }
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats={"trinket1": {"stamina": 800}, "chest": {"stamina": 1000}},
        item_ilvls={"trinket1": 660, "chest": 660},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
    )
    trinket_row = next(r for r in rows if r.slot == "trinket1")
    chest_row = next(r for r in rows if r.slot == "chest")
    assert trinket_row.is_trinket is True
    assert chest_row.is_trinket is False


def test_compute_upgrade_impact_school_mix_weights_armor() -> None:
    """All-physical scores armor; all-magic doesn't (armor magic_marginal=0)."""
    equipped = {"chest": {"item_id": 1, "ilvl": 660}}
    item_stats = {"chest": {"armor_from_gear": 4000}}
    item_ilvls = {"chest": 660}
    phys_rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats=item_stats,
        item_ilvls=item_ilvls,
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(phys=1.0),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
    )
    magic_rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats=item_stats,
        item_ilvls=item_ilvls,
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(phys=0.0),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
    )
    assert phys_rows[0].delta_ehp > 0
    assert magic_rows[0].delta_ehp == 0


def test_compute_upgrade_impact_clamps_to_slot_ceiling() -> None:
    """+6 against an item at 290 with a 295 ceiling clamps to 295 (effective delta=5)."""
    equipped = {"chest": {"item_id": 1, "ilvl": 290}}
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats={"chest": {"stamina": 1000, "armor_from_gear": 4000}},
        item_ilvls={"chest": 290},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
        slot_ceilings={"chest": 295},
    )
    assert len(rows) == 1
    assert rows[0].current_ilvl == 290
    assert rows[0].upgraded_ilvl == 295
    assert rows[0].is_capped is True


def test_compute_upgrade_impact_excludes_slots_at_cap() -> None:
    """A slot already at its ceiling drops out — no partial-upgrade theatre."""
    equipped = {
        "chest": {"item_id": 1, "ilvl": 295},
        "neck": {"item_id": 2, "ilvl": 290},
    }
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats={
            "chest": {"stamina": 1000},
            "neck": {"haste_rating": 800},
        },
        item_ilvls={"chest": 295, "neck": 290},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
        slot_ceilings={"chest": 295, "neck": 295},
    )
    assert {r.slot for r in rows} == {"neck"}
    assert rows[0].is_capped is True


def test_compute_upgrade_impact_respects_per_slot_ceilings_not_one_flat_number() -> None:
    """Armor and weapon/trinket slots reach DIFFERENT ceilings in Midnight
    12.0.5 (289 vs. 298 — Voidcore only applies to weapons/trinkets, armor's
    crest track maxes out lower). A chest piece already at armor's ceiling
    must be excluded even though a trinket at the SAME ilvl still has real
    headroom under its own, higher ceiling — a single shared cap number
    would wrongly suggest "upgrading" the maxed chest (real bug, user-
    flagged 2026-07-04: the ranker showed 5 armor slots at their crest-track
    max as if a +6 bump were achievable, when neither a crest — nothing left
    to buy — nor a Voidcore — wrong slot type — could actually do it)."""
    equipped = {
        "chest": {"item_id": 1, "ilvl": 289},
        "trinket1": {"item_id": 2, "ilvl": 289},
    }
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats={
            "chest": {"stamina": 1000},
            "trinket1": {"stamina": 1000},
        },
        item_ilvls={"chest": 289, "trinket1": 289},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
        slot_ceilings={"chest": 289, "trinket1": 298},
    )
    assert {r.slot for r in rows} == {"trinket1"}
    assert rows[0].upgraded_ilvl == 295
    assert rows[0].is_capped is False


def test_compute_upgrade_impact_no_cap_preserves_legacy_behavior() -> None:
    """No slot_ceilings entry for a slot keeps the pre-cap math — backward-compat guard."""
    equipped = {"chest": {"item_id": 1, "ilvl": 660}}
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats={"chest": {"stamina": 1000}},
        item_ilvls={"chest": 660},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
    )
    assert rows[0].upgraded_ilvl == 666
    assert rows[0].is_capped is False


def test_compute_upgrade_impact_dps_fraction_uses_weights() -> None:
    """Haste-only upgrade → positive DPS fraction proportional to the haste weight."""
    equipped = {"neck": {"item_id": 2, "ilvl": 660}}
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats={"neck": {"haste_rating": 1000}},
        item_ilvls={"neck": 660},
        item_names={},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=10000.0,
        ilvl_delta=10,
    )
    row = rows[0]
    # ilvl 660 → 670 = +10/660 ≈ +1.515% on a 1000 haste base → ~+15 haste delta.
    # weight 1.40 / pool 10000 → ~+0.21% DPS.
    assert row.delta_dps_pct > 0
    # Sanity: shouldn't be wildly above the expected ballpark.
    assert row.delta_dps_pct < 0.01


def test_compute_upgrade_impact_handles_itemspec_objects() -> None:
    """Accepts dataclass ItemSpec-like objects in addition to dicts."""
    from simf.io.simc_import import ItemSpec

    equipped = {"chest": ItemSpec(slot="chest", item_id=12345, ilvl=660)}
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats={"chest": {"stamina": 1000}},
        item_ilvls={"chest": 660},
        item_names={"chest": "Test Chest"},
        marginals=_fake_marginals(),
        dungeons=_fake_dungeons(),
        dps_stat_weights=_dps_weights(),
        base_secondary_pool=15000.0,
        ilvl_delta=6,
    )
    assert len(rows) == 1
    assert isinstance(rows[0], UpgradeRow)
    assert rows[0].item_id == 12345


# ---------------------------------------------------------------------------
# ehp_curve — per-piece upgrade curve. Powers the "what's the impact of crests
# on this piece?" leveling view (self-gain mode) and the "where does this vault
# piece overtake my equipped?" crossover (comparison mode).
# ---------------------------------------------------------------------------

from simf.optimizer.item_upgrade import CurvePoint, ehp_curve  # noqa: E402

_CURVE_M = {"stamina": {"p": 30.0, "m": 22.0}}
_CURVE_D = [{"id": "a", "abbrev": "A", "school_mix": {"physical": 1.0}}]


def test_ehp_curve_self_gain_anchored_at_zero_and_monotonic():
    pts = ehp_curve({"stamina": 1000}, 259, [259, 272, 289], _CURVE_M, _CURVE_D)
    assert [p.ilvl for p in pts] == [259, 272, 289]
    assert pts[0].delta_ehp == 0.0  # no gain at the piece's own base ilvl
    assert pts[1].delta_ehp > 0
    assert pts[2].delta_ehp > pts[1].delta_ehp  # more ilvl → more eHP
    assert all(isinstance(p, CurvePoint) for p in pts)


def test_ehp_curve_comparison_crossover():
    """A vault piece below equipped ilvl trails as-dropped but overtakes once
    upgraded high enough — the curve crosses zero between the two points."""
    equipped = {"stamina": 1000}
    pts = ehp_curve({"stamina": 950}, 259, [259, 289], _CURVE_M, _CURVE_D, baseline_stats=equipped)
    assert pts[0].delta_ehp < 0  # at 259, 950 < 1000 → behind equipped
    assert pts[1].delta_ehp > 0  # at 289, 950×289/259 ≈ 1060 > 1000 → ahead


def test_ehp_curve_ceiling_flags_unreachable_tail():
    pts = ehp_curve({"stamina": 1000}, 259, [272, 289, 298], _CURVE_M, _CURVE_D, ceiling_ilvl=289)
    flags = {p.ilvl: p.is_reachable for p in pts}
    assert flags == {272: True, 289: True, 298: False}


def test_ehp_curve_empty_on_bad_input():
    assert ehp_curve({}, 259, [289], _CURVE_M, _CURVE_D) == []
    assert ehp_curve({"stamina": 1000}, 0, [289], _CURVE_M, _CURVE_D) == []
