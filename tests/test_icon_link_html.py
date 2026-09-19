"""Regression test for Wowhead-linked icon anchor on paperdoll + upgrade rows.

User report 2026-05-21: hovering an item icon (image) did not trigger
the Wowhead Power tooltip; only the text link did. Root cause was that
the row layouts rendered the icon ``<img>`` as a bare element, outside
any anchor — Power.js attaches tooltips to anchors carrying a wowhead
URL, so an unwrapped image gets nothing.

The fix wraps the icon in its own anchor (``_icon_link_html``). These
tests pin two structural contracts:

  1. ``_icon_link_html`` returns an ``<a class="icon-tooltip-anchor"
     href="https://www.wowhead.com/item=N" …>…</a>`` for a known item —
     so Power.js can attach the tooltip.
  2. Falling back to a bare ``<div>`` when the item is None / has no
     resolvable icon (defensive — the slot rows can be empty).
"""

from __future__ import annotations

from simf.io.simc_import import ItemSpec
from simf.ui.app import _icon_link_html


def test_icon_link_html_wraps_image_in_wowhead_anchor(monkeypatch) -> None:
    # Icon resolution normally goes through fetch_item_icon_wowhead, which
    # hits the network on a cold cache — and Wowhead 403s CI runners. The
    # contract under test is the anchor HTML, not icon resolution; pin the
    # icon name so the test is deterministic offline (CI run 27272318241).
    # `_icon_link_html` moved to `simf.ui.item_html` (panel split, PR 2) and
    # resolves `_icon_for_item` there, so patch it on that module.
    import simf.ui.item_html as item_html_mod

    monkeypatch.setattr(item_html_mod, "_icon_for_item", lambda spec: "inv_helmet_03")
    item = ItemSpec(slot="head", item_id=151333, name="TestHelm", ilvl=285)
    html = _icon_link_html(item, "slot-row-icon")
    assert '<a class="icon-tooltip-anchor"' in html, html
    assert "https://www.wowhead.com/item=151333" in html, html
    assert "<img" in html
    assert 'class="slot-row-icon"' in html
    # New-tab affordance + accessible name match the existing item link
    # behaviour so keyboard users get the same nav contract.
    assert 'target="_blank"' in html
    assert "aria-label" in html


def test_icon_link_html_falls_back_for_missing_item() -> None:
    # An empty slot must not crash — bare placeholder div, no anchor.
    html = _icon_link_html(None, "slot-row-icon")
    assert html == '<div class="slot-row-icon"></div>'


def test_icon_link_html_includes_css_class_for_focus_ring(monkeypatch) -> None:
    """The CSS class on the anchor is what app.py uses to strip default
    link chrome and apply a keyboard focus ring. Pin it so a rename
    breaks loudly."""
    import simf.ui.item_html as item_html_mod

    monkeypatch.setattr(item_html_mod, "_icon_for_item", lambda spec: "inv_helmet_03")
    item = ItemSpec(slot="head", item_id=12345, name="Helm", ilvl=280)
    html = _icon_link_html(item, "slot-row-icon")
    assert 'class="icon-tooltip-anchor"' in html


def test_icon_link_html_size_defaults_to_medium(monkeypatch) -> None:
    """Callers that don't care about resolution (the default) keep fetching
    Wowhead's 'medium' (36px) source — the P4 icon-size bump only affects
    callers that explicitly opt into 'large'."""
    import simf.ui.item_html as item_html_mod

    monkeypatch.setattr(item_html_mod, "_icon_for_item", lambda spec: "inv_helmet_03")
    item = ItemSpec(slot="head", item_id=12345, name="Helm", ilvl=280)
    html = _icon_link_html(item, "slot-row-icon")
    assert "/medium/" in html


def test_icon_link_html_size_large_passthrough(monkeypatch) -> None:
    """P4 polish (2026-07-02): passing size='large' fetches Wowhead's 56px
    source instead of the 36px default — used by the enlarged 52px icons on
    the paperdoll cards and upgrade-panel rows so the browser downsamples a
    sharp source rather than upscaling a soft one."""
    import simf.ui.item_html as item_html_mod

    monkeypatch.setattr(item_html_mod, "_icon_for_item", lambda spec: "inv_helmet_03")
    item = ItemSpec(slot="head", item_id=12345, name="Helm", ilvl=280)
    html = _icon_link_html(item, "gear-card-icon", size="large")
    assert "/large/" in html
    assert "/medium/" not in html
