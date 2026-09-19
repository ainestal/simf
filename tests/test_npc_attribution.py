"""Tests for Phase B: per-death NPC+ability attribution and cross-log aggregation."""

from simf.io.combat_log import DamageTakenEvent, DeathRecord
from simf.io.death_analysis import (
    DeathEvent,
    cross_log_npc_attribution,
    kill_blow,
    npc_attribution,
    wowhead_npc_url,
    wowhead_spell_url,
)


def _ev(
    time_s,
    spell="Crushing",
    spell_id=12345,
    source="Circuit Seer",
    npc_id=248373,
    school="physical",
    amount=1_000_000,
):
    return DamageTakenEvent(
        time_s=time_s,
        event_type="SPELL_DAMAGE",
        source_name=source,
        spell_name=spell,
        school=school,
        amount=amount,
        base_amount=amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
        source_guid=f"Creature-0-0-0-0-{npc_id}-0" if npc_id else "",
        source_npc_id=npc_id,
        spell_id=spell_id,
    )


def _de(*evs, death_t=10.0):
    return DeathEvent(
        death=DeathRecord(time_s=death_t, rel_time_s=death_t),
        preceding=list(evs),
        total_damage_window=sum(e.amount for e in evs),
        max_hit=max((e.amount for e in evs), default=0),
        num_hits=len(evs),
    )


def test_wowhead_urls():
    assert wowhead_npc_url(248373) == "https://www.wowhead.com/npc=248373"
    assert wowhead_npc_url(None) is None
    assert wowhead_spell_url(204021) == "https://www.wowhead.com/spell=204021"
    assert wowhead_spell_url(None) is None


def test_npc_attribution_groups_repeated_swings_to_one_row():
    """Three Circuit-Seer auto-attacks collapse to one row with hits=3, not three rows."""
    de = _de(
        _ev(time_s=5.0, spell="auto-attack", spell_id=None, amount=1_000_000),
        _ev(time_s=6.0, spell="auto-attack", spell_id=None, amount=1_200_000),
        _ev(time_s=7.0, spell="auto-attack", spell_id=None, amount=900_000),
    )
    rows = npc_attribution(de)
    assert len(rows) == 1
    r = rows[0]
    assert r.npc_id == 248373
    assert r.npc_name == "Circuit Seer"
    assert r.spell_name == "auto-attack"
    assert r.spell_id is None
    assert r.hits == 3
    assert r.damage_to_hp == 3_100_000
    assert abs(r.share_of_window - 1.0) < 0.01


def test_npc_attribution_ranks_by_damage_and_includes_urls():
    de = _de(
        _ev(time_s=5.0, spell="Small Hit", spell_id=111, amount=500_000),
        _ev(time_s=6.0, spell="Big Hit", spell_id=222, amount=3_000_000),
    )
    rows = npc_attribution(de)
    assert rows[0].spell_name == "Big Hit"
    assert rows[0].spell_url == "https://www.wowhead.com/spell=222"
    assert rows[0].npc_url == "https://www.wowhead.com/npc=248373"
    assert rows[1].spell_name == "Small Hit"


def test_npc_attribution_handles_unknown_npc_id():
    """When source_npc_id is None (player pet, environment damage), fall back to source_name."""
    de = _de(
        _ev(time_s=5.0, spell="Fall Damage", npc_id=None, source="Environment"),
    )
    rows = npc_attribution(de)
    assert len(rows) == 1
    assert rows[0].npc_id is None
    assert rows[0].npc_url is None
    assert rows[0].npc_name == "Environment"


def test_kill_blow_returns_last_hit_not_biggest():
    """Kill blow is the temporally final event, even if it isn't the largest."""
    de = _de(
        _ev(time_s=5.0, spell="Huge", spell_id=111, amount=5_000_000),
        _ev(time_s=9.5, spell="Final Tap", spell_id=222, amount=100_000),
    )
    kb = kill_blow(de)
    assert kb is not None
    assert kb.spell_name == "Final Tap"


def test_kill_blow_empty_window():
    de = DeathEvent(
        death=DeathRecord(time_s=10.0, rel_time_s=10.0),
        preceding=[],
        total_damage_window=0,
        max_hit=0,
        num_hits=0,
    )
    assert kill_blow(de) is None


def test_cross_log_aggregates_across_runs():
    """Two deaths in two runs, both hit by Circuit Seer — kill count = 2."""
    de1 = _de(_ev(time_s=5.0, amount=2_000_000))
    de2 = _de(_ev(time_s=6.0, amount=3_000_000))
    rows = cross_log_npc_attribution([("Workshop +14", [de1]), ("Nexus +15", [de2])])
    assert len(rows) == 1
    r = rows[0]
    assert r["npc_id"] == 248373
    assert r["fatal_kills"] == 2
    assert r["fatal_damage"] == 5_000_000
    assert r["runs"] == 2
    assert r["npc_url"] == "https://www.wowhead.com/npc=248373"


def test_cross_log_ranks_npcs_by_damage():
    de_big = _de(
        _ev(time_s=5.0, source="Boss", npc_id=999, amount=8_000_000),
        _ev(time_s=5.5, source="Adds", npc_id=111, amount=1_000_000),
    )
    rows = cross_log_npc_attribution([("Run1", [de_big])])
    assert rows[0]["npc_name"] == "Boss"
    assert rows[1]["npc_name"] == "Adds"


def test_cross_log_kill_count_dedupes_within_death():
    """Same NPC hitting you 5 times in one death = 1 fatal kill, not 5."""
    de = _de(
        _ev(time_s=5.0, amount=1_000_000),
        _ev(time_s=6.0, amount=1_000_000),
        _ev(time_s=7.0, amount=1_000_000),
    )
    rows = cross_log_npc_attribution([("Run1", [de])])
    assert rows[0]["fatal_kills"] == 1
