"""Tests for Phase 6.1 — brute-force CD-placement optimizer."""

from __future__ import annotations

from simf.core.character import Character
from simf.core.cooldown_planner import CooldownPlan
from simf.core.events import DamageEvent
from simf.core.profiles import HealingProfile
from simf.optimizer.cooldown_planner_optimizer import optimize_cooldown_plan


def _char() -> Character:
    return Character(
        name="x",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )


def _healer() -> HealingProfile:
    return HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)


def test_optimizer_returns_no_plan_for_unknown_spec():
    """A spec with no entries in LONG_CD_BUTTONS returns the empty plan
    as the best — and the comparison list is just that baseline."""
    events = [
        DamageEvent(
            time_s=t,
            source_id="mob",
            raw_amount=5_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in [1.0, 2.0, 3.0]
    ]
    char = Character(
        name="x",
        race="orc",
        class_spec="not_a_spec",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    result = optimize_cooldown_plan(
        character=char,
        damage_profile=None,
        healing_profile=_healer(),
        events_override=events,
        duration_override=10.0,
        search_iterations=2,
    )
    assert result.best.plan.presses == []
    assert len(result.candidates) == 1


def test_optimizer_prefers_plan_that_lands_on_the_spike():
    """Construct a single late-fight damage spike. The optimizer
    should pick a plan whose Shield Wall press is at or near the
    spike — not at t=0."""
    # 30s of trickle then a 5s heavy spike around t=35.
    events: list[DamageEvent] = []
    for t in [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]:
        events.append(
            DamageEvent(
                time_s=t,
                source_id="mob",
                raw_amount=2_000.0,
                school="physical",
                attack_type="melee",
            )
        )
    # Spike: ten ~200k physical hits during 33-38s.
    for t in [33.0, 34.0, 35.0, 36.0, 37.0]:
        events.append(
            DamageEvent(
                time_s=t,
                source_id="boss",
                raw_amount=200_000.0,
                school="physical",
                attack_type="melee",
            )
        )

    result = optimize_cooldown_plan(
        character=_char(),
        damage_profile=None,
        healing_profile=_healer(),
        events_override=events,
        duration_override=40.0,
        search_iterations=3,
        top_n_spikes=4,
        max_candidates=12,
    )

    # The best plan should have at least one SW press near the spike (33-37s).
    sw_presses = result.best.plan.for_ability("shield_wall")
    assert sw_presses, "expected the optimizer to schedule at least one SW press"
    near_spike = [p for p in sw_presses if 30.0 <= p.time_s <= 38.0]
    assert near_spike, f"best plan SW presses {[p.time_s for p in sw_presses]} miss the spike"
    # And the best plan must beat (or equal) the no-plan baseline on
    # the spike window.
    assert result.best.p99_5s_window <= result.baseline_no_plan.p99_5s_window


def test_optimizer_includes_baselines_in_comparison():
    """The result always exposes no-plan and naive baselines so the UI
    can show the value the search added."""
    events = [
        DamageEvent(
            time_s=t,
            source_id="mob",
            raw_amount=50_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in [1.0, 5.0, 10.0, 15.0, 20.0]
    ]
    result = optimize_cooldown_plan(
        character=_char(),
        damage_profile=None,
        healing_profile=_healer(),
        events_override=events,
        duration_override=30.0,
        search_iterations=2,
        top_n_spikes=2,
        max_candidates=6,
    )
    assert result.baseline_no_plan.label == "no_plan"
    assert "naive" in result.baseline_naive.label
    # baseline_no_plan must be in the candidates list — same instance.
    assert result.baseline_no_plan in result.candidates
    assert result.baseline_naive in result.candidates
    # Candidates are sorted best-first.
    rates = [c.death_rate for c in result.candidates]
    assert rates == sorted(rates)


def test_optimizer_requires_inputs():
    """Either damage_profile or events_override must be supplied."""
    import pytest

    with pytest.raises(ValueError):
        optimize_cooldown_plan(
            character=_char(),
            damage_profile=None,
            healing_profile=_healer(),
            events_override=None,
            duration_override=10.0,
        )


def test_optimizer_plan_is_a_proper_cooldown_plan():
    """Returned plan is a CooldownPlan with sorted presses."""
    events = [
        DamageEvent(
            time_s=t,
            source_id="mob",
            raw_amount=20_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in [1.0, 10.0, 20.0, 30.0]
    ]
    result = optimize_cooldown_plan(
        character=_char(),
        damage_profile=None,
        healing_profile=_healer(),
        events_override=events,
        duration_override=40.0,
        search_iterations=2,
        top_n_spikes=2,
        max_candidates=6,
    )
    assert isinstance(result.best.plan, CooldownPlan)
    times = [p.time_s for p in result.best.plan.presses]
    assert times == sorted(times)


def test_optimizer_label_format_is_human_readable():
    """Candidate labels must read as plain English, not gear-strings.

    Old format `shield_wall@261s + last_stand@261s` made users ask
    "is 261 a timestamp or a duration?". New format `Shield Wall +
    Last Stand at 4:21` mm:ss-formats the time, title-cases ability
    names, and uses ``at`` so the time reads as a clock position.
    """
    # Drive the search wide enough that at least one non-baseline
    # candidate gets emitted with a real label.
    events = [
        DamageEvent(
            time_s=t,
            source_id="mob",
            raw_amount=80_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in [1.0, 60.0, 120.0, 180.0, 240.0, 261.0]
    ]
    result = optimize_cooldown_plan(
        character=_char(),
        damage_profile=None,
        healing_profile=_healer(),
        events_override=events,
        duration_override=300.0,
        search_iterations=2,
        top_n_spikes=3,
        max_candidates=8,
    )
    real_candidates = [
        c
        for c in result.candidates
        if c is not result.baseline_no_plan and c is not result.baseline_naive
    ]
    assert real_candidates, "expected at least one real candidate beyond baselines"
    for c in real_candidates:
        assert "@" not in c.label, (
            f"label still uses @-format: {c.label!r}. Expected `Shield Wall at 4:21`."
        )
        assert "_" not in c.label, (
            f"label has lowercase_underscore ability name: {c.label!r}. "
            "Title-case it for the UI surface."
        )


def test_optimizer_populates_press_anchors_for_real_candidates():
    """``press_anchors`` carries the structured anchor-time-to-abilities
    mapping that the UI uses to render segment-anchored labels
    (`early in Hadrox`). Baselines have no anchors (empty tuple);
    real candidates carry one entry per distinct anchor time."""
    events = [
        DamageEvent(
            time_s=t,
            source_id="mob",
            raw_amount=80_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in [1.0, 60.0, 120.0, 180.0, 240.0, 261.0]
    ]
    result = optimize_cooldown_plan(
        character=_char(),
        damage_profile=None,
        healing_profile=_healer(),
        events_override=events,
        duration_override=300.0,
        search_iterations=2,
        top_n_spikes=3,
        max_candidates=8,
    )
    # Baselines: empty anchors.
    assert result.baseline_no_plan.press_anchors == ()
    assert result.baseline_naive.press_anchors == ()
    # Real candidates: at least one entry, each ((time, (ability, ...))) shape.
    real = [
        c
        for c in result.candidates
        if c is not result.baseline_no_plan and c is not result.baseline_naive
    ]
    assert real, "expected at least one real candidate"
    for c in real:
        assert c.press_anchors, f"candidate has empty press_anchors: {c.label!r}"
        for anchor_t, abilities in c.press_anchors:
            assert isinstance(anchor_t, float)
            assert isinstance(abilities, tuple) and abilities
            assert all((isinstance(a, str) and " " in a) or a.istitle() for a in abilities), (
                f"anchor abilities should be title-cased: {abilities!r}"
            )
        assert " at " in c.label, f"label is missing the ` at M:SS` clause: {c.label!r}"


# ─── Session-10-queue close-out: HRPS column ──────────────────────────────────


def test_candidate_result_accepts_mean_hrps_field():
    """``CandidateResult`` must accept a ``mean_hrps`` field. Two plans
    that tie on death rate can differ meaningfully on HRPS — the lower-
    HRPS plan is the easier real-world ask of the healer. The session-10
    queue close-out adds this field so the UI's candidates dataframe can
    render an HRPS column.
    """
    from simf.optimizer.cooldown_planner_optimizer import CandidateResult

    c = CandidateResult(
        label="x",
        plan=CooldownPlan(presses=()),
        death_rate=0.1,
        p99_5s_window=100_000.0,
        mean_dtps=42_000.0,
        mean_hrps=37_500.0,
    )
    assert c.mean_hrps == 37_500.0
    # Default keeps backward compatibility with positional callers
    # that haven't been updated yet.
    c_default = CandidateResult(
        label="y",
        plan=CooldownPlan(presses=()),
        death_rate=0.1,
        p99_5s_window=100_000.0,
        mean_dtps=42_000.0,
    )
    assert c_default.mean_hrps == 0.0


def test_optimizer_populates_mean_hrps_from_sim_result():
    """Every candidate's ``mean_hrps`` is sourced from the underlying
    ``SimResult.mean_hrps``. Without this, the UI's HRPS column would
    always show 0 and the column would be misleading rather than useful."""
    events = [
        DamageEvent(
            time_s=t,
            source_id="mob",
            raw_amount=60_000.0,
            school="physical",
            attack_type="melee",
        )
        for t in [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    ]
    result = optimize_cooldown_plan(
        character=_char(),
        damage_profile=None,
        healing_profile=_healer(),
        events_override=events,
        duration_override=35.0,
        search_iterations=3,
        top_n_spikes=2,
        max_candidates=4,
    )
    # Every candidate carries a non-negative HRPS — 0 healing required
    # is technically valid (no damage taken) but should never be negative.
    for c in result.candidates:
        assert c.mean_hrps >= 0.0, f"{c.label!r} has negative HRPS: {c.mean_hrps}"
    # At least one candidate sees inbound damage and therefore has a
    # positive HRPS — otherwise the field is unobservable.
    assert any(c.mean_hrps > 0.0 for c in result.candidates), (
        "expected at least one candidate to report positive HRPS"
    )


def test_candidates_table_includes_hrps_column():
    """The UI candidates dataframe must add an "HRPS" column. Pin the
    string so a future refactor that drops the column fails this test
    rather than silently regressing the raid-lead comparison surface.
    """
    import inspect

    from simf.ui import log_view

    src = inspect.getsource(log_view.render_cd_plan_panel)
    # Column header appears in the row builder
    assert '"HRPS"' in src, (
        "candidates dataframe lost the HRPS column — raid leads can't "
        "compare healer pressure between plans."
    )
    # Format string is applied so the numeric column is readable
    assert '"HRPS": "{:,.0f}"' in src, (
        "HRPS column missing its `{:,.0f}` format — raw float will be unreadable."
    )


def test_cli_cd_plan_candidate_table_includes_hrps_column():
    """The CLI ``simf cd-plan`` candidates table also exposes HRPS so
    the UI/CLI surfaces stay in sync — both ask the same question of
    the same data."""
    import inspect

    from simf import cli

    src = inspect.getsource(cli.cd_plan)
    assert "hrps" in src, "CLI cd-plan candidates table missing the hrps column header"
    assert "c.mean_hrps" in src, "CLI cd-plan candidates table not rendering c.mean_hrps"
