"""Vanguard — Prot Warrior spec-passive strength->armor conversion (2026-07-21).

Vanguard (spell 71, `find_specialization_spell` — always active for every
Protection Warrior, NOT a talent) adds bonus armor = Strength x 70%. Sourced
two independent ways in agreement: a live Wowhead tooltip for spell 71 (raw
Playwright accessibility-tree read — Effect #1 "Mod Armor From Stat %",
Value 70%) and SimC source (`sc_warrior.cpp`,
`warrior_t::composite_bonus_armor()`:
``ba += spec.vanguard->effectN(1).percent() * current_str;``, gated only on
`specialization() == WARRIOR_PROTECTION`). See
docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md.

This closes a stale TODO (constants.yaml had carried an unresolved
"empirical 0.258 vs spell value 40%" comment since 2026-05-18/19) — both
numbers were wrong: 40% was Vanguard's unrelated Stamina effect (Effect #2),
and 0.258 was an empirical fit measured under a replay chain with 3
mitigation bugs that were fixed the same day as this feature shipped.
"""

from __future__ import annotations

from simf.core.character import Character
from simf.core.constants import load_constants


def _coefficient() -> float:
    return load_constants()["specs"]["protection_warrior"]["vanguard_armor_per_strength"]


def test_vanguard_coefficient_is_sourced_070_not_the_old_placeholders():
    """Guards against silent re-tuning back toward either of the two old,
    now-refuted numbers (0.258 empirical fit, 0.40 mis-cited spell effect)."""
    coeff = _coefficient()
    assert coeff == 0.70
    assert coeff != 0.258
    assert coeff != 0.40


def test_vanguard_adds_strength_derived_armor_unconditionally():
    """Present with ZERO talents selected — Vanguard is a spec passive, not
    gated by any talent pick."""
    c = Character(
        name="t",
        race="human",  # no armor racial multiplier — keeps the delta clean
        class_spec="protection_warrior",
        talents="brutoh-actual",  # runs neither armor-multiplier talent
        armor_from_gear=5000,
        strength=2000,
        stamina=30000,
    )
    assert c.total_armor() == 5000 + 2000 * _coefficient()


def test_vanguard_scales_linearly_with_strength():
    base = Character(
        name="t",
        race="human",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        armor_from_gear=5000,
        strength=1000,
        stamina=30000,
    )
    doubled_strength = Character(
        name="t",
        race="human",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        armor_from_gear=5000,
        strength=2000,
        stamina=30000,
    )
    # The strength-derived slice alone should exactly double.
    delta_base = base.total_armor() - 5000
    delta_doubled = doubled_strength.total_armor() - 5000
    assert delta_doubled == 2 * delta_base


def test_vanguard_bonus_armor_is_multiplied_by_reinforced_plates_and_armor_spec():
    """SimC's real composite_armor() adds bonus_armor BEFORE applying
    composite_armor_multiplier() (Reinforced Plates / Armor Specialization) —
    see docs/simc-reference/composite_armor.cpp. So Vanguard's flat armor
    add must ALSO get the multiplicative talent bump, not just gear armor."""
    c = load_constants()
    strength = 2000.0
    gear_armor = 5000.0
    char = Character(
        name="t",
        race="human",
        class_spec="protection_warrior",
        talents="wowhead-prot",  # runs BOTH reinforced_plates + armor_specialization
        armor_from_gear=gear_armor,
        strength=strength,
        stamina=30000,
    )
    expected = gear_armor + strength * _coefficient()
    expected *= 1 + c["talents"]["reinforced_plates"]["armor_multiplier"]
    expected *= 1 + c["talents"]["armor_specialization"]["armor_multiplier"]
    assert abs(char.total_armor() - expected) < 1e-9


def test_vanguard_does_not_leak_into_other_specs():
    """Vanguard is Protection-Warrior-only — every other modelled spec's
    total_armor() must be completely unaffected by `strength`, even an
    absurd value that would dominate the result if it leaked."""
    for spec in ("blood_death_knight", "vengeance_demon_hunter"):
        char = Character(
            name="t",
            race="human",
            class_spec=spec,
            talents="brutoh-actual",  # any loadout; not read by non-warrior specs
            armor_from_gear=5000,
            strength=99999,
            stamina=30000,
        )
        assert char.total_armor() == 5000

    def _guardian(strength: float) -> Character:
        return Character(
            name="t",
            race="human",
            class_spec="guardian_druid",
            talents="brutoh-actual",
            armor_from_gear=5000,
            strength=strength,
            agility=1000,
            stamina=30000,
        )

    # Guardian's own armor math (Bear Form + Ironfur, both agility-keyed)
    # must be bit-identical regardless of strength.
    assert _guardian(0).total_armor() == _guardian(99999).total_armor()
