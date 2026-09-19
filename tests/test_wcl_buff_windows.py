"""Tests for the WCL buff-window plumbing that feeds window-gated mitigation.

`fetch_buff_windows` (window-building from applybuff/removebuff events) and
`adapt_events`' per-event `active_buffs` stamping are value-independent — they
decide *when* a buff was up, which is what a window-gated layer (Vengeance
Metamorphosis' armor multiplier, Painbringer) reads. Mock the GraphQL layer so
no network is needed.
"""

from __future__ import annotations

from simf.io import wcl_api
from simf.io.combat_log import DamageTakenEvent
from simf.io.wcl_api import (
    WCLFight,
    WCLReport,
    _map_event,
    _spawn_key,
    fetch_buff_windows,
    fetch_source_debuff_windows,
)
from simf.io.wcl_replay import adapt_events

META = 187827
PAINBRINGER = 212988
OTHER = 999999


def _report_fight():
    # report starts at absolute 1,000,000 ms; fight is [0, 100_000] ms relative
    # → absolute fight window [1000.0s, 1100.0s].
    report = WCLReport(code="ABC", start_time_ms=1_000_000, fights=[])
    fight = WCLFight(id=1, name="Test", start_time_ms=0, end_time_ms=100_000, key_level=18)
    return report, fight


def _buff(type_, ts, guid):
    return {"type": type_, "timestamp": ts, "ability": {"guid": guid, "name": str(guid)}}


def test_fetch_buff_windows_builds_intervals(monkeypatch):
    events = [
        _buff("applybuff", 10_000, META),  # abs 1010
        _buff("removebuff", 30_000, META),  # abs 1030 → (1010, 1030)
        _buff("removebuff", 5_000, PAINBRINGER),  # up at pull, no applybuff → (1000, 1005)
        _buff("applybuff", 90_000, PAINBRINGER),  # abs 1090
        # no removebuff for this one → closed at fight end 1100 → (1090, 1100)
        _buff("applybuff", 50_000, OTHER),  # filtered out by ability_ids
        _buff("removebuff", 60_000, OTHER),
    ]

    def fake_gql(token, query, variables, cache_dir=None):
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}

    monkeypatch.setattr(wcl_api, "_gql", fake_gql)
    report, fight = _report_fight()
    wins = fetch_buff_windows(report, fight, 42, "tok", ability_ids={META, PAINBRINGER})

    assert wins[META] == [(1010.0, 1030.0)]
    assert sorted(wins[PAINBRINGER]) == [(1000.0, 1005.0), (1090.0, 1100.0)]
    assert OTHER not in wins  # ability filter dropped it


def test_fetch_buff_windows_refresh_keeps_open_start(monkeypatch):
    events = [
        _buff("applybuff", 10_000, META),  # 1010
        _buff("refreshbuff", 20_000, META),  # refresh does NOT open a new window
        _buff("removebuff", 30_000, META),  # 1030 → single (1010, 1030)
    ]

    def fake_gql(token, query, variables, cache_dir=None):
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}

    monkeypatch.setattr(wcl_api, "_gql", fake_gql)
    report, fight = _report_fight()
    wins = fetch_buff_windows(report, fight, 42, "tok", ability_ids={META})
    assert wins[META] == [(1010.0, 1030.0)]


def test_fetch_buff_windows_threads_cache_dir_to_gql(monkeypatch):
    """``fetch_buff_windows`` didn't accept ``cache_dir`` at all until
    2026-08-31 — the same class of bug ``_fetch_actor_id`` had (fixed
    2026-08-30) and every other WCL-fetch function in this module already
    avoided. Every window-gated-mitigation replay (``wcl_to_replay_data``
    calls this whenever ``buff_ability_ids`` is set, which every cross-player
    validation run does) always hit the live API here regardless of
    ``--wcl-cache-dir`` — a real contributor to a 2026-08-31 WCL rate-limit
    exhaustion that blocked 2 of 6 specs' validation runs mid-session (see
    docs/validation/s2_cross_spec_keylevel_check_round2_2026_08_31.md)."""
    calls: list[dict | None] = []

    def fake_gql(token, query, variables, cache_dir=None):
        calls.append(cache_dir)
        return {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}

    monkeypatch.setattr(wcl_api, "_gql", fake_gql)
    report, fight = _report_fight()
    sentinel = object()
    fetch_buff_windows(report, fight, 42, "tok", ability_ids={META}, cache_dir=sentinel)
    assert calls == [sentinel]


def _dmg(time_s):
    return DamageTakenEvent(
        time_s=time_s,
        event_type="SPELL_DAMAGE",
        source_name="Boss",
        spell_name="Hit",
        school="physical",
        amount=100,
        base_amount=200,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
    )


def test_adapt_events_stamps_active_buffs():
    # buff windows in ABSOLUTE seconds (matches DamageTakenEvent.time_s)
    buff_windows = {META: [(1010.0, 1030.0)], PAINBRINGER: [(1000.0, 1100.0)]}
    raw = [_dmg(1005.0), _dmg(1020.0), _dmg(1040.0)]  # outside Meta, inside Meta, after Meta
    events, _agg = adapt_events(
        raw, target_name="Tank", run_start_time_s=1000.0, buff_windows=buff_windows
    )
    assert events[0].active_buffs == frozenset({PAINBRINGER})  # Painbringer only
    assert events[1].active_buffs == frozenset({META, PAINBRINGER})  # both up
    assert events[2].active_buffs == frozenset({PAINBRINGER})  # Meta expired


def test_adapt_events_no_windows_leaves_active_buffs_empty():
    events, _agg = adapt_events([_dmg(1005.0)], target_name="Tank", run_start_time_s=1000.0)
    assert events[0].active_buffs == frozenset()


# ─── fetch_source_debuff_windows: tank-applied enemy debuff (Demo Shout) ──────

DEMO_SHOUT = 1160
TANK = 2  # tank actor id


def _debuff(type_, ts, *, tid, tinst=None, src=TANK, guid=DEMO_SHOUT):
    """A WCL Debuffs event (useActorIDs:false shape): source/target objects +
    a sibling targetInstance."""
    ev = {
        "type": type_,
        "timestamp": ts,
        "source": {"id": src, "name": "Brutoh", "type": "Warrior"},
        "target": {"id": tid, "name": "Mob", "type": "NPC"},
        "ability": {"guid": guid, "name": "Demoralizing Shout"},
    }
    if tinst is not None:
        ev["targetInstance"] = tinst
    return ev


def _gql_returning(events, capture=None):
    def fake_gql(token, query, variables):
        if capture is not None:
            capture["query"] = query
            capture["variables"] = variables
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}

    return fake_gql


def test_fetch_source_debuff_windows_builds_per_spawn(monkeypatch):
    # Two spawns of the same actor id (7), distinguished by instance.
    events = [
        _debuff("applydebuff", 10_000, tid=7, tinst=1),  # 1010
        _debuff("removedebuff", 18_000, tid=7, tinst=1),  # 1018 → (1010,1018)
        _debuff("applydebuff", 12_000, tid=7, tinst=3),  # 1012
        _debuff("removedebuff", 20_000, tid=7, tinst=3),  # 1020 → (1012,1020)
    ]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    wins = fetch_source_debuff_windows(report, fight, TANK, "tok", DEMO_SHOUT, duration_s=8.0)
    assert wins["7:1"] == [(1010.0, 1018.0)]
    assert wins["7:3"] == [(1012.0, 1020.0)]


def test_fetch_source_debuff_windows_filters_by_source(monkeypatch):
    """A debuff applied by someone OTHER than the tank is not credited."""
    events = [
        _debuff("applydebuff", 10_000, tid=7, tinst=1, src=99),  # not the tank
        _debuff("removedebuff", 18_000, tid=7, tinst=1, src=99),
    ]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    wins = fetch_source_debuff_windows(report, fight, TANK, "tok", DEMO_SHOUT, duration_s=8.0)
    assert wins == {}


def test_fetch_source_debuff_windows_duration_caps_unclosed(monkeypatch):
    """A mob that dies without a removedebuff closes at open + duration_s."""
    events = [_debuff("applydebuff", 50_000, tid=7, tinst=1)]  # 1050, never removed
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    wins = fetch_source_debuff_windows(report, fight, TANK, "tok", DEMO_SHOUT, duration_s=8.0)
    assert wins["7:1"] == [(1050.0, 1058.0)]  # capped at 1050+8, not fight end 1100


def test_fetch_source_debuff_windows_skips_remove_without_open(monkeypatch):
    """A removedebuff with no matching apply never fabricates a window."""
    events = [_debuff("removedebuff", 30_000, tid=7, tinst=1)]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    wins = fetch_source_debuff_windows(report, fight, TANK, "tok", DEMO_SHOUT, duration_s=8.0)
    assert wins == {}


def test_fetch_source_debuff_windows_refresh_keeps_open_start(monkeypatch):
    events = [
        _debuff("applydebuff", 10_000, tid=7, tinst=1),  # 1010
        _debuff("refreshdebuff", 14_000, tid=7, tinst=1),  # refresh, no new window
        _debuff("removedebuff", 20_000, tid=7, tinst=1),  # 1020 → single (1010,1020)
    ]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    wins = fetch_source_debuff_windows(report, fight, TANK, "tok", DEMO_SHOUT, duration_s=8.0)
    assert wins["7:1"] == [(1010.0, 1020.0)]


def test_fetch_source_debuff_windows_query_pins(monkeypatch):
    """Regression pin on the empirically-verified filter combo (a future edit
    that drops one breaks live Demo Shout coverage)."""
    cap = {}
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning([], capture=cap))
    report, fight = _report_fight()
    fetch_source_debuff_windows(report, fight, TANK, "tok", DEMO_SHOUT, duration_s=8.0)
    q = cap["query"]
    assert "dataType: Debuffs" in q
    assert "hostilityType: Enemies" in q  # debuffs ON enemies, not the default Friendlies
    assert "abilityID: $aid" in q
    assert "sourceID" not in q  # broken for aura dataTypes — filtered client-side
    # abilityID is typed Float server-side (an Int! variable is REJECTED — the
    # one detail invisible to anything but a live run). Pin both the declaration
    # and that a float is actually passed, so a Float!->Int! revert fails CI
    # instead of silently breaking live Demo Shout coverage.
    assert "$aid: Float!" in q
    assert isinstance(cap["variables"]["aid"], float)
    assert cap["variables"]["aid"] == DEMO_SHOUT


def test_spawn_key_matches_between_damage_source_and_debuff_target(monkeypatch):
    """THE invariant: the key _map_event stamps on a hit's source_guid equals
    the key fetch_source_debuff_windows uses for the same spawn — so the join
    lines up. Same actor id + instance on both sides → same string."""
    # damage from actor 7, instance 3
    raw_dmg = {
        "type": "damage",
        "timestamp": 0,
        "ability": {"name": "Slam", "type": 1, "guid": 5},
        "source": {"id": 7, "name": "Mob", "type": "NPC", "guid": 232071},
        "sourceInstance": 3,
        "target": {"id": 2},
        "amount": 1,
        "unmitigatedAmount": 1,
    }
    mapped = _map_event(raw_dmg, report_start_ms=0)
    # debuff on the SAME spawn (actor 7, instance 3)
    events = [
        _debuff("applydebuff", 0, tid=7, tinst=3),
        _debuff("removedebuff", 8_000, tid=7, tinst=3),
    ]
    monkeypatch.setattr(wcl_api, "_gql", _gql_returning(events))
    report, fight = _report_fight()
    wins = fetch_source_debuff_windows(report, fight, TANK, "tok", DEMO_SHOUT, duration_s=8.0)
    assert mapped.source_guid == "7:3"
    assert "7:3" in wins  # the hit's source_guid is a key in the debuff windows


def test_spawn_key_defaults_missing_instance_consistently():
    """A missing instance defaults to 1 on BOTH sides so unique actors / the
    first instance still match."""
    assert _spawn_key(7, None) == "7:1"
    assert _spawn_key(7, 3) == "7:3"
    assert _spawn_key(None, 3) == ""  # no actor id → empty (matches nothing)
