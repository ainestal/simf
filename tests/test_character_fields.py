"""Tests for `core.character_fields` — the single source of truth for which
`Character` fields the marginals-cache signature (`ui/marginals.py::
_char_marginals_signature`) and the reproduction hash
(`ui/helpers/run_config.py::compute_reproduction_hash`) must both include.

This module exists because those two lists were hand-maintained SEPARATELY
and drifted apart twice from the same root cause (a field added to
`Character` updated one but not the other): PR #275 (`agility` was missing
from the marginals signature alone) and a second gap found in the
2026-07-10 audit (`max_hp_override` / `detected_talent_spell_ids` were in
`run_config.py`'s list but not `ui/marginals.py`'s — despite a comment in
`run_config.py` explicitly, and at the time incorrectly, claiming the two
lists were "the same"). The tests below assert the two consumers can no
longer structurally diverge, and regression-test the specific collision the
drift allowed.
"""

from __future__ import annotations

import dataclasses

from simf.core.character import Character
from simf.core.character_fields import (
    SIM_AFFECTING_CHARACTER_FIELDS,
    normalize_sim_field,
    sim_affecting_signature,
)
from simf.ui.helpers.run_config import _REPRO_HASH_CHAR_FIELDS
from simf.ui.marginals import _char_marginals_signature


def _base_warrior(**overrides) -> Character:
    base = Character(
        name="test",
        race="human",
        class_spec="protection_warrior",
        talents="archon-meta",
        stamina=25000,
        armor_from_gear=100000,
        strength=3000,
    )
    return dataclasses.replace(base, **overrides)


def test_run_config_and_marginals_share_the_identical_field_list():
    """`_REPRO_HASH_CHAR_FIELDS` must be the SAME tuple object as
    `SIM_AFFECTING_CHARACTER_FIELDS`, not merely equal today — an `==` check
    alone would still pass if someone reintroduced a hand-copied literal
    tuple that happens to match right now and silently drifts tomorrow.
    `is` catches that regression the moment it's reintroduced."""
    assert _REPRO_HASH_CHAR_FIELDS is SIM_AFFECTING_CHARACTER_FIELDS


def test_every_sim_affecting_field_is_a_real_character_field():
    """A stale or renamed entry in the shared list would silently stop being
    read by BOTH consumers at once (rather than just one, the old failure
    mode) — catch a typo'd/removed field name here instead of two caches
    quietly going stale together."""
    real_fields = {f.name for f in dataclasses.fields(Character)}
    missing = set(SIM_AFFECTING_CHARACTER_FIELDS) - real_fields
    assert not missing, f"listed fields no longer exist on Character: {missing}"


def test_marginals_signature_is_built_from_the_shared_signature_helper():
    """`_char_marginals_signature` must literally be `(*sim_affecting_
    signature(char), constants_version)` — not a hand-rebuilt tuple that
    could quietly diverge in field order, count, or normalization from what
    `run_config.py` hashes."""
    char = _base_warrior(shield_armor=12000, active_buff_spell_ids=frozenset({1, 2}))
    sig = _char_marginals_signature(char)
    shared = sim_affecting_signature(char)
    assert sig[: len(shared)] == shared
    assert len(shared) == len(SIM_AFFECTING_CHARACTER_FIELDS)


def test_normalize_sim_field_sorts_sets_into_a_stable_hashable_tuple():
    """Must be hashable (for the marginals cache's dict key) AND JSON-stable
    (for the reproduction hash's `json.dumps` payload) — a sorted tuple is
    both; a bare frozenset is neither."""
    assert normalize_sim_field(frozenset({3, 1, 2})) == (1, 2, 3)
    assert normalize_sim_field(frozenset({3, 1, 2})) == normalize_sim_field(frozenset({1, 2, 3}))
    assert normalize_sim_field(None) is None
    assert normalize_sim_field(42) == 42
    assert hash(normalize_sim_field(frozenset({1, 2, 3})))  # must not raise


def test_detected_talent_spell_ids_changes_real_total_armor():
    """Sanity check that `detected_talent_spell_ids` is genuinely
    sim-affecting, not just defensively added to the list: Prot Warrior's
    armor-multiplier talents (reinforced_plates 382939 +5%,
    armor_specialization 1234769 +6%, see data/constants.yaml) are matched
    by spell id via `Character.total_armor()` when this field is set."""
    no_talents = _base_warrior(detected_talent_spell_ids=frozenset())
    with_talents = _base_warrior(detected_talent_spell_ids=frozenset({382939, 1234769}))
    assert with_talents.total_armor() > no_talents.total_armor()


def test_detected_talent_spell_ids_collision_is_fixed():
    """THE regression this module exists to prevent: two Prot Warriors with
    identical stats but different detected-talent armor multipliers must
    NOT share a marginals-cache signature. Before this fix,
    `_char_marginals_signature` omitted `detected_talent_spell_ids` entirely
    (it was hand-copied into `run_config.py`'s list but never added here) —
    this assertion would have FAILED against the pre-fix hardcoded tuple."""
    no_talents = _base_warrior(detected_talent_spell_ids=frozenset())
    with_talents = _base_warrior(detected_talent_spell_ids=frozenset({382939, 1234769}))
    # Sanity: these two characters are genuinely different (real armor
    # values diverge), so a shared signature really would be a wrong-value
    # collision, not a coincidental non-issue.
    assert with_talents.total_armor() != no_talents.total_armor()
    assert _char_marginals_signature(with_talents) != _char_marginals_signature(no_talents)


def test_decoded_talents_changes_real_total_armor_and_max_hp():
    """Sanity check that `decoded_talents` (the real-build decode path,
    2026-07-13) is genuinely sim-affecting: it's what `_talent_set()`
    consumes when set, feeding both `total_armor()` (armor-multiplier
    talents) and `max_hp()` (indomitable)."""
    no_talents = _base_warrior(decoded_talents=frozenset())
    with_talents = _base_warrior(
        decoded_talents=frozenset({"reinforced_plates", "armor_specialization", "indomitable"})
    )
    assert with_talents.total_armor() > no_talents.total_armor()
    assert with_talents.max_hp() > no_talents.max_hp()


def test_decoded_talents_collision_is_caught_by_the_shared_signature():
    """A same-vintage instance of the #275/2026-07-10 bug class, caught
    BEFORE merge this time (validator audit, 2026-07-13): `decoded_talents`
    was wired into `Character`/`_talent_set()` but initially left out of
    `SIM_AFFECTING_CHARACTER_FIELDS` — two Prot Warriors identical in every
    OTHER field but differing in `decoded_talents` (e.g. one loaded from a
    combat log, one from a `/simc` paste matched to a different preset)
    would have silently shared one cached marginals entry / reproduction
    hash despite genuinely different armor and max HP."""
    no_talents = _base_warrior(decoded_talents=frozenset())
    with_talents = _base_warrior(
        decoded_talents=frozenset({"reinforced_plates", "armor_specialization", "indomitable"})
    )
    assert with_talents.total_armor() != no_talents.total_armor()
    assert _char_marginals_signature(with_talents) != _char_marginals_signature(no_talents)
