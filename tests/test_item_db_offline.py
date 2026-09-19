"""item_db OFFLINE mode + cache cap (public-deploy safety, 2026-06-13 review;
narrowed 2026-08-22 for `fetch_item_stats_wowhead`, generalised 2026-08-23).

In SIMF_PUBLIC mode a stranger's /simc paste must not be able to drive
UNBOUNDED live item-stat fetches: `vault_ranking` loops once per parsed
item, so without a gate an anonymous visitor could fan out thousands of
outbound Wowhead/Blizzard GETs (egress flood → IP ban) and thousands of
cache-file writes (SD-card / inode exhaustion). But blocking them outright
was collateral damage — resolving a visitor's own ~16-20 equipped items is
naturally bounded (one per slot), and refusing it left anyone whose gear
wasn't already cached with "Couldn't fill in your stats."

Public mode therefore BUDGETS every zero-auth Wowhead lookup
(`_live_wowhead_allowed` → `wcl_budget.item_lookup_guard`) rather than
blocking it, and reserves a character load's uncached slots as one atomic
burst (`live_lookup_lease`). Blizzard's owner-credentialed endpoints stay
fully blocked publicly — `_is_offline()` now governs only those. The window
mechanics live in tests/test_wcl_budget.py and the public-mode behaviour in
tests/test_item_db_public_budget.py; what's pinned HERE is the
`SIMF_ITEM_DB_OFFLINE` kill switch (no live fetch at all, for any endpoint)
and the cache cap.
"""

from __future__ import annotations

from simf.core import wcl_budget
from simf.io import item_db


def test_is_offline_keys_on_simf_public(monkeypatch):
    monkeypatch.delenv("SIMF_ITEM_DB_OFFLINE", raising=False)
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    assert item_db._is_offline() is False
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    assert item_db._is_offline() is True
    # Explicit override wins (lets the owner run a public preview that still
    # resolves gear, and lets tests force either way).
    monkeypatch.setenv("SIMF_ITEM_DB_OFFLINE", "0")
    assert item_db._is_offline() is False


def test_offline_never_makes_a_live_request(monkeypatch, tmp_path):
    """`SIMF_ITEM_DB_OFFLINE=1` is the kill switch: a cache MISS returns
    None/[] without touching the network, for EVERY endpoint including the
    zero-auth Wowhead stat lookup that public mode budgets rather than blocks.

    PR #487's counter checked `_is_offline()` and then overrode it for that one
    endpoint, which left the operator no way to stop live stat fetches on a
    misbehaving box. The switch means what it says again."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)  # empty → guaranteed miss
    monkeypatch.setenv("SIMF_ITEM_DB_OFFLINE", "1")

    def _boom(*a, **k):
        raise AssertionError("offline mode must not make a network request")

    monkeypatch.setattr(item_db.requests, "get", _boom)
    assert item_db.fetch_item_stats_wowhead(999999) is None
    assert item_db.fetch_item_stats(999999) is None
    assert item_db.search_items("Some Item") == []
    assert item_db.fetch_item_socket_count_wowhead(999999) is None
    assert item_db.fetch_item_is_two_handed_wowhead(999999) is None
    assert item_db.fetch_item_icon_wowhead(999999) is None


def test_public_wowhead_stats_fetch_allowed_within_budget(monkeypatch, tmp_path):
    """A visitor's own equipped-gear resolution shouldn't dead-end just because
    the box is public, as long as the lookup window isn't exhausted. (Was
    written against PR #487's counter and `SIMF_ITEM_DB_OFFLINE=1`; that env
    var is the kill switch, so public mode is expressed as SIMF_PUBLIC=1.)"""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")
    monkeypatch.delenv("SIMF_ITEM_DB_OFFLINE", raising=False)
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    monkeypatch.setattr(wcl_budget, "item_lookup_guard", lambda: wcl_budget.WCLRateGuard())

    class _FakeResp:
        text = (
            "<?xml version='1.0'?><wowhead><htmlTooltip><![CDATA["
            "<!--stat7-->+1000 Stamina<br>]]></htmlTooltip></wowhead>"
        )

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp())

    assert item_db.fetch_item_stats_wowhead(999999) == {"stamina": 1000}


def test_public_wowhead_stats_fetch_blocked_once_budget_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")
    monkeypatch.delenv("SIMF_ITEM_DB_OFFLINE", raising=False)
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    spent = wcl_budget.WCLRateGuard(per_minute=1, per_hour=1)
    spent.check()  # window now full
    monkeypatch.setattr(wcl_budget, "item_lookup_guard", lambda: spent)

    def _boom(*a, **k):
        raise AssertionError("a budget-exhausted public box must not make a network request")

    monkeypatch.setattr(item_db.requests, "get", _boom)
    assert item_db.fetch_item_stats_wowhead(999999) is None


def test_public_budget_frees_as_the_window_rolls(monkeypatch, tmp_path):
    """The replacement for PR #487's fixed-bucket reset test. A rolling window
    frees the slot 60 s after the request that used it — not at an arbitrary
    bucket boundary, which is what let the old counter admit 2x the cap."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")
    monkeypatch.delenv("SIMF_ITEM_DB_OFFLINE", raising=False)
    monkeypatch.setenv("SIMF_PUBLIC", "1")
    now = [1000.0]
    guard = wcl_budget.WCLRateGuard(per_minute=1, per_hour=10, clock=lambda: now[0])
    monkeypatch.setattr(wcl_budget, "item_lookup_guard", lambda: guard)

    assert item_db._live_wowhead_allowed() is True
    assert item_db._live_wowhead_allowed() is False  # window full
    now[0] += 61
    assert item_db._live_wowhead_allowed() is True


# ─── nsockets (empty-socket detection) ─────────────────────────────────────


def test_fetch_item_socket_count_wowhead_parses_nsockets(monkeypatch, tmp_path):
    """The real signal this function exists for: a ``jsonEquip`` CDATA block
    carrying ``"nsockets":N`` — confirmed live against a real Wowhead XML
    response (Necklace of the Twisting Void, item=151309) — must resolve to
    the integer N, independent of whatever gems the player currently has
    socketed (this queries the item's own static data)."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")

    fake_xml = (
        "<?xml version='1.0'?><wowhead>"
        '<jsonEquip><![CDATA["hastertng":170,"itemSquishEraId":0,"reqlevel":90,'
        '"sellprice":16734,"slotbak":2,"sta":995,"versatility":133,"nsockets":1]]>'
        "</jsonEquip></wowhead>"
    )

    class _FakeResp:
        text = fake_xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp())
    assert item_db.fetch_item_socket_count_wowhead(151309, bonus_ids=[13440, 6652]) == 1


def test_fetch_item_socket_count_wowhead_no_nsockets_key_is_none(monkeypatch, tmp_path):
    """An item with no socket (e.g. a chest with no gem slot) carries a
    ``jsonEquip`` block with no ``nsockets`` key at all — confirmed live
    against item=244570. Absence must resolve to ``None`` ("unknown/zero"),
    never a fabricated count."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")

    fake_xml = (
        "<?xml version='1.0'?><wowhead>"
        '<jsonEquip><![CDATA["itemSquishEraId":0,"reqlevel":90,"sellprice":9000,'
        '"slotbak":5,"sta":1200]]></jsonEquip></wowhead>'
    )

    class _FakeResp:
        text = fake_xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp())
    assert item_db.fetch_item_socket_count_wowhead(244570) is None


def test_fetch_item_socket_count_wowhead_caches_result(monkeypatch, tmp_path):
    """A second call must be served from disk cache, not a second network hit."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")

    calls = {"n": 0}
    fake_xml = (
        "<?xml version='1.0'?><wowhead><jsonEquip><![CDATA[\"nsockets\":2]]></jsonEquip></wowhead>"
    )

    class _FakeResp:
        text = fake_xml

        def raise_for_status(self):
            return None

    def _get(*a, **kw):
        calls["n"] += 1
        return _FakeResp()

    monkeypatch.setattr(item_db.requests, "get", _get)
    assert item_db.fetch_item_socket_count_wowhead(555) == 2
    assert item_db.fetch_item_socket_count_wowhead(555) == 2
    assert calls["n"] == 1


def test_offline_still_serves_cached_and_seed_hits(monkeypatch, tmp_path):
    """Offline must NOT break the demo: existing disk-cache hits still resolve."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.delenv("SIMF_ITEM_DB_OFFLINE", raising=False)
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    item_db._save_named_cache("wh_12345_base", {"stamina": 100})  # warm the cache

    monkeypatch.setenv("SIMF_ITEM_DB_OFFLINE", "1")

    def _boom(*a, **k):
        raise AssertionError("cached hit must not fall through to the network")

    monkeypatch.setattr(item_db.requests, "get", _boom)
    assert item_db.fetch_item_stats_wowhead(12345) == {"stamina": 100}


def test_item_cache_pruned_to_cap(monkeypatch, tmp_path):
    """The on-disk item cache is bounded so a paste can't fill the SD card."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_ITEM_CACHE_CAP", 5)
    for i in range(20):
        item_db._save_named_cache(f"k{i}", {"stamina": i})
    files = list(tmp_path.glob("*.json"))
    assert len(files) <= 5, f"prune should cap entries at 5, got {len(files)}"


def test_offline_default_off_for_owner(monkeypatch):
    """The owner (no SIMF_PUBLIC) is never forced offline — normal lookups work."""
    monkeypatch.delenv("SIMF_ITEM_DB_OFFLINE", raising=False)
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    assert item_db._is_offline() is False
