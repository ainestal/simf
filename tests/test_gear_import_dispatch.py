"""Online gear-source dispatcher — source selection + fallback logic."""

from __future__ import annotations

import simf.io.raider_io as raider_io
from simf.io import armory, gear_import


class _Fake:
    """Stand-in for HydrateResult — only `.source` is inspected here."""

    def __init__(self, source):
        self.source = source
        self.char_data = {}
        self.equipped = {}
        self.summary = ""


def test_auto_prefers_blizzard_when_configured(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: True)
    monkeypatch.setattr(armory, "fetch_character_gear", lambda n, r, reg: _Fake("blizzard"))
    monkeypatch.setattr(raider_io, "fetch_character_gear", lambda n, r, reg: _Fake("raiderio"))
    res, used = gear_import.fetch_online_gear("B", "u", "eu", "auto")
    assert used == "blizzard" and res.source == "blizzard"


def test_auto_uses_raiderio_when_unconfigured(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: False)
    monkeypatch.setattr(raider_io, "fetch_character_gear", lambda n, r, reg: _Fake("raiderio"))
    _res, used = gear_import.fetch_online_gear("B", "u", "eu", "auto")
    assert used == "raiderio"


def test_auto_falls_back_when_blizzard_misses(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: True)
    monkeypatch.setattr(armory, "fetch_character_gear", lambda n, r, reg: None)
    monkeypatch.setattr(raider_io, "fetch_character_gear", lambda n, r, reg: _Fake("raiderio"))
    _res, used = gear_import.fetch_online_gear("B", "u", "eu", "auto")
    assert used == "raiderio"


def test_explicit_blizzard_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: True)
    monkeypatch.setattr(armory, "fetch_character_gear", lambda n, r, reg: None)
    # raiderio would succeed, but explicit blizzard must not use it
    monkeypatch.setattr(raider_io, "fetch_character_gear", lambda n, r, reg: _Fake("raiderio"))
    res, used = gear_import.fetch_online_gear("B", "u", "eu", "blizzard")
    assert res is None and used == "blizzard"


def test_explicit_raiderio(monkeypatch):
    monkeypatch.setattr(raider_io, "fetch_character_gear", lambda n, r, reg: _Fake("raiderio"))
    res, used = gear_import.fetch_online_gear("B", "u", "eu", "raiderio")
    assert used == "raiderio" and res.source == "raiderio"


def test_auto_returns_none_when_both_miss(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: False)
    monkeypatch.setattr(raider_io, "fetch_character_gear", lambda n, r, reg: None)
    res, _used = gear_import.fetch_online_gear("B", "u", "eu", "auto")
    assert res is None
