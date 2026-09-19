"""A single flaky network attempt must not sink an otherwise-working item
lookup. `resolve_equipped_stats` only surfaces "Couldn't fill in your
stats" to the user when EVERY equipped item's lookup comes back empty — so
a transient connection error/5xx on one attempt used to be indistinguishable
from a permanently-broken item. `_with_retries` (item_db.py) gives every
live GET/POST a couple of extra tries before giving up.
"""

from __future__ import annotations

from simf.io import item_db


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}


def _patch_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")


def _fake_xml(*lines: str) -> str:
    body = "".join(f"{line}<br>" for line in lines)
    return f"<?xml version='1.0'?><wowhead><htmlTooltip><![CDATA[{body}]]></htmlTooltip></wowhead>"


def test_wowhead_fetch_succeeds_after_two_transient_failures(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(item_db.time, "sleep", lambda _seconds: None)
    fake_xml = _fake_xml("<!--stat7-->+1000 Stamina")
    calls = {"n": 0}

    def _flaky_get(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("simulated transient network failure")
        return _FakeResp(fake_xml)

    monkeypatch.setattr(item_db.requests, "get", _flaky_get)

    stats = item_db.fetch_item_stats_wowhead(151333)
    assert stats == {"stamina": 1000}
    assert calls["n"] == 3


def test_wowhead_fetch_gives_up_after_exhausting_retries(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(item_db.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    def _always_fails(*_a, **_kw):
        calls["n"] += 1
        raise ConnectionError("simulated permanent network failure")

    monkeypatch.setattr(item_db.requests, "get", _always_fails)

    assert item_db.fetch_item_stats_wowhead(151333) is None
    assert calls["n"] == item_db._RETRY_ATTEMPTS


def test_blizzard_token_fetch_survives_one_flaky_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(item_db, "_SIMF_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_TOKEN_FILE", tmp_path / "blizzard_token.json")
    monkeypatch.setattr(item_db.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(item_db, "_load_credentials", lambda: ("id", "secret"))
    calls = {"n": 0}

    class _TokenResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"access_token": "tok123", "expires_in": 86400}

    def _flaky_post(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("simulated transient timeout")
        return _TokenResp()

    monkeypatch.setattr(item_db.requests, "post", _flaky_post)

    token = item_db._get_token("eu")
    assert token == "tok123"  # noqa: S105 — fake test token, not a real secret
    assert calls["n"] == 2


def test_get_token_returns_none_after_retries_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(item_db, "_SIMF_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_TOKEN_FILE", tmp_path / "blizzard_token.json")
    monkeypatch.setattr(item_db.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(item_db, "_load_credentials", lambda: ("id", "secret"))

    def _always_fails(*_a, **_kw):
        raise TimeoutError("simulated permanent timeout")

    monkeypatch.setattr(item_db.requests, "post", _always_fails)

    assert item_db._get_token("eu") is None


class _JsonResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_fetch_item_stats_resolves_via_blizzard_after_token(monkeypatch, tmp_path):
    """Full live-path exercise of the Blizzard fallback (Wowhead down/no
    bonus_ids case): token obtained, GET succeeds, preview_item.stats parsed."""
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(item_db, "_get_token", lambda region: "tok")
    payload = {
        "preview_item": {
            "stats": [
                {"type": {"type": "STAMINA"}, "value": 5000},
                {"type": {"type": "HASTE_RATING"}, "value": 100},
            ]
        }
    }
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _JsonResp(payload))

    stats = item_db.fetch_item_stats(151333)
    assert stats == {"stamina": 5000, "haste_rating": 100}


def test_fetch_item_stats_returns_none_when_lookup_fails_after_token(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(item_db.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(item_db, "_get_token", lambda region: "tok")

    def _always_fails(*_a, **_kw):
        raise ConnectionError("simulated permanent network failure")

    monkeypatch.setattr(item_db.requests, "get", _always_fails)

    assert item_db.fetch_item_stats(151333) is None


def test_search_items_returns_results_after_token(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(item_db, "_get_token", lambda region: "tok")
    payload = {
        "results": [
            {
                "data": {
                    "id": 12345,
                    "name": {"en_US": "Test Epic Helm"},
                    "level": 285,
                    "quality": {"type": "EPIC"},
                    "inventory_type": {"type": "HEAD"},
                }
            }
        ]
    }
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _JsonResp(payload))

    results = item_db.search_items("Test Epic")
    assert results == [
        {"id": 12345, "name": "Test Epic Helm", "ilvl": 285, "quality": "EPIC", "slot": "HEAD"}
    ]


def test_search_items_returns_empty_when_lookup_fails_after_token(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(item_db.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(item_db, "_get_token", lambda region: "tok")

    def _always_fails(*_a, **_kw):
        raise ConnectionError("simulated permanent network failure")

    monkeypatch.setattr(item_db.requests, "get", _always_fails)

    assert item_db.search_items("Test Epic") == []
