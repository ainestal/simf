"""``_trial_delta_vs_baseline`` routes trinket slots through the same
on-use/proc registry (`trinket_swap_per_dungeon`) the Gear-tab recommendation
card uses (F-005, 2026-07-19).

Before this fix, the trial banner scored every slot — including trinkets —
purely on stat deltas, while the recommendation card that suggested the swap
valued trinkets on their real proc/on-use effect via `optimizer/trinket_db`.
For a proc-heavy trinket with a weak stat budget, that produced an opposite
SIGN between the card ("+2.10% (+41,896 eHP)") and the trial banner for the
exact same swap ("-0.33% (-6,474 eHP)") — a self-contradicting recommendation,
found independently by two live-browser tank personas + ui-craft-critic in the
2026-07-19 R3 review round.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from simf.core.character import Character
from simf.optimizer.per_dungeon import score_item_across_dungeons, trinket_swap_per_dungeon
from simf.ui import trial_banner


@dataclass
class _Spec:
    item_id: int
    slot: str = "trinket1"


def _brutoh() -> Character:
    """Approximate Brutoh stats so trinket_ehp_contribution has a real char
    (same fixture shape as test_trinket_swap_per_dungeon.py)."""
    return Character(
        name="Test",
        race="earthen",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=34176,
        armor_from_gear=5015,
        haste_rating=2318,
        crit_rating=1391,
        mastery_rating=1608,
        versatility_rating=296,
    )


_DUNGEONS = [
    {"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.90}},
    {"id": "np", "abbrev": "NP", "school_mix": {"physical": 0.30}},
]


def test_trinket_trial_delta_matches_recommendation_registry(monkeypatch):
    """The exact regression: a trinket swap both sides of the registry know
    about must score the SAME number here as `trinket_swap_per_dungeon` gives
    the recommendation card — not an independently-computed stats-only
    number that can disagree in sign."""
    equipped = _Spec(item_id=249343, slot="trinket1")  # Gaze of the Alnseer
    candidate = _Spec(item_id=260235, slot="trinket1")  # Umbral Plume
    monkeypatch.setattr(trial_banner, "_baseline_equipped", lambda: {"trinket1": equipped})

    char = _brutoh()
    registry_scores, both_known = trinket_swap_per_dungeon(equipped, candidate, char, _DUNGEONS)
    assert both_known is True  # both sides catalogued — this is the card's exact number
    expected = sum(s.delta_ehp for s in registry_scores) / len(registry_scores)

    got = trial_banner._trial_delta_vs_baseline(
        "trinket1", candidate, marginals={}, dungeons=_DUNGEONS, char=char
    )
    assert got == pytest.approx(expected)


def test_unknown_candidate_trinket_falls_back_to_stats_only(monkeypatch):
    """A candidate not in the registry still falls back to the pre-existing
    stats-only path (unchanged behavior) instead of raising or fabricating a
    registry number — mirrors the recommendation card's own fallback."""
    equipped = _Spec(item_id=249343, slot="trinket1")
    unknown_candidate = _Spec(item_id=99999999, slot="trinket1")
    monkeypatch.setattr(trial_banner, "_baseline_equipped", lambda: {"trinket1": equipped})
    monkeypatch.setattr(trial_banner, "_stats_for_item", lambda item: None)

    got = trial_banner._trial_delta_vs_baseline(
        "trinket1", unknown_candidate, marginals={}, dungeons=_DUNGEONS, char=_brutoh()
    )
    assert got == 0.0  # both sides' stats are None → zero stat delta, zero eHP delta


def test_non_trinket_slot_never_touches_registry(monkeypatch):
    """Non-trinket slots keep the exact pre-fix stats-only path — a
    regression guard against accidentally routing every slot through the
    trinket registry."""
    equipped_item = _Spec(item_id=1, slot="head")
    new_item = _Spec(item_id=2, slot="head")
    stats_by_id = {id(equipped_item): {"stamina": 50}, id(new_item): {"stamina": 100}}
    monkeypatch.setattr(trial_banner, "_baseline_equipped", lambda: {"head": equipped_item})
    monkeypatch.setattr(trial_banner, "_stats_for_item", lambda item: stats_by_id[id(item)])

    marginals = {"stamina": {"p": 1.0, "m": 1.0}}
    expected_scores = score_item_across_dungeons(
        new_stats={"stamina": 100},
        equipped_stats={"stamina": 50},
        marginals=marginals,
        dungeons=_DUNGEONS,
    )
    expected = sum(s.delta_ehp for s in expected_scores) / len(expected_scores)

    got = trial_banner._trial_delta_vs_baseline(
        "head", new_item, marginals=marginals, dungeons=_DUNGEONS, char=_brutoh()
    )
    assert got == pytest.approx(expected)
