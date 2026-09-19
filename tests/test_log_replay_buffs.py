"""``io.log_replay.load_replay``'s ``buff_ability_ids`` parameter.

Mirrors ``wcl_replay.adapt_events``'s ``buff_windows`` stamping
(tests/test_wcl_buff_windows.py) for the LOCAL log path — before this, the
local replay used by ``simf calibrate-k``'s ratified Prot Warrior corpus had
no way to populate ``event.active_buffs`` at all (confirmed absent by
``classes/blood_death_knight.py``'s own comment: "io/log_replay.load_replay
never stamps event.active_buffs at all"). Window-gated mitigation layers
(Keep Your Feet on the Ground) depend on this.
"""

from __future__ import annotations

import pathlib

import pytest

MGT_LOG = (
    pathlib.Path(__file__).resolve().parents[1] / "examples" / "WoWCombatLog-051526_210245.txt"
)
TARGET = "Brutoh-Uldum-EU"
KYFOTG = 438591


def _skip_if_missing():
    if not MGT_LOG.exists():
        pytest.skip(f"calibration corpus log not present at {MGT_LOG}")


def test_buff_ability_ids_none_leaves_active_buffs_empty():
    """Default (no buff_ability_ids): every event's active_buffs stays empty —
    bit-identical to pre-this-change behaviour."""
    _skip_if_missing()
    from simf.io.log_replay import load_replay

    replay = load_replay(MGT_LOG, TARGET, run_index=0)
    assert replay.event_count > 0
    assert all(e.active_buffs == frozenset() for e in replay.events)


def test_buff_ability_ids_stamps_real_kyfotg_windows():
    """With buff_ability_ids={KYFOTG}, at least some non-self events fall
    inside a real logged Keep Your Feet on the Ground window (spell 438591
    fires 100-670 times in every one of the ratified corpus's 12 log files)
    and at least some fall outside — this buff is real but not permanently
    up, so a test that only checked "at least one true" could pass on a
    buggy always-on implementation too."""
    _skip_if_missing()
    from simf.io.log_replay import load_replay

    replay = load_replay(MGT_LOG, TARGET, run_index=0, buff_ability_ids={KYFOTG})
    non_self = [e for e in replay.events if not e.is_self_inflicted]
    assert non_self, "expected non-self-inflicted damage events in this replay"

    inside = [e for e in non_self if KYFOTG in e.active_buffs]
    outside = [e for e in non_self if KYFOTG not in e.active_buffs]
    assert inside, "expected at least one event inside a real KYFOTG window"
    assert outside, "expected at least one event outside a real KYFOTG window"


def test_buff_ability_ids_unrequested_spell_never_appears():
    """Requesting only KYFOTG must never stamp an unrelated spell id, even
    if that spell also fires as a self-buff in this log (e.g. Avatar)."""
    _skip_if_missing()
    from simf.io.log_replay import load_replay

    replay = load_replay(MGT_LOG, TARGET, run_index=0, buff_ability_ids={KYFOTG})
    AVATAR = 107574
    assert all(AVATAR not in e.active_buffs for e in replay.events)
