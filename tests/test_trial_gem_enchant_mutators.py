"""ui/state.py's enchant/gem trial mutators.

Code inspection (2026-08-07 launch-readiness pass) found TrialState.with_
enchant_override/with_gem_override — the pure trial_swap.py primitives,
already tested in isolation in test_trial_swap.py — had no caller anywhere
in src/: the three existing mutators (_apply_trial_swap/_revert_trial_slot/
_reset_trial_swaps) only ever touched `_trial_swaps` (whole-item swaps).
Live click-through confirmed the slot dialog showed a real "current -> best"
gem/enchant recommendation with a computed eHP delta but no button to act on
it. This file tests the two new mutators (_apply_trial_gem/_apply_trial_
enchant) and the matching updates to _trial_state()/_revert_trial_slot/
_reset_trial_swaps so all three trial channels round-trip through session
state identically.
"""

from __future__ import annotations

import simf.ui.state as state
from simf.io.simc_import import ItemSpec


def _session(**overrides) -> dict:
    base = {"char_data": {"class_spec": "protection_warrior"}}
    base.update(overrides)
    return base


def test_apply_trial_enchant_sets_session_state(monkeypatch):
    ss = _session()
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._apply_trial_enchant("chest", 1236054)
    assert ss["_trial_enchants"] == {"chest": 1236054}


def test_apply_trial_enchant_preserves_other_slots(monkeypatch):
    ss = _session(_trial_enchants={"legs": 1236072})
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._apply_trial_enchant("chest", 1236054)
    assert ss["_trial_enchants"] == {"legs": 1236072, "chest": 1236054}


def test_apply_trial_gem_sets_full_gem_ids_list(monkeypatch):
    item = ItemSpec(slot="neck", item_id=50228, gem_ids=[240983])
    ss = _session(simc_equipped={"neck": item})
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._apply_trial_gem("neck", 0, 240894)
    assert ss["_trial_gems"] == {"neck": [240894]}


def test_apply_trial_gem_preserves_other_sockets_on_multi_socket_item(monkeypatch):
    item = ItemSpec(slot="finger1", item_id=999, gem_ids=[111, 222])
    ss = _session(simc_equipped={"finger1": item})
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._apply_trial_gem("finger1", 1, 333)
    assert ss["_trial_gems"] == {"finger1": [111, 333]}


def test_apply_trial_gem_on_genuinely_empty_socket_pads_with_falsy(monkeypatch):
    """An item with zero gem_ids, trialing socket index 1 (its second
    socket), must leave index 0 empty (falsy — the same convention
    sockets_from_equipped uses for "no gem here") rather than crashing on a
    short list."""
    item = ItemSpec(slot="head", item_id=1, gem_ids=[])
    ss = _session(simc_equipped={"head": item})
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._apply_trial_gem("head", 1, 555)
    ids = ss["_trial_gems"]["head"]
    assert len(ids) == 2
    assert not ids[0]
    assert ids[1] == 555


def test_apply_trial_gem_builds_on_top_of_existing_trial(monkeypatch):
    """Trialing socket 1 after socket 0 was already trialed must keep
    socket 0's trial — _equipped() already reflects it via apply_trials."""
    item = ItemSpec(slot="finger1", item_id=999, gem_ids=[111, 222])
    ss = _session(simc_equipped={"finger1": item}, _trial_gems={"finger1": [777, 222]})
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._apply_trial_gem("finger1", 1, 333)
    assert ss["_trial_gems"] == {"finger1": [777, 333]}


def test_trial_state_reads_all_three_channels(monkeypatch):
    ss = _session(
        _trial_swaps={"head": "X"},
        _trial_enchants={"chest": 1},
        _trial_gems={"neck": [2]},
    )
    monkeypatch.setattr(state, "_ss", lambda: ss)
    trial = state._trial_state()
    assert trial.swaps == {"head": "X"}
    assert trial.enchant_overrides == {"chest": 1}
    assert trial.gem_overrides == {"neck": [2]}


def test_reset_trial_swaps_clears_all_three_channels(monkeypatch):
    ss = _session(
        _trial_swaps={"head": "X"},
        _trial_enchants={"chest": 1},
        _trial_gems={"neck": [2]},
    )
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._reset_trial_swaps()
    assert "_trial_swaps" not in ss
    assert "_trial_enchants" not in ss
    assert "_trial_gems" not in ss


def test_revert_trial_slot_drops_enchant_and_gem_for_that_slot_only(monkeypatch):
    ss = _session(
        _trial_enchants={"chest": 1, "legs": 2},
        _trial_gems={"neck": [3]},
    )
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._revert_trial_slot("chest")
    assert ss["_trial_enchants"] == {"legs": 2}
    assert ss["_trial_gems"] == {"neck": [3]}


def test_revert_trial_slot_pops_everything_when_nothing_left_active(monkeypatch):
    ss = _session(_trial_gems={"neck": [3]})
    monkeypatch.setattr(state, "_ss", lambda: ss)
    state._revert_trial_slot("neck")
    assert "_trial_swaps" not in ss
    assert "_trial_enchants" not in ss
    assert "_trial_gems" not in ss
