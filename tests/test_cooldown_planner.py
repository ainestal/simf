"""Tests for Phase 6.1 — Cooldown Planner."""

from __future__ import annotations

from itertools import pairwise

from simf.core.cooldown_planner import (
    LONG_CD_BUTTONS,
    CooldownPlan,
    CooldownPress,
    find_damage_spikes,
    naive_plan_for_spec,
)


def test_long_cd_buttons_covers_every_spec():
    """Each Midnight tank spec has at least one long CD registered."""
    expected = {
        "protection_warrior",
        "protection_paladin",
        "blood_death_knight",
        "vengeance_demon_hunter",
        "brewmaster_monk",
        "guardian_druid",
    }
    assert set(LONG_CD_BUTTONS) >= expected


def test_find_damage_spikes_picks_worst_windows():
    """Spike detection finds the timestamps with highest 5s damage sums."""
    # Build a timeline with two clear spikes at t=10 and t=60.
    timeline = []
    for t in [10.0, 10.5, 11.0, 11.5]:
        timeline.append((t, 1_000_000))  # spike 1
    for t in [60.0, 60.5, 61.0]:
        timeline.append((t, 1_000_000))  # spike 2
    # Quiet background damage everywhere else
    for t in [20.0, 30.0, 40.0, 50.0]:
        timeline.append((t, 10_000))

    spikes = find_damage_spikes(timeline, top_n=2)
    assert len(spikes) == 2
    # Each spike is now `(centroid_time, damage_sum)` — annotation
    # needs the damage so the UI can name which spike a press covers.
    times = [t for t, _ in spikes]
    damages = [d for _, d in spikes]
    assert any(abs(t - 10.0) < 2.0 for t in times)
    assert any(abs(t - 60.0) < 2.0 for t in times)
    # Spike damages reflect the in-window sum (4×1M and 3×1M here);
    # exact value depends on bucket alignment so use a sanity bound.
    for d in damages:
        assert d > 1_000_000


def test_find_damage_spikes_returns_damage_per_spike():
    """The damage component is what powers UI annotation —
    "Shield Wall covers a 1.2M-damage spike" only works if the
    centroid carries its damage sum forward. A burst at t=50 is
    larger than a burst at t=10, so the larger one comes back with
    the larger damage."""
    timeline: list[tuple[float, float]] = []
    for t in [10.0, 10.5, 11.0]:
        timeline.append((t, 500_000))  # small spike — 1.5M total
    for t in [50.0, 50.5, 51.0, 51.5]:
        timeline.append((t, 2_000_000))  # big spike — 8M total
    spikes = find_damage_spikes(timeline, top_n=2)
    assert len(spikes) == 2
    by_time = {round(t): d for t, d in spikes}
    # Order is by centroid time (sorted ascending).
    small = by_time[10] if 10 in by_time else by_time[11]
    big = by_time[50] if 50 in by_time else by_time[51]
    assert big > small
    assert big >= 8_000_000 - 1.0  # allow float fp tolerance
    assert small >= 1_500_000 - 1.0


def test_find_damage_spikes_empty_timeline_returns_empty():
    assert find_damage_spikes([], top_n=5) == []


def test_naive_plan_chains_on_cooldown():
    """The naive plan presses each ability on CD starting at the first
    spike. Shield Wall's CD is 240s, so a 600s run should see 3 presses."""
    timeline = [(5.0, 1_000_000)]  # single spike at t=5
    plan = naive_plan_for_spec("protection_warrior", timeline, duration_s=600.0)
    sw_presses = plan.for_ability("shield_wall")
    assert len(sw_presses) >= 2  # 5s, 245s, 485s — three before t=600
    # First press is at the spike time
    assert sw_presses[0].time_s == 5.0
    # Subsequent presses are CD-spaced.
    for a, b in pairwise(sw_presses):
        assert b.time_s - a.time_s == 240.0


def test_naive_plan_unknown_spec_returns_empty():
    plan = naive_plan_for_spec("not_a_spec", [(1.0, 100)], duration_s=300.0)
    assert plan.presses == []


def test_apply_plan_sets_state_slot():
    """`apply_plan_at_tick` should set the state's `until` field when
    a press's time is at or just past now."""
    from simf.core.character import Character
    from simf.core.cooldown_planner import apply_plan_at_tick
    from simf.core.mitigation import MitigationState

    char = Character(
        name="x",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    state = MitigationState(char)
    plan = CooldownPlan(
        presses=[
            CooldownPress(time_s=5.0, ability="shield_wall"),
            CooldownPress(time_s=100.0, ability="last_stand"),
        ]
    )

    apply_plan_at_tick(state, plan, now=5.0)
    assert state.shield_wall_until > 5.0  # Shield Wall active window
    assert state.shield_wall_cd_until > 5.0  # And the CD lockout
    # Last Stand hasn't fired yet (time_s=100 > now=5)
    assert state.last_stand_until <= 0.0

    apply_plan_at_tick(state, plan, now=100.0)
    assert state.last_stand_until > 100.0
    assert state.last_stand_cd_until > 100.0


def test_consume_due_presses_index_cursor():
    """The runner-facing consume_due_presses returns an updated index
    instead of mutating the plan."""
    from simf.core.character import Character
    from simf.core.cooldown_planner import consume_due_presses
    from simf.core.mitigation import MitigationState

    char = Character(
        name="x",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    state = MitigationState(char)
    plan = CooldownPlan(
        presses=[
            CooldownPress(time_s=5.0, ability="shield_wall"),
            CooldownPress(time_s=100.0, ability="last_stand"),
        ]
    )

    idx = consume_due_presses(state, plan, now=10.0, last_idx=0)
    assert idx == 1  # only the t=5 press has fired
    assert state.shield_wall_until > 5.0
    # The plan itself is untouched.
    assert len(plan.presses) == 2

    idx = consume_due_presses(state, plan, now=110.0, last_idx=idx)
    assert idx == 2
    assert state.last_stand_until > 100.0


def test_run_simulation_honors_cooldown_plan():
    """Feeding a plan into run_simulation should reduce damage taken
    during the planned active window."""
    from simf.core.character import Character
    from simf.core.events import DamageEvent
    from simf.core.profiles import HealingProfile
    from simf.core.runner import run_simulation

    char = Character(
        name="x",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    # Steady stream of small physical hits — too small to trigger the
    # heuristic SW press (which requires <40% HP) but enough that a
    # planned SW noticeably cuts damage taken.
    # raw_amount bumped 10_000 -> 50_000 alongside the Patch 12.1.0 Ignore
    # Pain/Brutal Vitality scalar fix (2026-08-13): this build's
    # brutal_vitality talent passively feeds Ignore Pain from the tank's
    # OWN outgoing damage (policy.py), independent of incoming damage. At
    # the real 10% BV rate (was a fudged 5%), 9 hits of only 10_000 raw
    # (~3.3k each post-armor-DR) were fully absorbed by BV alone within a
    # couple of ticks regardless of Shield Wall, collapsing both arms of
    # this comparison to an uninformative 0.0 mean DTPS. 50_000 raw keeps
    # comfortably clear of the heuristic SW threshold (confirmed HP never
    # drops below ~94%) while staying large enough that BV's passive feed
    # can no longer fully swallow it — restoring the intended signal.
    events = [
        DamageEvent(
            time_s=t,
            source_id="mob",
            raw_amount=50_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    ]
    healer = HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)
    plan = CooldownPlan(presses=[CooldownPress(time_s=3.0, ability="shield_wall")])

    no_plan_result = run_simulation(
        character=char,
        damage_profile=None,
        healing_profile=healer,
        iterations=1,
        events_override=events,
        duration_override=10.0,
        cooldown_plan=None,
    )
    with_plan_result = run_simulation(
        character=char,
        damage_profile=None,
        healing_profile=healer,
        iterations=1,
        events_override=events,
        duration_override=10.0,
        cooldown_plan=plan,
    )
    # Plan SW should reduce mean DTPS — SW gives ~40% DR while active.
    assert with_plan_result.mean_dtps < no_plan_result.mean_dtps


def test_plan_blocks_heuristic_shield_wall():
    """Regression: when a plan declares Shield Wall, the heuristic
    policy must NOT fire SW on its own. Before the inf-block was
    introduced, the heuristic would press SW at <40% HP and then the
    plan would press SW again seconds later — two SWs inside one CD.
    """
    from simf.core.character import Character
    from simf.core.events import DamageEvent
    from simf.core.mitigation import MitigationState
    from simf.core.policy import make_policy
    from simf.core.profiles import HealingProfile
    from simf.core.runner import run_simulation

    char = Character(
        name="x",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    state = MitigationState(char)
    policy = make_policy(char)
    # Drive HP below 40% — heuristic would normally fire SW here.
    state.hp = state.max_hp * 0.10
    policy.decide(state, now=50.0, recent_dtps=500_000.0)
    assert state.shield_wall_until > 50.0, "sanity: heuristic fires SW at low HP"
    assert state.shield_wall_cd_until > 50.0

    # Now run a real sim with a plan that declares SW at t=100, and a
    # spike that drops HP <40% at t=50. The heuristic must not fire SW
    # at t=50 (plan owns the slot).
    # Heavy shadow hits — armor doesn't touch magic, so SW's 40% DR
    # produces a measurable delta in dealt damage. We need at least one
    # hit AFTER HP drops <40% so the heuristic gets a tick to fire SW
    # in the no-plan run.
    heavy_hits = [
        DamageEvent(
            time_s=t,
            source_id="boss",
            raw_amount=400_000.0,
            school="shadow",
            attack_type="spell",
        )
        for t in [10.0, 20.0, 30.0, 40.0, 45.0, 50.0, 55.0, 58.0]
    ]
    healer = HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)
    plan = CooldownPlan(presses=[CooldownPress(time_s=100.0, ability="shield_wall")])

    # With the plan, mean DTPS during the heuristic's would-be SW
    # window (50-58s) should NOT show 40% DR. We compare against a
    # no-plan run on the same events — if the plan blocks the
    # heuristic, the with-plan run takes MORE damage during the
    # heuristic window, not less.
    no_plan = run_simulation(
        character=char,
        damage_profile=None,
        healing_profile=healer,
        iterations=1,
        events_override=heavy_hits,
        duration_override=60.0,
        cooldown_plan=None,
    )
    with_plan = run_simulation(
        character=char,
        damage_profile=None,
        healing_profile=healer,
        iterations=1,
        events_override=heavy_hits,
        duration_override=60.0,
        cooldown_plan=plan,  # plan press is at t=100, outside this 60s window
    )
    # With the plan suppressing heuristic SW (and the plan press not
    # arriving before t=60), the with-plan run takes strictly more
    # damage. Pre-fix, the with-plan run would equal no-plan (heuristic
    # still fired) — the assertion below catches the bug.
    assert with_plan.mean_dtps > no_plan.mean_dtps


def test_compute_tmi_metrics_false_skips_tmi_but_keeps_other_metrics():
    """The cd-plan optimizer ranks by death_rate / p99_5s / mean_dtps and
    doesn't need TMI. `compute_tmi_metrics=False` should zero TMI fields
    while leaving everything else populated."""
    from simf.core.character import Character
    from simf.core.events import DamageEvent
    from simf.core.profiles import HealingProfile
    from simf.core.runner import run_simulation

    char = Character(
        name="x",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    events = [
        DamageEvent(
            time_s=float(t),
            source_id="mob",
            raw_amount=50_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in range(1, 10)
    ]
    healer = HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)

    full = run_simulation(
        character=char,
        damage_profile=None,
        healing_profile=healer,
        iterations=5,
        events_override=events,
        duration_override=10.0,
    )
    fast = run_simulation(
        character=char,
        damage_profile=None,
        healing_profile=healer,
        iterations=5,
        events_override=events,
        duration_override=10.0,
        compute_tmi_metrics=False,
    )

    assert full.tmi_6 > 0 or full.etmi_6 > 0, "full path must compute TMI for this damage shape"
    assert fast.tmi_6 == 0.0
    assert fast.etmi_6 == 0.0
    assert fast.tmi_12 == 0.0
    assert fast.etmi_12 == 0.0
    assert fast.mean_dtps == full.mean_dtps
    assert fast.death_rate == full.death_rate
    assert fast.p99_5s_window == full.p99_5s_window
    assert fast.mean_5s_window == full.mean_5s_window


def test_cooldown_plan_for_ability_filter():
    plan = CooldownPlan(
        presses=[
            CooldownPress(time_s=1.0, ability="shield_wall"),
            CooldownPress(time_s=2.0, ability="last_stand"),
            CooldownPress(time_s=3.0, ability="shield_wall"),
        ]
    )
    assert len(plan.for_ability("shield_wall")) == 2
    assert len(plan.for_ability("last_stand")) == 1
    assert plan.for_ability("nope") == []
