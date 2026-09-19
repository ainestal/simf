"""Rage-flow audit tests — Brutoh ask 2026-05-24.

Covers:
  * SPELL_ENERGIZE parsing with a hand-crafted fixture (mix of rage +
    non-rage powerType; filter to rage only).
  * Timeline reconstruction with a known gain/spend sequence; assert rage
    at sampled timestamps.
  * Starvation detection: window IS detected at ≥3s under threshold; NOT
    detected at 2.5s.
  * Spike attribution: a damage event inside a window is tagged
    rage-starved; one 5s after window-close is not.
  * Real-log smoke: assert timeline non-empty + no crash on one Brutoh
    log (gitignored — skips when the file isn't present).
"""

from pathlib import Path

import pytest

from simf.io.combat_log import (
    CastEvent,
    EnergizeEvent,
    parse_energize_events,
)
from simf.io.death_analysis import (
    DamageTakenEvent,
    DeathEvent,
    DeathRecord,
    annotate_rage_starvation,
    compute_rage_timeline,
    detect_starvation_windows,
)

# ─── parse_energize_events ────────────────────────────────────────────────────


# 5 SPELL_ENERGIZE lines: 4 rage (powerType=1), 1 holy power (powerType=9).
# Spell IDs / names are realistic; amounts match in-game tooltips.
# Timestamps are spaced 1s apart so we can confirm time_s parses correctly.
ENERGIZE_FIXTURE_LINES = [
    # Brutoh Shield Slam: 17 rage, powerType 1
    "5/24/2026 12:00:00.0000  SPELL_ENERGIZE,"
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    '23922,"Shield Slam",0x1,Player-1379-AAAA0001,0000000000000000,'
    "789448,789448,3218,434,5517,485,100,0,1,200,1000,0,"
    "252.66,297.56,2556,0.5174,280,"
    "17.0000,0.0000,1,1000\n",
    # Brutoh Shield Charge: 20 rage, powerType 1
    "5/24/2026 12:00:01.0000  SPELL_ENERGIZE,"
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    '385954,"Shield Charge",0x1,Player-1379-AAAA0001,0000000000000000,'
    "789448,789448,3218,434,5517,485,100,0,1,230,1000,0,"
    "252.66,297.56,2556,0.5174,280,"
    "20.0000,0.0000,1,1000\n",
    # Brutoh Devastator: 2 rage, powerType 1
    "5/24/2026 12:00:02.0000  SPELL_ENERGIZE,"
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    '236282,"Devastator",0x1,Player-1379-AAAA0001,0000000000000000,'
    "789448,789448,3218,434,5517,485,100,0,1,280,1000,0,"
    "252.91,298.31,2556,0.5135,280,"
    "2.0000,0.0000,1,1000\n",
    # SOMEONE ELSE (not Brutoh) — should be filtered out by actor_name.
    "5/24/2026 12:00:03.0000  SPELL_ENERGIZE,"
    'Player-9999-FFFFFFFF,"NotBrutoh-Uldum-EU",0x511,0x80000000,'
    'Player-9999-FFFFFFFF,"NotBrutoh-Uldum-EU",0x511,0x80000000,'
    '23922,"Shield Slam",0x1,Player-9999-FFFFFFFF,0000000000000000,'
    "500000,500000,1000,200,1000,100,100,0,1,100,1000,0,"
    "0.00,0.00,2556,0.5,280,"
    "17.0000,0.0000,1,1000\n",
    # HOLY POWER (powerType 9) — should be filtered out by power_type=1.
    "5/24/2026 12:00:04.0000  SPELL_ENERGIZE,"
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    'Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,'
    '220637,"Judgment",0x2,Player-1379-AAAA0001,0000000000000000,'
    "789448,789448,3218,434,5517,485,100,0,1,200,1000,0,"
    "252.66,297.56,2556,0.5174,280,"
    "1.0000,0.0000,9,5\n",
]


def _write_fixture(tmp_path: Path) -> Path:
    p = tmp_path / "energize_fixture.txt"
    # WoW combat log format requires two spaces between timestamp and event.
    # Our fixture lines already include them — write straight to disk.
    p.write_text("".join(ENERGIZE_FIXTURE_LINES))
    return p


def test_parse_energize_filters_rage_only(tmp_path: Path) -> None:
    """Five lines in, three out: only Brutoh's rage events survive both filters."""
    log_path = _write_fixture(tmp_path)
    events = parse_energize_events(log_path, actor_name="Brutoh-Uldum-EU")
    # Expect 3: Shield Slam, Shield Charge, Devastator. NotBrutoh dropped on
    # actor; Judgment dropped on power_type (holy power, type 9).
    assert len(events) == 3
    spells = {(e.spell_name, e.amount) for e in events}
    assert ("Shield Slam", 17.0) in spells
    assert ("Shield Charge", 20.0) in spells
    assert ("Devastator", 2.0) in spells


def test_parse_energize_amounts_match_displayed_units(tmp_path: Path) -> None:
    """Sanity-check that we did NOT divide by 10 — Shield Charge is 20 rage
    in-game, and that's what our fixture line emits as the suffix amount.
    Regression test for the "is the log encoded in tenths?" question.
    """
    log_path = _write_fixture(tmp_path)
    events = parse_energize_events(log_path, actor_name="Brutoh-Uldum-EU")
    shield_charge = next(e for e in events if e.spell_name == "Shield Charge")
    assert shield_charge.amount == 20.0
    # In-game rage bar is 0-100; if we'd accidentally divided by 10, this
    # value would be 2.0 (clearly wrong for an iconic 20-rage spender).
    assert shield_charge.amount > 5.0


def test_parse_energize_power_type_filter_disabled(tmp_path: Path) -> None:
    """When power_type=None, all power types should pass — including the
    Judgment holy-power event that was filtered out above.
    """
    log_path = _write_fixture(tmp_path)
    events = parse_energize_events(log_path, actor_name="Brutoh-Uldum-EU", power_type=None)
    # Brutoh has 3 rage + 1 holy power = 4 total (NotBrutoh still excluded).
    assert len(events) == 4
    assert any(e.spell_name == "Judgment" and e.power_type == 9 for e in events)


# ─── compute_rage_timeline ────────────────────────────────────────────────────


def test_timeline_starts_at_initial_rage_with_no_events() -> None:
    """Empty event streams → flat timeline at initial_rage across the window."""
    tl = compute_rage_timeline(
        energize_events=[],
        cast_events=[],
        t_start=0.0,
        t_end=4.0,
        dt=1.0,
        initial_rage=50.0,
    )
    # t = 0, 1, 2, 3, 4 → 5 samples, all rage = 50.
    assert len(tl) == 5
    assert all(rage == 50.0 for _, rage in tl)


def test_timeline_applies_gain_then_spend() -> None:
    """Known sequence: gain 30 at t=1, spend Shield Block (30) at t=3."""
    energize = [
        EnergizeEvent(
            time_s=1.0,
            spell_id=23922,
            spell_name="Shield Slam",
            amount=30.0,
            over_energize=0.0,
            power_type=1,
            source_name="Brutoh",
        ),
    ]
    casts = [CastEvent(time_s=3.0, spell_id=2565, source_name="Brutoh")]
    tl = compute_rage_timeline(
        energize_events=energize,
        cast_events=casts,
        t_start=0.0,
        t_end=4.0,
        dt=1.0,
        initial_rage=0.0,
    )
    # Sample order: t=0 (rage=0), t=1 (gain → 30), t=2 (30), t=3 (spend 30 → 0), t=4 (0)
    samples = {round(t, 2): rage for t, rage in tl}
    assert samples[0.0] == 0.0
    assert samples[1.0] == 30.0
    assert samples[2.0] == 30.0
    assert samples[3.0] == 0.0
    assert samples[4.0] == 0.0


def test_timeline_caps_at_rage_max() -> None:
    """Two big gains shouldn't push rage above the cap."""
    energize = [
        EnergizeEvent(
            time_s=1.0,
            spell_id=1,
            spell_name="x",
            amount=80.0,
            over_energize=0.0,
            power_type=1,
            source_name="B",
        ),
        EnergizeEvent(
            time_s=2.0,
            spell_id=1,
            spell_name="x",
            amount=80.0,
            over_energize=0.0,
            power_type=1,
            source_name="B",
        ),
    ]
    tl = compute_rage_timeline(
        energize_events=energize,
        cast_events=[],
        t_start=0.0,
        t_end=3.0,
        dt=1.0,
        rage_cap=100.0,
    )
    rages = [r for _, r in tl]
    assert max(rages) == 100.0


def test_timeline_clamps_at_zero() -> None:
    """Spending rage you don't have can't go negative."""
    casts = [CastEvent(time_s=1.0, spell_id=2565, source_name="B")]
    tl = compute_rage_timeline(
        energize_events=[],
        cast_events=casts,
        t_start=0.0,
        t_end=2.0,
        dt=1.0,
        initial_rage=10.0,  # Less than Shield Block's 30
    )
    rages = [r for _, r in tl]
    assert min(rages) == 0.0


# ─── detect_starvation_windows ────────────────────────────────────────────────


def test_starvation_detected_at_threshold() -> None:
    """Rage under 20 for 3.5s → flagged as starvation."""
    # dt=0.5; 7 samples below threshold (3.5s), then recovery.
    tl = [
        (0.0, 30.0),
        (0.5, 10.0),  # window starts
        (1.0, 10.0),
        (1.5, 10.0),
        (2.0, 10.0),
        (2.5, 10.0),
        (3.0, 10.0),
        (3.5, 10.0),  # window ends here (still under) — 3.5s span
        (4.0, 30.0),  # recovery
    ]
    windows = detect_starvation_windows(tl, threshold=20.0, min_duration=3.0)
    assert len(windows) == 1
    start, end = windows[0]
    assert start == 0.5
    assert end == 3.5
    assert (end - start) >= 3.0


def test_starvation_not_detected_below_min_duration() -> None:
    """2.5s under threshold is shorter than min_duration=3.0 → NOT flagged."""
    tl = [
        (0.0, 30.0),
        (0.5, 10.0),  # under
        (1.0, 10.0),
        (1.5, 10.0),
        (2.0, 10.0),
        (2.5, 10.0),
        (3.0, 10.0),  # last sample under (2.5s span: 0.5 → 3.0 inclusive)
        (3.5, 30.0),  # recovers
    ]
    windows = detect_starvation_windows(tl, threshold=20.0, min_duration=3.0)
    assert windows == []


def test_starvation_window_at_end_of_timeline() -> None:
    """Tail-end starvation window closes correctly even with no recovery sample."""
    tl = [
        (0.0, 30.0),
        (1.0, 10.0),
        (2.0, 10.0),
        (3.0, 10.0),
        (4.0, 10.0),
    ]
    windows = detect_starvation_windows(tl, threshold=20.0, min_duration=3.0)
    assert len(windows) == 1
    assert windows[0] == (1.0, 4.0)


# ─── annotate_rage_starvation ─────────────────────────────────────────────────


def _make_death_event(death_t: float, ev_times: list[float]) -> DeathEvent:
    """Build a DeathEvent with placeholder damage at the given absolute times."""
    preceding = [
        DamageTakenEvent(
            time_s=t,
            event_type="SPELL_DAMAGE",
            source_name="Test Mob",
            spell_name="Test Hit",
            school="physical",
            amount=100_000,
            base_amount=120_000,
            overkill=0,
            blocked=0,
            absorbed=0,
            resisted=0,
            is_critical=False,
            is_glancing=False,
        )
        for t in ev_times
    ]
    return DeathEvent(
        death=DeathRecord(time_s=death_t, rel_time_s=death_t),
        preceding=preceding,
        total_damage_window=sum(e.amount for e in preceding),
        max_hit=max((e.amount for e in preceding), default=0),
        num_hits=len(preceding),
    )


def test_spike_inside_window_marked_starved() -> None:
    """Damage event whose time_s falls inside a starvation window → tagged."""
    windows = [(10.0, 15.0)]  # 5-second starvation window
    de = _make_death_event(death_t=15.5, ev_times=[12.0])  # event mid-window
    out = annotate_rage_starvation([de], windows)
    assert out == {0: {0}}


def test_spike_within_proximity_marked_starved() -> None:
    """Damage event 1s after window-end (< 2s proximity) → still tagged."""
    windows = [(10.0, 15.0)]
    de = _make_death_event(death_t=17.0, ev_times=[16.0])
    out = annotate_rage_starvation([de], windows, proximity_s=2.0)
    assert out == {0: {0}}


def test_spike_far_outside_window_not_starved() -> None:
    """Event 5s after window-close is NOT tagged — proximity is 2s."""
    windows = [(10.0, 15.0)]
    de = _make_death_event(death_t=22.0, ev_times=[20.0])
    out = annotate_rage_starvation([de], windows, proximity_s=2.0)
    assert out == {}


def test_spike_attribution_no_windows_empty_result() -> None:
    de = _make_death_event(death_t=15.5, ev_times=[12.0])
    out = annotate_rage_starvation([de], windows=[])
    assert out == {}


# ─── Real-log smoke test ──────────────────────────────────────────────────────


# Brutoh's combat logs are gitignored (live in `examples/` on the user's
# checkout, not in CI). Resolved relative to this test file so a worktree
# checkout finds its own `examples/`, not another checkout's.
_REAL_LOG_PATH = (
    Path(__file__).resolve().parent.parent / "examples" / "WoWCombatLog-051026_073906.txt"
)


def _find_real_log() -> Path | None:
    if _REAL_LOG_PATH.exists():
        return _REAL_LOG_PATH
    return None


@pytest.mark.skipif(_find_real_log() is None, reason="Brutoh combat log not present locally")
def test_real_log_smoke() -> None:
    """End-to-end: parse Brutoh's energize events, build a timeline, detect
    windows. Asserts non-empty timeline + no crash. No claim about WHICH
    windows show up — the log is real data and the assertion is "the
    pipeline runs," not "Brutoh starved at minute 14."
    """
    log_path = _find_real_log()
    assert log_path is not None
    energize = parse_energize_events(log_path, actor_name="Brutoh-Uldum-EU")
    # Brutoh emits ~2249 SPELL_ENERGIZE events across the full session log;
    # we just want > 0 to confirm the path is wired end-to-end.
    assert len(energize) > 0
    # All emitted events must be rage (powerType 1) by default.
    assert all(e.power_type == 1 for e in energize)
    # And every amount is in the expected displayed-units range.
    # Zero amounts DO appear (overflow: the player was at cap and the energize
    # was wasted — over_energize captures the lost amount). We still want to
    # rule out the "log encodes rage at 10×" hypothesis: typical Shield Slam
    # is 17 rage, Shield Charge is 20 — anything > 50 would be a red flag.
    assert all(0.0 <= e.amount <= 100.0 for e in energize)
    assert max(e.amount for e in energize) <= 50.0
    # Build a 60-second timeline starting at the first energize.
    t0 = energize[0].time_s
    tl = compute_rage_timeline(
        energize_events=energize,
        cast_events=[],
        t_start=t0,
        t_end=t0 + 60.0,
        dt=0.5,
    )
    assert len(tl) == 121  # 60s / 0.5s + 1 endpoint
    # Should accumulate SOME rage in the first minute of a fight log.
    assert max(r for _, r in tl) > 0.0
    # Starvation detection should run without crashing.
    windows = detect_starvation_windows(tl)
    assert isinstance(windows, list)
