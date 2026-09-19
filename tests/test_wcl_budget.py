"""Tests for the public-mode WCL rate-limit guard (ADR 0001)."""

import threading

from simf.core import wcl_budget
from simf.core.wcl_budget import (
    WCLRateGuard,
    format_retry,
    gear_lookup_guard,
    item_lookup_guard,
    public_guard,
    session_cooldown_remaining,
    wcl_slot,
)


class _FakeClock:
    """Injectable monotonic clock — advance() instead of sleeping."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ─── rolling-window guard ────────────────────────────────────────────────────


def test_guard_admits_until_minute_cap():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=3, per_hour=100, clock=clk)
    assert g.check() == (True, 0.0)
    assert g.check()[0] is True
    assert g.check()[0] is True
    allowed, retry = g.check()  # 4th in the same minute
    assert allowed is False
    assert 0 < retry <= 60


def test_guard_minute_window_frees_after_60s():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=2, per_hour=100, clock=clk)
    assert g.check()[0] is True
    assert g.check()[0] is True
    assert g.check()[0] is False
    clk.advance(61)  # first two age out of the minute window
    assert g.check()[0] is True


def test_guard_denied_does_not_consume_budget():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=1, per_hour=100, clock=clk)
    assert g.check()[0] is True
    assert g.check()[0] is False  # denied — must NOT record a second event
    # Only the admitted event is on the books: minute window has 1, hour has 1.
    assert g.snapshot() == (1, 1)
    # After the minute frees, the hour still holds exactly the one admitted
    # event (a recorded denial would show 2 here).
    clk.advance(61)
    assert g.snapshot() == (0, 1)
    assert g.check()[0] is True


def test_guard_hour_cap_binds_independently_of_minute():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=100, per_hour=3, clock=clk)
    for _ in range(3):
        # spread across minutes so the minute cap never binds
        assert g.check()[0] is True
        clk.advance(120)
    allowed, retry = g.check()
    assert allowed is False
    assert retry > 60  # must wait for the oldest hour-event to age out


def test_guard_snapshot_counts_both_windows():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=10, per_hour=10, clock=clk)
    g.check()
    clk.advance(120)  # leaves the minute window, stays in the hour window
    g.check()
    minute_n, hour_n = g.snapshot()
    assert minute_n == 1
    assert hour_n == 2


def test_guard_is_thread_safe_under_contention():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=50, per_hour=50, clock=clk)
    admitted = []
    lock = threading.Lock()

    def worker():
        ok, _ = g.check()
        if ok:
            with lock:
                admitted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly the cap is admitted — no over-admission from a race.
    assert sum(admitted) == 50


# ─── single-flight slot ──────────────────────────────────────────────────────


def test_wcl_slot_single_flight():
    with wcl_slot() as first:
        assert first is True
        with wcl_slot() as second:
            assert second is False  # already in flight
    # released — next caller acquires again
    with wcl_slot() as again:
        assert again is True


def test_wcl_slot_released_on_exception():
    try:
        with wcl_slot() as held:
            assert held is True
            raise ValueError("boom")
    except ValueError:
        pass
    with wcl_slot() as after:
        assert after is True


# ─── per-session cooldown ────────────────────────────────────────────────────


def test_session_cooldown_none_is_ready():
    assert session_cooldown_remaining(None, 20.0, now=1000.0) == 0.0


def test_session_cooldown_blocks_then_clears():
    assert session_cooldown_remaining(1000.0, 20.0, now=1005.0) == 15.0
    assert session_cooldown_remaining(1000.0, 20.0, now=1025.0) == 0.0


# ─── retry formatting ────────────────────────────────────────────────────────


def test_format_retry():
    assert format_retry(0) == "now"
    assert format_retry(10) == "a few seconds"
    assert format_retry(120) == "about 2 minutes"
    assert format_retry(60) == "about 1 minute"


# ─── process-global singleton ────────────────────────────────────────────────


def test_public_guard_is_singleton():
    assert public_guard() is wcl_budget._PUBLIC_GUARD
    assert public_guard() is public_guard()


# ─── gear-lookup guard (public Raider.IO path) ───────────────────────────────


def test_gear_lookup_guard_is_singleton():
    assert gear_lookup_guard() is wcl_budget._GEAR_LOOKUP_GUARD
    assert gear_lookup_guard() is gear_lookup_guard()


def test_gear_lookup_guard_window_is_independent_of_wcl_guard():
    """The gear-lookup guard owns its OWN rolling window (a fresh WCLRateGuard
    instance, separate deque), so a stranger hammering name lookups can't
    starve the WCL fetch budget and vice-versa (ADR 0001)."""
    assert gear_lookup_guard() is not public_guard()
    # Each guard counts into its own deque — prove the two windows don't share
    # state by exhausting two fresh instances independently.
    clk_a, clk_b = _FakeClock(), _FakeClock()
    a = WCLRateGuard(per_minute=1, per_hour=10, clock=clk_a)
    b = WCLRateGuard(per_minute=1, per_hour=10, clock=clk_b)
    assert a.check()[0] is True
    assert a.check()[0] is False  # a is now exhausted for this minute…
    assert b.check()[0] is True  # …but b is untouched.


def test_gear_lookup_cooldown_is_lighter_than_wcl():
    """A Raider.IO GET is one cheap request, so its per-session cooldown is
    shorter than the multi-page WCL analyze cooldown."""
    assert wcl_budget.DEFAULT_GEAR_SESSION_COOLDOWN_S < wcl_budget.DEFAULT_SESSION_COOLDOWN_S


# ─── burst admission (item-lookup guard, 2026-08-23) ─────────────────────────


def test_burst_admits_all_or_nothing():
    """A burst reserves every slot it asked for, or none of them. Partial
    admission is what would let a caller resolve 12 of 17 equipped items and
    report a stat total that's silently short."""
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=10, per_hour=100, clock=clk)
    assert g.check(4) == (True, 0.0)
    assert g.snapshot() == (4, 4)
    allowed, retry = g.check(8)  # 4 + 8 > 10
    assert allowed is False
    assert 0 < retry <= 60
    assert g.snapshot() == (4, 4), "a denied burst records nothing"
    assert g.check(6)[0] is True, "a burst that fits is still admitted"
    assert g.snapshot() == (10, 10)


def test_burst_retry_waits_for_enough_slots_not_just_one():
    """Denied at cost 3 with 1 slot free, the retry hint must be when THREE
    slots free up, not when the first one does."""
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=3, per_hour=100, clock=clk)
    g.check()  # t=1000
    clk.advance(10)
    g.check()  # t=1010
    clk.advance(10)  # now t=1020, one slot left in the window
    allowed, retry = g.check(3)
    assert allowed is False
    # Needs 2 of the recorded events to age out: the second one (t=1010) frees
    # at t=1070, i.e. 50s from now.
    assert retry == 50.0


def test_burst_hour_window_binds_too():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=100, per_hour=5, clock=clk)
    assert g.check(5)[0] is True
    allowed, retry = g.check(1)
    assert allowed is False
    assert 0 < retry <= 3600
    assert g.snapshot() == (5, 5)


def test_burst_larger_than_the_window_is_denied_not_partially_reserved():
    clk = _FakeClock()
    g = WCLRateGuard(per_minute=4, per_hour=100, clock=clk)
    allowed, retry = g.check(9)
    assert allowed is False
    assert retry > 0
    assert g.snapshot() == (0, 0)


def test_cost_below_one_is_treated_as_one():
    g = WCLRateGuard(per_minute=2, per_hour=10, clock=_FakeClock())
    assert g.check(0)[0] is True
    assert g.snapshot() == (1, 1)


# ─── item-lookup guard (public /simc-paste + name-lookup gear resolution) ────


def test_item_lookup_guard_is_singleton():
    assert item_lookup_guard() is wcl_budget._ITEM_LOOKUP_GUARD
    assert item_lookup_guard() is item_lookup_guard()


def test_item_lookup_window_is_independent_and_burst_sized():
    """Item lookups fan out per equipped slot, so this window is much wider
    than the one-request-per-action WCL/Raider.IO windows — and separate, so a
    stranger resolving gear can't starve either of them."""
    assert item_lookup_guard() is not public_guard()
    assert item_lookup_guard() is not gear_lookup_guard()
    assert wcl_budget._DEFAULT_ITEM_PER_MINUTE > wcl_budget._DEFAULT_GEAR_PER_MINUTE
    # One character load reserves one lookup per equipped slot (~18 max), so
    # the minute window must comfortably exceed a single load or a lone
    # visitor's burst would deny itself.
    assert wcl_budget._DEFAULT_ITEM_PER_MINUTE >= 2 * 18
