"""Guardian agility → dodge wiring tests.

Closes the latent gap surfaced 2026-05-23 by the AnonGuardian3 smoke
test: ``Character`` had no ``agility`` field, so the YAML constant
``agility_per_dodge_pct: 1000.0`` was dead code (``grep -rn``
confirmed zero reads outside the YAML) and Guardian dodge fell back
to the flat ``base_dodge_chance: 0.05`` baseline regardless of gear.

This file pins the post-fix behaviour: the field exists, the SimC
importer threads ``gear_agility`` into the character config, and
``base_dodge()`` actually consumes the value for Guardian (only).
Other tank specs are unchanged.
"""

from __future__ import annotations

import pytest

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io.simc_import import _GEAR_STAT_KEYS, parse_simc_string, simc_to_character_yaml


def _guardian(agility: int = 0, mastery_rating: int = 0) -> Character:
    return Character(
        name="t",
        race="night_elf",
        class_spec="guardian_druid",
        talents="anonguardian2-guardian",
        strength=0,
        agility=agility,
        stamina=80000,
        armor_from_gear=5000,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=mastery_rating,
        versatility_rating=0,
        max_hp_override=8_000_000,
    )


def test_character_dataclass_has_agility_field() -> None:
    """``Character.__init__`` must accept ``agility=`` as a kw arg —
    plate-tank fixtures didn't pass it before this PR, so the default
    of 0 keeps every pre-existing Character construction working."""
    char = Character(
        name="t",
        race="human",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=5000,
    )
    assert char.agility == 0  # default
    # Explicit assignment also works.
    char_with_agi = _guardian(agility=2500)
    assert char_with_agi.agility == 2500


def test_guardian_base_dodge_adds_agility_contribution_on_top_of_flat_baseline() -> None:
    """Pre-this-PR Guardian dodge was always ``base_dodge_chance`` (0.05).
    Post-this-PR the agility-scaled portion lands on top.

    1000 agility / 1000 agility_per_dodge_pct / 100 = 0.01 (1pp).
    So a Guardian with 2500 agility dodges +2.5pp over the flat
    baseline; well below any cap, so no clamp logic to worry about."""
    c = load_constants()
    spec_cfg = c["specs"]["guardian_druid"]
    base = _guardian(agility=0).base_dodge()
    plus_2500 = _guardian(agility=2500).base_dodge()

    assert base == pytest.approx(spec_cfg["base_dodge_chance"], abs=1e-12)
    expected_bonus = 2500 / spec_cfg["agility_per_dodge_pct"] / 100  # = 0.025
    assert plus_2500 == pytest.approx(spec_cfg["base_dodge_chance"] + expected_bonus, abs=1e-12)
    # Sanity: a clear delta — pre-PR this would have been 0.0.
    assert (plus_2500 - base) == pytest.approx(0.025, abs=1e-12)


def test_guardian_base_dodge_scales_linearly_within_normal_stack_range() -> None:
    """At every realistic Midnight 12.0.5 stack (0 → 5000 agility), the
    agility contribution scales 1:1 with the YAML constant. No DR curve
    on this in SimC's player_t::composite_dodge for the relevant range."""
    c = load_constants()
    base = _guardian(agility=0).base_dodge()
    for agility in (500, 1000, 2000, 3500, 5000):
        delta = _guardian(agility=agility).base_dodge() - base
        expected = agility / c["specs"]["guardian_druid"]["agility_per_dodge_pct"] / 100
        assert delta == pytest.approx(expected, abs=1e-12), (
            f"agility={agility} produced delta {delta}, expected {expected}"
        )


def test_non_guardian_tank_specs_ignore_agility() -> None:
    """The wiring is strictly Guardian-scoped. Setting ``agility`` on a
    Prot Warrior or Prot Pal must NOT change their dodge — plate tanks
    don't get agility from gear and the YAML doesn't define a per-spec
    DR formula for them."""
    for spec in ("protection_warrior", "protection_paladin", "blood_death_knight"):
        char_no_agi = Character(
            name="t",
            race="human",
            class_spec=spec,
            talents="brutoh-actual" if spec == "protection_warrior" else "default-paladin",
            strength=2000,
            stamina=50000,
            armor_from_gear=5000,
        )
        char_with_agi = Character(
            name="t",
            race="human",
            class_spec=spec,
            talents="brutoh-actual" if spec == "protection_warrior" else "default-paladin",
            strength=2000,
            agility=2500,
            stamina=50000,
            armor_from_gear=5000,
        )
        assert char_no_agi.base_dodge() == char_with_agi.base_dodge(), (
            f"{spec} dodge changed with agility — wiring leaked outside Guardian"
        )


def test_vdh_dodge_unchanged_for_now() -> None:
    """VDH uses agility too in-game but the YAML only models a flat
    ``base_dodge_chance: 0.08`` baseline for them today. Pin the
    current behaviour — when VDH gets its own ``agility_per_dodge_pct``
    entry, this test will fail loudly and force a coupled update."""
    c = load_constants()
    char = Character(
        name="t",
        race="blood_elf",
        class_spec="vengeance_demon_hunter",
        talents="default-dh",
        strength=0,
        agility=2500,
        stamina=80000,
        armor_from_gear=5000,
    )
    assert char.base_dodge() == c["specs"]["vengeance_demon_hunter"]["base_dodge_chance"]


# ─── SimC import wires gear_agility → Character.agility ──────────────────────


def test_gear_stat_keys_table_maps_gear_agility_to_agility() -> None:
    """The ``_GEAR_STAT_KEYS`` table is the SimC-line → Character-field
    map used by every gear-stats parse. Pre-this-PR it had no
    ``gear_agility`` entry, so Guardian profiles with a ``gear_agility``
    line silently dropped it on the floor."""
    assert _GEAR_STAT_KEYS.get("gear_agility") == "agility"


def test_parse_simc_picks_up_gear_agility() -> None:
    """Round-trip: a SimC export with ``gear_agility=`` populates
    ``SimcImport.gear_stats['agility']`` and the auto-generated
    Character YAML includes the value."""
    text = """druid="AnonGuardian3"
spec=guardian
gear_agility=2500
gear_stamina=80000
gear_armor=5200
"""
    sim = parse_simc_string(text)
    assert sim.gear_stats.get("agility") == 2500
    yaml_dict = simc_to_character_yaml(sim, stats={"agility": sim.gear_stats["agility"]})
    assert yaml_dict["agility"] == 2500


def test_simc_to_character_yaml_includes_zero_agility_default() -> None:
    """When ``stats`` is not passed, the fallback YAML must include
    ``agility: 0`` so plate-tank imports stay backward-compatible
    (a Prot Warrior import without gear_* lines built a char with the
    Character defaults; now agility=0 is explicit)."""
    text = """warrior="Brutoh"
spec=protection
"""
    sim = parse_simc_string(text)
    yaml_dict = simc_to_character_yaml(sim)  # no stats passed
    assert yaml_dict["agility"] == 0


# ── Bear Form +40% max HP (2026-06-26) ────────────────────────────────────────


def _bear(
    stamina: int = 23_524,
    race: str = "tauren",
    max_hp_override=None,
    stamina_in_caster_form: bool = True,
) -> Character:
    """A Guardian with NO max_hp_override, so max_hp() exercises the
    stamina-derived path. ``stamina_in_caster_form`` defaults True (the SimC-paste
    case → caster stamina, Bear Form ×1.40 applies). Default stamina ≈ AnonGuardian1's
    caster total."""
    return Character(
        name="t",
        race=race,
        class_spec="guardian_druid",
        talents="anonguardian2-guardian",
        strength=0,
        agility=1900,
        stamina=stamina,
        armor_from_gear=919,
        max_hp_override=max_hp_override,
        stamina_in_caster_form=stamina_in_caster_form,
    )


def test_guardian_bear_form_applies_40pct_hp_when_caster_stamina():
    """Caster stamina (stamina_in_caster_form=True, the SimC-paste case) gets the
    Bear Form ×1.40."""
    c = load_constants()
    hp_per_stam = c["stat_conversion"]["hp_per_stamina"]
    bear = c["specs"]["guardian_druid"]["bear_form_stamina_multiplier"]
    g = _bear(stamina=23_524, race="night_elf")  # no tauren racial, isolate Bear Form
    assert g.max_hp() == pytest.approx(23_524 * hp_per_stam * bear)


def test_guardian_no_bear_form_when_stamina_already_in_form():
    """The log/WCL hydrate case: stamina is the in-form COMBATANT_INFO value, so
    NO ×1.40 — stamina × hp_per_stam is already the bear HP (no double-count)."""
    c = load_constants()
    hp_per_stam = c["stat_conversion"]["hp_per_stamina"]
    g = _bear(stamina=32_934, race="night_elf", stamina_in_caster_form=False)
    assert g.max_hp() == pytest.approx(32_934 * hp_per_stam)  # no ×1.40


def test_guardian_bear_form_stacks_with_tauren_racial():
    """AnonGuardian1 is a Tauren: caster stamina × hp_per_stam × Bear(1.40) × Tauren(1.05)
    should land near their in-game bear HP (759,384)."""
    g = _bear(stamina=23_524, race="tauren")
    assert g.max_hp() == pytest.approx(759_384, rel=0.02)


def test_guardian_max_hp_override_not_bear_multiplied():
    """A max_hp_override is the in-form sheet value already — Bear Form must NOT
    be applied on top of it even with the caster-stamina flag set."""
    g = _bear(max_hp_override=759_384, race="night_elf")
    assert g.max_hp() == pytest.approx(759_384)


def test_non_guardian_no_bear_form_multiplier():
    """A plate tank with identical stamina gets NO Bear Form multiplier — max HP
    stays near stamina×hp_per_stam (modulo small talent mults like Indomitable
    +4%), well below the Guardian's ×1.40."""
    c = load_constants()
    hp_per_stam = c["stat_conversion"]["hp_per_stamina"]
    base = 23_524 * hp_per_stam
    w = Character(
        name="t",
        race="human",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=23_524,
        armor_from_gear=5000,
    )
    assert base <= w.max_hp() < base * 1.10  # talent mults only, never ×1.40


def test_simc_to_character_yaml_sets_caster_form_flag_for_guardian():
    """SimC gear stamina is the caster value → Guardian import opts into Bear
    Form ×1.40 via stamina_in_caster_form; a plate tank does not."""
    guardian = parse_simc_string('druid="R"\nspec=guardian\nrace=tauren\n')
    gy = simc_to_character_yaml(guardian, stats={"stamina": 17180})
    assert gy["stamina_in_caster_form"] is True

    warrior = parse_simc_string('warrior="B"\nspec=protection\n')
    wy = simc_to_character_yaml(warrior, stats={"stamina": 30000})
    assert wy.get("stamina_in_caster_form", False) is False
