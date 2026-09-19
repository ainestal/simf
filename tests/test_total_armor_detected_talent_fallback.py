"""Regression tests for the `Character.total_armor()` detected-talent-id
dead-branch bug (2026-07-11).

THE BUG: `total_armor()`'s Prot Warrior branch matched
`detected_talent_spell_ids` (populated by `calibrate-k` from COMBATANT_INFO
talent blocks via `iter_combatant_info`) against `talents.<name>.spell_id`
in constants.yaml. But COMBATANT_INFO's talent-block middle values are
trait-node-ENTRY ids, not spell ids — verified empirically against
`examples/bruttah-prot/WoWCombatLog-070326_074225.txt` and
`examples/WoWCombatLog-051026_073906.txt` (a warrior-calibration-corpus
log): every observed middle value falls in ~80140-137078, with ZERO
intersection with any modelled Prot Warrior spell_id (382939, 1234769,
etc. — the ranges are disjoint). So the match could NEVER succeed on a
real log, and — worse — a zero-match detection attempt silently suppressed
the YAML-loadout fallback the `else` branch would have applied, leaving
armor unmultiplied (×1.0) for every log-detected character whose real
build runs reinforced_plates / armor_specialization.

THE FIX: when the detected-id match applies nothing, fall through to the
YAML `_talent_set()` loop instead of silently skipping it. A detected
match (if a real trait-entry-id mapping ever lands) stays authoritative
and skips the fallback — never double-applied.
"""

from __future__ import annotations

import dataclasses

from simf.core.character import Character
from simf.core.constants import load_constants

# Real trait-node-ENTRY ids extracted from COMBATANT_INFO talent blocks in
# examples/bruttah-prot/WoWCombatLog-070326_074225.txt (e.g. node 94962 ->
# entry 117559). These are what `detected_talent_spell_ids` actually
# carries on every real ACL log today — NOT spell ids.
REAL_LOG_ENTRY_IDS = frozenset({117559, 117563, 126352, 126397, 135705, 136060, 137050})

# The two modelled armor-talent spell ids (constants.yaml `talents:`).
REINFORCED_PLATES_SPELL_ID = 382939
ARMOR_SPECIALIZATION_SPELL_ID = 1234769


def _warrior(**overrides) -> Character:
    base = Character(
        name="test",
        race="human",  # no armor racial — keeps expected values clean
        class_spec="protection_warrior",
        # wowhead-prot is a shipped loadout that runs BOTH armor talents
        # (reinforced_plates +5%, armor_specialization +6%).
        talents="wowhead-prot",
        stamina=25000,
        armor_from_gear=100000,
        strength=3000,
    )
    return dataclasses.replace(base, **overrides)


def _vanguard_armor(strength: float = 3000.0) -> float:
    """Vanguard's unconditional spec-passive armor add (2026-07-21) — every
    Protection Warrior gets this regardless of talent selection. See
    docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md."""
    c = load_constants()
    return strength * c["specs"]["protection_warrior"]["vanguard_armor_per_strength"]


def _expected_both_talents_armor(base: float = 100000.0, strength: float = 3000.0) -> float:
    """(base + Vanguard) ×(1+reinforced_plates) ×(1+armor_specialization),
    applied SEQUENTIALLY exactly as total_armor() does (Vanguard's flat add
    happens BEFORE the talent multipliers, matching SimC's real
    composite_armor() ordering — bonus_armor is added before
    composite_armor_multiplier) so the comparison is bit-exact, not merely
    approx-equal."""
    talents = load_constants()["talents"]
    armor = base + _vanguard_armor(strength)
    armor *= 1 + talents["reinforced_plates"]["armor_multiplier"]
    armor *= 1 + talents["armor_specialization"]["armor_multiplier"]
    return armor


def test_real_log_entry_ids_are_disjoint_from_modelled_spell_ids():
    """Precondition the whole bug rests on: the ids a real log yields can
    never match the modelled armor-talent spell ids."""
    assert not REAL_LOG_ENTRY_IDS & {
        REINFORCED_PLATES_SPELL_ID,
        ARMOR_SPECIALIZATION_SPELL_ID,
    }


def test_entry_id_detection_no_longer_suppresses_yaml_armor_credit():
    """THE regression test — fails on the pre-fix code.

    A character whose YAML loadout runs both armor talents, hydrated with
    real-log entry ids (which can never match a spell_id): pre-fix, the
    zero-match detected branch silently swallowed the YAML credit and left
    armor at ×1.0. Post-fix it falls back to the YAML loadout and matches
    the detected=None character exactly.
    """
    detected = _warrior(detected_talent_spell_ids=REAL_LOG_ENTRY_IDS)
    yaml_only = _warrior(detected_talent_spell_ids=None)
    assert detected.total_armor() == yaml_only.total_armor()
    # And that shared value really carries the armor-talent credit.
    assert detected.total_armor() == _expected_both_talents_armor()


def test_matching_detected_ids_stay_authoritative_never_double_applied():
    """If the detected ids DO match (synthetic today — only possible if a
    real trait-entry mapping ever lands), the detected signal is trusted
    as-is and the YAML fallback must NOT stack on top."""
    char = _warrior(
        detected_talent_spell_ids=frozenset(
            {REINFORCED_PLATES_SPELL_ID, ARMOR_SPECIALIZATION_SPELL_ID}
        )
    )
    # Exactly one application of each multiplier — not squared via fallback.
    assert char.total_armor() == _expected_both_talents_armor()


def test_partial_detected_match_is_authoritative_no_yaml_topup():
    """A single detected match means the detected signal 'worked' — the
    YAML loadout (which has BOTH talents) must not top up the second one."""
    talents = load_constants()["talents"]
    char = _warrior(detected_talent_spell_ids=frozenset({REINFORCED_PLATES_SPELL_ID}))
    assert char.total_armor() == (100000 + _vanguard_armor()) * (
        1 + talents["reinforced_plates"]["armor_multiplier"]
    )


def test_talent_set_override_still_wins_over_detected_ids():
    """`talent_set_override` is authoritative over detected ids — even ids
    that would match — exactly as before the fix."""
    no_armor_override = _warrior(
        detected_talent_spell_ids=frozenset(
            {REINFORCED_PLATES_SPELL_ID, ARMOR_SPECIALIZATION_SPELL_ID}
        ),
        talent_set_override=frozenset({"indomitable"}),
    )
    assert no_armor_override.total_armor() == 100000.0 + _vanguard_armor()

    talents = load_constants()["talents"]
    with_plates_override = _warrior(
        detected_talent_spell_ids=REAL_LOG_ENTRY_IDS,
        talent_set_override=frozenset({"reinforced_plates"}),
    )
    assert with_plates_override.total_armor() == (100000 + _vanguard_armor()) * (
        1 + talents["reinforced_plates"]["armor_multiplier"]
    )


def test_detected_none_path_bit_identical():
    """The plain (no detection, no override) path is pinned to its exact
    pre-fix values: YAML loadout with both armor talents multiplies, a
    loadout without them leaves gear armor untouched."""
    assert _warrior().total_armor() == _expected_both_talents_armor()
    # brutoh-actual (the calibration loadout) runs neither armor talent —
    # but still gets Vanguard, the unconditional spec passive.
    assert _warrior(talents="brutoh-actual").total_armor() == 100000.0 + _vanguard_armor()
