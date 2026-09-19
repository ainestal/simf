"""Regression tests for Wowhead tooltip z-index — the tooltip MUST
render in front of every other floating layer, especially
`st.dialog` / `st.popover` modals.

User report 2026-05-16: in the slot-dialog popup (`@st.dialog("Slot
details")`), hovering an item link rendered the Wowhead Power tooltip
BEHIND the dialog. Root cause: the CSS targeted only legacy
`.wowhead-tooltip` / `#wowhead-tooltip` / `[id*="wowhead"]` /
`[class*="powerinfo"]` — the modern Power.js build uses `.whtt` and
`[id^="whtt"]` selectors, which fell through to the BaseWeb modal's
default z-index.

Two test layers:

1. **CSS-content test** (this file, always runs) — asserts the CSS in
   `app.py` contains the right selectors and a sufficiently high
   z-index. Catches future refactors that drop the `.whtt` selectors
   or lower the z-index below the BaseWeb modal range.

2. **Playwright integration test** (optional, requires running
   Streamlit) — drives the live UI, opens the slot dialog, hovers a
   Wowhead link, and asserts the computed z-index of the tooltip
   element exceeds the dialog's. See `test_wowhead_tooltip_live`
   below — skipped when `http://localhost:8501` doesn't respond.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parent.parent / "src" / "simf" / "ui" / "app.py"


def _css_block() -> str:
    """Return the global <style> block from `app.py`."""
    src = APP_PY.read_text()
    start = src.find("<style>")
    end = src.find("</style>", start)
    assert start >= 0 and end > start, "global <style> block missing from app.py"
    return src[start:end]


def test_wowhead_tooltip_css_targets_whtt_selectors():
    """Modern Wowhead Power renders the tooltip with class/id starting
    with `whtt-`. The CSS must target at least one of these selectors
    so the tooltip's z-index rule applies."""
    css = _css_block()
    assert ".whtt" in css or '[id^="whtt"]' in css or '[class^="whtt"]' in css, (
        "Wowhead tooltip CSS must target modern .whtt / [id^='whtt'] / "
        "[class^='whtt'] selectors so the modern Power.js tooltip layer "
        "gets the z-index rule. Without it, the tooltip renders BEHIND "
        "st.dialog / st.popover modals."
    )


def test_wowhead_tooltip_zindex_beats_baseweb_modal():
    """BaseWeb modals (Streamlit st.dialog / st.popover) sit around
    z-index 10000. The Wowhead tooltip MUST sit above that. We require
    at least 100000 — anything less indicates the rule was lowered
    without considering the modal stacking case."""
    import re

    css = _css_block()
    # Find every z-index rule in the block and pick the max.
    zindex_values = []
    for match in re.finditer(r"z-index:\s*(\d+)", css):
        zindex_values.append(int(match.group(1)))
    assert zindex_values, "no z-index rules found in app.py <style> block"
    max_zindex = max(zindex_values)
    assert max_zindex >= 100_000, (
        f"highest z-index in app.py is {max_zindex:,}, but Wowhead "
        "tooltip needs at least 100,000 to beat BaseWeb modal stacking "
        "(st.dialog and st.popover sit at ~10,000). User-reported "
        "regression 2026-05-16."
    )


def test_wowhead_tooltip_has_opaque_background():
    """Wowhead Power's own background CSS takes a beat to load. Until
    then the dark app surface shows through and the tooltip reads as
    transparent. We enforce an explicit dark background-color rule on
    the Wowhead selectors so the tooltip is readable from first paint.

    User report 2026-05-16: tooltip transparent + unreadable on the
    dark app theme.
    """
    css = _css_block()
    # The Wowhead block must include an explicit background-color rule.
    # Anchor on the whtt selector group + a `background-color:` line in
    # the same declaration block.
    assert "background-color" in css, (
        "Wowhead tooltip CSS must set an explicit `background-color` "
        "so the tooltip is readable before Wowhead's own CSS loads."
    )
    # The selector group must include at least one .whtt-family selector
    # in the same block as a background-color rule.
    block_start = css.find(".whtt")
    assert block_start >= 0, ".whtt selector missing from Wowhead tooltip CSS"
    # Look ahead a reasonable window — the declaration body should be
    # within ~600 chars of the selector group.
    block_tail = css[block_start : block_start + 600]
    assert "background-color" in block_tail, (
        "background-color rule must appear in the same block as the "
        ".whtt selector — found block but no background-color in it."
    )


def test_wowhead_tooltip_does_not_force_position_fixed():
    """Wowhead's own positioner uses `position: absolute` and computes
    coords against the document. Forcing `position: fixed !important`
    clamps it to the viewport and collapses the tooltip's computed
    height, bleeding content past the border.

    Previous CSS had `position: fixed !important` as a stale workaround
    for an unrelated `overflow: visible` bug on stMain. Both were
    reverted 2026-05-16 after a user reported content overflow on
    Brutoh's Warworn Cleaver hover.
    """
    css = _css_block()
    # The Wowhead block must NOT include `position: fixed !important`.
    # Look around the .whtt selector group.
    block_start = css.find(".whtt")
    if block_start < 0:
        return  # covered by test_wowhead_tooltip_css_targets_whtt_selectors
    block_tail = css[block_start : block_start + 600]
    assert "position: fixed !important" not in block_tail, (
        "Wowhead tooltip CSS must NOT force `position: fixed` on the "
        ".whtt block — it overrides Power.js's own positioner and "
        "collapses the tooltip's height. User-reported regression "
        "2026-05-16."
    )


@pytest.mark.skipif(True, reason="manual: requires live Streamlit on :8501")
def test_wowhead_tooltip_live():
    """End-to-end Playwright check.

    Skipped by default — flip the `skipif` decorator to enable locally
    after `make ui` is running. The test:

      1. Loads the Brutoh demo, switches to Gear tab.
      2. Clicks the Main-hand recommendation button to open the slot dialog.
      3. Waits for the dialog to render.
      4. Hovers a Wowhead item link inside the dialog.
      5. Inspects the computed z-index of the `.whtt` tooltip element
         and the `[role="dialog"]` element.
      6. Asserts the tooltip's z-index is strictly greater.

    Not in the default test run because it requires a long-lived
    Streamlit server and the Wowhead CDN to be reachable. The CSS tests
    above are the durable regression guard.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.firefox.launch()
        page = browser.new_context(viewport={"width": 1400, "height": 1100}).new_page()
        page.goto("http://localhost:8501", wait_until="networkidle")
        page.wait_for_timeout(3000)
        page.get_by_role("button", name="Load Brutoh demo").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(4000)
        page.get_by_role("button", name="All gear").click()
        page.wait_for_timeout(2000)
        # The Main-hand recommendation button label varies — look for a
        # button whose text starts with the recommendation arrow.
        rec_btn = page.locator("button", has_text="→").first
        rec_btn.click()
        page.wait_for_timeout(2000)
        # Hover the Wowhead item link inside the dialog.
        page.get_by_role("link", name="Warworn Cleaver").first.hover()
        page.wait_for_timeout(2500)

        # Inspect computed z-index. Wowhead Power renders into `.whtt`
        # (modern build).
        zinfo = page.evaluate(
            """() => {
                const tooltip = document.querySelector('.whtt, #wowhead-tooltip, [id^="whtt"]');
                const dialog = document.querySelector('[role="dialog"]');
                if (!tooltip || !dialog) return null;
                return {
                    tooltip: parseInt(getComputedStyle(tooltip).zIndex || '0', 10),
                    dialog: parseInt(getComputedStyle(dialog).zIndex || '0', 10),
                };
            }"""
        )
        browser.close()
        assert zinfo is not None, "tooltip or dialog element not found in DOM"
        assert zinfo["tooltip"] > zinfo["dialog"], (
            f"tooltip z-index ({zinfo['tooltip']}) must beat dialog "
            f"({zinfo['dialog']}) so it renders in front"
        )
