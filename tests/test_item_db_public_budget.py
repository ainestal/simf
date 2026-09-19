"""Budgeted item lookups on the public box (2026-08-23).

Before this, SIMF_PUBLIC hard-blocked EVERY live item-stat lookup, and the
committed seed cache is keyed by item_id + bonus_ids — so it can only ever
serve the demo character's exact gear. A visitor pasting their own /simc (or
looking up their name) therefore resolved zero stats and got an error, which
is both headline flows dead for everyone but the demo. Wowhead's zero-auth
tooltip endpoint is now budgeted instead of blocked; Blizzard's
owner-credentialed endpoints stay blocked.

The tests below pin the four properties that make that safe: the window is
enforced, a burst is all-or-nothing (never a silently-short stat total),
cached gear costs nothing, and the owner's box is never metered.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from simf.core import wcl_budget
from simf.io import item_db

_XML = (
    "<?xml version='1.0'?><wowhead><item><icon>inv_test</icon>"
    "<htmlTooltip><![CDATA[<!--stat3-->+100 Stamina<!--rtg36-->+50 Haste]]></htmlTooltip>"
    '<jsonEquip><![CDATA["nsockets":1]]></jsonEquip></item></wowhead>'
)


@dataclass
class _Spec:
    item_id: int
    bonus_ids: list[int] = field(default_factory=list)
    crafted_stats: list[int] | None = None


class _Resp:
    text = _XML

    def raise_for_status(self):
        return None


@pytest.fixture
def public_box(monkeypatch, tmp_path):
    """Public mode, cold cache, counted network, tiny private rate window."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")
    monkeypatch.delenv("SIMF_ITEM_DB_OFFLINE", raising=False)
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    calls = {"n": 0}

    def _get(*_a, **_kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(item_db.requests, "get", _get)
    guard = wcl_budget.WCLRateGuard(per_minute=40, per_hour=40)
    monkeypatch.setattr(wcl_budget, "item_lookup_guard", lambda: guard)
    return calls, guard


def test_public_box_resolves_uncached_gear(public_box):
    """The regression this whole change exists for: a stranger's item is not in
    the seed, and must still resolve."""
    calls, guard = public_box
    stats = item_db.fetch_item_stats_wowhead(250024, [13440, 6652])
    assert stats and stats["stamina"] == 100
    assert calls["n"] == 1
    assert guard.snapshot() == (1, 1)  # one lookup, charged once


def test_burst_is_all_or_nothing(public_box, monkeypatch):
    """A window that can't cover every uncached slot must resolve NOTHING.

    Metering per request would admit some slots and hand back a character
    whose stamina is silently short — a plausible-looking wrong number, worse
    than an honest failure.
    """
    calls, _ = public_box
    tiny = wcl_budget.WCLRateGuard(per_minute=3, per_hour=3)
    monkeypatch.setattr(wcl_budget, "item_lookup_guard", lambda: tiny)
    items = {f"slot{i}": _Spec(item_id=1000 + i) for i in range(5)}

    assert item_db.resolve_equipped_stats(items) == {}
    assert calls["n"] == 0, "a denied burst must not fetch even one item"
    assert tiny.snapshot() == (0, 0), "a denied burst must reserve nothing"


def test_burst_reserves_every_uncached_slot_once(public_box):
    calls, guard = public_box
    items = {f"slot{i}": _Spec(item_id=2000 + i) for i in range(6)}

    stats = item_db.resolve_equipped_stats(items)
    assert stats["stamina"] == 600  # 6 × 100
    assert calls["n"] == 6
    assert guard.snapshot() == (6, 6), "one charge per uncached slot, not per item×site"


def test_cached_gear_costs_no_budget(public_box):
    """Repeat visits on already-resolved gear must be free, or a popular gear
    set would exhaust the window for everyone else."""
    calls, guard = public_box
    items = {"head": _Spec(item_id=3001), "neck": _Spec(item_id=3002)}
    item_db.resolve_equipped_stats(items)
    assert (calls["n"], guard.snapshot()) == (2, (2, 2))

    item_db.resolve_equipped_stats(items)  # same gear, now on disk
    assert calls["n"] == 2, "cache hit must not re-fetch"
    assert guard.snapshot() == (2, 2), "cache hit must not spend budget"


def test_owner_box_is_never_metered(public_box, monkeypatch):
    calls, guard = public_box
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    items = {f"slot{i}": _Spec(item_id=4000 + i) for i in range(3)}
    assert item_db.resolve_equipped_stats(items)["stamina"] == 300
    assert calls["n"] == 3
    assert guard.snapshot() == (0, 0), "the owner's own lookups spend no public budget"


def test_owner_box_unaffected_by_an_exhausted_public_window(public_box, monkeypatch):
    """Sanity: an exhausted window can't leak into owner mode."""
    calls, _ = public_box
    spent = wcl_budget.WCLRateGuard(per_minute=1, per_hour=1)
    spent.check()
    monkeypatch.setattr(wcl_budget, "item_lookup_guard", lambda: spent)
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    assert item_db.fetch_item_stats_wowhead(4242) is not None
    assert calls["n"] == 1


def test_hard_offline_kill_switch_blocks_wowhead(public_box, monkeypatch):
    """`SIMF_ITEM_DB_OFFLINE=1` (the documented public kill switch) still means
    no live fetch at all — the pre-budget behaviour, on demand."""
    calls, guard = public_box
    monkeypatch.setenv("SIMF_ITEM_DB_OFFLINE", "1")
    assert item_db.fetch_item_stats_wowhead(5001) is None
    assert item_db.fetch_item_icon_wowhead(5001) is None
    assert item_db.fetch_item_socket_count_wowhead(5001) is None
    assert item_db.fetch_item_is_two_handed_wowhead(5001) is None
    assert calls["n"] == 0
    assert guard.snapshot() == (0, 0), "a blocked lookup must not spend budget either"


def test_hard_offline_still_sums_whatever_is_cached(public_box, monkeypatch):
    """Offline is partial-by-design (it's how the seeded demo loads); the
    all-or-nothing burst rule must not turn that into an empty result."""
    calls, _ = public_box
    items = {"head": _Spec(item_id=6001), "neck": _Spec(item_id=6002)}
    item_db.resolve_equipped_stats(items)  # warm both
    monkeypatch.setenv("SIMF_ITEM_DB_OFFLINE", "1")
    items["waist"] = _Spec(item_id=6003)  # never cached
    before = calls["n"]

    stats = item_db.resolve_equipped_stats(items)
    assert stats["stamina"] == 200, "cached slots still sum when offline"
    assert calls["n"] == before, "offline never reaches the network"


def test_blizzard_endpoints_stay_blocked_in_public_mode(public_box, monkeypatch):
    """Budgeting covers Wowhead (zero-auth) only. A stranger must never be able
    to spend the OWNER's Blizzard OAuth budget."""
    calls, _ = public_box
    monkeypatch.setattr(item_db, "_get_token", lambda _region: "token")
    assert item_db.fetch_item_stats(7001) is None
    assert item_db.search_items("Some Item") == []
    assert calls["n"] == 0


def test_icon_lookup_is_gated_by_the_window(public_box, monkeypatch):
    """The icon fetch had no public gate at all before this change — every
    rendered card leaked one uncapped request."""
    calls, _ = public_box
    spent = wcl_budget.WCLRateGuard(per_minute=1, per_hour=1)
    spent.check()
    monkeypatch.setattr(wcl_budget, "item_lookup_guard", lambda: spent)
    assert item_db.fetch_item_icon_wowhead(8001) is None
    assert calls["n"] == 0


def test_lease_is_reentrant(public_box):
    """A nested lease rides the outer reservation instead of double-charging."""
    _calls, guard = public_box
    with item_db.live_lookup_lease(2) as outer:
        assert outer
        with item_db.live_lookup_lease(2) as inner:
            assert inner
        assert item_db._lease_depth() == 1
    assert item_db._lease_depth() == 0
    assert guard.snapshot() == (2, 2), "the nested lease must not reserve a second time"
