"""Tests for the v0.9 slot-grouped gear list helper.

Builds the 16-row data structure that drives Surface 1's gear column.
Pure function — caller injects equipped items + optional warnings callback.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from simf.ui.helpers.gear_list import (
    CANONICAL_SLOT_ORDER,
    build_slot_rows,
    display_name,
    format_item_stats,
    wowhead_icon_url,
    wowhead_url,
)


@dataclass
class _Item:
    slot: str
    item_id: int = 0
    name: str | None = None
    ilvl: int | None = None
    enchant_id: int | None = None
    gem_ids: list = None
    bonus_ids: list = None
    crafted_stats: list = None
    crafting_quality: int | None = None


def test_canonical_order_has_16_slots():
    assert len(CANONICAL_SLOT_ORDER) == 16


def test_canonical_order_matches_blizzard_paperdoll_order():
    """Head→Foot, then rings, trinkets, weapons."""
    assert CANONICAL_SLOT_ORDER[0] == "head"
    assert CANONICAL_SLOT_ORDER[-2:] == ["main_hand", "off_hand"]
    # No duplicates
    assert len(set(CANONICAL_SLOT_ORDER)) == 16


def test_build_returns_one_row_per_canonical_slot():
    rows = build_slot_rows(equipped={})
    assert len(rows) == 16
    assert [r.slot for r in rows] == CANONICAL_SLOT_ORDER


def test_empty_slot_renders_with_no_item():
    rows = build_slot_rows(equipped={})
    row = next(r for r in rows if r.slot == "head")
    assert row.item is None
    assert row.ilvl is None
    assert row.name is None
    assert row.warnings == []


def test_equipped_item_populates_row():
    helm = _Item(slot="head", item_id=12345, name="Helm of Foo", ilvl=658)
    rows = build_slot_rows(equipped={"head": helm})
    row = next(r for r in rows if r.slot == "head")
    assert row.item is helm
    assert row.name == "Helm of Foo"
    assert row.ilvl == 658


def test_slot_label_is_human_readable():
    """slot_label is for UI rendering — not just the raw slot key."""
    rows = build_slot_rows(equipped={})
    by_slot = {r.slot: r.slot_label for r in rows}
    assert by_slot["head"] == "Helm"
    assert by_slot["finger1"] == "Ring 1"
    assert by_slot["off_hand"] == "Off hand"


def test_warnings_callback_runs_per_slot():
    """If a warnings_fn is provided, it is called once per slot with (slot, item)."""
    calls: list[tuple[str, object]] = []

    def warnings(slot, item):
        calls.append((slot, item))
        return ["missing enchant"] if slot == "back" else []

    helm = _Item(slot="head", ilvl=658, name="Helm")
    cloak = _Item(slot="back", ilvl=658, name="Cloak", enchant_id=None)
    rows = build_slot_rows(
        equipped={"head": helm, "back": cloak},
        warnings_fn=warnings,
    )

    assert len(calls) == 16  # called for every slot
    head_row = next(r for r in rows if r.slot == "head")
    back_row = next(r for r in rows if r.slot == "back")
    assert head_row.warnings == []
    assert back_row.warnings == ["missing enchant"]


def test_warnings_callback_optional():
    rows = build_slot_rows(equipped={"head": _Item(slot="head", name="X")})
    assert all(r.warnings == [] for r in rows)


def test_slot_row_is_frozen():
    """Rows are immutable data carriers — UI shouldn't mutate them."""
    rows = build_slot_rows(equipped={})
    with pytest.raises((AttributeError, Exception)):
        rows[0].slot = "neck"  # type: ignore[misc]


def test_display_name_falls_back_to_item_id():
    assert display_name(_Item(slot="head", item_id=12345)) == "Item #12345"
    assert display_name(_Item(slot="head", item_id=12345, name="Helm of Foo")) == "Helm of Foo"


def test_wowhead_url_includes_bonus_ids():
    item = _Item(slot="head", item_id=12345, bonus_ids=[1, 2, 3])
    assert wowhead_url(item) == "https://www.wowhead.com/item=12345?bonus=1:2:3"


def test_wowhead_url_without_bonuses():
    item = _Item(slot="head", item_id=12345)
    assert wowhead_url(item) == "https://www.wowhead.com/item=12345"


def test_wowhead_url_none_when_no_item_id():
    assert wowhead_url(_Item(slot="head", item_id=0)) is None


def test_format_item_stats_empty_when_no_stats():
    assert format_item_stats(None) == ""
    assert format_item_stats({}) == ""


def test_format_item_stats_orders_primary_first_then_secondaries():
    stats = {
        "haste_rating": 240,
        "mastery_rating": 180,
        "stamina": 1507,
        "strength": 110,
    }
    out = format_item_stats(stats)
    assert out == "Str 110 · Sta 1,507 · Hst 240 · Mst 180"


def test_format_item_stats_skips_zero_values():
    stats = {"haste_rating": 240, "crit_rating": 0, "mastery_rating": 180}
    out = format_item_stats(stats)
    assert "Crit" not in out
    assert "Hst 240" in out and "Mst 180" in out


def test_wowhead_icon_url_default_size():
    assert wowhead_icon_url("inv_helmet_98") == (
        "https://wow.zamimg.com/images/wow/icons/medium/inv_helmet_98.jpg"
    )


def test_wowhead_icon_url_small_size():
    assert wowhead_icon_url("inv_sword_42", size="small") == (
        "https://wow.zamimg.com/images/wow/icons/small/inv_sword_42.jpg"
    )


def test_wowhead_icon_url_none_when_empty():
    assert wowhead_icon_url(None) is None
    assert wowhead_icon_url("") is None
