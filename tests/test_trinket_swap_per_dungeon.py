"""`trinket_swap_per_dungeon` integration — wires `optimizer/trinket_db`
into the recommender so known trinkets get proc/use-aware ΔeHP and the
⚠️ stats-only warning only fires on unknowns.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.core.character import Character
from simf.optimizer.per_dungeon import trinket_swap_per_dungeon


@dataclass
class _Spec:
    item_id: int
    slot: str = "trinket1"


def _brutoh() -> Character:
    """Approximate Brutoh stats so trinket_ehp_contribution has a real char."""
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


def test_returns_none_when_candidate_not_in_registry():
    """Unknown trinket id → caller falls back to stats-only with warning."""
    cand = _Spec(item_id=99999999)
    eq = _Spec(item_id=249343)  # Gaze of the Alnseer is in registry
    out = trinket_swap_per_dungeon(eq, cand, _brutoh(), _DUNGEONS)
    assert out is None


def test_returns_scores_and_both_known_flag_when_both_in_registry():
    """Both equipped and candidate registered → (scores, both_known=True).
    UI drops the warning glyph."""
    eq = _Spec(item_id=249343)  # Gaze of the Alnseer
    cand = _Spec(item_id=260235)  # Umbral Plume
    out = trinket_swap_per_dungeon(eq, cand, _brutoh(), _DUNGEONS)
    assert out is not None
    scores, both_known = out
    assert both_known is True
    assert len(scores) == 2
    assert {s.dungeon_id for s in scores} == {"wr", "np"}


def test_both_known_false_when_equipped_unknown():
    """Candidate known but equipped not in registry → asymmetric
    comparison; warning stays on."""
    eq = _Spec(item_id=99999999)
    cand = _Spec(item_id=260235)
    out = trinket_swap_per_dungeon(eq, cand, _brutoh(), _DUNGEONS)
    assert out is not None
    scores, both_known = out
    assert both_known is False
    assert len(scores) == 2


def test_empty_equipped_slot_treats_as_no_trinket():
    """No equipped trinket → contribution baseline is zero. both_known
    stays False because there's nothing to compare against. ΔeHP may be
    zero if the candidate's stats don't include any eHP-contributing
    keys (passive secondaries like haste/crit don't help survivability)."""
    cand = _Spec(item_id=260235)
    out = trinket_swap_per_dungeon(None, cand, _brutoh(), _DUNGEONS)
    assert out is not None
    scores, both_known = out
    assert both_known is False
    assert len(scores) == 2  # one per dungeon
