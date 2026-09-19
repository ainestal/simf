"""Headless-browser screenshot of the running Streamlit app.

Usage: screenshot.py <out.png> [URL] [--demo] [--tab gear|vault] [--wait-ms N]
       [--settle-ms N] [--browser firefox|chromium] [--zoom PCT]

Cross-engine + zoom verification
---------------------------------
Firefox is still the default (preserves every existing caller), but a real
CSS overflow bug (`.gear-col` missing `min-width: 0`, fixed in PR #279) was
called "unreproducible" after four attempts that all happened to use
headless Firefox only — it only showed up in headless Chromium at the exact
same viewport (Firefox's text-metric rounding just didn't tip it over).
`--browser chromium` exists so a "can't reproduce" verdict can actually rule
out the other engine instead of assuming Firefox speaks for both.

`--zoom` (default 100) applies CSS `zoom` on `<html>` after the page loads.
Verified live (2026-07-07) against both installed Playwright browsers here
(Firefox 1511 / Chromium 1217): `document.documentElement.style.zoom` is
honored by both and increases `scrollWidth` relative to a fixed
`clientWidth` at higher zoom, i.e. it reproduces width-overflow bugs more
readily than 100% — a cheap complementary stress test to switching engines.
No width/height fallback was needed. See CONTRIBUTING.md's "Screenshot any UI
change" rule for when to reach for `--zoom 200` / the other engine.

Failure model
-------------
Earlier revisions caught every Playwright exception and printed it to stderr
while still calling `page.screenshot(...)` and returning 0. That produced
silent-success-but-empty PNGs whenever a click target was missing — three
review iterations in a row were misled by "demo loaded" screenshots that
showed only the landing surface.

This module now treats every requested interaction as load-bearing: if the
caller passed `--demo`, the demo MUST load; if it passed `--tab gear`, that
tab MUST be clickable; etc. Failures raise `SystemExit(2)` with a clear
message before the screenshot ever lands on disk, so a downstream agent
reading a "successful" PNG can trust it.

Settle model
------------
The 'Gear' tab label appears the instant a character lands in session state —
but the gear surface then spends ~10-20s computing sim-derived survivability
marginals behind a "Calibrating stat weights…" spinner. A fixed `--wait-ms`
after the demo/tab click would capture that mid-rerun frame (spinner showing,
surfaces blank). `_settle()` instead waits for the spinner to appear (cold
load) and then clear (compute done) before a short final paint, so the PNG
shows the settled surface. Best-effort — a settle timeout just proceeds.
"""

from __future__ import annotations

import argparse
import contextlib
import sys

from playwright.sync_api import sync_playwright

# Sentinel text that only appears AFTER a character is loaded. The Vault/Gear
# sub-nav (a button pair, not `st.tabs` — see app.py::_set_gear_subtab, fixed
# 2026-07-11) renders once a character is in session state, so finding a
# "Vault"-labeled button is the cheapest proof that the demo click actually
# populated the app — but NOT that the surface finished computing (see
# _settle / the Settle model above). "Vault" (not "Gear") specifically:
# there's ALSO a top-level "Gear & vault" nav button that renders on the cold
# landing page before any character loads, so its label can't discriminate;
# no top-level button says "Vault".
DEMO_LOADED_SENTINEL = "Vault"

# Widget `key=` for the two Vault/Gear sub-nav buttons (app.py's
# `_render_gear_subtabs`) — Streamlit wraps any keyed widget in a
# `.st-key-<key>` CSS class, so `--tab gear|vault` clicks by key rather than
# by label. Labels are a UI/copy concern that can change (the header nav
# button and this sub-nav button were both once bare "Gear" — the exact
# ambiguity a label-based click above had to route around with `.last`);
# the key is a stable identifier that isn't.
_SUBTAB_KEYS = {"vault": "gear_subtab_vault", "gear": "gear_subtab_gear"}

# Streamlit renders `st.spinner(...)` as a [data-testid="stSpinner"] element.
# The gear surface's survivability-marginals spinner is the dominant cold-load
# cost; this element reliably attaches when the compute starts and detaches
# when it finishes (verified by DOM probe — text/`:visible` signals are flaky).
_SPINNER_TESTID = '[data-testid="stSpinner"]'


def _fatal(msg: str) -> None:
    """Emit msg and exit non-zero so the caller sees a real failure."""
    print(f"screenshot.py: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _settle(page, settle_ms: int, final_ms: int) -> None:
    """Wait for Streamlit to finish its rerun + marginals compute before the
    screenshot, so we capture the settled surface rather than a mid-rerun
    frame. Best-effort: every wait swallows its timeout and proceeds — the
    capture is never worse than the old fixed-wait behaviour."""
    spinner = page.locator(_SPINNER_TESTID)
    # Wait for the marginals spinner to ATTACH (the rerun reaches the gear
    # surface a beat after the click — `attached` waits for it to appear even
    # though it's absent at call time, so we don't short-circuit on the race).
    # Cached load (disk-cached marginals): it never attaches and this times out
    # harmlessly within the short grace.
    with contextlib.suppress(Exception):
        spinner.first.wait_for(state="attached", timeout=8000)
    # Compute done when the spinner detaches. Generous cap for a cold Pi.
    with contextlib.suppress(Exception):
        spinner.first.wait_for(state="detached", timeout=settle_ms)
    with contextlib.suppress(Exception):
        page.wait_for_load_state("networkidle")
    page.wait_for_timeout(final_ms)


def _run_interactions(page, args) -> None:
    """Everything from initial load through the final hover, replayable
    against any page. Split out of ``main`` so a page whose viewport turned
    out too short can be replaced with a freshly, correctly-sized one and
    have the exact same interactions replayed against it — see the
    "full_page truncation" fix in ``main`` for why a mid-session resize on
    the ORIGINAL page isn't enough."""
    page.goto(args.url, wait_until="networkidle")
    if args.zoom != 100:
        page.evaluate(f"document.documentElement.style.zoom = '{args.zoom}%'")
    page.wait_for_timeout(args.wait_ms)
    if args.demo:
        try:
            page.get_by_role("button", name="Load sample build").click()
            page.wait_for_load_state("networkidle")
            # Wait for the gear surface to finish computing, not just for
            # the click to register — the 'Gear' tab appears long before
            # the marginals spinner clears.
            _settle(page, args.settle_ms, args.wait_ms)
        except Exception as exc:
            _fatal(f"--demo click failed: {exc}")
        # Verify the demo character actually loaded. The Vault/Gear
        # sub-nav only renders post-load; if it isn't visible the click
        # landed on a stale DOM and the screenshot would be empty.
        if page.get_by_role("button", name=DEMO_LOADED_SENTINEL).count() == 0:
            _fatal(
                f"--demo click reported success but the '{DEMO_LOADED_SENTINEL}' "
                "sub-nav button never appeared — character did not load."
            )
    if args.tab:
        try:
            page.locator(f".st-key-{_SUBTAB_KEYS[args.tab]} button").click()
            # Gear/vault sub-tabs both trigger the marginals compute — settle.
            _settle(page, args.settle_ms, args.wait_ms)
        except Exception as exc:
            _fatal(f"--tab '{args.tab}' click failed: {exc}")
    # Expanders run before clicks so a `--expander ... --click ...`
    # invocation can open a closed expander first, then click a
    # button revealed inside it. Streamlit's `<details><summary>`
    # widgets aren't role=button and can't be reached by --click.
    for label in args.expander:
        try:
            # Streamlit expander summary is a <summary> inside a
            # <details data-testid="stExpander"> — clicking the
            # summary toggles the details. get_by_text finds it.
            summary = page.get_by_text(label, exact=False).first
            summary.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(args.wait_ms)
        except Exception as exc:
            _fatal(f"--expander '{label}' failed: {exc}")
    for label in args.click:
        try:
            page.get_by_role("button", name=label).first.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(args.wait_ms)
        except Exception as exc:
            _fatal(f"--click '{label}' failed: {exc}")
    for chip in args.remove_chip:
        try:
            # BaseWeb tag close button — find the parent chip by visible
            # text, then click its inner SVG close icon.
            chip_root = page.locator(f'[data-baseweb="tag"]:has-text("{chip}")').first
            chip_root.locator("span[role='presentation']").first.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(args.wait_ms)
        except Exception as exc:
            _fatal(f"--remove-chip '{chip}' failed: {exc}")
    if args.hover_link:
        try:
            page.get_by_role("link", name=args.hover_link).first.hover()
            # Wowhead Power tooltip fetches on hover; wait for it to render.
            page.wait_for_timeout(2500)
        except Exception as exc:
            _fatal(f"--hover-link '{args.hover_link}' failed: {exc}")


_MAX_RESIZE_ATTEMPTS = 5


def _max_content_bottom(page) -> float:
    """How far the real rendered content actually extends, in page pixels.

    NOT `stMain.scrollHeight`: empirically confirmed (2026-07-13) unreliable
    for this app — that container's height is tied to the viewport (a
    `min-height`-style rule), so `scrollHeight` reports whatever the
    viewport happens to be rather than genuine overflow, even when real
    content extends well past it (a 12000px viewport measured scrollHeight
    of exactly 12000 while content actually ran to ~12988px). Walking every
    element's own `getBoundingClientRect().bottom` catches that overflow
    directly, independent of the container's own sizing rules."""
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('[data-testid=\"stMain\"] *'))"
        ".reduce((m, el) => Math.max(m, el.getBoundingClientRect().bottom), 0)"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("out")
    p.add_argument("url", nargs="?", default="http://localhost:8501")
    p.add_argument("--demo", action="store_true", help="Click 'Load sample build' first.")
    p.add_argument("--tab", choices=["vault", "gear"], help="Click a tab after demo-load.")
    p.add_argument(
        "--click",
        action="append",
        default=[],
        help="Additional button label to click (repeatable).",
    )
    p.add_argument(
        "--hover-link",
        help="Hover the first <a> matching this text before screenshotting.",
    )
    p.add_argument(
        "--remove-chip",
        action="append",
        default=[],
        help="Click the close (×) button on a BaseWeb tag containing this text. "
        "Repeatable. Use to deselect multiselect chips.",
    )
    p.add_argument(
        "--expander",
        action="append",
        default=[],
        help="Open a Streamlit st.expander whose summary text contains this "
        "string. Repeatable. Streamlit renders expanders as <details><summary>, "
        "which `--click` (which uses role=button) can't find.",
    )
    p.add_argument("--wait-ms", type=int, default=4000, help="Final paint wait after settling.")
    p.add_argument(
        "--settle-ms",
        type=int,
        default=120_000,
        help="Max wait for the gear surface's marginals compute to finish "
        "(spinner to clear) after a demo/tab transition. Cold Pi loads can "
        "take 10-20s; the cap is generous.",
    )
    p.add_argument("--width", type=int, default=1400)
    p.add_argument("--height", type=int, default=1100)
    p.add_argument(
        "--browser",
        choices=["firefox", "chromium"],
        default="firefox",
        help="Rendering engine. Default firefox (preserves prior behavior). "
        "Retry a 'can't reproduce' layout bug in chromium before calling it "
        "a false alarm — see the module docstring.",
    )
    p.add_argument(
        "--zoom",
        type=int,
        default=100,
        help="Page zoom percent, applied via CSS `zoom` on <html> after load. "
        "Use 200 as a required second verification screenshot for any "
        "CSS/layout-affecting change — it stresses width-overflow bugs "
        "harder than 100% in both supported engines.",
    )
    args = p.parse_args()

    with sync_playwright() as pw:
        launcher = pw.chromium if args.browser == "chromium" else pw.firefox
        browser = launcher.launch()
        ctx = browser.new_context(viewport={"width": args.width, "height": args.height})
        page = ctx.new_page()
        _run_interactions(page, args)

        # `full_page=True` was empirically confirmed (2026-07-13, while
        # investigating a talent-tree layout bug) to silently truncate to
        # whatever viewport height was passed at context-creation time —
        # `document.documentElement` (what full-page capture measures)
        # never overflows its own fixed-size viewport for this app. A
        # `--height 3000` capture is ALWAYS exactly 3000px regardless of
        # how tall the real page is, UNLESS the passed height already
        # happens to exceed the true content height by luck. That means
        # every prior "verified" screenshot only ever proved the FIRST
        # `--height` pixels were clean, silently missing anything below.
        #
        # A single same-page resize is NOT enough either: this app's real
        # content height only reveals itself gradually as the viewport
        # grows (confirmed empirically — a 1100px viewport measured true
        # content at ~6448px, but re-measuring at a 6494px viewport found
        # the real figure was actually ~9112px, and again higher again at
        # 9000px, before finally stabilizing around 13000px). So this
        # loops: measure how far real content extends, and if that exceeds
        # the current viewport, close the page and replay every
        # interaction from scratch against a taller one — repeating (with
        # a hard cap) until a measurement no longer exceeds the viewport
        # it was measured in.
        current_height = args.height
        for _ in range(_MAX_RESIZE_ATTEMPTS):
            needed = _max_content_bottom(page)
            if not needed or needed <= current_height:
                break
            current_height = int(needed) + 200
            page.close()
            ctx = browser.new_context(viewport={"width": args.width, "height": current_height})
            page = ctx.new_page()
            _run_interactions(page, args)
        page.screenshot(path=args.out, full_page=True)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
