"""Radio/select controls need a visible keyboard-focus ring (WCAG 2.4.7).

BaseWeb hides the native `<input type="radio">` / select input and paints a
custom dot/box in its place — the browser's default focus ring (which draws
around the *native* element) has nothing visible to attach to. A keyboard-only
or low-vision user tabbing through the "Compare at item level" radio (every
slot dialog + Vault tab + upgrade panel) or the "Prog dungeons" multiselect
got zero visible confirmation of where focus landed (round-1 accessibility
audit, 2026-07-05). `:focus-within` on the BaseWeb wrapper forwards focus
state to the visible control, mirroring the existing text-input pattern.

Live-verified separately (Playwright, computed `box-shadow`) — this file
just pins the CSS source so a refactor can't silently drop the rule.
"""

from __future__ import annotations

from pathlib import Path

APP_SOURCE = (Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py").read_text()


def test_radio_focus_within_rule_present():
    assert 'label[data-baseweb="radio"]:focus-within' in APP_SOURCE


def test_select_focus_within_rule_present():
    assert 'div[data-baseweb="select"] > div:focus-within' in APP_SOURCE


def test_radio_and_select_focus_rules_use_the_shared_accent_token():
    """Same visual language as the button/text-input focus rings — not a
    one-off color that'll drift out of sync with a future palette change."""
    idx = APP_SOURCE.index('label[data-baseweb="radio"]:focus-within')
    snippet = APP_SOURCE[idx : idx + 400]
    assert "var(--accent-gold)" in snippet
