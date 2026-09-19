"""Tests for CD-plan press-timeline annotation.

The press timeline used to render each press as
`Shield Wall — early in Hadrox`. A player reading that can't tell
*why* simf scheduled the CD at that moment. The annotation work adds:

- "covers a 1.2M-damage spike" suffix on the first (anchored) press
  for each ability — pulled from ``CandidateResult.spike_damage_by_anchor``.
- "CD refresh" suffix on chained on-cooldown presses (presses whose
  ``time_s`` doesn't match any anchor — every Shield Wall press after
  the first one in a key).

These tests pin both behaviours and the empty/legacy fallbacks so the
CLI path and baseline rows stay unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.core.cooldown_planner import CooldownPress, find_damage_spikes
from simf.optimizer.cooldown_planner_optimizer import CandidateResult
from simf.ui.log_view import _fmt_press_row, _fmt_spike_damage, _press_rationale


@dataclass
class _Seg:
    kind: str
    label: str
    start_time_s: float
    end_time_s: float

    def duration_s(self) -> float:
        return self.end_time_s - self.start_time_s


def test_fmt_press_row_no_anchors_falls_back_to_when_only():
    """Legacy path — CLI / pre-annotation callers pass no anchor map and
    get the bare `— <when>` row they always had."""
    press = CooldownPress(time_s=60.0, ability="shield_wall")
    out = _fmt_press_row(press, segments=None)
    assert out == "  - **Shield Wall** — at 1:00"


def test_fmt_press_row_anchored_press_names_the_spike():
    """A press whose ``time_s`` matches an anchor gets the
    "covers a <magnitude>-damage spike" suffix. Brutoh's eyes can
    match that against the log timeline."""
    press = CooldownPress(time_s=60.0, ability="shield_wall")
    anchors = {60.0: 1_200_000.0}
    out = _fmt_press_row(press, segments=None, anchor_damage_by_time=anchors)
    assert out == "  - **Shield Wall** — at 1:00 · covers a 1.2M-damage spike"


def test_fmt_press_row_chained_press_says_cd_refresh():
    """Shield Wall's CD is 240s — press #1 anchors to a spike, press
    #2 fires 240s later on cooldown with no matching anchor. The
    annotation honestly names it 'CD refresh', not a fake spike."""
    press = CooldownPress(time_s=300.0, ability="shield_wall")
    anchors = {60.0: 1_200_000.0}  # the only spike — no anchor at 300s
    out = _fmt_press_row(press, segments=None, anchor_damage_by_time=anchors)
    assert out == "  - **Shield Wall** — at 5:00 · CD refresh"


def test_fmt_press_row_matches_anchor_within_half_second_tolerance():
    """Spike centroids are float-derived and presses can land at
    near-but-not-equal times after the optimizer's ``_chain_press``
    arithmetic. The 0.5s tolerance still calls those "anchored"."""
    press = CooldownPress(time_s=60.4, ability="shield_wall")
    anchors = {60.0: 900_000.0}
    out = _fmt_press_row(press, segments=None, anchor_damage_by_time=anchors)
    assert "covers a 900k-damage spike" in out


def test_fmt_press_row_renders_segment_phrase_and_rationale_together():
    """When segments AND anchor damage are both supplied the row reads
    `<ability> — early in Hadrox · covers a 1.2M-damage spike`."""
    press = CooldownPress(time_s=20.0, ability="last_stand")
    segments = [_Seg(kind="boss", label="Hadrox", start_time_s=0.0, end_time_s=120.0)]
    anchors = {20.0: 1_200_000.0}
    out = _fmt_press_row(press, segments=segments, anchor_damage_by_time=anchors)
    assert out == "  - **Last Stand** — early in Hadrox · covers a 1.2M-damage spike"


def test_press_rationale_empty_map_returns_cd_refresh_marker():
    """An empty anchor map means the candidate had no spike data (rare,
    but possible for baselines if a future caller threads it through).
    We still want to tag the press as a chained press rather than
    silently dropping the rationale."""
    assert _press_rationale(60.0, {}, tolerance_s=0.5) == "CD refresh"


def test_fmt_spike_damage_formats_each_magnitude_bucket():
    assert _fmt_spike_damage(2_400_000) == "2.4M-damage"
    assert _fmt_spike_damage(850_000) == "850k-damage"
    assert _fmt_spike_damage(500) == "500-damage"


def test_optimize_cooldown_plan_populates_spike_damage_by_anchor():
    """End-to-end smoke: a synthetic damage timeline with one clear
    spike produces candidate results whose ``spike_damage_by_anchor``
    field matches the same spike's ``find_damage_spikes`` output."""
    from simf.core.character import Character
    from simf.core.events import DamageEvent
    from simf.core.profiles import HealingProfile
    from simf.optimizer.cooldown_planner_optimizer import optimize_cooldown_plan

    # One large spike at t=60s, otherwise quiet.
    events: list[DamageEvent] = []
    for t in [60.0, 60.5, 61.0, 61.5]:
        events.append(
            DamageEvent(
                time_s=t,
                source_id="big",
                raw_amount=400_000.0,
                school="physical",
                attack_type="melee",
            )
        )
    for t in [10.0, 200.0]:
        events.append(
            DamageEvent(
                time_s=t,
                source_id="small",
                raw_amount=10_000.0,
                school="physical",
                attack_type="melee",
            )
        )

    character = Character(
        name="t",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    healing = HealingProfile(
        profile="test",
        baseline_hps_pct_of_dtps=0.0,
        baseline_hps_abs=0.0,
    )

    result = optimize_cooldown_plan(
        character,
        damage_profile=None,
        healing_profile=healing,
        events_override=events,
        duration_override=300.0,
        search_iterations=5,
        top_n_spikes=2,
        max_candidates=4,
    )

    # At least one non-baseline candidate carries spike_damage_by_anchor.
    non_baselines = [c for c in result.candidates if c.press_anchors]
    assert non_baselines, "expected at least one non-baseline candidate"
    sample = non_baselines[0]
    assert sample.spike_damage_by_anchor, "spike_damage_by_anchor must be plumbed"
    # Every anchor in press_anchors has a matching damage entry.
    anchor_times = {t for t, _ in sample.press_anchors}
    damage_times = {t for t, _ in sample.spike_damage_by_anchor}
    assert anchor_times == damage_times
    # And the damages are positive (the spike at t=60 carries ~1.6M).
    for _, d in sample.spike_damage_by_anchor:
        assert d > 0


def test_find_damage_spikes_shape_is_pairs():
    """Belt-and-braces guard against accidentally reverting to the
    list[float] return shape — annotation depends on the damage half."""
    timeline = [(10.0, 1_000_000), (10.5, 1_000_000), (11.0, 1_000_000)]
    out = find_damage_spikes(timeline, top_n=1)
    assert len(out) == 1
    entry = out[0]
    assert isinstance(entry, tuple) and len(entry) == 2
    centroid, damage = entry
    assert isinstance(centroid, float)
    assert damage > 0


def _make_candidate(anchors, damages):
    """Helper — build a CandidateResult with arbitrary anchor data."""
    return CandidateResult(
        label="test",
        plan=None,  # type: ignore[arg-type]
        death_rate=0.1,
        p99_5s_window=0.0,
        mean_dtps=0.0,
        press_anchors=anchors,
        spike_damage_by_anchor=damages,
    )


def test_optimize_naive_baseline_anchors_first_press_to_spike():
    """The naive (CD-on-CD from first spike) baseline historically
    shipped with an empty ``spike_damage_by_anchor`` — that would tag
    every press as `CD refresh` on the UI, including the legitimately-
    anchored first press of each ability. Pin the fix: the naive
    baseline's first press carries the spike's damage just like the
    enumerated candidates do."""
    from simf.core.character import Character
    from simf.core.events import DamageEvent
    from simf.core.profiles import HealingProfile
    from simf.optimizer.cooldown_planner_optimizer import optimize_cooldown_plan

    events: list[DamageEvent] = []
    for t in [60.0, 60.5, 61.0, 61.5]:
        events.append(
            DamageEvent(
                time_s=t,
                source_id="big",
                raw_amount=400_000.0,
                school="physical",
                attack_type="melee",
            )
        )
    character = Character(
        name="t",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    healing = HealingProfile(profile="test", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)

    result = optimize_cooldown_plan(
        character,
        damage_profile=None,
        healing_profile=healing,
        events_override=events,
        duration_override=300.0,
        search_iterations=3,
        top_n_spikes=2,
        max_candidates=2,
    )
    assert result.baseline_naive.spike_damage_by_anchor, (
        "naive baseline must carry the first spike's damage so the UI "
        "press timeline tags it as 'covers a spike' rather than the "
        "misleading 'CD refresh'."
    )


def test_candidate_result_default_spike_damage_is_empty():
    """Baselines and legacy CandidateResult constructions still work
    without supplying spike_damage_by_anchor."""
    cr = CandidateResult(
        label="no_plan",
        plan=None,  # type: ignore[arg-type]
        death_rate=0.5,
        p99_5s_window=0.0,
        mean_dtps=0.0,
    )
    assert cr.spike_damage_by_anchor == ()
    assert cr.press_anchors == ()
