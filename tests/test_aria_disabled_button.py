"""WCAG 2.1.1-compliant disabled-button helper tests.

``aria_disabled_button`` is the planned drop-in replacement for
``st.button(disabled=True)`` — the latter strips the button from the
accessibility tree, the former keeps it focusable and announced.

These tests pin the HTML contract that screen-reader implementations
key off. Visual styling is covered by the lazy CSS injection but
isn't asserted here — the CSS is a presentational concern, the
ARIA attributes are the a11y-compliance contract.
"""

from __future__ import annotations

import inspect
import re

from streamlit.testing.v1 import AppTest

from simf.ui.helpers import aria_button

# ─── Helper output contract ──────────────────────────────────────────────────


def _run_button_app(**kwargs) -> AppTest:
    """Stand up a minimal AppTest that calls aria_disabled_button once.

    Returns the AppTest after a single run so callers can inspect the
    rendered ``markdown`` elements (where our HTML lands).
    """
    label = kwargs.pop("label", "Test Button")
    help_text = kwargs.pop("help", None)
    key = kwargs.pop("key", None)

    script = f"""
import streamlit as st
from simf.ui.helpers.aria_button import aria_disabled_button

aria_disabled_button(
    {label!r},
    help={help_text!r},
    key={key!r},
)
"""
    at = AppTest.from_string(script)
    at.run()
    return at


def test_emits_aria_disabled_true() -> None:
    """The HTML must include ``aria-disabled="true"`` — this is the
    whole point of the helper. Without this attribute SR users hear
    the button as actionable."""
    at = _run_button_app(label="Reset Helm trial")
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)
    assert 'aria-disabled="true"' in markdown_blobs


def test_emits_tabindex_zero_to_keep_focusable() -> None:
    """``tabindex="0"`` keeps the button in the natural tab order so
    keyboard users can land on it. Streamlit's native disabled state
    sets tabindex="-1" implicitly, which is the WCAG 2.1.1 gap."""
    at = _run_button_app(label="Reset Helm trial")
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)
    assert 'tabindex="0"' in markdown_blobs


def test_label_is_html_escaped() -> None:
    """User-facing labels can contain ``<`` / ``>`` (e.g. "✗ <no alts>");
    the label has to survive HTML escaping intact in the rendered output
    — the rendered HTML must contain the entity, not raw markup that
    could break the DOM."""
    at = _run_button_app(label="Drop <bad>", help=None)
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)
    assert "Drop &lt;bad&gt;" in markdown_blobs
    assert "<bad>" not in markdown_blobs


def test_help_text_wires_aria_describedby() -> None:
    """When ``help`` is provided, ``aria-describedby`` must point to a
    sibling element carrying the same text — that's how SR announces
    the disabled-state rationale."""
    at = _run_button_app(label="Try X", help="Read-only share — trial swaps disabled.")
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)

    # aria-describedby must point to an id that exists on a sibling.
    m = re.search(r'aria-describedby="(aria-btn-[a-f0-9]+-help)"', markdown_blobs)
    assert m is not None, "aria-describedby missing or malformed"
    describer_id = m.group(1)

    # The pointed-at id must appear on a span carrying the help text.
    assert f'id="{describer_id}"' in markdown_blobs
    assert "Read-only share" in markdown_blobs


def test_help_text_is_html_escaped() -> None:
    """Help text comes from app code so it shouldn't carry raw HTML,
    but if a future copy edit slips in an ``&`` or ``<`` the helper
    must escape it. Defensive."""
    at = _run_button_app(label="X", help="Locked & <closed>")
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)
    assert "Locked &amp; &lt;closed&gt;" in markdown_blobs


def test_no_help_omits_describedby() -> None:
    """When ``help`` is None, no ``aria-describedby`` should be emitted —
    SR users only see the label, no hidden sibling. Avoids dangling
    ids referring to non-existent describers."""
    at = _run_button_app(label="X", help=None)
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)
    assert "aria-describedby" not in markdown_blobs


def test_help_text_also_in_title_attr_for_hover_tooltip() -> None:
    """Sighted users get the same tooltip behaviour they had with
    ``st.button(help=...)``  — via the ``title`` attribute on the
    button element. This mirrors Streamlit's native tooltip surface."""
    at = _run_button_app(label="X", help="Hover me")
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)
    assert 'title="Hover me"' in markdown_blobs


def test_button_class_is_consistent_for_css_targeting() -> None:
    """All aria-disabled buttons share the same class so a single CSS
    rule styles the lot. The class name is the contract for the
    page-level stylesheet."""
    at = _run_button_app(label="X")
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)
    assert "simf-aria-disabled-btn" in markdown_blobs


def test_different_keys_produce_different_ids() -> None:
    """Two buttons with the same label but different ``key``s must get
    distinct ids so ``aria-describedby`` doesn't collide."""
    at = AppTest.from_string(
        """
import streamlit as st
from simf.ui.helpers.aria_button import aria_disabled_button

aria_disabled_button("Same label", help="Help A", key="a")
aria_disabled_button("Same label", help="Help B", key="b")
"""
    )
    at.run()
    markdown_blobs = " ".join(getattr(m, "value", "") or m.body for m in at.markdown)

    ids = re.findall(r'id="(aria-btn-[a-f0-9]+)"', markdown_blobs)
    button_ids = [i for i in ids if not i.endswith("-help")]
    assert len(button_ids) == 2, f"expected 2 button ids, got {button_ids}"
    assert len(set(button_ids)) == 2, "keys did not produce distinct button ids"


# ─── Migration tripwire on app.py ────────────────────────────────────────────


def test_all_disabled_button_sites_migrated_to_aria_disabled_button() -> None:
    """PR 1 shipped the helper + 2 call-site migrations as proof.
    PR 2 closes the migration across the remaining 6 sites — the
    a11y story is now WHOLE, not half-shipped (per director: "half-
    shipped a11y is worse than not starting").

    This tripwire is stronger than the PR 1 version: it asserts that
    ``src/simf/ui/app.py`` contains ZERO ``st.button(disabled=True)``
    call sites anywhere. Any new disabled button must use
    ``aria_disabled_button`` or this test fails.

    Migrated this PR:
      - vault read-only "Try X" (`vault_try_*`)
      - alt read-only "Try X" (`try_alt_*`)
      - read-only "Reset {label}" per slot (`trial_reset_{slot}`)
      - read-only "Reset all" (`trial_reset`)
      - read-only "Trial all N" (`trial_all_recommendations`)
      - read-only "Compute verdict" (`compute_verdict_read_only`)
    """
    from simf.ui import app

    src = inspect.getsource(app)

    # Strip out comments so the test isn't fooled by the WCAG
    # explanation header comments (which legitimately contain the
    # string "disabled=True"). Then assert zero live call sites.
    code_only = re.sub(r"#.*", "", src)
    live_sites = re.findall(r"\bdisabled\s*=\s*True\b", code_only)
    assert not live_sites, (
        f"app.py still has {len(live_sites)} st.button(disabled=True) call "
        f"site(s) — every disabled button must use aria_disabled_button"
    )


def test_helper_module_does_not_import_streamlit_at_module_level() -> None:
    """The helper module must lazy-import Streamlit inside its functions
    so importing this file in a non-Streamlit context (unit tests, CLI
    tooling) doesn't drag in the full Streamlit runtime. Mirrors the
    pattern used by ``pareto_scatter.py``."""
    src = inspect.getsource(aria_button)
    # Module-level imports come BEFORE any function definition. Scan
    # only the prefix up to the first `def `.
    first_def = src.index("\ndef ")
    module_level = src[:first_def]
    assert "import streamlit" not in module_level, (
        "aria_button imports streamlit at module level — break the lazy-import contract"
    )
