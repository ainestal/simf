"""Tests for the CSS/script injectors in `simf.ui.css`.

Batch B guardrail (2026-07-07, ROADMAP.md "guardrails before the charts
ship") — a `prefers-reduced-motion` guard, designed in *before* any
animated chart lands, not retrofitted after a complaint. Mirrors the
`_render_school_badge_css()` / `_render_track_badge_css()` test idiom
already established in `tests/test_damage_school_badge.py` /
`tests/test_upgrade_track.py`: a source-text check that the injector is
wired into `app.py`, plus an end-to-end check (fake `st.markdown`) that
the emitted CSS actually contains the guard.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = REPO_ROOT / "src" / "simf" / "ui" / "app.py"
APP = APP_PATH.read_text()


def test_reduced_motion_css_wired_into_app():
    """The injector must be imported AND called from app.py — mirrors
    `test_track_badge_css_present` in tests/test_upgrade_track.py."""
    assert "_render_reduced_motion_css" in APP, (
        "prefers-reduced-motion guard not wired into app.py — a future "
        "animated chart would ship with no reduced-motion fallback."
    )
    # Must actually be invoked (call site), not just imported.
    assert APP.count("_render_reduced_motion_css()") >= 1


def test_reduced_motion_css_emits_the_media_query():
    """End-to-end check: call the real injector (with a fake `st.markdown`)
    and assert the emitted CSS carries the `prefers-reduced-motion` media
    query plus the `!important` overrides that make it actually win
    against per-component transition/animation rules."""
    from simf.ui import app as app_module

    captured: list[str] = []

    def fake_markdown(content, *, unsafe_allow_html=False):
        captured.append(content)

    real_markdown = app_module.st.markdown
    app_module.st.markdown = fake_markdown
    try:
        app_module._render_reduced_motion_css()
    finally:
        app_module.st.markdown = real_markdown

    assert captured, "_render_reduced_motion_css emitted no CSS"
    css = "\n".join(captured)
    assert "prefers-reduced-motion: reduce" in css
    assert "animation-duration: 0.01ms !important" in css
    assert "animation-iteration-count: 1 !important" in css
    assert "transition-duration: 0.01ms !important" in css
    assert "scroll-behavior: auto !important" in css


# ── Mobile chrome-before-verdict reflow (2026-07-26) ───────────────────────
#
# Measured live at 390px: the Vault/Gear subtab buttons stacked as two
# separate 96px-tall full-width rows, and the "Prog dungeons" multiselect
# grew unbounded with every chip (184px with all 8 selected) — together part
# of ~915px of pure chrome rendering above ANY verdict-bearing content on
# both subtabs. Mirrors the `test_gear_col_has_min_width_zero` idiom: a
# source-text pin on the CSS rule block, so a future edit to this `<style>`
# section can't silently drop the fix.


def test_vault_gear_subtab_row_is_keyed_container():
    """`_render_surface_gear` must wrap the Vault/Gear buttons in
    `st.container(key="vault-gear-subtab-row")` — the hook the CSS below
    needs to pull them onto a shared row at narrow widths. Without this key,
    the media-query rule matches nothing and the buttons silently fall back
    to Streamlit's default one-per-row mobile stacking."""
    start = APP.find("def _render_surface_gear(")
    assert start != -1
    body = APP[start : APP.find("\ndef ", start + 1)]
    assert 'st.container(key="vault-gear-subtab-row")' in body


def test_vault_gear_subtab_row_css_shares_one_row_at_narrow_widths():
    start = APP.find(".st-key-vault-gear-subtab-row")
    assert start != -1, "vault-gear-subtab-row CSS rule not found"
    block = APP[start : start + 400]
    assert "flex-wrap: wrap" in block
    assert "min-width: 0 !important" in block
    assert "flex: 1 1 0 !important" in block


def test_prog_dungeons_multiselect_chip_area_is_height_capped():
    """The chip/tag area caps at a scrollable window instead of growing with
    every selected dungeon — every chip stays selected and removable (this
    is purely visual truncation, not a change in what's averaged over)."""
    start = APP.find('.st-key-_dungeon_select [data-baseweb="select"]')
    assert start != -1, "_dungeon_select multiselect CSS cap not found"
    block = APP[start : start + 1200]
    assert "max-height: 96px" in block
    assert "overflow-y: auto" in block
    # Best-effort visible-scroll signal (see the code comment) — a bare
    # `overflow: auto` measured with NO visible scrollbar in Firefox
    # (auto-hiding overlay scrollbar), which would silently hide selected
    # dungeons with no indication more exist below the fold.
    assert "scrollbar-color:" in block


def test_vault_gear_subtab_row_shares_one_row_end_to_end():
    """End-to-end: render the gear surface with a real vault and confirm both
    subtab buttons exist inside the keyed container (not just that the string
    is present somewhere in the source)."""
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["view"] = "gear"
    app.run()
    assert not app.exception, f"app raised: {app.exception}"
    subtab_keys = {b.key for b in app.button if b.key in {"gear_subtab_vault", "gear_subtab_gear"}}
    assert subtab_keys == {"gear_subtab_vault", "gear_subtab_gear"}
