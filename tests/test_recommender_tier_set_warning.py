"""The paperdoll recommender flags a swap that breaks an active tier set.

The recommender's ΔeHP does NOT model the 2pc/4pc set bonus, so a "+X eHP"
swap that drops you below a threshold is usually a net survival LOSS the number
can't see (user #3, 2026-06-30: "this is not taking into account the armor
set at all"). `_per_slot_picks` flags such a pick (`breaks_set`) via the same
`swap_breaks_threshold` detector the slot dialog already warns with, and
`_slot_card_html` surfaces it on the card.
"""

from __future__ import annotations

from simf.io.simc_import import ItemSpec
from simf.ui.app import _flag_set_breaks, _slot_card_html, _SlotPick
from simf.ui.helpers.gear_list import SlotRow

# Brutoh's real Night Ender's Vesture tier-set ids (head/shoulder/chest/hands),
# mirroring tests/test_tier_sets.py — so the wiring test hits the real registry.
_NIGHT_ENDER = (249950, 249955, 249953, 249951)
_NON_TIER = 151333


class _IdItem:
    def __init__(self, item_id: int) -> None:
        self.item_id = item_id


def _equipped_4pc() -> dict:
    slots = ("head", "shoulder", "chest", "hands")
    return {s: _IdItem(i) for s, i in zip(slots, _NIGHT_ENDER, strict=True)}


def _swap(slot: str) -> _SlotPick:
    return _SlotPick(
        slot=slot,
        item=_IdItem(_NON_TIER),
        is_swap=True,
        delta_ehp=40_000.0,
        delta_dps=0.0,
        composite=0.7,
        has_warning=False,
    )


def _row() -> SlotRow:
    item = ItemSpec(slot="chest", item_id=100, name="Tier Chest", ilvl=289)
    return SlotRow(slot="chest", slot_label="Chest", item=item, name="Tier Chest", ilvl=289)


def _swap_pick(*, breaks_set: str | None) -> _SlotPick:
    return _SlotPick(
        slot="chest",
        item=ItemSpec(slot="chest", item_id=200, name="Non-tier Breastplate", ilvl=295),
        is_swap=True,
        delta_ehp=41_464.0,
        delta_dps=0.0,
        composite=0.7,
        has_warning=False,
        breaks_set=breaks_set,
    )


def test_breaks_set_field_defaults_none() -> None:
    p = _SlotPick(
        slot="x",
        item=None,
        is_swap=False,
        delta_ehp=0.0,
        delta_dps=0.0,
        composite=0.0,
        has_warning=False,
    )
    assert p.breaks_set is None


def test_card_warns_when_swap_breaks_a_set() -> None:
    html = _slot_card_html(_row(), _swap_pick(breaks_set="4pc Night Ender's Vesture"))
    assert "gear-card-setbreak" in html
    assert "breaks 4pc Night Ender's Vesture" in html
    assert "set bonus not in this number" in html
    # The ΔeHP gain still shows — we flag the break, we don't hide the number.
    assert "eHP" in html


def test_card_has_no_setbreak_warning_when_set_is_kept() -> None:
    html = _slot_card_html(_row(), _swap_pick(breaks_set=None))
    assert "gear-card-setbreak" not in html
    assert "breaks" not in html


# ── the wiring: _flag_set_breaks populates breaks_set off the real registry ─────


def test_flag_set_breaks_marks_a_tier_breaking_swap() -> None:
    """Swapping a tier head (4pc equipped) for a non-tier head drops to 3pc →
    the pick is flagged with the broken bonus."""
    picks = {"head": _swap("head")}
    _flag_set_breaks(picks, _equipped_4pc(), "protection_warrior")
    assert picks["head"].breaks_set is not None
    assert picks["head"].breaks_set.startswith("4pc")
    assert "Night Ender's Vesture" in picks["head"].breaks_set


def test_flag_set_breaks_leaves_a_non_tier_slot_swap_alone() -> None:
    """A swap in a slot that isn't part of the set can't break it → no flag."""
    picks = {"neck": _swap("neck")}
    _flag_set_breaks(picks, _equipped_4pc(), "protection_warrior")
    assert picks["neck"].breaks_set is None


def test_flag_set_breaks_skips_non_swaps() -> None:
    baseline = _SlotPick(
        slot="head",
        item=None,
        is_swap=False,
        delta_ehp=0.0,
        delta_dps=0.0,
        composite=0.0,
        has_warning=False,
    )
    picks = {"head": baseline}
    _flag_set_breaks(picks, _equipped_4pc(), "protection_warrior")
    assert picks["head"].breaks_set is None
