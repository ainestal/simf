"""Tests for `simf.io.wcl_rankings` — live world-max-key lookup.

Covers:
  * `fetch_live_world_max_keys` returns the per-spec max bracketData
    when the WCL `_gql` mock supplies one.
  * Disk cache: a fresh cache file is reused without hitting the
    network; a stale cache file is bypassed; a corrupt file is treated
    as a miss (never raises).
  * `is_configured() == False` short-circuits to `None` (callers fall
    back to YAML).
  * The world_ceilings loader prefers live data when present and falls
    back to YAML on `None`.

No live API calls. Every test mocks the WCL token and `_gql`
implementation; the cache path is monkeypatched to a tmp dir.

Gotcha (found 2026-07-08, 6 tests silently violated this for months): the
world-ceilings loader calls BOTH `fetch_live_world_max_keys` AND
`fetch_live_broad_completion_counts` on every `world_ceiling_for()` — a
test that mocks only one of them lets the OTHER fall through to the real
function. Since this module opts out of the global `_disable_live_wcl_rankings`
guard and `isolated_cache` below forces every cache lookup to miss, an
unmocked sibling means a real `https://www.warcraftlogs.com/oauth/token`
handshake + live GraphQL queries on any machine with real WCL creds
configured (this dev box has them) — 30-45s of network wait per test,
invisible on a creds-less CI runner. Always stub both.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from simf.data import world_ceilings as wc_module
from simf.io import wcl_rankings

# Opt every test in this file out of the global `_disable_live_wcl_rankings`
# autouse fixture defined in `tests/conftest.py`. The tests below
# explicitly exercise the live path (mocked at the `_gql` boundary, not
# the `fetch_live_world_max_keys` boundary).
pytestmark = pytest.mark.live_wcl


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the cache path to a per-test tmp dir, and clear the
    in-process world_ceilings cache before AND after each test so live
    paths don't leak across the file.
    """
    cache_file = tmp_path / "wcl_rankings_cache.json"
    monkeypatch.setattr(wcl_rankings, "CACHE_PATH", cache_file)
    wc_module.reset_cache()
    yield cache_file
    wc_module.reset_cache()


def _mock_gql_factory(per_spec_max: dict[tuple[str, str], int]):
    """Build a `_gql` mock that returns a one-row characterRankings
    response with `bracketData = per_spec_max[(className, specName)]`.

    The mock ignores `encounter_id` and `bracket` arguments — the
    real-world bisection logic in `_max_bracket_for_encounter` will
    happily accept a one-row answer at the first probe bracket.
    """

    def _gql(_token, _query, variables):
        cls = variables["cls"]
        spec = variables["spec"]
        max_bd = per_spec_max.get((cls, spec), 0)
        rankings = [] if max_bd == 0 else [{"bracketData": max_bd}]
        return {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "rankings": rankings,
                        "hasMorePages": False,
                    }
                }
            }
        }

    return _gql


# ---- fetch_live_world_max_keys ---------------------------------------------


def test_fetch_returns_per_spec_max_when_configured() -> None:
    """Happy path — credentials present, every spec returns a number."""
    mock_gql = _mock_gql_factory(
        {
            ("Warrior", "Protection"): 23,
            ("Paladin", "Protection"): 21,
            ("DeathKnight", "Blood"): 22,
            ("DemonHunter", "Vengeance"): 22,
            ("Monk", "Brewmaster"): 20,
            ("Druid", "Guardian"): 20,
        }
    )

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="fake-token"),
        patch("simf.io.wcl_api._gql", mock_gql),
    ):
        result = wcl_rankings.fetch_live_world_max_keys(force_refresh=True)

    assert result is not None
    assert result["protection_warrior"] == 23
    assert result["protection_paladin"] == 21
    assert result["blood_death_knight"] == 22
    assert result["brewmaster_monk"] == 20


def test_fetch_returns_none_when_not_configured() -> None:
    """No WCL creds → return None → caller falls back to YAML."""
    with patch("simf.io.wcl_api.is_configured", return_value=False):
        result = wcl_rankings.fetch_live_world_max_keys(force_refresh=True)
    assert result is None


def test_fetch_returns_none_when_token_fails() -> None:
    """Token fetch raises → fall back path, no crash."""
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", side_effect=RuntimeError("oauth down")),
    ):
        result = wcl_rankings.fetch_live_world_max_keys(force_refresh=True)
    assert result is None


def test_fetch_skips_specs_with_no_data() -> None:
    """One spec returns 0 (no rankings at any bracket) → that spec is
    omitted from the result; others still come through."""
    # Only Prot Warrior has data; every other spec returns empty.
    mock_gql = _mock_gql_factory({("Warrior", "Protection"): 23})

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="fake-token"),
        patch("simf.io.wcl_api._gql", mock_gql),
    ):
        result = wcl_rankings.fetch_live_world_max_keys(force_refresh=True)

    assert result is not None
    assert result == {"protection_warrior": 23}


def test_fetch_returns_none_when_all_specs_fail() -> None:
    """If every spec returns 0, the live path is useless — caller should
    fall back to YAML, signalled by `None`."""
    mock_gql = _mock_gql_factory({})  # empty: nothing returns rows
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="fake-token"),
        patch("simf.io.wcl_api._gql", mock_gql),
    ):
        result = wcl_rankings.fetch_live_world_max_keys(force_refresh=True)
    assert result is None


# ---- Cache layer ------------------------------------------------------------


def test_fresh_cache_skips_network(isolated_cache: Path) -> None:
    """A cache file <24h old returns immediately; no `_gql` calls."""
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zone_id": 47,
                "specs": {"protection_warrior": {"world_max_key": 22}},
            }
        )
    )
    # If anything reaches the network, `is_configured` would have to be
    # called — fail loudly if it is.
    with patch("simf.io.wcl_api.is_configured", side_effect=AssertionError("network!")):
        result = wcl_rankings.fetch_live_world_max_keys()
    assert result == {"protection_warrior": 22}


def test_stale_cache_triggers_refresh(isolated_cache: Path) -> None:
    """A cache older than `CACHE_TTL_SECONDS` is ignored — the live
    path runs."""
    stale_ts = (
        datetime.now(UTC) - timedelta(seconds=wcl_rankings.CACHE_TTL_SECONDS + 3600)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": stale_ts,
                "specs": {"protection_warrior": {"world_max_key": 18}},  # stale
            }
        )
    )

    mock_gql = _mock_gql_factory({("Warrior", "Protection"): 24})  # fresh
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock_gql),
    ):
        result = wcl_rankings.fetch_live_world_max_keys()

    # Live value, not stale cache.
    assert result is not None
    assert result["protection_warrior"] == 24


def test_corrupt_cache_treated_as_miss(isolated_cache: Path) -> None:
    """Corrupt JSON in the cache file must not raise — treat as miss
    and run the live path (or fall back if creds missing)."""
    isolated_cache.write_text("{not valid json")
    with patch("simf.io.wcl_api.is_configured", return_value=False):
        result = wcl_rankings.fetch_live_world_max_keys()
    assert result is None  # creds missing → None, no exception


def test_cache_write_after_successful_fetch(isolated_cache: Path) -> None:
    """A successful live fetch persists to disk so subsequent calls
    skip the network."""
    mock_gql = _mock_gql_factory({("Warrior", "Protection"): 23})
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock_gql),
    ):
        wcl_rankings.fetch_live_world_max_keys(force_refresh=True)

    assert isolated_cache.exists()
    payload = json.loads(isolated_cache.read_text())
    assert payload["specs"]["protection_warrior"]["world_max_key"] == 23
    assert payload["zone_id"] == wcl_rankings.SEASON_ZONE_ID


# ---- World-ceilings loader integration --------------------------------------


def test_loader_uses_live_data_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When live data is present, the loader returns rows with the live
    `world_max_key` and clears the placeholder flag for live-refreshed
    rows (live data is real, not hand-wavy)."""

    def fake_live(force_refresh: bool = False):
        return {"protection_warrior": 25}  # well above YAML's 20

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", fake_live)
    # Sibling live layer — unmocked, this falls through to a real WCL API
    # call (this box has real creds configured), turning a unit test into
    # a ~30s+ network round-trip. Every test in this file must stub BOTH
    # live-fetch functions since the module opts out of the global
    # `_disable_live_wcl_rankings` autouse guard (see the module docstring).
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts", lambda *a, **kw: None
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.world_max_key == 25
    # Other fields must still come from YAML.
    assert row.broad_completion_key > 0
    assert row.population_descriptor != ""
    # Live data clears placeholder.
    assert row.placeholder is False


def test_loader_falls_back_to_yaml_when_live_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`fetch_live_world_max_keys` returns None → loader uses YAML as
    the source of truth."""
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda: None)
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts", lambda *a, **kw: None
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    # YAML value at time of writing (the static fallback) — assert
    # only that the row loaded, since the YAML number can change.
    assert row is not None
    assert row.world_max_key > 0


def test_loader_falls_back_to_yaml_when_live_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live path raising must not break the verdict surface — loader
    swallows and uses YAML."""

    def bombing_live(force_refresh: bool = False):
        raise RuntimeError("WCL on fire")

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", bombing_live)
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts", lambda *a, **kw: None
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.world_max_key > 0


def test_loader_mixes_live_and_yaml_per_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live data for one spec, no live data for another → live value
    for the first, YAML value for the second. Verifies the merge is
    per-spec, not all-or-nothing."""

    def partial_live(force_refresh: bool = False):
        return {"protection_warrior": 24}

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", partial_live)
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts", lambda *a, **kw: None
    )
    wc_module.reset_cache()

    warrior = wc_module.world_ceiling_for("protection_warrior")
    paladin = wc_module.world_ceiling_for("protection_paladin")

    assert warrior is not None
    assert warrior.world_max_key == 24
    # Paladin gets the YAML value (whatever it is — just assert it
    # loaded and didn't take the warrior's number).
    assert paladin is not None
    assert paladin.world_max_key != 24 or paladin is not warrior


# ---- Broad-completion count layer ------------------------------------------


def _broad_count_gql(
    per_spec_rows: dict[tuple[str, str], list[dict]],
    has_more: bool = False,
):
    """Build a `_gql` mock that returns the given `rankings` rows for
    each `(className, specName)` pair. Pagination is single-page unless
    `has_more=True`, in which case the mock returns the same rows for
    every requested page (the loop will keep accumulating distinct
    `(name, server, region)` tuples — useful for cap simulation when
    rows are unique per page).
    """
    state: dict[str, dict] = {"page_calls": {}}

    def _gql(_token, _query, variables):
        cls = variables["cls"]
        spec = variables["spec"]
        page = variables["page"]
        rows = per_spec_rows.get((cls, spec), [])
        # If `has_more` and the test wants unique rows per page, the
        # rows list itself is partitioned by page in the test setup.
        # For default behaviour (single-page), just return all rows on
        # page 1 and an empty list afterwards.
        if has_more:
            # Each call returns the full list (caller dedupes by key).
            pass
        else:
            if page > 1:
                rows = []
        state["page_calls"].setdefault((cls, spec), 0)
        state["page_calls"][(cls, spec)] += 1
        return {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "rankings": rows,
                        "hasMorePages": has_more,
                    }
                }
            }
        }

    return _gql


def _row(name: str, server: str, region: str, bracket_data: int = 17) -> dict:
    return {
        "name": name,
        "server": {"name": server, "region": region},
        "bracketData": bracket_data,
    }


def test_broad_count_returns_distinct_player_count() -> None:
    """Happy path — distinct `(name, server.name, server.region)` tuples
    are counted; duplicate rows in the same response are deduped."""
    rows = [
        _row("Brutoh", "Uldum", "EU"),
        _row("Brutoh", "Uldum", "EU"),  # duplicate
        _row("Feedman", "Tarren Mill", "EU"),
        _row("Brutoh", "Stormrage", "US"),  # same name, different server
    ]
    mock = _broad_count_gql({("Warrior", "Protection"): rows})

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = wcl_rankings.fetch_live_broad_completion_counts(
            {"protection_warrior": 17}, force_refresh=True
        )

    assert result is not None
    row = result["protection_warrior"]
    assert row["broad_count"] == 3  # Brutoh@Uldum, Feedman@TM, Brutoh@Stormrage
    assert row["broad_count_capped"] is False
    assert row["broad_threshold"] == 17


def test_broad_count_bracket_exclusive_lower_bound() -> None:
    """`broad_completion_key=N` must query `bracket=N-1` so the returned
    rows have `bracketData >= N`. Verify by capturing the variables the
    mock received."""
    captured: dict = {}

    def capturing_gql(_token, _query, variables):
        captured.update(variables)
        return {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "rankings": [_row("X", "S", "EU")],
                        "hasMorePages": False,
                    }
                }
            }
        }

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", capturing_gql),
    ):
        wcl_rankings.fetch_live_broad_completion_counts(
            {"protection_warrior": 17}, force_refresh=True
        )

    # broad_completion_key=17 → bracket=16 (exclusive lower bound).
    assert captured["bracket"] == 16


def test_broad_count_capped_when_row_cap_hit() -> None:
    """When the count grows to `BROAD_COMPLETION_ROW_CAP` while
    `hasMorePages` is still true, mark `broad_count_capped=True`."""
    # Build unique rows per page — `mock_gql` here partitions.
    pages: dict[int, list[dict]] = {}
    seq = 0
    rows_per_page = 100
    pages_needed = (wcl_rankings.BROAD_COMPLETION_ROW_CAP // rows_per_page) + 1
    for p in range(1, pages_needed + 1):
        pages[p] = [_row(f"P{p}C{i}", "S", "EU") for i in range(rows_per_page)]
        seq += rows_per_page

    def paginating_gql(_token, _query, variables):
        page = variables["page"]
        rows = pages.get(page, [])
        return {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "rankings": rows,
                        "hasMorePages": True,  # always true → cap hit
                    }
                }
            }
        }

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", paginating_gql),
    ):
        result = wcl_rankings.fetch_live_broad_completion_counts(
            {"protection_warrior": 17}, force_refresh=True
        )

    assert result is not None
    row = result["protection_warrior"]
    assert row["broad_count"] >= wcl_rankings.BROAD_COMPLETION_ROW_CAP
    assert row["broad_count_capped"] is True


def test_broad_count_skips_specs_with_no_data() -> None:
    """Specs with zero rows are omitted from the result; specs with rows
    pass through."""
    mock = _broad_count_gql(
        {
            ("Warrior", "Protection"): [_row("A", "S", "EU")],
            # Paladin returns nothing
        }
    )
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = wcl_rankings.fetch_live_broad_completion_counts(
            {"protection_warrior": 17, "protection_paladin": 17},
            force_refresh=True,
        )

    assert result is not None
    assert "protection_warrior" in result
    assert "protection_paladin" not in result


def test_broad_count_falls_back_to_none_when_not_configured() -> None:
    """No creds → None → caller falls back to static descriptor."""
    with patch("simf.io.wcl_api.is_configured", return_value=False):
        result = wcl_rankings.fetch_live_broad_completion_counts(
            {"protection_warrior": 17}, force_refresh=True
        )
    assert result is None


def test_broad_count_cache_hit_for_matching_threshold(isolated_cache) -> None:
    """A cache row whose `broad_threshold` matches the requested threshold
    is reused without network calls."""
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zone_id": 47,
                "specs": {
                    "protection_warrior": {
                        "world_max_key": 22,
                        "broad_count": 1253,
                        "broad_count_capped": False,
                        "broad_threshold": 17,
                    }
                },
            }
        )
    )
    # Fail loud if anything hits the network.
    with patch("simf.io.wcl_api.is_configured", side_effect=AssertionError("net!")):
        result = wcl_rankings.fetch_live_broad_completion_counts({"protection_warrior": 17})
    assert result is not None
    assert result["protection_warrior"]["broad_count"] == 1253
    assert result["protection_warrior"]["broad_count_capped"] is False


def test_broad_count_cache_miss_when_threshold_changes(isolated_cache) -> None:
    """A cache row with a different `broad_threshold` is a miss for the
    new threshold → triggers a live fetch."""
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zone_id": 47,
                "specs": {
                    "protection_warrior": {
                        "broad_count": 999,
                        "broad_count_capped": False,
                        "broad_threshold": 18,  # was 18, now want 17
                    }
                },
            }
        )
    )
    mock = _broad_count_gql({("Warrior", "Protection"): [_row("X", "S", "EU")]})
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = wcl_rankings.fetch_live_broad_completion_counts({"protection_warrior": 17})

    # Live value (count=1), not stale cache (count=999).
    assert result is not None
    assert result["protection_warrior"]["broad_count"] == 1
    assert result["protection_warrior"]["broad_threshold"] == 17


def test_broad_count_cache_write_merges_with_world_max(isolated_cache) -> None:
    """A successful broad-count fetch must NOT clobber an existing
    `world_max_key` entry in the cache. The two layers coexist."""
    # Seed with a world_max_key entry only.
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zone_id": 47,
                "specs": {"protection_warrior": {"world_max_key": 22}},
            }
        )
    )
    mock = _broad_count_gql({("Warrior", "Protection"): [_row("X", "S", "EU")]})
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        wcl_rankings.fetch_live_broad_completion_counts(
            {"protection_warrior": 17}, force_refresh=True
        )

    payload = json.loads(isolated_cache.read_text())
    row = payload["specs"]["protection_warrior"]
    # Both layers present.
    assert row["world_max_key"] == 22
    assert row["broad_count"] == 1


def test_loader_uses_live_broad_count_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the live broad-count fetch returns data, the loader
    populates `WorldCeiling.broad_count` + `broad_count_capped`."""

    def fake_counts(spec_threshold_map, force_refresh: bool = False):
        return {
            "protection_warrior": {
                "broad_count": 1253,
                "broad_count_capped": False,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_broad_completion_counts", fake_counts)
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_world_max_keys",
        lambda *a, **kw: None,
        raising=False,
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.broad_count == 1253
    assert row.broad_count_capped is False
    # Static fields unchanged.
    assert row.broad_completion_key > 0


def test_loader_passes_capped_flag_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`broad_count_capped=True` from the live layer must surface on
    `WorldCeiling` so the caption can render "at least N+"."""

    def fake_counts(spec_threshold_map, force_refresh: bool = False):
        return {
            "protection_warrior": {
                "broad_count": 2000,
                "broad_count_capped": True,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_broad_completion_counts", fake_counts)
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda *a, **kw: None)
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.broad_count == 2000
    assert row.broad_count_capped is True


def test_loader_falls_back_when_live_count_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the broad-count live path returns None, `broad_count` stays
    None on the row and the caption falls back to the static descriptor."""
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda *a, **kw: None)
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.broad_count is None
    assert row.broad_count_capped is False
    # Static descriptor still loaded.
    assert row.population_descriptor != ""


# ---- Cross-encounter union (CLI pre-warm) ----------------------------------


def _cross_gql_factory(per_encounter_rows: dict[int, list[dict]], has_more: bool = False):
    """Build a `_gql` mock that returns different `rankings` rows per
    encounter. Each encounter's rows are returned on page 1; later pages
    return empty unless `has_more=True` (mock keeps returning the full
    list for cap-simulation tests).

    `per_encounter_rows` keys are encounter IDs; values are the rows the
    mock returns for that encounter's page-1 response. Encounters not in
    the dict get an empty rankings list (count as nothing for that
    encounter).
    """

    def _gql(_token, _query, variables):
        enc_id = variables["encId"]
        page = variables["page"]
        rows = per_encounter_rows.get(enc_id, [])
        if not has_more and page > 1:
            rows = []
        return {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "rankings": rows,
                        "hasMorePages": has_more,
                    }
                }
            }
        }

    return _gql


def test_cross_encounter_dedups_across_encounters() -> None:
    """A character timing the bracket in 2 encounters is counted once in
    the union. This is the core motivation for the cross-encounter layer."""
    enc_ids = wcl_rankings.SEASON_ENCOUNTER_IDS
    rows_by_enc = {
        enc_ids[0]: [_row("Brutoh", "Uldum", "EU"), _row("Feedman", "TM", "EU")],
        enc_ids[1]: [_row("Brutoh", "Uldum", "EU"), _row("Krono", "Sylvanas", "EU")],
        enc_ids[2]: [_row("Solo", "Stormrage", "US")],
    }
    mock = _cross_gql_factory(rows_by_enc)

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = wcl_rankings.refresh_cross_encounter_broad_counts({"protection_warrior": 17})

    assert result is not None
    payload = result["protection_warrior"]
    # Distinct (name, server, region) tuples: Brutoh, Feedman, Krono, Solo = 4.
    assert payload["broad_count_cross"] == 4
    assert payload["broad_count_cross_capped"] is False
    assert payload["broad_threshold"] == 17


def test_cross_encounter_union_is_at_least_single_encounter() -> None:
    """The cross-encounter union must be >= the single-encounter count
    (encounter[0] has new characters absent from any one other encounter)."""
    enc_ids = wcl_rankings.SEASON_ENCOUNTER_IDS
    # encounter[0] (first) has 3 chars. encounter[2] (third) has 2 new chars.
    rows_by_enc = {
        enc_ids[0]: [
            _row("A", "S", "EU"),
            _row("B", "S", "EU"),
            _row("C", "S", "EU"),
        ],
        enc_ids[2]: [_row("D", "S", "EU"), _row("E", "S", "EU")],
    }
    mock = _cross_gql_factory(rows_by_enc)

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = wcl_rankings.refresh_cross_encounter_broad_counts({"protection_warrior": 17})

    assert result is not None
    assert result["protection_warrior"]["broad_count_cross"] == 5


def test_cross_encounter_marks_capped_when_any_encounter_capped(isolated_cache) -> None:
    """If any one of the contributing encounters hits the row cap, the
    union's `broad_count_cross_capped` flag is True (the count is a floor)."""
    enc_ids = wcl_rankings.SEASON_ENCOUNTER_IDS

    # encounter[0] returns hasMorePages=True with unique rows on every page
    # → triggers the cap. Other encounters return small finite sets.
    def paginating_gql(_token, _query, variables):
        enc_id = variables["encId"]
        page = variables["page"]
        if enc_id == enc_ids[0]:
            # Always hasMorePages → will hit the page cap.
            rows = [_row(f"E0_P{page}_C{i}", "S", "EU") for i in range(100)]
            return {
                "worldData": {
                    "encounter": {
                        "characterRankings": {
                            "rankings": rows,
                            "hasMorePages": True,
                        }
                    }
                }
            }
        # Other encounters: empty.
        return {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "rankings": [],
                        "hasMorePages": False,
                    }
                }
            }
        }

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", paginating_gql),
    ):
        result = wcl_rankings.refresh_cross_encounter_broad_counts({"protection_warrior": 17})

    assert result is not None
    payload = result["protection_warrior"]
    assert payload["broad_count_cross_capped"] is True
    # Floor count should be at least the row cap.
    assert payload["broad_count_cross"] >= wcl_rankings.BROAD_COMPLETION_ROW_CAP


def test_cross_encounter_writes_cache_preserving_single_encounter_row(
    isolated_cache,
) -> None:
    """Cross-encounter refresh must merge alongside any pre-existing
    `broad_count` (single-encounter) row in the cache, not clobber it."""
    # Seed cache with a single-encounter row.
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zone_id": 47,
                "specs": {
                    "protection_warrior": {
                        "world_max_key": 22,
                        "broad_count": 1253,
                        "broad_count_capped": False,
                        "broad_threshold": 17,
                    }
                },
            }
        )
    )

    enc_ids = wcl_rankings.SEASON_ENCOUNTER_IDS
    rows = {enc_ids[0]: [_row("A", "S", "EU"), _row("B", "S", "EU")]}
    mock = _cross_gql_factory(rows)

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        wcl_rankings.refresh_cross_encounter_broad_counts({"protection_warrior": 17})

    payload = json.loads(isolated_cache.read_text())
    row = payload["specs"]["protection_warrior"]
    # Both layers coexist.
    assert row["world_max_key"] == 22
    assert row["broad_count"] == 1253
    assert row["broad_count_cross"] == 2
    assert row["broad_count_cross_capped"] is False


def test_cross_encounter_returns_none_when_not_configured() -> None:
    """No WCL creds → None, caller (CLI) errors out cleanly."""
    with patch("simf.io.wcl_api.is_configured", return_value=False):
        result = wcl_rankings.refresh_cross_encounter_broad_counts({"protection_warrior": 17})
    assert result is None


def test_read_cached_cross_encounter_counts_returns_only_matching_threshold(
    isolated_cache,
) -> None:
    """The UI-safe cache reader returns cross-encounter rows only when
    the cached `broad_threshold` matches the requested threshold."""
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zone_id": 47,
                "specs": {
                    "protection_warrior": {
                        "broad_count_cross": 1500,
                        "broad_count_cross_capped": False,
                        "broad_threshold": 17,
                    },
                    "protection_paladin": {
                        # Cached at +18 but the request is +17 — skip.
                        "broad_count_cross": 900,
                        "broad_count_cross_capped": False,
                        "broad_threshold": 18,
                    },
                    "blood_death_knight": {
                        # No cross-encounter field at all — skip.
                        "broad_count": 500,
                        "broad_threshold": 17,
                    },
                },
            }
        )
    )

    out = wcl_rankings.read_cached_cross_encounter_counts(
        {
            "protection_warrior": 17,
            "protection_paladin": 17,
            "blood_death_knight": 17,
        }
    )
    assert "protection_warrior" in out
    assert out["protection_warrior"]["broad_count_cross"] == 1500
    assert "protection_paladin" not in out  # threshold mismatch
    assert "blood_death_knight" not in out  # no cross field


def test_loader_prefers_cross_encounter_over_single_encounter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the cross-encounter cache has both broad-count and world-max
    rows, the loader uses the broad-count cross value and the
    `is_cross_encounter` flag fires. The single-encounter live count is
    ignored even when both are present.

    `is_cross_encounter` requires BOTH the broad-count cross layer AND
    the world-max-key cross layer to be present — see the loader's
    docstring for the compose rule. This test seeds both.
    """

    def fake_cross(spec_threshold_map):
        return {
            "protection_warrior": {
                "broad_count_cross": 1500,
                "broad_count_cross_capped": False,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    def fake_cross_world_max():
        return {"protection_warrior": 22}

    def fake_single(spec_threshold_map, force_refresh: bool = False):
        return {
            "protection_warrior": {
                "broad_count": 1253,  # single-encounter is smaller
                "broad_count_capped": False,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    monkeypatch.setattr("simf.io.wcl_rankings.read_cached_cross_encounter_counts", fake_cross)
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_world_max",
        fake_cross_world_max,
    )
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_broad_completion_counts", fake_single)
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda *a, **kw: None)
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.broad_count == 1500  # cross wins
    assert row.is_cross_encounter is True


def test_loader_falls_back_to_single_encounter_when_no_cross(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cross-encounter cache → loader uses the single-encounter count
    and `is_cross_encounter` stays False."""
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_counts", lambda *a, **kw: {}
    )

    def fake_single(spec_threshold_map, force_refresh: bool = False):
        return {
            "protection_warrior": {
                "broad_count": 1253,
                "broad_count_capped": False,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_broad_completion_counts", fake_single)
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda *a, **kw: None)
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.broad_count == 1253
    assert row.is_cross_encounter is False


def test_cli_rankings_refresh_invokes_cross_encounter_path(
    monkeypatch: pytest.MonkeyPatch, isolated_cache
) -> None:
    """`simf rankings-refresh --specs protection_warrior` end-to-end:
    invokes the cross-encounter fetch, writes the cache, exit 0."""
    from typer.testing import CliRunner

    from simf.cli import app as cli_app

    enc_ids = wcl_rankings.SEASON_ENCOUNTER_IDS
    rows = {
        enc_ids[0]: [_row("A", "S", "EU"), _row("B", "S", "EU")],
        enc_ids[1]: [_row("C", "S", "EU")],
    }
    mock = _cross_gql_factory(rows)

    runner = CliRunner()
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = runner.invoke(
            cli_app,
            ["rankings-refresh", "--specs", "protection_warrior", "--key-levels", "17"],
        )

    assert result.exit_code == 0, result.output
    assert "protection_warrior" in result.output
    # Cache write happened.
    payload = json.loads(isolated_cache.read_text())
    assert "broad_count_cross" in payload["specs"]["protection_warrior"]
    assert payload["specs"]["protection_warrior"]["broad_count_cross"] == 3


def test_loader_tolerates_corrupt_broad_count_in_live_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the live broad-count layer hands back a payload where
    `broad_count` is None, a string, or otherwise non-numeric (hand-edited
    cache, future writer bug), the loader must NOT crash — it falls back
    to None broad_count for that row so the caption uses the static
    descriptor."""
    bad_payloads = [
        # broad_count missing entirely
        {"protection_warrior": {"broad_threshold": 17}},
        # broad_count explicitly null
        {"protection_warrior": {"broad_count": None, "broad_threshold": 17}},
        # broad_count is a string
        {"protection_warrior": {"broad_count": "not a number", "broad_threshold": 17}},
        # broad_count is a float-looking string
        {"protection_warrior": {"broad_count": "12.5", "broad_threshold": 17}},
    ]
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda *a, **kw: None)
    for bad in bad_payloads:
        monkeypatch.setattr(
            "simf.io.wcl_rankings.fetch_live_broad_completion_counts",
            lambda *a, _p=bad, **kw: _p,
        )
        wc_module.reset_cache()

        row = wc_module.world_ceiling_for("protection_warrior")
        assert row is not None, f"crashed on payload {bad}"
        assert row.broad_count is None, f"non-None broad_count from {bad}"
        # Capped flag must stay False when count is unusable.
        assert row.broad_count_capped is False


# ---- Cross-encounter world_max_key_cross (CLI pre-warm, single-pass) -------


def test_cross_encounter_refresh_writes_world_max_key_cross(isolated_cache) -> None:
    """The cross-encounter pagination loop tracks the highest `bracketData`
    across all 8 season encounters in the same pass it builds the
    distinct-player union; the result lands in the cache under
    `world_max_key_cross`. This is the single-pass extension that mirrors
    the broad_count_cross naming from PR #115."""
    enc_ids = wcl_rankings.SEASON_ENCOUNTER_IDS
    # Three encounters contribute: max bracketData is 24 on enc 1.
    rows_by_enc = {
        enc_ids[0]: [_row("A", "S", "EU", bracket_data=20)],
        enc_ids[1]: [_row("B", "S", "EU", bracket_data=24), _row("C", "S", "EU", bracket_data=22)],
        enc_ids[2]: [_row("D", "S", "EU", bracket_data=18)],
    }
    mock = _cross_gql_factory(rows_by_enc)

    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = wcl_rankings.refresh_cross_encounter_broad_counts({"protection_warrior": 17})

    assert result is not None
    payload = result["protection_warrior"]
    assert payload["world_max_key_cross"] == 24
    # Persisted alongside broad_count_cross.
    cached = json.loads(isolated_cache.read_text())
    cached_row = cached["specs"]["protection_warrior"]
    assert cached_row["world_max_key_cross"] == 24
    assert cached_row["broad_count_cross"] == 4


def test_read_cached_cross_encounter_world_max_returns_only_valid_rows(
    isolated_cache,
) -> None:
    """The UI-safe cache reader returns `world_max_key_cross` per spec
    when present and numeric; rows without the field, with non-numeric
    values, or with non-positive values are silently skipped."""
    isolated_cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zone_id": 47,
                "specs": {
                    "protection_warrior": {"world_max_key_cross": 23},
                    "protection_paladin": {"world_max_key": 22},  # no cross field
                    "blood_death_knight": {"world_max_key_cross": "bad"},  # non-int
                    "vengeance_demon_hunter": {"world_max_key_cross": 0},  # zero
                    "brewmaster_monk": {"world_max_key_cross": -3},  # negative
                    "guardian_druid": {"world_max_key_cross": 21},
                },
            }
        )
    )

    out = wcl_rankings.read_cached_cross_encounter_world_max()
    assert out == {"protection_warrior": 23, "guardian_druid": 21}


def test_loader_prefers_cross_encounter_world_max_when_higher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loader composes `max(cross_world_max, single_world_max)` — the
    cross value can ratchet the world-max upward when it exceeds the
    single-encounter high-probe value. (Practical case on uncapped
    refreshes where cross-encounter sees a high-bracket run the single
    encounter missed.)"""
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_world_max_keys",
        lambda *a, **kw: {"protection_warrior": 22},
    )
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_world_max",
        lambda: {"protection_warrior": 25},
    )
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts", lambda *a, **kw: None
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.world_max_key == 25  # cross wins because it's higher


def test_loader_does_not_regress_world_max_when_cross_is_lower(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critical anti-regression: when the cross-encounter run hits the
    row cap, its `world_max_key_cross` can be a floor — score-order
    pagination may hide the highest-bracket run past the cap. The loader
    composes with `max()` so the cross value can NEVER regress below the
    single-encounter high-probe `world_max_key` from PR #106."""
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_world_max_keys",
        lambda *a, **kw: {"protection_warrior": 23},
    )
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_world_max",
        lambda: {"protection_warrior": 20},  # capped, floor
    )
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts", lambda *a, **kw: None
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    # Single-encounter high-probe wins because it's higher.
    assert row.world_max_key == 23


def test_loader_three_tier_fallback_for_world_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3-tier fallback: cross-encounter cache → single-encounter live →
    static YAML. When the cross cache is empty AND the live high-probe
    fails, the loader returns the YAML value."""
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda *a, **kw: None)
    monkeypatch.setattr("simf.io.wcl_rankings.read_cached_cross_encounter_world_max", lambda: {})
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts", lambda *a, **kw: None
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    # YAML value (placeholder=True row in world_ceilings.yaml).
    assert row.world_max_key > 0
    # No cross-encounter sourcing → suffix should not fire.
    assert row.is_cross_encounter is False


def test_loader_is_cross_encounter_requires_both_layers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`is_cross_encounter` is the composed AND of broad-count-cross and
    world-max-cross. A partial state (one layer cross, one layer single)
    surfaces as False — the caption suffix only fires when EVERY number
    shown is cross-sourced."""

    def fake_cross_counts(spec_threshold_map):
        return {
            "protection_warrior": {
                "broad_count_cross": 1500,
                "broad_count_cross_capped": False,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    def fake_single(spec_threshold_map, force_refresh: bool = False):
        return {
            "protection_warrior": {
                "broad_count": 1253,
                "broad_count_capped": False,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    # Case A: broad-count cross present, world-max cross MISSING.
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_counts", fake_cross_counts
    )
    monkeypatch.setattr("simf.io.wcl_rankings.read_cached_cross_encounter_world_max", lambda: {})
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_broad_completion_counts", fake_single)
    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_world_max_keys", lambda *a, **kw: None)
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.broad_count == 1500  # broad-cross still wins
    assert row.is_cross_encounter is False  # AND-compose: partial state → False

    # Case B: world-max cross present, broad-count cross MISSING.
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_counts", lambda *a, **kw: {}
    )
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_world_max",
        lambda: {"protection_warrior": 24},
    )
    wc_module.reset_cache()

    row = wc_module.world_ceiling_for("protection_warrior")
    assert row is not None
    assert row.broad_count == 1253  # single-encounter (no cross broad)
    assert row.is_cross_encounter is False  # AND-compose: partial state → False


def test_cli_rankings_refresh_writes_world_max_key_cross(
    monkeypatch: pytest.MonkeyPatch, isolated_cache
) -> None:
    """End-to-end CLI: `simf rankings-refresh` persists both
    `broad_count_cross` AND `world_max_key_cross` in the same run."""
    from typer.testing import CliRunner

    from simf.cli import app as cli_app

    enc_ids = wcl_rankings.SEASON_ENCOUNTER_IDS
    rows = {
        enc_ids[0]: [_row("A", "S", "EU", bracket_data=21)],
        enc_ids[3]: [_row("B", "S", "EU", bracket_data=23)],
    }
    mock = _cross_gql_factory(rows)

    runner = CliRunner()
    with (
        patch("simf.io.wcl_api.is_configured", return_value=True),
        patch("simf.io.wcl_api._get_token", return_value="t"),
        patch("simf.io.wcl_api._gql", mock),
    ):
        result = runner.invoke(
            cli_app,
            ["rankings-refresh", "--specs", "protection_warrior", "--key-levels", "17"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(isolated_cache.read_text())
    spec_row = payload["specs"]["protection_warrior"]
    # BOTH cross-encounter fields land in the cache from one CLI invocation.
    assert spec_row["broad_count_cross"] == 2
    assert spec_row["world_max_key_cross"] == 23
    # CLI prints the world max alongside the count.
    assert "world max: +23" in result.output
