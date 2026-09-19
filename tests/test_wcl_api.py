"""Tests for the Warcraft Logs v2 GraphQL API client."""

from unittest.mock import MagicMock, patch

import pytest

from simf.io.wcl_api import (
    WCLFight,
    WCLRateLimit,
    WCLReport,
    _gql,
    _icon_to_class_spec_slug,
    _map_event,
    fetch_damage_events,
    fetch_player_details,
    fetch_rate_limit,
    fetch_report,
    fight_to_run,
    url_to_code,
    url_to_source_id,
)

# ─── url_to_code ─────────────────────────────────────────────────────────────


def test_url_to_code_standard():
    assert url_to_code("https://www.warcraftlogs.com/reports/ABC123xyz") == "ABC123xyz"


def test_url_to_code_with_fragment():
    assert url_to_code("https://www.warcraftlogs.com/reports/xYz789#fight=1") == "xYz789"


def test_url_to_code_bare_code():
    assert url_to_code("/reports/TestCode99") == "TestCode99"


def test_url_to_code_invalid():
    with pytest.raises(ValueError):
        url_to_code("https://www.warcraftlogs.com/rankings/character")


# ─── url_to_source_id ────────────────────────────────────────────────────────


def test_url_to_source_id_query_param():
    # The Discord-share form Brutoh pasted: ?fight=N&source=N
    assert url_to_source_id("https://www.warcraftlogs.com/reports/LvMK?fight=10&source=128") == 128


def test_url_to_source_id_fragment_form():
    assert url_to_source_id("https://www.warcraftlogs.com/reports/abc#fight=4&source=7") == 7


def test_url_to_source_id_absent():
    assert url_to_source_id("https://www.warcraftlogs.com/reports/abc#fight=4") is None
    assert url_to_source_id("") is None


# ─── _map_event ──────────────────────────────────────────────────────────────


def _make_raw_event(**overrides) -> dict:
    base = {
        "type": "damage",
        "timestamp": 5000,
        "ability": {"name": "Melee", "type": 1},
        "hitType": 1,
        "amount": 80000,
        "unmitigatedAmount": 100000,
        "overkill": 0,
        "blocked": 10000,
        "absorbed": 5000,
        "resisted": 0,
        "sourceID": 42,
        "sourceName": "BossA",
    }
    base.update(overrides)
    return base


def test_map_event_basic():
    ev = _map_event(_make_raw_event(), report_start_ms=1000000)
    assert ev is not None
    assert ev.amount == 80000
    assert ev.base_amount == 100000
    assert ev.blocked == 10000
    assert ev.absorbed == 5000
    assert ev.resisted == 0
    assert ev.source_name == "BossA"
    assert ev.event_type == "SWING_DAMAGE"
    assert ev.school == "physical"


def test_map_event_spell_type():
    ev = _map_event(
        _make_raw_event(
            ability={"name": "Shadow Bolt", "type": 32},
            hitType=1,
        ),
        report_start_ms=0,
    )
    assert ev is not None
    assert ev.event_type == "SPELL_DAMAGE"
    assert ev.school == "shadow"


def test_map_event_crit():
    ev = _map_event(_make_raw_event(hitType=2), report_start_ms=0)
    assert ev is not None
    assert ev.is_critical is True
    assert ev.is_glancing is False


def test_map_event_glancing():
    ev = _map_event(_make_raw_event(hitType=8), report_start_ms=0)
    assert ev is not None
    assert ev.is_glancing is True
    assert ev.is_critical is False


def test_map_event_non_damage_returns_none():
    raw = _make_raw_event()
    raw["type"] = "heal"
    assert _map_event(raw, report_start_ms=0) is None


def test_map_event_timestamp_offset():
    ev = _map_event(_make_raw_event(timestamp=10000), report_start_ms=2000000)
    assert ev is not None
    # (2000000 + 10000) / 1000 = 2010.0
    assert abs(ev.time_s - 2010.0) < 0.001


def test_map_event_fallback_to_amount_when_no_unmitigated():
    ev = _make_raw_event()
    del ev["unmitigatedAmount"]
    result = _map_event(ev, report_start_ms=0)
    assert result is not None
    assert result.base_amount == result.amount


# ─── fight_to_run ────────────────────────────────────────────────────────────


def test_fight_to_run_fields():
    fight = WCLFight(
        id=3,
        name="The Stonevault",
        start_time_ms=10000,
        end_time_ms=490000,
        key_level=12,
        affixes=[9, 11, 135],
        completed=True,
    )
    run = fight_to_run(fight, report_start_ms=1000000)
    assert run.map_name == "The Stonevault"
    assert run.key_level == 12
    assert run.affixes == [9, 11, 135]
    assert run.success is True
    # start_s = (1000000 + 10000) / 1000 = 1010.0
    assert abs(run.start_time_s - 1010.0) < 0.001
    # end_s = (1000000 + 490000) / 1000 = 1490.0
    assert abs(run.end_time_s - 1490.0) < 0.001


# ─── fetch_report (mocked HTTP) ──────────────────────────────────────────────

_MOCK_REPORT_RESPONSE = {
    "data": {
        "reportData": {
            "report": {
                "startTime": 1700000000000,
                "fights": [
                    {
                        "id": 1,
                        "name": "Ara-Kara, City of Echoes",
                        "startTime": 60000,
                        "endTime": 1800000,
                        "keystoneLevel": 10,
                        "keystoneAffixes": [9, 11, 135],
                        "completeRaid": True,
                    },
                    {
                        "id": 2,
                        "name": "The Stonevault",
                        "startTime": 2000000,
                        "endTime": 2500000,
                        "keystoneLevel": 11,
                        "keystoneAffixes": [9, 11, 135],
                        "completeRaid": False,
                    },
                ],
            }
        }
    }
}


def _mock_post(url, **kwargs):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    if "oauth" in url:
        resp.json.return_value = {"access_token": "fake-token"}
    else:
        resp.json.return_value = _MOCK_REPORT_RESPONSE
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post)
def test_fetch_report_basic(mock_post):
    report = fetch_report("TESTREPORT", token="fake-token")
    assert report.code == "TESTREPORT"
    assert report.start_time_ms == 1700000000000
    assert len(report.fights) == 2
    f = report.fights[0]
    assert f.name == "Ara-Kara, City of Echoes"
    assert f.key_level == 10
    assert f.affixes == [9, 11, 135]
    assert f.id == 1


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post)
def test_fetch_report_fight_count(mock_post):
    report = fetch_report("TESTREPORT", token="fake-token")
    assert len(report.fights) == 2
    assert report.fights[1].key_level == 11


# ─── _gql opt-in on-disk cache (2026-07-25) ──────────────────────────────────


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post)
def test_gql_without_cache_dir_hits_network_every_call(mock_post):
    """Default behaviour (cache_dir=None) is unchanged: every call is a real
    request, even for the identical query twice in a row."""
    q = "query { x }"
    _gql("fake-token", q)
    _gql("fake-token", q)
    assert mock_post.call_count == 2


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post)
def test_gql_with_cache_dir_hits_network_once(mock_post, tmp_path):
    """Second call with the identical (query, variables) is served from disk —
    no second network request."""
    q = "query($x: Int) { y(x: $x) }"
    r1 = _gql("fake-token", q, {"x": 1}, cache_dir=tmp_path)
    assert mock_post.call_count == 1
    r2 = _gql("fake-token", q, {"x": 1}, cache_dir=tmp_path)
    assert mock_post.call_count == 1  # no new request
    assert r1 == r2


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post)
def test_gql_cache_keys_differ_by_variables(mock_post, tmp_path):
    """Different variables must not collide on the same cache file — a cache
    keyed only on the query string would silently return the wrong fight's
    data for every subsequent distinct call."""
    q = "query($x: Int) { y(x: $x) }"
    _gql("fake-token", q, {"x": 1}, cache_dir=tmp_path)
    _gql("fake-token", q, {"x": 2}, cache_dir=tmp_path)
    assert mock_post.call_count == 2
    cached_files = list(tmp_path.glob("*.json"))
    assert len(cached_files) == 2


def test_gql_cache_never_stores_errors(tmp_path):
    """A GraphQL error response must not be cached — a transient failure
    (rate limit, timeout) caching itself would poison every future call with
    the same query/variables, permanently."""

    def _error_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"errors": [{"message": "boom"}]}
        return resp

    with (
        patch("simf.io.wcl_api.requests.post", side_effect=_error_post),
        pytest.raises(RuntimeError),
    ):
        _gql("fake-token", "query { x }", cache_dir=tmp_path)
    assert list(tmp_path.glob("*.json")) == []


# ─── rateLimitData (mocked HTTP) ─────────────────────────────────────────────


def _rate_limit_post(remaining_spent: float, limit: int = 3600):
    def _post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "data": {
                "rateLimitData": {
                    "limitPerHour": limit,
                    "pointsSpentThisHour": remaining_spent,
                    "pointsResetIn": 1234,
                }
            }
        }
        return resp

    return _post


@patch("simf.io.wcl_api.requests.post", side_effect=_rate_limit_post(120.0))
def test_fetch_rate_limit_parses_fields(mock_post):
    rl = fetch_rate_limit(token="fake-token")
    assert isinstance(rl, WCLRateLimit)
    assert rl.limit_per_hour == 3600
    assert rl.points_spent_this_hour == 120.0
    assert rl.points_reset_in == 1234
    assert rl.remaining == 3480.0


def test_rate_limit_has_headroom():
    assert WCLRateLimit(3600, 120.0, 100).has_headroom(0.10) is True
    # only 200 of 3600 left = 5.5% < 10% reserve → no headroom
    assert WCLRateLimit(3600, 3400.0, 100).has_headroom(0.10) is False
    # unknown/zero limit treated as unlimited → admit
    assert WCLRateLimit(0, 0.0, 0).has_headroom(0.10) is True


def test_rate_limit_remaining_never_negative():
    assert WCLRateLimit(3600, 5000.0, 0).remaining == 0.0


# ─── fetch_damage_events (mocked HTTP) ───────────────────────────────────────

_MOCK_ACTORS_RESPONSE = {
    "data": {
        "reportData": {
            "report": {
                "masterData": {
                    "actors": [
                        {"id": 7, "name": "Brutoh", "type": "Player"},
                        {"id": 8, "name": "BossEnemy", "type": "NPC"},
                    ]
                }
            }
        }
    }
}

_MOCK_EVENTS_RESPONSE = {
    "data": {
        "reportData": {
            "report": {
                "events": {
                    "data": [
                        {
                            "type": "damage",
                            "timestamp": 100000,
                            "ability": {"name": "Melee", "type": 1},
                            "hitType": 1,
                            "amount": 75000,
                            "unmitigatedAmount": 100000,
                            "overkill": 0,
                            "blocked": 20000,
                            "absorbed": 5000,
                            "resisted": 0,
                            "sourceID": 8,
                            "sourceName": "BossEnemy",
                            "targetID": 7,  # Brutoh, per _MOCK_ACTORS_RESPONSE
                        }
                    ],
                    "nextPageTimestamp": None,
                }
            }
        }
    }
}


def _mock_post_events(url, json=None, **kwargs):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    body = json or {}
    query = body.get("query", "")
    if "masterData" in query:
        resp.json.return_value = _MOCK_ACTORS_RESPONSE
    else:
        resp.json.return_value = _MOCK_EVENTS_RESPONSE
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_events)
def test_fetch_damage_events_returns_events(mock_post):
    report = WCLReport(code="TESTREPORT", start_time_ms=1700000000000, fights=[])
    fight = WCLFight(
        id=1,
        name="Test",
        start_time_ms=60000,
        end_time_ms=1860000,
        key_level=10,
        affixes=[9, 11],
    )
    events = fetch_damage_events(report, fight, "Brutoh", token="fake-token")
    assert len(events) == 1
    ev = events[0]
    assert ev.amount == 75000
    assert ev.base_amount == 100000
    assert ev.blocked == 20000
    assert ev.source_name == "BossEnemy"


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_events)
def test_fetch_damage_events_actor_not_found(mock_post):
    report = WCLReport(code="TESTREPORT", start_time_ms=0, fights=[])
    fight = WCLFight(
        id=1, name="Test", start_time_ms=0, end_time_ms=100000, key_level=10, affixes=[]
    )
    with pytest.raises(ValueError, match="not found"):
        fetch_damage_events(report, fight, "NoSuchChar", token="fake-token")


def _mock_only_events(url, json=None, **kwargs):
    """Mock that ONLY answers the events query — proves masterData was
    not hit because we pre-supplied the actor ID."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    body = json or {}
    query = body.get("query", "")
    if "events(" in query:
        resp.json.return_value = _MOCK_EVENTS_RESPONSE
        return resp
    raise AssertionError(f"Unexpected GraphQL query when actor ID was provided: {query[:80]}")


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_only_events)
def test_fetch_damage_events_skips_masterdata_when_actor_id_given(mock_post):
    """When the caller supplies target_actor_id, fetch_damage_events
    skips the masterData lookup entirely — fixes the realm-suffix mismatch
    that produced 'No damage-taken events for Somename' even though the
    player was in the fight."""
    report = WCLReport(code="TESTREPORT", start_time_ms=1700000000000, fights=[])
    fight = WCLFight(
        id=1,
        name="Test",
        start_time_ms=60000,
        end_time_ms=1860000,
        key_level=18,
        affixes=[],
    )
    events = fetch_damage_events(report, fight, "Somename", token="fake-token", target_actor_id=7)
    assert len(events) == 1
    # Exactly one POST: the events query. Zero masterData calls.
    assert mock_post.call_count == 1


# Match the realm-suffix case the live bug hit.
_MOCK_ACTORS_WITH_REALM = {
    "data": {
        "reportData": {
            "report": {
                "masterData": {
                    "actors": [
                        {"id": 42, "name": "Somename-Faketown", "type": "Player"},
                        {"id": 7, "name": "Bossy", "type": "NPC"},
                    ]
                }
            }
        }
    }
}


def _mock_actors_with_realm(url, json=None, **kwargs):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = _MOCK_ACTORS_WITH_REALM
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_actors_with_realm)
def test_fetch_actor_id_matches_bare_name_against_realm_suffixed_actor(mock_post):
    """If masterData returns ``Somename-Faketown`` but the picker handed
    us the bare name ``Somename``, the lookup should still match. The
    pre-fix code did an exact-equality comparison that silently failed."""
    from simf.io.wcl_api import _fetch_actor_id

    assert _fetch_actor_id("REPORT", "Somename", token="fake-token") == 42


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_actors_with_realm)
def test_fetch_actor_id_with_cache_dir_hits_network_once(mock_post, tmp_path):
    """``_fetch_actor_id`` didn't accept ``cache_dir`` at all until this test was
    added — every caller with a buff-window lookup (cross_player_validation.py's
    WCL replay path in particular) always hit the live API even when
    ``--wcl-cache-dir`` was supplied, because this was the one WCL-fetch
    function in the module that never threaded cache_dir through to ``_gql``.
    A report's masterData actor list never changes, so caching it is
    zero-staleness-risk, same as every other cached WCL call."""
    from simf.io.wcl_api import _fetch_actor_id

    r1 = _fetch_actor_id("REPORT", "Somename", token="fake-token", cache_dir=tmp_path)
    assert mock_post.call_count == 1
    r2 = _fetch_actor_id("REPORT", "Somename", token="fake-token", cache_dir=tmp_path)
    assert mock_post.call_count == 1  # no new request
    assert r1 == r2 == 42


def _capture_query(captured: list):
    """Return a `requests.post` side_effect that captures the events GraphQL
    body into ``captured`` so a test can inspect the query string."""

    def _mock(url, json=None, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        body = json or {}
        query = body.get("query", "")
        if "events(" in query:
            captured.append(query)
            resp.json.return_value = _MOCK_EVENTS_RESPONSE
            return resp
        resp.json.return_value = _MOCK_ACTORS_RESPONSE
        return resp

    return _mock


def test_fetch_damage_events_query_pins_critical_flags():
    """Regression pin for the WCL flag values verified by the 2026-05-26
    probe (see docs/validation/wcl_events_probe_2026_05_26.md). If any
    of these silently flip back to defaults, real-report analysis breaks:

      hostilityType: Friendlies  — without this, we'd get damage *enemies*
        took (i.e., damage party DEALT) instead of damage taken by the
        tank. Default IS Friendlies but we state it explicitly.
      NO targetID on the events query — WCL's targetID filter doesn't
        reliably restrict to the target; we filter client-side.
      useActorIDs: false — gives us ``source: {name, id}`` objects so we
        can show real source names instead of "from 2".
      useAbilityIDs: false — gives us ``ability: {name, type}`` so we
        can name spells instead of falling back to "auto-attack"."""
    captured: list[str] = []
    with patch("simf.io.wcl_api.requests.post", side_effect=_capture_query(captured)):
        report = WCLReport(code="X", start_time_ms=0, fights=[])
        fight = WCLFight(
            id=1,
            name="t",
            start_time_ms=0,
            end_time_ms=1_000_000,
            key_level=14,
            affixes=[],
        )
        fetch_damage_events(report, fight, "Brutoh", token="t", target_actor_id=7)
    assert captured, "events query was never issued"
    q = captured[0]
    assert "hostilityType: Friendlies" in q, (
        "hostilityType must be Friendlies — Enemies returns damage party *dealt*, not damage taken"
    )
    assert "targetID:" not in q, (
        "WCL's targetID filter on events doesn't restrict reliably; filter client-side"
    )
    assert "useActorIDs: false" in q
    assert "useAbilityIDs: false" in q


# ─── object-shape mapping (useActorIDs:false response) ───────────────────────


def test_map_event_object_shape_source_name_from_object():
    """With useActorIDs:false WCL returns ``source: {name, id, ...}``
    instead of flat sourceID/sourceName. _map_event must read the nested
    name, not fall through to "0"."""
    ev = {
        "type": "damage",
        "timestamp": 100,
        "ability": {"name": "Phial Toss", "type": 8, "guid": 423459},
        "source": {"name": "Fervent Apothecary", "id": 5, "guid": 241643, "type": "NPC"},
        "target": {"name": "Brutoh", "id": 2, "type": "Warrior"},
        "hitType": 1,
        "amount": 250000,
        "unmitigatedAmount": 345135,
    }
    result = _map_event(ev, report_start_ms=0)
    assert result is not None
    assert result.source_name == "Fervent Apothecary"
    assert result.spell_name == "Phial Toss"
    assert result.school == "nature"  # WCL school type 8 = nature
    assert result.amount == 250000
    assert result.base_amount == 345135


def test_map_event_object_shape_carries_spell_id_and_npc_id():
    """`ability.guid` is the in-game spell ID and `source.guid` is the
    in-game NPC ID — wiring these through unlocks the Wowhead-link
    renderer that the local-log path has been using all along."""
    ev = {
        "type": "damage",
        "timestamp": 0,
        "ability": {"name": "Phial Toss", "type": 8, "guid": 423459},
        "source": {"name": "Fervent Apothecary", "id": 5, "guid": 241643, "type": "NPC"},
        "target": {"id": 2},
        "hitType": 1,
        "amount": 1,
        "unmitigatedAmount": 1,
    }
    result = _map_event(ev, report_start_ms=0)
    assert result is not None
    assert result.spell_id == 423459
    assert result.source_npc_id == 241643


def test_map_event_player_source_does_not_set_npc_id():
    """Defensive: party-member friendly fire / pet damage shouldn't be
    Wowhead-linked as an NPC. Only types `NPC` / `Boss` carry an npc_id."""
    ev = {
        "type": "damage",
        "timestamp": 0,
        "ability": {"name": "Bug", "type": 1, "guid": 42},
        "source": {"name": "Pet", "id": 9, "guid": 9999, "type": "Pet"},
        "target": {"id": 2},
        "hitType": 1,
        "amount": 1,
        "unmitigatedAmount": 1,
    }
    result = _map_event(ev, report_start_ms=0)
    assert result is not None
    assert result.spell_id == 42
    assert result.source_npc_id is None


def test_event_target_matches_object_shape():
    """useActorIDs:false shape: target is an object with id."""
    from simf.io.wcl_api import _event_target_matches

    ev = {"target": {"id": 2, "name": "Brutoh"}}
    assert _event_target_matches(ev, 2) is True
    assert _event_target_matches(ev, 5) is False


def test_event_target_matches_legacy_shape():
    """Legacy flat-ID shape stays supported."""
    from simf.io.wcl_api import _event_target_matches

    ev = {"targetID": 7}
    assert _event_target_matches(ev, 7) is True
    assert _event_target_matches(ev, 8) is False


def test_fetch_damage_events_filters_by_target_client_side():
    """The probe found WCL's targetID filter on `events` doesn't restrict
    to the target. We fetch the whole party's damage-taken events and
    filter by `event.target.id == actor_id` locally."""
    mixed_response = {
        "data": {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            # Brutoh took a hit — keep
                            {
                                "type": "damage",
                                "timestamp": 100_000,
                                "ability": {"name": "Phial Toss", "type": 8},
                                "source": {"name": "Apothecary", "id": 5},
                                "target": {"name": "Brutoh", "id": 2},
                                "hitType": 1,
                                "amount": 1000,
                                "unmitigatedAmount": 1200,
                            },
                            # Healer took a hit — DROP
                            {
                                "type": "damage",
                                "timestamp": 100_500,
                                "ability": {"name": "Melee", "type": 1},
                                "source": {"name": "Apothecary", "id": 5},
                                "target": {"name": "Healy", "id": 4},
                                "hitType": 1,
                                "amount": 500,
                                "unmitigatedAmount": 600,
                            },
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
    }

    def _mock(url, json=None, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = mixed_response
        return resp

    with patch("simf.io.wcl_api.requests.post", side_effect=_mock):
        report = WCLReport(code="X", start_time_ms=0, fights=[])
        fight = WCLFight(
            id=1, name="t", start_time_ms=0, end_time_ms=1_000_000, key_level=15, affixes=[]
        )
        events = fetch_damage_events(report, fight, "Brutoh", token="t", target_actor_id=2)

    assert len(events) == 1
    assert events[0].source_name == "Apothecary"
    assert events[0].school == "nature"


# ─── _icon_to_class_spec_slug ────────────────────────────────────────────────


def test_icon_to_class_spec_slug_warrior_protection():
    assert _icon_to_class_spec_slug("Warrior-Protection") == "protection_warrior"


def test_icon_to_class_spec_slug_death_knight_blood():
    # "DeathKnight-Blood" is what WCL emits — the slug compresses to
    # one token per side. The display layer renders it as "Blood Deathknight"
    # which is good-enough for a picker label.
    assert _icon_to_class_spec_slug("DeathKnight-Blood") == "blood_deathknight"


def test_icon_to_class_spec_slug_empty_inputs():
    assert _icon_to_class_spec_slug("") == ""
    assert _icon_to_class_spec_slug("Warrior") == ""
    assert _icon_to_class_spec_slug("-Protection") == ""
    assert _icon_to_class_spec_slug("Warrior-") == ""


# ─── fetch_player_details (mocked HTTP) ──────────────────────────────────────

_MOCK_PLAYER_DETAILS_RESPONSE = {
    "data": {
        "reportData": {
            "report": {
                "playerDetails": {
                    "data": {
                        "playerDetails": {
                            "tanks": [
                                {
                                    "name": "Brutoh",
                                    "id": 7,
                                    "icon": "Warrior-Protection",
                                }
                            ],
                            "healers": [
                                {
                                    "name": "AnonHealer2",
                                    "id": 9,
                                    "icon": "Priest-Holy",
                                }
                            ],
                            "dps": [
                                {
                                    "name": "AnonDPS1",
                                    "id": 11,
                                    "icon": "Rogue-Assassination",
                                },
                                {
                                    "name": "AnonDPS2",
                                    "id": 12,
                                    "icon": "Druid-Balance",
                                },
                            ],
                        }
                    }
                }
            }
        }
    }
}

_MOCK_PLAYER_DETAILS_UNWRAPPED = {
    "data": {
        "reportData": {
            "report": {
                "playerDetails": {
                    "tanks": [{"name": "Solo", "id": 1, "icon": "Paladin-Protection"}],
                    "healers": [],
                    "dps": [],
                }
            }
        }
    }
}

_MOCK_PLAYER_DETAILS_EMPTY = {"data": {"reportData": {"report": {"playerDetails": None}}}}


def _mock_player_details(url, json=None, **kwargs):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = _MOCK_PLAYER_DETAILS_RESPONSE
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_player_details)
def test_fetch_player_details_groups_tank_first(mock_post):
    players = fetch_player_details("REPORT", fight_id=4, token="fake-token")
    # Tanks first, healers second, DPS last.
    assert [p.name for p in players] == ["Brutoh", "AnonHealer2", "AnonDPS1", "AnonDPS2"]
    assert [p.role for p in players] == ["tank", "healer", "dps", "dps"]


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_player_details)
def test_fetch_player_details_carries_class_spec(mock_post):
    players = fetch_player_details("REPORT", fight_id=4, token="fake-token")
    by_name = {p.name: p for p in players}
    assert by_name["Brutoh"].class_spec == "protection_warrior"
    assert by_name["AnonHealer2"].class_spec == "holy_priest"
    assert by_name["AnonDPS2"].class_spec == "balance_druid"


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_player_details)
def test_fetch_player_details_carries_actor_id(mock_post):
    # actor_id is what we use to match `source=N` URL fragments to the
    # right player in the picker.
    players = fetch_player_details("REPORT", fight_id=4, token="fake-token")
    by_name = {p.name: p for p in players}
    assert by_name["Brutoh"].actor_id == 7
    assert by_name["AnonHealer2"].actor_id == 9
    assert by_name["AnonDPS1"].actor_id == 11
    assert by_name["AnonDPS2"].actor_id == 12


def _mock_unwrapped(url, json=None, **kwargs):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = _MOCK_PLAYER_DETAILS_UNWRAPPED
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_unwrapped)
def test_fetch_player_details_handles_unwrapped_shape(mock_post):
    # WCL sometimes returns playerDetails as a bare object instead of the
    # ``{"data": {"playerDetails": {...}}}`` wrapper. Both must parse.
    players = fetch_player_details("REPORT", fight_id=1, token="fake-token")
    assert len(players) == 1
    assert players[0].name == "Solo"
    assert players[0].role == "tank"


def _mock_empty_details(url, json=None, **kwargs):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = _MOCK_PLAYER_DETAILS_EMPTY
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_empty_details)
def test_fetch_player_details_handles_missing_details(mock_post):
    # Old reports / trash-only pulls may return null. Callers fall back to
    # the free-text input in that case.
    assert fetch_player_details("REPORT", fight_id=1, token="fake-token") == []


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_player_details)
def test_fetch_player_details_with_cache_dir_hits_network_once(mock_post, tmp_path):
    """``fetch_player_details`` didn't accept ``cache_dir`` at all until this
    test was added (found 2026-08-31 alongside the same gap in
    ``fetch_buff_windows``) — every WCL discovery scan that lists a fight's
    players always hit the live API even with ``--wcl-cache-dir`` set. A
    fight's player roster never changes, so caching it is zero-staleness-risk,
    same as ``fetch_report``."""
    r1 = fetch_player_details("REPORT", fight_id=4, token="fake-token", cache_dir=tmp_path)
    assert mock_post.call_count == 1
    r2 = fetch_player_details("REPORT", fight_id=4, token="fake-token", cache_dir=tmp_path)
    assert mock_post.call_count == 1  # no new request
    assert [p.name for p in r1] == [p.name for p in r2]
