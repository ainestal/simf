"""Unit tests for mastery → critical-block-chance coefficient (audit F7).

F7 retune (2026-05-20): coefficient flipped 1.0 → 1.5, per SimC
`sc_warrior.cpp:8990` — `cache.mastery() × effectN(1).mastery_value()`
where `effectN(1).mastery_value() = 1.5` per spell 76857.

In-game tooltip cross-check at 8% mastery: crit-block +12% → 1.5 × 8.

This ships atomically with F11 (mastery_block_value_scaling 0.5 → 0.0,
landed with F12) and F13 (mastery_block_chance_scaling 0.0 → 0.5,
newly wired into base_block). The previous single-coefficient attempt
was reverted; the atomic chain is the structurally-correct shape.

See docs/validation/k_calibration_mastery_chain_2026_05_20.md.
"""

from __future__ import annotations

import pytest

from simf.core.character import Character
from simf.core.constants import load_constants


def _prot_warrior(mastery_rating: int = 0) -> Character:
    return Character(
        name="Test",
        race="human",
        class_spec="protection_warrior",
        talents="",
        strength=10_000,
        stamina=50_000,
        armor_from_gear=20_000,
        mastery_rating=mastery_rating,
    )


def test_crit_block_coefficient_is_one_point_five():
    """F7 retune: mastery_crit_block_scaling = 1.5 (SimC effectN(1).mastery_value())."""
    c = load_constants()
    assert c["base"]["mastery_crit_block_scaling"] == 1.5


def test_crit_block_chance_at_base_mastery():
    """0 mastery rating → crit_block_chance == base_mastery × 1.5."""
    char = _prot_warrior(mastery_rating=0)
    c = load_constants()
    base = c["base"]["mastery_pct_warrior_prot"]
    expected = base * c["base"]["mastery_crit_block_scaling"]
    assert char.critical_block_chance() == pytest.approx(expected, abs=1e-9)


def test_crit_block_chance_scales_with_mastery_at_one_point_five():
    """+1000 mastery rating (10%) → +15pp crit block chance over the base."""
    base_char = _prot_warrior(mastery_rating=0)
    plus_char = _prot_warrior(mastery_rating=1000)

    delta = plus_char.critical_block_chance() - base_char.critical_block_chance()
    # 1000 rating / 100 = 10% mastery, below DR breakpoint at 30%.
    # 1.5 coefficient → +15pp.
    assert delta == pytest.approx(0.15, abs=1e-9)


def test_crit_block_chance_is_mastery_pct_times_one_point_five():
    """At any rating below DR, crit_block_chance == mastery_pct × 1.5."""
    char = _prot_warrior(mastery_rating=1500)
    assert char.critical_block_chance() == pytest.approx(char.mastery_pct() * 1.5, abs=1e-9)
