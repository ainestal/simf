"""Tests for the upgrade-normalized comparison helpers (pure, no Streamlit)."""

from __future__ import annotations

from simf.ui.helpers.upgrade_compare import (
    CompareBounds,
    category_ceiling,
    category_for_slot,
    compare_bounds,
    crest_cost_label,
    curve_ilvl_points,
    upgrade_levels_between,
)

# ── slot → category ───────────────────────────────────────────────────────────


def test_category_for_slot():
    assert category_for_slot("trinket1") == "trinket"
    assert category_for_slot("trinket2") == "trinket"
    assert category_for_slot("main_hand") == "weapon"
    assert category_for_slot("off_hand") == "weapon"
    assert category_for_slot("chest") == "armor"
    assert category_for_slot("finger1") == "armor"


def test_category_ceiling_takes_smaller_of_season_and_account():
    ceilings = {"armor": 289, "trinket": 298, "weapon": 298}
    # armor season cap 289 < account 298 → 289
    assert category_ceiling("chest", ceilings, account_ceiling=298) == 289
    # trinket season cap 298 == account 298 → 298
    assert category_ceiling("trinket1", ceilings, account_ceiling=298) == 298
    # account lower than season cap → account wins
    assert category_ceiling("trinket1", ceilings, account_ceiling=295) == 295


def test_category_ceiling_none_when_unknown():
    assert category_ceiling("chest", None, account_ceiling=None) is None
    assert category_ceiling("chest", {}, account_ceiling=None) is None
    # account-only still resolves
    assert category_ceiling("chest", None, account_ceiling=298) == 298


# ── compare bounds ────────────────────────────────────────────────────────────


def test_compare_bounds_default_is_equipped_ilvl():
    b = compare_bounds(289, [259, 272], ceiling_ilvl=289)
    assert isinstance(b, CompareBounds)
    assert b.default_target == 289  # match my gear
    assert b.min_target == 259  # lowest piece in play (as-dropped floor)
    assert b.max_target == 289  # reachable ceiling


def test_compare_bounds_ceiling_never_below_equipped():
    # A nonsense ceiling below the equipped piece must not hide the maxed gear.
    b = compare_bounds(289, [259], ceiling_ilvl=200)
    assert b.max_target == 289


def test_compare_bounds_ceiling_allows_headroom_above_equipped():
    # Trinket equipped at 289 with a 298 ceiling — slider can climb to 298.
    b = compare_bounds(289, [272], ceiling_ilvl=298)
    assert b.max_target == 298


def test_compare_bounds_no_ceiling_falls_back_to_top_in_play():
    b = compare_bounds(289, [272, 259], ceiling_ilvl=None)
    assert b.max_target == 289


def test_compare_bounds_empty():
    b = compare_bounds(None, [], ceiling_ilvl=None)
    assert b.default_target is None
    assert b.min_target == 0


# ── upgrade-level / crest cost ────────────────────────────────────────────────


def test_upgrade_levels_between_ceil_division():
    assert upgrade_levels_between(259, 289, 3) == 10  # 30 / 3
    assert upgrade_levels_between(259, 290, 3) == 11  # 31 / 3 → ceil
    assert upgrade_levels_between(289, 289, 3) == 0  # no upgrade
    assert upgrade_levels_between(289, 259, 3) == 0  # downgrade target → 0
    assert upgrade_levels_between(None, 289, 3) == 0
    assert upgrade_levels_between(259, 289, 0) == 0  # bad step


def test_crest_cost_label():
    assert crest_cost_label(259, 289, 3) == "+30 ilvl · ≈10 upgrade levels"
    assert crest_cost_label(286, 289, 3) == "+3 ilvl · ≈1 upgrade level"  # singular
    assert crest_cost_label(289, 289, 3) == ""  # nothing to upgrade
    assert crest_cost_label(289, 259, 3) == ""  # downgrade → empty


# ── curve points ──────────────────────────────────────────────────────────────


def test_curve_ilvl_points_includes_base_and_ceiling():
    pts = curve_ilvl_points(259, 289, 3)
    assert pts[0] == 259  # base anchor
    assert pts[-1] == 289  # ceiling always included
    # stepped by 3 between
    assert pts[1] == 262


def test_curve_ilvl_points_appends_offgrid_ceiling():
    # 259 stepping by 6 → 259,265,271,...,289 isn't on the grid (259+6k),
    # so the real ceiling 289 is appended.
    pts = curve_ilvl_points(259, 289, 6)
    assert pts[-1] == 289
    assert pts[-2] < 289


def test_curve_ilvl_points_exclude_base():
    pts = curve_ilvl_points(259, 289, 3, include_base=False)
    assert 259 not in pts
    assert pts[-1] == 289


def test_curve_ilvl_points_degenerate():
    assert curve_ilvl_points(0, 289, 3) == []
    assert curve_ilvl_points(289, 259, 3) == []  # max below base
    assert curve_ilvl_points(259, 289, 0) == []


# ── compare-mode resolver (shared by Vault tab + slot dialog) ─────────────────

from simf.ui.helpers.upgrade_compare import (  # noqa: E402
    COMPARE_AS_DROPPED,
    COMPARE_BASE_MODES,
    COMPARE_CUSTOM,
    COMPARE_MATCH_GEAR,
    COMPARE_MAX_UPGRADE,
    resolve_target_ilvl,
)


def test_base_modes_are_the_three_semantic_ones():
    assert COMPARE_BASE_MODES == (COMPARE_MATCH_GEAR, COMPARE_MAX_UPGRADE, COMPARE_AS_DROPPED)


def test_resolve_target_ilvl_as_dropped_is_none():
    assert resolve_target_ilvl(COMPARE_AS_DROPPED, equipped_ilvl=289, ceiling=298) is None


def test_resolve_target_ilvl_match_gear_is_equipped():
    assert resolve_target_ilvl(COMPARE_MATCH_GEAR, equipped_ilvl=289, ceiling=298) == 289
    # Empty slot (no equipped ilvl) falls back to the ceiling.
    assert resolve_target_ilvl(COMPARE_MATCH_GEAR, equipped_ilvl=None, ceiling=298) == 298


def test_resolve_target_ilvl_max_upgrade_is_ceiling():
    assert resolve_target_ilvl(COMPARE_MAX_UPGRADE, equipped_ilvl=289, ceiling=298) == 298
    # No known ceiling falls back to equipped.
    assert resolve_target_ilvl(COMPARE_MAX_UPGRADE, equipped_ilvl=289, ceiling=None) == 289


def test_resolve_target_ilvl_custom_uses_value():
    assert (
        resolve_target_ilvl(COMPARE_CUSTOM, equipped_ilvl=289, ceiling=298, custom_value=294) == 294
    )
    assert (
        resolve_target_ilvl(COMPARE_CUSTOM, equipped_ilvl=289, ceiling=298, custom_value=None)
        is None
    )
