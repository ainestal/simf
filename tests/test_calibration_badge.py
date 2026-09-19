"""Tests for the calibration-tier badge helper (ROADMAP Batch H, 2026-07-08).

Mirrors `test_damage_school_badge.py`'s split:
  - `compute_calibration_badge` pure-function coverage for all 3 tiers plus
    the missing/unset-tier default (must match `_uncalibrated_spec_warning`'s
    own `spec_cfg.get("calibration_tier", "placeholder")` default — a second,
    disagreeing default here would reproduce the exact "two surfaces
    disagree about calibration status" bug class this app has hit before).
  - `render_calibration_badge_html` HTML-contract + WCAG 1.4.1 (every tier
    renders a real, non-empty text label, never icon/color alone).
  - a source-level regression guard confirming `app.py`'s `main()` actually
    renders the badge instead of the old always-expanded `st.warning(uncal)`
    wall of text at this call site.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from simf.core.constants import CALIBRATION_TIERS
from simf.ui.helpers.calibration_badge import (
    CalibrationBadge,
    compute_calibration_badge,
    render_calibration_badge_html,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── compute_calibration_badge ────────────────────────────────────────────────


def test_compute_calibrated_tier():
    constants = {"specs": {"my_spec": {"calibration_tier": "calibrated"}}}
    badge = compute_calibration_badge("my_spec", constants)
    assert badge.tier == "calibrated"
    assert badge.label == "Calibrated"
    assert badge.icon  # non-empty
    assert badge.trailing_clause == "matches real combat logs closely"


def test_compute_characterized_tier():
    constants = {"specs": {"my_spec": {"calibration_tier": "characterized"}}}
    badge = compute_calibration_badge("my_spec", constants)
    assert badge.tier == "characterized"
    assert badge.label == "Characterized"
    assert badge.icon
    assert badge.trailing_clause == "checked against a few logs — trust less"


def test_compute_placeholder_tier():
    constants = {"specs": {"my_spec": {"calibration_tier": "placeholder"}}}
    badge = compute_calibration_badge("my_spec", constants)
    assert badge.tier == "placeholder"
    assert badge.label == "Placeholder"
    assert badge.icon
    assert badge.trailing_clause == "not checked against real logs yet"


def test_compute_missing_calibration_tier_key_defaults_placeholder():
    """A spec entry present but with no `calibration_tier` key at all must
    default to placeholder — same default `spec_is_calibrated` uses, so a
    newly-added spec never silently reads as calibrated before anyone
    ratifies it."""
    constants = {"specs": {"my_spec": {}}}
    badge = compute_calibration_badge("my_spec", constants)
    assert badge.tier == "placeholder"
    assert badge.label == "Placeholder"
    assert badge.trailing_clause == "not checked against real logs yet"


def test_compute_spec_missing_entirely_defaults_placeholder():
    """A spec name with no entry at all in `specs:` (e.g. a typo, or a spec
    constants.yaml hasn't caught up to yet) must not crash and must default
    to the least-confident tier."""
    constants = {"specs": {}}
    badge = compute_calibration_badge("totally_unknown_spec", constants)
    assert badge.tier == "placeholder"


def test_compute_empty_constants_dict_defaults_placeholder():
    badge = compute_calibration_badge("any_spec", {})
    assert badge.tier == "placeholder"


def test_compute_unrecognized_tier_value_clamped_to_placeholder():
    """A malformed/typo'd `calibration_tier` value in YAML degrades to the
    least-confident tier rather than crashing or silently matching nothing."""
    constants = {"specs": {"my_spec": {"calibration_tier": "some_typo"}}}
    badge = compute_calibration_badge("my_spec", constants)
    assert badge.tier == "placeholder"


def test_compute_matches_spec_is_calibrated_default_for_every_real_spec():
    """Cross-check against the live constants.yaml: for every real spec,
    the badge's "is this the top tier" reading must agree with
    `spec_is_calibrated` — the exact invariant the whole module exists to
    protect (badge and text must never disagree about calibration status)."""
    from simf.core.constants import load_constants, spec_is_calibrated

    constants = load_constants()
    for spec, spec_cfg in constants.get("specs", {}).items():
        badge = compute_calibration_badge(spec, constants)
        assert (badge.tier == "calibrated") == spec_is_calibrated(spec_cfg), spec


def test_all_three_tiers_produce_distinct_badges():
    labels = set()
    icons = set()
    clauses = set()
    for tier in CALIBRATION_TIERS:
        constants = {"specs": {"s": {"calibration_tier": tier}}}
        badge = compute_calibration_badge("s", constants)
        labels.add(badge.label)
        icons.add(badge.icon)
        clauses.add(badge.trailing_clause)
    assert len(labels) == 3
    assert len(icons) == 3
    assert len(clauses) == 3


# ── render_calibration_badge_html ────────────────────────────────────────────


def test_render_html_shape():
    badge = CalibrationBadge(
        tier="calibrated",
        label="Calibrated",
        icon="✓",
        trailing_clause="matches real combat logs closely",
    )
    out = render_calibration_badge_html(badge)
    assert out == (
        '<span class="calibration-badge calibration-badge-calibrated">'
        "✓ Calibrated — matches real combat logs closely</span>"
    )


def test_render_html_omits_dash_when_no_trailing_clause_set():
    """A hand-built badge with no `trailing_clause` (the field's default)
    degrades to the old icon+label-only rendering — never a dangling
    ' — ' with nothing after it."""
    badge = CalibrationBadge(tier="calibrated", label="Calibrated", icon="✓")
    out = render_calibration_badge_html(badge)
    assert out == '<span class="calibration-badge calibration-badge-calibrated">✓ Calibrated</span>'
    assert "—" not in out


@pytest.mark.parametrize("tier", CALIBRATION_TIERS)
def test_render_html_every_tier_carries_class_and_label(tier):
    constants = {"specs": {"s": {"calibration_tier": tier}}}
    badge = compute_calibration_badge("s", constants)
    html = render_calibration_badge_html(badge)
    assert f'class="calibration-badge calibration-badge-{tier}"' in html
    assert badge.label in html


@pytest.mark.parametrize("tier", CALIBRATION_TIERS)
def test_render_html_every_tier_carries_its_trailing_clause(tier):
    """2026-07-10 readability workshop fix: a first-time reader shouldn't
    have to already know what 'Characterized' means to act on it — every
    tier's pill must carry its own short plain-English trust clause inline,
    not just the bare tier word."""
    constants = {"specs": {"s": {"calibration_tier": tier}}}
    badge = compute_calibration_badge("s", constants)
    html = render_calibration_badge_html(badge)
    assert badge.trailing_clause in html
    assert badge.trailing_clause, f"tier {tier!r} has no trailing clause"


def test_render_html_never_icon_only():
    """WCAG 1.4.1 — every rendered badge must carry a real text label
    alongside the icon, not just an icon/color as the only signifier."""
    for tier in CALIBRATION_TIERS:
        constants = {"specs": {"s": {"calibration_tier": tier}}}
        badge = compute_calibration_badge("s", constants)
        html = render_calibration_badge_html(badge)
        m = re.search(r">([^<]+)<", html)
        assert m, f"no inner text in {html!r}"
        inner = m.group(1).strip()
        assert inner, f"empty inner text for tier {tier!r}"
        # The inner text must contain the plain-English label, not just the
        # icon glyph — a screen reader reading only the icon character
        # would announce nothing meaningful.
        assert badge.label in inner


def test_render_html_is_a_single_span_no_nested_markup():
    badge = CalibrationBadge(tier="placeholder", label="Placeholder", icon="⚠️")
    html = render_calibration_badge_html(badge)
    assert html.count("<span") == 1
    assert html.count("</span>") == 1


# ── Regression guard: app.py wires the badge, not the old warning wall ──────


def test_app_py_no_longer_renders_bare_uncal_warning():
    """The old `if uncal: st.warning(uncal)` pattern must be gone from
    `main()`'s executable code — replaced by the badge + collapsed expander.
    Grep-based, same idiom as `test_cd_plan_tab.py`'s equivalent guard on
    `log_cd_plan.py`. Strips comment-only lines first so the explanatory
    comment left behind at the call site (which names the old call for
    context) doesn't trip a false positive."""
    lines = (REPO_ROOT / "src" / "simf" / "ui" / "app.py").read_text().splitlines()
    code_only = "\n".join(line for line in lines if not line.strip().startswith("#"))
    assert "st.warning(uncal)" not in code_only, (
        "app.py still renders the pre-Batch-H always-expanded calibration "
        "warning wall — should be replaced by the calibration badge."
    )


def test_load_py_uses_the_badge_helper_for_the_calibration_chip():
    """The calibration confidence tier is now surfaced by the single
    calibration chip in `load.py` (F-001/F-002, review round R2 2026-07-17) —
    the old standalone "Model calibration:" pill row app.py rendered folded
    into it. Guard that the chip still reads its tier from the canonical badge
    helper (the single source of truth), so a spec's tier can't silently
    diverge from the (unchanged) `_uncalibrated_spec_warning()` text."""
    text = (REPO_ROOT / "src" / "simf" / "ui" / "load.py").read_text()
    assert "from simf.ui.helpers.calibration_badge import" in text
    assert "compute_calibration_badge" in text


def test_app_py_no_longer_renders_the_standalone_calibration_pill_row():
    """F-001/F-002 (R2): the "Model calibration: ✓ Calibrated" pill row app.py
    used to render as a separate full-width line was the split-signal both
    tank personas flagged — number and confidence in different corners. It's
    gone; confidence now rides in the run-config-strip chip."""
    text = (REPO_ROOT / "src" / "simf" / "ui" / "app.py").read_text()
    assert "**Model calibration:**" not in text
    assert "render_calibration_badge_html" not in text


def test_app_py_css_injector_is_wired():
    text = (REPO_ROOT / "src" / "simf" / "ui" / "app.py").read_text()
    assert "_render_calibration_badge_css" in text


def test_css_py_defines_calibration_badge_layout():
    text = (REPO_ROOT / "src" / "simf" / "ui" / "css.py").read_text()
    assert ".calibration-badge {" in text
    for tier in CALIBRATION_TIERS:
        assert f".calibration-badge-{tier}" in text


def test_calibration_chip_label_is_left_aligned_for_every_tier():
    """Live-UI review, 2026-07-18: the chip label is centered by Streamlit's
    default button styling, which makes `text-overflow: ellipsis` render a
    confusing symmetric clip from BOTH ends when the label is wider than the
    button (at high zoom, or just a long tier caption) instead of a normal
    trailing "…". Left-aligning must apply to EVERY tier's chip container
    (`[class*="st-key-calibration-chip-"]`), not just the calibrated/
    placeholder ones the color overrides above target."""
    text = (REPO_ROOT / "src" / "simf" / "ui" / "css.py").read_text()
    assert '[class*="st-key-calibration-chip-"] button,' in text
    assert "justify-content: flex-start !important;" in text
    assert "text-align: left !important;" in text
