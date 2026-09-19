"""Public-mode rate-limit guard for Warcraft Logs fetches (ADR 0001).

The public deploy exposes the WCL-URL analysis flow on a single *owner*
client-credentials key. That key's hourly point budget is shared by every
visitor, so before a public WCL fetch we apply a layered guard:

  * a process-global rolling-window limiter (N per minute, M per hour) that
    bounds *our own* request cadence regardless of the WCL points budget;
  * a non-blocking single-flight slot, so concurrent strangers serialize
    instead of each firing a multi-page GraphQL fetch at once;
  * a per-session cooldown (the UI passes the session's last-fetch time).

The WCL *points* ceiling (``rateLimitData``) is a separate, complementary
gate checked via :func:`simf.io.wcl_api.fetch_rate_limit`: this module
governs how often *we* call, that governs how much budget *they* have left.

All counters are in-memory and thread-safe. A process restart resets them —
acceptable: the limiter bounds abuse, it is not a durable accounting ledger.
The clock is injectable so tests don't sleep.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# Defaults tuned for one shared owner key on the Pi. The window bounds the
# public instance's *cadence* of WCL fetches; a single user-facing "analysis"
# can consume up to 3 slots (report-fetch + player-details + analyze), so the
# per-minute ceiling carries headroom for a few concurrent visitors rather than
# choking one. The precise WCL points budget is protected separately by the
# rateLimitData pre-flight; the disk cache (analyze_wcl_fight) makes repeat hits
# on a popular fight cost zero. Both are constructor params — tune for the box.
_DEFAULT_PER_MINUTE = 12
_DEFAULT_PER_HOUR = 120

# Per-session cooldown: one analyze per session per this many seconds. Stops a
# single visitor hammering "Analyze" without throttling distinct visitors.
DEFAULT_SESSION_COOLDOWN_S = 20.0

# ── Gear-lookup guard (public Raider.IO name-lookup path) ────────────────────
# A Raider.IO gear fetch is ONE cheap unauthenticated request (~15 s timeout),
# far lighter than a multi-page WCL GraphQL analyze, so it gets its OWN window
# (a separate WCLRateGuard instance below — the class just counts events into a
# per-instance deque) and a shorter per-session cooldown. There is no shared
# owner-points budget to protect (Raider.IO is zero-auth) and no single-flight
# slot (one request, not a multi-page fetch); the window + cooldown suffice.
# 6/min is comfortably under Raider.IO's ~200/min unauth ceiling while still
# bounding one Pi against a stranger hammering the lookup button.
_DEFAULT_GEAR_PER_MINUTE = 6
_DEFAULT_GEAR_PER_HOUR = 60
DEFAULT_GEAR_SESSION_COOLDOWN_S = 8.0

# When the WCL points budget drops below this fraction of the hourly limit, the
# public flow refuses new fetches and shows a "busy, try later" message rather
# than spending the last of the shared key (kept here so UI + tests agree).
DEFAULT_RESERVE_FRACTION = 0.10


class WCLRateGuard:
    """Process-global rolling-window limiter over two windows at once.

    A single monotonic-timestamp deque is the source of truth for the hour
    window; the minute count is the tail of that same deque. :meth:`check`
    is atomic: it admits (and records) only when *both* windows have room,
    so a caller never spends the minute budget then fails the hour budget.
    """

    def __init__(
        self,
        per_minute: int = _DEFAULT_PER_MINUTE,
        per_hour: int = _DEFAULT_PER_HOUR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._clock = clock
        self._events: deque[float] = deque()  # ascending monotonic timestamps
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def _count_within(self, now: float, window: float) -> int:
        cutoff = now - window
        n = 0
        for t in reversed(self._events):
            if t > cutoff:
                n += 1
            else:
                break
        return n

    def check(self, cost: int = 1) -> tuple[bool, float]:
        """Admit ``cost`` fetches if both windows have room for all of them.

        Returns ``(allowed, retry_after_seconds)``. When allowed, ``cost``
        events are recorded and ``retry_after`` is 0.0. When denied, NOTHING
        is recorded and ``retry_after`` is the seconds until the binding
        window frees enough slots.

        ``cost > 1`` is the burst case: one user action that fans out into
        several requests (a character load resolves one item lookup per
        equipped slot — see ``item_db.live_lookup_lease``). Reserving the
        whole burst atomically is what makes the caller's *result* honest: a
        per-request check could admit 12 of 17 item lookups and hand back a
        character whose stats are silently 30% short, which reads as a real
        number and isn't. All-or-nothing means the caller either resolves
        everything or reports that it couldn't — never a plausible wrong
        total.

        A ``cost`` above ``per_minute`` can never be admitted; it is denied
        with the full-minute retry rather than partially reserved. Keep
        bursts well under the window (equipped-slot bursts top out ~18).
        """
        cost = max(1, int(cost))
        with self._lock:
            now = self._clock()
            self._prune(now)
            in_minute = self._count_within(now, 60.0)
            if in_minute + cost > self._per_minute:
                # Wait for enough of the minute window's oldest events to age
                # out that `cost` slots open up.
                need = in_minute + cost - self._per_minute
                within = [t for t in self._events if t > now - 60.0]
                frees_at = within[need - 1] if 0 < need <= len(within) else now
                return False, max(0.0, frees_at + 60.0 - now)
            if len(self._events) + cost > self._per_hour:
                need = len(self._events) + cost - self._per_hour
                frees_at = self._events[need - 1] if 0 < need <= len(self._events) else now
                return False, max(0.0, frees_at + 3600.0 - now)
            self._events.extend([now] * cost)
            return True, 0.0

    def snapshot(self) -> tuple[int, int]:
        """``(used_this_minute, used_this_hour)`` — for tests / diagnostics."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            return self._count_within(now, 60.0), len(self._events)


# Single-flight: only one public WCL fetch runs at a time across the process,
# so two strangers don't each kick off a multi-page fetch and double the spend.
_WCL_FETCH_LOCK = threading.Lock()


@contextmanager
def wcl_slot() -> Iterator[bool]:
    """Non-blocking single-flight slot, mirroring the sim's ``_sim_slot``.

    Yields ``True`` if this caller holds the slot, ``False`` if another fetch
    is already in flight (the caller should degrade, not block).
    """
    acquired = _WCL_FETCH_LOCK.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _WCL_FETCH_LOCK.release()


def session_cooldown_remaining(
    last_ts: float | None,
    cooldown_s: float = DEFAULT_SESSION_COOLDOWN_S,
    *,
    now: float | None = None,
) -> float:
    """Seconds remaining on a per-session cooldown, 0.0 if ready.

    ``last_ts`` and ``now`` are monotonic timestamps; the UI stores
    ``last_ts`` in ``st.session_state`` after each successful analyze.
    """
    if last_ts is None:
        return 0.0
    current = time.monotonic() if now is None else now
    return max(0.0, last_ts + cooldown_s - current)


def format_retry(seconds: float) -> str:
    """Human-friendly retry hint: 'a few seconds' / 'about N minute(s)'."""
    if seconds <= 0:
        return "now"
    if seconds < 45:
        return "a few seconds"
    minutes = max(1, round(seconds / 60.0))
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


# Process-global guard for the public WCL flow. One instance per process; the
# UI calls :func:`public_guard` so tests can monkeypatch a fresh instance.
_PUBLIC_GUARD = WCLRateGuard()


def public_guard() -> WCLRateGuard:
    return _PUBLIC_GUARD


# Separate process-global guard for the public Raider.IO gear-lookup flow. A
# fresh WCLRateGuard owns its own event deque, so this does NOT share the WCL
# fetch window above — a stranger looking up gear can't starve the WCL flow and
# vice-versa. The UI calls :func:`gear_lookup_guard` so tests can monkeypatch.
_GEAR_LOOKUP_GUARD = WCLRateGuard(
    per_minute=_DEFAULT_GEAR_PER_MINUTE, per_hour=_DEFAULT_GEAR_PER_HOUR
)


def gear_lookup_guard() -> WCLRateGuard:
    return _GEAR_LOOKUP_GUARD


# ── Item-lookup guard (public /simc-paste + name-lookup gear resolution) ─────
# Until 2026-08-23 the public box simply REFUSED every live item-stat lookup
# (`item_db._is_offline`), so any visitor whose gear wasn't in the committed
# 118-entry demo seed got zero stats — and the seed's cache keys are
# bonus-id-specific, so it can never serve arbitrary gear. That made the two
# headline flows (paste your /simc, look up your name) structurally incapable
# of working for anyone but the demo character. Budget the lookups instead of
# blocking them: Wowhead's XML tooltip endpoint is zero-auth and cheap, and
# every result is cached to disk, so a fresh visitor costs one bounded burst
# and a repeat visit on the same gear costs nothing.
#
# Sizing: a first-time character load resolves one lookup per equipped slot
# (~18 max, atomically reserved — see `item_db.live_lookup_lease`), then up to
# ~25 vault/bag cards each costing a stat + icon lookup as they render — call
# it ~90 requests for one cold visitor, and ~0 for their next visit (disk
# cache). 180/min leaves room for a couple of concurrent cold loads without a
# burst denying itself; 1800/hour bounds the box to roughly 20 cold visitors
# an hour (~0.5 requests/second average), well inside what Wowhead tolerates
# and inside the item cache's own 1024-file cap.
#
# Owner-credentialed Blizzard endpoints (item search, Blizzard item stats) are
# NOT covered here — they stay hard-blocked in public mode so a stranger can
# never spend the owner's OAuth budget (see `item_db._is_offline`).
#
# No per-session cooldown, unlike the Raider.IO gear lookup: re-pasting the
# same export is free (disk cache), so the realistic repeat-load — a visitor
# fixing a typo and loading again — should not be throttled. The rolling
# window is the egress bound.
_DEFAULT_ITEM_PER_MINUTE = 180
_DEFAULT_ITEM_PER_HOUR = 1800

_ITEM_LOOKUP_GUARD = WCLRateGuard(
    per_minute=_DEFAULT_ITEM_PER_MINUTE, per_hour=_DEFAULT_ITEM_PER_HOUR
)


def item_lookup_guard() -> WCLRateGuard:
    """Process-global window over live item-stat lookups on the public box.

    Own event deque (a fresh :class:`WCLRateGuard`), so item lookups can't
    starve the WCL or Raider.IO windows and vice-versa. Callers go through
    this accessor so tests can monkeypatch a fresh instance.
    """
    return _ITEM_LOOKUP_GUARD
