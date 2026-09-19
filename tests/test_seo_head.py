"""Tests for `ui.helpers.seo_head.inject_seo_head_tags` — the window.parent.
document meta/OG/JSON-LD injection that works around Streamlit's vendored
index.html shipping a hardcoded ``<title>Streamlit</title>`` and no meta
tags at all (SEO audit, 2026-08-01).

`AppTest.from_function` runs the render call's SOURCE CODE in an isolated
script context (see `tests/test_danger_pull_cheatsheet.py` for the same
pattern) — the import happens inside the function body.
"""

from __future__ import annotations

import json

from streamlit.testing.v1 import AppTest

from simf.ui.helpers.seo_head import DESCRIPTION, SITE_URL


def _run() -> AppTest:
    def script():
        from simf.ui.helpers.seo_head import inject_seo_head_tags

        inject_seo_head_tags()

    at = AppTest.from_function(script)
    at.run()
    return at


def test_inject_seo_head_tags_renders_without_error():
    at = _run()
    assert at.exception == []


def test_inject_seo_head_tags_emits_exactly_one_iframe():
    at = _run()
    assert len(at.get("iframe")) == 1


def test_injected_html_has_an_idempotency_guard_and_all_tags():
    # AppTest can't execute the iframe's JS (no real browser), so this
    # checks the generated JS SOURCE is well-formed — the actual runtime
    # behavior (exactly 11 tags land, once, even across reruns) was
    # verified live via Playwright against the dev instance, 2026-08-01.
    at = _run()
    html = at.get("iframe")[0].proto.srcdoc
    assert "doc.querySelector('[data-simf-seo]')" in html  # idempotency guard
    assert "doc.head.appendChild" in html
    assert "og:title" in html
    assert "og:description" in html
    assert "twitter:card" in html
    assert "canonical" in html
    assert "application/ld+json" in html


def test_injected_tags_array_has_the_expected_entries():
    at = _run()
    html = at.get("iframe")[0].proto.srcdoc
    marker = "var tags = "
    start = html.index(marker) + len(marker)
    end = html.index(";", start)
    tags = json.loads(html[start:end])
    # 9 <meta> + 1 <link rel=canonical>; plus the JSON-LD <script> appended
    # separately makes the 11 elements the live Playwright check counted.
    assert len(tags) == 10
    names_or_props = {t[1].get("name") or t[1].get("property") or t[1].get("rel") for t in tags}
    assert names_or_props == {
        "description",
        "og:title",
        "og:description",
        "og:type",
        "og:url",
        "og:site_name",
        "twitter:card",
        "twitter:title",
        "twitter:description",
        "canonical",
    }
    description_tag = next(t for t in tags if t[1].get("name") == "description")
    assert description_tag[1]["content"] == DESCRIPTION


def test_injected_jsonld_is_valid_and_matches_description():
    at = _run()
    html = at.get("iframe")[0].proto.srcdoc
    # The JSON-LD is double-encoded (Python json.dumps of a json.dumps
    # string) so it survives as a JS string literal in the <script> body —
    # decode through both layers to get the real object back.
    marker = "ld.textContent = "
    start = html.index(marker) + len(marker)
    end = html.index(";", start)
    js_string_literal = json.loads(html[start:end])
    data = json.loads(js_string_literal)
    assert data["@type"] == "WebApplication"
    assert data["url"] == SITE_URL
    assert data["description"] == DESCRIPTION
    assert data["offers"]["price"] == "0"


def test_description_is_a_reasonable_meta_description_length():
    # Search engines truncate meta descriptions around ~155-160 chars —
    # guard against it silently growing past that as copy gets edited.
    assert 50 <= len(DESCRIPTION) <= 160
