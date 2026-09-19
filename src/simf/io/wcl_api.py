"""Warcraft Logs v2 GraphQL API client.

Credentials: set WCL_CLIENT_ID and WCL_CLIENT_SECRET env vars, or create
~/.simf/wcl_config.yaml with keys client_id / client_secret.
Register a client at https://www.warcraftlogs.com/api/clients/.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import requests

    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

from .combat_log import ChallengeModeRun, DamageTakenEvent, EncounterWindow, school_name

_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"  # noqa: S105 — false positive — this is the OAuth token endpoint URL, not a credential
_API_URL = "https://www.warcraftlogs.com/api/v2/client"

# Opt-in on-disk cache for _gql responses (2026-07-25) — WCL fight data for a
# past pull never changes, so caching is zero-staleness-risk, but this stays
# OFF by default (cache_dir=None) so every existing caller/test keeps today's
# exact behaviour; a caller opts in explicitly by passing cache_dir=. Not
# wired into the production calibrate-k --wcl-url path — that stays live,
# unchanged. Intended for investigation/research scripts that repeatedly hit
# the same historical fights (rankings discovery, independent-player
# corroboration) and want to avoid re-paying WCL's ~90s-per-call latency.
DEFAULT_WCL_CACHE_DIR = Path("examples/wcl_cache")

# WCL hit type constants
_HIT_NORMAL = 1
_HIT_CRIT = 2
_HIT_GLANCE = 8


@dataclass
class WCLFight:
    id: int
    name: str
    start_time_ms: int
    end_time_ms: int
    key_level: int | None
    affixes: list[int] = field(default_factory=list)
    completed: bool = True


@dataclass
class WCLReport:
    code: str
    start_time_ms: int  # Unix ms for report start — event timestamps are relative to this
    fights: list[WCLFight] = field(default_factory=list)


@dataclass
class WCLPlayer:
    """One player in a WCL fight, normalized for the character picker."""

    name: str
    role: str  # "tank" | "healer" | "dps"
    class_spec: str = ""  # slug like "protection_warrior"; empty if unknown
    actor_id: int | None = None  # WCL actor ID; matches `source=N` URL fragment


def is_configured() -> bool:
    return _REQUESTS_OK and bool(_load_credentials())


def _load_credentials() -> dict | None:
    cid = os.environ.get("WCL_CLIENT_ID")
    csec = os.environ.get("WCL_CLIENT_SECRET")
    if cid and csec:
        return {"client_id": cid, "client_secret": csec}
    cfg = Path.home() / ".simf" / "wcl_config.yaml"
    if cfg.exists():
        try:
            import yaml

            data = yaml.safe_load(cfg.read_text())
            if data.get("client_id") and data.get("client_secret"):
                return data
        except Exception:  # noqa: S110 — best-effort fallback
            pass
    return None


def _get_token() -> str:
    creds = _load_credentials()
    if not creds:
        raise RuntimeError(
            "WCL credentials not found. Set WCL_CLIENT_ID / WCL_CLIENT_SECRET env vars "
            "or create ~/.simf/wcl_config.yaml with client_id / client_secret."
        )
    resp = requests.post(
        _TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(creds["client_id"], creds["client_secret"]),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@dataclass
class WCLRateLimit:
    """WCL ``rateLimitData`` snapshot — points are billed per client
    application (the shared owner key), not per user, so the public flow
    reads this to degrade gracefully before exhausting it (ADR 0001)."""

    limit_per_hour: int
    points_spent_this_hour: float
    points_reset_in: int  # seconds until the hourly budget resets

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit_per_hour - self.points_spent_this_hour)

    def has_headroom(self, reserve_fraction: float = 0.10) -> bool:
        """True if enough budget remains above the reserve to serve a fetch.

        ``limit_per_hour <= 0`` is treated as "unknown / unlimited" → admit,
        so a schema hiccup never hard-blocks the flow.
        """
        if self.limit_per_hour <= 0:
            return True
        return self.remaining >= self.limit_per_hour * reserve_fraction


def fetch_rate_limit(token: str) -> WCLRateLimit:
    """Query WCL's ``rateLimitData`` so the public flow can pre-flight the
    shared owner key's hourly point budget (ADR 0001)."""
    query = "{ rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn } }"
    rl = _gql(token, query)["rateLimitData"]
    return WCLRateLimit(
        limit_per_hour=int(rl["limitPerHour"]),
        points_spent_this_hour=float(rl["pointsSpentThisHour"]),
        points_reset_in=int(rl["pointsResetIn"]),
    )


def _gql_cache_key(query: str, variables: dict | None) -> str:
    """Stable hash of (query, variables) — the cache key. Deliberately excludes
    the token (auth doesn't change the data returned for a given query)."""
    blob = json.dumps({"query": query, "variables": variables or {}}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _gql(
    token: str,
    query: str,
    variables: dict | None = None,
    *,
    cache_dir: Path | None = None,
) -> dict:
    """``cache_dir`` (opt-in, default None): check/write a JSON file cache
    keyed by a hash of (query, variables) before hitting the network. Every
    existing call site omits it and is completely unaffected. Only successful
    responses are cached — an error is never written, so a transient failure
    can't poison future runs."""
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{_gql_cache_key(query, variables)}.json"
        if cache_file.exists():
            with cache_file.open() as f:
                return json.load(f)

    resp = requests.post(
        _API_URL,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"WCL GraphQL error: {data['errors']}")
    result = data["data"]

    if cache_dir is not None:
        with cache_file.open("w") as f:
            json.dump(result, f)

    return result


def url_to_code(url: str) -> str:
    """Extract report code from a WCL URL like https://www.warcraftlogs.com/reports/ABC123."""
    m = re.search(r"/reports/([A-Za-z0-9]+)", url)
    if not m:
        raise ValueError(f"Could not parse WCL report code from: {url!r}")
    return m.group(1)


def url_to_code_and_fight(url: str) -> tuple[str, int | None]:
    """Return ``(report_code, fight_id_or_None)`` from a WCL URL.

    WCL share-links commonly carry a ``#fight=N`` fragment that selects one
    fight inside the report (``…/reports/abc123XYZ#fight=4``). The Discord
    "paste a link" workflow tends to include that fragment because it
    deep-links to the fight the user actually cares about. We preserve the
    selection so the UI can preselect that row in the fight picker.

    Raises ``ValueError`` for malformed inputs (no ``/reports/<code>``
    segment) so the UI can surface a clean error message instead of a
    stack trace. Bare or `fight=last` fragments yield ``None`` — the
    picker defaults to whatever the user finds easiest.
    """
    code = url_to_code(url)  # raises ValueError on malformed input
    m = re.search(r"fight=(\d+)", url)
    if m:
        try:
            return code, int(m.group(1))
        except ValueError:  # pragma: no cover — regex already constrains digits
            return code, None
    return code, None


def url_to_source_id(url: str) -> int | None:
    """Return the ``source=N`` WCL actor ID from a share URL, or None.

    WCL share-links include ``source=N`` when the user clicked a specific
    player before copying the URL. That actor ID maps directly to a player
    in ``playerDetails`` — preserving it lets the picker default to the
    player the user actually meant instead of the first tank in the fight.
    """
    m = re.search(r"source=(\d+)", url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:  # pragma: no cover — regex already constrains digits
        return None


def fetch_report(report_code: str, token: str, *, cache_dir: Path | None = None) -> WCLReport:
    """Fetch the fight list and report start time for a WCL report.

    ``cache_dir``: opt-in on-disk cache (see ``_gql``'s docstring) — a past
    report's fight list never changes, so this is zero-staleness-risk.
    """
    query = """
    query($code: String!) {
      reportData {
        report(code: $code) {
          startTime
          fights(translate: true) {
            id
            name
            startTime
            endTime
            keystoneLevel
            keystoneAffixes
            completeRaid
          }
        }
      }
    }
    """
    data = _gql(token, query, {"code": report_code}, cache_dir=cache_dir)
    report = data["reportData"]["report"]
    start_ms = int(report["startTime"])
    fights = []
    for f in report.get("fights") or []:
        kl = f.get("keystoneLevel")
        affixes = f.get("keystoneAffixes") or []
        fights.append(
            WCLFight(
                id=f["id"],
                name=f.get("name") or "Unknown",
                start_time_ms=int(f["startTime"]),
                end_time_ms=int(f["endTime"]),
                key_level=kl,
                affixes=[int(a) for a in affixes],
            )
        )
    return WCLReport(code=report_code, start_time_ms=start_ms, fights=fights)


def _icon_to_class_spec_slug(icon: str) -> str:
    """Convert WCL's ``"Class-Spec"`` icon string to a ``"spec_class"`` slug.

    WCL playerDetails returns ``icon`` like ``"Warrior-Protection"``;
    simf's ``class_spec_display_name`` expects ``"protection_warrior"``.
    Returns "" when the icon is missing or malformed so the caller can fall
    back to a plain role label.
    """
    if not icon or "-" not in icon:
        return ""
    cls, _, spec = icon.partition("-")
    if not cls or not spec:
        return ""
    return f"{spec.lower()}_{cls.lower()}"


def fetch_player_details(
    report_code: str, fight_id: int, token: str, *, cache_dir: Path | None = None
) -> list[WCLPlayer]:
    """Return the players in a WCL fight grouped tank → healer → DPS.

    Matches the ordering used by the local-log party picker so the WCL flow
    reads the same as paste-a-log. Names are returned as the bare character
    name (no realm suffix) since that's what ``_fetch_actor_id`` matches on.

    Returns an empty list when WCL has no playerDetails for the fight (rare
    — old reports, trash-only pulls). Callers should fall back to a free
    text input in that case.

    ``cache_dir``: opt-in on-disk cache (see ``_gql``'s docstring) — a
    fight's player roster never changes, so this is zero-staleness-risk, same
    as ``fetch_report``. Found missing 2026-08-31 alongside the same gap in
    ``fetch_buff_windows`` (see that function's docstring) — this one is
    heavily called during WCL discovery scans (once per candidate fight), so
    the gap compounds rate-limit exposure on any large discovery pass.
    """
    query = """
    query($code: String!, $fightIDs: [Int]) {
      reportData {
        report(code: $code) {
          playerDetails(fightIDs: $fightIDs, translate: true)
        }
      }
    }
    """
    data = _gql(token, query, {"code": report_code, "fightIDs": [fight_id]}, cache_dir=cache_dir)
    raw = data["reportData"]["report"]["playerDetails"]
    if raw is None:
        return []
    # WCL sometimes wraps the JSON scalar as ``{"data": {"playerDetails":
    # {...}}}`` and sometimes returns the inner object directly. Unwrap
    # defensively so we tolerate either shape.
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], dict):
        inner = raw["data"].get("playerDetails", raw["data"])
    else:
        inner = raw
    if not isinstance(inner, dict):
        return []
    out: list[WCLPlayer] = []
    for role, key in (("tank", "tanks"), ("healer", "healers"), ("dps", "dps")):
        for p in inner.get(key) or []:
            name = (p.get("name") or "").strip()
            if not name:
                continue
            # WCL names are already bare; strip realm just in case a future
            # API revision changes that.
            raw_id = p.get("id")
            try:
                actor_id = int(raw_id) if raw_id is not None else None
            except (TypeError, ValueError):
                actor_id = None
            out.append(
                WCLPlayer(
                    name=name.split("-")[0],
                    role=role,
                    class_spec=_icon_to_class_spec_slug(p.get("icon") or ""),
                    actor_id=actor_id,
                )
            )
    return out


def fetch_combatant_info_events(
    report_code: str, fight_id: int, token: str, *, cache_dir: Path | None = None
) -> list[dict]:
    """Return the CombatantInfo events for a fight — one per player when ACL is on.

    Each event carries the player's **gear** (positional, WoW equip order),
    **exact stats** (strength/agility/stamina/armor + crit/haste/mastery/vers
    ratings) and **talents** (`talentTree[].id`) — the same payload a local
    COMBATANT_INFO line holds. This is the gear-and-stat-certain calibration
    source. Returns ``[]`` when the report has no combatant info (ACL off) —
    note WCL surfaces this here even when ``playerDetails.combatantInfo`` is
    empty, so this is the reliable path.

    ``cache_dir``: opt-in on-disk cache (see ``_gql``'s docstring).
    """
    query = """
    query($code: String!, $fightIDs: [Int]) {
      reportData {
        report(code: $code) {
          events(fightIDs: $fightIDs, dataType: CombatantInfo, limit: 100) { data }
        }
      }
    }
    """
    data = _gql(token, query, {"code": report_code, "fightIDs": [fight_id]}, cache_dir=cache_dir)
    events = (((data.get("reportData") or {}).get("report") or {}).get("events") or {}).get("data")
    return events if isinstance(events, list) else []


def _fetch_actor_id(
    report_code: str, target_name: str, token: str, *, cache_dir: Path | None = None
) -> int | None:
    """Return the WCL actor ID for the given character name.

    Used as a fallback when callers don't already have the actor ID
    (Other → free-text picker branch). Strips realm suffix on both sides
    of the comparison — WCL's masterData sometimes returns players as
    ``Somename-Faketown`` while the user typed ``Somename``, and the
    older equality match silently failed in that case.

    ``cache_dir``: opt-in on-disk cache (see ``_gql``'s docstring) — a report's
    masterData actor list never changes, so this is zero-staleness-risk.
    Every existing caller omits it and is unaffected.
    """
    query = """
    query($code: String!) {
      reportData {
        report(code: $code) {
          masterData(translate: true) {
            actors { id name type }
          }
        }
      }
    }
    """
    data = _gql(token, query, {"code": report_code}, cache_dir=cache_dir)
    actors = data["reportData"]["report"]["masterData"]["actors"]
    target_bare = target_name.split("-")[0].lower()
    target_full = target_name.lower()
    for actor in actors:
        actor_name = (actor.get("name") or "").lower()
        actor_bare = actor_name.split("-")[0]
        if actor_name in (target_full, target_bare) or actor_bare == target_bare:
            return int(actor["id"])
    return None


def fetch_damage_events(
    report: WCLReport,
    fight: WCLFight,
    target_name: str,
    token: str,
    *,
    target_actor_id: int | None = None,
    cache_dir: Path | None = None,
) -> list[DamageTakenEvent]:
    """Fetch all damage-taken events for target_name in the given fight.

    ``target_actor_id`` short-circuits the masterData lookup when the
    caller already has the ID (the picker path uses playerDetails which
    returns IDs directly). Falls back to a name lookup only when the ID
    is missing — covers the "Other (type a name)" escape hatch and the
    rare report with no playerDetails.

    ``cache_dir``: opt-in on-disk cache (see ``_gql``'s docstring) — each
    page is cached individually so a partially-fetched (e.g. interrupted)
    run resumes from where it left off instead of re-paying every page.
    """
    actor_id = target_actor_id
    if actor_id is None:
        actor_id = _fetch_actor_id(report.code, target_name, token, cache_dir=cache_dir)
    if actor_id is None:
        raise ValueError(
            f"Actor '{target_name}' not found in report {report.code}. "
            "Check the character name (without realm suffix)."
        )

    # Query shape verified empirically against the WCL v2 GraphQL API
    # (see docs/validation/wcl_events_probe_2026_05_26.md for the probe
    # session that pinned this).
    #
    #   dataType: DamageTaken    — events where someone took damage.
    #   hostilityType: Friendlies — the *target* of the damage is friendly.
    #     This is what we want; the default already is Friendlies, but we
    #     state it for clarity and as a regression pin.
    #   No targetID / sourceID  — WCL's targetID filter on `events` does
    #     NOT reliably restrict to a specific target. Empirical: passing
    #     targetID=<tank> returned 3 weird self-cast buff events instead
    #     of the tank's damage taken. We filter by target client-side.
    #   useActorIDs: false       — returns source/target as objects with
    #     name + id, so we can show real names ("Fervent Apothecary")
    #     instead of numeric IDs ("from 2") and filter by target.id.
    #   useAbilityIDs: false     — returns ability as {name, type}; lets
    #     us label spells instead of falling back to "auto-attack" for
    #     every hit.
    #   limit: 10000             — cuts pagination roundtrips on long runs.
    query = """
    query($code: String!, $fightID: Int!, $start: Float!, $end: Float!) {
      reportData {
        report(code: $code) {
          events(
            fightIDs: [$fightID]
            dataType: DamageTaken
            hostilityType: Friendlies
            startTime: $start
            endTime: $end
            translate: true
            useActorIDs: false
            useAbilityIDs: false
            limit: 10000
          ) {
            data
            nextPageTimestamp
          }
        }
      }
    }
    """

    # WCL event timestamps are ms relative to the report start.
    fight_start = fight.start_time_ms
    fight_end = fight.end_time_ms
    report_start_ms = report.start_time_ms

    all_events: list[DamageTakenEvent] = []
    page_start = float(fight_start)

    while True:
        data = _gql(
            token,
            query,
            {
                "code": report.code,
                "fightID": fight.id,
                "start": page_start,
                "end": float(fight_end),
            },
            cache_dir=cache_dir,
        )
        result = data["reportData"]["report"]["events"]
        raw_events = result.get("data") or []
        next_ts = result.get("nextPageTimestamp")

        for ev in raw_events:
            # Client-side target filter. With useActorIDs:false, target is
            # an object; legacy shape kept as a fallback for the existing
            # tests + the (unlikely) future toggle.
            if not _event_target_matches(ev, actor_id):
                continue
            evt = _map_event(ev, report_start_ms)
            if evt is not None:
                all_events.append(evt)

        if not next_ts or next_ts >= fight_end:
            break
        page_start = float(next_ts)

    return all_events


def _event_target_matches(ev: dict, target_actor_id: int) -> bool:
    """Return True when this WCL event targets the given actor.

    Handles both response shapes:
      - ``useActorIDs: false`` → ``ev["target"] = {"id": N, "name": ...}``
      - ``useActorIDs: true``  → ``ev["targetID"] = N``
    """
    target = ev.get("target")
    if isinstance(target, dict):
        return target.get("id") == target_actor_id
    raw = ev.get("targetID")
    return raw == target_actor_id


def fetch_buff_windows(
    report: WCLReport,
    fight: WCLFight,
    source_actor_id: int,
    token: str,
    *,
    ability_ids: set[int] | None = None,
    cache_dir: Path | None = None,
) -> dict[int, list[tuple[float, float]]]:
    """Return active windows (absolute seconds) per buff ability on the tank.

    Maps the fight's ``Buffs`` events for ``source_actor_id`` into
    ``{ability_guid: [(start_s, end_s), ...]}`` — the data needed to credit a
    window-gated mitigation layer (e.g. Vengeance Metamorphosis' armor
    multiplier, Painbringer) only while the buff was actually up. Times are
    absolute seconds (``report_start + ts``) — the same base
    ``DamageTakenEvent.time_s`` uses, so the replay adapter can stamp each
    damage event's ``active_buffs`` by a simple containment test.

    ``ability_ids`` filters to the buffs we care about (cheaper to build); when
    ``None`` every buff is returned. A buff already up at the pull (a
    ``removebuff`` with no prior ``applybuff``) is treated as active from fight
    start; a buff still up at the end is closed at fight end.

    ``cache_dir``: opt-in on-disk cache (see ``_gql``'s docstring). Until
    2026-08-31 this function silently ignored any ``--wcl-cache-dir`` a caller
    supplied — the same class of bug ``_fetch_actor_id`` had (fixed
    2026-08-30, see ``docs/validation/s2_cross_spec_keylevel_check_2026_08_30.md``)
    and every OTHER WCL-fetch function in this module already avoided. Every
    hero-talent/window-gated-mitigation replay (``wcl_to_replay_data`` calls
    this whenever ``buff_ability_ids`` is set — which every cross-player
    validation run does) always hit the live API here regardless of caching,
    which is a real contributor to WCL rate-limit exhaustion on any workload
    that runs many fights or many concurrent sessions against the same token
    (see ``docs/validation/s2_cross_spec_keylevel_check_round2_2026_08_31.md``).
    """
    query = """
    query($code: String!, $fightID: Int!, $start: Float!, $end: Float!, $sid: Int!) {
      reportData {
        report(code: $code) {
          events(
            fightIDs: [$fightID]
            dataType: Buffs
            sourceID: $sid
            startTime: $start
            endTime: $end
            useAbilityIDs: false
            limit: 10000
          ) {
            data
            nextPageTimestamp
          }
        }
      }
    }
    """
    report_start_ms = report.start_time_ms
    fight_start_s = (report_start_ms + fight.start_time_ms) / 1000.0
    fight_end_s = (report_start_ms + fight.end_time_ms) / 1000.0

    # Collect raw (time_s, ability_id, kind) transitions, then build windows.
    opens: dict[int, float] = {}
    windows: dict[int, list[tuple[float, float]]] = {}

    def _close(aid: int, end_s: float) -> None:
        start_s = opens.pop(aid, None)
        if start_s is None:
            start_s = fight_start_s  # up at pull → no prior applybuff
        windows.setdefault(aid, []).append((start_s, end_s))

    page_start = float(fight.start_time_ms)
    while True:
        data = _gql(
            token,
            query,
            {
                "code": report.code,
                "fightID": fight.id,
                "start": page_start,
                "end": float(fight.end_time_ms),
                "sid": source_actor_id,
            },
            cache_dir=cache_dir,
        )
        result = data["reportData"]["report"]["events"]
        for ev in result.get("data") or []:
            ability = ev.get("ability")
            aid = ability.get("guid") if isinstance(ability, dict) else ev.get("abilityGameID")
            if aid is None:
                continue
            try:
                aid = int(aid)
            except (TypeError, ValueError):
                continue
            if ability_ids is not None and aid not in ability_ids:
                continue
            t_s = (report_start_ms + ev.get("timestamp", 0)) / 1000.0
            etype = ev.get("type", "")
            if etype in ("applybuff", "refreshbuff"):
                opens.setdefault(aid, t_s)  # refresh keeps the existing open start
            elif etype == "removebuff":
                _close(aid, t_s)
            # stack events (applybuffstack/removebuffstack) don't change up/down
        next_ts = result.get("nextPageTimestamp")
        if not next_ts or next_ts >= fight.end_time_ms:
            break
        page_start = float(next_ts)

    # Close anything still open at fight end.
    for aid in list(opens):
        _close(aid, fight_end_s)
    return windows


def _spawn_key(actor_id, instance) -> str:
    """Spawn-unique identity for a WCL actor: ``f"{actor_id}:{instance}"``.

    The WCL analog of a creature GUID. A single NPC *type* shares ONE actor id
    across all its spawned copies; the per-event ``instance`` index
    distinguishes them. Stamped on both a damage event's source
    (``_map_event``) and a tank-applied debuff's target
    (``fetch_source_debuff_windows``) so the coverage join can match "did the
    *exact* mob that hit me carry this debuff?" rather than "did *any* mob of
    its type?". A missing instance defaults to ``1`` (WCL omits it for unique
    actors / occasionally for the first instance); using the SAME default on
    both sides keeps the keys consistent. Returns ``""`` when no actor id is
    present — an empty key matches no debuff window, so the join under-credits
    (never over-credits) rather than crashing.
    """
    try:
        aid = int(actor_id)
    except (TypeError, ValueError):
        return ""
    inst = instance if instance is not None else 1
    return f"{aid}:{inst}"


def fetch_source_debuff_windows(
    report: WCLReport,
    fight: WCLFight,
    source_actor_id: int,
    token: str,
    debuff_id: int,
    *,
    duration_s: float | None = None,
) -> dict[str, list[tuple[float, float]]]:
    """Per-enemy-spawn windows of a debuff the tank APPLIED to enemies.

    The WCL analog of ``combat_log.parse_source_debuff_windows``. Returns
    ``{spawn_key: [(start_s, end_s), ...]}`` where ``spawn_key`` is
    ``_spawn_key(target)`` — the SAME identity ``_map_event`` stamps on
    ``DamageTakenEvent.source_guid`` — so the coverage join asks "did the exact
    mob that hit me have this debuff up?" spawn-precisely, never blanket. Used
    for Demoralizing Shout (1160): a −20% enemy debuff only "covers" a hit if
    that hit's own source carried it.

    Query shape pinned empirically (see
    ``docs/validation/wcl_source_debuff_probe_2026_06_29.md``):

      - ``dataType: Debuffs`` + ``hostilityType: Enemies`` → debuffs ON enemies
        (the default Friendlies returns debuffs ON the tank, the wrong side).
      - ``abilityID: debuff_id`` server-filters to the one spell.
      - NO ``sourceID``: the source filter is BROKEN for aura dataTypes
        (returns 0 events — the same class of quirk the damage path documents
        for ``targetID``), so we filter ``source.id == source_actor_id``
        CLIENT-side to credit only the TANK's application (not, say, an Arms
        warrior's).
      - ``useActorIDs: false`` → ``target: {id}`` + the sibling
        ``targetInstance`` that forms the enemy spawn key.

    ``duration_s`` caps a window left open when a debuffed mob dies without a
    ``removedebuff`` at ``open + duration_s`` (matches the local path; using the
    original open under-states a refreshed window, the safe direction). A
    ``removedebuff`` with no matching open is skipped — never fabricate a window,
    so coverage can only under-credit.
    """
    # NOTE: WCL types the `abilityID` events arg as Float (not Int) — passing an
    # Int!-typed variable is rejected ("used in position expecting type Float").
    query = """
    query($code: String!, $fightID: Int!, $start: Float!, $end: Float!, $aid: Float!) {
      reportData {
        report(code: $code) {
          events(
            fightIDs: [$fightID]
            dataType: Debuffs
            hostilityType: Enemies
            abilityID: $aid
            startTime: $start
            endTime: $end
            useActorIDs: false
            useAbilityIDs: false
            limit: 10000
          ) {
            data
            nextPageTimestamp
          }
        }
      }
    }
    """
    report_start_ms = report.start_time_ms
    fight_end_s = (report_start_ms + fight.end_time_ms) / 1000.0

    opens: dict[str, float] = {}
    windows: dict[str, list[tuple[float, float]]] = {}

    page_start = float(fight.start_time_ms)
    while True:
        data = _gql(
            token,
            query,
            {
                "code": report.code,
                "fightID": fight.id,
                "start": page_start,
                "end": float(fight.end_time_ms),
                "aid": float(debuff_id),
            },
        )
        result = data["reportData"]["report"]["events"]
        for ev in result.get("data") or []:
            # Credit only the TANK's application (the source filter is broken
            # server-side for this dataType — filter client-side instead).
            src = ev.get("source")
            src_id = src.get("id") if isinstance(src, dict) else ev.get("sourceID")
            if src_id != source_actor_id:
                continue
            tgt = ev.get("target")
            tgt_id = tgt.get("id") if isinstance(tgt, dict) else ev.get("targetID")
            key = _spawn_key(tgt_id, ev.get("targetInstance"))
            if not key:
                continue
            t_s = (report_start_ms + ev.get("timestamp", 0)) / 1000.0
            etype = ev.get("type", "")
            if etype in ("applydebuff", "refreshdebuff"):
                opens.setdefault(key, t_s)  # refresh keeps the existing open start
            elif etype == "removedebuff":
                start_s = opens.pop(key, None)
                if start_s is not None:  # never fabricate a window
                    windows.setdefault(key, []).append((start_s, t_s))
            # stack events (applydebuffstack/removedebuffstack) don't move up/down
        next_ts = result.get("nextPageTimestamp")
        if not next_ts or next_ts >= fight.end_time_ms:
            break
        page_start = float(next_ts)

    # Close anything still open: prefer the debuff-duration cap (a mob that died
    # without a removedebuff), else fight end.
    for key, start_s in opens.items():
        close = start_s + duration_s if duration_s is not None else fight_end_s
        windows.setdefault(key, []).append((start_s, close))
    return windows


def _extract_stopped_spell_id(ev: dict) -> int | None:
    """Pull the interrupted spell's ID off a WCL ``Interrupts``-dataType event.

    Confirmed against a real live payload (2026-07-03, public rankings report
    ``ExampleCode6666666`` fight 8, Algeth'ar Academy +22): a real interrupt event
    is flat-shaped, e.g. ``{"type": "interrupt", "sourceID": 78, "targetID":
    109, "abilityGameID": 47528, "extraAbilityGameID": 396640}`` —
    ``abilityGameID`` is the interrupting ability (Mind Freeze), and
    ``extraAbilityGameID`` is the spell that got interrupted, the one we want.
    WCL's own scripting-API docs (``warcraftlogs.com/help/pins``) name this
    concept ``stoppedAbility``, which turned out to be a scripting-API-only
    accessor name, not the raw GraphQL field — kept below as a defensive
    fallback in case a future WCL schema revision nests it, but
    ``extraAbilityGameID`` is the confirmed primary. An unrecognized shape
    returns ``None`` and the event is silently dropped — same fail-safe
    "under-show, never lie" rule every other lever in the coaching module
    follows (see ``core/coaching.py``), so a wrong guess here costs coverage,
    never fabricates it.
    """
    candidates = [ev.get("extraAbilityGameID")]
    stopped = ev.get("stoppedAbility")
    candidates.append(stopped.get("guid") if isinstance(stopped, dict) else stopped)
    extra = ev.get("extraAbility")
    candidates.append(extra.get("guid") if isinstance(extra, dict) else extra)
    for val in candidates:
        if val is None:
            continue
        try:
            return int(val)
        except (TypeError, ValueError):
            continue
    return None


def fetch_interrupted_spell_ids(
    report: WCLReport,
    fight: WCLFight,
    token: str,
) -> frozenset[int]:
    """Spell IDs proven interruptible by a real interrupt anywhere in this fight.

    The WCL analog of ``combat_log.parse_interrupted_spell_ids``: deliberately
    party-wide (no source filter) — an interrupt by ANY player proves the
    spell is interruptible, whether or not the tank was the one who kicked
    it, matching the local-log parser's scope exactly. Evidence-only, same
    honesty rule as the local path: a spell never actually interrupted in
    this fight is silently not flagged, even if some other spell of the same
    kind theoretically could be.
    """
    query = """
    query($code: String!, $fightID: Int!, $start: Float!, $end: Float!) {
      reportData {
        report(code: $code) {
          events(
            fightIDs: [$fightID]
            dataType: Interrupts
            startTime: $start
            endTime: $end
            useAbilityIDs: false
            limit: 10000
          ) {
            data
            nextPageTimestamp
          }
        }
      }
    }
    """
    interrupted: set[int] = set()
    page_start = float(fight.start_time_ms)
    while True:
        data = _gql(
            token,
            query,
            {
                "code": report.code,
                "fightID": fight.id,
                "start": page_start,
                "end": float(fight.end_time_ms),
            },
        )
        result = data["reportData"]["report"]["events"]
        for ev in result.get("data") or []:
            sid = _extract_stopped_spell_id(ev)
            if sid is not None:
                interrupted.add(sid)
        next_ts = result.get("nextPageTimestamp")
        if not next_ts or next_ts >= fight.end_time_ms:
            break
        page_start = float(next_ts)
    return frozenset(interrupted)


def fetch_own_cast_times(
    report: WCLReport,
    fight: WCLFight,
    source_actor_id: int,
    ability_id: int,
    token: str,
) -> tuple[float, ...]:
    """Absolute-second timestamps this actor cast a specific ability.

    The WCL analog of ``combat_log.parse_cast_events``, used for the
    cooldown-aware kick-availability split: unlike ``fetch_interrupted_spell_ids``
    (party-wide evidence), this IS filtered to one actor — it answers "when
    did *this tank* personally cast their interrupt," not "did anyone."

    Confirmed live (2026-07-03) against a real report: ``dataType: Casts``
    with ``sourceID`` + ``abilityID`` server-filters to exactly the matching
    casts, so no client-side filtering is needed here (unlike the Debuffs
    dataType, where the source filter is documented as broken).
    """
    query = """
    query($code: String!, $fightID: Int!, $start: Float!, $end: Float!, $sid: Int!, $aid: Float!) {
      reportData {
        report(code: $code) {
          events(
            fightIDs: [$fightID]
            dataType: Casts
            sourceID: $sid
            abilityID: $aid
            startTime: $start
            endTime: $end
            limit: 10000
          ) {
            data
            nextPageTimestamp
          }
        }
      }
    }
    """
    report_start_ms = report.start_time_ms
    times: list[float] = []
    page_start = float(fight.start_time_ms)
    while True:
        data = _gql(
            token,
            query,
            {
                "code": report.code,
                "fightID": fight.id,
                "start": page_start,
                "end": float(fight.end_time_ms),
                "sid": source_actor_id,
                "aid": float(ability_id),
            },
        )
        result = data["reportData"]["report"]["events"]
        for ev in result.get("data") or []:
            ts_ms = ev.get("timestamp")
            if ts_ms is None:
                continue
            times.append((report_start_ms + ts_ms) / 1000.0)
        next_ts = result.get("nextPageTimestamp")
        if not next_ts or next_ts >= fight.end_time_ms:
            break
        page_start = float(next_ts)
    return tuple(times)


def _map_event(ev: dict, report_start_ms: int) -> DamageTakenEvent | None:
    """Map a WCL damage event dict to a DamageTakenEvent.

    Tolerates both response shapes:
      - ``useActorIDs: false`` + ``useAbilityIDs: false`` (production):
        ``source: {name, id, ...}``, ``target: {name, id, ...}``,
        ``ability: {name, type, guid, abilityIcon}``.
      - Legacy flat shape kept for the unit-test fixtures and as a
        fallback should the upstream defaults ever change again:
        ``sourceID``, ``sourceName``, ``ability: {name, type}`` or
        ``abilityGameID``.
    """
    ev_type = ev.get("type", "")
    if ev_type != "damage":
        return None

    ts_ms = ev.get("timestamp", 0)
    time_s = (report_start_ms + ts_ms) / 1000.0

    ability = ev.get("ability")
    if isinstance(ability, dict):
        spell_name = ability.get("name") or "auto-attack"
        school_flags = ability.get("type", 1)
        # `ability.guid` is the in-game spell ID — same numbering Wowhead
        # uses, so we can hand it straight to `wowhead_spell_url`. Auto-
        # attacks come back as `abilityGameID: 1` (which IS the canonical
        # auto-attack spell on Wowhead — leave it so the link still resolves).
        raw_spell_id = ability.get("guid")
    else:
        spell_name = "auto-attack"
        school_flags = 1
        raw_spell_id = ev.get("abilityGameID")
    try:
        spell_id: int | None = int(raw_spell_id) if raw_spell_id is not None else None
    except (TypeError, ValueError):
        spell_id = None
    school = school_name(int(school_flags))

    # Infer event type from hit type and ability name
    hit_type = ev.get("hitType", _HIT_NORMAL)
    attack_type_raw = ev.get("attackType", "")
    if attack_type_raw == "Melee" or spell_name.lower() in ("melee", "auto attack", "auto-attack"):
        event_type = "SWING_DAMAGE"
    else:
        event_type = "SPELL_DAMAGE"

    amount = int(ev.get("amount", 0))
    # unmitigatedAmount = raw before armor/DR; may be absent on old logs
    base_amount = int(ev.get("unmitigatedAmount") or ev.get("amount", 0))
    overkill = int(ev.get("overkill") or 0)
    blocked = int(ev.get("blocked") or 0)
    absorbed = int(ev.get("absorbed") or 0)
    resisted = int(ev.get("resisted") or 0)
    is_critical = hit_type == _HIT_CRIT
    is_glancing = hit_type == _HIT_GLANCE

    source = ev.get("source")
    npc_id: int | None = None
    source_actor_id = None
    if isinstance(source, dict):
        source_name = source.get("name") or str(source.get("id") or 0)
        source_actor_id = source.get("id")
        # WCL's `source.guid` for an NPC IS the in-game NPC ID — matches
        # `npc_id_from_guid` output for the local-log path. Only flag NPCs
        # / bosses so we don't accidentally Wowhead-link friendly pets.
        if (source.get("type") or "").lower() in ("npc", "boss"):
            try:
                guid_val = source.get("guid")
                npc_id = int(guid_val) if guid_val is not None else None
            except (TypeError, ValueError):
                npc_id = None
    else:
        source_actor_id = ev.get("sourceID")
        source_name = ev.get("sourceName") or str(source_actor_id or 0)

    # Spawn-unique source key (WCL actor id + per-event instance) — the WCL
    # analog of a creature GUID. Powers the debuff-coverage join in
    # `core.coaching`: a hit is credited a tank-applied enemy debuff
    # (Demoralizing Shout) ONLY if the exact spawn that dealt it carried the
    # debuff. `sourceInstance` is a sibling top-level field on the raw event
    # (present with useActorIDs both true and false — verified empirically).
    source_guid = _spawn_key(source_actor_id, ev.get("sourceInstance"))

    # WCL attaches ``tick: true`` to periodic-damage events on the raw
    # JSON. We don't widen ``event_type`` to ``SPELL_PERIODIC_DAMAGE``
    # (would ripple through the rest of the WCL UI surface) — instead we
    # pass the boolean through ``tick_flag`` so the K-calibration adapter
    # can drive ``is_dot_tick`` off it. Defensive ``bool(...)`` so a
    # missing or ``None`` value yields ``False`` and the calibrator still
    # runs (DoT-tick classification is a hint, not math-affecting).
    #
    # Live-probe 2026-05-27 (report ExampleCode5555555 fight 1, Brutoh on
    # Nexus-Point Xenas +13): event union of keys carried exactly the
    # ``tick`` field on 349/1650 Brutoh-targeted damage events (21%).
    # ``tick: false`` was never present explicitly (absent = false).
    # Alternative spellings (``isTick``, ``periodic``, ``isPeriodic``,
    # ``attackType``) were absent across all 1650 events. The tick=true
    # set covered exactly the abilities one would expect for periodic
    # damage (Searing Rend, Arcing Mana, Sparkburn, Radiant Scar, etc.),
    # so this read is confirmed correct.
    tick_flag = bool(ev.get("tick"))

    return DamageTakenEvent(
        time_s=time_s,
        event_type=event_type,
        source_name=source_name,
        spell_name=spell_name,
        school=school,
        amount=amount,
        base_amount=base_amount,
        overkill=overkill,
        blocked=blocked,
        absorbed=absorbed,
        resisted=resisted,
        is_critical=is_critical,
        is_glancing=is_glancing,
        spell_id=spell_id,
        source_guid=source_guid,
        source_npc_id=npc_id,
        tick_flag=tick_flag,
    )


def fetch_dungeon_pulls(
    report: WCLReport,
    fight: WCLFight,
    token: str,
) -> list[EncounterWindow]:
    """Return the boss-pull encounter windows inside a WCL M+ fight.

    WCL exposes M+ run substructure via ``fight.dungeonPulls`` — one entry per
    "pull" (a boss attempt OR a trash pack), each with absolute timestamps
    relative to report start, ``encounterID`` (>0 for boss pulls, 0 for trash),
    and ``kill``. We map the boss-pull entries (``encounterID != 0``) to the
    ``EncounterWindow`` shape the local-log path produces from
    ``ENCOUNTER_START`` / ``ENCOUNTER_END`` events. ``segment_run`` then slices
    the run into boss + trash segments exactly as it does for local logs, and
    the Why-died renderer produces the same boss-vs-trash card hierarchy on
    both sources.

    Returns an empty list when ``dungeonPulls`` is null (non-M+ fights — raids,
    incomplete reports) or when no boss pulls exist (trash-only sliver of a
    run). The empty case falls back to the whole-run trash segment, which is
    correct for those shapes.

    Probed against report ``ExampleCode4444444`` fight=19 (Algeth'ar Academy +19):
    4 boss pulls (Overgrown Ancient, Crawth, Vexamus, Echo of Doragosa) +
    12 trash pulls map to 4 boss EncounterWindows; ``segment_run`` then
    collapses the trash pulls between bosses into one trash segment per gap
    (4 trash segments end-to-end), matching the local-log path's hierarchy.
    """
    query = """
    query($code: String!, $fightID: Int!) {
      reportData {
        report(code: $code) {
          fights(fightIDs: [$fightID], translate: true) {
            dungeonPulls {
              id
              name
              encounterID
              kill
              startTime
              endTime
            }
          }
        }
      }
    }
    """
    data = _gql(token, query, {"code": report.code, "fightID": fight.id})
    fights = data["reportData"]["report"].get("fights") or []
    if not fights:
        return []
    pulls = fights[0].get("dungeonPulls") or []
    encounters: list[EncounterWindow] = []
    # Count attempt index per encounter_id so multi-pull bosses (e.g. wipe +
    # re-pull) get attempt_index=1, 2, ... matching local-log semantics.
    seen: dict[int, int] = {}
    for p in pulls:
        try:
            enc_id = int(p.get("encounterID") or 0)
        except (TypeError, ValueError):
            enc_id = 0
        if enc_id == 0:
            continue  # trash pull
        try:
            start_ms_rel = float(p.get("startTime"))
            end_ms_rel = float(p.get("endTime"))
        except (TypeError, ValueError):
            continue
        # dungeonPulls timestamps are ms relative to report start, same scale
        # as fight.start_time_ms.
        start_s = (report.start_time_ms + start_ms_rel) / 1000.0
        end_s = (report.start_time_ms + end_ms_rel) / 1000.0
        attempt = seen.get(enc_id, 0) + 1
        seen[enc_id] = attempt
        encounters.append(
            EncounterWindow(
                encounter_id=enc_id,
                name=(p.get("name") or "Unknown").strip(),
                start_time_s=start_s,
                end_time_s=end_s,
                success=bool(p.get("kill")),
                attempt_index=attempt,
            )
        )
    return encounters


def fight_to_run(fight: WCLFight, report_start_ms: int) -> ChallengeModeRun:
    """Convert a WCLFight to a ChallengeModeRun for use with existing analysis code."""
    start_s = (report_start_ms + fight.start_time_ms) / 1000.0
    end_s = (report_start_ms + fight.end_time_ms) / 1000.0
    return ChallengeModeRun(
        map_id=0,
        map_name=fight.name,
        key_level=fight.key_level or 0,
        affixes=fight.affixes,
        start_time_s=start_s,
        end_time_s=end_s,
        success=fight.completed,
        duration_ms=fight.end_time_ms - fight.start_time_ms,
    )
