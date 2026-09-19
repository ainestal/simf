"""`_char_data_effective()` — char_data with an active trial's stat delta
folded in.

Regression coverage for the bug where a trial swap changed what the gear
cards showed (they read `_equipped()`) but left the key-level verdict panel,
skill ladder, and reproduction hash scoring the PRE-trial gear (they built
`Character.from_dict(_ss()["char_data"])` directly, which a trial swap never
touches — only `_trial_swaps` does).
"""

from __future__ import annotations

from dataclasses import dataclass

import simf.ui.state as state


@dataclass
class _FakeItem:
    item_id: int
    name: str


def _fake_resolve_equipped_stats(items, class_spec=""):
    """Deterministic stand-in for the real (networked) item-stat resolver:
    each item's contribution is just its own `item_id`, on `haste_rating`."""
    total = 0
    for item in items.values():
        if item is not None:
            total += item.item_id
    return {"haste_rating": total} if total else {}


def test_char_data_effective_passes_through_when_no_trial_active(monkeypatch):
    monkeypatch.setattr(
        state, "_ss", lambda: {"char_data": {"name": "Brutoh", "haste_rating": 500}}
    )
    assert state._char_data_effective() == {"name": "Brutoh", "haste_rating": 500}


def test_char_data_effective_passes_through_with_no_character(monkeypatch):
    monkeypatch.setattr(state, "_ss", lambda: {})
    assert state._char_data_effective() == {}


def test_char_data_effective_reflects_trial_swap_stat_delta(monkeypatch):
    ss = {
        "char_data": {"name": "Brutoh", "class_spec": "protection_warrior", "haste_rating": 500},
        "simc_equipped": {"trinket1": _FakeItem(item_id=100, name="Old Trinket")},
        "_trial_swaps": {"trinket1": _FakeItem(item_id=340, name="New Trinket")},
    }
    monkeypatch.setattr(state, "_ss", lambda: ss)
    monkeypatch.setattr(state, "_resolve_equipped_stats", _fake_resolve_equipped_stats)

    effective = state._char_data_effective()

    # baseline resolves to haste 100, trial-merged resolves to haste 340 —
    # delta (+240) lands on top of char_data's own (unrelated) value of 500.
    assert effective["haste_rating"] == 500 + (340 - 100)
    # Non-gear fields pass through unchanged.
    assert effective["name"] == "Brutoh"
    # The original char_data in session state is untouched (Reset stays exact).
    assert ss["char_data"]["haste_rating"] == 500


def test_char_data_effective_reverts_once_trial_is_cleared(monkeypatch):
    ss = {
        "char_data": {"name": "Brutoh", "class_spec": "protection_warrior", "haste_rating": 500},
        "simc_equipped": {"trinket1": _FakeItem(item_id=100, name="Old Trinket")},
        "_trial_swaps": {},
    }
    monkeypatch.setattr(state, "_ss", lambda: ss)
    monkeypatch.setattr(state, "_resolve_equipped_stats", _fake_resolve_equipped_stats)

    assert state._char_data_effective() == ss["char_data"]


def test_char_data_effective_returns_base_when_item_db_unavailable(monkeypatch):
    ss = {
        "char_data": {"name": "Brutoh", "haste_rating": 500},
        "simc_equipped": {"trinket1": _FakeItem(item_id=100, name="Old Trinket")},
        "_trial_swaps": {"trinket1": _FakeItem(item_id=340, name="New Trinket")},
    }
    monkeypatch.setattr(state, "_ss", lambda: ss)
    monkeypatch.setattr(state, "_ITEM_DB", None)
    assert state._char_data_effective() == ss["char_data"]
