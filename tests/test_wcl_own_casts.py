"""Tests for the WCL ``Casts``-dataType fetcher powering the cooldown-aware
kick-availability split.

``fetch_own_cast_times`` is the WCL analog of ``combat_log.parse_cast_events``
— UNLIKE ``fetch_interrupted_spell_ids`` (party-wide evidence), this IS
actor-specific: it answers "when did *this tank* personally cast their
interrupt," not "did anyone in the party." Mock the GraphQL layer so no
network is needed.
"""

from __future__ import annotations

import pytest

from simf.io import wcl_api
from simf.io.wcl_api import WCLFight, WCLReport, fetch_own_cast_times

PUMMEL = 6552
TANK_ACTOR_ID = 75


def _report_fight():
    # report starts at absolute 1,000,000 ms; fight is [0, 100_000] ms relative.
    report = WCLReport(code="ABC", start_time_ms=1_000_000, fights=[])
    fight = WCLFight(id=8, name="Test", start_time_ms=0, end_time_ms=100_000, key_level=18)
    return report, fight


def _gql_returning(events, capture=None):
    def fake_gql(token, query, variables):
        if capture is not None:
            capture["query"] = query
            capture["variables"] = variables
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}

    return fake_gql


def test_fetch_own_cast_times_real_payload_shape(monkeypatch):
    """Pinned to the exact payload observed live 2026-07-03 against public
    report ExampleCode7777777 fight 8 — the real tank (actor 75) casting Pummel."""
    real_events = [
        {
            "timestamp": 7559248,
            "type": "cast",
            "sourceID": 75,
            "targetID": 109,
            "targetInstance": 2,
            "abilityGameID": 6552,
            "fight": 8,
            "sourceMarker": 2,
            "targetMarker": 7,
        },
        {
            "timestamp": 7609630,
            "type": "cast",
            "sourceID": 75,
            "targetID": 109,
            "targetInstance": 3,
            "abilityGameID": 6552,
            "fight": 8,
        },
    ]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(real_events))
    report, fight = _report_fight()
    times = fetch_own_cast_times(report, fight, TANK_ACTOR_ID, PUMMEL, "tok")
    # absolute seconds = (report_start_ms + timestamp_ms) / 1000
    assert times == pytest.approx((8559.248, 8609.630))


def test_fetch_own_cast_times_empty(monkeypatch):
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning([]))
    report, fight = _report_fight()
    assert fetch_own_cast_times(report, fight, TANK_ACTOR_ID, PUMMEL, "tok") == ()


def test_fetch_own_cast_times_paginates(monkeypatch):
    calls = {"n": 0}

    def fake_gql(token, query, variables):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "reportData": {
                    "report": {
                        "events": {
                            "data": [{"timestamp": 1000}],
                            "nextPageTimestamp": 50_000,
                        }
                    }
                }
            }
        return {
            "reportData": {
                "report": {"events": {"data": [{"timestamp": 60_000}], "nextPageTimestamp": None}}
            }
        }

    monkeypatch.setattr(wcl_api, "_gql", fake_gql)
    report, fight = _report_fight()
    times = fetch_own_cast_times(report, fight, TANK_ACTOR_ID, PUMMEL, "tok")
    assert times == (1001.0, 1060.0)
    assert calls["n"] == 2


def test_fetch_own_cast_times_query_pins(monkeypatch):
    """Regression pin: dropping sourceID or abilityID would silently start
    crediting a different player's or a different ability's casts."""
    cap = {}
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning([], capture=cap))
    report, fight = _report_fight()
    fetch_own_cast_times(report, fight, TANK_ACTOR_ID, PUMMEL, "tok")
    q = cap["query"]
    assert "dataType: Casts" in q
    assert "sourceID: $sid" in q
    assert "abilityID: $aid" in q
    assert "$aid: Float!" in q  # WCL types abilityID as Float, not Int
    assert cap["variables"]["sid"] == TANK_ACTOR_ID
    assert cap["variables"]["aid"] == float(PUMMEL)
