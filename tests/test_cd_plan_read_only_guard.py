"""Read-only guard on the CD-plan "Find best plan" button.

When the page loads with `?ro=1` (cold-share URL — a raid lead reviewing
a candidate's posted simf loadout), the optimizer search must NOT fire.
The button surface in `render_cd_plan_panel` swaps to a WCAG 2.1.1
compliant `aria_disabled_button` and a caption explains how to recover.

Without this guard a stranger viewing a shared link would burn ~20-30s
of compute (Pi-class hardware) and mutate session-level cache state on
what is supposed to be a passive review surface.

Session-10 queue (annotation > read-only guard > HRPS column);
annotation shipped via PR #112, this is the second item.
"""

from __future__ import annotations

import inspect

from streamlit.testing.v1 import AppTest

from simf.ui import log_view

# ─── Source-pinned contract ──────────────────────────────────────────────────


def test_cd_plan_panel_read_only_branch_exists() -> None:
    """The renderer must guard the "Find best plan" button on read-only —
    via `_is_read_only()`, NOT a narrower direct session-state read.

    Without an explicit branch in `render_cd_plan_panel`, the primary
    `st.button` falls through to the search path — and `?ro=1` viewers
    can kick off a 20-30s optimizer run that mutates the host's
    session-state cache.

    Checking `_is_read_only()` specifically (not just any read-only-ish
    check) matters: a launch-readiness audit (2026-08-01) found this panel
    used to check only `st.session_state.get("read_only", False)`, which is
    set exclusively by a `?ro=1` share-URL directive — never by
    `SIMF_PUBLIC` itself. That let any ordinary public-instance visitor who
    didn't arrive via such a link run the unguarded search freely. See
    test_public_visitor_without_ro1_param_is_still_guarded below for the
    regression coverage.
    """
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "_is_read_only()" in src, (
        "render_cd_plan_panel no longer calls _is_read_only() — either the "
        "cold-share `?ro=1` guard or the SIMF_PUBLIC guard (or both) may be gone."
    )


def test_cd_plan_panel_read_only_uses_aria_disabled_button() -> None:
    """Read-only must swap to the WCAG 2.1.1-compliant helper, NOT
    `st.button(disabled=True)`. The native disabled attribute strips
    the button from the a11y tree entirely; `aria_disabled_button` keeps
    it focusable with `aria-disabled="true"` + `aria-describedby` on the
    help text. PR #82 established this pattern across 8 sites and this
    site needs parity.
    """
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "aria_disabled_button" in src, (
        "Read-only branch dropped aria_disabled_button — SR users will "
        "no longer perceive the button at all when ?ro=1 is set."
    )


def test_cd_plan_panel_read_only_branch_returns_before_optimizer() -> None:
    """The read-only branch must short-circuit before `_cached_cd_plan`.

    A misplaced guard (e.g., disabling the button visually but still
    calling the optimizer on rerun) would defeat the whole point —
    the guard must early-return, not just dim the button.
    """
    src = inspect.getsource(log_view.render_cd_plan_panel)
    ro_idx = src.index("_is_read_only()")
    optimize_idx = src.index("_cached_cd_plan(")
    assert ro_idx < optimize_idx, (
        "Read-only check fires AFTER _cached_cd_plan — the optimizer "
        "still runs on cold-share URLs, defeating the guard."
    )
    # And the branch must `return` before the search button line so
    # the active `st.button` never renders on a read-only page.
    branch_body = src[ro_idx:optimize_idx]
    assert "return" in branch_body, (
        "Read-only branch doesn't early-return — control falls through "
        "to the active Find best plan button."
    )


# ─── AppTest smoke: read-only renders the dimmed surface ─────────────────────


def _render_panel_script(read_only: bool) -> str:
    """Inline Streamlit script that renders the CD-plan panel under a
    minimal char_dict so we can exercise the read-only branch without
    needing a real log on disk. The branch we care about fires BEFORE
    any optimizer call, so a stub char_dict + missing log is fine."""
    return f"""
import streamlit as st
from simf.ui import log_view

st.session_state["read_only"] = {read_only!r}

char_dict = {{
    "name": "Brutoh",
    "server": "uldum",
    "region": "eu",
    "race": "earthen",
    "class_spec": "protection_warrior",
    "talents": "default",
    "stamina": 12000,
    "armor_from_gear": 5500,
    "haste_rating": 1200,
    "crit_rating": 800,
    "mastery_rating": 600,
    "versatility_rating": 1400,
    "max_hp_override": None,
}}

log_view.render_cd_plan_panel(
    log_name="examples/brutoh.txt",
    target="Brutoh",
    run_index=0,
    char_dict=char_dict,
    healer_profile="m+_high_key_healer",
    uncalibrated_warning="",
    segments=None,
)
"""


def test_apptest_read_only_renders_aria_disabled_html() -> None:
    """End-to-end: when `read_only=True` is in session state, the panel
    emits the aria-disabled HTML (the helper writes via `st.markdown`)
    and no active `Find best plan` Streamlit button materializes."""
    at = AppTest.from_string(_render_panel_script(read_only=True))
    at.run()

    # The helper writes markdown — aggregate every markdown blob and
    # confirm the aria-disabled attribute landed.
    markdown_blobs = " ".join(
        getattr(m, "value", "") or getattr(m, "body", "") for m in at.markdown
    )
    assert 'aria-disabled="true"' in markdown_blobs, (
        "Read-only render did not emit aria-disabled HTML — the guard "
        "either short-circuited too early or the helper wasn't called."
    )

    # No active button means clicking can't trigger the optimizer.
    # When the read-only branch returns, `st.button("Find best plan", ...)`
    # never runs, so the button widget list is empty for that label.
    button_labels = [getattr(b, "label", "") for b in at.button]
    assert "Find best plan" not in button_labels, (
        "Active Find best plan button still rendered under read_only=True — "
        "a stranger clicking it would kick off the optimizer."
    )


def test_apptest_active_mode_renders_real_button() -> None:
    """Negative control: without `read_only=True`, the active
    `st.button("Find best plan", ...)` renders as today — the guard
    only affects the cold-share path."""
    at = AppTest.from_string(_render_panel_script(read_only=False))
    at.run()

    button_labels = [getattr(b, "label", "") for b in at.button]
    assert "Find best plan" in button_labels, (
        "Active mode lost the Find best plan button — the guard "
        "regressed the normal user surface, not just the read-only path."
    )


# ─── Regression: SIMF_PUBLIC alone must guard the button too ─────────────────
#
# The bug this covers (found by a launch-readiness audit, 2026-08-01): the
# panel used to check ONLY `st.session_state.get("read_only", False)`, which
# is set exclusively by a `?ro=1` share-URL directive (`load._apply_share_url`)
# — never by `SIMF_PUBLIC` itself. An ordinary public-instance visitor who
# reached this panel WITHOUT a `?ro=1` link (e.g. a fresh "Why did I die?"
# lookup) had `st.session_state["read_only"]` unset and could press "Find
# best plan" freely — the one heavy, unguarded Monte Carlo search on the
# whole public surface. The fix switches the check to `_is_read_only()`,
# which ORs in `_is_public_mode()`.


def test_public_visitor_without_ro1_param_is_still_guarded(monkeypatch) -> None:
    """SIMF_PUBLIC=1 alone — no `?ro=1`, no session-state `read_only` — must
    still disable the button. This is the actual vulnerability: without the
    fix, this test's setup is indistinguishable from a normal, active-mode
    visitor and the button would render active."""
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    at = AppTest.from_string(_render_panel_script(read_only=False))
    at.run()

    button_labels = [getattr(b, "label", "") for b in at.button]
    assert "Find best plan" not in button_labels, (
        "A SIMF_PUBLIC visitor with no ?ro=1 param and no session-state "
        "read_only flag can still run the unguarded optimizer search — "
        "the SIMF_PUBLIC gap is back."
    )

    markdown_blobs = " ".join(
        getattr(m, "value", "") or getattr(m, "body", "") for m in at.markdown
    )
    assert 'aria-disabled="true"' in markdown_blobs, (
        "SIMF_PUBLIC visitor did not get the aria-disabled surface either."
    )
