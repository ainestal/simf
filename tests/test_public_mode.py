"""SIMF_PUBLIC read-only public-instance mode (Phase 5, 2026-06-13).

A public deploy behind a reverse proxy / tunnel runs simf with `SIMF_PUBLIC=1`: a
locked-down, read-only public instance for the share-card wedge. It must:
  * force read-only (no `?ro=0` escape);
  * hide the local-log UPLOAD surface (the dominant OOM/abuse vector) while
    still exposing the WCL-URL "Why did I die?" flow behind a rate-limit guard
    (ADR 0001);
  * EXPOSE online gear lookup, but force the zero-auth Raider.IO source and
    rate-limit it (the owner's Blizzard creds are never spent, the SSRF-shaped
    armory host is never reached); Blizzard Armory stays owner-only;
  * still let a visitor load the demo / paste a /simc / open a shared build.

`_is_public_mode()` reads the env at call time, so monkeypatching `SIMF_PUBLIC`
before `app.run()` takes effect.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.core import wcl_budget
from simf.core.wcl_budget import WCLRateGuard
from simf.ui.app import _is_public_mode, _is_read_only, _sim_slot

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


# ─── pure helpers ────────────────────────────────────────────────────────────


def test_public_mode_env_toggle(monkeypatch):
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    assert _is_public_mode() is False
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("SIMF_PUBLIC", truthy)
        assert _is_public_mode() is True
    for falsy in ("0", "false", "", "no"):
        monkeypatch.setenv("SIMF_PUBLIC", falsy)
        assert _is_public_mode() is False


def test_public_mode_forces_read_only(monkeypatch):
    """Public mode is read-only regardless of session state — no ?ro=0 escape."""
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    assert _is_read_only() is True


def test_sim_slot_is_single_flight():
    """The single-flight guard hands out one slot at a time, non-blocking —
    so a 2nd concurrent sim degrades honestly instead of thrashing the GIL."""
    with _sim_slot() as a:
        assert a is True
        with _sim_slot() as b:
            assert b is False  # already held → non-blocking False, not a deadlock
    # Released on exit → available again.
    with _sim_slot() as c:
        assert c is True


# ─── AppTest integration ─────────────────────────────────────────────────────


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _labels(app) -> list[str]:
    return [b.label for b in app.button if b.label]


def test_public_mode_shows_wcl_nav_and_lookup_hides_upload(app, monkeypatch):
    """ADR 0001 + the 2026-06-14 landing redesign: the public instance exposes
    the WCL-URL "Why did I die?" surface AND the online name lookup (forced to
    Raider.IO, rate-limited). Only the local-log UPLOAD stays hidden (OOM —
    covered by test_public_mode_view_log_opens_wcl_surface)."""
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    labels = _labels(app)
    # Nav is present (WCL surface is reachable).
    assert "Why did I die?" in labels, labels
    assert "Gear & vault" in labels, labels
    # Online lookup is now ON in public (Raider.IO-only — see _do_raider_io_load).
    assert "Look up my character" in labels, labels
    markdowns = " ".join(getattr(m, "value", "") or "" for m in app.markdown)
    assert "Find your character by name" in markdowns
    # The /simc paste + the demo fallback are still here.
    assert "Load from /simc" in labels, labels
    assert "Load sample build" in labels, labels


def test_public_mode_hides_read_only_banner(app, monkeypatch):
    """The eye read-only banner is noise to a first-time visitor — public mode
    paints nothing (2026-06-14). Read-only is still ENFORCED inline at each
    disabled control; the ?ro=0 escape is never advertised."""
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    app.run()
    assert not app.exception
    markdowns = " ".join(getattr(m, "value", "") or "" for m in app.markdown)
    assert "Public read-only simf" not in markdowns
    assert "?ro=0" not in markdowns  # no escape hatch advertised in public mode
    # Enforcement is unchanged even though the banner is gone.
    assert _is_read_only() is True


def test_public_mode_view_log_opens_wcl_surface(app, monkeypatch):
    """ADR 0001: a ?view=log share/nav in public mode opens the WCL-URL-only
    "Why did I die?" surface (no local-file upload). It must NOT fall through
    to the load form, and must NOT render the local-log upload flow."""
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception
    markdowns = " ".join(getattr(m, "value", "") or "" for m in app.markdown)
    # WCL surface rendered: the "### Why did I die?" heading + the WCL URL input.
    assert "Why did I die?" in markdowns
    assert any(ti.label == "Warcraft Logs URL" for ti in app.text_input), [
        ti.label for ti in app.text_input
    ]
    # The owner-only two-tab caption ("Drop in a combat log") must NOT appear —
    # proves the public WCL-only branch was taken, not the local upload flow.
    captions = " ".join(getattr(c, "value", "") or "" for c in app.caption)
    assert "Drop in a combat log" not in (markdowns + " " + captions)


def test_non_public_keeps_nav_and_lookup(app, monkeypatch):
    """Default (owner) mode is unchanged: nav + online lookup present."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.run()
    assert not app.exception
    labels = _labels(app)
    assert "Why did I die?" in labels, labels
    assert "Look up my character" in labels, labels
    assert _is_read_only() is False


def _fill_lookup(app, name="Brutoh", realm="Uldum"):
    """Fill the name-lookup inputs (region defaults to eu) and click the
    'Look up my character' button. Mirrors the live name-lookup flow."""
    next(ti for ti in app.text_input if ti.key == "_rio_name").set_value(name).run()
    next(sb for sb in app.selectbox if sb.key == "_rio_realm").set_value(realm).run()
    next(b for b in app.button if b.label == "Look up my character").click().run()


def test_public_lookup_forces_raiderio_source(app, monkeypatch):
    """Public mode MUST force source='raiderio' so the owner's Blizzard creds
    are never spent and armory's region-host interpolation is never reached.
    ('auto' would hit Blizzard first when configured — which it is on the owner's box.)"""
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    # Fresh guard so an unrelated test's accumulated window can't deny this.
    monkeypatch.setattr(wcl_budget, "_GEAR_LOOKUP_GUARD", WCLRateGuard(per_minute=10, per_hour=100))
    captured: dict[str, str] = {}

    def _fake_fetch(name, realm, region, source):
        captured["source"] = source
        return None, source  # None → early return; no HydrateResult / rerun needed

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    app.run()
    _fill_lookup(app)
    assert not app.exception
    assert captured.get("source") == "raiderio", captured


def test_owner_lookup_uses_auto_source(app, monkeypatch):
    """Owner mode keeps source='auto' (Blizzard-first, exact stats) — the
    2026-06-09 behaviour."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    captured: dict[str, str] = {}

    def _fake_fetch(name, realm, region, source):
        captured["source"] = source
        return None, source

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    app.run()
    _fill_lookup(app)
    assert not app.exception
    assert captured.get("source") == "auto", captured


def test_public_lookup_blocked_by_session_cooldown(app, monkeypatch):
    """A public visitor who just looked up a character is denied a second
    immediate lookup (per-session cooldown) — the fetch never fires again."""
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    monkeypatch.setattr(wcl_budget, "_GEAR_LOOKUP_GUARD", WCLRateGuard(per_minute=10, per_hour=100))
    calls = {"n": 0}

    def _fake_fetch(name, realm, region, source):
        calls["n"] += 1
        return None, source

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    app.run()
    # Pretend a successful lookup just happened this session.
    app.session_state["_gear_lookup_last_ts"] = time.monotonic()
    app.run()
    _fill_lookup(app)
    assert not app.exception
    assert calls["n"] == 0, "cooldown must block the fetch entirely"
    md = "\n".join(str(m.value) for m in app.markdown)
    assert "try again in" in md.lower()
