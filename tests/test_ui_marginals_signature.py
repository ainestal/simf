"""Regression test for the marginals-cache signature (2026-07-05 gem-trust
review; extended 2026-07-08 for the same bug class one precedent later).

`_char_marginals_signature` must include every stat the sim perturbs, or a
change to that stat alone (a gem/enchant trial swap, or a log-hydrated
character vs. a `/simc`-paste of the same ratings) silently reuses a stale
cached marginals dict.

Agility was missing until PR #275 — it's the largest Guardian/Brewmaster/VDH
marginal. `shield_armor` (feeds Warrior/Paladin block value),
`active_buff_spell_ids` (gates Guardian's Elune's-Chosen-scaled Ironfur and
Brewmaster's Predictive Training), and `stamina_in_caster_form` (gates
Guardian's Bear-Form HP multiplier) were missing until this fix.

`max_hp_override` and `detected_talent_spell_ids` were a THIRD instance of
the same bug class, found in the 2026-07-10 audit: `ui/helpers/run_config.
py`'s `_REPRO_HASH_CHAR_FIELDS` already carried both (added 2026-07-08,
Batch F), but this module's `_char_marginals_signature` carried neither —
the two lists were hand-maintained separately and had drifted. Both fields
now come from the single shared `core.character_fields.
SIM_AFFECTING_CHARACTER_FIELDS` tuple both consumers import — see
`tests/test_character_fields.py` for the structural test guarding against a
FOURTH instance of this drift, and for the specific
`detected_talent_spell_ids` cache-collision regression test.
"""

from __future__ import annotations

from dataclasses import replace

from simf.core.character import Character
from simf.ui.marginals import _char_marginals_signature


def _char(**overrides) -> Character:
    base = Character(
        name="test",
        race="worgen",
        class_spec="guardian_druid",
        talents="anonguardian1-guardian",
        strength=0,
        agility=3000,
        stamina=32000,
        armor_from_gear=5000,
        haste_rating=1000,
        crit_rating=1200,
        mastery_rating=1500,
        versatility_rating=300,
    )
    return replace(base, **overrides)


def test_agility_only_change_produces_a_different_signature():
    a = _char(agility=3000)
    b = _char(agility=3200)
    assert _char_marginals_signature(a) != _char_marginals_signature(b)


def test_signature_includes_the_live_agility_value():
    char = _char(agility=3000)
    assert 3000 in _char_marginals_signature(char)


def test_shield_armor_only_change_produces_a_different_signature():
    """shield_armor feeds Warrior/Paladin block value (shield_armor * 2.5) —
    a change here must not silently reuse another character's cached
    marginals."""
    a = _char(shield_armor=1000)
    b = _char(shield_armor=1200)
    assert _char_marginals_signature(a) != _char_marginals_signature(b)


def test_active_buff_spell_ids_only_change_produces_a_different_signature():
    """A log-hydrated character carries active_buff_spell_ids (e.g. Elune's
    Chosen 202770, Predictive Training 451230); a /simc-paste of the same
    ratings does not — they must not collide on the same cache entry."""
    a = _char(active_buff_spell_ids=frozenset({202770}))
    b = _char(active_buff_spell_ids=frozenset())
    assert _char_marginals_signature(a) != _char_marginals_signature(b)


def test_active_buff_spell_ids_signature_is_order_independent():
    """A frozenset has no stable iteration order — the signature must sort
    before hashing/inclusion so two characters with the same buff set (built
    in different orders) always produce the same signature."""
    a = _char(active_buff_spell_ids=frozenset({202770, 451230}))
    b = _char(active_buff_spell_ids=frozenset({451230, 202770}))
    assert _char_marginals_signature(a) == _char_marginals_signature(b)


def test_stamina_in_caster_form_only_change_produces_a_different_signature():
    """stamina_in_caster_form gates Guardian's Bear-Form x1.40 HP multiplier."""
    a = _char(stamina_in_caster_form=False)
    b = _char(stamina_in_caster_form=True)
    assert _char_marginals_signature(a) != _char_marginals_signature(b)


def test_max_hp_override_only_change_produces_a_different_signature():
    """max_hp_override is a no-op for the SIM path (compute_survivability_
    marginals strips it before perturbing — see survivability_weights.py's
    `dc_replace(character, max_hp_override=None)`), but `_marginals_for` can
    also cache a CLOSED-FORM `ehp_marginals(char)` result on a sim error/
    anchor-failure, and that path reads `char.max_hp()` directly, which DOES
    honor the override. One cache key can end up storing either kind of
    value, so the signature must stay safe for the closed-form case too."""
    a = _char(max_hp_override=None)
    b = _char(max_hp_override=250000)
    assert _char_marginals_signature(a) != _char_marginals_signature(b)


def test_detected_talent_spell_ids_only_change_produces_a_different_signature():
    """detected_talent_spell_ids changes Prot Warrior's armor multiplier in
    total_armor() (reinforced_plates/armor_specialization matched by spell
    id, COMBATANT_INFO-detected talents) — was missing from this signature
    entirely until the 2026-07-10 audit, though it was already present in
    run_config.py's reproduction-hash field list. See
    tests/test_character_fields.py::test_detected_talent_spell_ids_collision_is_fixed
    for the real-armor-value version of this regression."""
    a = _char(detected_talent_spell_ids=frozenset({382939}))
    b = _char(detected_talent_spell_ids=frozenset())
    assert _char_marginals_signature(a) != _char_marginals_signature(b)


def test_detected_talent_spell_ids_signature_is_order_independent():
    """Same reasoning as active_buff_spell_ids: a frozenset has no stable
    iteration order, so the signature must sort before inclusion."""
    a = _char(detected_talent_spell_ids=frozenset({382939, 1234769}))
    b = _char(detected_talent_spell_ids=frozenset({1234769, 382939}))
    assert _char_marginals_signature(a) == _char_marginals_signature(b)
