"""Unit tests for block via the armor curve (audit F3 + F2 + F12).

F3 (numerical fix): crit block uses `calculate_armor_resist(value, K, multiplier=2.0)`,
clamped at 0.85 (MAX_ARMOR_DAMAGE_REDUCTION). simf's prior
`min(block_value * 2, 1.0)` allowed full immunity when block_value > 0.5
— off by up to 15pp on a crit-blocked event.

F12 (2026-05-20): block value is now sourced from shield armor per the
SimC formula `block_value = shield.armor × 2.5` (engine/player/player.cpp:1681)
and runs through `calculate_armor_resist` with multiplier=1.0 (regular)
or 2.0 (crit). The pre-F12 `0.30 + mastery × 0.5` flat-derivation is
replaced — see character.py:block_value_rating().
"""

from __future__ import annotations

import random

import pytest

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.mitigation import (
    MAX_ARMOR_DAMAGE_REDUCTION,
    MitigationState,
    apply_mitigation,
    calculate_armor_resist,
)


def _prot_warrior(mastery_rating: int = 0, shield_armor: int = 989) -> Character:
    """Default shield_armor matches Brutoh's Spellbreaker's Rebuke at ilvl 295
    post-voidcore (in-game tooltip 2026-05-20) so the block path produces
    realistic numbers. Override to 0 to model non-shield specs."""
    return Character(
        name="Test",
        race="human",
        class_spec="protection_warrior",
        talents="",
        strength=10_000,
        stamina=50_000,
        armor_from_gear=20_000,
        mastery_rating=mastery_rating,
        shield_armor=shield_armor,
    )


# ---- calculate_armor_resist helper -------------------------------------------


def test_armor_resist_matches_simple_formula_below_cap():
    """At sub-cap rating, calculate_armor_resist == value / (value + K)."""
    assert calculate_armor_resist(2000, 3430) == pytest.approx(2000 / (2000 + 3430))


def test_armor_resist_clamps_at_max_armor_dr():
    """At very high rating, result clamps at 0.85."""
    assert calculate_armor_resist(1_000_000, 3430) == pytest.approx(0.85)


def test_armor_resist_multiplier_two_doubles_the_value():
    """Crit-block path: multiplier=2.0 doubles the RESIST FRACTION, applied
    AFTER the armor-curve division — per SimC's real `util::calculate_armor_resist`
    (fetched fresh from GitHub 2026-07-21, `engine/util/util.cpp`, branch
    `midnight`): `resist = armor/(armor+coeff); resist *= multiplier;`. The
    previous implementation doubled the INPUT value before dividing — a
    different, lower-magnitude curve for any multiplier != 1.0. See
    docs/validation/protwarrior_shield_block_fix_2026_07_21.md."""
    bv = 2000
    expected = (bv / (bv + 3430)) * 2
    assert calculate_armor_resist(bv, 3430, multiplier=2.0) == pytest.approx(expected)


def test_armor_resist_multiplier_clamps_at_max_armor_dr():
    """Even with multiplier=2.0 and high rating, result clamps at 0.85."""
    assert calculate_armor_resist(10_000, 3430, multiplier=2.0) == pytest.approx(0.85)


def test_armor_resist_zero_value_is_zero_dr():
    """No armor / no block value → 0% DR."""
    assert calculate_armor_resist(0, 3430) == 0.0


# ---- block_value_rating = shield_armor × 2.5 (F12) ---------------------------


def test_block_value_rating_is_shield_armor_times_2_5():
    """F12: block value is the shield item's armor stat times 2.5.

    SimC engine/player/player.cpp:1681 — `block_value = shield.armor × 2.5`.
    """
    c = load_constants()
    mult = c["base"]["block_value_armor_multiplier"]
    char = _prot_warrior(shield_armor=989)
    assert char.block_value_rating() == pytest.approx(989 * mult, abs=1e-9)


def test_block_value_rating_is_zero_when_no_shield():
    """Brewmaster fist weapons / Vengeance warglaives / unconfigured fixtures
    have shield_armor=0 and so block_value_rating is 0 (block roll succeeds
    but yields 0% DR)."""
    char = _prot_warrior(shield_armor=0)
    assert char.block_value_rating() == 0.0


def test_block_value_rating_scales_linearly_with_shield_armor():
    """Doubling shield armor doubles block_value_rating exactly — the formula
    is linear pre-curve. The armor curve then de-linearizes the DR."""
    base = _prot_warrior(shield_armor=500).block_value_rating()
    double = _prot_warrior(shield_armor=1000).block_value_rating()
    assert double == pytest.approx(2 * base, abs=1e-9)


# ---- crit block: F3 shape change ---------------------------------------------


def test_crit_block_caps_at_max_armor_dr_not_one():
    """F3: at high block value, legacy code would full-immune; the SimC-correct
    armor curve caps at 0.85.

    bv_rating is constructed so the base (uncrit) armor-curve fraction is
    EXACTLY bv_pct (bv_rating = bv_pct*K/(1-bv_pct) is the inverse of
    armor/(armor+K)). At bv_pct=0.60, doubling gives 1.20 — clamped to 0.85
    by MAX_ARMOR_DAMAGE_REDUCTION, same as SimC's `clamp(resist, 0, 0.85)`.
    """
    c = load_constants()
    K = c["armor"]["k_constant"]
    bv_pct = 0.60
    bv_rating = bv_pct * K / (1 - bv_pct)
    crit_dr = calculate_armor_resist(bv_rating, K, multiplier=2.0)
    assert crit_dr == pytest.approx(0.85, abs=1e-9)
    # Legacy (pre-F2/F3, flat-% × 2 clamped to 1.0) would have given 1.0 — a
    # full-immunity reading the real game never grants on a crit block.
    legacy_crit_dr = min(bv_pct * 2, 1.0)
    assert crit_dr < legacy_crit_dr


def test_crit_block_below_cap_equals_flat_double():
    """Below the point where 2×(base fraction) would exceed 0.85, the
    SimC-correct crit-block DR is EXACTLY the base block fraction doubled —
    the armor curve and a flat-% doubling coincide once the multiplier is
    applied to the fraction (not the input value), matching SimC's real
    `resist = armor/(armor+coeff); resist *= multiplier` order of operations.
    They diverge only once doubling would exceed the 0.85 cap (see
    test_crit_block_caps_at_max_armor_dr_not_one)."""
    c = load_constants()
    K = c["armor"]["k_constant"]
    bv_pct = 0.36  # well under the 0.425 threshold where 2x would hit 0.85
    bv_rating = bv_pct * K / (1 - bv_pct)
    new_crit_dr = calculate_armor_resist(bv_rating, K, multiplier=2.0)
    assert new_crit_dr == pytest.approx(bv_pct * 2, abs=1e-9)
    assert new_crit_dr == pytest.approx(0.72, abs=1e-3)


# ---- Character.block_value_rating sanity checks ------------------------------


def test_block_value_rating_is_positive_for_prot_warrior():
    char = _prot_warrior()
    assert char.block_value_rating() > 0


def test_block_value_rating_independent_of_mastery():
    """F12 (post-2026-05-20): mastery does NOT scale block value in SimC —
    mastery_block_value_scaling is 0.0. Mastery still affects block chance
    (deferred F13) and crit-block chance (F7). See sc_warrior.cpp 8898/8910/8990.
    """
    base = _prot_warrior(mastery_rating=0).block_value_rating()
    plus = _prot_warrior(mastery_rating=1500).block_value_rating()
    assert base == pytest.approx(plus, abs=1e-12)


def test_mastery_block_value_scaling_constant_is_zero():
    """F12 (2026-05-20): mastery_block_value_scaling is 0.0 (SimC truth).

    The 0.5 fudge was load-bearing pre-F12 — it absorbed the missing
    shield-armor contribution. F12 sources block_value from shield armor
    directly, so the fudge is no longer needed.
    """
    c = load_constants()
    assert c["base"]["mastery_block_value_scaling"] == 0.0


def test_block_value_armor_multiplier_constant_is_two_point_five():
    """SimC engine/player/player.cpp:1681 — the 2.5 coefficient on shield armor."""
    c = load_constants()
    assert c["base"]["block_value_armor_multiplier"] == 2.5


def test_base_block_scales_with_mastery_under_dr_curve():
    """F13 + F14 (2026-05-23): mastery → block chance with coefficient 0.5,
    THEN the bonus portion flows through composite_block_dr.

    SimC sc_warrior.cpp:8893 adds `cache.mastery() × effectN(2).mastery_value()`
    to block chance. effectN(2).mastery_value() = 0.5 per spell 76857.
    +1000 mastery rating ≈ +10pp mastery_pct (well below the secondary-stat
    DR breakpoint), so the pre-DR bonus increment is `0.10 × 0.5 = +5pp`.
    F14 then applies the player.cpp:5467 curve to the WHOLE bonus block
    (base + mastery contribution), so the visible delta is smaller than 5pp.
    """
    from simf.core.character import _composite_block_dr

    c = load_constants()
    base_bonus = c["base"]["mastery_pct_warrior_prot"] * c["base"]["mastery_block_chance_scaling"]
    base_char = _prot_warrior(mastery_rating=0)
    plus_char = _prot_warrior(mastery_rating=1000)
    plus_bonus = (
        base_bonus + 0.10 * c["base"]["mastery_block_chance_scaling"]
    )  # +10pp mastery × 0.5
    expected_delta = _composite_block_dr(plus_bonus, c) - _composite_block_dr(base_bonus, c)
    assert (plus_char.base_block() - base_char.base_block()) == pytest.approx(
        expected_delta, abs=1e-9
    )
    # Sanity: post-DR delta is positive but strictly less than the pre-DR 5pp.
    assert 0 < expected_delta < 0.05


def test_base_block_at_zero_mastery_is_dr_curve_value():
    """F14: with 0 mastery rating, base_block = block_chance + composite_block_dr(base_mastery × 0.5)."""
    from simf.core.character import _composite_block_dr

    c = load_constants()
    char = _prot_warrior(mastery_rating=0)
    base_bonus = c["base"]["mastery_pct_warrior_prot"] * c["base"]["mastery_block_chance_scaling"]
    expected = c["base"]["block_chance"] + _composite_block_dr(base_bonus, c)
    assert char.base_block() == pytest.approx(expected, abs=1e-9)
    # Sanity: the pre-DR (flat-linear) baseline used by the old F13 expectation
    # was strictly larger — F14 took ~0.6pp out at this stack.
    pre_dr_baseline = c["base"]["block_chance"] + base_bonus
    assert char.base_block() < pre_dr_baseline
    assert (pre_dr_baseline - char.base_block()) < 0.01  # less than 1pp at base mastery


def test_uses_shield_true_for_shield_specs():
    """Protection Warrior/Paladin are the only specs with a nonzero
    base_block — and the only ones the gear recommender should refuse
    two-handed main-hand candidates for (2026-07-04 fix)."""
    warrior = _prot_warrior()
    assert warrior.uses_shield is True
    paladin = Character(
        name="Test",
        race="human",
        class_spec="protection_paladin",
        talents="",
        strength=10_000,
        stamina=50_000,
        armor_from_gear=20_000,
    )
    assert paladin.uses_shield is True


def test_uses_shield_false_for_non_shield_specs():
    dk = Character(
        name="Test",
        race="human",
        class_spec="blood_death_knight",
        talents="",
        strength=10_000,
        stamina=50_000,
        armor_from_gear=20_000,
    )
    assert dk.uses_shield is False


# ─── F14 block-chance diminishing returns (SimC player.cpp:5467) ──────────────


def test_f14_dr_constants_match_simc_dbc_warrior_class_id():
    """Anchor the DR constants to the SimC DBC values for the Warrior class.

    Source: engine/dbc/sc_extra_data.inc lines 1807-1818 in the simc/midnight
    branch (build 12.0.5). The Warrior class_id=1 entry holds the same values
    every other modelled tank class carries — `block_factor: 1.0`,
    `block_vertical_stretch: 0.0067`, `horizontal_shift: 1/0.94`.
    """
    c = load_constants()
    dr = c["base"]["block_chance_diminishing_returns"]
    assert dr["block_factor"] == pytest.approx(1.0, abs=1e-12)
    assert dr["block_vertical_stretch"] == pytest.approx(0.0067, abs=1e-12)
    assert dr["horizontal_shift"] == pytest.approx(1 / 0.94, abs=1e-9)


def test_f14_composite_block_dr_matches_simc_formula_at_brutoh_stack():
    """A reproduction of SimC's player.cpp:5467 formula at Brutoh's mastery
    stack — the regression target for F14. Brutoh's mastery_pct sits around
    25%, so the bonus_block contribution is ~12.5%. The DR formula should
    cut it down to ~10.9%."""
    from simf.core.character import _composite_block_dr

    c = load_constants()
    bonus = 0.125
    dr = c["base"]["block_chance_diminishing_returns"]
    expected = bonus / (
        dr["block_factor"] * bonus * 100 * dr["block_vertical_stretch"] + dr["horizontal_shift"]
    )
    assert _composite_block_dr(bonus, c) == pytest.approx(expected, abs=1e-12)
    # Sanity: at Brutoh's stack F14 takes roughly 1.5pp out.
    assert 0.10 < _composite_block_dr(bonus, c) < 0.12


def test_f14_composite_block_dr_is_zero_when_bonus_is_zero():
    """SimC guards the DR application with `if (bonus_block > 0)`. Mirror it
    so a zero-mastery, zero-block-rating Warrior contributes nothing from
    the DR'd portion (only the flat ``block_chance`` baseline remains)."""
    from simf.core.character import _composite_block_dr

    c = load_constants()
    assert _composite_block_dr(0.0, c) == 0.0
    assert _composite_block_dr(-0.05, c) == 0.0  # negative input also short-circuits


@pytest.mark.parametrize(
    "bonus",
    [0.05, 0.10, 0.20, 0.30, 0.50, 1.00],
)
def test_f14_composite_block_dr_increases_monotonically_but_is_capped_by_curve(bonus: float):
    """Across the realistic + extreme range of bonus_block, the post-DR
    contribution is monotonically increasing AND strictly less than the
    pre-DR value (DR is always a loss, never a gain)."""
    from simf.core.character import _composite_block_dr

    c = load_constants()
    diminished = _composite_block_dr(bonus, c)
    larger = _composite_block_dr(bonus + 0.01, c)
    assert diminished < bonus, "DR must not increase bonus block"
    assert larger > diminished, "DR curve must be monotonic in bonus_block"
    # Asymptote: as bonus → ∞ the diminished value approaches
    # 1 / (block_factor × 100 × vertical_stretch) ≈ 1/0.67 ≈ 1.493 — but
    # we never sample anywhere near that here, every finite call should
    # land well under 1.493.
    asymptote = 1.0 / (1.0 * 100 * 0.0067)
    assert diminished < asymptote


def test_mastery_block_chance_scaling_constant_is_zero_point_five():
    """F13 (2026-05-20): mastery_block_chance_scaling = 0.5 (SimC effectN(2).mastery_value())."""
    c = load_constants()
    assert c["base"]["mastery_block_chance_scaling"] == 0.5


def test_max_armor_damage_reduction_constant_matches_yaml():
    """The hot-path MAX_ARMOR_DAMAGE_REDUCTION constant must match constants.yaml."""
    c = load_constants()
    assert c["armor"]["max_armor_dr"] == MAX_ARMOR_DAMAGE_REDUCTION


# ---- Integration: apply_mitigation block path wires F3 correctly -------------


def _block_test_character() -> Character:
    """Deterministic Prot Warrior with zero armor and zero vers so the block
    path is the only DR layer (besides Defensive Stance, which we factor out)."""
    return Character(
        name="block-test",
        race="human",
        class_spec="protection_warrior",
        talents="",
        strength=0,
        stamina=80_000,
        armor_from_gear=0,
        mastery_rating=1500,  # 15% mastery → 15% crit-block chance (F7 1:1)
        versatility_rating=0,
        shield_armor=931,  # Spellbreaker's Rebuke — F12 block_value source
        max_hp_override=8_000_000,
    )


def _block_event(amount: float = 1_000_000) -> DamageEvent:
    return DamageEvent(
        time_s=0.0,
        source_id="test",
        school="physical",
        raw_amount=amount,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=True,
    )


def _force_block_state(char: Character, crit_block: bool) -> MitigationState:
    """A MitigationState where the block path always fires and crit-block
    deterministically lands or doesn't."""
    state = MitigationState(char)
    state.talents = set()
    # Force base block to 1.0 so the first rng.random() < block_chance always succeeds.
    state.cached_base_block = 1.0
    # Force crit-block chance to either 1.0 or 0.0 to skip the rng coin-flip.
    state.cached_critical_block_chance = 1.0 if crit_block else 0.0
    return state


def test_apply_mitigation_regular_block_matches_armor_curve():
    """Wired-up regular block: dealt damage = raw × (1 − curve(BV_rating, K)) × DS."""
    char = _block_test_character()
    state = _force_block_state(char, crit_block=False)
    c = load_constants()
    K = c["armor"]["k_constant"]
    ds_dr = c["specs"]["protection_warrior"]["defensive_stance_dr"]

    expected_block_dr = calculate_armor_resist(char.block_value_rating(), K, 1.0)
    # Versatility = 0, armor = 0 → only block × DS applies.
    expected_dealt = 1_000_000 * (1 - expected_block_dr) * (1 - ds_dr)

    result = apply_mitigation(state, _block_event(), random.Random(0))
    assert result["was_blocked"] is True
    assert result["was_crit_blocked"] is False
    assert result["dealt"] == pytest.approx(expected_dealt, rel=1e-6)


def test_apply_mitigation_crit_block_matches_armor_curve_with_multiplier_two():
    """Wired-up crit block: dealt damage = raw × (1 − curve(BV_rating, K, m=2)) × DS.

    2026-07-21 fix: the multiplier scales the RESIST FRACTION (post-division),
    matching SimC's real `util::calculate_armor_resist`. At this fixture's
    shield_armor=931 (block_value_rating ≈2327.5, base fraction ≈0.404), crit
    block DR ≈0.809 — nearly double the regular-block fraction, well under
    the 0.85 cap. See docs/validation/protwarrior_shield_block_fix_2026_07_21.md.
    """
    char = _block_test_character()
    state = _force_block_state(char, crit_block=True)
    c = load_constants()
    K = c["armor"]["k_constant"]
    ds_dr = c["specs"]["protection_warrior"]["defensive_stance_dr"]

    expected_crit_block_dr = calculate_armor_resist(char.block_value_rating(), K, 2.0)
    expected_dealt = 1_000_000 * (1 - expected_crit_block_dr) * (1 - ds_dr)

    result = apply_mitigation(state, _block_event(), random.Random(0))
    assert result["was_blocked"] is True
    assert result["was_crit_blocked"] is True
    assert result["dealt"] == pytest.approx(expected_dealt, rel=1e-6)
    # Sanity: crit-block DR is strictly greater than regular block (multiplier=2),
    # and — since the base fraction here is ~0.40 — is exactly double it,
    # not yet clamped at 0.85.
    expected_block_dr = calculate_armor_resist(char.block_value_rating(), K, 1.0)
    assert expected_crit_block_dr > expected_block_dr
    assert expected_crit_block_dr == pytest.approx(2 * expected_block_dr, abs=1e-9)
    # NOTE: at this fixture's real (non-toy) shield_armor, 2x the base fraction
    # (~0.809) is now numerically LARGER than the old placeholder legacy
    # flat-30%-doubled figure (0.60) — unlike the smaller-bv_pct=0.36 case in
    # test_crit_block_below_cap_equals_flat_double above. The old "legacy is
    # always an upper bound" framing only holds relative to the SPECIFIC
    # flat-% legacy formula being compared against, not as a general law —
    # removed the comparison here rather than assert something misleading.
