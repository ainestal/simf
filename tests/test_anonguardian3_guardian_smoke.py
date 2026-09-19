"""AnonGuardian3 Guardian Druid smoke test.

AnonGuardian3 is the first non-Brutoh tank dropped into ``examples/`` —
a Guardian Druid (EU/AnonRealm3). Per ``constants.yaml`` the
``guardian_druid`` spec is ``calibrated: false`` and uses the warrior K
as a placeholder; we still have no AnonGuardian3 combat logs in tree to
run an actual K sweep against.

This file is the no-input smoke layer that DOES land today: confirm
the SimC import path round-trips AnonGuardian3's profile into a
Guardian-shaped character config, and confirm the Guardian mitigation
code paths exercised by the runner produce non-degenerate output.

Catches the ImportError-grade bug class — a typo or import drift that
silently routes AnonGuardian3 to the Warrior policy and pretends it
worked. When logs eventually arrive, calibration starts from a known-
good import, not a broken one.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from simf.classes.guardian_druid import GuardianPolicy, apply_guardian_mitigation
from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState, apply_mitigation
from simf.core.policy import make_policy
from simf.core.profiles import HealingProfile
from simf.core.runner import run_simulation
from simf.io.simc_import import load_simc_file, simc_to_character_yaml

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples"
ANONGUARDIAN3_SIMC = EXAMPLE_DIR / "AnonGuardian3.simc"


def _phys(amount: float, time_s: float = 0.0) -> DamageEvent:
    return DamageEvent(
        time_s=time_s,
        source_id="smoke",
        school="physical",
        raw_amount=amount,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=False,
    )


def _magic(amount: float, time_s: float = 0.0) -> DamageEvent:
    return DamageEvent(
        time_s=time_s,
        source_id="smoke",
        school="magic",
        raw_amount=amount,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )


def _anonguardian3_inspired_char() -> Character:
    """Guardian Druid sized in the Midnight 12.0.5 +14-18 M+ range.

    Stats picked to land roughly where a tank wearing AnonGuardian3's
    283-289 ilvl mix sits — not a calibration fit, just a sane
    non-degenerate body for the smoke test. ``agility=2500`` is now
    consumed by ``character.base_dodge()`` for Guardian (closed the
    latent gap surfaced when this fixture first landed 2026-05-23;
    pre-this-PR ``agility_per_dodge_pct`` was dead code).
    """
    return Character(
        name="anonguardian3_smoke",
        race="night_elf",
        class_spec="guardian_druid",
        talents="anonguardian2-guardian",
        strength=0,
        agility=2500,
        stamina=80000,
        armor_from_gear=5200,
        haste_rating=2000,
        crit_rating=1500,
        mastery_rating=1800,
        versatility_rating=1200,
        max_hp_override=8_500_000,
    )


def test_anonguardian3_simc_parses_as_guardian_druid():
    """The raw SimC export round-trips into a Guardian Druid profile."""
    sim = load_simc_file(ANONGUARDIAN3_SIMC)
    assert sim.name == "AnonGuardian3"
    assert sim.class_name == "druid"
    assert sim.spec == "guardian"
    assert sim.role == "tank"
    assert sim.region == "eu"
    assert sim.server == "anonrealm3"
    # Active talent loadout present; commented loadouts captured separately.
    assert sim.talents.startswith("CgGA8cL7tpvige+kkmGM9zUPWDAAAAAAAAAAA")
    assert {"raid", "clawbear", "lunarbear", "clawbear2"} <= set(sim.saved_loadouts)


def test_anonguardian3_gear_is_parsed_with_names():
    """Spot-check that the M+ gear from the equipped list comes through with ilvl."""
    sim = load_simc_file(ANONGUARDIAN3_SIMC)
    head = sim.items["head"]
    assert head.name == "Branches of the Luminous Bloom"
    assert head.ilvl == 289
    trinket1 = sim.items["trinket1"]
    assert trinket1.name == "Emberwing Feather"
    assert trinket1.ilvl == 298
    main_hand = sim.items["main_hand"]
    assert main_hand.name == "Sunlance of Viryx"
    assert main_hand.ilvl == 298


def test_anonguardian3_to_character_yaml_picks_guardian_spec_and_loadout():
    """Auto-pick should set class_spec=guardian_druid and the Guardian loadout.

    Regression: the import default previously slapped warrior talents
    onto every spec — engaged_tank Phase 4 audit (2026-05-16). For
    AnonGuardian3 this would silently route them to Warrior mitigation
    and hide the entire Guardian code path.
    """
    sim = load_simc_file(ANONGUARDIAN3_SIMC)
    yaml_dict = simc_to_character_yaml(sim)
    assert yaml_dict["class_spec"] == "guardian_druid"
    assert yaml_dict["talents"] == "anonguardian2-guardian"
    assert yaml_dict["race"] == "human"  # night_elf isn't in SIMC_RACE_MAP — defaults
    assert yaml_dict["server"] == "anonrealm3"
    assert yaml_dict["region"] == "eu"


def test_make_policy_for_anonguardian3_returns_guardian_policy():
    """The runner's dispatch must build a GuardianPolicy, not the Warrior fallback."""
    char = _anonguardian3_inspired_char()
    policy = make_policy(char)
    assert isinstance(policy, GuardianPolicy), (
        f"AnonGuardian3 routed to {type(policy).__name__}, expected GuardianPolicy"
    )


def test_apply_mitigation_dispatches_to_guardian_for_anonguardian3():
    """Both physical and magic incoming routes through the Guardian handler.

    A silent fallthrough to ``apply_warrior_mitigation`` would mean the
    Guardian baseline DR (Barkskin averaged + Bear Form/Thick Hide +
    Ursol's Warding magic DR) never applies — visible as a phys DR much
    closer to armor-only, and a magic DR with no Barkskin floor. Asserting
    non-degenerate output on both schools catches the routing.
    """
    char = _anonguardian3_inspired_char()
    state = MitigationState(char)
    state.talents = set()

    phys_result = apply_mitigation(state, _phys(1_000_000), random.Random(0))
    assert 0 < phys_result["dealt"] < 1_000_000

    magic_result = apply_mitigation(state, _magic(1_000_000), random.Random(0))
    assert 0 < magic_result["dealt"] < 1_000_000

    # Magic events skip the armor curve, so they hit harder than physical
    # for the same raw amount on a Guardian (no shield, no block).
    assert magic_result["dealt"] > phys_result["dealt"]


def test_guardian_baseline_dr_matches_spec_module_not_warrior():
    """Direct call to ``apply_guardian_mitigation`` confirms baseline DR layering.

    A magic hit with no CDs up should still get versatility + Ursol's Warding
    magic DR + Barkskin averaged DR applied. If the routing slipped to warrior,
    none of these would be present.
    """
    c = load_constants()
    spec_cfg = c["specs"]["guardian_druid"]
    char = _anonguardian3_inspired_char()
    state = MitigationState(char)
    state.talents = set()

    raw = 1_000_000
    result = apply_guardian_mitigation(state, _magic(raw), random.Random(0))

    vers = char.versatility_dr()
    # Ursol's Warding (2026-06-29): magic DR = 10% of the tank's armor DR. Generic
    # "magic" school → no Bear Form arcane layer (that's arcane-only). No Incarnation
    # up on a fresh state, so armor DR is the plain Ironfur-inclusive curve.
    K = c["armor"]["k_constant"]
    armor_dr = min(char.total_armor() / (char.total_armor() + K), c["armor"]["max_armor_dr"])
    ursols = spec_cfg["ursols_warding_magic_dr_pct_of_armor"] * armor_dr
    expected_floor = (
        raw
        * (1 - vers)
        * char._always_on_dr()  # Thick Hide × Bear Form passive baseline DR (2026-06-24)
        * (1 - ursols)  # Ursol's Warding magic DR (2026-06-29)
        * (1 - spec_cfg["barkskin_avg_dr"])
    )
    # Result should be very close to this product — no healer absorb on
    # the fresh state, no emergency CD up.
    assert result["dealt"] < raw * (1 - vers)  # at least barkskin + rots stacked on top of vers
    assert abs(result["dealt"] - expected_floor) < 1.0


def test_guardian_magic_dr_arcane_gets_extra_layer_physical_untouched():
    """2026-06-29 magic-mit fix: Ursol's Warding (all non-physical, 10% of armor
    DR) + Bear Form -6% arcane (arcane-only). Validated against 11 WCL logs.

    - arcane takes the extra -6% on top of generic magic → strictly less dealt;
    - physical and bleeds (armor-bypassing physical) get NEITHER magic layer.
    """
    c = load_constants()
    spec_cfg = c["specs"]["guardian_druid"]
    char = _anonguardian3_inspired_char()
    raw = 1_000_000

    def dealt(school: str, is_bleed: bool = False) -> float:
        st = MitigationState(char)
        st.talents = set()
        ev = DamageEvent(
            time_s=0.0,
            source_id="t",
            school=school,
            raw_amount=raw,
            attack_type="spell",
            is_avoidable=False,
            is_blockable=False,
            is_bleed=is_bleed,
        )
        return apply_guardian_mitigation(st, ev, random.Random(0))["dealt"]

    magic = dealt("shadow")
    arcane = dealt("arcane")
    # arcane = generic magic × (1 - bear_form_arcane_dr), exactly.
    assert arcane == pytest.approx(magic * (1 - spec_cfg["bear_form_arcane_dr"]), rel=1e-9)
    assert arcane < magic

    # Physical path must be untouched by the magic layers. Compare a physical hit
    # to the same hit with the magic block hypothetically removed: the physical
    # dealt must equal the armor+vers+baseline+CD chain with NO Ursol's/arcane.
    K = c["armor"]["k_constant"]
    armor_dr = min(char.total_armor() / (char.total_armor() + K), c["armor"]["max_armor_dr"])
    phys = dealt("physical")
    expected_phys = (
        raw
        * (1 - armor_dr)
        * (1 - char.versatility_dr())
        * char._always_on_dr()
        * (1 - spec_cfg["barkskin_avg_dr"])
    )
    assert phys == pytest.approx(expected_phys, abs=1.0)

    # A bleed (physical school, armor-bypassing) gets no armor AND no magic DR.
    bleed = dealt("physical", is_bleed=True)
    expected_bleed = (
        raw * (1 - char.versatility_dr()) * char._always_on_dr() * (1 - spec_cfg["barkskin_avg_dr"])
    )
    assert bleed == pytest.approx(expected_bleed, abs=1.0)


def test_guardian_synthetic_sim_runs_end_to_end():
    """Small synthetic Monte Carlo over Guardian mitigation produces sane output.

    This is the trip-wire: any regression that breaks the Guardian
    path inside the runner (policy dispatch, mitigation routing, tick
    cadence, Incarnation slot) shows up here as either an exception
    or an obviously broken death-rate / dtps number.
    """
    char = _anonguardian3_inspired_char()
    events = [_phys(120_000, time_s=t) for t in range(0, 60, 2)] + [
        _magic(180_000, time_s=t + 1) for t in range(0, 60, 4)
    ]
    events.sort(key=lambda e: e.time_s)
    healing = HealingProfile(profile="smoke", baseline_hps_pct_of_dtps=0.40, baseline_hps_abs=0.0)

    result = run_simulation(
        char,
        damage_profile=None,
        healing_profile=healing,
        events_override=events,
        iterations=3,
        seed=42,
    )

    assert result.iterations == 3
    assert 0.0 <= result.death_rate <= 1.0
    assert result.mean_dtps > 0.0
    assert result.constants_version >= 17
