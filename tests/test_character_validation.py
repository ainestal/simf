"""The Character validation gate — one source of truth for "did this
character's stats resolve?" plus a hard non-negative invariant at construction.

Regression guard for the phantom-eHP bug (#231): a public online name-lookup
forced the item DB offline, so per-item stats came back empty and stamina was
backfilled to 0 — yielding a ~21k phantom eHP pool and a bogus +195% ΔeHP the
UI showed as if real. The detector that drives the honest banner now lives on
Character (is_degraded / validation_issues), so every surface and every future
import path shares the same definition instead of re-deriving it. These tests
pin that contract.
"""

from __future__ import annotations

import pytest

from simf.core.character import Character


def _degraded_warrior(**overrides) -> Character:
    """A warrior whose gear stats failed to resolve — mirrors the raider_io.py
    offline-backfill path (stamina/primary defaulted to 0)."""
    base = dict(
        name="Degraded",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        stamina=0,
        armor_from_gear=0,
        strength=0,
    )
    base.update(overrides)
    return Character(**base)


# --- the happy path: real characters are never flagged ----------------------


def test_valid_character_has_no_issues(brutoh: Character) -> None:
    assert brutoh.validation_issues() == []
    assert brutoh.is_degraded() is False


def test_agility_tank_zero_strength_is_not_a_false_positive() -> None:
    """Guardian / Brewmaster / VDH legitimately have strength 0 (Agi primary).
    The no-primary check must only fire when BOTH primaries are absent."""
    guardian = Character(
        name="Bear",
        race="tauren",
        class_spec="guardian_druid",
        talents="default-guardian",
        stamina=30_000,
        armor_from_gear=5_000,
        agility=2_000,  # primary present
        strength=0,  # legit zero for an Agi spec
    )
    assert guardian.validation_issues() == []
    assert guardian.is_degraded() is False


# --- degraded hydrate detection (the #231 scenario) -------------------------


def test_zero_stamina_is_flagged_degraded() -> None:
    """The exact phantom-eHP case: stamina backfilled to 0."""
    char = _degraded_warrior()
    assert char.is_degraded() is True
    assert any("stamina" in issue for issue in char.validation_issues())
    # Why it matters: every eHP-derived number is nonsense — exactly what the UI
    # must NOT present as real. (No div-by-zero: the denominator is mitigation.)
    assert char.max_hp() == 0
    assert char.effective_hp_physical() == 0
    assert char.effective_hp_magic() == 0


def test_no_primary_stat_is_flagged() -> None:
    """Stamina resolved but both primaries 0 → gear stats still didn't load."""
    char = _degraded_warrior(stamina=30_000, armor_from_gear=5_000)
    assert char.is_degraded() is True
    issues = char.validation_issues()
    assert any("primary" in issue for issue in issues)
    assert not any("stamina" in issue for issue in issues)  # stamina is fine here


def test_degraded_construction_does_not_raise() -> None:
    """A 0-backfilled (degraded) hydrate must still CONSTRUCT — a degraded
    surface beats a crashed one. Detection is via is_degraded(), not an
    exception; only negatives raise."""
    char = _degraded_warrior()  # stamina=0, strength=0 — must not raise
    assert isinstance(char, Character)


# --- the hard non-negative invariant ----------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "stamina",
        "armor_from_gear",
        "strength",
        "agility",
        "haste_rating",
        "crit_rating",
        "mastery_rating",
        "versatility_rating",
        "parry_rating",
        "shield_armor",
    ],
)
def test_negative_stat_raises_at_construction(field: str) -> None:
    base = dict(
        name="Bad",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        stamina=30_000,
        armor_from_gear=5_000,
        strength=2_000,
    )
    base[field] = -1
    with pytest.raises(ValueError, match=field):
        Character(**base)


def test_from_dict_enforces_the_invariant() -> None:
    """Hydrate paths construct via from_dict — the raise must fire there too,
    and extra payload keys (region/server) are filtered, not fatal."""
    data = dict(
        name="Bad",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        stamina=-5,
        armor_from_gear=5_000,
        strength=2_000,
        region="eu",  # extra hydrate-payload key; from_dict drops it
    )
    with pytest.raises(ValueError, match="stamina"):
        Character.from_dict(data)
