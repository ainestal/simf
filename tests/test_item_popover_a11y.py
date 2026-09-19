"""Regression tests for item-detail accessibility on the gear surfaces.

History
-------
The gear rows once carried a per-row item-details ``st.popover`` (a ⓘ chip)
as the keyboard / screen-reader path to an item's stats, because the Wowhead
Power tooltip fires only on mouse hover (WCAG 2.1.1 / 1.4.13).

2026-06-20: the popover was **removed** at the user's request — it added a
redundant ⓘ button to every row when the same item detail is already reachable
two other ways:

* the item **name** is a focusable ``<a class="item-link">`` Wowhead link with
  a visible ``:focus-visible`` ring — activating it (Enter) opens the item's
  Wowhead page, the keyboard path to full item detail (WCAG 2.1.1);
* the item **icon** is a focusable ``<a class="icon-tooltip-anchor">`` Wowhead
  link as well (same destination), and fires the hover tooltip for mouse users.

So the functionality (reach an item's detail) stays operable by keyboard; only
the redundant inline popover went away. These tests pin that contract so a
future refactor can't silently re-introduce the ⓘ clutter — or, worse, drop
the focusable link and leave only the hover-only tooltip.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

_UI_DIR = Path(__file__).parent.parent / "src" / "simf" / "ui"
APP_PATH = _UI_DIR / "app.py"
APP_SOURCE = APP_PATH.read_text()
# `_item_link_html` / `_slot_card_html` moved into `simf.ui.item_html` (panel
# split, PR 2); the focus-ring CSS stays in app.py.
ITEM_HTML_SOURCE = (_UI_DIR / "item_html.py").read_text()
# `_render_vault_cell` moved into `simf.ui.vault_panel`, and `_slot_dialog` (with
# its nested `_render_alt_row` + "Currently equipped:" line) into
# `simf.ui.slot_dialog` (L3 render-panel split). The inline-a11y source
# assertions below read the module where each now lives.
VAULT_PANEL_SOURCE = (_UI_DIR / "vault_panel.py").read_text()
SLOT_DIALOG_SOURCE = (_UI_DIR / "slot_dialog.py").read_text()


@pytest.fixture
def app() -> AppTest:
    # The Gear-tab sheet renders the rows; first paint can scan bag+vault on
    # cold cache. Same 30s timeout as test_app_smoke.
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _load_demo(app: AppTest) -> None:
    """Click 'Load sample build' so the Gear surface has items to render."""
    app.run()
    for btn in app.button:
        if "sample build" in (btn.label or "").lower():
            btn.click()
            break
    app.run()


def test_item_details_popover_helper_is_gone() -> None:
    """The ``_render_item_popover`` helper was removed wholesale. Guard against
    a refactor re-introducing the redundant per-row ⓘ popover."""
    assert "_render_item_popover" not in APP_SOURCE, (
        "`_render_item_popover` was removed 2026-06-20 (redundant ⓘ chip — "
        "item detail is reachable via the focusable item-name/icon Wowhead "
        "links). Do not re-introduce it; if you need keyboard item detail, "
        "the item-link anchor is the path."
    )


def test_gear_card_exposes_item_detail_via_focusable_link_not_popover() -> None:
    """The gear card builder must render the item name as a focusable Wowhead
    link (the keyboard path to item detail) and must NOT render a popover."""
    card_start = ITEM_HTML_SOURCE.find("def _slot_card_html(")
    assert card_start >= 0, "`_slot_card_html` missing"
    next_def = ITEM_HTML_SOURCE.find("\ndef ", card_start + 1)
    card_body = ITEM_HTML_SOURCE[card_start : next_def if next_def > 0 else len(ITEM_HTML_SOURCE)]
    assert "_item_link_html(" in card_body, (
        "_slot_card_html must render the item name via _item_link_html "
        "(focusable a.item-link) — the keyboard path to item detail (WCAG 2.1.1)."
    )
    assert "_icon_link_html_q(" in card_body, (
        "_slot_card_html must render the icon via _icon_link_html_q "
        "(focusable a.icon-tooltip-anchor + quality border)."
    )
    assert "_render_item_popover" not in card_body, (
        "_slot_card_html must NOT render an item-details popover — it was "
        "removed as redundant 2026-06-20. The focusable item-name link carries a11y."
    )


def test_item_link_has_focus_visible_ring() -> None:
    """WCAG 2.4.7: the item-name Wowhead link is the primary keyboard
    affordance on the gear rows now, so it must carry a visible focus ring
    (the default is near-invisible on the cream surface)."""
    assert "a.item-link:focus-visible" in APP_SOURCE, (
        "global stylesheet must give `a.item-link` a :focus-visible ring — it "
        "is the primary keyboard affordance on the gear rows (WCAG 2.4.7)."
    )
    assert "a.icon-tooltip-anchor:focus-visible" in APP_SOURCE, (
        "global stylesheet must give `a.icon-tooltip-anchor` a :focus-visible "
        "ring — the icon is also a focusable Wowhead link (WCAG 2.4.7)."
    )


def test_dead_popover_css_is_removed() -> None:
    """The popover-only CSS hooks (`popover-wowhead-link`,
    `slot-popover-icon-only`) must be gone — dead rules once the helper is."""
    assert "popover-wowhead-link" not in APP_SOURCE, (
        "`popover-wowhead-link` CSS is dead — the popover it styled was removed."
    )
    assert "slot-popover-icon-only" not in APP_SOURCE, (
        "`slot-popover-icon-only` CSS is dead — the popover marker it scoped was removed."
    )


def test_vault_cell_uses_inline_a11y_not_popover() -> None:
    """``_render_vault_cell`` shows item-level-SCALED stats (``r.new_stats``)
    via a focusable name link + an always-visible static stat line — the
    inline a11y contract that carries WCAG 2.1.1 without a popover."""
    cell_start = VAULT_PANEL_SOURCE.find("def _render_vault_cell(")
    assert cell_start >= 0, "`_render_vault_cell` missing"
    cell_end = VAULT_PANEL_SOURCE.find("\ndef ", cell_start)
    cell_body = VAULT_PANEL_SOURCE[cell_start : cell_end if cell_end > 0 else None]
    assert "_item_link_html(" in cell_body, (
        "vault cell must render the item name as a focusable Wowhead link "
        "(_item_link_html) — the keyboard path to the item (WCAG 2.1.1)."
    )
    assert "format_item_stats(" in cell_body and "st.caption(stats_line)" in cell_body, (
        "vault cell must render the stat line as always-visible static text "
        "(format_item_stats → st.caption(stats_line)) (WCAG 2.1.1)."
    )
    assert "_render_item_popover" not in cell_body


def test_inline_link_has_no_onmouseenter_only_handler() -> None:
    """The inline ``<a>`` Wowhead anchor is the hover trigger for mouse users.
    It must NOT carry an inline ``onmouseenter`` handler with no keyboard
    equivalent — that would re-create the WCAG 1.4.13 problem."""
    link_start = ITEM_HTML_SOURCE.find("def _item_link_html(")
    assert link_start >= 0, "`_item_link_html` helper missing"
    link_body = ITEM_HTML_SOURCE[link_start : link_start + 1500]
    for forbidden in ("onmouseenter=", "onmouseover=", "onmouseout="):
        assert forbidden not in link_body, (
            f"_item_link_html must not emit a hard-coded `{forbidden}` "
            "attribute — that would re-introduce a hover-only handler "
            "with no keyboard equivalent (WCAG 2.1.1)."
        )


def test_slot_dialog_current_item_uses_inline_a11y_not_popover() -> None:
    """The slot dialog's "Currently equipped:" line shows the user's existing
    item via a focusable Wowhead-linked name + an always-visible static stat
    line (WCAG 2.1.1), not a popover."""
    anchor = SLOT_DIALOG_SOURCE.find("Currently equipped:")
    assert anchor >= 0, "'Currently equipped:' line missing from _slot_dialog"
    block = SLOT_DIALOG_SOURCE[anchor : anchor + 800]
    assert "_item_link_html(current)" in block, (
        "the equipped line must render the item name as a focusable Wowhead link (WCAG 2.1.1)."
    )
    assert "format_item_stats(" in block and "st.caption(stats_line)" in block, (
        "the equipped line must render its stat line as always-visible static "
        "text (format_item_stats → st.caption(stats_line)) (WCAG 2.1.1)."
    )
    assert "_render_item_popover" not in block


def test_alt_row_uses_inline_a11y_not_popover() -> None:
    """``_render_alt_row`` renders each candidate via a focusable Wowhead-linked
    name + an always-visible static stat line (WCAG 2.1.1), not a popover."""
    alt_row_start = SLOT_DIALOG_SOURCE.find("def _render_alt_row(")
    assert alt_row_start >= 0, "`_render_alt_row` (nested in _slot_dialog) missing"
    alt_row_body = SLOT_DIALOG_SOURCE[alt_row_start : alt_row_start + 4000]
    assert "_item_link_html(a.item)" in alt_row_body, (
        "alt row must render the candidate name as a focusable Wowhead link (WCAG 2.1.1)."
    )
    assert "format_item_stats(" in alt_row_body and "st.caption(stats_line)" in alt_row_body, (
        "alt row must render the stat line as always-visible static text "
        "(format_item_stats → st.caption(stats_line)) (WCAG 2.1.1)."
    )
    assert "_render_item_popover" not in alt_row_body


def test_no_item_details_popover_in_rendered_app_after_demo_load(app: AppTest) -> None:
    """End-to-end: after loading the demo character, the rendered gear sheet
    must contain NO 'Item details · ...' popover (it was removed) — and the
    gear rows must still render their per-slot buttons so the sheet works."""
    _load_demo(app)
    assert not app.exception, f"app raised after demo load: {app.exception}"
    popovers = list(app.get("popover"))
    labels = [getattr(getattr(p.proto, "popover", None), "label", "") or "" for p in popovers]
    item_labels = [lbl for lbl in labels if lbl.startswith("Item details · ")]
    assert not item_labels, (
        "The per-row item-details popover was removed 2026-06-20, but the "
        f"rendered app still contains item-details popovers: {item_labels}"
    )
    # Sanity: the gear sheet still renders (card paperdoll HTML present).
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "gear-card" in body, "gear sheet rendered no cards after demo load"
