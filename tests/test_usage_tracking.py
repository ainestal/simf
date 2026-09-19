"""Tests for `ui.helpers.usage_tracking` — the session-correlated event
recorder layered on `core.share_hits` (2026-08-16 usage-analytics
broadening; see `core.share_hits`'s module docstring for why the old
minimal-tracking framing was dropped).

`AppTest.from_function` runs the render call's SOURCE CODE in an isolated
script context — same pattern as `tests/test_privacy_footer.py`.
"""

from __future__ import annotations

import json

from streamlit.testing.v1 import AppTest


def _render_twice() -> None:
    import streamlit as st

    from simf.ui.helpers.usage_tracking import record_event, usage_session_id

    st.session_state["_sid_first"] = usage_session_id()
    st.session_state["_sid_second"] = usage_session_id()
    record_event("test_event", spec="protection_warrior")


def _run() -> AppTest:
    at = AppTest.from_function(_render_twice)
    at.run()
    return at


def test_session_id_stable_within_one_session(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(tmp_path / "hits.jsonl"))
    at = _run()
    assert at.session_state["_sid_first"] == at.session_state["_sid_second"]
    assert len(at.session_state["_sid_first"]) == 8  # secrets.token_hex(4) -> 8 hex chars


def test_record_event_writes_sid_and_fields(monkeypatch, tmp_path):
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))
    at = _run()
    lines = hits_path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "test_event"
    assert entry["spec"] == "protection_warrior"
    assert entry["sid"] == at.session_state["_sid_first"]


def test_record_event_fires_regardless_of_public_mode(monkeypatch, tmp_path):
    """Unlike the old cold-share-load signal (still public-mode-gated at its
    own call sites), the general usage-analytics events record for the
    owner's own sessions too — the maintainer is simf's primary user, per
    2026-08-16 direction."""
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))
    _run()
    assert hits_path.exists()
    entry = json.loads(hits_path.read_text().splitlines()[0])
    assert entry["event"] == "test_event"


def test_record_event_drops_unallowlisted_kwargs(monkeypatch, tmp_path):
    """`record_event` passes fields straight through to `share_hits.record`,
    which silently drops anything outside its own allowlist — this pins that
    an unexpected kwarg can't sneak a new field into the log without a
    matching `_RECORDED_FIELDS` change."""
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))

    def _render() -> None:
        from simf.ui.helpers.usage_tracking import record_event

        record_event("test_event", not_allowlisted="should be dropped")

    at = AppTest.from_function(_render)
    at.run()
    entry = json.loads(hits_path.read_text().splitlines()[0])
    assert "not_allowlisted" not in entry
