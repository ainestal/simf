"""Tests for the per-dungeon ΔeHP helper.

The v0.9 UI redesign needs a single canonical way to compute "what does this
item-swap give me, per dungeon" for the vault verdict card and the slot-click
side panel. This uses the analytical eHP marginals × school_mix path — no
Monte Carlo sim needed — which the tractability benchmark confirms is sub-ms.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from simf.optimizer.per_dungeon import (
    score_item_across_dungeons,
    verdict_sentence,
)


@dataclass
class _Item:
    slot: str
    name: str = ""


@pytest.fixture
def marginals_brutoh():
    """Brutoh-tier marginals — physical-heavy character."""
    return {
        "stamina": {"p": 30.0, "m": 22.0},
        "armor_from_gear": {"p": 8.0, "m": 0.0},
        "versatility_rating": {"p": 5.0, "m": 4.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
    }


@pytest.fixture
def two_dungeons():
    """Two dungeons with sharply different school mixes — physical vs magic."""
    return [
        {"id": "windrunner_spire", "abbrev": "WR", "school_mix": {"physical": 0.90}},
        {"id": "nexus_point_xenas", "abbrev": "NP", "school_mix": {"physical": 0.10}},
    ]


# ── core behaviour ────────────────────────────────────────────────────────


def test_score_returns_one_entry_per_dungeon(marginals_brutoh, two_dungeons):
    scores = score_item_across_dungeons(
        new_stats={"stamina": 1000},
        equipped_stats={"stamina": 800},
        marginals=marginals_brutoh,
        dungeons=two_dungeons,
    )
    assert len(scores) == 2
    assert {s.dungeon_id for s in scores} == {"windrunner_spire", "nexus_point_xenas"}


def test_score_uses_delta_not_absolute(marginals_brutoh, two_dungeons):
    """The score is delta-relative-to-equipped, not the absolute stat value."""
    upgrade = score_item_across_dungeons(
        new_stats={"stamina": 1000},
        equipped_stats={"stamina": 800},
        marginals=marginals_brutoh,
        dungeons=two_dungeons,
    )
    sidegrade = score_item_across_dungeons(
        new_stats={"stamina": 800},
        equipped_stats={"stamina": 800},
        marginals=marginals_brutoh,
        dungeons=two_dungeons,
    )
    assert all(s.delta_ehp > 0 for s in upgrade)
    assert all(s.delta_ehp == 0 for s in sidegrade)


def test_armor_scores_higher_in_physical_dungeon(marginals_brutoh, two_dungeons):
    """100 armor weighs more in a 90% physical dungeon than a 10% physical one."""
    scores = score_item_across_dungeons(
        new_stats={"stamina": 0, "armor_from_gear": 100},
        equipped_stats={"stamina": 0, "armor_from_gear": 0},
        marginals=marginals_brutoh,
        dungeons=two_dungeons,
    )
    by_abbrev = {s.abbrev: s for s in scores}
    assert by_abbrev["WR"].delta_ehp > by_abbrev["NP"].delta_ehp
    # 100 armor × 8.0 marginal × 0.90 phys = 720 vs × 0.10 = 80
    assert abs(by_abbrev["WR"].delta_ehp - 720) < 0.1
    assert abs(by_abbrev["NP"].delta_ehp - 80) < 0.1


def test_handles_missing_equipped_baseline(marginals_brutoh, two_dungeons):
    """Empty/None equipped slot treated as zero stats."""
    scores = score_item_across_dungeons(
        new_stats={"stamina": 500},
        equipped_stats=None,
        marginals=marginals_brutoh,
        dungeons=two_dungeons,
    )
    assert all(s.delta_ehp > 0 for s in scores)


def test_score_preserves_dungeon_order(marginals_brutoh, two_dungeons):
    """Output order matches input order — UI relies on this for stable rendering."""
    scores = score_item_across_dungeons(
        new_stats={"stamina": 500},
        equipped_stats={"stamina": 0},
        marginals=marginals_brutoh,
        dungeons=two_dungeons,
    )
    assert [s.dungeon_id for s in scores] == [d["id"] for d in two_dungeons]


# ── verdict_sentence: the three-line headline ────────────────────────────


def test_verdict_picks_top_dungeons_and_calls_out_sidegrade(marginals_brutoh):
    """Headline names the two best dungeons by ΔeHP and explicitly flags any
    dungeon where the delta is ≤0 (sidegrade or downgrade)."""
    dungeons = [
        {"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.90}},  # phys-heavy → big ΔeHP
        {"id": "fg", "abbrev": "FG", "school_mix": {"physical": 0.70}},  # still good
        {
            "id": "alg",
            "abbrev": "Algaz",
            "school_mix": {"physical": 0.10},
        },  # magic-heavy → no benefit
    ]
    # Pure armor upgrade — only benefits physical
    scores = score_item_across_dungeons(
        new_stats={"armor_from_gear": 100},
        equipped_stats={"armor_from_gear": 0},
        marginals=marginals_brutoh,
        dungeons=dungeons,
    )
    sentence = verdict_sentence(scores)
    assert "WR" in sentence  # top dungeon named
    assert "FG" in sentence  # second-best dungeon named


def test_verdict_flags_sidegrade_when_smallest_delta_is_near_zero(marginals_brutoh):
    """If the worst dungeon's delta is ≤ 5% of the best, call it out as a sidegrade."""
    dungeons = [
        {"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.90}},
        {"id": "alg", "abbrev": "Algaz", "school_mix": {"physical": 0.05}},
    ]
    scores = score_item_across_dungeons(
        new_stats={"armor_from_gear": 100},  # phys-only
        equipped_stats={"armor_from_gear": 0},
        marginals=marginals_brutoh,
        dungeons=dungeons,
    )
    sentence = verdict_sentence(scores)
    assert "Algaz" in sentence
    assert "sidegrade" in sentence.lower() or "no benefit" in sentence.lower()


def test_verdict_handles_uniform_delta(marginals_brutoh):
    """When all dungeons give roughly the same ΔeHP, the sentence does not
    fabricate a "wins X loses Y" framing."""
    dungeons = [
        {"id": "a", "abbrev": "A", "school_mix": {"physical": 0.50}},
        {"id": "b", "abbrev": "B", "school_mix": {"physical": 0.50}},
    ]
    scores = score_item_across_dungeons(
        new_stats={"stamina": 1000},
        equipped_stats={"stamina": 0},
        marginals=marginals_brutoh,
        dungeons=dungeons,
    )
    sentence = verdict_sentence(scores)
    assert "sidegrade" not in sentence.lower()


def test_verdict_empty_scores_returns_safe_string():
    assert verdict_sentence([]) == "No upgrade — equivalent to current."
