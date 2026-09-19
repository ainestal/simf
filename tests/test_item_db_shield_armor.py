"""F12 — `resolve_equipped_stats` must surface the off-hand slot's armor as
`shield_armor` in isolation so the SimC block-value formula has its input.

The aggregate `armor_from_gear` total is unchanged for backwards compatibility;
`shield_armor` is added as a new key when the off-hand item has armor (i.e.
is a shield).
"""

from __future__ import annotations

from simf.io import item_db
from simf.io.simc_import import ItemSpec


def _stub_resolver(monkeypatch, per_slot: dict[str, dict[str, int]]):
    """Make `fetch_item_stats_for_spec` deterministic — return whichever stats
    dict the test specifies for each ItemSpec, keyed by slot."""

    def _stub(item_spec, region="eu", class_spec=None):
        return per_slot.get(item_spec.slot)

    monkeypatch.setattr(item_db, "fetch_item_stats_for_spec", _stub)


def test_resolve_equipped_stats_extracts_shield_armor_from_off_hand(monkeypatch):
    _stub_resolver(
        monkeypatch,
        {
            "head": {"armor_from_gear": 800, "stamina": 1000},
            "chest": {"armor_from_gear": 1200, "stamina": 1500},
            "off_hand": {"armor_from_gear": 931, "stamina": 842},
        },
    )
    items = {
        "head": ItemSpec(slot="head", item_id=1),
        "chest": ItemSpec(slot="chest", item_id=2),
        "off_hand": ItemSpec(slot="off_hand", item_id=237831),
    }
    totals = item_db.resolve_equipped_stats(items)
    # Aggregate still sums correctly.
    assert totals["armor_from_gear"] == 800 + 1200 + 931
    assert totals["stamina"] == 1000 + 1500 + 842
    # Per-slot shield armor surfaced for the F12 block-value formula.
    assert totals["shield_armor"] == 931


def test_resolve_equipped_stats_omits_shield_armor_when_off_hand_has_no_armor(monkeypatch):
    """Brewmaster fist weapons, VDH warglaives — off-hand exists but no armor
    stat. `shield_armor` should not appear in the dict at all."""
    _stub_resolver(
        monkeypatch,
        {
            "head": {"armor_from_gear": 800},
            "off_hand": {"strength": 200, "stamina": 400},  # weapon, no armor
        },
    )
    items = {
        "head": ItemSpec(slot="head", item_id=1),
        "off_hand": ItemSpec(slot="off_hand", item_id=2),
    }
    totals = item_db.resolve_equipped_stats(items)
    assert "shield_armor" not in totals


def test_resolve_equipped_stats_omits_shield_armor_when_no_off_hand(monkeypatch):
    """Specs that don't equip an off-hand item at all."""
    _stub_resolver(
        monkeypatch,
        {"head": {"armor_from_gear": 800, "stamina": 1000}},
    )
    items = {"head": ItemSpec(slot="head", item_id=1)}
    totals = item_db.resolve_equipped_stats(items)
    assert "shield_armor" not in totals
    assert totals["armor_from_gear"] == 800
