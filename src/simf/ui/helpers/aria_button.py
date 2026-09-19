"""WCAG 2.1.1-compliant disabled-button replacement for ``st.button(disabled=True)``.

The problem this solves
-----------------------

Streamlit's ``st.button(label, disabled=True)`` sets the native HTML
``disabled`` attribute on the underlying ``<button>``. That HTML
attribute does two things at once:

  1. Removes the element from keyboard tab order, AND
  2. Removes the element from the accessibility tree entirely — screen
     readers don't announce it at all.

Combined, this means a screen-reader user can't perceive that the
button exists or learn *why* it's disabled. The WCAG 2.1.1 violation
isn't "the button can't be activated" (a disabled button shouldn't be
activatable — that's correct), it's "the user can't discover the
button or its disabled state." The recommended pattern is
``aria-disabled="true"`` instead of ``disabled`` — the button stays
focusable AND in the accessibility tree, gets announced as
"<label>, dimmed" or "<label>, disabled" depending on the SR, and
``aria-describedby`` carries the "why" so the user understands the
state.

What this helper does
---------------------

Emits a focusable ``<button>`` with ``aria-disabled="true"``, the
visual styling of a muted Streamlit secondary button, and an
``aria-describedby`` reference to a visually-hidden ``<span>`` that
carries the ``help`` text. SR users navigating by Tab hear:

    "Reset Helm trial, dimmed, Read-only share — trial swaps disabled"

Sighted users see the same muted button affordance they already get
from Streamlit's disabled state. Power users can still hover for
the help-text tooltip via the ``title`` attribute.

Scope of this PR
----------------

This is the **component PR** — the helper lives here and is wired
into 2 call sites in ``app.py`` as a proof point. The remaining
disabled-button call sites stay on ``st.button(disabled=True)`` until
a follow-up PR migrates them in batches.
"""

from __future__ import annotations

import hashlib
import html

_BUTTON_CSS_INJECTED = False


def _inject_css_once() -> None:
    """Lazy one-shot CSS injection.

    The same CSS works for every aria-disabled button on the page, so
    we inject it once per Streamlit script run (the module global
    resets each rerun, which is the right cadence — Streamlit injects
    fresh styles on every rerun anyway).
    """
    global _BUTTON_CSS_INJECTED
    if _BUTTON_CSS_INJECTED:
        return

    import streamlit as st

    st.markdown(
        """
<style>
/* aria_button.py — visual styling for WCAG 2.1.1-compliant disabled
   buttons. Shares the cohesion button family's tokens (radius/border/
   surface + steel focus ring) so a disabled button reads as a clearly-
   muted member of the SAME family — not a stray control in a different
   design (user-flagged 2026-06-21). The `var(--token, fallback)` form
   keeps the helper self-contained if it renders before app.py's :root.
   Muted text + not-allowed cursor are the disabled cues. */
button.simf-aria-disabled-btn {
    width: 100%;
    padding: 0.45rem 0.9rem;
    border-radius: var(--radius-control, 6px);
    border: 1px solid var(--border-subtle, rgba(26, 29, 34, 0.14));
    background: var(--surface-base, #f4f1ea);
    color: var(--text-dim, rgba(26, 29, 34, 0.45));
    font-family: inherit;
    font-size: 0.9rem;
    font-weight: 500;
    line-height: 1.4;
    cursor: not-allowed;
    text-align: center;
    box-sizing: border-box;
}
button.simf-aria-disabled-btn:focus-visible {
    /* Steel focus ring, matching the rest of the button family (was an
       off-palette #1f77b4 blue). Keeps focus visible for SR + keyboard. */
    outline: 2px solid var(--accent-gold, #34556e);
    outline-offset: 2px;
}
</style>
""",
        unsafe_allow_html=True,
    )
    _BUTTON_CSS_INJECTED = True


def _stable_id(*parts: str) -> str:
    """Deterministic short id for ``aria-describedby`` wiring.

    Multiple aria_button calls on the same page must produce different
    ids so SR doesn't conflate their help text. Hashing the label +
    caller-provided key gives a stable id without exposing internal
    state to the DOM.
    """
    blob = "::".join(parts).encode("utf-8")
    digest = hashlib.sha1(blob, usedforsecurity=False).hexdigest()[:10]
    return f"aria-btn-{digest}"


def aria_disabled_button(
    label: str,
    *,
    help: str | None = None,
    key: str | None = None,
) -> None:
    """Render a visually-disabled button that screen readers still see.

    Drop-in replacement for ``st.button(label, disabled=True, help=help)``
    that keeps the button in the accessibility tree per WCAG 2.1.1.
    Has no click semantics — disabled means non-interactive — but
    sighted-keyboard users still Tab through it and SR users still
    hear it announced as disabled.

    Args:
        label: button caption, identical to what ``st.button`` would show.
        help: tooltip + SR description. Visible on hover via the ``title``
            attribute, announced via ``aria-describedby``. Optional.
        key: stability hint for the SHA1-derived element id. Two calls
            with the same label can pass different ``key`` values to
            disambiguate; in practice the label alone is usually unique
            enough.
    """
    _inject_css_once()

    import streamlit as st

    btn_id = _stable_id(label, key or "")
    describedby_attr = ""
    title_attr = ""
    describer_html = ""
    if help:
        help_id = f"{btn_id}-help"
        describedby_attr = f' aria-describedby="{help_id}"'
        title_attr = f' title="{html.escape(help)}"'
        describer_html = (
            f'<span id="{help_id}" '
            'style="position:absolute;width:1px;height:1px;padding:0;'
            "margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;"
            f'border:0">{html.escape(help)}</span>'
        )

    st.markdown(
        f'<button class="simf-aria-disabled-btn" aria-disabled="true" '
        f'tabindex="0" id="{btn_id}"{describedby_attr}{title_attr}>'
        f"{html.escape(label)}</button>{describer_html}",
        unsafe_allow_html=True,
    )
