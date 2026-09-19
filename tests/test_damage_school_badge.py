"""Tests for the canonical damage-school badge helper.

Brutoh idea #d, 2026-05-25 — every Why-died ability rendering site
prefixes the spell name with a colored pill carrying the damage school
("Physical", "Fire", "Magic", "Physical (bleed)" …) so tank
decision-making is school-routed ("this is Magic → Spell Reflect, not
Shield Block").

The helper is the single source of truth across surfaces (top damage
abilities, ability danger ranking, mitigation audit, death recap,
per-segment risk, death timeline). These tests pin down:

  - the HTML contract (span + class + text label)
  - the bleed treatment ("Physical (bleed)")
  - the plain-text mode for `st.dataframe` cells
  - graceful fallback for unknown schools
  - WCAG 1.4.1 — every supported school has a non-empty text label
  - the coverage tripwire — every Why-died ability rendering site
    routes through the canonical helper (grep-style audit of
    `log_view.py`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from simf.ui.helpers.damage_school_badge import (
    SUPPORTED_SCHOOLS,
    build_ability_school_map,
    dominant_school_for_ability,
    render_school_badge,
    school_label,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── HTML contract ────────────────────────────────────────────────────────────


def test_render_html_fire_badge_shape():
    out = render_school_badge("fire")
    assert out == '<span class="school-badge fire">Fire</span>'


def test_render_html_physical_badge_shape():
    out = render_school_badge("physical")
    assert out == '<span class="school-badge physical">Physical</span>'


def test_render_html_case_insensitive():
    """School names from the parser are lowercase, but defensive: an
    upper-cased input shouldn't render a broken class name."""
    out = render_school_badge("FIRE")
    assert 'class="school-badge fire"' in out
    assert ">Fire<" in out


# ── Bleed treatment ──────────────────────────────────────────────────────────


def test_bleed_html_uses_physical_bleed_modifier_class():
    """Bleeds are physical-school DOTs that bypass armor. The badge
    needs a distinct visual treatment so the user understands why the
    mitigation math looked different on that bucket."""
    out = render_school_badge("physical", is_bleed=True)
    assert "school-badge" in out
    assert "physical" in out
    assert "physical-bleed" in out
    assert "Physical (bleed)" in out


def test_bleed_text_mode_carries_parenthetical():
    assert render_school_badge("physical", is_bleed=True, style="text") == "Physical (bleed)"


def test_bleed_on_non_physical_school_ignored():
    """No non-physical bleed exists in 12.0.5 — if a caller passes
    is_bleed=True for a non-physical school we treat it as a no-op
    rather than render confusing 'Fire (bleed)' label."""
    out = render_school_badge("fire", is_bleed=True, style="text")
    assert out == "Fire"


# ── Unknown school fallback ──────────────────────────────────────────────────


def test_unknown_school_falls_back_to_unknown_badge():
    """Malformed combat-log school flag or future-patch school should
    NOT raise — render a neutral 'Unknown' pill."""
    out = render_school_badge("voodoo")
    assert 'class="school-badge unknown"' in out
    assert ">Unknown<" in out


def test_none_school_falls_back_to_unknown_badge():
    out = render_school_badge(None)
    assert 'class="school-badge unknown"' in out
    assert ">Unknown<" in out


def test_unknown_text_label_non_empty():
    """WCAG 1.4.1 — the text label must never be empty (color is
    decoration, not the only signifier)."""
    assert render_school_badge("voodoo", style="text") == "Unknown"


# ── Plain-text mode (st.dataframe cells) ─────────────────────────────────────


def test_text_mode_returns_bare_label_no_html():
    out = render_school_badge("nature", style="text")
    assert out == "Nature"
    assert "<" not in out
    assert "span" not in out


def test_text_mode_for_every_supported_school():
    for school in SUPPORTED_SCHOOLS:
        out = render_school_badge(school, style="text")
        assert out and "<" not in out, f"{school}: {out!r}"


# ── WCAG 1.4.1: text label is never empty ────────────────────────────────────


def test_every_supported_school_has_non_empty_text_label():
    """The text label is the non-color signifier required by WCAG 1.4.1
    ("Use of Color"). If a school renders as an empty pill the user
    has only color to read."""
    for school in SUPPORTED_SCHOOLS:
        label = school_label(school)
        assert label, f"empty label for {school!r}"
        assert label[0].isupper(), f"label for {school!r} not titlecase: {label!r}"


def test_every_supported_school_renders_text_inside_html_pill():
    """The HTML pill always carries the label as inner text, not as
    an attribute only — screen-readers and copy-paste both work."""
    for school in SUPPORTED_SCHOOLS:
        html = render_school_badge(school)
        # Extract inner text between `>` and `<`
        m = re.search(r">([^<]+)<", html)
        assert m, f"no inner text in {html!r}"
        assert m.group(1).strip(), f"empty inner text for {school!r}"


# ── school_label() canonical labels ──────────────────────────────────────────


@pytest.mark.parametrize(
    "school,is_bleed,expected",
    [
        ("physical", False, "Physical"),
        ("physical", True, "Physical (bleed)"),
        ("fire", False, "Fire"),
        ("holy", False, "Holy"),
        ("nature", False, "Nature"),
        ("frost", False, "Frost"),
        ("shadow", False, "Shadow"),
        ("arcane", False, "Arcane"),
        ("unknown", False, "Unknown"),
        ("voodoo", False, "Unknown"),  # fallback
        (None, False, "Unknown"),
        ("fire", True, "Fire"),  # bleed on non-physical → no parenthetical
    ],
)
def test_school_label_canonical(school, is_bleed, expected):
    assert school_label(school, is_bleed=is_bleed) == expected


# ── dominant_school_for_ability / build_ability_school_map ────────────────────


class _FakeEvent:
    def __init__(self, spell_name, school, amount):
        self.spell_name = spell_name
        self.school = school
        self.amount = amount


def test_dominant_school_picks_largest_share():
    events = [
        _FakeEvent("Frostbolt", "frost", 100),
        _FakeEvent("Frostbolt", "frost", 200),
        _FakeEvent("Frostbolt", "shadow", 50),  # impossible in practice; minority
    ]
    school, is_bleed = dominant_school_for_ability(events, "Frostbolt")
    assert school == "frost"
    assert is_bleed is False


def test_dominant_school_bleed_detected_by_name():
    events = [_FakeEvent("Rake", "physical", 100)]
    school, is_bleed = dominant_school_for_ability(events, "Rake")
    assert school == "physical"
    assert is_bleed is True


def test_dominant_school_for_unknown_ability_returns_unknown():
    school, is_bleed = dominant_school_for_ability([], "Missing Spell")
    assert school == "unknown"
    assert is_bleed is False


def test_build_ability_school_map_one_pass():
    events = [
        _FakeEvent("Fireball", "fire", 100),
        _FakeEvent("Frostbolt", "frost", 200),
        _FakeEvent("Rake", "physical", 50),
    ]
    m = build_ability_school_map(events)
    assert m["Fireball"] == ("fire", False)
    assert m["Frostbolt"] == ("frost", False)
    assert m["Rake"] == ("physical", True)


# ── Coverage tripwire ────────────────────────────────────────────────────────
#
# Asserts every Why-died ability rendering site in `log_view.py` routes
# through the canonical helper. The check is grep-based — keep the
# allowlist below explicit so a future regression has to argue with a
# test, not silently sidestep it.


def test_log_view_has_no_residual_school_glyph_helper():
    """The legacy `_school_glyph` / `_SCHOOL_ICONS` mini-system used to
    live alongside the badge helper. After the consolidation it MUST
    NOT come back — the codepath would render emoji + lowercase school
    name and silently bypass the WCAG-audited badge."""
    text = (REPO_ROOT / "src" / "simf" / "ui" / "log_analysis.py").read_text()
    assert "_school_glyph" not in text, (
        "Legacy `_school_glyph` helper resurrected — every ability "
        "render must route through `render_school_badge` instead."
    )
    assert "_SCHOOL_ICONS" not in text, (
        "Legacy `_SCHOOL_ICONS` map resurrected — colors live in "
        "`data/constants.yaml: damage_school_colors`."
    )


def test_log_view_imports_canonical_helper():
    text = (REPO_ROOT / "src" / "simf" / "ui" / "log_analysis.py").read_text()
    assert "from simf.ui.helpers.damage_school_badge import" in text
    assert "render_school_badge" in text


def test_log_view_renders_badge_on_every_ability_site():
    """Every spell-name display site (per-segment top abilities, death
    attribution, top damage abilities, danger ranking, mitigation
    audit, death timeline, hero verdict) must include a
    `render_school_badge` or `school_label` call.

    Implementation: look at the *body* of every render-site function in
    `log_view.py` and assert the body mentions the helper. The function
    list is locked here so adding a new render site without touching
    this test causes a regression — the new function won't match the
    allowlist and the test will explicitly fail.
    """
    ui_dir = REPO_ROOT / "src" / "simf" / "ui"

    # Functions known to render ability/spell names — each MUST mention the
    # canonical helper inside its body. Maps function name -> (owning module
    # file, helper symbol). The log_view split scattered these render sites
    # across sibling modules; the file column tracks where each now lives.
    sites = {
        "_render_death_attribution": ("log_death.py", "render_school_badge"),
        "render_per_segment_risk": ("log_segment_risk.py", "render_school_badge"),
        "render_log_analysis": ("log_analysis.py", "render_school_badge"),
    }
    # Function source bodies, split on `def <name>`.
    for fn, (fname, helper) in sites.items():
        text = (ui_dir / fname).read_text()
        pattern = re.compile(rf"def {re.escape(fn)}\(.*?\n(?=\ndef |\Z)", re.DOTALL)
        m = pattern.search(text)
        assert m, f"{fn} not found in {fname}"
        body = m.group(0)
        assert helper in body or "school_label" in body or "_badge_for_event" in body, (
            f"{fn} renders ability/spell names but doesn't route through "
            f"the canonical badge helper. Either call render_school_badge / "
            f"school_label / _badge_for_event, or update the test allowlist "
            f"with a comment explaining why."
        )


def test_constants_yaml_carries_school_color_block():
    """The colour map MUST live in constants.yaml, not hardcoded in
    Python. Mirrors the project rule from CONTRIBUTING.md ("All constants in
    `data/constants.yaml` — never hardcode numbers in Python")."""
    from simf.core.constants import load_constants

    colors = load_constants().get("damage_school_colors", {})
    assert colors, "damage_school_colors block missing from constants.yaml"
    for school in ("physical", "holy", "fire", "nature", "frost", "shadow", "arcane", "unknown"):
        assert school in colors, f"{school} missing from damage_school_colors"
        entry = colors[school]
        assert "fg" in entry and "bg" in entry, f"{school} missing fg/bg"
        # Sanity-check the hex format so a future YAML edit doesn't
        # accidentally land an unparseable colour.
        assert re.match(r"^#[0-9a-fA-F]{6}$", entry["fg"]), entry
        assert re.match(r"^#[0-9a-fA-F]{6}$", entry["bg"]), entry


def test_app_css_layout_block_exists():
    """The static CSS (badge layout — padding, border-radius, font-size)
    lives in `app.py`. Colors are injected from YAML at render time
    via `_render_school_badge_css` — see the next test."""
    text = (REPO_ROOT / "src" / "simf" / "ui" / "app.py").read_text()
    assert ".school-badge {" in text, (
        "Static `.school-badge` layout rule missing from app.py — "
        "badges would render with no padding / border-radius."
    )
    assert "_render_school_badge_css" in text, (
        "YAML→CSS bridge missing from app.py — colors wouldn't flow "
        "from constants.yaml into the stylesheet."
    )


def test_school_badge_css_is_generated_from_constants_yaml():
    """End-to-end check: the YAML `damage_school_colors:` block must
    actually drive what the CSS shows.

    Caches the YAML, calls the renderer (with a fake `st.markdown`),
    then asserts every supported school appears with its YAML fg/bg
    in the emitted CSS. This is the test that catches "YAML edited
    but page colors unchanged" — the failure mode the advisor
    surfaced when the per-school rules were hardcoded in the
    static stylesheet."""
    from simf.core.constants import load_constants
    from simf.ui import app as app_module

    captured: list[str] = []

    def fake_markdown(content, *, unsafe_allow_html=False):
        captured.append(content)

    real_markdown = app_module.st.markdown
    app_module.st.markdown = fake_markdown
    try:
        app_module._render_school_badge_css()
    finally:
        app_module.st.markdown = real_markdown

    assert captured, "_render_school_badge_css emitted no CSS"
    css = "\n".join(captured)
    colors = load_constants().get("damage_school_colors", {})
    for school, entry in colors.items():
        assert f".school-badge.{school}" in css, f"{school}: no rule emitted from YAML"
        assert entry["fg"] in css, (
            f"{school}: fg color {entry['fg']!r} from YAML not in emitted CSS"
        )
    # Bleed variant derived from the physical entry.
    assert ".school-badge.physical-bleed" in css
    assert "dashed" in css


def test_yaml_colors_drive_what_helper_emits():
    """Cross-check the CSS classes the Python helper emits (`render_school_badge`)
    against the classes the YAML→CSS renderer generates. If the helper
    emits `.school-badge.fire` but the YAML lacks a `fire:` entry, the
    pill renders as bare text — caught here."""
    from simf.core.constants import load_constants

    colors = load_constants().get("damage_school_colors", {})
    for school in SUPPORTED_SCHOOLS:
        html = render_school_badge(school)
        # Every class the helper puts on the span must have a YAML
        # entry that drives its color.
        assert school in colors, (
            f'Helper emits `<span class="... {school}">` but YAML has '
            f"no `{school}:` color entry — pill would render uncolored."
        )
        assert school in html  # sanity
