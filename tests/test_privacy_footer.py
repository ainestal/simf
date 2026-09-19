"""Tests for `ui.helpers.privacy_footer.render_privacy_footer` — the
Blizzard-affiliation disclaimer + privacy notice added 2026-08 to satisfy
the Blizzard Developer API Terms of Use (attribution + a posted privacy
policy are contractual conditions of using the API, not etiquette).

`AppTest.from_function` runs the render call's SOURCE CODE in an isolated
script context — see `tests/test_seo_head.py` / `tests/test_danger_pull_
cheatsheet.py` for the same pattern.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from simf.ui.helpers.privacy_footer import (
    ATTRIBUTION,
    BLIZZARD_DISCLAIMER,
    DISCLAIMER_TITLE,
    PRIVACY_BLURB,
)


def _render() -> None:
    from simf.ui.helpers.privacy_footer import render_privacy_footer

    render_privacy_footer()


def _run() -> AppTest:
    at = AppTest.from_function(_render)
    at.run()
    return at


def test_renders_without_error():
    at = _run()
    assert at.exception == []


def test_renders_exactly_one_collapsed_expander():
    at = _run()
    assert len(at.expander) == 1
    assert at.expander[0].label == DISCLAIMER_TITLE


def test_blizzard_disclaimer_is_always_visible_not_collapsed():
    """The affiliation disclaimer must "clearly and conspicuously identify
    Blizzard" per the API terms (see module docstring) — it renders as its
    own top-level caption, not behind the click-to-reveal expander like the
    longer attribution/privacy text."""
    at = _run()
    top_level_body = "\n".join(str(c.value) for c in at.caption)
    assert BLIZZARD_DISCLAIMER in top_level_body
    assert "not affiliated with, endorsed, or sponsored by Blizzard" in top_level_body
    expander_body = "\n".join(str(c.value) for c in at.expander[0].caption)
    assert BLIZZARD_DISCLAIMER not in expander_body


def test_expander_contains_attribution_to_all_three_data_sources():
    at = _run()
    body = "\n".join(str(c.value) for c in at.expander[0].caption)
    assert ATTRIBUTION in body
    assert "Blizzard Battle.net API" in body
    assert "Raider.IO" in body
    assert "Warcraft Logs" in body


def test_expander_contains_the_privacy_blurb_and_the_wcl_cache_exception():
    at = _run()
    body = "\n".join(str(c.value) for c in at.expander[0].caption)
    assert PRIVACY_BLURB in body
    assert "No accounts" in body
    # The one real exception the audit found — the WCL-URL flow's disk
    # cache — must be disclosed, not glossed over by the general
    # "held only in your browser session" claim above it.
    assert "Why did I die?" in body
    assert "already public" in body
