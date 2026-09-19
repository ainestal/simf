"""Tests for the WCL defensive-coverage fast-follow.

The local-log surface builds the hit-vs-coverage coaching report by scanning a
file (``_cached_coverage_report``); the WCL surface builds the SAME report from
fetched self-buff windows + the analysis bundle's damage events
(``wcl_bridge.build_wcl_coverage_report``). These tests pin:

  - the join credits a buff window that was up over a big hit, and leaves an
    uncovered big hit uncovered;
  - only buff-detect lever ids are fetched (Demo Shout, the lone
    ``debuff_on_source`` lever, is never requested — and never credited, since
    the WCL path has no source-debuff fetch yet, a deliberate under-credit);
  - None when there's nothing to render (no spec, unknown spec, unresolvable
    actor);
  - the actor-id fallback (``_fetch_actor_id``) when the picker didn't hand one;
  - the Buffs fetch is cached, so a re-render issues no second network call;
  - the shared renderer surfaces the section when handed a ``coverage_report``.

The network layer (``fetch_buff_windows`` / ``_fetch_actor_id``) is mocked —
``fetch_buff_windows`` itself is covered in ``test_wcl_buff_windows.py``.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from simf.core import coaching
from simf.core.constants import load_constants
from simf.io import wcl_bridge
from simf.io.combat_log import DamageTakenEvent
from simf.io.wcl_api import WCLFight, WCLReport, fight_to_run

# Prot Warrior registry: 871 Shield Wall (all/major/buff), 12975 Last Stand
# (buff), 132404 Shield Block (continuous/buff), 1160 Demoralizing Shout
# (minor/debuff_on_source — the ONLY non-buff lever in the whole registry).
SHIELD_WALL = 871
DEMO_SHOUT = 1160


@pytest.fixture(autouse=True)
def _no_debuff_fetch(monkeypatch):
    """Default every test to NO source-debuff windows so tests not about Demo
    Shout never touch the network (warrior builds now issue a Debuffs fetch).
    Tests that exercise the debuff join override this with their own patch."""
    monkeypatch.setattr(wcl_bridge, "fetch_source_debuff_windows", lambda *a, **k: {})


@pytest.fixture(autouse=True)
def _no_interrupt_fetch(monkeypatch):
    """Default every test to NO WCL-proven interrupts (every build now issues
    an Interrupts fetch). Tests exercising the interrupt lever itself override
    this with their own patch — see ``test_wcl_interrupts.py`` for the fetcher
    unit tests and the "interrupt lever" section below for the join wire-up."""
    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", lambda *a, **k: frozenset())


@pytest.fixture(autouse=True)
def _no_own_cast_fetch(monkeypatch):
    """Default every test to NO own-interrupt-cast times (every build for a
    spec with a registered interrupt ability now issues a Casts fetch). Tests
    exercising the kick-availability split override this with their own
    patch — see ``test_wcl_own_casts.py`` for the fetcher unit tests."""
    monkeypatch.setattr(wcl_bridge, "fetch_own_cast_times", lambda *a, **k: ())


def _report_fight():
    # report at abs 1_000_000 ms; fight [0, 100_000] ms → abs [1000s, 1100s].
    report = WCLReport(code="RPT", start_time_ms=1_000_000, fights=[])
    fight = WCLFight(id=1, name="Ara-Kara", start_time_ms=0, end_time_ms=100_000, key_level=18)
    return report, fight


def _ev(amount, t, *, school="physical", spell="Hit", sid=1, src="Mob", guid="g"):
    return DamageTakenEvent(
        time_s=t,
        event_type="SPELL_DAMAGE",
        source_name=src,
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
        source_guid=guid,
        spell_id=sid,
    )


def _events_with_two_big_hits():
    """25 events: filler + a big COVERED hit at 1010 + a big UNCOVERED hit at 1020."""
    evs = [_ev(1000, 1001.0 + i) for i in range(23)]
    evs.append(_ev(500_000, 1010.0, spell="Covered Slam"))  # under Shield Wall window
    evs.append(_ev(400_000, 1020.0, spell="Naked Slam"))  # nothing up
    return evs


# ─── build_wcl_coverage_report: the join ─────────────────────────────────────


def test_credits_buff_window_over_big_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wcl_bridge, "fetch_buff_windows", lambda *a, **k: {SHIELD_WALL: [(1008.0, 1012.0)]}
    )
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    events = _events_with_two_big_hits()

    rep = wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=events,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )

    assert rep is not None
    assert not rep.too_short  # 25 events ≥ min_events
    by_time = {round(h.time_s): h for h in rep.top_hits}
    assert 1010 in by_time and 1020 in by_time  # both big hits are top-N
    assert by_time[1010].covered, "Shield Wall up at 1010 → covered"
    assert by_time[1020].uncovered, "nothing up at 1020 → uncovered"
    assert rep.n_covered >= 1


def test_only_buff_levers_are_fetched_demo_shout_never_requested(monkeypatch, tmp_path):
    captured = {}

    def _fake(report, fight, actor_id, token, *, ability_ids=None, cache_dir=None):
        captured["ability_ids"] = ability_ids
        return {}

    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)

    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )

    ids = captured["ability_ids"]
    assert SHIELD_WALL in ids
    assert DEMO_SHOUT not in ids, "Demo Shout is debuff_on_source — not fetchable as a buff"
    # Every requested id is a buff-detect lever for the spec.
    levers = coaching.levers_for_spec("protection_warrior", load_constants())
    buff_ids = {lv.spell_id for lv in levers if lv.detect == "buff"}
    assert set(ids) == buff_ids


def test_demo_shout_credited_spawn_precisely(monkeypatch, tmp_path):
    """Demo Shout (the lone debuff_on_source lever) is now credited on the WCL
    path — but ONLY on a hit whose source spawn carried the debuff. The hit at
    1020 comes from spawn "demo" (which the tank debuffed 1018-1028) → partial;
    the hit at 1030 comes from spawn "other" (never debuffed) → uncovered, even
    though Demo Shout had a window open at that time on a DIFFERENT spawn. That
    is the spawn-precision over-credit guard."""
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    monkeypatch.setattr(
        wcl_bridge,
        "fetch_source_debuff_windows",
        # window keyed by the debuffed enemy's spawn key only
        lambda *a, **k: {"demo": [(1018.0, 1028.0)]},
    )
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    # 23 filler + a big hit from the debuffed spawn + a big hit from another spawn
    evs = [_ev(1000, 1001.0 + i) for i in range(23)]
    evs.append(_ev(500_000, 1020.0, spell="Debuffed Mob Slam", guid="demo"))
    evs.append(_ev(400_000, 1030.0, spell="Other Mob Slam", guid="other"))

    rep = wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=evs,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    assert rep is not None
    by_time = {round(h.time_s): h for h in rep.top_hits}
    # 1020: from the debuffed spawn, in-window → minor (partial) coverage.
    assert by_time[1020].partial, "debuffed spawn's hit reads as partial"
    assert "Demoralizing Shout" in by_time[1020].minor_by
    assert not by_time[1020].covered, "a minor debuff is never a clean (major) soak"
    # 1030: same-time Demo Shout window exists, but on a DIFFERENT spawn → NOT credited.
    assert by_time[1030].uncovered, "non-debuffed spawn's hit stays uncovered"
    assert "Demoralizing Shout" not in by_time[1030].covered_by
    assert "Demoralizing Shout" in rep.minor_levers_considered


def test_debuff_fetch_is_cached_no_second_network_call(monkeypatch, tmp_path):
    """The Debuffs fetch lands on its own cache key, so a re-build is a hit."""
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return {"demo": [(1018.0, 1028.0)]}

    monkeypatch.setattr(wcl_bridge, "fetch_source_debuff_windows", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    kw = dict(
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    assert calls["n"] == 1, "second build is a cache hit on the debuff fetch"


def test_no_debuff_fetch_for_spec_without_debuff_lever(monkeypatch, tmp_path):
    """A spec with no debuff_on_source lever (Guardian) issues no extra query —
    the Debuffs fetch cost stays zero for everyone but the warrior."""
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    called = {"n": 0}
    monkeypatch.setattr(
        wcl_bridge,
        "fetch_source_debuff_windows",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="guardian_druid",  # registry has no debuff_on_source lever
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    assert called["n"] == 0, "no debuff lever → no Debuffs fetch"


def test_none_when_no_spec(tmp_path):
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    assert (
        wcl_bridge.build_wcl_coverage_report(
            report,
            fight,
            "Brutoh",
            "tok",
            events=_events_with_two_big_hits(),
            run=run,
            class_spec=None,
            target_actor_id=7,
            cache_dir=tmp_path,
        )
        is None
    )


def test_none_for_unknown_spec_no_levers(monkeypatch, tmp_path):
    called = {"n": 0}
    monkeypatch.setattr(
        wcl_bridge, "fetch_buff_windows", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    rep = wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="frost_mage",  # not a tank spec → no registry levers
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    assert rep is None
    assert called["n"] == 0, "no levers → never touch the network"


def test_none_when_actor_unresolvable(monkeypatch, tmp_path):
    fetched = {"n": 0}
    monkeypatch.setattr(
        wcl_bridge, "fetch_buff_windows", lambda *a, **k: fetched.__setitem__("n", fetched["n"] + 1)
    )
    monkeypatch.setattr(wcl_bridge, "_fetch_actor_id", lambda *a, **k: None)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    rep = wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="protection_warrior",
        target_actor_id=None,  # picker didn't resolve one
        cache_dir=tmp_path,
    )
    assert rep is None
    assert fetched["n"] == 0, "no actor → can't query buffs → no fetch"


def test_actor_id_fallback_uses_fetch_actor_id(monkeypatch, tmp_path):
    seen = {}

    def _fake_buffs(report, fight, actor_id, token, *, ability_ids=None, cache_dir=None):
        seen["actor_id"] = actor_id
        return {}

    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", _fake_buffs)
    monkeypatch.setattr(wcl_bridge, "_fetch_actor_id", lambda code, name, token, **kwargs: 128)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="protection_warrior",
        target_actor_id=None,
        cache_dir=tmp_path,
    )
    assert seen["actor_id"] == 128


def test_buff_fetch_is_cached_no_second_network_call(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _fake(report, fight, actor_id, token, *, ability_ids=None, cache_dir=None):
        calls["n"] += 1
        return {SHIELD_WALL: [(1008.0, 1012.0)]}

    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    events = _events_with_two_big_hits()
    kw = dict(
        events=events,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    a = wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    b = wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    assert calls["n"] == 1, "second build is a cache hit on the windows fetch"
    assert a.n_covered == b.n_covered


# ─── interrupt lever: WCL parity for core/coaching.py's `interruptible` flag ─
#
# The local-log path (`ui/log_data.py`) has fed `interrupted_spell_ids` into
# `coaching.build_coverage_report` since PR #257; this closes the WCL-path gap
# that PR's own validation doc named as a deliberate follow-up (see
# `docs/validation/interruptible_cast_coverage_lever_2026_07_03.md`).


def test_wcl_interrupt_flags_matching_hit(monkeypatch, tmp_path):
    """A spell_id WCL proved interruptible flags the matching top hit, exactly
    like the local-log path's SPELL_INTERRUPT scan does."""
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", lambda *a, **k: frozenset({99}))
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    evs = [_ev(1000, 1001.0 + i) for i in range(23)]
    evs.append(_ev(500_000, 1010.0, spell="Kickable Cast", sid=99))
    evs.append(_ev(400_000, 1020.0, spell="Not Proven Interruptible", sid=100))

    rep = wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=evs,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    by_time = {round(h.time_s): h for h in rep.top_hits}
    assert by_time[1010].interruptible, "spell_id 99 was WCL-proven interrupted"
    assert not by_time[1020].interruptible, "spell_id 100 was never interrupted"
    assert rep.n_interruptible == 1


def test_wcl_interrupt_fetch_is_cached_no_second_network_call(monkeypatch, tmp_path):
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return frozenset({99})

    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    kw = dict(
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    assert calls["n"] == 1, "second build is a cache hit on the interrupts fetch"


def test_wcl_interrupt_fetch_shared_across_specs_on_same_fight(monkeypatch, tmp_path):
    """Interrupt evidence has nothing to do with which spec is being analyzed —
    a second build for a DIFFERENT spec on the SAME fight reuses the same
    cache entry rather than refetching identical data."""
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return frozenset({99})

    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    events = _events_with_two_big_hits()
    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=events,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "AnonGuardian1",
        "tok",
        events=events,
        run=run,
        class_spec="guardian_druid",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    assert calls["n"] == 1, "same fight's interrupt evidence is spec-independent"


# ─── kick-availability split: WCL parity for the cooldown-aware kick check ───


def test_wcl_kick_was_ready_flows_through(monkeypatch, tmp_path):
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", lambda *a, **k: frozenset({99}))
    monkeypatch.setattr(wcl_bridge, "fetch_own_cast_times", lambda *a, **k: (980.0,))  # 30s before
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    evs = [_ev(1000, 1001.0 + i) for i in range(23)]
    evs.append(_ev(500_000, 1010.0, spell="Kickable Cast", sid=99))

    rep = wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=evs,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    hit = next(h for h in rep.top_hits if round(h.time_s) == 1010)
    assert hit.interruptible is True
    assert hit.kick_was_ready is True  # cast 30s ago, Pummel CD is 15s
    assert rep.interrupt_ability_name == "Pummel"
    assert rep.n_kick_ready == 1


def test_wcl_kick_was_ready_false_when_cast_recently(monkeypatch, tmp_path):
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", lambda *a, **k: frozenset({99}))
    monkeypatch.setattr(wcl_bridge, "fetch_own_cast_times", lambda *a, **k: (1005.0,))  # 5s before
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    evs = [_ev(1000, 1001.0 + i) for i in range(23)]
    evs.append(_ev(500_000, 1010.0, spell="Kickable Cast", sid=99))

    rep = wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=evs,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    hit = next(h for h in rep.top_hits if round(h.time_s) == 1010)
    assert hit.kick_was_ready is False  # cast only 5s ago, Pummel CD is 15s
    assert rep.n_kick_on_cooldown == 1


def test_wcl_own_cast_fetch_is_cached_no_second_network_call(monkeypatch, tmp_path):
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", lambda *a, **k: frozenset({99}))
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return (980.0,)

    monkeypatch.setattr(wcl_bridge, "fetch_own_cast_times", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    kw = dict(
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    wcl_bridge.build_wcl_coverage_report(report, fight, "Brutoh", "tok", **kw)
    assert calls["n"] == 1, "second build is a cache hit on the own-casts fetch"


def test_wcl_own_cast_fetch_not_shared_across_specs_on_same_fight(monkeypatch, tmp_path):
    """UNLIKE the party-wide interrupts fetch, own-cast times are personal —
    a Prot Warrior's Pummel casts must not leak into a Guardian's report on
    the same fight, so each (target, spec) pair gets its own fetch."""
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", lambda *a, **k: frozenset({99}))
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return (980.0,)

    monkeypatch.setattr(wcl_bridge, "fetch_own_cast_times", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    events = _events_with_two_big_hits()
    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=events,
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "AnonGuardian1",
        "tok",
        events=events,
        run=run,
        class_spec="guardian_druid",
        target_actor_id=8,
        cache_dir=tmp_path,
    )
    assert calls["n"] == 2, "own-cast times are per-tank, not shared like party-wide interrupts"


def test_wcl_no_own_cast_fetch_when_hit_not_interruptible(monkeypatch, tmp_path):
    """No point fetching a kick timeline if nothing on the top-hits list was
    ever proven interruptible — but the fetch still fires today since it's
    unconditional on having a registered ability, not on n_interruptible > 0.
    This pins CURRENT behavior (fetch always fires for a registered spec) so
    a future optimization change is a deliberate, visible diff."""
    monkeypatch.setattr(wcl_bridge, "fetch_buff_windows", lambda *a, **k: {})
    monkeypatch.setattr(wcl_bridge, "fetch_interrupted_spell_ids", lambda *a, **k: frozenset())
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return ()

    monkeypatch.setattr(wcl_bridge, "fetch_own_cast_times", _fake)
    report, fight = _report_fight()
    run = fight_to_run(fight, report.start_time_ms)
    wcl_bridge.build_wcl_coverage_report(
        report,
        fight,
        "Brutoh",
        "tok",
        events=_events_with_two_big_hits(),
        run=run,
        class_spec="protection_warrior",
        target_actor_id=7,
        cache_dir=tmp_path,
    )
    assert calls["n"] == 1


# ─── render wire-up: the shared renderer surfaces a passed-in report ─────────


def test_render_log_analysis_surfaces_passed_coverage_report():
    """render_log_analysis(..., coverage_report=...) renders the coaching
    section. Uses a REAL summarize_events_direct summary (exactly what the WCL
    bundle hands the renderer in production) under a real Streamlit runtime."""

    def _script():
        import tempfile
        from pathlib import Path

        from simf.io import wcl_bridge
        from simf.io.combat_log import (
            DamageTakenEvent,
            summarize_events_direct,
        )
        from simf.io.wcl_api import WCLFight, WCLReport, fight_to_run
        from simf.ui import log_view

        # Patch the network fetches: Shield Wall up over the big hit at 1010,
        # no source debuffs (Demo Shout not exercised here).
        wcl_bridge.fetch_buff_windows = lambda *a, **k: {871: [(1008.0, 1012.0)]}
        wcl_bridge.fetch_source_debuff_windows = lambda *a, **k: {}
        wcl_bridge.fetch_interrupted_spell_ids = lambda *a, **k: frozenset()
        wcl_bridge.fetch_own_cast_times = lambda *a, **k: ()

        report = WCLReport(code="RPT", start_time_ms=1_000_000, fights=[])
        fight = WCLFight(id=1, name="Ara-Kara", start_time_ms=0, end_time_ms=100_000, key_level=18)
        run = fight_to_run(fight, report.start_time_ms)
        events = [
            DamageTakenEvent(
                time_s=1001.0 + i,
                event_type="SPELL_DAMAGE",
                source_name="Mob",
                spell_name="Hit",
                school="physical",
                amount=1000,
                base_amount=1000,
                overkill=0,
                blocked=0,
                absorbed=0,
                resisted=0,
                is_critical=False,
                is_glancing=False,
                source_guid="g",
                spell_id=1,
            )
            for i in range(23)
        ]
        for amt, t, name in [(500_000, 1010.0, "Covered Slam"), (400_000, 1020.0, "Naked Slam")]:
            events.append(
                DamageTakenEvent(
                    time_s=t,
                    event_type="SPELL_DAMAGE",
                    source_name="Mob",
                    spell_name=name,
                    school="physical",
                    amount=amt,
                    base_amount=amt,
                    overkill=0,
                    blocked=0,
                    absorbed=0,
                    resisted=0,
                    is_critical=False,
                    is_glancing=False,
                    source_guid="g",
                    spell_id=2,
                )
            )

        summary = summarize_events_direct(events, run, deaths=[])
        cov = wcl_bridge.build_wcl_coverage_report(
            report,
            fight,
            "Brutoh",
            "tok",
            events=events,
            run=run,
            class_spec="protection_warrior",
            target_actor_id=7,
            cache_dir=Path(tempfile.mkdtemp()),
        )
        log_view.render_log_analysis(
            summary,
            [],
            [],
            class_spec="protection_warrior",
            run=run,
            events=events,
            deaths=[],
            segments=[],
            coverage_report=cov,
        )

    at = AppTest.from_function(_script, default_timeout=30)
    at.run()
    assert not at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "Defensive coverage on your biggest hits" in md


# ─── _wcl_coverage_for_render: build the report AT MOST ONCE per session ──────
#
# Regression guard for the confirmed review finding: the Buffs fetch is a
# network call against the shared owner key, and its disk cache only persists
# on SUCCESS. Without per-session memoisation a *failed* fetch would re-fire on
# every Streamlit rerun (ungated, exactly when the budget is exhausted). The
# helper records the outcome — including a failure recorded as None — so the
# builder runs at most once per (analyze_key, spec) session.


def test_coverage_memoised_success_builds_once():
    from simf.ui.log_view import _wcl_coverage_for_render

    ss = {}
    calls = {"n": 0}

    def _builder():
        calls["n"] += 1
        return "REPORT"

    a = _wcl_coverage_for_render(ss, "k", _builder)
    b = _wcl_coverage_for_render(ss, "k", _builder)
    assert a == "REPORT" and b == "REPORT"
    assert calls["n"] == 1, "second render reuses the memoised report, no rebuild"


def test_coverage_memoised_failure_recorded_as_none_and_not_retried():
    from simf.ui.log_view import _wcl_coverage_for_render

    ss = {}
    calls = {"n": 0}

    def _failing():
        calls["n"] += 1
        raise RuntimeError("WCL 429 rate limited")

    a = _wcl_coverage_for_render(ss, "k", _failing)
    b = _wcl_coverage_for_render(ss, "k", _failing)
    assert a is None and b is None
    assert "k" in ss and ss["k"] is None, "failure cached as None"
    assert calls["n"] == 1, "a rate-limited fetch must NOT re-fire on the next rerun"


def test_coverage_present_key_short_circuits_builder():
    from simf.ui.log_view import _wcl_coverage_for_render

    sentinel = object()
    ss = {"k": sentinel}

    def _boom():
        raise AssertionError("builder must not be called when key is present")

    assert _wcl_coverage_for_render(ss, "k", _boom) is sentinel
