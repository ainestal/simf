"""SEO metadata injection for the single-page Streamlit app.

Streamlit's vendored ``index.html`` ships a hardcoded ``<title>Streamlit</title>``
and no meta description / Open Graph / structured data — ``st.set_page_config``'s
``page_title`` only sets ``document.title`` via client-side JS after the app
loads, and there is no Streamlit API for meta tags at all.

This reaches into ``window.parent.document`` from a same-origin
``st.iframe`` (srcdoc iframes inherit the parent's origin, so this is not
a cross-origin hack) to add the tags a JS-executing crawler
(Googlebot) or a human's browser tab will see. It deliberately does NOT
help non-JS crawlers — Discord/Slack/Twitter/Reddit link-unfurlers fetch raw
HTML and never run this script, so they still see nothing useful. Fixing
that requires origin- or edge-side rendering, which is a separate,
infrastructure-level decision (see the SEO audit in ROADMAP.md).
"""

from __future__ import annotations

import json

import streamlit as st

SITE_URL = "https://simf.cc/"
# Kept under ~155 chars — Google/social unfurlers truncate meta
# descriptions around there (see test_description_is_a_reasonable_meta_
# description_length). "Raidbots alternative" is the strongest validated
# search-intent match found in the 2026-08-01 SEO audit (real, recurring
# "Ask Mr Robot vs Raidbots"-style comparison threads on the WoW forums;
# Raidbots itself dropped tank survivability metrics in 2017), so it's
# worth the words over a purely generic description.
DESCRIPTION = (
    "Free WoW Mythic+ tank survivability simulator — the Raidbots "
    "alternative for tanks. Check your gear, vault picks, or why you died."
)
_TITLE = "simf — Free WoW Mythic+ Tank Survivability Simulator"

_JSONLD = {
    "@context": "https://schema.org",
    "@type": "WebApplication",
    "name": "simf",
    "url": SITE_URL,
    "description": DESCRIPTION,
    "applicationCategory": "GameApplication",
    "operatingSystem": "Web",
    "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
}

_META_TAGS = [
    ("meta", {"name": "description", "content": DESCRIPTION}),
    ("meta", {"property": "og:title", "content": _TITLE}),
    ("meta", {"property": "og:description", "content": DESCRIPTION}),
    ("meta", {"property": "og:type", "content": "website"}),
    ("meta", {"property": "og:url", "content": SITE_URL}),
    ("meta", {"property": "og:site_name", "content": "simf"}),
    ("meta", {"name": "twitter:card", "content": "summary"}),
    ("meta", {"name": "twitter:title", "content": _TITLE}),
    ("meta", {"name": "twitter:description", "content": DESCRIPTION}),
    ("link", {"rel": "canonical", "href": SITE_URL}),
]


def inject_seo_head_tags() -> None:
    """Add meta/OG/Twitter/JSON-LD tags to the real document head.

    Idempotent — guarded by a ``data-simf-seo`` marker so repeated
    Streamlit reruns (every widget interaction reruns this whole module)
    never duplicate tags, regardless of whether Streamlit reuses the
    positional iframe element or recreates it.
    """
    html = f"""
    <script>
      (function() {{
        var doc = window.parent.document;
        if (doc.querySelector('[data-simf-seo]')) return;
        var tags = {json.dumps(_META_TAGS)};
        tags.forEach(function(t) {{
          var el = doc.createElement(t[0]);
          Object.keys(t[1]).forEach(function(k) {{ el.setAttribute(k, t[1][k]); }});
          el.setAttribute('data-simf-seo', '1');
          doc.head.appendChild(el);
        }});
        var ld = doc.createElement('script');
        ld.type = 'application/ld+json';
        ld.setAttribute('data-simf-seo', '1');
        ld.textContent = {json.dumps(json.dumps(_JSONLD))};
        doc.head.appendChild(ld);
      }})();
    </script>
    """
    # width/height=0 aren't valid st.iframe values (must be a positive int,
    # "stretch", or "content") — 1px is the smallest allowed, effectively
    # invisible.
    st.iframe(html, height=1, width=1)
