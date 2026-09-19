"""Regression tests for the gear paperdoll's item-quality border (P1a).

Gear-UI redesign 2026-06-17 (ratified): the paperdoll should read like the
in-game character sheet, where each item icon carries a quality-colored
frame. ItemSpec has no quality field, so quality is inferred from an EPIC
FLOOR (every endgame M+ piece is at least epic) with a legendary promotion
allowlist — both classified in ``constants.yaml: gear.quality_border``.

These tests pin three contracts:
  1. ``_item_quality_class`` applies the epic floor, promotes the allowlist,
     and stays empty for an id-less item.
  2. ``_icon_link_html_q`` appends the quality class to the icon markup
     (mutation guard — reverting the wiring drops ``q-epic``).
  3. The CSS tokens + rules that paint the border exist in app.py.
"""

from __future__ import annotations

from pathlib import Path

from simf.io.simc_import import ItemSpec
from simf.ui.app import _icon_link_html_q, _item_quality_class

APP = (Path(__file__).resolve().parents[1] / "src" / "simf" / "ui" / "app.py").read_text()


def test_item_quality_class_epic_floor() -> None:
    # Unknown endgame item → epic floor (purple), never an unstyled frame.
    item = ItemSpec(slot="head", item_id=151333, name="TestHelm", ilvl=285)
    assert _item_quality_class(item) == "q-epic"


def test_item_quality_class_empty_for_id_less_item() -> None:
    # Empty slot / placeholder has no id → no quality class, neutral frame.
    assert _item_quality_class(ItemSpec(slot="head", item_id=0)) == ""
    assert _item_quality_class(None) == ""


def test_item_quality_class_promotes_legendary_allowlist(monkeypatch) -> None:
    # `_item_quality_class` moved to `simf.ui.format_html` (foundation split,
    # PR 1) and resolves `load_constants` there, so patch it on that module.
    import simf.ui.format_html as fmt_mod

    monkeypatch.setattr(
        fmt_mod,
        "load_constants",
        lambda: {"gear": {"quality_border": {"default": "epic", "legendary_item_ids": [12345]}}},
    )
    assert _item_quality_class(ItemSpec(slot="finger1", item_id=12345)) == "q-legendary"
    # An id NOT on the allowlist still floors to epic under the same config.
    assert _item_quality_class(ItemSpec(slot="finger2", item_id=999)) == "q-epic"


def test_icon_link_html_q_appends_quality_class(monkeypatch) -> None:
    # Pin the icon name so the test is deterministic offline (Wowhead 403s CI).
    # `_icon_link_html_q` moved to `simf.ui.item_html` (panel split, PR 2) and
    # resolves `_icon_for_item` there, so patch it on that module.
    import simf.ui.item_html as item_html_mod

    monkeypatch.setattr(item_html_mod, "_icon_for_item", lambda spec: "inv_helmet_03")
    item = ItemSpec(slot="head", item_id=151333, name="TestHelm", ilvl=285)
    html = _icon_link_html_q(item, "slot-row-icon")
    # The quality fragment rides along with the base class on the same node.
    assert 'class="slot-row-icon q-epic"' in html, html


def test_icon_link_html_q_falls_back_cleanly_for_missing_item() -> None:
    # None item → bare placeholder div, no trailing-space class artifact.
    assert _icon_link_html_q(None, "slot-row-icon") == '<div class="slot-row-icon"></div>'


def test_icon_link_html_q_size_passthrough(monkeypatch) -> None:
    """P4 polish (2026-07-02): the quality-border wrapper forwards `size` to
    the underlying icon-URL builder, same as the bare `_icon_link_html`."""
    import simf.ui.item_html as item_html_mod

    monkeypatch.setattr(item_html_mod, "_icon_for_item", lambda spec: "inv_helmet_03")
    item = ItemSpec(slot="head", item_id=151333, name="TestHelm", ilvl=285)
    html = _icon_link_html_q(item, "gear-card-icon", size="large")
    assert "/large/" in html
    assert 'class="gear-card-icon q-epic"' in html, html


def test_quality_border_css_present() -> None:
    # The tokens that carry the canonical Blizzard quality hexes…
    assert "--q-epic:" in APP
    assert "--q-legendary:" in APP
    # …and the rules that paint the icon frame from them.
    assert ".slot-row-icon.q-epic" in APP
    assert ".slot-row-icon.q-legendary" in APP
    assert "var(--q-epic)" in APP
    assert "var(--q-legendary)" in APP
