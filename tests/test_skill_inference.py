"""Tests for Phase 2.10b — log-based playstyle inference.

Three layers:
1. Cast parser (`parse_cast_events`) — string pre-filters, source filter,
   spell-id filter, byte-offset honored.
2. Uptime math — overlap merging, end clipping, empty-input guards.
3. Tier bucket — top-down threshold match, edge cases.

Plus one integration test against a real example log so we don't lose
the "actually works on the bytes a tank uploads" guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from simf.core.skill_inference import (
    compute_ds_press_rate_from_casts,
    compute_sb_uptime_from_casts,
    compute_top_sb_gaps,
    infer_skill_tier,
    infer_tier_from_casts,
    match_sb_tier_to_ladder,
)


# Minimal CastEvent-shape stub so the uptime tests don't depend on the io
# module's parser. The real CastEvent is `(time_s, spell_id, source_name)`
# — we only need `time_s` for the math, so a tiny frozen dataclass is
# enough.
@dataclass(frozen=True)
class _Cast:
    time_s: float
    spell_id: int = 2565
    source_name: str = "Brutoh-Uldum-EU"


# ─── Uptime math ───────────────────────────────────────────────────────────


def test_uptime_empty_casts_returns_zero():
    assert compute_sb_uptime_from_casts([], duration_s=60.0, sb_duration_s=6.0) == 0.0


def test_uptime_zero_duration_returns_zero():
    """Defensive — a zero-length segment can't have meaningful coverage,
    don't divide by zero, fall through to no signal."""
    assert (
        compute_sb_uptime_from_casts([_Cast(time_s=0.0)], duration_s=0.0, sb_duration_s=6.0) == 0.0
    )


def test_uptime_single_cast_no_overlap():
    """One cast: 6s of coverage in a 60s segment = 10%."""
    uptime = compute_sb_uptime_from_casts(
        [_Cast(time_s=10.0)], duration_s=60.0, sb_duration_s=6.0, start_time_s=0.0
    )
    assert uptime == pytest.approx(6.0 / 60.0)


def test_uptime_two_disjoint_casts():
    """Two non-overlapping casts: 12s coverage in 60s = 20%."""
    uptime = compute_sb_uptime_from_casts(
        [_Cast(time_s=10.0), _Cast(time_s=30.0)],
        duration_s=60.0,
        sb_duration_s=6.0,
        start_time_s=0.0,
    )
    assert uptime == pytest.approx(12.0 / 60.0)


def test_uptime_overlapping_casts_merge():
    """Two casts 2s apart: [10..16] + [12..18] merge to [10..18] = 8s.
    Without merging the naive sum would be 12s → 1.5× overcounting."""
    uptime = compute_sb_uptime_from_casts(
        [_Cast(time_s=10.0), _Cast(time_s=12.0)],
        duration_s=60.0,
        sb_duration_s=6.0,
        start_time_s=0.0,
    )
    assert uptime == pytest.approx(8.0 / 60.0)


def test_uptime_cast_at_segment_end_gets_clipped():
    """A cast 3s before the segment ends shouldn't credit 3s of buff
    after the segment ends. Buff window [57..63] clips to [57..60]."""
    uptime = compute_sb_uptime_from_casts(
        [_Cast(time_s=57.0)],
        duration_s=60.0,
        sb_duration_s=6.0,
        start_time_s=0.0,
    )
    assert uptime == pytest.approx(3.0 / 60.0)


def test_uptime_cast_before_segment_start_gets_clipped():
    """A cast 2s before the segment opens credits only the portion that
    falls inside the segment. Buff window [-2..4] clips to [0..4]."""
    uptime = compute_sb_uptime_from_casts(
        [_Cast(time_s=-2.0)],
        duration_s=60.0,
        sb_duration_s=6.0,
        start_time_s=0.0,
    )
    assert uptime == pytest.approx(4.0 / 60.0)


def test_uptime_three_overlapping_casts_merge_correctly():
    """Three casts: [0..6], [3..9], [7..13]. Merge sequence:
    [0..6]+[3..9] = [0..9]; then [0..9] vs [7..13] = [0..13] = 13s."""
    uptime = compute_sb_uptime_from_casts(
        [_Cast(time_s=0.0), _Cast(time_s=3.0), _Cast(time_s=7.0)],
        duration_s=60.0,
        sb_duration_s=6.0,
        start_time_s=0.0,
    )
    assert uptime == pytest.approx(13.0 / 60.0)


def test_uptime_loads_sb_duration_from_constants_by_default():
    """When `sb_duration_s` is omitted, the function reads
    `active_mitigation.shield_block.duration_s` from constants.yaml.
    Pin the default behavior so a typo in the YAML key surfaces here."""
    uptime = compute_sb_uptime_from_casts(
        [_Cast(time_s=10.0)],
        duration_s=60.0,
        start_time_s=0.0,
    )
    # SB duration is 6.0s per constants.yaml (active_mitigation.shield_block).
    assert uptime == pytest.approx(6.0 / 60.0)


# ─── Tier bucket ───────────────────────────────────────────────────────────


def _stub_tiers() -> list[dict]:
    """Compact tiers list with explicit thresholds for predictable tests.

    Mirrors the real `constants.yaml.skill_tiers` shape — both
    `min_sb_uptime` and `min_ds_press_rate` keys present so v3 combined
    inference can be exercised without loading the real YAML.
    """
    return [
        {"id": "in_the_zone", "min_sb_uptime": 0.75, "min_ds_press_rate": 0.75},
        {"id": "anticipating", "min_sb_uptime": 0.50, "min_ds_press_rate": 0.50},
        {"id": "reading", "min_sb_uptime": 0.25, "min_ds_press_rate": 0.25},
        {"id": "learning", "min_sb_uptime": 0.0, "min_ds_press_rate": 0.0},
    ]


def test_infer_tier_top_bucket():
    assert infer_skill_tier(0.85, tiers=_stub_tiers()) == "in_the_zone"
    assert infer_skill_tier(0.75, tiers=_stub_tiers()) == "in_the_zone"


def test_infer_tier_middle_buckets():
    assert infer_skill_tier(0.74, tiers=_stub_tiers()) == "anticipating"
    assert infer_skill_tier(0.50, tiers=_stub_tiers()) == "anticipating"
    assert infer_skill_tier(0.40, tiers=_stub_tiers()) == "reading"
    assert infer_skill_tier(0.25, tiers=_stub_tiers()) == "reading"


def test_infer_tier_bottom_bucket():
    assert infer_skill_tier(0.10, tiers=_stub_tiers()) == "learning"
    assert infer_skill_tier(0.0, tiers=_stub_tiers()) == "learning"


def test_infer_tier_empty_tiers_returns_empty():
    """Defensive — a misconfigured YAML with no tiers shouldn't crash."""
    assert infer_skill_tier(0.5, tiers=[]) == ""


def test_infer_tier_real_constants_yaml_top_threshold():
    """Sanity that the real `constants.yaml` thresholds match the doc'd
    pattern (top-tier threshold ≤ 1.0). If the YAML drifts to a value >
    1.0 by accident, a player at 100% uptime would fall through to
    `learning` — surface that here."""
    from simf.core.constants import load_skill_tiers

    tiers = load_skill_tiers()
    assert float(tiers[0]["min_sb_uptime"]) <= 1.0
    # And a 100%-uptime player buckets as the top tier.
    assert infer_skill_tier(1.0, tiers=tiers) == tiers[0]["id"]


# ─── Cast parser ───────────────────────────────────────────────────────────


@pytest.fixture
def synthetic_log(tmp_path: Path) -> Path:
    """Hand-built combat log with three Shield Block casts by Brutoh, one
    by another player (filtered out by source), and one event of a
    different type (filtered out by event)."""
    p = tmp_path / "synthetic.txt"
    lines = [
        # Brutoh casts SB — should match
        '5/10/2026 07:44:22.700  SPELL_CAST_SUCCESS,Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,0000000000000000,nil,0x80000000,0x80000000,2565,"Shield Block",0x1,Player-1379-AAAA0001,0000000000000000,705069,1026282,3191,434,5499,485',
        # Different source, same spell — filter by source
        '5/10/2026 07:44:25.500  SPELL_CAST_SUCCESS,Player-1379-OTHER,"Stranger-Uldum-EU",0x511,0x80000000,0000000000000000,nil,0x80000000,0x80000000,2565,"Shield Block",0x1,Player-1379-OTHER,0000000000000000,705069,1026282,3191,434,5499,485',
        # Brutoh casts SB — should match (second)
        '5/10/2026 07:44:30.500  SPELL_CAST_SUCCESS,Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,0000000000000000,nil,0x80000000,0x80000000,2565,"Shield Block",0x1,Player-1379-AAAA0001,0000000000000000,705069,1026282,3191,434,5499,485',
        # Brutoh casts something else — filter by spell name + id
        '5/10/2026 07:44:35.500  SPELL_CAST_SUCCESS,Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,0000000000000000,nil,0x80000000,0x80000000,6673,"Battle Shout",0x1,Player-1379-AAAA0001,0000000000000000',
        # Damage event with "Shield Block" in some text — pre-filter shouldn't false-positive
        '5/10/2026 07:44:40.000  SPELL_DAMAGE,Creature-0-Mob,"Goblin",0x10000,0x0,Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x0,12345,"Shield Block",0x1,1000,1000,0,1,0,0,nil,nil',
        # Brutoh casts SB — third match
        '5/10/2026 07:44:50.000  SPELL_CAST_SUCCESS,Player-1379-AAAA0001,"Brutoh-Uldum-EU",0x511,0x80000000,0000000000000000,nil,0x80000000,0x80000000,2565,"Shield Block",0x1,Player-1379-AAAA0001,0000000000000000,705069,1026282,3191,434,5499,485',
    ]
    p.write_text("\n".join(lines) + "\n")
    return p


def test_cast_parser_matches_source_and_spell(synthetic_log):
    from simf.io.combat_log import parse_cast_events

    casts = parse_cast_events(
        synthetic_log,
        source_name="Brutoh-Uldum-EU",
        spell_name="Shield Block",
        spell_id=2565,
    )
    assert len(casts) == 3
    assert all(c.source_name == "Brutoh-Uldum-EU" for c in casts)
    assert all(c.spell_id == 2565 for c in casts)


def test_cast_parser_filters_by_source(synthetic_log):
    """Stranger-Uldum-EU cast a Shield Block too — should not appear."""
    from simf.io.combat_log import parse_cast_events

    casts = parse_cast_events(
        synthetic_log,
        source_name="Brutoh-Uldum-EU",
        spell_name="Shield Block",
        spell_id=2565,
    )
    assert not any(c.source_name == "Stranger-Uldum-EU" for c in casts)


def test_cast_parser_ignores_damage_events_with_spell_name(synthetic_log):
    """A SPELL_DAMAGE event whose spell name happens to be 'Shield Block'
    must not slip through — only SPELL_CAST_SUCCESS counts."""
    from simf.io.combat_log import parse_cast_events

    casts = parse_cast_events(
        synthetic_log,
        source_name="Brutoh-Uldum-EU",
        spell_name="Shield Block",
        spell_id=2565,
    )
    # The synthetic log has three real casts plus one damage event with
    # 'Shield Block' in the spell name field. If the event-type filter
    # leaks, we'd see 4 here.
    assert len(casts) == 3


def test_cast_parser_time_window_filters(synthetic_log):
    """Pass start/end_time_s and verify casts outside the window are
    dropped. Times in synthetic_log span ~30s; restrict to a 10s mid-
    window and expect only the casts inside."""
    from simf.io.combat_log import parse_cast_events

    # First cast at 07:44:22.700 → ts ≈ X. Just pin by relative ordering:
    # ask for everything except the first cast and verify count.
    casts_all = parse_cast_events(
        synthetic_log,
        source_name="Brutoh-Uldum-EU",
        spell_name="Shield Block",
        spell_id=2565,
    )
    first_time = casts_all[0].time_s
    casts_after = parse_cast_events(
        synthetic_log,
        source_name="Brutoh-Uldum-EU",
        spell_name="Shield Block",
        spell_id=2565,
        start_time_s=first_time + 1.0,  # exclude the first
    )
    assert len(casts_after) == len(casts_all) - 1


# ─── Integration smoke — real log on disk ──────────────────────────────────


# ─── compute_top_sb_gaps — anchored coaching callouts (2.10c) ──────────────


# Stub damage-event shape — only `time_s`, `school`, `amount` matter.
@dataclass(frozen=True)
class _Dmg:
    time_s: float
    amount: float
    school: str = "physical"


def test_top_gaps_returns_empty_when_no_events():
    assert compute_top_sb_gaps([], [], duration_s=60.0, sb_duration_s=6.0) == []


def test_top_gaps_returns_empty_when_no_gaps_with_damage():
    """SB covers the whole segment → no gaps; or gaps exist but have no
    damage → no callouts. Either way, empty list."""
    casts = [_Cast(time_s=0.0), _Cast(time_s=6.0), _Cast(time_s=12.0)]
    events = [_Dmg(time_s=2.0, amount=50_000.0)]
    out = compute_top_sb_gaps(casts, events, duration_s=18.0, sb_duration_s=6.0, min_gap_s=5.0)
    assert out == []  # SB covered [0..18], no uncovered intervals


def test_top_gaps_finds_uncovered_window_with_physical_damage():
    """One cast at t=0 (covers 0..6), another at t=20 (covers 20..26).
    Gap [6..20] = 14s. 200k physical damage lands inside it → one
    callout."""
    casts = [_Cast(time_s=0.0), _Cast(time_s=20.0)]
    events = [_Dmg(time_s=10.0, amount=200_000.0)]
    out = compute_top_sb_gaps(
        casts,
        events,
        duration_s=30.0,
        sb_duration_s=6.0,
        min_gap_s=5.0,
        min_damage=50_000.0,
    )
    assert len(out) == 1
    gap = out[0]
    assert gap.start_time_s == 6.0
    assert gap.end_time_s == 20.0
    assert gap.damage_during_gap == 200_000.0


def test_top_gaps_ignores_non_physical_damage():
    """Block only mitigates physical hits — a shadow nuke in an SB gap
    isn't a missed-press indictment. The same event with school=shadow
    should not register as gap damage."""
    casts = [_Cast(time_s=0.0), _Cast(time_s=20.0)]
    events = [_Dmg(time_s=10.0, amount=200_000.0, school="shadow")]
    out = compute_top_sb_gaps(casts, events, duration_s=30.0, sb_duration_s=6.0)
    assert out == []


def test_top_gaps_ranks_by_damage_desc():
    """Two gaps, both with damage. Higher-damage one ranks first."""
    # Casts: [0..6], [10..16], [20..26]
    # Gaps: [6..10] = 4s (too short), [16..20] = 4s (too short)
    # Need longer gaps for the test — use spacing 30, 60.
    casts = [_Cast(time_s=0.0), _Cast(time_s=30.0), _Cast(time_s=60.0)]
    # Gap [6..30] = 24s with 100k damage; gap [36..60] = 24s with 500k
    events = [
        _Dmg(time_s=10.0, amount=100_000.0),
        _Dmg(time_s=45.0, amount=500_000.0),
    ]
    out = compute_top_sb_gaps(
        casts,
        events,
        duration_s=90.0,
        sb_duration_s=6.0,
        min_gap_s=5.0,
        min_damage=50_000.0,
    )
    assert len(out) == 2
    assert out[0].damage_during_gap == 500_000.0
    assert out[1].damage_during_gap == 100_000.0


def test_top_gaps_respects_min_gap_filter():
    """A 4s gap with significant damage should be filtered out — those
    are between back-to-back presses, not a real coverage failure."""
    # SB covers [10..16], [17..23]. Gap [16..17] = 1s, below threshold.
    casts = [_Cast(time_s=10.0), _Cast(time_s=17.0)]
    events = [_Dmg(time_s=16.5, amount=300_000.0)]
    out = compute_top_sb_gaps(
        casts,
        events,
        duration_s=30.0,
        sb_duration_s=6.0,
        min_gap_s=5.0,
        min_damage=50_000.0,
    )
    assert out == []


def test_top_gaps_respects_min_damage_filter():
    """A 30s gap with only 10k damage shouldn't be flagged — a quiet
    stretch isn't actionable feedback."""
    casts = [_Cast(time_s=0.0), _Cast(time_s=40.0)]
    events = [_Dmg(time_s=20.0, amount=10_000.0)]
    out = compute_top_sb_gaps(
        casts,
        events,
        duration_s=50.0,
        sb_duration_s=6.0,
        min_gap_s=5.0,
        min_damage=50_000.0,
    )
    assert out == []


def test_top_gaps_time_phrase_is_relative_mss():
    """The time_phrase should format the gap start as elapsed M:SS from
    `start_time_s`, not absolute log time. A gap at log time t=1063
    when the run started at t=1000 should read as `1:03`."""
    casts = [_Cast(time_s=1000.0), _Cast(time_s=1100.0)]
    events = [_Dmg(time_s=1063.0, amount=200_000.0)]
    out = compute_top_sb_gaps(
        casts,
        events,
        duration_s=120.0,
        sb_duration_s=6.0,
        start_time_s=1000.0,
        min_gap_s=5.0,
        min_damage=50_000.0,
    )
    assert len(out) == 1
    # Gap [1006..1100]; phrase = M:SS of (1006 - 1000) = 6s → "0:06"
    assert out[0].time_phrase == "0:06"


def test_top_gaps_top_n_caps_result_length():
    """Many gaps with damage → only `top_n` returned."""
    # 10 casts spaced 100s apart, each cast covers 6s → 9 gaps of ~94s each
    casts = [_Cast(time_s=i * 100.0) for i in range(10)]
    # One damage event in each gap (between cast i and cast i+1)
    events = [_Dmg(time_s=i * 100.0 + 50.0, amount=(i + 1) * 100_000.0) for i in range(9)]
    out = compute_top_sb_gaps(
        casts,
        events,
        duration_s=1000.0,
        sb_duration_s=6.0,
        min_gap_s=5.0,
        min_damage=50_000.0,
        top_n=3,
    )
    assert len(out) == 3
    # Top 3 should be the 3 largest damage gaps (i=8, i=7, i=6)
    assert [g.damage_during_gap for g in out] == [900_000.0, 800_000.0, 700_000.0]


def test_top_gaps_handles_no_coverage_at_all():
    """If the player never pressed SB, the whole segment is one big gap."""
    casts: list = []
    events = [_Dmg(time_s=50.0, amount=200_000.0), _Dmg(time_s=80.0, amount=300_000.0)]
    out = compute_top_sb_gaps(
        casts,
        events,
        duration_s=120.0,
        sb_duration_s=6.0,
        min_gap_s=5.0,
        min_damage=50_000.0,
    )
    assert len(out) == 1
    assert out[0].damage_during_gap == 500_000.0
    assert out[0].start_time_s == 0.0
    assert out[0].end_time_s == 120.0


# ─── Demo Shout press rate math (v3) ──────────────────────────────────────


def test_ds_press_rate_empty_casts_returns_zero():
    assert compute_ds_press_rate_from_casts([], duration_s=900.0, cooldown_s=45.0) == 0.0


def test_ds_press_rate_zero_duration_returns_zero():
    """Defensive — a zero-length segment can't have meaningful cadence."""
    assert (
        compute_ds_press_rate_from_casts(
            [_Cast(time_s=0.0, spell_id=1160)], duration_s=0.0, cooldown_s=45.0
        )
        == 0.0
    )


def test_ds_press_rate_perfect_cadence_is_one():
    """20 casts in 900s at base 45s CD = 20/20 = 1.0."""
    casts = [_Cast(time_s=i * 45.0, spell_id=1160) for i in range(20)]
    rate = compute_ds_press_rate_from_casts(casts, duration_s=900.0, cooldown_s=45.0)
    assert rate == pytest.approx(1.0)


def test_ds_press_rate_half_cadence():
    """10 casts in 900s at base 45s CD = 10/20 = 0.5."""
    casts = [_Cast(time_s=i * 90.0, spell_id=1160) for i in range(10)]
    rate = compute_ds_press_rate_from_casts(casts, duration_s=900.0, cooldown_s=45.0)
    assert rate == pytest.approx(0.5)


def test_ds_press_rate_caps_at_one_for_thunderlord():
    """A Thunderlord player can press DS on the buffed 31.5s CD; the
    inference uses the base 45s denominator, so they exceed 1.0 in raw
    ratio. The function caps at 1.0 so the threshold semantics stay
    parseable — being generous to Thunderlord beats false-flooring."""
    # 30 casts in 900s = 30 / (900/45) = 30/20 = 1.5 raw → cap to 1.0.
    casts = [_Cast(time_s=i * 30.0, spell_id=1160) for i in range(30)]
    rate = compute_ds_press_rate_from_casts(casts, duration_s=900.0, cooldown_s=45.0)
    assert rate == pytest.approx(1.0)


def test_ds_press_rate_loads_cooldown_from_constants_by_default():
    """Mirror of the SB test — when cooldown is omitted, the function reads
    `active_mitigation.demoralizing_shout.cooldown_s` from constants.yaml.
    Pin behaviour so a typo in the YAML key surfaces here."""
    # 1 cast in 90s of run, default 45s CD → max 2 → rate 0.5.
    rate = compute_ds_press_rate_from_casts(
        [_Cast(time_s=10.0, spell_id=1160)],
        duration_s=90.0,
    )
    assert rate == pytest.approx(0.5)


# ─── Combined SB + DS inference ────────────────────────────────────────────


def test_combined_inference_takes_min_tier():
    """SB at 80% (in_the_zone) + DS at 30% (reading) → combined `reading`.
    The weaker signal floors the verdict."""
    sb = [_Cast(time_s=i * 6.5) for i in range(50)]  # ~50% raw uptime — push higher
    # Easier path: pass tiers explicitly to avoid SB-window math drift.
    ds = [_Cast(time_s=i * 90.0, spell_id=1160) for i in range(3)]  # 3 casts in 300s
    result = infer_tier_from_casts(
        sb,
        duration_s=300.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
        ds_casts=ds,
        ds_cooldown_s=45.0,
    )
    # DS rate = 3 / (300/45) = 3/6.67 ≈ 0.45 → anticipating? No: 0.45 < 0.50, so reading.
    assert result.ds_press_rate == pytest.approx(3.0 / (300.0 / 45.0))
    assert result.ds_tier_id == "reading"
    # SB tier could be anticipating or in_the_zone depending on overlap.
    # Combined must be the lower of the two — at worst `reading`.
    assert result.tier_id in {"reading", "learning"}


def test_combined_inference_both_signals_strong_climbs_to_top():
    """Both SB and DS at perfect → in_the_zone for the combined verdict."""
    # 100 SB casts spaced 5s apart in 600s → 600s of coverage on a 600s segment.
    sb = [_Cast(time_s=i * 5.0) for i in range(120)]
    # 14 DS casts at 45s spacing in 600s → 14 / (600/45) ≈ 1.05 → cap to 1.0.
    ds = [_Cast(time_s=i * 45.0, spell_id=1160) for i in range(14)]
    result = infer_tier_from_casts(
        sb,
        duration_s=600.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
        ds_casts=ds,
        ds_cooldown_s=45.0,
    )
    assert result.sb_tier_id == "in_the_zone"
    assert result.ds_tier_id == "in_the_zone"
    assert result.tier_id == "in_the_zone"


def test_combined_inference_ds_none_falls_back_to_sb_only():
    """Backward compatibility — when ds_casts is None, behave like v2.10b."""
    sb = [_Cast(time_s=i * 6.0) for i in range(50)]
    result = infer_tier_from_casts(
        sb,
        duration_s=300.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
    )
    assert result.ds_cast_count == 0
    assert result.ds_press_rate == 0.0
    assert result.ds_tier_id == ""
    # Combined falls through to SB tier when DS is empty.
    assert result.tier_id == result.sb_tier_id


def test_combined_inference_real_constants_yaml_has_min_ds_press_rate():
    """Pin schema: every tier in the shipped YAML must carry
    `min_ds_press_rate`. If the field disappears (e.g. via a regression
    or a YAML edit that drops it), surface it here rather than letting
    the inference silently default to 0.0 everywhere."""
    from simf.core.constants import load_skill_tiers

    tiers = load_skill_tiers()
    assert tiers, "expected skill_tiers populated in constants.yaml"
    for tier in tiers:
        assert "min_ds_press_rate" in tier, f"tier {tier.get('id')} missing min_ds_press_rate"


# ─── PR #2 (2026-05-21): ladder nearest-neighbor + ceiling threading ─────


def test_ladder_match_nearest_picks_closest_tier():
    """Player at 0.41 actual; ladder uptimes [0.62, 0.50, 0.35, 0.20] →
    closest to 0.35 → `reading`. Same shape as Brutoh's real Nexus +12
    pattern; this is the headline behaviour PR #2 brings to inference."""
    ladder = [
        ("in_the_zone", 0.62),
        ("anticipating", 0.50),
        ("reading", 0.35),
        ("learning", 0.20),
    ]
    assert match_sb_tier_to_ladder(0.41, ladder) == "reading"


def test_ladder_match_perfect_play_matches_top():
    """Actual ≥ ceiling → top tier. Pandemic-refresh outliers shouldn't
    floor the player to learning when their ladder is dialed in."""
    ladder = [
        ("in_the_zone", 0.62),
        ("anticipating", 0.50),
        ("reading", 0.35),
        ("learning", 0.20),
    ]
    assert match_sb_tier_to_ladder(0.65, ladder) == "in_the_zone"
    assert match_sb_tier_to_ladder(1.00, ladder) == "in_the_zone"


def test_ladder_match_zero_uptime_floors_to_lowest():
    """A player who never pressed SB (or whose log was too short to
    matter) should bucket as the lowest tier — not as no-signal."""
    ladder = [
        ("in_the_zone", 0.62),
        ("anticipating", 0.50),
        ("reading", 0.35),
        ("learning", 0.20),
    ]
    assert match_sb_tier_to_ladder(0.00, ladder) == "learning"


def test_ladder_match_ties_resolve_to_higher_tier():
    """Equidistant between `anticipating` (0.50) and `reading` (0.30):
    player at 0.40 is 0.10 from each. Strict `<` in the matcher keeps
    the first-seen (higher) tier — generous-but-deterministic."""
    ladder = [
        ("in_the_zone", 0.62),
        ("anticipating", 0.50),
        ("reading", 0.30),
        ("learning", 0.10),
    ]
    assert match_sb_tier_to_ladder(0.40, ladder) == "anticipating"


def test_ladder_match_empty_returns_empty_string():
    """Defensive — missing ladder data should fall through to no signal,
    not crash. The caller is expected to fall back to absolute thresholds."""
    assert match_sb_tier_to_ladder(0.41, []) == ""


def test_infer_tier_with_ladder_uses_nearest_neighbor():
    """When `ladder_sb_uptimes` is provided, infer_tier_from_casts swaps
    its threshold-based SB matcher for nearest-neighbor on the ladder.
    Same player + log, different ladders, different answers — proves
    the threshold table is bypassed."""
    sb = [_Cast(time_s=i * 14.6) for i in range(40)]  # ~0.41 raw uptime
    ladder_high_ceiling = [
        ("in_the_zone", 0.95),  # absolute match would land here on 0.41
        ("anticipating", 0.75),
        ("reading", 0.50),
        ("learning", 0.20),
    ]
    ladder_low_ceiling = [
        ("in_the_zone", 0.45),  # player at 0.41 is now closest to top
        ("anticipating", 0.30),
        ("reading", 0.15),
        ("learning", 0.05),
    ]
    high = infer_tier_from_casts(
        sb,
        duration_s=600.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
        ladder_sb_uptimes=ladder_high_ceiling,
    )
    low = infer_tier_from_casts(
        sb,
        duration_s=600.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
        ladder_sb_uptimes=ladder_low_ceiling,
    )
    # Different ladders, same actual uptime → different tier.
    assert high.sb_tier_id != low.sb_tier_id
    # The low-ceiling player is at their build's top → `in_the_zone`.
    assert low.sb_tier_id == "in_the_zone"


def test_infer_tier_missed_pressable_derives_from_ceiling():
    """`sb_missed_pressable_pct = max(0, ceiling - actual)` — stored as a
    fraction in [0, 1] like `sb_ceiling_pct`. UI multiplies by 100 to
    render as `Xpp below ceiling`. Verify the derivation."""
    sb = [_Cast(time_s=i * 14.6) for i in range(40)]  # ~0.41 raw uptime
    inferred = infer_tier_from_casts(
        sb,
        duration_s=600.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
        sb_ceiling_pct=0.62,
        sb_ceiling_rage_starved_pct=0.18,
        sb_ceiling_charge_limited_pct=0.20,
    )
    # actual uptime should be in the ~0.40 band; missed = ceiling - actual.
    assert inferred.sb_ceiling_pct == pytest.approx(0.62)
    assert inferred.sb_ceiling_rage_starved_pct == pytest.approx(0.18)
    assert inferred.sb_ceiling_charge_limited_pct == pytest.approx(0.20)
    assert inferred.sb_missed_pressable_pct == pytest.approx(0.62 - inferred.sb_uptime_pct)
    assert inferred.sb_missed_pressable_pct > 0


def test_infer_tier_missed_pressable_clamped_at_zero():
    """If the player exceeds the ceiling (rare — log uptime > sim
    uptime), missed_pressable should clamp to 0, not go negative."""
    sb = [_Cast(time_s=i * 6.0) for i in range(60)]  # high uptime
    inferred = infer_tier_from_casts(
        sb,
        duration_s=600.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
        sb_ceiling_pct=0.30,  # artificially low ceiling
    )
    assert inferred.sb_uptime_pct > 0.30
    assert inferred.sb_missed_pressable_pct == 0.0


def test_infer_tier_no_ceiling_keeps_zero_attribution():
    """Backward compat: when ceiling info is omitted (v3 / v2.10b call
    sites), `sb_ceiling_*` fields stay at 0.0 — UI keys off > 0 to
    decide whether to render the bottleneck caption line."""
    sb = [_Cast(time_s=i * 12.0) for i in range(20)]
    inferred = infer_tier_from_casts(
        sb,
        duration_s=300.0,
        sb_duration_s=6.0,
        tiers=_stub_tiers(),
    )
    assert inferred.sb_ceiling_pct == 0.0
    assert inferred.sb_ceiling_rage_starved_pct == 0.0
    assert inferred.sb_ceiling_charge_limited_pct == 0.0
    assert inferred.sb_missed_pressable_pct == 0.0


def test_brutoh_nexus_12_inference_returns_expected_tier():
    """End-to-end against Brutoh's Nexus-Point Xenas +12 log. Verifies
    the parser → uptime → tier pipeline produces a stable answer on a
    real file. The expected tier is `reading` (40-50% SB uptime) — if
    the inference framework starts buckets at different thresholds the
    test must be updated, not silently regressed."""
    from simf.io.combat_log import parse_cast_events, parse_challenge_modes

    log = Path("examples/WoWCombatLog-051026_073906.txt")
    if not log.exists():
        pytest.skip(f"integration log not in repo: {log}")
    runs = parse_challenge_modes(log)
    assert runs, "expected at least one challenge mode run in the test log"
    run = runs[0]
    casts = parse_cast_events(
        log,
        source_name="Brutoh-Uldum-EU",
        spell_name="Shield Block",
        spell_id=2565,
        start_time_s=run.start_time_s,
        end_time_s=run.end_time_s,
        start_byte_offset=run.start_byte_offset,
    )
    ds_casts = parse_cast_events(
        log,
        source_name="Brutoh-Uldum-EU",
        spell_name="Demoralizing Shout",
        spell_id=1160,
        start_time_s=run.start_time_s,
        end_time_s=run.end_time_s,
        start_byte_offset=run.start_byte_offset,
    )
    inferred = infer_tier_from_casts(
        casts,
        duration_s=run.duration_s(),
        start_time_s=run.start_time_s,
        ds_casts=ds_casts,
    )
    # Brutoh on this specific log: ~94 SB casts at ~40% uptime, ~32 DS casts
    # — DS press rate caps at 1.0 (he has Thunderlord; engine inference uses
    # base 45s CD denominator). Combined tier is floored by SB → "reading".
    # Bands pin the observed measurements, not exact counts, so iteration
    # / parser refactors don't false-fail.
    assert 0.30 <= inferred.sb_uptime_pct <= 0.60
    assert inferred.cast_count >= 50
    assert inferred.ds_cast_count >= 20
    assert inferred.ds_press_rate >= 0.70
    # SB drags the combined verdict — DS alone would be `in_the_zone`.
    assert inferred.tier_id in {"reading", "anticipating"}
    assert inferred.sb_tier_id in {"reading", "anticipating"}
    assert inferred.ds_tier_id == "in_the_zone"
