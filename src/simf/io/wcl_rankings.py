"""Live WCL rankings lookup for the verdict honesty caption.

Hand-curated YAML at `src/simf/data/world_ceilings.yaml` started life as
the truth source for the world-max key and the "a few hundred" caption
descriptor, but both went stale within a couple of weeks (Brutoh's "+20
max" was actually +22-+23 by 2026-05-27; the "few hundred" phrasing was
always a guess). This module refreshes the **world_max_key** AND the
**broad_completion count** live from WCL's `Encounter.characterRankings`
GraphQL endpoint.

Behaviour:
  * If WCL creds aren't configured (`wcl_api.is_configured()` False) →
    YAML fallback. No network calls, no errors.
  * If a disk cache at `~/.simf/wcl_rankings_cache.json` exists and is
    younger than `CACHE_TTL_SECONDS` (24h) → use it.
  * Otherwise → query WCL for each spec, merge with the YAML, write the
    cache, return the live values.

Network failures collapse silently to YAML — the caption is polish, it
should never break the verdict surface.

Cache file format:
  {
    "fetched_at": "2026-05-27T18:00:00Z",
    "zone_id": 47,
    "specs": {
      "protection_warrior": {
        "world_max_key": 23,
        "broad_count": 1253,
        "broad_count_capped": false,
        "broad_threshold": 17,
        # Cross-encounter layer (written by `simf rankings-refresh`):
        "broad_count_cross": 1500,
        "broad_count_cross_capped": false,
        "world_max_key_cross": 23
      },
      ...
    }
  }

Old cache files (pre-broad-count) are forward-compatible: missing fields
simply force a refresh of the missing layer.

Discovery / verification notes (2026-05-27 introspection):
  * `WorldData.encounter(id).characterRankings(className, specName,
    bracket, page)` is the right entry point. `Zone` does not expose
    `rankings` directly.
  * `bracket` is an **exclusive** lower bound — `bracket=20` returns
    runs with `bracketData >= 21`. `count` is page size (100), NOT total.
  * Rows include `{name, server: {name, region}, bracketData, ...}` —
    `(name, server.name, server.region)` is the unique character key.
  * Per-encounter result set is capped at 2000 rows (20 pages × 100).
    When we hit the cap, the distinct-char count is a floor — caption
    reads "at least N+" instead of "N".
  * Rankings are per-encounter; the season zone has 8 encounters. **Scope
    cut**: broad-completion count probes ONE representative encounter
    (see `BROAD_COMPLETION_ENCOUNTER_ID`). A true union-across-all-8
    would 8x the page budget (~10 minutes cold refresh for 6 specs) and
    is deferred. Per-encounter count is a lower bound for the
    cross-encounter union.

Zone correction (2026-08-13, live authenticated WCL GraphQL query):
  Zone 45 was the WRONG zone — it is TWW Season 3 (a previous
  expansion's 8-dungeon pool: Ara-Kara, Eco-Dome Al'dani, Halls of
  Atonement, Operation: Floodgate, Priory of the Sacred Flame, both
  Tazavesh wings, The Dawnbreaker). `worldData.zone(id: 47)` is the
  real, live Midnight Mythic+ Season 1 zone:
    {"id": 47, "name": "Mythic+ Season 1", "encounters": [
      {"id": 112526, "name": "Algeth'ar Academy"},
      {"id": 12811,  "name": "Magisters' Terrace"},
      {"id": 12874,  "name": "Maisara Caverns"},
      {"id": 12915,  "name": "Nexus-Point Xenas"},
      {"id": 10658,  "name": "Pit of Saron"},
      {"id": 361753, "name": "Seat of the Triumvirate"},
      {"id": 61209,  "name": "Skyreach"},
      {"id": 12805,  "name": "Windrunner Spire"}
    ]}
  Zone 55 is the (not-yet-live) Midnight Season 2 zone — do not use it
  either; this correction only concerns replacing 45 with 47. Every
  cache written under the old (wrong) zone id is stale and will be
  silently superseded once `fetch_live_world_max_keys` /
  `fetch_live_broad_completion_counts` refresh under the corrected
  constants below.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Current M+ season zone (Midnight 12.0.5, Mythic+ Season 1).
# Confirmed via a live authenticated WCL GraphQL query on 2026-08-13:
# `worldData.zone(id: 47) = {"id": 47, "name": "Mythic+ Season 1", ...}`.
# Zone 45 (previously hardcoded here) is WRONG — it's TWW Season 3, a
# different expansion's dungeon pool entirely (30x stat scale mismatch
# against this project's Midnight-tuned engine). Zone 55 is the
# not-yet-live Midnight Season 2 zone — do not use it either.
SEASON_ZONE_ID = 47

# 8 encounters in zone 47 — confirmed live 2026-08-13 via the same
# introspection query. Kept as a constant so the module doesn't need to
# roundtrip the zone query every refresh; if WCL adds dungeons mid-season,
# refresh this list.
SEASON_ENCOUNTER_IDS = [
    112526,  # Algeth'ar Academy
    12811,  # Magisters' Terrace
    12874,  # Maisara Caverns
    12915,  # Nexus-Point Xenas
    10658,  # Pit of Saron
    361753,  # Seat of the Triumvirate
    61209,  # Skyreach
    12805,  # Windrunner Spire
]

# Representative encounter for broad-completion counts. Using one
# encounter (instead of all 8) keeps the cold refresh under ~90 s and
# the count is a lower bound for the true cross-encounter union;
# per-encounter is documented as a floor in the caption copy ("at least
# N"). NOTE: the old zone-45 comment claimed Ara-Kara was verified
# densely-played (2026-05-27) — that claim doesn't carry over to zone
# 47's dungeon at index 0 (Algeth'ar Academy); which of the 8 is most
# densely played hasn't been re-verified for this zone. Index 0 is kept
# as the arbitrary "pick one" choice pending that re-verification.
BROAD_COMPLETION_ENCOUNTER_ID = SEASON_ENCOUNTER_IDS[0]  # Algeth'ar Academy (unverified density)

# WCL caps `characterRankings` at ~2000 rows per (encounter, spec,
# bracket) tuple. When we paginate to the cap we report the count as a
# floor and flag `broad_count_capped: true` so the caption can read
# "at least 2,000+" instead of an exact integer.
BROAD_COMPLETION_PAGE_CAP = 25  # 25 pages × 100 = 2500 rows ceiling
BROAD_COMPLETION_ROW_CAP = 2000

# Spec slug → (className, specName) for the WCL query. The WCL API
# takes English class names (no localisation). Keep this in sync with
# the keys in `src/simf/data/world_ceilings.yaml`.
SPEC_TO_WCL = {
    "protection_warrior": ("Warrior", "Protection"),
    "protection_paladin": ("Paladin", "Protection"),
    "blood_death_knight": ("DeathKnight", "Blood"),
    "vengeance_demon_hunter": ("DemonHunter", "Vengeance"),
    "brewmaster_monk": ("Monk", "Brewmaster"),
    "guardian_druid": ("Druid", "Guardian"),
}

# Cache lives in the user's simf directory next to the WCL config.
CACHE_PATH = Path.home() / ".simf" / "wcl_rankings_cache.json"

# 24h TTL — world-max rankings move slowly enough that a daily refresh
# is more than enough resolution for an honesty caption.
CACHE_TTL_SECONDS = 24 * 60 * 60

# Probe ladder for `bracket=N` — start high, drop if page 1 is empty.
# 25 catches anything plausible in season 3; the lowest fallback (15)
# protects specs that nobody pushes (placeholder safety).
_PROBE_BRACKETS = [25, 22, 20, 18, 15]


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime | None:
    try:
        # Tolerate either "...Z" or "...+00:00"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _read_cache() -> dict | None:
    """Read the disk cache. Returns `None` on missing / corrupt /
    expired — never raises. Corrupt JSON is treated as a miss so the
    verdict surface never crashes on a bad file.
    """
    if not CACHE_PATH.exists():
        return None
    try:
        raw = json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        logger.debug("wcl_rankings cache corrupt or unreadable; treating as miss")
        return None
    if not isinstance(raw, dict):
        return None
    fetched_at = _parse_iso(str(raw.get("fetched_at", "")))
    if fetched_at is None:
        return None
    age = (datetime.now(UTC) - fetched_at).total_seconds()
    if age > CACHE_TTL_SECONDS:
        return None
    return raw


def _write_cache(specs: dict[str, dict]) -> None:
    """Best-effort cache write. Failures (disk full, permission) log and
    swallow — the caller still gets the live values back.

    `specs` is the per-spec payload dict (e.g. ``{"world_max_key": 23,
    "broad_count": 1253, "broad_count_capped": False,
    "broad_threshold": 17}``); the writer passes it through unchanged so
    new layers can be added without ceremony.
    """
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": _now_iso(),
            "zone_id": SEASON_ZONE_ID,
            "specs": specs,
        }
        CACHE_PATH.write_text(json.dumps(payload, indent=2))
    except OSError as e:
        logger.debug("wcl_rankings cache write failed: %s", e)


def _max_bracket_for_spec(
    class_name: str,
    spec_name: str,
    token: str,
    gql,
) -> int:
    """Find the highest `bracketData` across all 8 season encounters
    for one spec. Probes each encounter starting at the highest plausible
    bracket and drops if page 1 is empty.

    `gql` is injected so tests can mock it (production callers pass
    `simf.io.wcl_api._gql`).

    Returns 0 if no runs found (caller should fall back to YAML).
    """
    best = 0
    for enc_id in SEASON_ENCOUNTER_IDS:
        enc_best = _max_bracket_for_encounter(enc_id, class_name, spec_name, token, gql)
        if enc_best > best:
            best = enc_best
    return best


def _max_bracket_for_encounter(
    encounter_id: int,
    class_name: str,
    spec_name: str,
    token: str,
    gql,
) -> int:
    """One encounter, one spec → highest `bracketData` seen. Drops the
    probe bracket until page 1 has rows; once it does, scans the rows
    (and any further pages — usually 0-1 more) for the max."""
    for try_bracket in _PROBE_BRACKETS:
        rows = _fetch_page(encounter_id, class_name, spec_name, try_bracket, 1, token, gql)
        if rows is None:
            # Network or schema failure — bail out for this encounter,
            # the caller's loop continues with the others.
            return 0
        if not rows["rankings"]:
            continue
        best = max(int(r.get("bracketData") or 0) for r in rows["rankings"])
        # Scan further pages defensively. Empirically there's rarely
        # more than 1-2 pages at high brackets, and bracketData on
        # later pages is usually equal-or-lower (sort is by score, not
        # by bracket — so higher bracket data CAN appear later, just
        # rarely).
        page = 2
        max_extra_pages = 5
        while rows["hasMorePages"] and max_extra_pages > 0:
            rows = _fetch_page(encounter_id, class_name, spec_name, try_bracket, page, token, gql)
            if rows is None or not rows["rankings"]:
                break
            page_best = max(int(r.get("bracketData") or 0) for r in rows["rankings"])
            if page_best > best:
                best = page_best
            page += 1
            max_extra_pages -= 1
        return best
    return 0


def _fetch_page(
    encounter_id: int,
    class_name: str,
    spec_name: str,
    bracket: int,
    page: int,
    token: str,
    gql,
) -> dict | None:
    """One characterRankings query. Returns `{"rankings": [...],
    "hasMorePages": bool}` or `None` on any failure."""
    query = """
    query($encId: Int!, $cls: String!, $spec: String!, $bracket: Int!, $page: Int!) {
      worldData {
        encounter(id: $encId) {
          characterRankings(
            className: $cls
            specName: $spec
            bracket: $bracket
            page: $page
          )
        }
      }
    }
    """
    variables = {
        "encId": encounter_id,
        "cls": class_name,
        "spec": spec_name,
        "bracket": bracket,
        "page": page,
    }
    try:
        data = gql(token, query, variables)
    except Exception as e:
        logger.debug(
            "WCL characterRankings query failed (enc=%d spec=%s page=%d): %s",
            encounter_id,
            spec_name,
            page,
            e,
        )
        return None
    cr = ((data or {}).get("worldData", {}) or {}).get("encounter", {})
    if not cr:
        return None
    payload = cr.get("characterRankings")
    if not isinstance(payload, dict):
        return None
    return {
        "rankings": payload.get("rankings") or [],
        "hasMorePages": bool(payload.get("hasMorePages")),
    }


def fetch_live_world_max_keys(*, force_refresh: bool = False) -> dict[str, int] | None:
    """Live world-max key per spec slug. Returns `None` when WCL isn't
    configured or every spec lookup fails — callers should fall back to
    the YAML in that case.

    `force_refresh=True` bypasses the disk cache (useful for tests and
    manual refresh).

    The successful return is `{spec_slug: world_max_key}`. Specs that
    failed individually are omitted (caller will see the YAML value
    for those).
    """
    # Cache first.
    if not force_refresh:
        cached = _read_cache()
        if cached is not None:
            specs = cached.get("specs") or {}
            hits = {
                spec: int(row.get("world_max_key", 0))
                for spec, row in specs.items()
                if isinstance(row, dict) and row.get("world_max_key", 0)
            }
            if hits:
                return hits
            # Cache present but lacks world_max_key for any spec — fall
            # through to a live refresh.

    # Lazy-import to avoid pulling `requests` when not needed.
    from . import wcl_api

    if not wcl_api.is_configured():
        return None

    try:
        token = wcl_api._get_token()
    except Exception as e:
        logger.debug("WCL token fetch failed; falling back to static YAML: %s", e)
        return None

    out: dict[str, int] = {}
    for spec_slug, (class_name, spec_name) in SPEC_TO_WCL.items():
        try:
            n = _max_bracket_for_spec(class_name, spec_name, token, wcl_api._gql)
        except Exception as e:
            logger.debug("WCL spec lookup failed for %s: %s", spec_slug, e)
            continue
        if n > 0:
            out[spec_slug] = n

    if not out:
        return None

    # Merge into any existing cache rather than overwriting other layers
    # (broad_count). Cache miss → blank dict.
    existing = _read_cache() or {}
    specs_payload = dict(existing.get("specs") or {})
    for spec_slug, n in out.items():
        row = dict(specs_payload.get(spec_slug) or {})
        row["world_max_key"] = int(n)
        specs_payload[spec_slug] = row
    _write_cache(specs_payload)
    return out


# ───────────────────────────────────────────────────────────────────────────
# Broad-completion count layer
# ───────────────────────────────────────────────────────────────────────────


def _collect_distinct_chars_at_bracket(
    encounter_id: int,
    class_name: str,
    spec_name: str,
    bracket: int,
    token: str,
    gql,
) -> tuple[set[tuple], bool, int]:
    """Set of distinct `(name, server.name, server.region)` tuples who
    timed > `bracket` on one encounter, plus a `capped` flag and the
    maximum `bracketData` seen across all paginated rows.

    This is the underlying primitive that both the per-encounter count
    and the cross-encounter union are built on. Cross-encounter callers
    take the union of these sets across all 8 season encounters; the
    legacy per-encounter caller takes `len(seen)`.

    `bracket` is the WCL exclusive lower bound; pass `broad_threshold - 1`
    if you want characters with `bracketData >= broad_threshold`.

    `capped` is True when EITHER:
      * the count grew to `BROAD_COMPLETION_ROW_CAP` while `hasMorePages`
        was still true (WCL is truncating us), OR
      * we exhausted `BROAD_COMPLETION_PAGE_CAP` pages with `hasMorePages`
        still flagged (defensive ceiling).

    `max_bracket_data` is the highest `bracketData` value observed across
    every row visited. **This can be a floor when `capped` is True** —
    WCL paginates by score, not by `bracketData`, so a higher-bracket run
    can be hidden past the row cap. Callers that need a strict upper
    bound should fall back to the high-probe path
    (`_max_bracket_for_spec`) and compose with `max()` rather than trust
    this value blindly.
    """
    seen: set[tuple] = set()
    capped = False
    max_bracket_data = 0
    for page in range(1, BROAD_COMPLETION_PAGE_CAP + 1):
        rows = _fetch_page(encounter_id, class_name, spec_name, bracket, page, token, gql)
        if rows is None:
            return set(), False, 0
        for r in rows["rankings"]:
            server = r.get("server") or {}
            key = (
                r.get("name"),
                server.get("name"),
                server.get("region"),
            )
            seen.add(key)
            # Track the highest bracketData while we're already iterating
            # rows — single-pass extension keeps the cross-encounter
            # pagination cost identical to the count-only baseline.
            try:
                bd = int(r.get("bracketData") or 0)
            except (TypeError, ValueError):
                bd = 0
            if bd > max_bracket_data:
                max_bracket_data = bd
        if not rows["hasMorePages"]:
            break
        if len(seen) >= BROAD_COMPLETION_ROW_CAP:
            # WCL is going to cap us at ~2000 rows total — record as a
            # floor and bail.
            capped = True
            break
    else:
        # Fell off the end of the page cap with hasMorePages still true.
        capped = True
    return seen, capped, max_bracket_data


def _count_distinct_chars_at_bracket(
    encounter_id: int,
    class_name: str,
    spec_name: str,
    bracket: int,
    token: str,
    gql,
) -> tuple[int, bool]:
    """Distinct players who timed > `bracket` on one encounter.

    Returns `(count, capped)`. Network or schema failures return
    `(0, False)` — caller treats 0 as "no live data".

    Thin wrapper around `_collect_distinct_chars_at_bracket` that
    preserves the legacy `(int, bool)` shape for the single-encounter
    fetch path; the third tuple element (max bracketData) is discarded
    because the single-encounter caption layer doesn't need it.
    """
    seen, capped, _ = _collect_distinct_chars_at_bracket(
        encounter_id, class_name, spec_name, bracket, token, gql
    )
    return len(seen), capped


def fetch_live_broad_completion_counts(
    spec_threshold_map: dict[str, int],
    *,
    force_refresh: bool = False,
) -> dict[str, dict] | None:
    """Live distinct-player counts at the broad-completion threshold per spec.

    `spec_threshold_map` maps spec_slug → `broad_completion_key` (from
    the YAML). For each spec we count distinct players with `bracketData
    >= broad_completion_key` on `BROAD_COMPLETION_ENCOUNTER_ID`. Result:

      {spec_slug: {"broad_count": int, "broad_count_capped": bool,
                   "broad_threshold": int}, ...}

    Specs missing from the input (or where the live lookup yielded 0)
    are omitted — caller falls back to the static descriptor for those.

    Returns `None` when WCL isn't configured, the token fetch fails, or
    EVERY spec yielded 0. Cache hits skip the network. Force-refresh
    bypasses the cache.

    The cache is checked per-spec: a row whose cached `broad_threshold`
    matches the requested threshold is reused; mismatches force a live
    refresh for that spec (the broad-completion threshold can move
    between releases as YAML is hand-tuned).
    """
    # Per-spec cache check.
    cache_hits: dict[str, dict] = {}
    pending: dict[str, int] = {}
    cached = None if force_refresh else _read_cache()
    cached_specs = (cached or {}).get("specs") or {}

    for spec_slug, threshold in spec_threshold_map.items():
        row = cached_specs.get(spec_slug)
        if (
            isinstance(row, dict)
            and "broad_count" in row
            and int(row.get("broad_threshold", -1)) == int(threshold)
        ):
            cache_hits[spec_slug] = {
                "broad_count": int(row["broad_count"]),
                "broad_count_capped": bool(row.get("broad_count_capped", False)),
                "broad_threshold": int(threshold),
            }
        else:
            pending[spec_slug] = threshold

    if not pending:
        return cache_hits or None

    # Live path for the pending specs.
    from . import wcl_api

    if not wcl_api.is_configured():
        return cache_hits or None

    try:
        token = wcl_api._get_token()
    except Exception as e:
        logger.debug("WCL token fetch failed for broad-count layer: %s", e)
        return cache_hits or None

    fresh: dict[str, dict] = {}
    for spec_slug, threshold in pending.items():
        wcl_pair = SPEC_TO_WCL.get(spec_slug)
        if wcl_pair is None:
            continue
        class_name, spec_name = wcl_pair
        try:
            count, capped = _count_distinct_chars_at_bracket(
                BROAD_COMPLETION_ENCOUNTER_ID,
                class_name,
                spec_name,
                bracket=int(threshold) - 1,  # exclusive: bracket=N-1 => bracketData >= N
                token=token,
                gql=wcl_api._gql,
            )
        except Exception as e:
            logger.debug("WCL broad-count lookup failed for %s: %s", spec_slug, e)
            continue
        if count > 0:
            fresh[spec_slug] = {
                "broad_count": int(count),
                "broad_count_capped": bool(capped),
                "broad_threshold": int(threshold),
            }

    merged = {**cache_hits, **fresh}
    if not merged:
        return None

    # Persist new rows back into the cache (preserving any other layers
    # like world_max_key).
    if fresh:
        existing = _read_cache() or {}
        specs_payload = dict(existing.get("specs") or {})
        for spec_slug, payload in fresh.items():
            row = dict(specs_payload.get(spec_slug) or {})
            row.update(payload)
            specs_payload[spec_slug] = row
        _write_cache(specs_payload)

    return merged


# ───────────────────────────────────────────────────────────────────────────
# Cross-encounter broad-completion union (CLI pre-warm only — never UI)
# ───────────────────────────────────────────────────────────────────────────


def read_cached_cross_encounter_counts(
    spec_threshold_map: dict[str, int],
) -> dict[str, dict]:
    """Cache-only read of cross-encounter broad counts. Never network.

    This is the UI-safe accessor. The cold refresh takes 6-10 min and
    must not run on the verdict render path; users pre-warm via
    `simf rankings-refresh`. When the cache hasn't been pre-warmed (or
    a row has a stale threshold), this returns an empty dict and the
    loader falls back to the single-encounter `broad_count`.

    Returns `{spec_slug: {"broad_count_cross": int,
    "broad_count_cross_capped": bool, "broad_threshold": int}, ...}`.
    Specs without a usable cached row are simply omitted.
    """
    cached = _read_cache()
    if cached is None:
        return {}
    specs = cached.get("specs") or {}
    out: dict[str, dict] = {}
    for spec_slug, threshold in spec_threshold_map.items():
        row = specs.get(spec_slug)
        if not isinstance(row, dict):
            continue
        if "broad_count_cross" not in row:
            continue
        # Tolerate corrupt threshold (None, string, non-numeric) as a
        # miss rather than a crash — the loader's outer try/except would
        # catch it but we'd rather degrade gracefully here.
        try:
            cached_threshold = int(row.get("broad_threshold", -1))
        except (TypeError, ValueError):
            continue
        if cached_threshold != int(threshold):
            # Threshold drifted — treat as a miss; the cross-encounter
            # number wasn't computed for this threshold.
            continue
        try:
            count_int = int(row["broad_count_cross"])
        except (TypeError, ValueError):
            continue
        if count_int <= 0:
            continue
        out[spec_slug] = {
            "broad_count_cross": count_int,
            "broad_count_cross_capped": bool(row.get("broad_count_cross_capped", False)),
            "broad_threshold": int(threshold),
        }
    return out


def read_cached_cross_encounter_world_max() -> dict[str, int]:
    """Cache-only read of cross-encounter `world_max_key_cross`. Never network.

    Symmetric to `read_cached_cross_encounter_counts` — the UI-safe
    accessor for the cross-encounter world-max-key layer written by
    `simf rankings-refresh`. The cold refresh takes 6-10 min and must
    not run on the verdict render path.

    Returns `{spec_slug: world_max_key_cross}`. Specs without a usable
    cached row are simply omitted; rows with non-numeric / zero /
    negative values are skipped. The loader composes this with the
    single-encounter `world_max_key` via `max()` so a row-capped cross
    value never regresses against the high-probe path from PR #106.
    """
    cached = _read_cache()
    if cached is None:
        return {}
    specs = cached.get("specs") or {}
    out: dict[str, int] = {}
    for spec_slug, row in specs.items():
        if not isinstance(row, dict):
            continue
        if "world_max_key_cross" not in row:
            continue
        try:
            n = int(row["world_max_key_cross"])
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        out[spec_slug] = n
    return out


def refresh_cross_encounter_broad_counts(
    spec_threshold_map: dict[str, int],
    *,
    progress=None,
) -> dict[str, dict] | None:
    """Cross-encounter distinct-player count per spec, unioning all 8
    season encounters.

    This is the CLI pre-warm entrypoint (`simf rankings-refresh`). It is
    NEVER called from the UI render path — the cold network cost is
    6-10 min for 6 specs × 8 encounters × ~25 pages, which is
    unacceptable on a verdict render. The UI continues to read the
    cached values via `fetch_live_broad_completion_counts` and the
    loader's cross-field preference.

    For each spec we collect distinct `(name, server.name, server.region)`
    tuples across every encounter in `SEASON_ENCOUNTER_IDS`, then union
    them. The cross-encounter union is strictly >= the single-encounter
    count (~17-20% higher in practice on dense specs).

    `spec_threshold_map` maps spec_slug → `broad_completion_key`. The
    WCL bracket query is exclusive lower bound, so we pass
    `threshold - 1` to get rows with `bracketData >= threshold`.

    `progress` is an optional callable invoked as
    `progress(spec_slug, encounter_index, encounter_count)` after each
    encounter completes. The CLI uses this to print a dot per encounter
    so a 6-10 min wait isn't silent. Test code passes None and asserts
    on the returned dict.

    Returns `{spec_slug: {"broad_count_cross": int,
    "broad_count_cross_capped": bool, "broad_threshold": int}, ...}`,
    or `None` when WCL isn't configured / every spec yields 0.

    `broad_count_cross_capped` is True when ANY contributing encounter
    hit the row cap — the union is a floor in that case (some characters
    are unobserved in the truncated encounter result sets).

    Successful payloads are persisted to the same cache file as
    `fetch_live_broad_completion_counts`, with the new
    `broad_count_cross` / `broad_count_cross_capped` keys merged
    alongside any existing `broad_count` row (no clobber).
    """
    from . import wcl_api

    if not wcl_api.is_configured():
        return None

    try:
        token = wcl_api._get_token()
    except Exception as e:
        logger.debug("WCL token fetch failed for cross-encounter refresh: %s", e)
        return None

    fresh: dict[str, dict] = {}
    for spec_slug, threshold in spec_threshold_map.items():
        wcl_pair = SPEC_TO_WCL.get(spec_slug)
        if wcl_pair is None:
            continue
        class_name, spec_name = wcl_pair
        union: set[tuple] = set()
        any_capped = False
        max_bracket_cross = 0
        for idx, enc_id in enumerate(SEASON_ENCOUNTER_IDS):
            try:
                seen, capped, enc_max = _collect_distinct_chars_at_bracket(
                    enc_id,
                    class_name,
                    spec_name,
                    bracket=int(threshold) - 1,
                    token=token,
                    gql=wcl_api._gql,
                )
            except Exception as e:
                logger.debug(
                    "WCL cross-encounter fetch failed for %s enc=%d: %s",
                    spec_slug,
                    enc_id,
                    e,
                )
                continue
            union |= seen
            any_capped = any_capped or capped
            # Track the highest `bracketData` observed across all 8
            # season encounters — the same single pass that builds the
            # distinct-player union. When `any_capped` is True this is a
            # floor (score-order pagination may hide the highest bracket
            # past the row cap); the world_ceilings loader guards
            # against regression by composing with `max(cross, single)`.
            if enc_max > max_bracket_cross:
                max_bracket_cross = enc_max
            if progress is not None:
                # Best-effort UX layer — never let a buggy progress
                # callback break the refresh.
                import contextlib

                with contextlib.suppress(Exception):
                    progress(spec_slug, idx + 1, len(SEASON_ENCOUNTER_IDS))
        if union:
            payload = {
                "broad_count_cross": len(union),
                "broad_count_cross_capped": any_capped,
                "broad_threshold": int(threshold),
            }
            if max_bracket_cross > 0:
                payload["world_max_key_cross"] = max_bracket_cross
            fresh[spec_slug] = payload

    if not fresh:
        return None

    # Persist into the shared cache, preserving every other layer.
    existing = _read_cache() or {}
    specs_payload = dict(existing.get("specs") or {})
    for spec_slug, payload in fresh.items():
        row = dict(specs_payload.get(spec_slug) or {})
        row.update(payload)
        specs_payload[spec_slug] = row
    _write_cache(specs_payload)

    return fresh
