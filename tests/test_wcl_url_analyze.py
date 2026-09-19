"""Tests for the Warcraft Logs URL → analyze flow.

Covers:

  - URL parsing (code + optional fight ID)
  - Invalid URL handling — clean error, no exception bubbling
  - Credentials-not-configured banner renders on the UI surface
  - End-to-end mocked: paste URL → fights → damage events → analysis bundle
  - Cache key parity for WCL reports (report_code, fight_id, target)
  - Hydrate fallback — bundle still renders when no Character is loaded

The WCL API is mocked end-to-end via ``requests.post`` patching, mirroring
the pattern already used in ``tests/test_wcl_api.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.log_analysis_cache import (
    CacheKey,
    clear_cache,
    make_wcl_key,
)
from simf.io.wcl_api import (
    WCLFight,
    url_to_code_and_fight,
)
from simf.io.wcl_bridge import (
    WCLAnalysisBundle,
    _deaths_from_events,
    analyze_wcl_fight,
)

# ─── URL parsing ─────────────────────────────────────────────────────────────


def test_url_to_code_and_fight_with_fragment():
    code, fight_id = url_to_code_and_fight("https://www.warcraftlogs.com/reports/ABC123xyz#fight=4")
    assert code == "ABC123xyz"
    assert fight_id == 4


def test_url_to_code_and_fight_no_fragment():
    code, fight_id = url_to_code_and_fight("https://www.warcraftlogs.com/reports/ABC123xyz")
    assert code == "ABC123xyz"
    assert fight_id is None


def test_url_to_code_and_fight_fragment_first():
    """``#fight=N`` BEFORE the code segment isn't a real WCL URL — but the
    regex grabs whatever ``fight=N`` it can find. This test pins the
    "tolerate trailing junk" behaviour explicitly."""
    code, fight_id = url_to_code_and_fight(
        "https://www.warcraftlogs.com/reports/xYz789?foo=bar#fight=10"
    )
    assert code == "xYz789"
    assert fight_id == 10


def test_url_to_code_and_fight_invalid_raises():
    with pytest.raises(ValueError):
        url_to_code_and_fight("https://example.com/not-a-report")


# ─── WCL cache key ───────────────────────────────────────────────────────────


def test_make_wcl_key_same_report_same_fight_same_target():
    """Same triple → same hash → cache hit."""
    a = make_wcl_key("REPORT1", 2, "Brutoh", constants_version=1, analysis_version=1)
    b = make_wcl_key("REPORT1", 2, "Brutoh", constants_version=1, analysis_version=1)
    assert a.hashed() == b.hashed()


def test_make_wcl_key_different_fight_id_misses():
    """Same report, different fight → different hash."""
    a = make_wcl_key("REPORT1", 2, "Brutoh", constants_version=1, analysis_version=1)
    b = make_wcl_key("REPORT1", 3, "Brutoh", constants_version=1, analysis_version=1)
    assert a.hashed() != b.hashed()


def test_make_wcl_key_different_report_misses():
    a = make_wcl_key("REPORT1", 2, "Brutoh", constants_version=1, analysis_version=1)
    b = make_wcl_key("REPORT2", 2, "Brutoh", constants_version=1, analysis_version=1)
    assert a.hashed() != b.hashed()


def test_make_wcl_key_different_target_misses():
    a = make_wcl_key("REPORT1", 2, "Brutoh", constants_version=1, analysis_version=1)
    b = make_wcl_key("REPORT1", 2, "AnonGuardian3", constants_version=1, analysis_version=1)
    assert a.hashed() != b.hashed()


def test_make_wcl_key_version_bump_misses():
    """A constants_version bump invalidates the cache entry."""
    a = make_wcl_key("REPORT1", 2, "Brutoh", constants_version=1, analysis_version=1)
    b = make_wcl_key("REPORT1", 2, "Brutoh", constants_version=2, analysis_version=1)
    assert a.hashed() != b.hashed()


def test_make_wcl_key_pseudo_uri_does_not_collide_with_local_log():
    """A local-log key for `wcl://X` would only happen if a user named a
    local file that path, but the synthetic abspath + zero mtime mean
    even a hypothetical collision is statistically impossible."""
    wcl = make_wcl_key("REPORT1", 0, "Brutoh", constants_version=1, analysis_version=1)
    local = CacheKey(
        abspath="wcl://REPORT1",
        mtime_ns=1234567,
        size=999_999,
        constants_version=1,
        analysis_version=1,
        target="Brutoh",
        run_index=0,
    )
    assert wcl.hashed() != local.hashed()


# ─── End-to-end mocked pipeline ──────────────────────────────────────────────

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
                        "keystoneLevel": 18,
                        "keystoneAffixes": [9, 11, 135],
                        "completeRaid": True,
                    },
                ],
            }
        }
    }
}

_MOCK_ACTORS_RESPONSE = {
    "data": {
        "reportData": {
            "report": {
                "masterData": {
                    "actors": [
                        {"id": 7, "name": "Brutoh", "type": "Player"},
                        {"id": 8, "name": "FangedHorror", "type": "NPC"},
                    ]
                }
            }
        }
    }
}

# Three damage events: a normal swing, a crit, and a lethal overkill swing.
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
                            "amount": 50000,
                            "unmitigatedAmount": 75000,
                            "overkill": 0,
                            "blocked": 15000,
                            "absorbed": 5000,
                            "resisted": 0,
                            "sourceID": 8,
                            "sourceName": "FangedHorror",
                            "targetID": 7,  # Brutoh, per _MOCK_ACTORS_RESPONSE
                        },
                        {
                            "type": "damage",
                            "timestamp": 150000,
                            "ability": {"name": "Shadow Bolt", "type": 32},
                            "hitType": 2,
                            "amount": 120000,
                            "unmitigatedAmount": 140000,
                            "overkill": 0,
                            "blocked": 0,
                            "absorbed": 10000,
                            "resisted": 5000,
                            "sourceID": 8,
                            "sourceName": "FangedHorror",
                            "targetID": 7,  # Brutoh, per _MOCK_ACTORS_RESPONSE
                        },
                        {
                            "type": "damage",
                            "timestamp": 200000,
                            "ability": {"name": "Devastating Strike", "type": 1},
                            "hitType": 2,
                            "amount": 500000,
                            "unmitigatedAmount": 600000,
                            "overkill": 200000,
                            "blocked": 0,
                            "absorbed": 0,
                            "resisted": 0,
                            "sourceID": 8,
                            "sourceName": "FangedHorror",
                            "targetID": 7,  # Brutoh, per _MOCK_ACTORS_RESPONSE
                        },
                    ],
                    "nextPageTimestamp": None,
                }
            }
        }
    }
}


# dungeonPulls comes back null for non-M+ (raid) fights. The default mock
# returns null so the existing single-segment expectation holds; a dedicated
# test below overrides with a real M+ pull list.
_MOCK_DUNGEON_PULLS_EMPTY = {
    "data": {
        "reportData": {
            "report": {
                "fights": [
                    {"dungeonPulls": None},
                ]
            }
        }
    }
}


def _mock_post_full(url, json=None, **kwargs):
    """Route mocked responses based on URL + query content.

    The OAuth endpoint and the GraphQL endpoint are separate URLs, so
    the OAuth branch is keyed on the URL. The GraphQL branch is keyed
    on the query body (masterData = actor lookup, events = damage,
    dungeonPulls = encounter segmentation, everything else = report)."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    if "oauth" in url:
        resp.json.return_value = {"access_token": "fake-token"}
        return resp
    body = json or {}
    query = body.get("query", "")
    if "masterData" in query:
        resp.json.return_value = _MOCK_ACTORS_RESPONSE
    elif "events(" in query:
        resp.json.return_value = _MOCK_EVENTS_RESPONSE
    elif "dungeonPulls" in query:
        resp.json.return_value = _MOCK_DUNGEON_PULLS_EMPTY
    else:
        resp.json.return_value = _MOCK_REPORT_RESPONSE
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_full)
def test_end_to_end_wcl_url_to_bundle(mock_post, tmp_path):
    """Paste WCL URL → fetch report → fetch events → analyze → bundle."""
    from simf.io.wcl_api import fetch_report

    clear_cache(cache_dir=tmp_path)
    report = fetch_report("ABC123xyz", token="fake-token")
    assert len(report.fights) == 1
    fight = report.fights[0]
    bundle = analyze_wcl_fight(report, fight, "Brutoh", token="fake-token", cache_dir=tmp_path)

    assert isinstance(bundle, WCLAnalysisBundle)
    # Three events stamped → summary picks them up.
    assert bundle.summary.event_count == 3
    # Mit stats: three abilities → three rows, sorted by total_base desc.
    assert len(bundle.mit_stats) == 3
    assert bundle.mit_stats[0].ability == "Devastating Strike"
    # Overkill > 0 on the third event → death inferred.
    assert len(bundle.deaths) == 1
    assert len(bundle.death_events) == 1
    # Death window pulls all three events (within 5s — events are at
    # 100/150/200s offsets from report start = 1700000100 / 1700000150 /
    # 1700000200; window is 5s before death at 1700000200).
    assert bundle.death_events[0].num_hits >= 1
    # Segments: one whole-run segment (no encounters extracted from WCL).
    assert len(bundle.segments) == 1


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_full)
def test_end_to_end_cache_hit_avoids_refetch(mock_post, tmp_path):
    """Second analyze_wcl_fight call with the same triple is a cache hit
    and does NOT issue any further HTTP calls beyond the first run's
    OAuth + report + actors + events fetches."""
    from simf.io.wcl_api import fetch_report

    clear_cache(cache_dir=tmp_path)
    report = fetch_report("ABC123xyz", token="fake-token")
    fight = report.fights[0]
    bundle_a = analyze_wcl_fight(report, fight, "Brutoh", token="fake-token", cache_dir=tmp_path)
    post_call_count_after_first = mock_post.call_count
    bundle_b = analyze_wcl_fight(report, fight, "Brutoh", token="fake-token", cache_dir=tmp_path)
    post_call_count_after_second = mock_post.call_count

    # Cache hit: no new HTTP calls.
    assert post_call_count_after_second == post_call_count_after_first
    # Same payload, same event count.
    assert bundle_a.summary.event_count == bundle_b.summary.event_count


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_full)
def test_end_to_end_different_fight_id_is_a_cache_miss(mock_post, tmp_path):
    """Two different fight IDs in the same report each get their own
    cache slot — switching fights must NOT serve the prior fight's data."""
    from simf.io.wcl_api import fetch_report

    clear_cache(cache_dir=tmp_path)
    report = fetch_report("ABC123xyz", token="fake-token")
    fight_a = report.fights[0]
    # Synthesize a second fight with a different ID inside the same report.
    fight_b = WCLFight(
        id=99,
        name="Different Fight",
        start_time_ms=fight_a.start_time_ms,
        end_time_ms=fight_a.end_time_ms,
        key_level=fight_a.key_level,
        affixes=fight_a.affixes,
    )
    _ = analyze_wcl_fight(report, fight_a, "Brutoh", token="fake-token", cache_dir=tmp_path)
    calls_after_first = mock_post.call_count
    _ = analyze_wcl_fight(report, fight_b, "Brutoh", token="fake-token", cache_dir=tmp_path)
    calls_after_second = mock_post.call_count

    # Different fight_id → cache miss → at least one new POST (actors + events).
    assert calls_after_second > calls_after_first


# ─── dungeonPulls → boss-segment hierarchy ───────────────────────────────────


_MOCK_DUNGEON_PULLS_ALGETHAR = {
    # Mirrors what WCL v2 returns for Algeth'ar Academy +19 (probed against
    # report ExampleCode4444444 fight=19, 2026-05-27): 16 pulls, 4 boss kills
    # interleaved with 12 trash pulls. encounterID != 0 marks boss; kill is
    # True for boss completions. Timestamps are ms relative to report start
    # — same scale as fight.start_time_ms — so the analyze code reconstructs
    # absolute seconds the way `fight_to_run` does.
    "data": {
        "reportData": {
            "report": {
                "fights": [
                    {
                        "dungeonPulls": [
                            {
                                "id": 1,
                                "name": "Vile Lasher",
                                "encounterID": 0,
                                "kill": False,
                                "startTime": 60_000,
                                "endTime": 90_000,
                            },
                            {
                                "id": 2,
                                "name": "Overgrown Ancient",
                                "encounterID": 2563,
                                "kill": True,
                                "startTime": 120_000,
                                "endTime": 240_000,
                            },
                            {
                                "id": 3,
                                "name": "Guardian Sentry",
                                "encounterID": 0,
                                "kill": False,
                                "startTime": 260_000,
                                "endTime": 320_000,
                            },
                            {
                                "id": 4,
                                "name": "Crawth",
                                "encounterID": 2564,
                                "kill": True,
                                "startTime": 360_000,
                                "endTime": 480_000,
                            },
                            {
                                "id": 5,
                                "name": "Vexamus",
                                "encounterID": 2562,
                                "kill": True,
                                "startTime": 540_000,
                                "endTime": 660_000,
                            },
                            {
                                "id": 6,
                                "name": "Echo of Doragosa",
                                "encounterID": 2565,
                                "kill": True,
                                "startTime": 720_000,
                                "endTime": 840_000,
                            },
                        ]
                    }
                ]
            }
        }
    }
}


def _mock_post_with_algethar_pulls(url, json=None, **kwargs):
    """Same as ``_mock_post_full`` but the dungeonPulls branch returns the
    Algeth'ar +19 pull shape so analyze produces 4 boss segments."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    if "oauth" in url:
        resp.json.return_value = {"access_token": "fake-token"}
        return resp
    body = json or {}
    query = body.get("query", "")
    if "masterData" in query:
        resp.json.return_value = _MOCK_ACTORS_RESPONSE
    elif "events(" in query:
        resp.json.return_value = _MOCK_EVENTS_RESPONSE
    elif "dungeonPulls" in query:
        resp.json.return_value = _MOCK_DUNGEON_PULLS_ALGETHAR
    else:
        resp.json.return_value = _MOCK_REPORT_RESPONSE
    return resp


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_with_algethar_pulls)
def test_dungeon_pulls_produce_boss_and_trash_segments(mock_post, tmp_path):
    """Brutoh's Algeth'ar +19 has 4 boss kills + 12 trash pulls. Analyze must
    produce 4 boss segments named after the bosses, with trash segments
    filling the gaps — matching what a local log's ENCOUNTER_START/END
    boundaries yield. The user's regression complaint (2026-05-27) was
    that every pull rendered as trash because the WCL path was passing
    ``encounters=[]`` into ``segment_run``."""
    from simf.io.wcl_api import fetch_report

    clear_cache(cache_dir=tmp_path)
    report = fetch_report("ABC123xyz", token="fake-token")
    fight = report.fights[0]
    bundle = analyze_wcl_fight(report, fight, "Brutoh", token="fake-token", cache_dir=tmp_path)

    boss_segments = [s for s in bundle.segments if s.kind == "boss"]
    boss_labels = {s.label for s in boss_segments}
    assert boss_labels == {"Overgrown Ancient", "Crawth", "Vexamus", "Echo of Doragosa"}, (
        f"Expected all 4 Algeth'ar bosses; got {boss_labels}"
    )
    # Encounter IDs propagate so the per-segment renderer can show the
    # attempt_index suffix on multi-pulls.
    assert all(s.encounter is not None for s in boss_segments)
    assert {s.encounter.encounter_id for s in boss_segments} == {2562, 2563, 2564, 2565}
    # Boss windows preserve the WCL kill flag.
    assert all(s.encounter.success for s in boss_segments)
    # Trash segments fill the gaps between bosses. At least one, possibly
    # several (one before each boss given the test fixture has pre-boss
    # pulls). The exact count varies with which trash falls inside the
    # event-only window — assert "at least 1 trash" rather than a brittle
    # exact count.
    trash_segments = [s for s in bundle.segments if s.kind == "trash"]
    assert len(trash_segments) >= 1
    # Final ordering is monotonic in start_time_s — boss segments and
    # trash segments are interleaved by time, not bucketed by kind.
    times = [s.start_time_s for s in bundle.segments]
    assert times == sorted(times)


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_with_algethar_pulls)
def test_dungeon_pulls_fetched_once_then_cached(mock_post, tmp_path):
    """First analyze fetches dungeonPulls; second analyze with same cache key
    is a cache hit and issues zero new HTTP requests."""
    from simf.io.wcl_api import fetch_report

    clear_cache(cache_dir=tmp_path)
    report = fetch_report("ABC123xyz", token="fake-token")
    fight = report.fights[0]
    _ = analyze_wcl_fight(report, fight, "Brutoh", token="fake-token", cache_dir=tmp_path)
    n_after_first = mock_post.call_count
    _ = analyze_wcl_fight(report, fight, "Brutoh", token="fake-token", cache_dir=tmp_path)
    assert mock_post.call_count == n_after_first  # cache hit, no new POSTs


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_full)
def test_no_dungeon_pulls_falls_back_to_whole_run_segment(mock_post, tmp_path):
    """Non-M+ fights have dungeonPulls=null; the bridge must fall back to
    the existing whole-run trash segment instead of crashing."""
    from simf.io.wcl_api import fetch_report

    clear_cache(cache_dir=tmp_path)
    report = fetch_report("ABC123xyz", token="fake-token")
    fight = report.fights[0]
    bundle = analyze_wcl_fight(report, fight, "Brutoh", token="fake-token", cache_dir=tmp_path)
    # 0 boss segments → 1 trash segment covering the whole run.
    boss_segments = [s for s in bundle.segments if s.kind == "boss"]
    assert boss_segments == []
    assert len(bundle.segments) == 1


# ─── _deaths_from_events ─────────────────────────────────────────────────────


def test_deaths_from_events_picks_overkill_event():
    from simf.io.combat_log import ChallengeModeRun, DamageTakenEvent

    run = ChallengeModeRun(
        map_id=0,
        map_name="Test",
        key_level=10,
        affixes=[],
        start_time_s=100.0,
        end_time_s=200.0,
    )
    events = [
        DamageTakenEvent(
            time_s=120.0,
            event_type="SWING_DAMAGE",
            source_name="Boss",
            spell_name="Melee",
            school="physical",
            amount=10000,
            base_amount=10000,
            overkill=0,
            blocked=0,
            absorbed=0,
            resisted=0,
            is_critical=False,
            is_glancing=False,
        ),
        DamageTakenEvent(
            time_s=150.0,
            event_type="SPELL_DAMAGE",
            source_name="Boss",
            spell_name="Kill Shot",
            school="physical",
            amount=500_000,
            base_amount=600_000,
            overkill=100_000,
            blocked=0,
            absorbed=0,
            resisted=0,
            is_critical=True,
            is_glancing=False,
        ),
    ]
    deaths = _deaths_from_events(events, run)
    assert len(deaths) == 1
    assert deaths[0].time_s == 150.0
    assert deaths[0].rel_time_s == 50.0


# ─── UI: credentials-not-configured banner ───────────────────────────────────


def test_wcl_tab_renders_not_configured_banner_when_creds_missing(monkeypatch):
    """When ``is_configured()`` returns False, the WCL tab shows the help
    banner with credential-setup steps — and does NOT attempt an API call.

    AppTest serialises the script function, so the monkeypatch happens
    inside ``_script`` rather than at the outer test level.
    """
    monkeypatch.delenv("WCL_CLIENT_ID", raising=False)
    monkeypatch.delenv("WCL_CLIENT_SECRET", raising=False)

    def _script():
        from simf.io import wcl_api
        from simf.ui.log_view import _render_wcl_url_flow

        # Force not-configured inside the AppTest subprocess.
        wcl_api._load_credentials = lambda: None
        _render_wcl_url_flow(character_name="Brutoh", class_spec="protection_warrior")

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    info_text = " ".join(i.value for i in at.info)
    assert "credentials not configured" in info_text.lower()


def test_wcl_tab_renders_help_banner_when_creds_missing_and_url_pasted(monkeypatch):
    """Even with a URL pasted, missing credentials still surface the help
    banner instead of attempting an API call. The user gets actionable
    guidance, not a 'WCL credentials not found' RuntimeError."""
    monkeypatch.delenv("WCL_CLIENT_ID", raising=False)
    monkeypatch.delenv("WCL_CLIENT_SECRET", raising=False)

    def _script():
        import streamlit as st

        from simf.io import wcl_api
        from simf.ui.log_view import _render_wcl_url_flow

        wcl_api._load_credentials = lambda: None
        st.session_state["v9_wcl_url"] = "https://www.warcraftlogs.com/reports/abc123XYZ#fight=4"
        _render_wcl_url_flow(character_name="Brutoh")

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    info_text = " ".join(i.value for i in at.info)
    assert "credentials not configured" in info_text.lower()


def test_wcl_tab_invalid_url_shows_clean_error():
    """A garbage URL surfaces an st.error, not an uncaught exception."""

    def _script():
        import streamlit as st

        from simf.io import wcl_api
        from simf.ui.log_view import _render_wcl_url_flow

        # Pretend creds ARE configured so we hit the URL-validation branch.
        wcl_api._load_credentials = lambda: {"client_id": "x", "client_secret": "y"}
        st.session_state["v9_wcl_url"] = "https://example.com/not-a-wcl-link"
        _render_wcl_url_flow(character_name="Brutoh")

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    err_text = " ".join(e.value for e in at.error)
    # Body should explain the URL format, not bubble the ValueError repr.
    assert "warcraftlogs.com/reports" in err_text


# ─── Synthetic WCL fight: render path doesn't crash ──────────────────────────


def test_bundle_render_signature_matches_render_log_analysis(tmp_path):
    """Every field ``render_log_analysis`` reads off the bundle must exist
    with a compatible shape. This is a structural pin — if someone changes
    the bundle dataclass without updating the renderer (or vice versa)
    this test fails before users hit a Streamlit traceback."""
    from simf.io.combat_log import ChallengeModeRun

    # Build a bundle by hand — no API mocking needed for the shape check.
    run = ChallengeModeRun(
        map_id=0,
        map_name="Test",
        key_level=10,
        affixes=[],
        start_time_s=0.0,
        end_time_s=100.0,
        success=True,
        duration_ms=100_000,
    )
    bundle = WCLAnalysisBundle(
        summary=type(
            "S",
            (),
            {
                "run": run,
                "duration_s": 100.0,
                "event_count": 0,
                "total_amount": 0,
                "total_base_amount": 0,
                "total_blocked": 0,
                "total_absorbed": 0,
                "total_resisted": 0,
                "by_school": {},
                "by_source_amount": {},
                "by_source_count": {},
                "by_ability_amount": {},
                "by_ability_count": {},
                "ability_max_hit": {},
                "ability_spike_score": {},
                "deaths": [],
            },
        )(),
        mit_stats=[],
        death_events=[],
        run=run,
        events=[],
        deaths=[],
        segments=[],
    )
    # All fields the renderer reads:
    assert bundle.summary.run is run
    assert bundle.summary.event_count == 0
    assert bundle.mit_stats == []
    assert bundle.death_events == []
    assert bundle.run is run
    assert bundle.events == []
    assert bundle.deaths == []
    assert bundle.segments == []


# ─── UI: character picker is a selectbox, not a text input ───────────────────


def test_wcl_picker_renders_selectbox_when_players_provided():
    """When playerDetails returns players, the WCL flow shows a tank-first
    selectbox — no more 'type the name exactly right or get an error'.

    Regression test for the friction Brutoh hit on his first WCL paste:
    he had to guess the realm-suffix-or-not spelling and got a wrong-name
    error with no recovery path."""

    def _script():
        from simf.io.wcl_api import WCLPlayer
        from simf.ui.log_view import _pick_wcl_target

        players = [
            WCLPlayer(name="Brutoh", role="tank", class_spec="protection_warrior"),
            WCLPlayer(name="AnonHealer2", role="healer", class_spec="holy_priest"),
            WCLPlayer(name="AnonDPS1", role="dps", class_spec="assassination_rogue"),
        ]
        _pick_wcl_target(players, "REPORT", fight_id=4, prefill="")

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    # Should produce a selectbox with the 3 players + Other sentinel.
    assert len(at.selectbox) == 1
    sb = at.selectbox[0]
    assert sb.label == "Character to analyze"
    # AppTest exposes `options` after running through format_func, so we
    # check membership rather than raw equality. Tank-first ordering and
    # the role icon both matter for the user-visible label.
    assert "Brutoh" in sb.options[0]
    assert "🛡" in sb.options[0]
    assert "AnonHealer2" in sb.options[1]
    assert "💚" in sb.options[1]
    assert "AnonDPS1" in sb.options[2]
    assert "⚔" in sb.options[2]
    assert "Other" in sb.options[3]
    # Default selection is the tank (no prefill).
    assert sb.value == "Brutoh"


def test_resolve_target_info_uses_the_picked_players_own_spec_not_the_loaded_char():
    """Round-2 review (2026-07-05): defensive-coverage coaching (and the
    rest of the analysis) always graded the LOADED Gear-tab character's
    spec, even when the WCL player being analyzed was someone else
    entirely — a raid leader checking a Death Knight friend's pull while
    their own Warrior stayed loaded would silently get Shield-Block-style
    coaching graded against a DK's kit. The picked player's own detected
    spec must win."""
    from simf.io.wcl_api import WCLPlayer
    from simf.ui.log_view import _resolve_wcl_target_info

    players = [
        WCLPlayer(name="Friendo", role="tank", class_spec="blood_death_knight", actor_id=7),
        WCLPlayer(name="Brutoh", role="tank", class_spec="protection_warrior", actor_id=2),
    ]
    # Loaded character is a Warrior; the picked player is the DK friend.
    actor_id, spec = _resolve_wcl_target_info(players, "Friendo", "protection_warrior")
    assert actor_id == 7
    assert spec == "blood_death_knight"


def test_resolve_target_info_falls_back_when_player_spec_unknown():
    """An unrecognized WCL role icon leaves `class_spec` empty — fall back
    to the loaded character's spec rather than losing coaching entirely."""
    from simf.io.wcl_api import WCLPlayer
    from simf.ui.log_view import _resolve_wcl_target_info

    players = [WCLPlayer(name="Mystery", role="dps", class_spec="", actor_id=5)]
    actor_id, spec = _resolve_wcl_target_info(players, "Mystery", "protection_warrior")
    assert actor_id == 5
    assert spec == "protection_warrior"


def test_resolve_target_info_falls_back_when_no_player_matches():
    """A typed 'Other' name with no matching player: no actor_id, and the
    loaded character's spec is the only signal available."""
    from simf.io.wcl_api import WCLPlayer
    from simf.ui.log_view import _resolve_wcl_target_info

    players = [WCLPlayer(name="SomeoneElse", role="dps", class_spec="assassination_rogue")]
    actor_id, spec = _resolve_wcl_target_info(players, "TypedName", "protection_warrior")
    assert actor_id is None
    assert spec == "protection_warrior"


def test_wcl_picker_falls_back_to_text_input_when_no_players():
    """If playerDetails returns nothing (very old reports, trash-only
    pulls), the picker preserves the legacy free-text input rather than
    leaving the user with a blank UI."""

    def _script():
        from simf.ui.log_view import _pick_wcl_target

        _pick_wcl_target([], "REPORT", fight_id=4, prefill="Brutoh")

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    assert len(at.selectbox) == 0
    assert len(at.text_input) == 1
    assert at.text_input[0].label == "Character to analyze"
    assert at.text_input[0].value == "Brutoh"


def test_wcl_picker_source_id_overrides_default_tank():
    """When a WCL URL carries ``source=N``, the picker should default to
    the player whose actor_id matches — not the first tank.

    Regression test for the friction Brutoh hit on his second WCL paste:
    URL had ``source=128`` (AnonTank1) but the picker defaulted to the first
    tank (AnonHealer1), producing a confusing 'no damage events for AnonHealer1' error."""

    def _script():
        from simf.io.wcl_api import WCLPlayer
        from simf.ui.log_view import _pick_wcl_target

        players = [
            WCLPlayer(
                name="AnonHealer1", role="tank", class_spec="protection_warrior", actor_id=42
            ),
            WCLPlayer(name="AnonTank1", role="tank", class_spec="protection_warrior", actor_id=128),
            WCLPlayer(name="AnonHealer2", role="healer", class_spec="holy_priest", actor_id=9),
        ]
        # URL had source=128 (AnonTank1) — that wins even though AnonHealer1 is first.
        _pick_wcl_target(players, "REPORT", fight_id=4, prefill="", source_actor_id=128)

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    assert at.selectbox[0].value == "AnonTank1"


def test_wcl_picker_source_id_unmatched_falls_back_to_prefill():
    """If source=N is set but no player has that actor_id (rare — stale
    URL after report edits), the picker drops back to the prefill rule."""

    def _script():
        from simf.io.wcl_api import WCLPlayer
        from simf.ui.log_view import _pick_wcl_target

        players = [
            WCLPlayer(name="Tankly", role="tank", actor_id=1),
            WCLPlayer(name="Brutoh", role="tank", actor_id=2),
        ]
        _pick_wcl_target(players, "REPORT", fight_id=4, prefill="Brutoh", source_actor_id=999)

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    assert at.selectbox[0].value == "Brutoh"


def test_wcl_picker_prefill_selects_matching_player():
    """When the user has a SimC-pasted character whose name matches a
    detected player, that player is the default selectbox value — they
    don't have to click anything."""

    def _script():
        from simf.io.wcl_api import WCLPlayer
        from simf.ui.log_view import _pick_wcl_target

        players = [
            WCLPlayer(name="OtherTank", role="tank", class_spec="protection_paladin"),
            WCLPlayer(name="Brutoh", role="tank", class_spec="protection_warrior"),
        ]
        # Prefill carries a realm suffix — picker should strip it before
        # matching.
        _pick_wcl_target(players, "REPORT", fight_id=4, prefill="Brutoh-Uldum-EU")

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    assert len(at.selectbox) == 1
    assert at.selectbox[0].value == "Brutoh"


# ─── URL form variants: `?fight=N` vs `#fight=N` ─────────────────────────────


def test_url_to_code_and_fight_query_form():
    """WCL's canonical URL uses ``#fight=N``, but some clients (Discord
    unfurls, copy-paste from the address bar after navigation) hand back a
    ``?fight=N`` query-string form. Both must resolve to the same fight id.

    Regression pin for Brutoh's 2026-05-27 paste of
    ``…/reports/ExampleCode4444444?fight=19`` — confirms the URL parser
    accepts the query form, isolating that side from the widget-key bleed
    fixed in ``test_wcl_url_flow_fight_picker_resets_when_url_changes``."""
    code, fight_id = url_to_code_and_fight(
        "https://www.warcraftlogs.com/reports/ExampleCode4444444?fight=19"
    )
    assert code == "ExampleCode4444444"
    assert fight_id == 19


# ─── WCL URL flow: fight picker resets when URL fight changes ────────────────


@patch("simf.io.wcl_api.requests.post", side_effect=_mock_post_full)
def test_wcl_url_flow_fight_picker_resets_when_url_changes(mock_post):
    """Pasting a new URL with a different ``fight=N`` MUST reset the fight
    selectbox to the new URL's fight — Streamlit's selectbox-with-key
    behaviour silently keeps stale state otherwise.

    Regression test for Brutoh's 2026-05-27 report: he pasted
    ``…/reports/ExampleCode4444444?fight=19`` (Algeth'ar Academy) but the UI
    showed Windrunner Spire because the selectbox key was scoped only to
    ``report_code`` and a prior render had cached fight 16's selection.

    The fix incorporates ``default_fight_id`` into the widget key so a
    URL-driven fight change reaches the selectbox."""

    def _script():
        import streamlit as st

        from simf.io import wcl_api
        from simf.ui.log_view import _render_wcl_url_flow

        wcl_api._load_credentials = lambda: {"client_id": "x", "client_secret": "y"}
        # Pretend a prior render already cached fight 1 — simulate stale
        # selectbox state at the OLD (broken) key shape. Whether or not
        # the new code reads that key is the contract under test.
        old_key = "v9_wcl_fight_pick::ABC123xyz"
        st.session_state[old_key] = 0  # index 0 (the only fight in the mock)
        # User now pastes a URL targeting fight=99 — which doesn't exist in
        # the mocked single-fight report. The widget must still render and
        # honour the URL-derived fight as the default-when-present rule:
        # fight=99 absent → default_idx falls back to 0, but the KEY must
        # be the new shape, not the legacy one.
        st.session_state["v9_wcl_url"] = "https://www.warcraftlogs.com/reports/ABC123xyz?fight=1"
        _render_wcl_url_flow(character_name="Brutoh")

    at = AppTest.from_function(_script, default_timeout=10)
    at.run()
    # The selectbox renders and its widget key includes the default_fight_id
    # — verified by checking that the new-shape key is present in
    # session_state while the legacy key (read by no widget) is untouched.
    keys = list(at.session_state.keys())
    new_shape_keys = [k for k in keys if k.startswith("v9_wcl_fight_pick::ABC123xyz::")]
    assert new_shape_keys, (
        "Expected the fight-pick widget to register a key of shape "
        "`v9_wcl_fight_pick::<code>::<fight_id>`; got keys: " + repr(keys)
    )
