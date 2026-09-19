"""`_render_gear_per_dungeon_breakdown` (recommend.py) — the Gear tab's
consolidated per-dungeon eHP section, added 2026-07-27 to close a real
Vault/Gear parity gap: Vault's per-offer cards and the slot-browse dialog
both showed a per-dungeon breakdown, the Gear tab's own paperdoll cards
never did. Placement (a section below the paperdoll, one `st.expander` per
swap slot) was confirmed with the user directly — the alternative
(per-card expanders) would have required breaking the paperdoll's
single-combined-HTML-block architecture.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_breakdown_script() -> None:
    from simf.io.simc_import import ItemSpec
    from simf.optimizer.per_dungeon import DungeonScore
    from simf.ui.models import _SlotPick
    from simf.ui.recommend import _render_gear_per_dungeon_breakdown

    swap_item = ItemSpec(slot="head", item_id=42, name="TestHelm", ilvl=289)
    picks = {
        "head": _SlotPick(
            slot="head",
            item=swap_item,
            is_swap=True,
            delta_ehp=2400.0,
            delta_dps=0.0,
            composite=2400.0,
            has_warning=False,
            per_dungeon=[
                DungeonScore(dungeon_id="d1", abbrev="ABC", delta_ehp=800.0, name="Alpha Caverns"),
                DungeonScore(dungeon_id="d2", abbrev="XYZ", delta_ehp=1600.0, name="Xeno Yard"),
            ],
        ),
        "waist": _SlotPick(
            slot="waist",
            item=None,
            is_swap=False,
            delta_ehp=0.0,
            delta_dps=0.0,
            composite=0.0,
            has_warning=False,
        ),
    }
    _render_gear_per_dungeon_breakdown(picks)


def test_no_swap_slots_renders_nothing():
    def _script() -> None:
        from simf.ui.models import _SlotPick
        from simf.ui.recommend import _render_gear_per_dungeon_breakdown

        picks = {
            "waist": _SlotPick(
                slot="waist",
                item=None,
                is_swap=False,
                delta_ehp=0.0,
                delta_dps=0.0,
                composite=0.0,
                has_warning=False,
            )
        }
        _render_gear_per_dungeon_breakdown(picks)

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception, f"render error: {at.exception}"
    assert not at.caption
    assert not at.expander


def test_swap_without_per_dungeon_data_renders_nothing():
    """A swap slot with no `per_dungeon` scored (e.g. an unmodeled proc
    trinket) must not render an empty/misleading expander."""

    def _script() -> None:
        from simf.io.simc_import import ItemSpec
        from simf.ui.models import _SlotPick
        from simf.ui.recommend import _render_gear_per_dungeon_breakdown

        item = ItemSpec(slot="trinket1", item_id=1, name="MysteryTrinket", ilvl=289)
        picks = {
            "trinket1": _SlotPick(
                slot="trinket1",
                item=item,
                is_swap=True,
                delta_ehp=500.0,
                delta_dps=0.0,
                composite=500.0,
                has_warning=True,
            )
        }
        _render_gear_per_dungeon_breakdown(picks)

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception, f"render error: {at.exception}"
    assert not at.caption
    assert not at.expander


def test_swap_with_per_dungeon_renders_expander_with_rows():
    at = AppTest.from_function(_render_breakdown_script)
    at.run()
    assert not at.exception, f"render error: {at.exception}"

    captions = [str(c.value) for c in at.caption]
    assert any("per-dungeon" in c.lower() for c in captions)

    # Only the swap slot (head) gets an expander — the non-swap waist slot
    # doesn't, even though it's in the same picks dict.
    assert len(at.expander) == 1
    expander = at.expander[0]
    assert "Helm" in expander.label
    assert "TestHelm" in expander.label

    body = "\n".join(str(m.value) for m in expander.markdown)
    assert "Alpha Caverns" in body
    assert "+800" in body
    assert "Xeno Yard" in body
    assert "+1,600" in body


def test_multiple_swap_slots_render_separate_expanders():
    def _script() -> None:
        from simf.io.simc_import import ItemSpec
        from simf.optimizer.per_dungeon import DungeonScore
        from simf.ui.models import _SlotPick
        from simf.ui.recommend import _render_gear_per_dungeon_breakdown

        head_item = ItemSpec(slot="head", item_id=42, name="TestHelm", ilvl=289)
        legs_item = ItemSpec(slot="legs", item_id=43, name="TestLegs", ilvl=285)
        picks = {
            "head": _SlotPick(
                slot="head",
                item=head_item,
                is_swap=True,
                delta_ehp=800.0,
                delta_dps=0.0,
                composite=800.0,
                has_warning=False,
                per_dungeon=[
                    DungeonScore(
                        dungeon_id="d1", abbrev="ABC", delta_ehp=800.0, name="Alpha Caverns"
                    )
                ],
            ),
            "legs": _SlotPick(
                slot="legs",
                item=legs_item,
                is_swap=True,
                delta_ehp=400.0,
                delta_dps=0.0,
                composite=400.0,
                has_warning=False,
                per_dungeon=[
                    DungeonScore(
                        dungeon_id="d1", abbrev="ABC", delta_ehp=400.0, name="Alpha Caverns"
                    )
                ],
            ),
        }
        _render_gear_per_dungeon_breakdown(picks)

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception, f"render error: {at.exception}"
    assert len(at.expander) == 2
    labels = {e.label for e in at.expander}
    assert any("TestHelm" in label for label in labels)
    assert any("TestLegs" in label for label in labels)


def test_empty_slot_swap_falls_back_to_slot_label():
    """A swap into an otherwise-empty slot (item=None on the pick side
    shouldn't happen for a real swap, but the label fallback is exercised
    defensively — mirrors `_card_for`'s own empty-slot handling)."""

    def _script() -> None:
        from simf.optimizer.per_dungeon import DungeonScore
        from simf.ui.models import _SlotPick
        from simf.ui.recommend import _render_gear_per_dungeon_breakdown

        picks = {
            "waist": _SlotPick(
                slot="waist",
                item=None,
                is_swap=True,
                delta_ehp=300.0,
                delta_dps=0.0,
                composite=300.0,
                has_warning=False,
                per_dungeon=[
                    DungeonScore(
                        dungeon_id="d1", abbrev="ABC", delta_ehp=300.0, name="Alpha Caverns"
                    )
                ],
            )
        }
        _render_gear_per_dungeon_breakdown(picks)

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception, f"render error: {at.exception}"
    assert len(at.expander) == 1
    assert "Belt" in at.expander[0].label
