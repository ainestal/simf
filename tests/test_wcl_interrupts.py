"""Tests for the WCL ``Interrupts``-dataType fetcher.

``fetch_interrupted_spell_ids`` is the WCL analog of
``combat_log.parse_interrupted_spell_ids`` — party-wide evidence of which
spell_ids were proven interruptible in a fight, feeding the same
``interruptible`` flag on ``core.coaching.HitCoverage`` the local-log path
already sets (see ``docs/validation/interruptible_cast_coverage_lever_2026_07_03.md``).

The exact WCL field name for "the spell that got interrupted" was confirmed
against a real live payload on 2026-07-03 (public rankings report
``ExampleCode7777777`` fight 8, Algeth'ar Academy +22, fetched via WCL's own
public zone rankings page since the API's ``characterRankings`` deliberately
redacts report codes): the real field is the flat ``extraAbilityGameID``, not
the ``stoppedAbility`` shape WCL's scripting-API docs suggested. That guess
is kept as a defensive fallback — see ``test_real_interrupt_event_shape_*``
below for the pinned real-world payload. Mock the GraphQL layer so no network
is needed.
"""

from __future__ import annotations

from simf.io import wcl_api
from simf.io.wcl_api import (
    WCLFight,
    WCLReport,
    _extract_stopped_spell_id,
    fetch_interrupted_spell_ids,
)


def _report_fight():
    # report starts at absolute 1,000,000 ms; fight is [0, 100_000] ms relative.
    report = WCLReport(code="ABC", start_time_ms=1_000_000, fights=[])
    fight = WCLFight(id=1, name="Test", start_time_ms=0, end_time_ms=100_000, key_level=18)
    return report, fight


def _gql_returning(events, capture=None):
    def fake_gql(token, query, variables):
        if capture is not None:
            capture["query"] = query
            capture["variables"] = variables
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}

    return fake_gql


# ─── _extract_stopped_spell_id: every candidate field shape ──────────────────


def test_extract_stopped_ability_nested_dict():
    assert _extract_stopped_spell_id({"stoppedAbility": {"guid": 123, "name": "Icy Blast"}}) == 123


def test_extract_stopped_ability_flat_value():
    assert _extract_stopped_spell_id({"stoppedAbility": 123}) == 123


def test_extract_extra_ability_game_id_fallback():
    assert _extract_stopped_spell_id({"extraAbilityGameID": 456}) == 456


def test_extract_extra_ability_nested_dict_fallback():
    assert _extract_stopped_spell_id({"extraAbility": {"guid": 789}}) == 789


def test_extract_returns_none_for_unrecognized_shape():
    assert _extract_stopped_spell_id({"type": "interrupt", "sourceID": 7}) is None


def test_extract_returns_none_for_garbage_values():
    assert _extract_stopped_spell_id({"stoppedAbility": {"guid": "not-a-number"}}) is None
    assert _extract_stopped_spell_id({"stoppedAbility": {"guid": None}}) is None


def test_extract_prefers_extra_ability_game_id_over_fallbacks():
    """extraAbilityGameID is the CONFIRMED real field (see module docstring)
    — it must win over the stoppedAbility/extraAbility guesses, not the
    other way around."""
    ev = {"stoppedAbility": {"guid": 1}, "extraAbilityGameID": 2, "extraAbility": {"guid": 3}}
    assert _extract_stopped_spell_id(ev) == 2


def test_real_interrupt_event_shape_extracts_correctly():
    """Pinned to the exact payload observed live 2026-07-03 against public
    report ExampleCode7777777 fight 8 (Algeth'ar Academy +22) — a Death Knight's
    Mind Freeze (47528) interrupting spell 396640. If WCL ever changes this
    shape, this is the test that should catch it."""
    real_event = {
        "timestamp": 7487056,
        "type": "interrupt",
        "sourceID": 78,
        "targetID": 109,
        "targetInstance": 1,
        "abilityGameID": 47528,
        "fight": 8,
        "extraAbilityGameID": 396640,
    }
    assert _extract_stopped_spell_id(real_event) == 396640


# ─── fetch_interrupted_spell_ids: the fetch + pagination + dedup ─────────────


def test_fetch_interrupted_spell_ids_collects_and_dedups(monkeypatch):
    events = [
        {"type": "interrupt", "stoppedAbility": {"guid": 111}},
        {"type": "interrupt", "stoppedAbility": {"guid": 222}},
        {"type": "interrupt", "stoppedAbility": {"guid": 111}},  # dup, same spell twice
    ]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    ids = fetch_interrupted_spell_ids(report, fight, "tok")
    assert ids == frozenset({111, 222})


def test_fetch_interrupted_spell_ids_empty_fight():
    def fake_gql(token, query, variables):
        return {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}

    import simf.io.wcl_api as wcl_api_mod

    orig = wcl_api_mod._gql
    wcl_api_mod._gql = fake_gql
    try:
        report, fight = _report_fight()
        assert fetch_interrupted_spell_ids(report, fight, "tok") == frozenset()
    finally:
        wcl_api_mod._gql = orig


def test_fetch_interrupted_spell_ids_skips_unrecognized_events(monkeypatch):
    events = [
        {"type": "interrupt", "stoppedAbility": {"guid": 111}},
        {"type": "interrupt"},  # no evidence field at all — silently dropped
    ]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    assert fetch_interrupted_spell_ids(report, fight, "tok") == frozenset({111})


def test_fetch_interrupted_spell_ids_paginates(monkeypatch):
    """A second page (nextPageTimestamp inside the fight window) is fetched;
    a page ending at/after fight end stops the loop — same contract as the
    Buffs/Debuffs fetchers in this module."""
    calls = {"n": 0}

    def fake_gql(token, query, variables):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "reportData": {
                    "report": {
                        "events": {
                            "data": [{"stoppedAbility": {"guid": 111}}],
                            "nextPageTimestamp": 50_000,
                        }
                    }
                }
            }
        return {
            "reportData": {
                "report": {
                    "events": {
                        "data": [{"stoppedAbility": {"guid": 222}}],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }

    monkeypatch.setattr(wcl_api, "_gql", fake_gql)
    report, fight = _report_fight()
    ids = fetch_interrupted_spell_ids(report, fight, "tok")
    assert ids == frozenset({111, 222})
    assert calls["n"] == 2


def test_fetch_interrupted_spell_ids_query_pins(monkeypatch):
    """Regression pin: a future edit that drops the dataType or fightIDs scope
    would silently start crediting interrupts from the wrong fight/dataset."""
    cap = {}
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning([], capture=cap))
    report, fight = _report_fight()
    fetch_interrupted_spell_ids(report, fight, "tok")
    q = cap["query"]
    assert "dataType: Interrupts" in q
    assert "fightIDs: [$fightID]" in q
    assert cap["variables"]["code"] == "ABC"
    assert cap["variables"]["fightID"] == 1
