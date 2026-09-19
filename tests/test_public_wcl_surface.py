"""Public WCL-URL on-ramp: surface split + rate-limit gate (ADR 0001).

The public instance exposes the WCL-URL "Why did I die?" flow ONLY (no local
file upload), gated by the shared-owner-key rate-limit guard. These tests cover
the surface split and each refusal branch of ``_public_wcl_analyze_gate`` via
AppTest scripts (so ``st.session_state`` is live).
"""

from __future__ import annotations

import contextlib

from streamlit.testing.v1 import AppTest

from simf.core import wcl_budget


def _all_text(at) -> str:
    parts: list[str] = []
    for attr in ("markdown", "caption", "info", "error", "warning"):
        with contextlib.suppress(Exception):
            parts += [getattr(e, "value", "") or "" for e in getattr(at, attr)]
    return " ".join(parts)


# ─── surface split: public = WCL-only, owner = both tabs ─────────────────────


def test_public_surface_renders_wcl_only():
    """``render_surface_log(public=True)`` shows the WCL caption + URL input
    and returns BEFORE the local-upload tab (the owner caption is absent)."""

    def _script():
        from simf.io import wcl_api
        from simf.ui.log_view import render_surface_log

        wcl_api._load_credentials = lambda: {"client_id": "x", "client_secret": "y"}
        render_surface_log(public=True)

    at = AppTest.from_function(_script, default_timeout=20).run()
    text = _all_text(at)
    assert "Paste a Warcraft Logs report URL" in text
    assert "Drop in a combat log" not in text  # owner two-tab caption absent
    assert any(ti.label == "Warcraft Logs URL" for ti in at.text_input)


def test_owner_surface_renders_both_tabs():
    """Owner mode (``public=False``) keeps the two-tab caption — unchanged."""

    def _script():
        from simf.io import wcl_api
        from simf.ui.log_view import render_surface_log

        wcl_api._load_credentials = lambda: {"client_id": "x", "client_secret": "y"}
        render_surface_log(public=False)

    at = AppTest.from_function(_script, default_timeout=20).run()
    text = _all_text(at)
    assert "Drop in a combat log" in text


# ─── _public_wcl_analyze_gate refusal branches ───────────────────────────────
# NOTE: AppTest.from_function runs the script in a subprocess that does NOT see
# this module's top-level imports/helpers — every name used inside ``_script``
# must be imported (or built) inside it.


def test_gate_refuses_fight_over_length_cap():
    def _script():
        from types import SimpleNamespace

        import streamlit as st

        from simf.ui.log_view import _PUBLIC_MAX_FIGHT_MINUTES, _public_wcl_analyze_gate

        fight = SimpleNamespace(
            start_time_ms=0, end_time_ms=int((_PUBLIC_MAX_FIGHT_MINUTES + 10) * 60_000)
        )
        st.session_state["_msg"] = _public_wcl_analyze_gate(fight, token="t")

    at = AppTest.from_function(_script, default_timeout=20).run()
    assert "capped at" in at.session_state["_msg"]


def test_gate_refuses_during_session_cooldown():
    def _script():
        import time
        from types import SimpleNamespace

        import streamlit as st

        from simf.ui.log_view import _public_wcl_analyze_gate

        # last analyze was just now → cooldown active, fight under the cap.
        st.session_state["_wcl_last_analyze_ts"] = time.monotonic()
        fight = SimpleNamespace(start_time_ms=0, end_time_ms=60_000)
        st.session_state["_msg"] = _public_wcl_analyze_gate(fight, token="t")

    at = AppTest.from_function(_script, default_timeout=20).run()
    assert "just ran an analysis" in at.session_state["_msg"]


def test_gate_refuses_when_global_window_exhausted(monkeypatch):
    def _script():
        from types import SimpleNamespace

        import streamlit as st

        from simf.core import wcl_budget
        from simf.ui.log_view import _public_wcl_analyze_gate

        guard = wcl_budget.public_guard()
        for _ in range(200):
            guard.check()
        fight = SimpleNamespace(start_time_ms=0, end_time_ms=60_000)
        st.session_state["_msg"] = _public_wcl_analyze_gate(fight, token="t")

    # The guard is a process singleton that persists across in-process AppTests
    # — monkeypatch (not raw assignment) so pytest restores the original after
    # this test, instead of leaking the exhausted/replaced guard to whichever
    # test runs next in this xdist worker.
    monkeypatch.setattr(wcl_budget, "_PUBLIC_GUARD", wcl_budget.WCLRateGuard())
    at = AppTest.from_function(_script, default_timeout=20).run()
    assert "handling a lot of Warcraft Logs requests" in at.session_state["_msg"]


def test_gate_refuses_when_points_below_reserve(monkeypatch):
    def _script():
        from types import SimpleNamespace

        import streamlit as st

        from simf.io import wcl_api
        from simf.ui.log_view import _public_wcl_analyze_gate

        # Pass length + cooldown + window; force the points pre-flight low.
        wcl_api.fetch_rate_limit = lambda token: wcl_api.WCLRateLimit(
            limit_per_hour=3600, points_spent_this_hour=3580.0, points_reset_in=300
        )
        fight = SimpleNamespace(start_time_ms=0, end_time_ms=60_000)
        st.session_state["_msg"] = _public_wcl_analyze_gate(fight, token="t")

    # Fresh window (monkeypatched, auto-restored) so a prior exhausted-guard
    # test can't bleed in and short us before the points pre-flight — the
    # singleton persists in-process across AppTests.
    monkeypatch.setattr(wcl_budget, "_PUBLIC_GUARD", wcl_budget.WCLRateGuard())
    at = AppTest.from_function(_script, default_timeout=20).run()
    assert "Warcraft Logs is busy" in at.session_state["_msg"]


def test_gate_admits_when_all_checks_pass(monkeypatch):
    def _script():
        from types import SimpleNamespace

        import streamlit as st

        from simf.io import wcl_api
        from simf.ui.log_view import _public_wcl_analyze_gate

        wcl_api.fetch_rate_limit = lambda token: wcl_api.WCLRateLimit(
            limit_per_hour=3600, points_spent_this_hour=10.0, points_reset_in=300
        )
        fight = SimpleNamespace(start_time_ms=0, end_time_ms=120_000)
        msg = _public_wcl_analyze_gate(fight, token="t")
        st.session_state["_msg"] = "ADMIT" if msg is None else msg

    monkeypatch.setattr(wcl_budget, "_PUBLIC_GUARD", wcl_budget.WCLRateGuard())
    at = AppTest.from_function(_script, default_timeout=20).run()
    assert at.session_state["_msg"] == "ADMIT"


def test_public_player_details_is_guarded(monkeypatch):
    """Regression (validator finding): the per-fight player-details GraphQL call
    must NOT bypass the public guard. With the window exhausted, flipping to a
    fight skips the fetch entirely (falls back to the free-text target picker)."""

    def _script():
        import streamlit as st

        from simf.core import wcl_budget
        from simf.io import wcl_api
        from simf.io.wcl_api import WCLFight, WCLReport
        from simf.ui import log_view

        wcl_api._load_credentials = lambda: {"client_id": "x", "client_secret": "y"}

        calls = {"n": 0}

        def _spy(report_code, fight_id, token):
            calls["n"] += 1
            return []

        wcl_api.fetch_player_details = _spy

        # Pre-seed the report cache so the report-fetch guard isn't what stops us
        # — we want to prove the PLAYER-DETAILS path is gated.
        report = WCLReport(
            code="ABC",
            start_time_ms=0,
            fights=[WCLFight(id=1, name="Pull", start_time_ms=0, end_time_ms=60_000, key_level=10)],
        )
        st.session_state["v9_wcl_report::ABC"] = report
        st.session_state["v9_wcl_report::ABC::token"] = "tok"
        st.session_state["v9_wcl_url"] = "https://www.warcraftlogs.com/reports/ABC#fight=1"

        # Exhaust the (already fresh, monkeypatched) global window.
        for _ in range(500):
            wcl_budget.public_guard().check()

        log_view._render_wcl_url_flow(public=True)
        st.session_state["_pd_calls"] = calls["n"]

    monkeypatch.setattr(wcl_budget, "_PUBLIC_GUARD", wcl_budget.WCLRateGuard())
    at = AppTest.from_function(_script, default_timeout=20).run()
    assert at.session_state["_pd_calls"] == 0


def test_public_player_details_fetches_when_window_has_room(monkeypatch):
    """Positive control: with a fresh window, the player-details fetch DOES run
    (so the guard refuses only under pressure, it doesn't break the happy path)."""

    def _script():
        import streamlit as st

        from simf.io import wcl_api
        from simf.io.wcl_api import WCLFight, WCLReport
        from simf.ui import log_view

        wcl_api._load_credentials = lambda: {"client_id": "x", "client_secret": "y"}

        calls = {"n": 0}

        def _spy(report_code, fight_id, token):
            calls["n"] += 1
            return []

        wcl_api.fetch_player_details = _spy

        report = WCLReport(
            code="ABC",
            start_time_ms=0,
            fights=[WCLFight(id=1, name="Pull", start_time_ms=0, end_time_ms=60_000, key_level=10)],
        )
        st.session_state["v9_wcl_report::ABC"] = report
        st.session_state["v9_wcl_report::ABC::token"] = "tok"
        st.session_state["v9_wcl_url"] = "https://www.warcraftlogs.com/reports/ABC#fight=1"

        log_view._render_wcl_url_flow(public=True)
        st.session_state["_pd_calls"] = calls["n"]

    monkeypatch.setattr(wcl_budget, "_PUBLIC_GUARD", wcl_budget.WCLRateGuard())  # fresh window
    at = AppTest.from_function(_script, default_timeout=20).run()
    assert at.session_state["_pd_calls"] == 1


def test_gate_points_query_failure_degrades_open(monkeypatch):
    """A rate-limit query error must NOT hard-block the flow (degrade open)."""

    def _script():
        from types import SimpleNamespace

        import streamlit as st

        from simf.io import wcl_api
        from simf.ui.log_view import _public_wcl_analyze_gate

        def _boom(token):
            raise RuntimeError("WCL down")

        wcl_api.fetch_rate_limit = _boom
        fight = SimpleNamespace(start_time_ms=0, end_time_ms=120_000)
        msg = _public_wcl_analyze_gate(fight, token="t")
        st.session_state["_msg"] = "ADMIT" if msg is None else msg

    monkeypatch.setattr(wcl_budget, "_PUBLIC_GUARD", wcl_budget.WCLRateGuard())
    at = AppTest.from_function(_script, default_timeout=20).run()
    assert at.session_state["_msg"] == "ADMIT"
