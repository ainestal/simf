"""
Blizzard Game Data API — item stat resolver with disk cache.

Credentials (in priority order):
  1. Env vars BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET
  2. ~/.simf/blizzard.yaml  (keys: client_id, client_secret)

All network calls have a 10-second timeout. Every exception is caught and
returns None / empty — network errors must never crash the UI.
"""

import contextlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import requests
import yaml

# Rolling-window limiter shared with the other public-mode fetch paths (WCL,
# Raider.IO). core.wcl_budget is stdlib-only and imports nothing from simf.io,
# so this is not a cycle.
from simf.core import wcl_budget

# ---------------------------------------------------------------------------
# Internal paths
# ---------------------------------------------------------------------------

_SIMF_DIR = Path.home() / ".simf"
_TOKEN_FILE = _SIMF_DIR / "blizzard_token.json"
_CACHE_DIR = _SIMF_DIR / "item_cache"
_CREDS_FILE = _SIMF_DIR / "blizzard.yaml"

# Committed read-only seed entries for items the bundled demo character
# needs — a cold environment (CI runner, fresh deploy) must never depend
# on ~35 live Wowhead round-trips to load the demo (the AppTest demo-load
# tests timed out in CI when the 2026-06-10 demo refresh pushed a cold
# first paint past the 30 s harness budget). Misses still fall through to
# the network; writes always go to _CACHE_DIR.
_SEED_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "item_cache_seed"

_CACHE_TTL = 30 * 24 * 3600  # 30 days in seconds
_TOKEN_GRACE = 60  # re-fetch token this many seconds before expiry

# Bound the on-disk item cache: a stranger's /simc paste could otherwise drive
# thousands of distinct item-id writes (SD-card / inode exhaustion). The public
# box additionally runs OFFLINE (see _is_offline), but the owner's own browsing
# benefits from a cap too. Mirrors marginals_cache._CACHE_CAP (mtime eviction).
_ITEM_CACHE_CAP = 1024

# Blizzard stat type → our stat key
_STAT_MAP = {
    "STAMINA": "stamina",
    "STRENGTH": "strength",
    "AGILITY": "agility",
    "INTELLECT": "intellect",
    "ARMOR": "armor_from_gear",
    "HASTE_RATING": "haste_rating",
    "CRIT_RATING": "crit_rating",
    "MASTERY": "mastery_rating",
    "VERSATILITY": "versatility_rating",
}


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _load_credentials() -> tuple[str, str] | tuple[None, None]:
    client_id = os.environ.get("BLIZZARD_CLIENT_ID")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret

    if _CREDS_FILE.exists():
        try:
            with open(_CREDS_FILE) as f:
                cfg = yaml.safe_load(f) or {}
            client_id = cfg.get("client_id")
            client_secret = cfg.get("client_secret")
            if client_id and client_secret:
                return str(client_id), str(client_secret)
        except Exception:  # noqa: S110 — best-effort fallback
            pass

    return None, None


def is_configured() -> bool:
    """True if Blizzard credentials are available."""
    cid, _ = _load_credentials()
    return cid is not None


# A single flaky attempt (dropped connection, a momentary 5xx/429, a DNS
# hiccup) must not sink an otherwise-working item lookup — resolving a
# character's ~16-20 equipped items each does one independent request, and
# `resolve_equipped_stats` only surfaces a hard failure to the user when
# EVERY one of them comes back empty, so a transient blip mid-batch used to
# be enough to trip "Couldn't fill in your stats" for real gear that WAS
# resolvable a moment later.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 0.2  # seconds; attempt N (0-indexed) waits _RETRY_BACKOFF * (N+1)


def _with_retries(fn, *args, **kwargs) -> "requests.Response | None":
    """Call `fn` (``requests.get``/``requests.post``) with a couple of
    retries. `fn` is passed in by the caller (not looked up by name in here)
    so tests that monkeypatch ``requests.get``/``requests.post`` directly
    keep working unchanged — the mock IS `fn`, resolved at the call site."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = fn(*args, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception:
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
    return None


# ---------------------------------------------------------------------------
# Auth token (cached to disk)
# ---------------------------------------------------------------------------


def _load_cached_token() -> str | None:
    if not _TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(_TOKEN_FILE.read_text())
        # Re-use if more than _TOKEN_GRACE seconds remain
        if data.get("expires_at", 0) - time.time() > _TOKEN_GRACE:
            return data["access_token"]
    except Exception:  # noqa: S110 — best-effort fallback
        pass
    return None


def _save_token(token: str, expires_in: int) -> None:
    try:
        _SIMF_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(
            json.dumps(
                {
                    "access_token": token,
                    "expires_at": time.time() + expires_in,
                }
            )
        )
    except Exception:  # noqa: S110 — best-effort fallback
        pass


def _get_token(region: str) -> str | None:
    cached = _load_cached_token()
    if cached:
        return cached

    client_id, client_secret = _load_credentials()
    if not client_id or not client_secret:
        return None

    resp = _with_retries(
        requests.post,
        f"https://{region}.battle.net/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10,
    )
    if resp is None:
        return None
    try:
        payload = resp.json()
        token = payload["access_token"]
        _save_token(token, payload.get("expires_in", 86400))
        return token
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Item cache helpers
# ---------------------------------------------------------------------------


def _cache_path(item_id: int) -> Path:
    return _CACHE_DIR / f"{item_id}.json"


def _load_cached_item(item_id: int) -> dict | None:
    path = _cache_path(item_id)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if time.time() - data.get("cached_at", 0) < _CACHE_TTL:
                return data["stats"]
        except Exception:  # noqa: S110 — best-effort fallback
            pass
    return _load_seed(f"{item_id}.json")


def _load_seed(filename: str) -> dict | None:
    """Read-only committed seed entry — no TTL; curated data, not a cache."""
    path = _SEED_CACHE_DIR / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())["stats"]
    except Exception:
        return None


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_public_mode() -> bool:
    """True on the public deploy (`SIMF_PUBLIC=1`)."""
    return _env_flag("SIMF_PUBLIC")


def _hard_offline() -> bool:
    """`SIMF_ITEM_DB_OFFLINE` explicitly set → make NO live item fetch at all,
    Wowhead included. The kill switch: set it on the public unit if Wowhead
    ever throttles the box, and every lookup degrades to seed/cache hits
    exactly as it did before budgeted lookups existed (2026-08-23). Unset (the
    normal case) leaves public-mode Wowhead lookups budget-gated rather than
    blocked — see `_live_wowhead_allowed`."""
    return os.environ.get("SIMF_ITEM_DB_OFFLINE") is not None and _env_flag("SIMF_ITEM_DB_OFFLINE")


def _is_offline() -> bool:
    """True when the OWNER-CREDENTIALED Blizzard endpoints must not be called.

    A stranger's /simc paste fans out one request per item, so on the public
    box an anonymous visitor must never be able to spend the owner's OAuth
    budget (`fetch_item_stats`, `search_items`) — those stay hard-blocked in
    SIMF_PUBLIC mode, resolving from the committed seed + existing disk cache
    only, with misses degrading to "stats unavailable".

    Wowhead's zero-auth XML tooltip endpoint is NO LONGER gated by this
    (2026-08-23): blocking it made the public box structurally incapable of
    resolving any gear it hadn't already cached — the seed's cache keys are
    bonus-id-specific, so a stranger's items can never hit it, and both
    headline flows (paste your /simc, look up your name) returned zero stats
    for everyone but the demo character. Those lookups are budget-gated
    instead; see `_live_wowhead_allowed`. `SIMF_ITEM_DB_OFFLINE` still
    overrides both, for tests and as the public kill switch."""
    val = os.environ.get("SIMF_ITEM_DB_OFFLINE")
    if val is not None:
        return _env_flag("SIMF_ITEM_DB_OFFLINE")
    return _is_public_mode()


# Thread-local depth counter for an active burst lease (see
# `live_lookup_lease`). Streamlit serves each session on its own thread, so a
# lease can't leak across visitors.
_LIVE_LEASE = threading.local()


def _lease_depth() -> int:
    return int(getattr(_LIVE_LEASE, "depth", 0))


def _live_wowhead_allowed() -> bool:
    """Admit one live Wowhead lookup, spending public-mode budget if needed.

    Not a pure predicate: on the public box a `True` answer RECORDS the
    request against the rolling window (`wcl_budget.item_lookup_guard`), so
    call it exactly once per would-be request, after the cache miss. Inside a
    burst lease the window was already paid up front, so the lease admits
    without double-charging. The owner's own box is unmetered."""
    if _hard_offline():
        return False
    if not _is_public_mode():
        return True
    if _lease_depth() > 0:
        return True
    allowed, _retry = wcl_budget.item_lookup_guard().check()
    return allowed


@contextlib.contextmanager
def live_lookup_lease(n_fetches: int) -> Iterator[bool]:
    """Reserve `n_fetches` live Wowhead lookups up front for one user action.

    Yields True when the burst is granted (or when there's nothing to meter —
    owner mode), False when the public box's window can't cover it, in which
    case NOTHING was reserved and the caller should report that it couldn't
    resolve rather than resolve part of the set.

    Why all-or-nothing: a character load needs every equipped slot to produce
    a correct stat total. Metering it per request could admit 12 of 17 item
    lookups and hand back a character whose stamina is silently ~30% short —
    a plausible-looking wrong number, the one failure mode this project
    treats as worse than an honest error (cf. `SimcLoadOk.stats_estimated`).
    Re-entrant: a nested lease rides the outer reservation."""
    if _hard_offline():
        yield False
        return
    if not _is_public_mode() or _lease_depth() > 0:
        _LIVE_LEASE.depth = _lease_depth() + 1
        try:
            yield True
        finally:
            _LIVE_LEASE.depth = _lease_depth() - 1
        return
    allowed, _retry = wcl_budget.item_lookup_guard().check(max(1, int(n_fetches)))
    if not allowed:
        yield False
        return
    _LIVE_LEASE.depth = _lease_depth() + 1
    try:
        yield True
    finally:
        _LIVE_LEASE.depth = _lease_depth() - 1


# The public box's blanket "never touch the network" gate was first narrowed on
# 2026-08-22 (PR #487) with a fixed 40-req/minute counter exempting
# `fetch_item_stats_wowhead` only. That diagnosis was right and this keeps its
# intent; the mechanism is now `_live_wowhead_allowed` /
# `live_lookup_lease` below, which adds four things the counter couldn't:
#   * a ROLLING window plus an hourly cap (`wcl_budget.item_lookup_guard`) —
#     a fixed 60 s bucket admits 2× the cap across a boundary, and nothing
#     bounded the day;
#   * an atomic per-load reservation, so a character's stat total is never
#     silently short a few slots (see `live_lookup_lease`);
#   * the icon/socket/two-hand lookups, which fan out per rendered card —
#     the icon one was making UNCAPPED public requests, so "offline" was
#     never actually offline;
#   * a working `SIMF_ITEM_DB_OFFLINE` kill switch: the counter checked
#     `_is_offline()` and then overrode it, which left the operator no way to
#     stop live stat fetches on a box that was misbehaving.
# Blizzard's owner-credentialed endpoints stay fully blocked in public mode,
# exactly as PR #487 left them.


def _prune_cache_dir(cap: int | None = None) -> None:
    """Keep the live item cache under `cap` files, evicting oldest by mtime.
    Resolves the cap at call time (not as a default arg) so the module-level
    `_ITEM_CACHE_CAP` stays overridable. Best-effort; the seed dir is never
    touched (it's read-only curated data)."""
    cap = _ITEM_CACHE_CAP if cap is None else cap
    try:
        files = sorted(_CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for stale in files[:-cap] if len(files) > cap else []:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def _save_cached_item(item_id: int, stats: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(item_id).write_text(
            json.dumps(
                {
                    "stats": stats,
                    "cached_at": time.time(),
                }
            )
        )
        _prune_cache_dir()
    except Exception:  # noqa: S110 — best-effort fallback
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_item_stats(item_id: int, region: str = "eu") -> dict[str, int] | None:
    """
    Fetch base item stats. Returns stat dict or None if unavailable/unconfigured.
    Stats are BASE item stats (no bonus_id adjustments) — label as approximate in UI.
    Results cached to disk.
    """
    if not item_id:
        return None

    cached = _load_cached_item(item_id)
    if cached is not None:
        return cached

    if _is_offline():
        # Owner-credentialed endpoint: never spend the owner's OAuth budget on
        # a stranger's paste. Public box gets seed/cache hits only.
        return None

    token = _get_token(region)
    if not token:
        return None

    resp = _with_retries(
        requests.get,
        f"https://{region}.api.blizzard.com/data/wow/item/{item_id}",
        params={
            "namespace": f"static-{region}.battle.net",
            "locale": "en_US",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp is None:
        return None
    try:
        data = resp.json()
        raw_stats = data["preview_item"]["stats"]
    except (KeyError, TypeError):
        return None

    stats: dict[str, int] = {}
    for entry in raw_stats:
        try:
            # stat type is nested: {"type": {"type": "STAMINA", ...}, "value": 123}
            stat_type = entry["type"]["type"]
            value = int(entry["value"])
            key = _STAT_MAP.get(stat_type)
            if key:
                stats[key] = stats.get(key, 0) + value
        except (KeyError, TypeError, ValueError):
            continue

    _save_cached_item(item_id, stats)
    return stats


# Specs whose primary stat is Agility; everyone else is Strength. Mirrors
# optimizer.gem_suggester._AGI_SPECS — duplicated (not imported) because io/
# is a lower layer than optimizer/ and importing it here would create a
# circular optimizer→io→optimizer dependency. Kept in sync deliberately, same
# convention as ui/helpers/simc_load.py's own local copy.
_AGI_SPECS = frozenset({"brewmaster_monk", "guardian_druid", "vengeance_demon_hunter"})

# Placeholder key `name_map` resolves the ambiguous agi/str hybrid tooltip
# labels to. Never a real stat — always resolved away by `_resolve_hybrid_primary`
# before a caller sees the dict, so it can't leak into an eHP marginal lookup.
_HYBRID_PRIMARY_KEY = "_hybrid_primary_agi_str"

# Wowhead rtg<N> ids for the four secondary ratings, empirically confirmed
# 2026-07-11 by fetching live crafted-item tooltips against known
# `crafted_stats=` pairs from real exports (e.g. item=237834 bonus 12214:...
# crafted_stats=32/36 rendered as rtg36 Haste + rtg40 Versatility — Wowhead's
# own default pair, NOT the 32/36 crit+haste the crafter actually chose —
# proving both that Wowhead ignores the real choice and that 32=crit,
# 36=haste, 40=versatility; a third fetch of a 49/36 item rendered rtg49
# Mastery + rtg36 Haste, confirming 49=mastery).
_CRAFTED_STAT_ID_MAP = {
    32: "crit_rating",
    36: "haste_rating",
    40: "versatility_rating",
    49: "mastery_rating",
}

_SECONDARY_RATING_KEYS = frozenset(_CRAFTED_STAT_ID_MAP.values())


def _remap_crafted_secondary_stats(
    stats: dict[str, int], crafted_stats: list[int] | None
) -> dict[str, int]:
    """Relabel a crafted item's secondary-stat pair to the crafter's real
    choice.

    Wowhead's XML tooltip endpoint ignores any crafted-stats URL parameter and
    always renders whatever pair is the bonus set's own DEFAULT — confirmed
    empirically against real exports, not merely suspected (see
    `_CRAFTED_STAT_ID_MAP`'s docstring). The two numeric magnitudes it returns
    are still correct (crafted items split their secondary budget evenly
    across the two chosen stats), so the fix is a pure relabel: take whichever
    two secondary-rating values Wowhead parsed and refile them under the
    stat keys `crafted_stats` actually names, discarding Wowhead's own labels.

    No-ops (returns `stats` unchanged) unless there are exactly two crafted
    stat ids AND exactly two parsed secondary-rating values — a malformed or
    partial input must never silently corrupt an ordinary item's stats.
    """
    if not crafted_stats or len(crafted_stats) != 2:
        return stats
    target_keys_raw = [_CRAFTED_STAT_ID_MAP.get(i) for i in crafted_stats]
    if None in target_keys_raw or len(set(target_keys_raw)) != 2:
        return stats
    target_keys: list[str] = [k for k in target_keys_raw if k is not None]
    present = {k: v for k, v in stats.items() if k in _SECONDARY_RATING_KEYS}
    if len(present) != 2:
        return stats
    if set(present) == set(target_keys):
        return stats  # Wowhead's default pair happens to match the real choice
    remapped = {k: v for k, v in stats.items() if k not in present}
    for key, val in zip(target_keys, present.values(), strict=True):
        remapped[key] = remapped.get(key, 0) + val
    return remapped


def _resolve_hybrid_primary(stats: dict[str, int], class_spec: str | None) -> dict[str, int]:
    """Resolve the ambiguous agi/str hybrid-label placeholder to the real
    primary stat for `class_spec` (agility for Guardian/Brewmaster/VDH,
    strength for everyone else) — mirrors
    `optimizer.gem_suggester.resolve_gem_stats`'s `primary` → agi/str
    translation, which the gem path already had and the item path didn't.

    `class_spec=None` (a caller that hasn't threaded the character's spec
    through yet) resolves to strength — the same value every caller got before
    this function existed, so an un-migrated call site doesn't regress.
    """
    if _HYBRID_PRIMARY_KEY not in stats:
        return stats
    resolved = dict(stats)
    val = resolved.pop(_HYBRID_PRIMARY_KEY)
    key = "agility" if class_spec in _AGI_SPECS else "strength"
    resolved[key] = resolved.get(key, 0) + val
    return resolved


def _wowhead_stats_cache_key(
    item_id: int,
    bonus_ids: list[int] | None = None,
    crafted_stats: list[int] | None = None,
) -> str:
    """Disk-cache key for one item's Wowhead stat lookup.

    Factored out (2026-08-23) so `_needs_live_wowhead_fetch` can ask "would
    this item cost a live request?" with the SAME key the fetch uses — a
    second, drifting copy of the key would make the burst reservation
    (`live_lookup_lease`) count the wrong number of misses.
    """
    bonus_key = "-".join(str(b) for b in sorted(bonus_ids or []))
    crafted_key = "-".join(str(c) for c in sorted(crafted_stats or []))
    key = f"wh_{item_id}_{bonus_key or 'base'}"
    if crafted_key:
        # Two crafted variants of the same base item+bonus (different chosen
        # secondary pair) must NOT collide on one cache entry — each resolves
        # to a different relabeled dict via `_remap_crafted_secondary_stats`.
        key += f"_c{crafted_key}"
    return key


def _needs_live_wowhead_fetch(item_spec) -> bool:
    """True when this item's stats are not already on disk (seed or cache), so
    resolving it would cost one live request."""
    if not item_spec or not getattr(item_spec, "item_id", 0):
        return False
    key = _wowhead_stats_cache_key(
        item_spec.item_id,
        getattr(item_spec, "bonus_ids", None),
        getattr(item_spec, "crafted_stats", None),
    )
    return _load_named_cache(key) is None


def fetch_item_stats_wowhead(
    item_id: int,
    bonus_ids: list[int] | None = None,
    crafted_stats: list[int] | None = None,
    class_spec: str | None = None,
) -> dict[str, int] | None:
    """Fetch item stats from Wowhead's public XML tooltip endpoint.

    Zero auth required. Respects bonus_ids if provided (returns properly-scaled
    stats at the player's actual ilvl, not the base item ilvl). Results cached
    on disk under the same TTL as Blizzard items.

    Names-based parsing — we look for Armor / Stamina / Strength / Critical Strike
    / Haste / Mastery / Versatility in the rendered tooltip rather than relying on
    Wowhead's internal numeric stat IDs, which is more robust to ID changes.

    `crafted_stats` (an `ItemSpec.crafted_stats` pair like `[32, 36]`) relabels
    a crafted item's secondary-rating pair to the crafter's real choice — see
    `_remap_crafted_secondary_stats`. `class_spec` resolves an ambiguous
    agi/str hybrid-primary tooltip label to the caller's actual primary stat —
    see `_resolve_hybrid_primary`. Both are optional and default to the
    pre-existing (unlabeled-crafted-pair / strength-primary) behavior so
    un-migrated call sites don't regress.
    """
    if not item_id:
        return None

    cache_key = _wowhead_stats_cache_key(item_id, bonus_ids, crafted_stats)
    cached = _load_named_cache(cache_key)
    if cached is not None:
        return _resolve_hybrid_primary(cached, class_spec)

    if not _live_wowhead_allowed():
        return None  # hard-offline, or the public box's lookup window is spent

    url = f"https://www.wowhead.com/item={item_id}&xml"
    if bonus_ids:
        url = f"https://www.wowhead.com/item={item_id}?bonus={':'.join(str(b) for b in bonus_ids)}&xml"

    resp = _with_retries(
        requests.get,
        url,
        headers={"User-Agent": "simf/0.7 (free WoW tank survivability sim; cached)"},
        timeout=10,
    )
    if resp is None:
        return None
    text = resp.text

    # Parse `<!--marker-->VALUE NAME` lines. Wowhead always emits stats with this
    # structure inside the htmlTooltip CDATA. Value can include commas (e.g. "+1,507").
    stats: dict[str, int] = {}
    name_map = {
        "Armor": "armor_from_gear",
        "Stamina": "stamina",
        "Strength": "strength",
        "[Strength or Intellect]": "strength",  # hybrid primary; tanks use str slot
        # Tri-primary label on stat-stick trinkets (e.g. Heart of Wind 250256)
        # and the two-way agi/str combo (e.g. Solarflare Prism 252420) are
        # both spec-dependent — a Prot Warrior/Pal/Blood DK wants strength,
        # but Guardian/Brewmaster/VDH ignore strength entirely (see
        # core/character.py) and need agility instead. Resolved to the real
        # primary stat by `_resolve_hybrid_primary`, after parsing, per
        # `class_spec` — NOT hardcoded to strength here (that silently zeroed
        # every tri/dual-primary item's primary-stat contribution for the
        # three agility specs).
        "[Agility or Strength or Intellect]": _HYBRID_PRIMARY_KEY,
        "[Agility or Strength]": _HYBRID_PRIMARY_KEY,
        # Agility/Intellect were absent entirely: every agility item resolved
        # stat-less for the agility tank specs (Brewmaster/Guardian/VDH), and
        # items whose ONLY stats were unmapped returned None — which is never
        # cached, so they were re-fetched on every load (2026-06-10 burst).
        "Agility": "agility",
        "Intellect": "intellect",
        "[Agility or Intellect]": "agility",  # no str option — agi is the tank-relevant half
        "Critical Strike": "crit_rating",
        "Haste": "haste_rating",
        "Mastery": "mastery_rating",
        "Versatility": "versatility_rating",
        # Tertiary stats — Wowhead surfaces these on rings/cloaks/etc. as
        # extra rating lines. Previously dropped silently from the stat
        # dict, which is why Brutoh's Bifurcation Band (266) tooltip
        # showed +43 Avoidance in-game but simf's slot-dialog card
        # rendered no Avoidance line at all. Tertiaries don't move the
        # eHP scorer today (no marginal seeded), but parsing them keeps
        # the displayed stats faithful to the tooltip.
        "Avoidance": "avoidance_rating",
        "Leech": "leech_rating",
        "Speed": "speed_rating",
        "Indestructible": "indestructible",
    }
    # Match value then NAME, where NAME is one of the known labels followed by < or whitespace-end.
    # Examples: "30 Armor", "+1,507 Stamina", "+110 [Strength or Intellect]", "12 Critical Strike"
    for m in re.finditer(
        r"<!--(?:amr|stat\d+|rtg\d+)-->\s*([+-]?[\d,]+)\s+([A-Za-z\[\] ]+?)\s*(?:<|$)",
        text,
    ):
        raw_val = m.group(1).replace(",", "").lstrip("+")
        try:
            val = int(raw_val)
        except ValueError:
            continue
        label = m.group(2).strip()
        key = name_map.get(label)
        if key:
            stats[key] = stats.get(key, 0) + val

    if not stats:
        return None
    stats = _remap_crafted_secondary_stats(stats, crafted_stats)
    _save_named_cache(cache_key, stats)
    return _resolve_hybrid_primary(stats, class_spec)


def fetch_item_socket_count_wowhead(item_id: int, bonus_ids: list[int] | None = None) -> int | None:
    """Number of gem sockets an item has, per Wowhead's own item data.

    Zero auth required — same XML endpoint as ``fetch_item_stats_wowhead``, but
    reads the ``<jsonEquip>`` CDATA block's ``"nsockets"`` key instead of the
    rendered tooltip text. This is the item's *static* socket count (does the
    item have a socket at all), independent of whether the player currently has
    a gem in it — unlike ``ItemSpec.gem_ids``, which only ever reflects
    currently-filled sockets and so can't distinguish "no socket" from "empty
    socket."

    Returns ``None`` — meaning "unknown," not "zero" — when the item isn't
    found, the box is offline, or the field is simply absent from the response
    (absence of ``nsockets`` means the item has 0 sockets, but we still can't
    always tell that apart from a fetch failure, so callers should treat
    ``None`` as "don't synthesize anything, preserve current behavior").
    Results cached on disk under the same TTL as the other Wowhead lookups.
    """
    if not item_id:
        return None

    bonus_key = "-".join(str(b) for b in sorted(bonus_ids or []))
    cache_key = f"wh_sockets_{item_id}_{bonus_key or 'base'}"
    cached = _load_named_cache(cache_key)
    if cached is not None:
        return cached.get("nsockets")

    if not _live_wowhead_allowed():
        return None  # hard-offline, or the public box's lookup window is spent

    url = f"https://www.wowhead.com/item={item_id}&xml"
    if bonus_ids:
        url = f"https://www.wowhead.com/item={item_id}?bonus={':'.join(str(b) for b in bonus_ids)}&xml"

    resp = _with_retries(
        requests.get,
        url,
        headers={"User-Agent": "simf/0.7 (free WoW tank survivability sim; cached)"},
        timeout=10,
    )
    if resp is None:
        return None
    text = resp.text

    block = re.search(r"<jsonEquip><!\[CDATA\[(.*?)\]\]></jsonEquip>", text, re.S)
    if not block:
        return None
    m = re.search(r'"nsockets":(\d+)', block.group(1))
    if not m:
        return None

    nsockets = int(m.group(1))
    _save_named_cache(cache_key, {"nsockets": nsockets})
    return nsockets


def fetch_item_is_two_handed_wowhead(
    item_id: int, bonus_ids: list[int] | None = None
) -> bool | None:
    """Whether an item occupies both weapon slots (Two-Hand), per Wowhead's
    own equip-slot data. Zero auth required — same XML endpoint as
    ``fetch_item_stats_wowhead``, reading the ``<inventorySlot>`` tag instead
    of the tooltip stat lines.

    Exists so the gear recommender can refuse to suggest a two-handed weapon
    as a "main-hand upgrade" for a shield spec (Protection Warrior/Paladin):
    equipping one silently unequips the off-hand shield, a defensive loss the
    per-slot ΔeHP compare never sees (it only touches the main_hand stat
    delta). Found 2026-07-04 chasing a Gear-tab recommendation that scored a
    272 Two-Handed Sword as a +2.82% eHP "upgrade" over an equipped 298
    one-hand weapon — the raw stat budget of an unpaired 2H weapon is much
    larger than a 1H weapon's, so any 2H candidate wins that comparison even
    though equipping it would strip the character's shield entirely.

    Returns ``None`` — "unknown" — when the item isn't found, the box is
    offline, or the tag is absent. Callers should treat ``None`` as "don't
    filter," matching the other Wowhead lookups' fail-open convention.
    """
    if not item_id:
        return None

    bonus_key = "-".join(str(b) for b in sorted(bonus_ids or []))
    cache_key = f"wh_twohand_{item_id}_{bonus_key or 'base'}"
    cached = _load_named_cache(cache_key)
    if cached is not None:
        return cached.get("is_two_hand")

    if not _live_wowhead_allowed():
        return None  # hard-offline, or the public box's lookup window is spent

    url = f"https://www.wowhead.com/item={item_id}&xml"
    if bonus_ids:
        url = f"https://www.wowhead.com/item={item_id}?bonus={':'.join(str(b) for b in bonus_ids)}&xml"

    resp = _with_retries(
        requests.get,
        url,
        headers={"User-Agent": "simf/0.7 (free WoW tank survivability sim; cached)"},
        timeout=10,
    )
    if resp is None:
        return None
    text = resp.text

    m = re.search(r'<inventorySlot id="\d+">([^<]+)</inventorySlot>', text)
    if not m:
        return None

    is_two_hand = m.group(1).strip() == "Two-Hand"
    _save_named_cache(cache_key, {"is_two_hand": is_two_hand})
    return is_two_hand


def fetch_item_icon_wowhead(item_id: int) -> str | None:
    """Wowhead icon slug (e.g. ``inv_helmet_98``) for an item.

    Doesn't depend on bonus_ids — same item, same icon at every ilvl. Cached
    on disk under a stable key so it survives the stats-cache TTL roll.
    """
    if not item_id:
        return None
    cache_key = f"wh_icon_{item_id}"
    cached = _load_named_cache(cache_key)
    if cached is not None:
        return cached.get("name")
    # Was ungated until 2026-08-23 — the one Wowhead fetch the public-mode gate
    # never covered, so every rendered gear card leaked an uncapped outbound
    # request. Same window as the stat lookups; a miss just means no icon
    # (callers already treat None as "render without one").
    if not _live_wowhead_allowed():
        return None
    resp = _with_retries(
        requests.get,
        f"https://www.wowhead.com/item={item_id}&xml",
        headers={"User-Agent": "simf/0.7 (free WoW tank survivability sim; cached)"},
        timeout=10,
    )
    if resp is None:
        return None
    text = resp.text
    m = re.search(r"<icon[^>]*>([^<]+)</icon>", text)
    if not m:
        return None
    icon = m.group(1).strip().lower()
    _save_named_cache(cache_key, {"name": icon})
    return icon


def _load_named_cache(key: str) -> dict | None:
    path = _CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if time.time() - data.get("cached_at", 0) < _CACHE_TTL:
                return data["stats"]
        except Exception:  # noqa: S110 — best-effort fallback
            pass
    return _load_seed(f"{key}.json")


def _save_named_cache(key: str, stats: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps(
                {
                    "stats": stats,
                    "cached_at": time.time(),
                }
            )
        )
        _prune_cache_dir()
    except Exception:  # noqa: S110 — best-effort fallback
        pass


def fetch_item_stats_for_spec(
    item_spec, region: str = "eu", class_spec: str | None = None
) -> dict[str, int] | None:
    """Best-available stats for an ItemSpec. Wowhead (no auth, respects bonus_ids)
    is preferred since it returns properly-scaled stats. Falls back to Blizzard
    (auth required, base-ilvl only). Returns None if neither source works.

    `class_spec`, when given, resolves an ambiguous agi/str hybrid-primary
    tooltip label (see `fetch_item_stats_wowhead`) to the caller's real
    primary stat; `item_spec.crafted_stats` (if present) relabels a crafted
    item's secondary-rating pair to the crafter's real choice.
    """
    if not item_spec or not getattr(item_spec, "item_id", 0):
        return None
    wh = fetch_item_stats_wowhead(
        item_spec.item_id,
        getattr(item_spec, "bonus_ids", None),
        getattr(item_spec, "crafted_stats", None),
        class_spec,
    )
    if wh:
        return wh
    return fetch_item_stats(item_spec.item_id, region)


def resolve_equipped_stats(
    items: dict, region: str = "eu", class_spec: str | None = None
) -> dict[str, int]:
    """
    Sum stats across all equipped item slots.
    `items` is SimcImport.items: dict[slot, ItemSpec].
    Returns aggregated stat dict. Missing items are skipped silently. Uses
    bonus_id-aware Wowhead lookup first, falls back to Blizzard base stats.

    Also surfaces `shield_armor` — the off-hand slot's armor in isolation —
    when an off-hand item is equipped and has an armor stat (shield). This
    feeds the SimC `block_value = shield.armor × 2.5` formula. Non-shield
    off-hands (Brewmaster fist weapons etc.) have no armor stat and so the
    key is simply omitted.

    `class_spec`, when given, is forwarded to `fetch_item_stats_for_spec` so
    an agi/str hybrid-primary item resolves to the right stat for this
    character instead of always strength.

    On the public box the uncached slots are reserved as ONE burst before any
    request goes out (`live_lookup_lease`): a character's stat total is only
    meaningful if every slot resolved, so a window that can't cover the whole
    set returns `{}` — "couldn't read your gear", which callers already
    surface — instead of a total that's silently a few slots short. A
    hard-offline box keeps its pre-existing behavior (sum whatever is cached).
    """
    # Only the public box meters lookups, so only it pays for the cache probe
    # (`_needs_live_wowhead_fetch` is one stat() per slot). An outer lease
    # already covers this set.
    if _is_public_mode() and not _hard_offline() and _lease_depth() == 0:
        uncached = sum(1 for spec in items.values() if _needs_live_wowhead_fetch(spec))
        if uncached:
            with live_lookup_lease(uncached) as granted:
                if not granted:
                    return {}
                return _sum_item_stats(items, region, class_spec)
    return _sum_item_stats(items, region, class_spec)


def _sum_item_stats(
    items: dict, region: str = "eu", class_spec: str | None = None
) -> dict[str, int]:
    """Per-slot stat sum — the body of `resolve_equipped_stats`, split out so
    the budget reservation wraps it without indenting the loop twice."""
    totals: dict[str, int] = {}
    for slot, item_spec in items.items():
        stats = fetch_item_stats_for_spec(item_spec, region, class_spec)
        if not stats:
            continue
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v
        if slot == "off_hand" and "armor_from_gear" in stats:
            totals["shield_armor"] = stats["armor_from_gear"]
    return totals


def search_items(query: str, region: str = "eu", page_size: int = 20) -> list[dict]:
    """Search Blizzard items by name. Returns a list of dicts:
      {"id": int, "name": str, "ilvl": int, "quality": str, "slot": str | None}

    Empty list on any failure / missing creds / blank query. Quality filtered to
    EPIC + LEGENDARY by default (M+/raid gear). Results cached per-query.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    cache_key = f"search_{region}_{query.lower()}"
    cached = _load_search_cache(cache_key)
    if cached is not None:
        return cached

    if _is_offline():
        return []  # owner-credentialed endpoint: no live item search publicly

    token = _get_token(region)
    if not token:
        return []

    resp = _with_retries(
        requests.get,
        f"https://{region}.api.blizzard.com/data/wow/search/item",
        params={
            "namespace": f"static-{region}.battle.net",
            "name.en_US": query,
            "orderby": "id:desc",
            "_pageSize": str(page_size),
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp is None:
        return []
    try:
        payload = resp.json()
    except Exception:
        return []

    results: list[dict] = []
    for entry in payload.get("results", []):
        data = entry.get("data", {}) or {}
        quality = (data.get("quality") or {}).get("type", "")
        if quality not in ("EPIC", "LEGENDARY"):
            continue
        name_obj = data.get("name") or {}
        name = name_obj.get("en_US") if isinstance(name_obj, dict) else str(name_obj)
        if not name:
            continue
        inv = (data.get("inventory_type") or {}).get("type", "") or None
        results.append(
            {
                "id": int(data.get("id", 0)),
                "name": name,
                "ilvl": int(data.get("level", 0)),
                "quality": quality,
                "slot": inv,
            }
        )

    _save_search_cache(cache_key, results)
    return results


_SEARCH_TTL = 7 * 24 * 3600  # 7 days


def _search_cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def _load_search_cache(key: str) -> list[dict] | None:
    path = _search_cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("cached_at", 0) < _SEARCH_TTL:
            return data["results"]
    except Exception:  # noqa: S110 — best-effort fallback
        pass
    return None


def _save_search_cache(key: str, results: list[dict]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _search_cache_path(key).write_text(
            json.dumps(
                {
                    "results": results,
                    "cached_at": time.time(),
                }
            )
        )
    except Exception:  # noqa: S110 — best-effort fallback
        pass


# Map Blizzard inventory_type → ItemSpec slot key (mirrors simc_import.ITEM_SLOTS).
_INV_TO_SLOT = {
    "HEAD": "head",
    "NECK": "neck",
    "SHOULDER": "shoulder",
    "CLOAK": "back",
    "CHEST": "chest",
    "ROBE": "chest",
    "WRIST": "wrist",
    "HAND": "hands",
    "WAIST": "waist",
    "LEGS": "legs",
    "FEET": "feet",
    "FINGER": "finger1",
    "TRINKET": "trinket1",
    "WEAPON": "main_hand",
    "TWOHWEAPON": "main_hand",
    "WEAPONMAINHAND": "main_hand",
    "WEAPONOFFHAND": "off_hand",
    "HOLDABLE": "off_hand",
    "SHIELD": "off_hand",
    "RANGED": "ranged",
}


def inventory_type_to_slot(inv_type: str | None) -> str | None:
    if not inv_type:
        return None
    return _INV_TO_SLOT.get(inv_type.upper())


def delta_stats(item_a: dict | None, item_b: dict | None) -> dict[str, int]:
    """
    Compute stat delta: item_b - item_a. Both are stat dicts or None.
    Returns dict of non-zero deltas only.
    """
    a = item_a or {}
    b = item_b or {}
    keys = set(a) | set(b)
    return {k: b.get(k, 0) - a.get(k, 0) for k in keys if b.get(k, 0) != a.get(k, 0)}
