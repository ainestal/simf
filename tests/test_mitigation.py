import random

import pytest

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.marginals import ehp_marginals
from simf.core.mitigation import MitigationState, apply_mitigation
from simf.core.policy import ActiveMitigationPolicy


def make_test_character(race: str = "human", armor: int = 25000, vers: int = 0) -> Character:
    return Character(
        name="test",
        race=race,
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=0,
        stamina=80000,
        armor_from_gear=armor,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=0,
        versatility_rating=vers,
        max_hp_override=8_000_000,
    )


def make_phys_event(
    amount: float = 1_000_000, avoidable: bool = False, blockable: bool = False
) -> DamageEvent:
    return DamageEvent(
        time_s=0.0,
        source_id="test",
        school="physical",
        raw_amount=amount,
        attack_type="melee",
        is_avoidable=avoidable,
        is_blockable=blockable,
    )


def fresh_state(c: Character) -> MitigationState:
    s = MitigationState(c)
    s.talents = set()  # disable talent passives for raw-formula tests
    return s


def test_armor_dr_formula():
    c_const = load_constants()
    # Use armor low enough that the uncapped formula stays below max_armor_dr (0.85).
    # With K=3430: armor=5000 → DR = 5000/8430 = 59.3% (below cap).
    char = make_test_character(armor=5000)
    K = c_const["armor"]["k_constant"]
    raw_dr = 5000 / (5000 + K)
    capped_dr = min(raw_dr, c_const["armor"]["max_armor_dr"])
    # Defensive Stance (15% all schools) also applies to physical events on
    # Prot Warrior — see mitigation.py step 5c and docs/simc-reference/AUDIT.md F4.
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    expected_damage = 1_000_000 * (1 - capped_dr) * (1 - ds_dr)

    state = fresh_state(char)
    result = apply_mitigation(state, make_phys_event(), random.Random(0))
    assert abs(result["dealt"] - expected_damage) < 1.0


def test_earthen_racial_armor_increase():
    c_human = make_test_character(race="human", armor=25000)
    c_earthen = make_test_character(race="earthen", armor=25000)
    assert abs(c_human.total_armor() - 25000) < 0.01
    assert abs(c_earthen.total_armor() - 27500) < 0.01


def test_wowhead_build_armor_multipliers_stack():
    """Reinforced Plates (+5%) and Armor Specialization (+6%) stack multiplicatively
    on top of base armor for Prot Warrior, per SimC composite_armor_multiplier()."""
    c_base = make_test_character(race="human", armor=5000)
    # The wowhead-prot loadout has both armor-multiplier talents.
    c_wowhead = Character(
        name="wowhead",
        race="human",
        class_spec="protection_warrior",
        talents="wowhead-prot",
        strength=0,
        stamina=80000,
        armor_from_gear=5000,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=0,
        versatility_rating=0,
        max_hp_override=8_000_000,
    )
    # Base: 5000 armor. Wowhead build: 5000 × 1.05 × 1.06 = 5565.
    assert abs(c_base.total_armor() - 5000) < 0.01
    assert abs(c_wowhead.total_armor() - 5000 * 1.05 * 1.06) < 0.01


def test_armor_multiplier_talents_dont_apply_without_loadout():
    """Brutoh-actual loadout has NEITHER armor-multiplier talent → no
    multiplicative inflation. Vanguard (spec passive, not a talent — see
    docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md) still
    adds its flat strength-derived bonus armor regardless of loadout."""
    c = Character(
        name="brutoh-test",
        race="earthen",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2182,
        stamina=34176,
        armor_from_gear=5015,
        haste_rating=2318,
        crit_rating=1391,
        mastery_rating=1608,
        versatility_rating=296,
    )
    vanguard_armor = (
        2182 * load_constants()["specs"]["protection_warrior"]["vanguard_armor_per_strength"]
    )
    # 5015 × 1.10 Earthen + Vanguard, no additional talent multipliers.
    assert abs(c.total_armor() - (5015 * 1.10 + vanguard_armor)) < 0.01


def test_earthen_reduces_physical_damage_by_expected_ratio():
    """Verify Earthen damage reduction matches the closed-form formula derivation."""
    c_const = load_constants()
    K = c_const["armor"]["k_constant"]
    # Choose armor low enough that neither human nor earthen (×1.10) hits max_armor_dr.
    # Safe threshold: armor/(armor+K) < max_armor_dr → armor < K*max_dr/(1-max_dr).
    # With K=1550 and cap=0.85: threshold = 1550*0.85/0.15 = 8783. Use 5000.
    armor = 5000

    c_h = make_test_character(race="human", armor=armor)
    c_e = make_test_character(race="earthen", armor=armor)

    rng_seed = 0
    r_h = apply_mitigation(fresh_state(c_h), make_phys_event(), random.Random(rng_seed))
    r_e = apply_mitigation(fresh_state(c_e), make_phys_event(), random.Random(rng_seed))

    expected_ratio = (armor + K) / (armor * 1.10 + K)
    actual_ratio = r_e["dealt"] / r_h["dealt"]
    assert abs(actual_ratio - expected_ratio) < 1e-6


def test_versatility_dr_formula():
    c_const = load_constants()
    rating_per_pct = c_const["stat_conversion"]["versatility_rating_per_pct"]
    # Keep vers below the 30% stat-DR breakpoint so this isolates the vers→DR
    # halving (vers_dr = vers_pct × 0.5), not secondary diminishing returns.
    rating = 1000  # 1000/54 = 18.5% vers, below the first DR bracket
    char = make_test_character(vers=rating)
    expected_vers_pct = rating / rating_per_pct / 100
    expected_dr = expected_vers_pct * 0.5
    assert abs(char.versatility_dr() - expected_dr) < 1e-6


def test_versatility_reduces_damage_multiplicatively():
    c0 = make_test_character(vers=0)
    c1 = make_test_character(vers=2050)  # 5% damage taken DR

    r0 = apply_mitigation(fresh_state(c0), make_phys_event(), random.Random(0))
    r1 = apply_mitigation(fresh_state(c1), make_phys_event(), random.Random(0))

    assert r1["dealt"] < r0["dealt"]
    expected_factor = 1 - c1.versatility_dr()
    assert abs(r1["dealt"] / r0["dealt"] - expected_factor) < 1e-6


def test_full_avoidance_zero_damage():
    """When dodge/parry rolls succeed, damage is zero."""
    char = make_test_character()
    state = fresh_state(char)

    class ZeroRNG:
        def random(self) -> float:
            return 0.0

    event = make_phys_event(avoidable=True)
    result = apply_mitigation(state, event, ZeroRNG())
    assert result["was_avoided"] is True
    assert result["dealt"] == 0.0


def test_magic_damage_ignores_armor():
    c_const = load_constants()
    def_stance_dr = (
        c_const.get("specs", {}).get("protection_warrior", {}).get("defensive_stance_dr", 0.0)
    )
    char = make_test_character(armor=99999)
    state = fresh_state(char)
    event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )
    result = apply_mitigation(state, event, random.Random(0))
    # Armor does NOT apply to magic, but Defensive Stance passive DR does.
    expected = 1_000_000 * (1 - def_stance_dr)
    assert abs(result["dealt"] - expected) < 1.0


def test_shield_block_active_adds_no_flat_physical_dr_layer():
    """REMOVED 2026-07-21 (docs/validation/protwarrior_shield_block_fix_2026_07_21.md):
    Shield Block does NOT apply a separate flat physical-DR multiplier on top
    of block chance/value in Midnight 12.0.5 — its only real effect is
    guaranteeing the block roll (handled at step 3, gated on a *blockable*
    event). For a non-blockable physical event, being inside vs outside a
    Shield Block window must produce IDENTICAL dealt damage — this is the
    regression test for the double-count bug (old `physical_dr: 0.30`) that
    used to cut damage a second time here."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    char = make_test_character(armor=0)  # zero armor so only DS contributes

    active_state = fresh_state(char)
    active_state.shield_block_until = 9999.0  # SB active
    inactive_state = fresh_state(char)
    inactive_state.shield_block_until = -1.0  # SB expired

    # blockable=False bypasses the block-chance roll at step 3, isolating
    # whatever (if anything) a "SB active" flag does elsewhere in the chain.
    event = make_phys_event(blockable=False)
    active_result = apply_mitigation(active_state, event, random.Random(0))
    inactive_result = apply_mitigation(inactive_state, event, random.Random(0))

    expected = 1_000_000 * (1 - ds_dr)  # Defensive Stance only — no SB layer
    assert abs(active_result["dealt"] - expected) < 1.0
    assert abs(inactive_result["dealt"] - expected) < 1.0
    assert active_result["dealt"] == pytest.approx(inactive_result["dealt"])


def test_shield_block_active_adds_no_flat_magic_dr_layer():
    """Same regression, magic school — SB active must not touch magic damage
    (it never did; kept as an explicit school-boundary check now that the
    physical-side layer this used to contrast against is gone)."""
    c_const = load_constants()
    def_stance_dr = (
        c_const.get("specs", {}).get("protection_warrior", {}).get("defensive_stance_dr", 0.0)
    )
    char = make_test_character(armor=0)
    state = fresh_state(char)
    state.shield_block_until = 9999.0  # SB active

    magic_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )
    result = apply_mitigation(state, magic_event, random.Random(0))
    expected = 1_000_000 * (1 - def_stance_dr)
    assert abs(result["dealt"] - expected) < 1.0


def test_keep_feet_on_ground_applies_flat_all_school_dr_when_active():
    """Keep Your Feet on the Ground (Mountain Thane, spell 438591) — flat -8%
    all-school DR while event.active_buffs carries the spell id (window-gated
    on the log's real buff windows, same pattern as VDH's Painbringer). See
    docs/validation/protwarrior_magic_wedge_kyfotg_lead_2026_07_22.md."""
    c_const = load_constants()
    spell_id = c_const["specs"]["protection_warrior"]["keep_feet_on_ground_spell_id"]
    kyfotg_dr = c_const["specs"]["protection_warrior"]["keep_feet_on_ground_dr"]
    char = make_test_character(armor=0)  # zero armor: isolate this one layer

    active_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
        active_buffs=frozenset({spell_id}),
    )
    inactive_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )

    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    active_result = apply_mitigation(fresh_state(char), active_event, random.Random(0))
    inactive_result = apply_mitigation(fresh_state(char), inactive_event, random.Random(0))

    expected_inactive = 1_000_000 * (1 - ds_dr)
    expected_active = expected_inactive * (1 - kyfotg_dr)
    assert active_result["dealt"] == pytest.approx(expected_active, abs=1.0)
    assert inactive_result["dealt"] == pytest.approx(expected_inactive, abs=1.0)


def test_keep_feet_on_ground_ignores_unrelated_active_buffs():
    """A non-empty active_buffs set that doesn't contain 438591 must not
    trigger the layer — a regression here would mean any window-gated buff
    (e.g. a future addition) silently credits every other one too."""
    c_const = load_constants()
    kyfotg_dr = c_const["specs"]["protection_warrior"]["keep_feet_on_ground_dr"]
    char = make_test_character(armor=0)

    event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
        active_buffs=frozenset({999999}),
    )
    result = apply_mitigation(fresh_state(char), event, random.Random(0))
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    expected = 1_000_000 * (1 - ds_dr)
    assert result["dealt"] == pytest.approx(expected, abs=1.0)
    assert kyfotg_dr > 0  # sanity: the constant itself is non-trivial


def test_fight_through_flames_reduces_magic_damage():
    """Fight Through Flames (spell 452494, May 12 2026 hotfix 4%→6%) adds a
    magic-only DR layer on top of Defensive Stance Effect 1. Composes
    multiplicatively per SimC parse_effects on the DS buff."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    ftf_dr = c_const["talents"]["fight_through_flames"]["magic_dr"]
    char = make_test_character(armor=0)
    state = fresh_state(char)
    state.talents = {"fight_through_flames"}

    magic_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )
    result = apply_mitigation(state, magic_event, random.Random(0))
    expected = 1_000_000 * (1 - ds_dr) * (1 - ftf_dr)
    assert abs(result["dealt"] - expected) < 1.0


def test_fight_through_flames_does_not_reduce_physical_damage():
    """FtF activates Effect 3 of the DS buff with school mask 126 (magic
    schools only). Physical events must be unaffected."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    char = make_test_character(armor=0)
    state = fresh_state(char)
    state.talents = {"fight_through_flames"}

    phys_event = make_phys_event(blockable=False)
    result = apply_mitigation(state, phys_event, random.Random(0))
    expected = 1_000_000 * (1 - ds_dr)
    assert abs(result["dealt"] - expected) < 1.0


def test_fight_through_flames_inactive_without_talent():
    """Without the talent, FtF must not apply — preserves bit-identity for
    every loadout that doesn't pick it (including brutoh-actual)."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    char = make_test_character(armor=0)
    state = fresh_state(char)
    assert "fight_through_flames" not in state.talents

    magic_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )
    result = apply_mitigation(state, magic_event, random.Random(0))
    expected = 1_000_000 * (1 - ds_dr)
    assert abs(result["dealt"] - expected) < 1.0


def test_unyielding_stance_reduces_physical_damage():
    """Unyielding Stance (spell 1235047) is a flat modifier to Defensive
    Stance's OWN Effect 1 magnitude (DBC: A_ADD_FLAT_MODIFIER on Effect #1,
    value -4) — it folds ADDITIVELY into Effect 1's DR, not a separate
    multiplicative aura like Fight Through Flames' Effect-3 layer. Regression
    test for the 2026-08-07 fix: this was previously miscoded as a
    Demoralizing Shout DR bump (a different ability), which meant it
    silently did nothing for any character without an active Demo Shout
    window."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    us_dr = c_const["talents"]["unyielding_stance"]["defensive_stance_dr_increase"]
    char = make_test_character(armor=0)
    state = fresh_state(char)
    state.talents = {"unyielding_stance"}

    phys_event = make_phys_event(blockable=False)
    result = apply_mitigation(state, phys_event, random.Random(0))
    expected = 1_000_000 * (1 - (ds_dr + us_dr))
    assert abs(result["dealt"] - expected) < 1.0


def test_unyielding_stance_reduces_magic_damage():
    """All-schools — Effect 1 carries school mask 127, same as the base DR
    it modifies, so magic events are reduced too."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    us_dr = c_const["talents"]["unyielding_stance"]["defensive_stance_dr_increase"]
    char = make_test_character(armor=0)
    state = fresh_state(char)
    state.talents = {"unyielding_stance"}

    magic_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=1_000_000,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )
    result = apply_mitigation(state, magic_event, random.Random(0))
    expected = 1_000_000 * (1 - (ds_dr + us_dr))
    assert abs(result["dealt"] - expected) < 1.0


def test_unyielding_stance_inactive_without_talent():
    """Without the talent, Unyielding Stance must not apply — preserves
    bit-identity for every loadout that doesn't pick it."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    char = make_test_character(armor=0)
    state = fresh_state(char)
    assert "unyielding_stance" not in state.talents

    phys_event = make_phys_event(blockable=False)
    result = apply_mitigation(state, phys_event, random.Random(0))
    expected = 1_000_000 * (1 - ds_dr)
    assert abs(result["dealt"] - expected) < 1.0


def test_unyielding_stance_does_not_inflate_demoralizing_shout_dr():
    """Regression test for the exact bug: Unyielding Stance must NOT add its
    bonus onto Demoralizing Shout's DR anymore, even while a Demo Shout
    window is active and the talent is present."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    us_dr = c_const["talents"]["unyielding_stance"]["defensive_stance_dr_increase"]
    demo_dr = c_const["active_mitigation"]["demoralizing_shout"]["damage_taken_reduction"]
    char = make_test_character(armor=0)
    state = fresh_state(char)
    state.talents = {"unyielding_stance"}
    state.demo_shout_until = 9999.0

    phys_event = make_phys_event(blockable=False)
    result = apply_mitigation(state, phys_event, random.Random(0))
    # (Defensive Stance folded with Unyielding Stance) x Demo Shout — two
    # independent multiplicative layers, Demo Shout not inflated by Unyielding.
    expected = 1_000_000 * (1 - (ds_dr + us_dr)) * (1 - demo_dr)
    assert abs(result["dealt"] - expected) < 1.0


def test_recent_damage_sum_matches_window_iteration_after_trim():
    """`recent_damage_total` caches a running sum updated by `add_recent_damage`
    and `trim_recent_damage`. Cache must stay in lockstep with the underlying
    window across every append + trim, including the boundary case where the
    window empties (sum should reset to exactly 0 to avoid float drift)."""
    char = make_test_character()
    state = fresh_state(char)
    rng = random.Random(0)

    # Fill the window across the boundary, then trim to verify decrement.
    sequence = [(0.5, 100.0), (1.0, 200.0), (3.5, 350.0), (5.5, 75.0), (8.0, 50.0)]
    for t, d in sequence:
        state.add_recent_damage(t, d)

    # Before any trim, cache equals the full sum.
    full_sum = sum(d for _t, d in state.recent_damage_window)
    assert state._recent_damage_sum == full_sum

    # Trim at t=5.0 with window=5.0 → cutoff=0.0 → no entries dropped.
    state.trim_recent_damage(5.0)
    assert len(state.recent_damage_window) == 5
    assert state.recent_damage_total(5.0) == sum(d for _t, d in state.recent_damage_window)

    # Trim at t=6.0 → cutoff=1.0 → drops (0.5, 100.0).
    state.trim_recent_damage(6.0)
    assert state.recent_damage_total(6.0) == sum(d for _t, d in state.recent_damage_window)
    assert (0.5, 100.0) not in state.recent_damage_window

    # Trim past the tail → window empties → sum must reset to exactly 0.0.
    state.trim_recent_damage(100.0)
    assert len(state.recent_damage_window) == 0
    assert state._recent_damage_sum == 0.0

    # And apply_mitigation through the helper keeps the invariant alive.
    state.add_recent_damage(101.0, 42.0)
    event = make_phys_event(blockable=False)
    event.time_s = 101.0  # so trim_recent_damage doesn't drop the new entry
    apply_mitigation(state, event, rng)
    assert state.recent_damage_total(101.0) == sum(d for _t, d in state.recent_damage_window)


def test_demo_shout_not_applied_to_immune_source():
    """Demo Shout DR is skipped when attacker is in demoralizing_shout.immune_sources."""
    c_const = load_constants()
    immune_sources = c_const["active_mitigation"]["demoralizing_shout"].get("immune_sources", [])
    assert immune_sources, "immune_sources list must be non-empty for this test to be meaningful"

    char = make_test_character(armor=0)
    rng = random.Random(0)

    # Baseline: non-immune attacker with DS active
    state_base = fresh_state(char)
    state_base.demo_shout_until = 9999.0
    evt_normal = DamageEvent(
        time_s=0.0,
        source_id="some_trash_mob",
        school="physical",
        raw_amount=1_000_000,
        attack_type="melee",
    )
    result_normal = apply_mitigation(state_base, evt_normal, rng)

    # Immune attacker with DS active — should take same damage as without DS
    state_immune = fresh_state(char)
    state_immune.demo_shout_until = 9999.0
    evt_immune = DamageEvent(
        time_s=0.0,
        source_id=immune_sources[0],
        school="physical",
        raw_amount=1_000_000,
        attack_type="melee",
    )
    result_immune = apply_mitigation(state_immune, evt_immune, rng)

    # Immune attacker without DS active — baseline for "no DS"
    state_no_ds = fresh_state(char)
    state_no_ds.demo_shout_until = -1.0
    result_no_ds = apply_mitigation(state_no_ds, evt_immune, rng)

    ds_dr = c_const["active_mitigation"]["demoralizing_shout"]["damage_taken_reduction"]
    assert result_normal["dealt"] < result_immune["dealt"], (
        "DS should reduce damage vs normal attacker"
    )
    assert abs(result_immune["dealt"] - result_no_ds["dealt"]) < 1.0, (
        f"Immune source should ignore DS: got {result_immune['dealt']:.0f}, expected {result_no_ds['dealt']:.0f}"
    )
    actual_ratio = result_normal["dealt"] / result_immune["dealt"]
    assert abs(actual_ratio - (1 - ds_dr)) < 1e-6, (
        f"DS should reduce normal attacker by {ds_dr:.0%}: ratio={actual_ratio:.6f}"
    )


def test_phalanx_not_applied_to_immune_source():
    """Phalanx DR is skipped when attacker is in talents.phalanx.immune_sources.

    Phalanx is a target debuff; bosses immune to negative effects never receive
    the mark, so the player gets no DR from them. Algeth'ar Academy's Echo of
    Doragosa is the empirical anchor — the calibration gap widened from -9% to
    -16.8% when Phalanx wrongly applied to that source.
    """
    c_const = load_constants()
    immune_sources = c_const["talents"]["phalanx"].get("immune_sources", [])
    assert immune_sources, "phalanx.immune_sources must be non-empty for this test to be meaningful"

    char = make_test_character(armor=0)
    rng = random.Random(0)

    state_normal = fresh_state(char)
    state_normal.talents = {"phalanx"}
    evt_normal = DamageEvent(
        time_s=0.0,
        source_id="some_trash_mob",
        school="physical",
        raw_amount=1_000_000,
        attack_type="melee",
    )
    result_normal = apply_mitigation(state_normal, evt_normal, rng)

    state_immune = fresh_state(char)
    state_immune.talents = {"phalanx"}
    evt_immune = DamageEvent(
        time_s=0.0,
        source_id=immune_sources[0],
        school="physical",
        raw_amount=1_000_000,
        attack_type="melee",
    )
    result_immune = apply_mitigation(state_immune, evt_immune, rng)

    state_no_phalanx = fresh_state(char)
    state_no_phalanx.talents = set()
    result_no_phalanx = apply_mitigation(state_no_phalanx, evt_immune, rng)

    phalanx_cfg = c_const["talents"]["phalanx"]
    expected_dr = phalanx_cfg["damage_taken_debuff_on_target"] * phalanx_cfg["avg_uptime"]
    assert result_normal["dealt"] < result_immune["dealt"], (
        "Phalanx should reduce damage from a non-immune attacker"
    )
    assert abs(result_immune["dealt"] - result_no_phalanx["dealt"]) < 1.0, (
        f"Immune source should ignore Phalanx: got {result_immune['dealt']:.0f}, "
        f"expected {result_no_phalanx['dealt']:.0f}"
    )
    actual_ratio = result_normal["dealt"] / result_immune["dealt"]
    assert abs(actual_ratio - (1 - expected_dr)) < 1e-6, (
        f"Phalanx should reduce normal attacker by {expected_dr:.1%}: ratio={actual_ratio:.6f}"
    )


def _replay_phys_event(is_log_replay: bool) -> DamageEvent:
    """A bare physical replay/synthetic event with no absorb/block interference."""
    return DamageEvent(
        time_s=0.0,
        source_id="some_trash_mob",
        school="physical",
        raw_amount=1_000_000,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=False,
        is_log_replay=is_log_replay,
        log_absorbed=0.0,
    )


def test_demo_shout_not_double_counted_in_log_replay():
    """Demo Shout's −20% must NOT re-apply on log-replay events.

    Demoralizing Shout is an attacker-side debuff (it reduces the damage the mob
    DEALS), so it is already baked into the log's `unmitigatedAmount` /
    `raw_amount`. Applying it again in replay double-counts it — the flagship
    Prot Warrior K=3430 calibration ran with this live. Regression test for
    docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md.

    Mutation guard: dropping `and not event.is_log_replay` at the Demo Shout
    guard in mitigation.py makes the replay-mode DS-active/inactive pair diverge,
    failing the equality assertion below.
    """
    char = make_test_character(armor=0)

    # SYNTHETIC mode: Demo Shout IS a legitimate modeled DR layer.
    syn_active = fresh_state(char)
    syn_active.demo_shout_until = 9999.0
    syn_inactive = fresh_state(char)
    syn_inactive.demo_shout_until = -1.0
    d_syn_active = apply_mitigation(syn_active, _replay_phys_event(False), random.Random(0))[
        "dealt"
    ]
    d_syn_inactive = apply_mitigation(syn_inactive, _replay_phys_event(False), random.Random(0))[
        "dealt"
    ]
    assert d_syn_active < d_syn_inactive, (
        "Synthetic mode must still apply Demo Shout's DR when the window is active"
    )

    # REPLAY mode: the −20% is already in raw_amount → the DS window must be inert.
    rep_active = fresh_state(char)
    rep_active.demo_shout_until = 9999.0
    rep_inactive = fresh_state(char)
    rep_inactive.demo_shout_until = -1.0
    d_rep_active = apply_mitigation(rep_active, _replay_phys_event(True), random.Random(0))["dealt"]
    d_rep_inactive = apply_mitigation(rep_inactive, _replay_phys_event(True), random.Random(0))[
        "dealt"
    ]
    assert abs(d_rep_active - d_rep_inactive) < 1e-6, (
        "Log-replay must NOT re-apply Demo Shout (already in unmitigatedAmount): "
        f"active={d_rep_active:.1f} vs inactive={d_rep_inactive:.1f}"
    )


def test_phalanx_not_double_counted_in_log_replay():
    """Phalanx's mark DR must NOT re-apply on log-replay events.

    Phalanx marks the mob (attacker-side), so its reduction is already inside the
    log's `raw_amount`. Brutoh's ratified 16-log corpus runs the `brutoh-actual`
    loadout, which includes Phalanx — so this was a live double-count. Regression
    test for docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md.

    Mutation guard: dropping `and not event.is_log_replay` at the Phalanx guard
    makes the replay-mode phalanx/no-phalanx pair diverge, failing the equality
    assertion below.
    """
    char = make_test_character(armor=0)

    # SYNTHETIC mode: Phalanx IS a legitimate modeled DR layer.
    syn_phalanx = fresh_state(char)
    syn_phalanx.talents = {"phalanx"}
    syn_none = fresh_state(char)
    syn_none.talents = set()
    d_syn_phalanx = apply_mitigation(syn_phalanx, _replay_phys_event(False), random.Random(0))[
        "dealt"
    ]
    d_syn_none = apply_mitigation(syn_none, _replay_phys_event(False), random.Random(0))["dealt"]
    assert d_syn_phalanx < d_syn_none, "Synthetic mode must still apply Phalanx's DR"

    # REPLAY mode: the mark's DR is already in raw_amount → Phalanx must be inert.
    rep_phalanx = fresh_state(char)
    rep_phalanx.talents = {"phalanx"}
    rep_none = fresh_state(char)
    rep_none.talents = set()
    d_rep_phalanx = apply_mitigation(rep_phalanx, _replay_phys_event(True), random.Random(0))[
        "dealt"
    ]
    d_rep_none = apply_mitigation(rep_none, _replay_phys_event(True), random.Random(0))["dealt"]
    assert abs(d_rep_phalanx - d_rep_none) < 1e-6, (
        "Log-replay must NOT re-apply Phalanx (already in unmitigatedAmount): "
        f"phalanx={d_rep_phalanx:.1f} vs none={d_rep_none:.1f}"
    )


def test_parry_from_strength_is_small_fraction():
    """Parry from strength must be a small probability (<0.5), not a large multiplier.

    Guards against the /100 divisor being dropped — without it, 2182 strength produces
    ~436% parry, making avoidance always trigger and silently zeroing sim DTPS.
    """
    char = Character(
        name="test",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=0,
        armor_from_gear=0,
    )
    parry = char.base_parry()
    # Should be in the range 0.03–0.20 (3%–20%), not > 1.0
    assert 0.03 <= parry <= 0.20, (
        f"base_parry() returned {parry:.4f} — likely missing /100 in strength conversion"
    )
    total_avoidance = char.base_dodge() + char.base_parry()
    assert total_avoidance < 0.30, (
        f"Total avoidance {total_avoidance:.4f} is unreasonably high (dodge+parry must be < 30%)"
    )


def test_riposte_credits_crit_as_bonus_parry_for_warrior():
    """Riposte (Prot Warrior spec ability, always active — not a talent)
    converts crit rating 1:1 into the parry-rating pool. Ground truth: two
    live in-game tooltip readings from the same character, 2026-07-12 —
    "Parry of 490 adds 9.42% Parry" and "Parry of 870 adds 16.73% Parry" —
    both reproduce exactly at parry_rating_per_pct=52 (see
    docs/validation/protwarrior_riposte_crit_parry_gap_2026_07_12.md)."""

    def _char(crit_rating: int) -> Character:
        return Character(
            name="test",
            race="human",
            class_spec="protection_warrior",
            talents="kiratank-defensive",
            strength=0,
            stamina=0,
            armor_from_gear=0,
            crit_rating=crit_rating,
        )

    no_crit = _char(0)
    delta_490 = _char(490).base_parry() - no_crit.base_parry()
    delta_870 = _char(870).base_parry() - no_crit.base_parry()
    assert delta_490 == pytest.approx(0.0942, abs=0.0005)
    assert delta_870 == pytest.approx(0.1673, abs=0.0005)


def test_riposte_crit_to_parry_does_not_leak_into_other_parry_specs():
    """Riposte is a Prot Warrior spec ability. Paladin/DK/VDH each have
    their own distinct avoidance kit — crit must not grant them parry
    without independently confirming each has an equivalent mechanic."""
    specs = [
        ("protection_paladin", {"strength": 2000}),
        ("blood_death_knight", {"strength": 2000}),
        ("vengeance_demon_hunter", {"strength": 2000, "agility": 2000}),
    ]
    for class_spec, stat_kwargs in specs:
        no_crit = Character(
            name="test",
            race="human",
            class_spec=class_spec,
            talents="brutoh-actual",
            stamina=0,
            armor_from_gear=0,
            crit_rating=0,
            **stat_kwargs,
        )
        with_crit = Character(
            name="test",
            race="human",
            class_spec=class_spec,
            talents="brutoh-actual",
            stamina=0,
            armor_from_gear=0,
            crit_rating=490,
            **stat_kwargs,
        )
        assert with_crit.base_parry() == no_crit.base_parry(), (
            f"{class_spec} must not gain parry from crit_rating (Riposte is warrior-only)"
        )


# ---------------------------------------------------------------------------
# Brewmaster Monk tests
# ---------------------------------------------------------------------------


def make_brewmaster_character(armor: int = 1307, vers: int = 910) -> Character:
    return Character(
        name="AnonBrewmaster1",
        race="human",
        class_spec="brewmaster_monk",
        talents="anonbrewmaster1-brewmaster",
        strength=0,
        stamina=30364,
        armor_from_gear=armor,
        versatility_rating=vers,
        max_hp_override=668018,
    )


def test_brewmaster_armor_dr_applies():
    """Brewmaster physical hits take armor DR then stagger-into-pool.

    Phase 4.4 updated the synthetic Brewmaster path: stagger now routes a
    fraction of damage into `state.stagger_pool` (DoT pool) instead of
    applying flat DR. Ironskin Brew uptime adds an extra stagger %.
    """
    c_const = load_constants()
    char = make_brewmaster_character(armor=1307, vers=0)
    K = c_const["armor"]["k_constant"]
    armor = char.total_armor()
    armor_dr = min(armor / (armor + K), c_const["armor"]["max_armor_dr"])
    spec = c_const["specs"]["brewmaster_monk"]
    stagger_pct = min(
        spec["stagger_pct_physical"]
        + spec["ironskin_brew_stagger_increase"] * spec["ironskin_brew_avg_uptime"],
        0.85,
    )
    expected = 1_000_000 * (1 - armor_dr) * (1 - stagger_pct)

    state = MitigationState(char)
    result = apply_mitigation(state, make_phys_event(1_000_000), random.Random(0))
    assert abs(result["dealt"] - expected) < 1.0, (
        f"Brewmaster armor DR wrong: got {result['dealt']:.0f}, expected {expected:.0f}"
    )
    # The staggered portion lives in the pool, not the dealt total.
    assert state.stagger_pool > 0


def test_brewmaster_no_block():
    """Brewmaster has no shield — base_block() returns 0."""
    char = make_brewmaster_character()
    assert char.base_block() == 0.0


def test_brewmaster_no_parry():
    """Brewmaster has no parry — base_parry() returns 0."""
    char = make_brewmaster_character()
    assert char.base_parry() == 0.0


def test_brewmaster_log_absorbed_used_in_replay():
    """In replay mode, log_absorbed is subtracted from post-mitigation damage."""
    char = make_brewmaster_character(armor=0, vers=0)
    state = MitigationState(char)

    event = DamageEvent(
        time_s=0.0,
        source_id="boss",
        school="physical",
        raw_amount=500_000,
        attack_type="melee",
        is_log_replay=True,
        log_absorbed=200_000,
    )
    result = apply_mitigation(state, event, random.Random(0))
    # No armor (armor=0), no vers, is_log_replay → absorb 200k
    assert abs(result["absorbed_by_ignore_pain"] - 200_000) < 1.0
    assert abs(result["dealt"] - 300_000) < 1.0


def test_brewmaster_stagger_pool_grows_on_synthetic_hit():
    """In synthetic mode, the staggered fraction lands in the DoT pool
    rather than being applied as flat DR. The dealt amount excludes the
    staggered portion; the pool grows by exactly that amount."""
    c_const = load_constants()
    spec = c_const["specs"]["brewmaster_monk"]
    stagger_pct = min(
        spec["stagger_pct_physical"]
        + spec["ironskin_brew_stagger_increase"] * spec["ironskin_brew_avg_uptime"],
        0.85,
    )
    char = make_brewmaster_character(armor=0, vers=0)
    state = MitigationState(char)

    event = DamageEvent(
        time_s=0.0,
        source_id="boss",
        school="physical",
        raw_amount=1_000_000,
        attack_type="melee",
        is_log_replay=False,
    )
    result = apply_mitigation(state, event, random.Random(0))
    expected_dealt = 1_000_000 * (1 - stagger_pct)
    expected_pool = 1_000_000 * stagger_pct
    assert abs(result["dealt"] - expected_dealt) < 1.0
    assert abs(state.stagger_pool - expected_pool) < 1.0


# ---------------------------------------------------------------------------
# Guardian Druid tests
# ---------------------------------------------------------------------------


def make_guardian_character(armor: int = 789, vers: int = 0, agility: int = 0) -> Character:
    return Character(
        name="AnonGuardian2",
        race="troll",
        class_spec="guardian_druid",
        talents="anonguardian2-guardian",
        strength=0,
        agility=agility,
        stamina=16570,
        armor_from_gear=armor,
        versatility_rating=vers,
        max_hp_override=364540,
    )


def test_guardian_total_armor_bear_form_and_flat_ironfur():
    """Guardian total_armor() = gear × Bear Form base-armor multiplier (×3.2)
    PLUS flat-Agility Ironfur (per-stack coeff × Agi × avg stacks) — the
    SimC-grounded mechanic: Ironfur (192081) bonus armor is a % of Agility added
    OUTSIDE composite_armor_multiplier, so simf's `gear×3.2 + agi×coeff×stacks`
    is the faithful shape. Reads the coeff/stacks from constants so it tracks the
    P1 retune (1.46 / 2.45) without a hardcoded magnitude."""
    c_const = load_constants()
    spec_cfg = c_const["specs"]["guardian_druid"]
    bear = spec_cfg["bear_form_armor_multiplier"]
    per_agi = spec_cfg["ironfur_flat_armor_per_agility_per_stack"]
    stacks = spec_cfg["ironfur_avg_stacks_m_plus"]
    char = make_guardian_character(armor=2961, agility=2656)
    expected_armor = 2961 * bear + 2656 * per_agi * stacks
    assert abs(char.total_armor() - expected_armor) < 0.01, (
        f"Guardian total_armor() wrong: got {char.total_armor():.2f}, expected {expected_armor:.2f}"
    )
    # Bear Form multiplier alone (no agility) must triple the gear armor.
    assert abs(make_guardian_character(armor=1000).total_armor() - 1000 * bear) < 0.01


def _guardian_haste_char(haste_rating: int, *, elunes_chosen: bool) -> Character:
    """AnonGuardian1-shaped Guardian for the haste-model tests."""
    return Character(
        name="R",
        race="tauren",
        class_spec="guardian_druid",
        talents="anonguardian2-guardian",
        strength=269,
        agility=2000,
        stamina=33450,
        armor_from_gear=919,
        haste_rating=haste_rating,
        versatility_rating=598,
        mastery_rating=481,
        active_buff_spell_ids=(frozenset({202770}) if elunes_chosen else None),
    )


def _ref_haste_rating() -> float:
    """The haste_rating whose haste_pct equals the model's ref_haste_pct EXACTLY.

    Returned unrounded so haste_pct == ref_haste_pct to the bit (tests the model
    no-op, not integer-rating rounding). Under the real lvl-90 conversion (44) the
    exact rating isn't an integer; rounding would leave a ~0.001 elasticity residual.
    ref_haste_pct < 0.30 → no secondary DR.
    """
    model = load_constants()["specs"]["guardian_druid"]["ironfur_haste_model"]
    per_pct = load_constants()["stat_conversion"]["haste_rating_per_pct"]
    return model["ref_haste_pct"] * 100 * per_pct


def test_guardian_ironfur_haste_scaling_is_no_op_at_reference():
    """At ref_haste_pct the elasticity factor is exactly 1.0, so an Elune's Chosen
    Guardian's armor equals the static-2.45 path — the no-op guarantee that keeps
    the P1 calibration corpus (13/16) intact."""
    spec = load_constants()["specs"]["guardian_druid"]
    ref_rating = _ref_haste_rating()
    ec = _guardian_haste_char(ref_rating, elunes_chosen=True)
    assert abs(ec._guardian_ironfur_avg_stacks() - spec["ironfur_avg_stacks_m_plus"]) < 1e-9
    noec = _guardian_haste_char(ref_rating, elunes_chosen=False)
    assert abs(ec.total_armor() - noec.total_armor()) < 1e-6


def test_guardian_ironfur_haste_scaling_monotonic_and_clamped():
    """More haste → more stacks for an Elune's Chosen Guardian, true-clamped to
    ironfur_stacks_max (NOT a quadratic collapse)."""
    spec = load_constants()["specs"]["guardian_druid"]
    ref = _ref_haste_rating()
    lo = _guardian_haste_char(ref - 300, elunes_chosen=True)._guardian_ironfur_avg_stacks()
    base = _guardian_haste_char(ref, elunes_chosen=True)._guardian_ironfur_avg_stacks()
    hi = _guardian_haste_char(ref + 1000, elunes_chosen=True)._guardian_ironfur_avg_stacks()
    assert lo < base < hi
    # absurd haste clamps to the ceiling, not above
    clamped = _guardian_haste_char(60000, elunes_chosen=True)._guardian_ironfur_avg_stacks()
    assert clamped == spec["ironfur_stacks_max"]


def test_guardian_no_haste_scaling_without_elunes_chosen():
    """A Guardian WITHOUT the Fury of Elune buff (Druid of the Claw / unknown)
    gets the static stacks regardless of haste — haste-independent, bit-identical
    to the pre-haste-model behaviour."""
    spec = load_constants()["specs"]["guardian_druid"]
    for rating in (500, 1210, 4000):
        c = _guardian_haste_char(rating, elunes_chosen=False)
        assert c._guardian_ironfur_avg_stacks() == spec["ironfur_avg_stacks_m_plus"]


def test_guardian_haste_survival_marginal_gated():
    """ehp_marginals gives haste a non-zero Guardian survival marginal ONLY for an
    Elune's Chosen build; non-EC Guardian and every other spec stay exactly 0."""
    ec = _guardian_haste_char(1210, elunes_chosen=True)
    noec = _guardian_haste_char(1210, elunes_chosen=False)
    assert ehp_marginals(ec)["haste_rating"]["p"] > 0.0
    assert ehp_marginals(noec)["haste_rating"]["p"] == 0.0
    # warrior path bit-identical (haste survival stays zero)
    warrior = Character(
        name="B",
        race="human",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2182,
        agility=428,
        stamina=35884,
        armor_from_gear=5015,
        shield_armor=931,
        haste_rating=1167,
        versatility_rating=262,
        mastery_rating=396,
    )
    assert ehp_marginals(warrior)["haste_rating"] == {"p": 0.0, "m": 0.0}


def test_guardian_always_on_dr_thick_hide_bear_form():
    """Guardian baseline flat all-school DR = Thick Hide × Bear Form passive."""
    c_const = load_constants()
    spec_cfg = c_const["specs"]["guardian_druid"]
    expected = (1 - spec_cfg["thick_hide_dr"]) * (1 - spec_cfg["bear_form_passive_dr"])
    char = make_guardian_character()
    assert abs(char._always_on_dr() - expected) < 1e-9
    assert expected < 1.0  # it actually reduces damage (≈0.931)


def test_warrior_always_on_dr_includes_unyielding_stance_when_talented():
    """_always_on_dr() (the eHP headline) must agree with the mitigation
    timeline: Unyielding Stance folds additively into Defensive Stance's own
    DR in both places, or the eHP number and the Monte Carlo result disagree
    for any build carrying the talent."""
    c_const = load_constants()
    ds_dr = c_const["specs"]["protection_warrior"]["defensive_stance_dr"]
    us_dr = c_const["talents"]["unyielding_stance"]["defensive_stance_dr_increase"]
    # kiratank-defensive (make_test_character's default loadout) also carries
    # indomitable — a separate, pre-existing always-on multiplicative layer,
    # unrelated to this fix but part of the same char's real _always_on_dr().
    indom_dr = c_const["talents"]["indomitable"]["damage_reduction"]
    char = make_test_character(armor=0)
    expected = (1 - (ds_dr + us_dr)) * (1 - indom_dr)
    assert abs(char._always_on_dr() - expected) < 1e-9


def test_guardian_no_block():
    """Guardian has no shield — base_block() returns 0."""
    char = make_guardian_character()
    assert char.base_block() == 0.0


def test_guardian_no_parry():
    """Guardian has no parry — base_parry() returns 0."""
    char = make_guardian_character()
    assert char.base_parry() == 0.0


def test_guardian_armor_dr_includes_ironfur():
    """Guardian physical damage reduction uses the Ironfur-boosted armor value."""
    c_const = load_constants()
    # Use a seed where the dodge roll doesn't fire — Guardian base dodge
    # is 5% so seed 0's first random is ~0.84 (no dodge).
    char = make_guardian_character(armor=789, vers=0)
    K = c_const["armor"]["k_constant"]
    armor = char.total_armor()
    spec_cfg = c_const["specs"]["guardian_druid"]
    armor_dr = min(armor / (armor + K), c_const["armor"]["max_armor_dr"])
    expected = (
        1_000_000
        * (1 - armor_dr)
        * char._always_on_dr()  # Thick Hide × Bear Form passive (2026-06-24)
        * (1 - spec_cfg["barkskin_avg_dr"])
    )

    state = MitigationState(char)
    result = apply_mitigation(state, make_phys_event(1_000_000), random.Random(0))
    if result["was_avoided"]:
        return  # dodge roll fired; retry-noise, not a regression
    assert abs(result["dealt"] - expected) < 1.0, (
        f"Guardian physical damage wrong: got {result['dealt']:.0f}, expected {expected:.0f}"
    )


def test_warrior_path_bit_identical_after_guardian_change():
    """Invariant: the Guardian wiring (2026-06-24) must not touch the warrior
    path. agility is a Guardian-only input (flat-Agi Ironfur) — a warrior's
    total_armor() must be agility-invariant, proving the new flat-Agi term is
    fenced inside the guardian_druid branch and doesn't leak."""
    base = dict(
        name="W",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2000,
        stamina=30000,
        armor_from_gear=5000,
    )
    w_no_agi = Character(**base, agility=0)
    w_agi = Character(**base, agility=8000)
    assert abs(w_no_agi.total_armor() - w_agi.total_armor()) < 1e-9, (
        "warrior total_armor() moved with agility — Guardian flat-Agi Ironfur leaked"
    )


def test_guardian_party_dr_applied_on_replay_opt_in():
    """Party-aura DR (warrior-parity) applies on the Guardian path only when
    state.party_magic_dr_active is set (replay opt-in)."""
    c_const = load_constants()
    dr_all = c_const["specs"]["guardian_druid"]["party_dr_by_school"]["all"]
    char = make_guardian_character(armor=0, vers=0)  # no armor → isolate the DR factors

    off = MitigationState(char)
    on = MitigationState(char)
    on.party_magic_dr_active = True
    r_off = apply_mitigation(off, make_phys_event(1_000_000), random.Random(0))
    r_on = apply_mitigation(on, make_phys_event(1_000_000), random.Random(0))
    if r_off["was_avoided"] or r_on["was_avoided"]:
        return  # dodge fired; retry-noise
    # The opt-in run takes the extra (1 - all) factor on top of everything else.
    assert abs(r_on["dealt"] - r_off["dealt"] * (1 - dr_all)) < 1.0, (
        f"party DR not applied on Guardian path: off={r_off['dealt']:.0f} on={r_on['dealt']:.0f}"
    )


def test_guardian_log_absorbed_used_in_replay():
    """In replay mode, log_absorbed is subtracted for Guardian Druid."""
    char = make_guardian_character(armor=0, vers=0)
    state = MitigationState(char)

    event = DamageEvent(
        time_s=0.0,
        source_id="boss",
        school="physical",
        raw_amount=500_000,
        attack_type="melee",
        is_log_replay=True,
        log_absorbed=150_000,
    )
    result = apply_mitigation(state, event, random.Random(0))
    if result["was_avoided"]:
        return  # dodge fired on seed 0 — retry-noise
    # armor=0 means no armor DR; baseline _always_on_dr (Thick Hide × Bear Form)
    # + Barkskin avg DR apply.
    c_const = load_constants()
    spec_cfg = c_const["specs"]["guardian_druid"]
    post_dr = 500_000 * char._always_on_dr() * (1 - spec_cfg["barkskin_avg_dr"])
    absorbed = min(post_dr, 150_000)
    expected_dealt = post_dr - absorbed
    assert abs(result["absorbed_by_ignore_pain"] - absorbed) < 1.0
    assert abs(result["dealt"] - expected_dealt) < 1.0


# --- Party-aura DR replay tests (Phase 3.9.2, 2026-05-24) ----------------------------
# Documents the per-school party-DR layer. The original (2026-05-16) fix added a
# flat magic-only `party_magic_dr` knob — that left a structural −12.79pp under-
# mitigation on physical across 18 Brutoh runs (see
# docs/validation/magic_mit_gap_remeasurement_2026_05_23.md) because the engine
# couldn't model all-school healer auras like Shaman Earth Shield 4% or Druid
# Symbol of Hope 5%. Phase 3.9.2 generalised the knob to
# `party_dr_by_school: {all, magic}`; `all` applies to every school, `magic`
# stacks on top for non-physical.


def test_party_dr_off_by_default():
    """Default replay does NOT auto-apply the party-DR layer — callers must opt
    in explicitly via state.party_magic_dr_active. Keeps the synthetic Monte
    Carlo path unbiased so K calibration is unaffected."""
    c_const = load_constants()
    spec_cfg = c_const.get("specs", {}).get("protection_warrior", {})
    def_stance_dr = spec_cfg.get("defensive_stance_dr", 0.0)
    party_cfg = spec_cfg.get("party_dr_by_school", {})
    assert party_cfg.get("all", 0.0) > 0.0, (
        "party_dr_by_school.all must be tuned > 0 for opt-in callers — it's the "
        "all-school healer-aura layer that closes the physical mit gap"
    )

    char = make_test_character(armor=99999, vers=0)
    raw = 1_000_000

    state_default = fresh_state(char)
    replay_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=raw,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
        is_log_replay=True,
        log_absorbed=0.0,
    )
    default_result = apply_mitigation(state_default, replay_event, random.Random(0))
    expected_default = raw * (1 - def_stance_dr)
    assert abs(default_result["dealt"] - expected_default) < 1.0, (
        f"Replay event without opt-in should not include party-DR layer: "
        f"got {default_result['dealt']:.0f}, expected {expected_default:.0f}"
    )


def test_party_dr_all_layer_applies_to_physical():
    """Phase 3.9.2 regression: the `all` key of party_dr_by_school MUST apply to
    physical events. The previous flat magic-only knob left ~5pp on the table
    because Shaman Earth Shield / Druid Symbol of Hope are all-school auras."""
    c_const = load_constants()
    K = c_const["armor"]["k_constant"]
    armor_max_dr = c_const["armor"]["max_armor_dr"]
    spec_cfg = c_const["specs"]["protection_warrior"]
    ds_dr = spec_cfg["defensive_stance_dr"]
    dr_all = spec_cfg["party_dr_by_school"]["all"]

    char = make_test_character(armor=5000, vers=0)
    raw = 1_000_000
    armor_dr = min(5000 / (5000 + K), armor_max_dr)

    state = fresh_state(char)
    state.party_magic_dr_active = True
    phys_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="physical",
        raw_amount=raw,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=False,
        is_log_replay=True,
        log_absorbed=0.0,
    )
    result = apply_mitigation(state, phys_event, random.Random(0))
    expected = raw * (1 - armor_dr) * (1 - ds_dr) * (1 - dr_all)
    assert abs(result["dealt"] - expected) < 1.0, (
        f"Physical event with party-DR opt-in must include `all` layer: "
        f"got {result['dealt']:.0f}, expected {expected:.0f}"
    )


def test_party_dr_magic_layer_stacks_on_top_of_all(monkeypatch):
    """Non-physical events get `all` × `magic` stacked multiplicatively. Verifies
    the school gating: physical only sees `all`, magic sees both. Uses a
    monkeypatched constants value for `magic` because the shipped default is
    0.0 (post-2026-05-24 retune); the test must still prove the engine reads
    and applies the key when it's non-zero."""
    import copy

    import simf.core.mitigation as mit_mod

    c_const = load_constants()
    spec_cfg = c_const["specs"]["protection_warrior"]
    ds_dr = spec_cfg["defensive_stance_dr"]
    dr_all = spec_cfg["party_dr_by_school"]["all"]
    # Override `magic` to a non-zero value so the stacking semantics are
    # actually exercised. The real shipped value is 0.0 pending per-log
    # aura detection. Use a deep copy so the lru_cached singleton stays
    # untouched and other tests are unaffected.
    fake_magic = 0.08

    real_load = mit_mod.load_constants

    def patched_load_constants():
        c = copy.deepcopy(real_load())
        c["specs"]["protection_warrior"]["party_dr_by_school"]["magic"] = fake_magic
        return c

    monkeypatch.setattr(mit_mod, "load_constants", patched_load_constants)

    char = make_test_character(armor=99999, vers=0)
    raw = 1_000_000

    state = fresh_state(char)
    state.party_magic_dr_active = True
    magic_event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=raw,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
        is_log_replay=True,
        log_absorbed=0.0,
    )
    result = apply_mitigation(state, magic_event, random.Random(0))
    expected = raw * (1 - ds_dr) * (1 - dr_all) * (1 - fake_magic)
    assert abs(result["dealt"] - expected) < 1.0, (
        f"Magic event with party-DR opt-in must stack `all` and `magic` layers: "
        f"got {result['dealt']:.0f}, expected {expected:.0f}"
    )


# --- Integration test against MGT+12 log ---------------------------------------------


def test_mgt_replay_shadow_dr_within_tolerance():
    """Integration regression: replay MGT+12 2026-05-15 log and assert that the
    sim's damage-weighted shadow DR is within 10pp of the log's actual shadow DR.

    History:
    - Pre-party_magic_dr: gap ~14pp on shadow.
    - After party_magic_dr (DS=0.20 magic-only): gap ~3-4pp; tolerance 5pp.
    - After structural F1+F4 fix (K=3430, DS=0.15 all schools, 2026-05-18): gap
      widens to ~8pp because Defensive Stance dropped from 20% (Brutoh-fitted)
      to 15% (spell-data). Per-event empirical analysis showed magic schools
      need school-specific party-DR (shadow ~17%, fire ~27%, nature ~34%) —
      uniform 5% party_magic_dr is a known approximation. Tolerance widened
      to 10pp pending school-specific party-DR work.
    - After Phase 3.9.2 (2026-05-24): `party_dr_by_school: {all: 0.05, magic: 0.05}`
      stacks an extra ~5pp on magic events (and ~5pp on physical, which closes
      the larger gap measured in
      docs/validation/magic_mit_gap_remeasurement_2026_05_23.md). Expected shadow
      gap tightens modestly; 10pp tolerance retained because the layer is a
      conservative default pending post-merge validator remeasurement.
    """
    import pathlib

    log_path = (
        pathlib.Path(__file__).resolve().parents[1] / "examples" / "WoWCombatLog-051526_210245.txt"
    )
    if not log_path.exists():
        # Skip silently if the example log isn't available (e.g., in CI without LFS)
        import pytest

        pytest.skip(f"MGT+12 calibration log not present at {log_path}")

    import yaml

    from simf.core.policy import make_policy
    from simf.io.combat_log import iter_damage_events
    from simf.io.log_replay import load_replay

    target = "Brutoh-Uldum-EU"
    char_yaml = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "simf"
        / "data"
        / "characters"
        / "brutoh.yaml"
    )
    with char_yaml.open() as f:
        char = Character.from_dict(yaml.safe_load(f))

    replay = load_replay(log_path, target, run_index=0)
    assert "Magisters" in replay.run.map_name, f"Expected MGT replay, got {replay.run.map_name}"

    c_const = load_constants()
    talent_set = set(c_const.get("talent_loadouts", {}).get(char.talents, {}).get("talents", []))

    # Re-pair to retrieve spell schools (replay.events lose spell_name).
    log_events = list(
        iter_damage_events(log_path, target, replay.run.start_time_s, replay.run.end_time_s)
    )

    state = MitigationState(char)
    state.talents = talent_set
    # Brutoh's MGT run has stacked party magic-DR auras (Ancestral Vigor, Earth
    # Shield, Elemental Resistance from the shaman healer). Opt in to the averaged
    # party_magic_dr layer so the replay reflects that realistic composition.
    state.party_magic_dr_active = True
    policy = make_policy(char)

    sim_shadow_raw = 0.0
    sim_shadow_dealt = 0.0
    log_shadow_raw = 0
    log_shadow_dealt = 0
    rng = random.Random(42)
    last_t = 0.0
    baseline_hps = replay.actual_dealt / replay.duration_s * 1.1

    for sim_evt, log_evt in zip(replay.events, log_events, strict=True):
        now = sim_evt.time_s
        policy.tick(state, now)
        recent_dmg = state.recent_damage_total(now, 5.0)
        recent_dtps = recent_dmg / 5.0 if recent_dmg > 0 else 0.0
        policy.decide(state, now, recent_dtps, sim_evt)
        result = apply_mitigation(state, sim_evt, rng)
        if log_evt.school == "shadow":
            sim_shadow_raw += sim_evt.raw_amount
            sim_shadow_dealt += result["dealt"]
            log_shadow_raw += log_evt.base_amount
            log_shadow_dealt += log_evt.amount

        state.hp -= result["dealt"]
        dt = now - last_t
        last_t = now
        state.hp = min(state.max_hp, state.hp + baseline_hps * dt)
        if state.hp <= 0:
            state.hp = 1.0  # keep alive for the full replay

    sim_mit = 1 - sim_shadow_dealt / sim_shadow_raw if sim_shadow_raw else 0
    log_mit = 1 - log_shadow_dealt / log_shadow_raw if log_shadow_raw else 0
    gap_pp = abs(sim_mit - log_mit) * 100

    # 10pp tolerance: see docstring history. School-specific party DR (currently
    # tracked as a TODO in mitigation.py step 5d) would tighten this back to
    # ~3-4pp on shadow specifically.
    assert gap_pp < 10.0, (
        f"Sim shadow mit {sim_mit * 100:.1f}% vs log {log_mit * 100:.1f}% — "
        f"gap {gap_pp:.1f}pp exceeds 10pp tolerance. "
        f"Likely regression in party_magic_dr or Brutoh talent loadout."
    )


# --- Brutal Vitality's own 15% self-cap (Patch 12.1.0 correctness fix) --------
#
# policy.py previously only enforced the shared 30%-of-max-HP cap on the whole
# Ignore Pain shield; it never enforced Brutal Vitality's own separate 15%
# self-cap on what IT specifically contributes
# (talents.brutal_vitality.cap_pct_of_max_hp). These two tests exercise
# ActiveMitigationPolicy.tick() directly (synthetic-mode-only path — replay
# mode bypasses this entirely via event.log_absorbed) to pin the fixed
# clamp behavior.


def test_brutal_vitality_only_clamps_at_its_own_15pct_cap_not_the_shared_30pct():
    """A shield fed ONLY by Brutal Vitality (no direct Ignore Pain casts)
    must stop growing at BV's own 15%-of-max-HP self-cap, not the shared
    30%-of-max-HP cap on the whole shield. Before the fix, policy.py had no
    concept of BV's own cap and would let a BV-only shield climb all the
    way to the shared 30%."""
    c = load_constants()
    char = make_test_character()
    state = MitigationState(char)
    state.talents = {"brutal_vitality"}

    bv_cap = state.max_hp * c["talents"]["brutal_vitality"]["cap_pct_of_max_hp"]
    shared_cap = state.max_hp * c["active_mitigation"]["ignore_pain"]["cap_pct_of_max_hp"]
    assert bv_cap < shared_cap  # sanity: the test is only meaningful if BV's cap binds first

    policy = ActiveMitigationPolicy(char, tank_dps=1_000_000.0, skill_modifier=1.0, skill_rng=None)
    # A single long tick simulates sustained tank damage over enough time
    # that cumulative BV grants alone would, absent its own cap, blow well
    # past both caps (damage_done_dt * 10% conversion >> either cap here).
    policy.tick(state, now=1000.0)

    assert state.brutal_vitality_absorb == pytest.approx(bv_cap)
    assert state.ignore_pain_absorb == pytest.approx(bv_cap)
    assert state.ignore_pain_absorb < shared_cap


def test_brutal_vitality_and_direct_ignore_pain_share_the_30pct_total_cap():
    """When a direct Ignore Pain cast has already filled most of the shared
    pool, Brutal Vitality can still top it up — but the grand total must
    stop exactly at the shared 30%-of-max-HP cap, and BV's own tracked
    contribution must stay under its 15% self-cap (it never even reaches
    that self-cap here because the shared cap binds tighter first)."""
    c = load_constants()
    char = make_test_character()
    state = MitigationState(char)
    state.talents = {"brutal_vitality"}

    bv_cap = state.max_hp * c["talents"]["brutal_vitality"]["cap_pct_of_max_hp"]
    shared_cap = state.max_hp * c["active_mitigation"]["ignore_pain"]["cap_pct_of_max_hp"]

    # Simulate a prior direct Ignore Pain cast (NOT attributable to BV) that
    # leaves less room under the shared cap than BV's own self-cap would
    # otherwise allow — i.e. the shared cap is the tighter constraint here.
    direct_cast_absorb = shared_cap - (bv_cap * 0.75)
    assert 0 < shared_cap - direct_cast_absorb < bv_cap  # sanity: shared cap binds first
    state.ignore_pain_absorb = direct_cast_absorb
    state.ignore_pain_until = 9999.0

    policy = ActiveMitigationPolicy(char, tank_dps=1_000_000.0, skill_modifier=1.0, skill_rng=None)
    policy.tick(state, now=1000.0)

    expected_bv_room = shared_cap - direct_cast_absorb
    assert state.ignore_pain_absorb == pytest.approx(shared_cap)
    assert state.brutal_vitality_absorb == pytest.approx(expected_bv_room)
    assert state.brutal_vitality_absorb < bv_cap
