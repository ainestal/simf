"""Tests for Phase 2.1 — key-level suitability verdict.

The verdict layer rides on top of `runner.run_simulation`; these tests
mock it so each case is fast and deterministic. The integration smoke
runs a real sim against a small profile at the end."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from simf.core.key_level_verdict import (
    KeyLevelPoint,
    compute_key_level_verdict,
    key_level_multiplier,
)
from simf.core.profiles import DamageProfile, MobSpec


@dataclass
class _StubResult:
    """Minimal SimResult-shape stub — only the fields the verdict reads."""

    death_rate: float
    mean_dtps: float = 0.0
    p99_5s_window: float = 0.0
    # Phase 6.2 / 6.3 — KeyLevelPoint mirrors these onward to the UI.
    # Stub defaults keep older test cases bit-identical.
    mean_hrps: float = 0.0
    normalized_tank_score: float = 0.0
    # Top-5 #2 (2026-07-06 retrospective) — tail-risk surfacing.
    p99_10s_window: float = 0.0
    p99_15s_window: float = 0.0
    sample_max_hp: float = 0.0
    p5_min_hp_pct: float | None = None
    # HP-over-time trace (2026-07-28 scoping workflow).
    sample_duration_s: float = 0.0
    sample_damage_timeline: list = None
    sample_heal_timeline: list = None
    sample_died: bool = False
    sample_time_to_die_s: float | None = None

    def __post_init__(self):
        if self.sample_damage_timeline is None:
            self.sample_damage_timeline = []
        if self.sample_heal_timeline is None:
            self.sample_heal_timeline = []


def _stub_profile() -> DamageProfile:
    """Tiny DamageProfile sufficient for scale_damage_profile to operate on."""
    return DamageProfile(
        profile="stub",
        duration_s=10.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=1.0,
                swing_damage_mean=1.0,
                swing_damage_variance=0.0,
                school="physical",
            )
        ],
    )


def _scaling_stub():
    """Compact key-level table for predictable tests."""
    return {
        "levels": {
            10: {"damage_multiplier": 1.0},
            11: {"damage_multiplier": 1.1},
            12: {"damage_multiplier": 1.2},
            13: {"damage_multiplier": 1.3},
            14: {"damage_multiplier": 1.4},
        },
        "affixes": {
            "fortified_non_boss_multiplier": 1.0,  # neutralized for stub tests
            "tyrannical_boss_multiplier": 1.0,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }


def test_key_level_multiplier_table_lookup():
    s = _scaling_stub()
    assert key_level_multiplier(10, s) == 1.0
    assert key_level_multiplier(12, s) == 1.2
    # Missing entry → 1.0 fallback (caller's job to detect)
    assert key_level_multiplier(99, s) == 1.0


def test_verdict_bins_keys_into_comfortable_progression_danger():
    """Death-rate sweep crossing both thresholds → three distinct bands."""
    # Death rates that monotonically increase across the sweep
    death_rates = {10: 0.01, 11: 0.04, 12: 0.10, 13: 0.30, 14: 0.60}
    calls = iter(death_rates.values())

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    s = _scaling_stub()
    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    bands = [p.band for p in verdict.points]
    assert bands == ["comfortable", "comfortable", "progression", "danger", "danger"]
    # comfortable_max = 11 (highest level under 5%)
    assert verdict.comfortable_max == 11
    # prog_ceiling = 12 (highest level under 25%, distinct from comfortable_max)
    assert verdict.prog_ceiling == 12


def test_verdict_headline_frames_damage_stream_not_completion():
    """Headline keeps both numbers but frames them as damage-stream, not
    key-completion. Brutoh feedback 2026-05-22: the old "Push key tonight:
    +X" copy read as a recommendation when the sim has no view of
    mechanic damage, group wipes, or timer pressure."""
    death_rates = {10: 0.02, 11: 0.04, 12: 0.10, 13: 0.30, 14: 0.60}
    calls = iter(death_rates.values())

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    s = _scaling_stub()
    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    headline = verdict.headline()
    assert "+11" in headline  # comfortable max
    assert "+12" in headline  # the prog ceiling, no longer labelled "push"
    assert "Fortified" in headline
    # The old recommendation framing must not return — "Push key tonight"
    # was the credibility leak.
    assert "Push key tonight" not in headline
    # The honest framing must be present — at least one of these phrases
    # signals "we measure damage stream, not key completion."
    assert "Damage stream" in headline or "damage stream" in headline


def test_verdict_undergeared_when_everything_dies():
    """Death rate > prog threshold at every key → undergeared headline."""
    death_rates = {10: 0.50, 11: 0.70, 12: 0.85, 13: 0.95, 14: 1.0}
    calls = iter(death_rates.values())

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    s = _scaling_stub()
    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    assert verdict.comfortable_max is None
    assert verdict.prog_ceiling is None
    head = verdict.headline()
    assert "Undergeared" in head
    assert "Gear up" in head
    # detail() must not dump an 11-arrow chain of identical numbers
    detail = verdict.detail()
    assert detail.count("→") == 0, "undergeared detail should be one clause"
    assert "%" in detail  # but should still cite a number


def test_verdict_all_comfortable_omits_push_key():
    """Death rate below comfortable at every key → no prog cliff named."""
    death_rates = {10: 0.001, 11: 0.005, 12: 0.01, 13: 0.02, 14: 0.04}
    calls = iter(death_rates.values())

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    s = _scaling_stub()
    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    assert verdict.comfortable_max == 14
    assert verdict.prog_ceiling is None  # no level strictly above comfortable_max
    head = verdict.headline()
    assert "Comfortable" in head
    # "top of the simulated range" frames over-geared as victory, not a
    # sim bug — the previous "Sweep didn't reach the prog ceiling" copy
    # read like incomplete data (ui-critic Phase 2.1 round 1 #4).
    assert "top of the simulated range" in head
    assert "Sweep didn't reach" not in head
    # Detail line stays silent when there's no cliff.
    assert verdict.detail() == ""


def test_displayable_points_caps_keys_past_cliff():
    """Chain shows comfortable_max + N progression rows, then stops.

    Brutoh feedback 2026-05-22 (`+24` push key context): the full sweep
    dumped 7 keys past comfort (+18 → +24), all labelled "progression",
    which read as a recommendation list. `displayable_points()` caps the
    chain so the player sees the cliff transition without picking up
    pushability claims the model can't back."""
    # 5-level scaling stub. Levels 10..14, threshold at <5%.
    # Comfort line at +11 → +12, +13, +14 are past comfort. With the
    # default 3-keys-past cap, all three are shown (no truncation here),
    # so build a longer table to exercise the trim.
    longer_scaling = {
        "levels": {k: {"damage_multiplier": 1.0 + 0.1 * (k - 10)} for k in range(10, 21)},
        "affixes": {
            "fortified_non_boss_multiplier": 1.0,
            "tyrannical_boss_multiplier": 1.0,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }
    # Death rates: +10..+12 comfortable, +13..+20 progression.
    death_rates = [0.01, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20]
    calls = iter(death_rates)

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=longer_scaling,
        )

    assert verdict.comfortable_max == 12
    # Full sweep has 11 points; displayable should cap at +12 + 3 prog = +15.
    shown = verdict.displayable_points(prog_keys_past_comfort=3)
    shown_keys = [p.key_level for p in shown]
    assert shown_keys == [10, 11, 12, 13, 14, 15]
    # Smaller caps tighten the chain further — confirm the knob works.
    shown_one = verdict.displayable_points(prog_keys_past_comfort=1)
    assert [p.key_level for p in shown_one] == [10, 11, 12, 13]


def test_displayable_points_returns_full_when_all_comfortable():
    """Over-geared sweep — no cliff to hide past; render every row."""
    death_rates = {10: 0.001, 11: 0.005, 12: 0.01, 13: 0.02, 14: 0.04}
    calls = iter(death_rates.values())

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    s = _scaling_stub()
    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    shown = verdict.displayable_points()
    assert len(shown) == len(verdict.points)


def test_headline_caps_prog_ceiling_to_displayable_range():
    """When the chain trims rows past the cliff, the headline must use
    the trimmed prog ceiling — not the full-sweep prog_ceiling that would
    name a key the user can't see in the rendered list."""
    # 11 levels, comfort at +12, prog runs to +20 in the raw sweep.
    longer_scaling = {
        "levels": {k: {"damage_multiplier": 1.0 + 0.1 * (k - 10)} for k in range(10, 21)},
        "affixes": {
            "fortified_non_boss_multiplier": 1.0,
            "tyrannical_boss_multiplier": 1.0,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }
    # +10..+12 comfortable, +13..+20 progression (all <25%).
    death_rates = [0.01, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20]
    calls = iter(death_rates)

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=longer_scaling,
        )

    # Raw prog ceiling = +20; displayable cap = +12 + 3 = +15.
    assert verdict.comfortable_max == 12
    assert verdict.prog_ceiling == 20
    assert verdict.displayable_prog_ceiling(prog_keys_past_comfort=3) == 15
    headline = verdict.headline()
    # Must claim the visible ceiling (+15), not the hidden one (+20).
    assert "+15" in headline
    assert "+20" not in headline
    assert "+12" in headline  # comfortable max still surfaced


def test_displayable_points_returns_full_when_undergeared():
    """Below the comfortable floor — every row is useful (the cliff IS
    the floor)."""
    death_rates = {10: 0.50, 11: 0.70, 12: 0.85, 13: 0.95, 14: 1.0}
    calls = iter(death_rates.values())

    def fake_run(**_kw):
        return _StubResult(death_rate=next(calls))

    s = _scaling_stub()
    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    assert verdict.comfortable_max is None
    shown = verdict.displayable_points()
    assert len(shown) == len(verdict.points)


def test_affix_changes_multiplier():
    """Fortified vs Tyrannical select different affix multipliers."""
    seen_multipliers: list[float] = []
    fortified_scaling = {
        "levels": {10: {"damage_multiplier": 1.0}},
        "affixes": {
            "fortified_non_boss_multiplier": 1.3,
            "tyrannical_boss_multiplier": 1.15,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }

    def fake_run(*, damage_profile, **_kw):
        # The scale_damage_profile wrapper isn't easy to inspect inside the
        # patch, so we read the profile's first mob mean to back-derive the
        # multiplier applied. The stub profile has swing_damage_mean = 1.0
        # so the scaled value IS the multiplier.
        mult = damage_profile.mobs[0].swing_damage_mean
        seen_multipliers.append(mult)
        return _StubResult(death_rate=0.0)

    from simf.core.profiles import DamageProfile, MobSpec

    profile = DamageProfile(
        profile="stub",
        duration_s=10.0,
        mobs=[
            MobSpec(
                count=1,
                swing_timer_s=1.0,
                swing_damage_mean=1.0,
                swing_damage_variance=0.0,
                school="physical",
            )
        ],
    )

    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        compute_key_level_verdict(
            character=object(),
            damage_profile=profile,
            healing_profile=None,
            key_levels=[10],
            scaling=fortified_scaling,
            affix="fortified",
        )
        compute_key_level_verdict(
            character=object(),
            damage_profile=profile,
            healing_profile=None,
            key_levels=[10],
            scaling=fortified_scaling,
            affix="tyrannical",
        )

    # First call used fortified (1.3); second used tyrannical (1.15)
    assert seen_multipliers == [1.3, 1.15]


def test_key_level_point_carries_full_diagnostic():
    """Each point exposes death_rate, multiplier, DTPS, p99 window."""

    def fake_run(**_kw):
        return _StubResult(death_rate=0.05, mean_dtps=4200.0, p99_5s_window=500000.0)

    s = _scaling_stub()
    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            key_levels=[10],
            scaling=s,
        )

    pt = verdict.points[0]
    assert isinstance(pt, KeyLevelPoint)
    assert pt.death_rate == 0.05
    assert pt.mean_dtps == 4200.0
    assert pt.p99_5s_window == 500000.0
    assert pt.damage_multiplier == 1.0


def test_real_yaml_covers_mythic_zero_to_title_push():
    """Smoke: the bundled yaml spans Mythic 0 (+2) through title-push
    territory (+24). Brutoh user-feedback 2026-05-21 asked for the
    verdict to start at +2 and reach +24 so the same widget shows the
    onboarding tank a survivable floor *and* shows the WR-tier player
    where the curve actually breaks."""
    from simf.core.constants import load_key_level_scaling

    s = load_key_level_scaling()
    assert "levels" in s
    assert "affixes" in s
    assert "death_rate_thresholds" in s
    levels = sorted(int(k) for k in s["levels"])
    assert min(levels) <= 2
    assert max(levels) >= 24


def test_default_sweep_includes_mythic_zero_floor_and_title_push_ceiling():
    """compute_key_level_verdict's default key_levels must cover the
    full range — no +10 lower bound. The verdict bands compress
    low-end deaths naturally for over-geared tanks; rendering the
    full range is the point. Brutoh user-feedback 2026-05-21."""
    from simf.core.constants import load_key_level_scaling
    from simf.core.key_level_verdict import compute_key_level_verdict

    s = load_key_level_scaling()
    with patch(
        "simf.core.key_level_verdict.run_simulation",
        return_value=_StubResult(death_rate=0.01),
    ):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            iterations=1,
            seed=1,
            scaling=s,
        )

    swept = {p.key_level for p in verdict.points}
    assert 2 in swept, "Mythic 0 floor missing from default sweep"
    assert 24 in swept, "Title-push ceiling missing from default sweep"


def test_keylevelpoint_carries_hrps_and_normalized_tank_score():
    """SimResult already computes mean_hrps + normalized_tank_score per
    iteration set; the verdict layer must thread them through onto each
    KeyLevelPoint so the UI can render them. Brutoh user-feedback
    2026-05-21 asked where the survivability coefficient was."""
    s = {
        "levels": {10: {"damage_multiplier": 1.0}},
        "affixes": {
            "fortified_non_boss_multiplier": 1.0,
            "tyrannical_boss_multiplier": 1.0,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }
    expected_hrps = 47_500.0
    expected_score = 0.72

    def fake_run(**_kw):
        return _StubResult(
            death_rate=0.02,
            mean_dtps=80_000.0,
            mean_hrps=expected_hrps,
            normalized_tank_score=expected_score,
        )

    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    pt = verdict.points[0]
    assert pt.mean_hrps == expected_hrps
    assert pt.normalized_tank_score == expected_score


def test_keylevelpoint_carries_tail_risk_fields():
    """SimResult already computes p99_10s_window + p5_min_hp_pct; the
    verdict layer must thread them through (with sample_max_hp so the UI
    can express the window as a % of max HP) onto each KeyLevelPoint.
    Top-5 #2 from the 2026-07-06 retrospective."""
    s = {
        "levels": {10: {"damage_multiplier": 1.0}},
        "affixes": {
            "fortified_non_boss_multiplier": 1.0,
            "tyrannical_boss_multiplier": 1.0,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }
    expected_window_10s = 210_000.0
    expected_window_15s = 260_000.0
    expected_max_hp = 750_000.0
    expected_p5 = 0.31

    def fake_run(**_kw):
        return _StubResult(
            death_rate=0.02,
            p99_10s_window=expected_window_10s,
            p99_15s_window=expected_window_15s,
            sample_max_hp=expected_max_hp,
            p5_min_hp_pct=expected_p5,
        )

    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    pt = verdict.points[0]
    assert pt.p99_10s_window == expected_window_10s
    assert pt.p99_15s_window == expected_window_15s
    assert pt.sample_max_hp == expected_max_hp
    assert pt.p5_min_hp_pct == expected_p5


def test_keylevelpoint_carries_hp_trace_fields():
    """SimResult already carries the sample iteration's timelines + death
    state; the verdict layer must thread them through onto each
    KeyLevelPoint so the HP-over-time chart can reconstruct a curve
    without a second sim run (2026-07-28 scoping workflow)."""
    s = {
        "levels": {10: {"damage_multiplier": 1.0}},
        "affixes": {
            "fortified_non_boss_multiplier": 1.0,
            "tyrannical_boss_multiplier": 1.0,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }
    expected_duration = 400.0
    expected_damage = [(1.0, 5000.0), (2.0, 6000.0)]
    expected_heal = [(1.5, 2000.0)]

    def fake_run(**_kw):
        return _StubResult(
            death_rate=0.02,
            sample_duration_s=expected_duration,
            sample_damage_timeline=list(expected_damage),
            sample_heal_timeline=list(expected_heal),
            sample_died=True,
            sample_time_to_die_s=250.0,
        )

    with patch("simf.core.key_level_verdict.run_simulation", side_effect=fake_run):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    pt = verdict.points[0]
    assert pt.sample_duration_s == expected_duration
    assert pt.sample_damage_timeline == expected_damage
    assert pt.sample_heal_timeline == expected_heal
    assert pt.sample_died is True
    assert pt.sample_time_to_die_s == 250.0


def test_keylevelpoint_p5_min_hp_pct_defaults_to_none_for_legacy_stub():
    """A SimResult-shaped stub built before p5_min_hp_pct existed must
    thread through as None, not a fabricated 0.0 — same sentinel-safety
    contract as SimResult itself."""
    s = {
        "levels": {10: {"damage_multiplier": 1.0}},
        "affixes": {
            "fortified_non_boss_multiplier": 1.0,
            "tyrannical_boss_multiplier": 1.0,
        },
        "death_rate_thresholds": {"comfortable": 0.05, "progression": 0.25},
    }

    @dataclass
    class _PreFieldStubResult:
        death_rate: float
        mean_dtps: float = 0.0
        p99_5s_window: float = 0.0

    with patch(
        "simf.core.key_level_verdict.run_simulation",
        return_value=_PreFieldStubResult(death_rate=0.02),
    ):
        verdict = compute_key_level_verdict(
            character=object(),
            damage_profile=_stub_profile(),
            healing_profile=None,
            scaling=s,
        )

    assert verdict.points[0].p5_min_hp_pct is None
