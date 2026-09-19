"""Card mechanism-tag discoverability fix (readability workshop round 2,
2026-07-10).

The per-stat composition line ("eHP: +905 Stamina · +338 Versatility") gave a
glossed stat a hover-only `title=` tooltip cued solely by a 1px dotted
underline in muted gray — four of six workshop personas (novice/engaged/
elite tank, ui-craft-critic, copy-microcopy-editor, calibration-scientist)
independently flagged this as undiscoverable: nothing in the rendered text
itself suggests "there's more here" without already knowing the dotted-
underline convention, and it's invisible on a touch device entirely.

The fix: `_format_part_html` now appends `MECHANISM_GLYPH` (an "ⓘ"
superscript) after any stat whose `CompositionPart.mechanism` is present,
inside the same `title=`-bearing span. A non-glossed stat is unchanged —
plain text, no glyph, no tooltip, exactly as before.
"""

from __future__ import annotations

from simf.ui.helpers.stat_composition import (
    MECHANISM_GLYPH,
    CompositionPart,
    _format_part_html,
)


def _part(mechanism: str | None) -> CompositionPart:
    return CompositionPart(
        stat="versatility_rating",
        display_name="Versatility",
        blended=338.0,
        rounded=338,
        mechanism=mechanism,
    )


def test_glyph_renders_for_a_glossed_part():
    html = _format_part_html(_part("flat 1% damage-taken reduction per rating breakpoint"))
    assert MECHANISM_GLYPH in html
    assert 'class="stat-mechanism-glyph"' in html
    # The glyph sits inside the SAME title-bearing span as the number, so
    # hovering either the number or the glyph reveals the identical tooltip.
    assert 'class="stat-mechanism"' in html
    assert "title=" in html


def test_glyph_absent_for_a_non_glossed_part():
    html = _format_part_html(_part(None))
    assert MECHANISM_GLYPH not in html
    assert "stat-mechanism" not in html
    assert "title=" not in html


def test_glyph_is_decorative_to_assistive_tech():
    """The glyph duplicates information already carried by the span's own
    `title=` — mark it `aria-hidden` so a screen reader doesn't announce a
    bare "circled i" with no context."""
    html = _format_part_html(_part("some mechanism"))
    assert 'aria-hidden="true"' in html


def test_glyph_appears_after_the_stat_number_and_name():
    """The glyph reads as a trailing footnote marker on the stat, not a
    prefix or something detached from the number it annotates."""
    html = _format_part_html(_part("some mechanism"))
    number_idx = html.index("+338")
    glyph_idx = html.index(MECHANISM_GLYPH)
    assert glyph_idx > number_idx
