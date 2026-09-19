"""Batch D — HP-trough ledger helper tests.

Pins the pure-data math (``compute_hp_trough_ledger``) — dedup-by-gap,
lowest-first selection, chronological output — mirroring the
``segment_risk_timeline`` / ``tail_risk_chart`` split. The parsing itself
(``current_hp``/``max_hp`` extraction, the SWING-vs-SPELL asymmetry) is
pinned separately against real log lines in
``test_combat_log_positions.py``; this file only exercises the ledger's
own selection logic given already-parsed events.
"""

from __future__ import annotations

from simf.io.combat_log import DamageTakenEvent
from simf.ui.helpers.hp_trough_ledger import (
    compute_hp_trough_ledger,
    render_hp_trough_ledger,
)


def _evt(
    t: float,
    current_hp: int | None,
    max_hp: int | None,
    spell_name: str = "Hit",
    amount: int = 1000,
) -> DamageTakenEvent:
    return DamageTakenEvent(
        time_s=t,
        event_type="SPELL_DAMAGE",
        source_name="Boss",
        spell_name=spell_name,
        school="physical",
        amount=amount,
        base_amount=amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
        current_hp=current_hp,
        max_hp=max_hp,
    )


def test_no_hp_events_returns_empty():
    events = [_evt(10.0, None, None)]
    assert compute_hp_trough_ledger(events, run_start=0.0) == []


def test_picks_lowest_hp_pct_first_in_chronological_output():
    events = [
        _evt(100.0, 900, 1000),  # 90%
        _evt(200.0, 300, 1000),  # 30% — the real trough
        _evt(300.0, 800, 1000),  # 80%
    ]
    rows = compute_hp_trough_ledger(events, run_start=0.0, top_n=2)
    # top_n=2 lowest are 30% (t=200) and 80% (t=300) — 90% (t=100) loses out.
    assert [round(r.hp_pct, 2) for r in rows] == [0.3, 0.8]
    # Returned in chronological order, not ranked order.
    assert [r.rel_time_s for r in rows] == [200.0, 300.0]


def test_dedups_events_within_min_gap():
    events = [
        _evt(100.0, 300, 1000),  # 30%
        _evt(105.0, 290, 1000),  # 29%, 5s later — same burst, collapses into one row
        _evt(400.0, 500, 1000),  # 50%, far away — its own row
    ]
    rows = compute_hp_trough_ledger(events, run_start=0.0, top_n=5)
    assert len(rows) == 2
    assert round(rows[0].hp_pct, 2) == 0.29  # the lower of the clustered pair wins
    assert round(rows[1].hp_pct, 2) == 0.5


def test_events_without_parsed_hp_are_ignored():
    events = [
        _evt(100.0, None, None),  # SWING_DAMAGE-shaped: HP wasn't parsed
        _evt(200.0, 400, 1000),
    ]
    rows = compute_hp_trough_ledger(events, run_start=0.0)
    assert len(rows) == 1
    assert rows[0].rel_time_s == 200.0


def test_row_carries_ability_and_source_for_display():
    events = [_evt(50.0, 400, 1000, spell_name="Arcane Bolt", amount=5000)]
    rows = compute_hp_trough_ledger(events, run_start=0.0)
    assert rows[0].spell_name == "Arcane Bolt"
    assert rows[0].amount == 5000
    assert rows[0].source_name == "Boss"


def test_rel_time_s_offset_by_run_start():
    events = [_evt(1050.0, 400, 1000)]
    rows = compute_hp_trough_ledger(events, run_start=1000.0)
    assert rows[0].rel_time_s == 50.0


def test_zero_max_hp_is_ignored_not_a_divide_by_zero():
    events = [_evt(10.0, 0, 0)]
    assert compute_hp_trough_ledger(events, run_start=0.0) == []


def test_render_is_noop_on_empty():
    # Must not raise or attempt to import streamlit when there's nothing to
    # render — the common case on ACL-off logs.
    render_hp_trough_ledger([])
