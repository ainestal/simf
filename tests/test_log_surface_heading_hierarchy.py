"""WCAG 2.4.6 / 1.3.1 heading-hierarchy regression tests for the Why-died
surface.

Before this fix, `log_surface.py` rendered "Why did I die?" as an h3 and
`log_formatters._verdict_card` (the death verdict — the surface's actual
answer) rendered its own h2. On a cold visit (before any analysis has run)
that produced a page hierarchy of h1 ("simf") -> h3 ("Why did I die?"),
skipping h2 entirely; once a verdict rendered, the h3 title sat ABOVE an
h2 later in the DOM, a non-monotonic structure a screen reader's heading
list can't make sense of.

Fixed by making "Why did I die?" — which renders on every visit, cold or
not — the surface's one h2, and demoting `_verdict_card` to h3 (a
subsection of the page, sibling to "Death timeline" / "Where you died" /
"Who keeps killing you", all already h3). Pinned via `inspect.getsource`,
the same static-source-inspection pattern `test_cd_plan_tab.py` already
uses for this exact "one h2 verdict per surface" convention — no
Streamlit render context needed.
"""

from __future__ import annotations

import inspect

from simf.ui import log_formatters, log_surface


def test_why_did_i_die_title_is_h2():
    """The page title must render as a real h2 on every visit — including
    a cold one, before any verdict card exists — so the surface never
    skips a heading level."""
    src = inspect.getsource(log_surface.render_surface_log)
    assert '"## Why did I die?"' in src, (
        "The page title must be a markdown h2 ('## ...') so a cold visit "
        "(before any verdict renders) doesn't skip from h1 straight to a "
        "deeper heading level."
    )
    assert '"### Why did I die?"' not in src, (
        "Title regressed to h3 — this reintroduces the h1->h3 skip on a cold visit."
    )


def test_verdict_card_renders_h3_not_h2():
    """`_verdict_card` (the death verdict) must be h3, not h2 — the page's
    one h2 is `render_surface_log`'s own title, which renders unconditionally
    even before any verdict exists. A future refactor could silently
    re-promote this back to h2, recreating a second top-level heading the
    moment a verdict renders; this test catches that."""
    src = inspect.getsource(log_formatters._verdict_card)
    assert "<h3>" in src and "</h3>" in src, (
        "Death-verdict card must render an h3 — the page's 'Why did I "
        "die?' title (log_surface.py) is the one h2 for this surface."
    )
    assert "<h2>" not in src, "Death-verdict card leaked an h2."
