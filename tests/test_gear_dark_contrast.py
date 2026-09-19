"""WCAG 2.2 AA contrast guard for the dark gear "character sheet" panel.

The Gear tab renders the 16-slot paperdoll as a dark inset panel (the
universal WoW / Raidbots / QE Live character-sheet look — examples/screenshots/
ui.png), scoped entirely to `.gear-sheet-dark` so the light Codex page is
untouched. Every quality-name / subtitle / signal hue must clear 4.5:1 on the
recessed card, and every icon-quality border must clear 3:1 (WCAG 1.4.11),
against the *dark* surfaces — the cream-page guard in test_palette_contrast.py
can't see these because they live on a different background.

Parses the LIVE `--gs-*` token values out of app.py's CSS and recomputes the
real contrast, so the dark tokens can't silently regress below AA.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / "src" / "simf" / "ui" / "app.py").read_text()


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(fg: tuple, bg: tuple) -> float:
    l1, l2 = _luminance(fg), _luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _blend_over(rgba: tuple[float, float, float, float], bg: tuple[int, int, int]) -> tuple:
    """Flatten a translucent `rgba()` fill over an opaque bg to an (r,g,b)
    triple — the browser does this compositing before a screen reader's own
    contrast-analysis tooling (or a sighted eye) ever sees a final color, so
    a badge's *effective* background is this blend, not the bg alone."""
    r, g, b, a = rgba
    br, bgc, bb = bg
    return (r * a + br * (1 - a), g * a + bgc * (1 - a), b * a + bb * (1 - a))


def _token(name: str) -> tuple[int, int, int]:
    """Resolve a `--gs-*` CSS custom property to its (r,g,b) hex value."""
    m = re.search(rf"{re.escape(name)}:\s*#([0-9a-fA-F]{{6}})", APP)
    assert m, f"dark-panel token {name} (#hex) not found in app.py CSS"
    return _rgb(m.group(1))


# The two dark surfaces text sits on: the recessed card and the panel.
CARD = _token("--gs-card")
PANEL = _token("--gs-panel")
# The empty-slot card bg is a CSS literal (not a token) — keep this in sync
# with `.gear-sheet-dark .gear-card.empty { background: #0f0f15; }`.
EMPTY_CARD = _rgb("#0f0f15")


def test_quality_names_clear_aa_on_card():
    """Every quality-coloured item NAME (the WoW signal) clears 4.5:1 on the
    recessed card — these are 14px/600 links, treated as normal text."""
    for token in (
        "--gs-q-epic",
        "--gs-q-legendary",
        "--gs-q-rare",
        "--gs-q-uncommon",
        "--gs-q-common",
    ):
        ratio = _contrast(_token(token), CARD)
        assert ratio >= 4.5, f"{token} is {ratio:.2f}:1 on the card — below WCAG AA (4.5:1)"


def test_subtitle_and_signals_clear_aa_on_card():
    """Subtitle, the gold swap signal, the quiet 'Best you own', and the
    aquamarine gem-upgrade signal all clear 4.5:1 on the card (normal text)."""
    for token in ("--gs-meta", "--gs-swap", "--gs-best", "--gs-gem"):
        ratio = _contrast(_token(token), CARD)
        assert ratio >= 4.5, f"{token} is {ratio:.2f}:1 on the card — below WCAG AA (4.5:1)"


def test_empty_slot_name_clears_aa():
    """The italic empty-slot placeholder name (rendered in --gs-best) sits on
    the darker empty-card bg — must still clear 4.5:1."""
    ratio = _contrast(_token("--gs-best"), EMPTY_CARD)
    assert ratio >= 4.5, f"empty-slot name is {ratio:.2f}:1 on #15151c — below AA"


def test_header_text_clears_aa_on_panel():
    """The panel title and spec eyebrow sit on the panel bg, not a card."""
    for token in ("--gs-head", "--gs-eyebrow"):
        ratio = _contrast(_token(token), PANEL)
        assert ratio >= 4.5, f"{token} is {ratio:.2f}:1 on the panel — below WCAG AA (4.5:1)"


def test_icon_quality_borders_clear_1411_on_card():
    """The four coloured icon-quality borders are non-text UI — WCAG 1.4.11
    needs 3:1 against the card they frame."""
    for token in ("--gs-q-epic", "--gs-q-legendary", "--gs-q-rare", "--gs-q-uncommon"):
        ratio = _contrast(_token(token), CARD)
        assert ratio >= 3.0, (
            f"{token} icon border is {ratio:.2f}:1 on the card — below 1.4.11 (3:1)"
        )


# ── Upgrade panel's dark rows (P4 polish, 2026-07-02) ──────────────────────
#
# `_render_upgrade_panel` reuses `.gear-sheet-dark` for its row list, but that
# box has no separate recessed "card" tier — the rows sit directly on
# `--gs-panel` (#191922), which is LIGHTER than `--gs-card` (#121218). Every
# token above was only ever measured against CARD/PANEL in its ORIGINAL
# context; this guards the new CARD-tuned tokens (--gs-meta / --gs-q-common /
# --gs-swap / --gs-best / --gs-gem / the two icon-border hues) against the
# lighter PANEL bg too, since a lighter bg can only shrink contrast for a
# light-on-dark pairing — a future palette tweak that keeps the CARD tests
# green could otherwise silently take the upgrade panel below AA.


def test_card_tuned_text_tokens_also_clear_aa_on_panel():
    """Text tokens the upgrade-panel rows borrow from the card palette still
    clear 4.5:1 against the lighter --gs-panel background."""
    for token in ("--gs-meta", "--gs-q-common", "--gs-swap", "--gs-best", "--gs-gem"):
        ratio = _contrast(_token(token), PANEL)
        assert ratio >= 4.5, f"{token} is {ratio:.2f}:1 on the panel — below WCAG AA (4.5:1)"


def test_card_tuned_icon_borders_also_clear_1411_on_panel():
    """The epic/legendary icon-border hues the upgrade panel's slot-row-icon
    borrows still clear the 3:1 non-text floor against --gs-panel."""
    for token in ("--gs-q-epic", "--gs-q-legendary"):
        ratio = _contrast(_token(token), PANEL)
        assert ratio >= 3.0, (
            f"{token} icon border is {ratio:.2f}:1 on the panel — below 1.4.11 (3:1)"
        )


def test_upgrade_cap_badge_clears_aa_on_panel():
    """`.gear-sheet-dark .upgrade-cap-badge` (added alongside the P4 dark-row
    reuse) paints a translucent gold pill — `background: rgba(255, 194, 75,
    0.18)` — under `--gs-swap` text, sitting on --gs-panel. Neither existing
    guard above catches this: it's not a flat `--gs-*` hex token (it's a
    literal rgba fill that must be alpha-composited over the panel first to
    get its *effective* background), and `test_upgrade_panel_app.py`'s
    `test_panel_css_present` only checks the selector string exists, not
    that the pairing clears AA. The badge fires whenever an upgrade clamps to
    the season ilvl cap, so it's not a rare edge case worth leaving unguarded.

    Reads BOTH the background and the text color out of the live rule block
    (not a hardcoded `--gs-swap` assumption) so a future edit that changes
    either side of the pairing — not just the background — still gets caught."""
    m = re.search(
        r"\.gear-sheet-dark \.upgrade-cap-badge \{([^}]*)\}",
        APP,
    )
    assert m, "`.gear-sheet-dark .upgrade-cap-badge` rule not found in app.py CSS"
    block = m.group(1)
    bg_m = re.search(
        r"background:\s*rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", block
    )
    assert bg_m, (
        f"expected an rgba() background in `.gear-sheet-dark .upgrade-cap-badge`, got: {block}"
    )
    r, g, b, a = (float(x) for x in bg_m.groups())
    effective_bg = _blend_over((r, g, b, a), PANEL)

    color_m = re.search(r"color:\s*(var\(--[\w-]+\)|#[0-9a-fA-F]{6})\s*;", block)
    assert color_m, (
        f"expected a `color:` declaration in `.gear-sheet-dark .upgrade-cap-badge`, got: {block}"
    )
    color_decl = color_m.group(1)
    var_m = re.match(r"var\((--[\w-]+)\)", color_decl)
    fg = _token(var_m.group(1)) if var_m else _rgb(color_decl)

    ratio = _contrast(fg, effective_bg)
    assert ratio >= 4.5, (
        f"upgrade-cap-badge text ({color_decl}) on its blended background is "
        f"{ratio:.2f}:1 — below WCAG AA (4.5:1)"
    )
