"""Regression test: `.item-link` must not fall through to the browser
default blue-underlined <a> outside a quality-tinted container.

Live-UI review, 2026-07-18 (ui-craft-critic, screenshot-verified): only a
`:focus-visible` rule existed for this class — no base color/decoration —
so every Vault-card Wowhead item-name link rendered as unstyled default-blue
underlined text. Fixed by tokenizing to the existing steel accent
(`--accent-gold`), underline on hover only. `.gear-card-name a` /
`.slot-row-name a` (inside the dark paperdoll) intentionally override this
back to `color: inherit` — those rules must keep appearing LATER in the
stylesheet than this one so the cascade still favors the quality tint
inside gear cards.
"""

from __future__ import annotations

from pathlib import Path

APP_SOURCE = (Path(__file__).resolve().parent.parent / "src" / "simf" / "ui" / "app.py").read_text()


def test_item_link_has_a_base_color_not_just_focus_ring():
    assert "a.item-link, a.item-link:visited" in APP_SOURCE
    assert "color: var(--accent-gold);" in APP_SOURCE
    # Underline stays for the hover state (WCAG 1.4.1 — never color alone).
    assert "a.item-link:hover" in APP_SOURCE


def test_gear_card_name_override_still_appears_after_the_base_link_rule():
    """Cascade order matters: `.gear-card-name a { color: inherit }` must sit
    AFTER the new base `.item-link` rule so the quality tint still wins
    inside gear cards (both selectors share the same specificity)."""
    base_idx = APP_SOURCE.find("a.item-link, a.item-link:visited")
    override_idx = APP_SOURCE.find(".gear-card-name a, .gear-card-name a:visited")
    assert base_idx != -1 and override_idx != -1
    assert base_idx < override_idx, (
        "the gear-card-name color:inherit override must come AFTER the new "
        "base .item-link color rule, or the quality tint would lose the cascade"
    )
