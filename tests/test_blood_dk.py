"""Tests for the Blood Death Knight mitigation chain — Phase 4.1.

The spec module dispatches when `Character.class_spec == "blood_death_knight"`.
Tests stay synthetic (no log replay) so the unit cases run fast and don't
require Blood DK calibration logs (which are pending — `calibrated: false`)."""

from __future__ import annotations

import random

import pytest

from simf.core.character import Character
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState, apply_mitigation


def _make_dk(strength: int = 2000, stamina: int = 50000) -> Character:
    """Construct a Blood DK with realistic stats. Armor sized so post-armor
    physical DR is ~60% — matches a current-tier DK on Brutoh-class gear."""
    return Character(
        name="TestDK",
        race="orc",
        class_spec="blood_death_knight",
        talents="brutoh-actual",  # any loadout; talent set not used by DK code
        strength=strength,
        stamina=stamina,
        armor_from_gear=4000,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=0,
        versatility_rating=0,
        parry_rating=0,
    )


def _physical_hit(t: float, amount: int) -> DamageEvent:
    return DamageEvent(
        time_s=t,
        source_id="trash_mob",
        raw_amount=amount,
        school="physical",
        is_avoidable=True,
        is_blockable=False,
        attack_type="melee",
    )


def _magic_hit(t: float, amount: int) -> DamageEvent:
    return DamageEvent(
        time_s=t,
        source_id="caster",
        raw_amount=amount,
        school="shadow",
        is_avoidable=False,
        is_blockable=False,
        attack_type="spell",
    )


def test_dk_dispatches_to_blood_dk_mitigation():
    """`apply_mitigation` routes class_spec == 'blood_death_knight' to the
    new spec module, not the default warrior path."""
    char = _make_dk()
    state = MitigationState(char)
    rng = random.Random(0)
    out = apply_mitigation(state, _physical_hit(0.0, 100_000), rng)
    # Default warrior chain would set was_blocked for blockable phys hits;
    # DK chain leaves it False (no shield).
    assert "dealt" in out
    assert not out["was_blocked"]


def test_dk_bone_shield_adds_strength_armor_synthetic():
    """Bone Shield = bonus armor of 180% of Strength while >=1 charge (SimC
    midnight composite_bonus_armor; Wowhead 195181). The synthetic sim
    treats it as always-on (measured 99.6-100% damage-weighted uptime), so
    a physical hit runs the armor curve at gear armor + 1.8 x strength."""
    from simf.core.constants import load_constants

    char = _make_dk(strength=2000)
    state = MitigationState(char)
    rng = random.Random(42)
    out = apply_mitigation(state, _physical_hit(0.0, 100_000), rng)
    if out["was_avoided"]:
        return  # the dodge/parry roll fired; rerun-noise, ignore
    c = load_constants()
    spec = c["specs"]["blood_death_knight"]
    armor = 4000 + spec["bone_shield_armor_from_strength"] * 2000
    k = c["armor"]["k_constant"]
    expected_dr = min(armor / (armor + k), c["armor"]["max_armor_dr"])
    assert out["dealt"] == pytest.approx(100_000 * (1 - expected_dr), rel=0.01)


def test_dk_bone_shield_replay_gated_on_buff_window():
    """In replay mode the Bone Shield armor is credited only while the
    195181 buff window covers the event (event.active_buffs) — a gap in
    the log's windows means base armor only, no phantom credit."""
    from simf.core.constants import load_constants

    char = _make_dk(strength=2000)
    c = load_constants()
    spec = c["specs"]["blood_death_knight"]
    k = c["armor"]["k_constant"]

    def replay_hit(active: frozenset[int]) -> DamageEvent:
        return DamageEvent(
            time_s=0.0,
            source_id="trash_mob",
            raw_amount=100_000,
            school="physical",
            is_avoidable=False,  # replay events are never avoidable
            is_blockable=False,
            attack_type="melee",
            is_log_replay=True,
            active_buffs=active,
        )

    out_up = apply_mitigation(
        MitigationState(char),
        replay_hit(frozenset({spec["bone_shield_spell_id"]})),
        random.Random(0),
    )
    out_down = apply_mitigation(MitigationState(char), replay_hit(frozenset()), random.Random(0))

    armor_up = 4000 + spec["bone_shield_armor_from_strength"] * 2000
    dr_up = min(armor_up / (armor_up + k), c["armor"]["max_armor_dr"])
    dr_down = min(4000 / (4000 + k), c["armor"]["max_armor_dr"])
    assert out_up["dealt"] == pytest.approx(100_000 * (1 - dr_up), rel=0.01)
    assert out_down["dealt"] == pytest.approx(100_000 * (1 - dr_down), rel=0.01)
    assert out_up["dealt"] < out_down["dealt"]


def test_dk_bone_shield_does_not_apply_to_magic_damage():
    """Bone Shield is armor — magic hits should not benefit."""
    char = _make_dk()
    state = MitigationState(char)
    rng = random.Random(0)

    # No bone shield path for magic — only vers applies (0 vers here)
    out_magic = apply_mitigation(state, _magic_hit(0.0, 100_000), rng)
    # Versatility 0 → no DR; dealt should be near raw
    assert out_magic["dealt"] == pytest.approx(100_000, rel=0.01)


def test_dk_rune_carved_plates_replay_per_school_buff_gated():
    """Rune Carved Plates (Deathbringer): -1.5%/stack x measured avg stacks,
    physical buff (440289) on physical events, magical buff (440290) on
    magic events, replay-only and gated on the buff being in
    event.active_buffs. No buff in the log (San'layn) -> no credit."""
    from simf.core.constants import load_constants

    char = _make_dk(strength=2000)
    c = load_constants()
    spec = c["specs"]["blood_death_knight"]
    rcp_mult = 1 - (
        spec["rune_carved_plates_dr_per_stack"] * spec["rune_carved_plates_avg_stacks_while_up"]
    )

    def replay_hit(school: str, active: frozenset[int]) -> DamageEvent:
        return DamageEvent(
            time_s=0.0,
            source_id="mob",
            raw_amount=100_000,
            school=school,
            is_avoidable=False,
            is_blockable=False,
            attack_type="melee" if school == "physical" else "spell",
            is_log_replay=True,
            active_buffs=active,
        )

    phys_id = spec["rune_carved_plates_physical_spell_id"]
    mag_id = spec["rune_carved_plates_magical_spell_id"]

    # Magic: with vs without the magical buff — exact RCP ratio (no armor).
    out_mag_up = apply_mitigation(
        MitigationState(char), replay_hit("shadow", frozenset({mag_id})), random.Random(0)
    )
    out_mag_down = apply_mitigation(
        MitigationState(char), replay_hit("shadow", frozenset()), random.Random(0)
    )
    assert out_mag_up["dealt"] == pytest.approx(out_mag_down["dealt"] * rcp_mult, rel=0.001)

    # The physical buff does NOT credit magic events.
    out_mag_wrong = apply_mitigation(
        MitigationState(char), replay_hit("shadow", frozenset({phys_id})), random.Random(0)
    )
    assert out_mag_wrong["dealt"] == pytest.approx(out_mag_down["dealt"], rel=0.001)

    # Physical: with vs without the physical buff (same active Bone Shield
    # so the armor term cancels) — exact RCP ratio.
    bs = spec["bone_shield_spell_id"]
    out_phys_up = apply_mitigation(
        MitigationState(char), replay_hit("physical", frozenset({bs, phys_id})), random.Random(0)
    )
    out_phys_down = apply_mitigation(
        MitigationState(char), replay_hit("physical", frozenset({bs})), random.Random(0)
    )
    assert out_phys_up["dealt"] == pytest.approx(out_phys_down["dealt"] * rcp_mult, rel=0.001)


def test_dk_rune_carved_plates_not_applied_synthetic():
    """RCP is a replay-only, buff-gated layer (the synthetic path has no
    active_buffs signal and must not invent hero-talent credit)."""
    char = _make_dk()
    out = apply_mitigation(MitigationState(char), _magic_hit(0.0, 100_000), random.Random(0))
    # Synthetic magic hit with 0 vers: nothing should reduce it.
    assert out["dealt"] == pytest.approx(100_000, rel=0.01)


def test_dk_blood_soaked_ground_replay_buff_gated():
    """Blood-Soaked Ground (San'layn): flat -5% physical damage taken while
    the buff (434034) is in event.active_buffs, replay-only. No buff in
    the log (Deathbringer) -> no credit — the symmetric case to RCP."""
    from simf.core.constants import load_constants

    char = _make_dk(strength=2000)
    c = load_constants()
    spec = c["specs"]["blood_death_knight"]
    bsg_mult = 1 - spec["blood_soaked_ground_physical_dr"]
    bsg_id = spec["blood_soaked_ground_spell_id"]

    def replay_hit(school: str, active: frozenset[int]) -> DamageEvent:
        return DamageEvent(
            time_s=0.0,
            source_id="mob",
            raw_amount=100_000,
            school=school,
            is_avoidable=False,
            is_blockable=False,
            attack_type="melee" if school == "physical" else "spell",
            is_log_replay=True,
            active_buffs=active,
        )

    # Physical: with vs without the buff — exact BSG ratio (no armor
    # variation, Bone Shield absent in both so it cancels).
    out_up = apply_mitigation(
        MitigationState(char), replay_hit("physical", frozenset({bsg_id})), random.Random(0)
    )
    out_down = apply_mitigation(
        MitigationState(char), replay_hit("physical", frozenset()), random.Random(0)
    )
    assert out_up["dealt"] == pytest.approx(out_down["dealt"] * bsg_mult, rel=0.001)

    # The buff does NOT credit magic events (it's physical-only, no
    # magical branch — unlike RCP).
    out_mag_up = apply_mitigation(
        MitigationState(char), replay_hit("shadow", frozenset({bsg_id})), random.Random(0)
    )
    out_mag_down = apply_mitigation(
        MitigationState(char), replay_hit("shadow", frozenset()), random.Random(0)
    )
    assert out_mag_up["dealt"] == pytest.approx(out_mag_down["dealt"], rel=0.001)


def test_dk_blood_soaked_ground_not_applied_synthetic():
    """Blood-Soaked Ground is a replay-only, buff-gated layer (the
    synthetic path has no active_buffs signal and must not invent
    hero-talent credit) — matches RCP's own deliberate convention."""
    from simf.core.constants import load_constants

    char = _make_dk(strength=2000)
    state = MitigationState(char)
    rng = random.Random(3)
    out = apply_mitigation(state, _physical_hit(0.0, 100_000), rng)
    if out["was_avoided"]:
        return  # dodge/parry roll fired; rerun-noise, not what we're testing

    c = load_constants()
    spec = c["specs"]["blood_death_knight"]
    k = c["armor"]["k_constant"]
    # Synthetic mode: Bone Shield's armor bonus is always-on (measured
    # near-100% uptime), but Blood-Soaked Ground gets NO credit outside
    # replay — expected dealt is armor+vers only, no extra BSG multiplier.
    # If BSG leaked into synthetic mode, dealt would be ~5% lower than this.
    armor = 4000 + spec["bone_shield_armor_from_strength"] * 2000
    dr = min(armor / (armor + k), c["armor"]["max_armor_dr"])
    assert out["dealt"] == pytest.approx(100_000 * (1 - dr), rel=0.01)


def test_dk_base_parry_is_higher_than_warrior_no_shield():
    """DKs have higher base parry (no shield → trade for parry)."""
    dk = _make_dk(strength=2000)
    warrior = Character(
        name="TestWar",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    # DK base parry config is 0.05; warrior default base is 0.03 →
    # adding strength contribution to both, DK should still be higher.
    assert dk.base_parry() > warrior.base_parry()


def test_dk_icebound_fortitude_30pct_dr_when_active():
    """When `state.shield_wall_until > now`, Icebound Fortitude's 30% DR
    multiplies through the mitigation chain (reuses the SW slot)."""
    char = _make_dk()
    state = MitigationState(char)
    rng = random.Random(7)
    no_cd = apply_mitigation(state, _physical_hit(0.0, 100_000), rng)
    if no_cd["was_avoided"]:
        # The avoidance roll fired on the first hit — retry with seed 1
        state = MitigationState(char)
        rng = random.Random(1)
        no_cd = apply_mitigation(state, _physical_hit(0.0, 100_000), rng)
        assert not no_cd["was_avoided"]

    # Same hit with Icebound Fortitude up
    state2 = MitigationState(char)
    state2.shield_wall_until = 100.0
    rng2 = random.Random(rng.random())  # different roll
    with_cd = apply_mitigation(state2, _physical_hit(0.0, 100_000), rng2)
    if with_cd["was_avoided"]:
        return

    # 30% DR on top of the rest of the chain → ~70% of the no-CD dealt
    assert with_cd["dealt"] < no_cd["dealt"]
    assert with_cd["dealt"] == pytest.approx(no_cd["dealt"] * 0.70, rel=0.20)


def test_dk_constants_block_present_and_uncalibrated():
    """The spec config exists and is marked `characterized` (Top-5 #4,
    2026-07-06) — real logs replayed, still short of the `calibrated`
    parity bar. The UI uses this tier to surface an amber uncalibrated-spec
    warning."""
    from simf.core.constants import load_constants, spec_is_calibrated

    c = load_constants()
    spec = c["specs"].get("blood_death_knight")
    assert spec is not None, "blood_death_knight specs block is missing"
    assert spec["calibration_tier"] == "characterized"
    assert not spec_is_calibrated(spec)
    # Spot-check the critical knobs the mitigation chain reads
    assert spec["bone_shield_armor_from_strength"] == pytest.approx(1.80)
    assert spec["bone_shield_spell_id"] == 195181
    assert spec["rune_carved_plates_physical_spell_id"] == 440289
    assert spec["rune_carved_plates_magical_spell_id"] == 440290
    assert spec["rune_carved_plates_dr_per_stack"] == pytest.approx(0.015)
    assert spec["rune_carved_plates_avg_stacks_while_up"] > 0
    assert spec["blood_soaked_ground_spell_id"] == 434034
    # 12.1.0 scalar bump (prepared on patch/12.1.0-scalars, held for ship
    # ~Aug 11 2026): live 5% -> PTR 8%, confirmed by Warcraft Wiki's Patch
    # 12.1.0 page.
    assert spec["blood_soaked_ground_physical_dr"] == pytest.approx(0.08)
    assert spec["death_strike_heal_pct_of_recent_dmg"] > 0
    assert spec["vampiric_blood_max_hp_increase"] > 0
    assert spec["icebound_fortitude_dr"] > 0
    # Wowhead 48792 (live Midnight): IBF cooldown is 2 minutes, and the
    # coaching registry in the same file says 120 — keep them consistent.
    assert spec["icebound_fortitude_cooldown_s"] == pytest.approx(120.0)


def test_dk_cooldown_planner_ibf_in_sync_with_constants():
    """`LONG_CD_BUTTONS` doesn't read constants.yaml — it's kept in sync by
    hand (the ProtPal Ardent Defender fix found this registry silently stale
    and load-bearing for planned runs). Tripwire: the Blood DK Icebound
    Fortitude row must match specs.blood_death_knight.*."""
    from simf.core.constants import load_constants
    from simf.core.cooldown_planner import LONG_CD_BUTTONS

    spec = load_constants()["specs"]["blood_death_knight"]
    ibf = next(
        row for row in LONG_CD_BUTTONS["blood_death_knight"] if row[0] == "icebound_fortitude"
    )
    assert ibf[1] == pytest.approx(spec["icebound_fortitude_cooldown_s"])
    assert ibf[2] == pytest.approx(spec["icebound_fortitude_duration_s"])
    vb = next(row for row in LONG_CD_BUTTONS["blood_death_knight"] if row[0] == "vampiric_blood")
    assert vb[1] == pytest.approx(spec["vampiric_blood_cooldown_s"])
    assert vb[2] == pytest.approx(spec["vampiric_blood_duration_s"])


def test_dk_policy_fires_death_strike_when_hp_low():
    """The decide() path heals via Death Strike when HP < 70% and the
    rate-limit interval has elapsed. The heal uses recent_damage_5s."""
    from simf.classes.blood_death_knight import BloodDKPolicy

    char = _make_dk()
    state = MitigationState(char)
    state.hp = state.max_hp * 0.5  # 50% HP — DS should fire
    # Seed the recent damage window so DS has something to heal from
    state.recent_damage_window.append((0.0, 100_000.0))

    policy = BloodDKPolicy(char)
    hp_before = state.hp
    policy.decide(state, now=0.5, recent_dtps=200_000.0)
    assert state.hp > hp_before, "Death Strike should restore HP at 50%"
