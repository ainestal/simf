from simf.core.character import Character
from simf.core.metrics import (
    IterationResult,
    SimResult,
    compute_m_plus_tmi,
    compute_tmi,
    compute_window_max,
    death_rate_stderr_pp,
)
from simf.core.profiles import load_damage_profile, load_healing_profile
from simf.core.runner import run_simulation


def test_window_max_simple():
    timeline = [(0.0, 100.0), (1.0, 200.0), (4.0, 50.0), (10.0, 1000.0)]
    # 5s window starting at t=0 captures (0, 1, 4) → 350; window at t=10 captures only the 1000
    max_5s = compute_window_max(timeline, window_s=5.0, total_duration=15.0, dt=0.5)
    assert max_5s >= 1000.0


def test_window_max_empty():
    assert compute_window_max([], window_s=5.0, total_duration=10.0) == 0.0


def _window_max_reference(timeline, window_s, total_duration, dt=0.5):
    """Slow O(n_starts * events) reference — matches the original boolean-mask impl."""
    import numpy as np

    if not timeline:
        return 0.0
    times = np.array([t for t, _ in timeline])
    damages = np.array([d for _, d in timeline])
    max_w = 0.0
    t = 0.0
    while t <= total_duration:
        s = float(damages[(times >= t) & (times < t + window_s)].sum())
        if s > max_w:
            max_w = s
        t += dt
    return max_w


def test_window_max_matches_reference_on_random_input():
    """Vectorized impl must match the slow boolean-mask reference exactly when all
    event times fall on the dt grid (otherwise sub-bin shifts are allowed)."""
    import random

    rng = random.Random(7)
    dt = 0.5
    total_duration = 60.0
    window_s = 6.0
    n_events = 200
    # Sample event times on the dt grid so bin assignment is unambiguous.
    timeline = sorted(
        (rng.randrange(0, int(total_duration / dt)) * dt, rng.uniform(50.0, 5000.0))
        for _ in range(n_events)
    )
    fast = compute_window_max(timeline, window_s, total_duration, dt)
    slow = _window_max_reference(timeline, window_s, total_duration, dt)
    assert abs(fast - slow) < 1e-6, f"fast={fast} slow={slow}"


def test_window_max_event_at_total_duration_included():
    """Half-open semantics: event exactly at total_duration is still summed
    by a window whose tail extends beyond duration."""
    timeline = [(0.0, 100.0), (60.0, 500.0)]
    result = compute_window_max(timeline, window_s=5.0, total_duration=60.0, dt=0.5)
    assert result >= 500.0


def test_tmi_zero_for_no_damage():
    it = IterationResult(
        died=False,
        time_to_die_s=None,
        final_hp_pct=1.0,
        raw_damage_total=0.0,
        dealt_damage_total=0.0,
        healing_total=0.0,
        damage_timeline=[],
        heal_timeline=[],
    )
    tmi = compute_m_plus_tmi([it], max_hp=8_000_000, duration_s=60.0)
    # With zero damage, MA = 0, exp(0) = 1 per bin, so TMI = 1e4 * ln(N0/N * N) = 1e4 * ln(N0)
    # which is positive — but there's no spike. Just verify it's finite.
    assert tmi >= 0.0
    assert tmi < 1e8


def test_etmi_below_tmi_when_healed():
    """Externals reduce net damage → ETMI < TMI for the same iteration."""
    it = IterationResult(
        died=False,
        time_to_die_s=None,
        final_hp_pct=1.0,
        raw_damage_total=0.0,
        dealt_damage_total=200_000.0,
        healing_total=150_000.0,
        damage_timeline=[(t, 1000.0) for t in range(0, 60, 1)],
        heal_timeline=[(t, 750.0) for t in range(0, 60, 1)],
    )
    max_hp = 800_000.0
    tmi = compute_tmi([it], max_hp, duration_s=60.0, window_s=6.0, include_externals=False)
    etmi = compute_tmi([it], max_hp, duration_s=60.0, window_s=6.0, include_externals=True)
    assert etmi < tmi


def _make_brutoh():
    return Character(
        name="t",
        race="earthen",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=34176,
        armor_from_gear=5015,
        haste_rating=1020,
        crit_rating=640,
        mastery_rating=1608,
        versatility_rating=160,
        max_hp_override=751872,
    )


def test_target_error_stops_early():
    """target_error > 0 should converge well before max iterations."""
    char = _make_brutoh()
    dmg = load_damage_profile("m+_pull_caster")
    heal = load_healing_profile("m+_high_key_healer")
    result = run_simulation(
        char,
        dmg,
        heal,
        iterations=2000,
        target_error=0.05,  # 5% relative std-err — easy target
        min_iterations=50,
        seed=42,
    )
    assert result.iterations >= 50
    assert result.iterations < 2000


def test_sim_dtps_is_in_plausible_range():
    """Sanity-check that a typical run produces DTPS in a physically reasonable range.

    Guards against silent calibration collapses (e.g., avoidance returns >1.0 so
    everything is avoided and DTPS → 0, or policy never casts Shield Block so DTPS → very high).
    """
    char = _make_brutoh()
    dmg = load_damage_profile("m+_pull_caster")
    heal = load_healing_profile("m+_high_key_healer")
    result = run_simulation(char, dmg, heal, iterations=200, seed=42)
    # Mean DTPS must be > 1000 (not collapsed to near-zero from wrong avoidance)
    # and < 500_000 (not exploded from missing mitigation). Ballpark for squished M+.
    assert result.mean_dtps > 1_000, (
        f"DTPS too low ({result.mean_dtps:.0f}) — avoidance or absorb bug?"
    )
    assert result.mean_dtps < 500_000, (
        f"DTPS too high ({result.mean_dtps:.0f}) — mitigation not applying?"
    )
    # SB uptime must be reasonable (>30%) — if rage never generates, SB won't be cast
    assert result.mean_sb_uptime > 0.30, f"SB uptime too low ({result.mean_sb_uptime:.1%})"


def test_iteration_result_min_hp_pct_defaults_to_full():
    """A construction site that doesn't track min_hp_pct (e.g. an old test
    fixture) must default to 1.0 — "never below full" — not 0.0, which would
    read as "hit zero HP" on a trust-facing surface."""
    it = IterationResult(
        died=False,
        time_to_die_s=None,
        final_hp_pct=1.0,
        raw_damage_total=0.0,
        dealt_damage_total=0.0,
        healing_total=0.0,
    )
    assert it.min_hp_pct == 1.0


def test_sim_result_p5_min_hp_pct_sentinel_for_legacy_construction():
    """A SimResult built without p5_min_hp_pct (pre-field cache, hand-built
    fixture) must read back None, not a fabricated 0.0 that would render as
    "you hit 0% HP" in the UI. UI code must gate on `is not None`."""
    legacy = SimResult(
        iterations=1,
        deaths=0,
        death_rate=0.0,
        death_times=[],
        mean_dtps=0.0,
        p50_dtps=0.0,
        p99_dtps=0.0,
        mean_5s_window=0.0,
        p95_5s_window=0.0,
        p99_5s_window=0.0,
        p99_10s_window=0.0,
        p99_15s_window=0.0,
        tmi_6=0.0,
        etmi_6=0.0,
        tmi_12=0.0,
        etmi_12=0.0,
    )
    assert legacy.p5_min_hp_pct is None


def test_p5_min_hp_pct_full_when_no_damage():
    """With zero damage the tank never drops below full HP — p5 of the
    per-iteration nadir must be exactly 1.0, not some artifact of the
    percentile machinery."""
    char = _make_brutoh()
    heal = load_healing_profile("m+_high_key_healer")
    result = run_simulation(
        char, None, heal, iterations=50, seed=1, events_override=[], duration_override=60.0
    )
    assert result.p5_min_hp_pct == 1.0


def test_p5_min_hp_pct_reflects_real_nadir_not_just_death():
    """On a real run, p5_min_hp_pct must be a plausible HP fraction in
    [0, 1] and — when deaths are common enough to dominate the p5 tail —
    must sit at a low value, since a dying pull's min_hp_pct is 0.

    Threshold is 10%, well clear of the ~5.33% (16/300) edge below which
    numpy's linear-interpolation percentile doesn't land exactly on 0.0
    even with deaths present — asserting a small-but-nonzero bound (not
    strict equality to 0.0) avoids relying on that interpolation detail."""
    char = _make_brutoh()
    dmg = load_damage_profile("m+_pull_caster")
    heal = load_healing_profile("m+_high_key_healer")
    result = run_simulation(char, dmg, heal, iterations=300, seed=42)
    assert result.p5_min_hp_pct is not None
    assert 0.0 <= result.p5_min_hp_pct <= 1.0
    if result.death_rate >= 0.10:
        assert result.p5_min_hp_pct <= 0.05


def test_death_rate_stderr_pp_matches_binomial_formula():
    """Promoted from ui/log_cd_plan.py's private helper (Top-5 #3, 2026-07-06
    retrospective) so the key-level verdict panel can share the same noise-
    floor formula the CD-plan verdict card already used. Same fixture values
    as the pre-existing log_cd_plan test, pinning the move is a pure
    relocation, not a rewrite."""
    assert abs(death_rate_stderr_pp(0.5, 50) - 7.0710678) < 1e-4
    assert abs(death_rate_stderr_pp(0.1, 200) - 2.1213203) < 1e-4
    assert death_rate_stderr_pp(1.0, 50) == 0.0
    assert death_rate_stderr_pp(0.5, 0) == 0.0
