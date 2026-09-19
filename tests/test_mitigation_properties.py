"""Property-based tests for the engine — invariants that must hold for ALL inputs.

Hypothesis generates many random inputs in a bounded range and asserts that
fundamental invariants hold. These are the "always works" guard rail: they
catch regressions example-based tests miss (negative damage, runaway DR,
non-monotonic stat scaling, etc.).

Engine-only — no Streamlit imports here so these survive the UI rewrite.

Invariants checked:
  Character math:
    - armor_dr ∈ [0, max_armor_dr]
    - versatility_dr ∈ [0, 1]
    - max_hp > 0
    - effective_hp_physical ≥ max_hp (mitigation can only help)
    - effective_hp_magic ≥ max_hp (vers helps; armor does not)
    - Adding armor monotonically reduces or holds physical damage taken

  apply_mitigation:
    - dealt ∈ [0, raw_amount]   (mitigation never amplifies)
    - was_avoided ⇒ dealt == 0
    - magic event: armor changes don't change dealt damage
"""

from __future__ import annotations

import random

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from simf.core.mitigation import MitigationState, apply_mitigation

from .conftest import magic_event, make_warrior, phys_event

# Sensible-range strategies for tank stat blocks.
ARMOR = st.integers(min_value=0, max_value=80_000)
RATING = st.integers(min_value=0, max_value=15_000)
STAMINA = st.integers(min_value=10_000, max_value=200_000)
RAW_DAMAGE = st.floats(
    min_value=1.0,
    max_value=1e8,
    allow_nan=False,
    allow_infinity=False,
)


# ─── Character invariants ─────────────────────────────────────────────────────


@given(armor=ARMOR, vers=RATING, stamina=STAMINA)
@settings(max_examples=200, deadline=None)
def test_armor_dr_bounded(armor, vers, stamina):
    """armor_dr must land in [0, max_armor_dr=0.85] regardless of stat block."""
    char = make_warrior(armor_from_gear=armor, versatility_rating=vers, stamina=stamina)
    dr = char.armor_dr()
    assert 0.0 <= dr <= 0.85 + 1e-9, f"armor_dr={dr} out of bounds (armor={armor})"


@given(vers=RATING)
@settings(max_examples=200, deadline=None)
def test_versatility_dr_bounded(vers):
    char = make_warrior(versatility_rating=vers)
    dr = char.versatility_dr()
    assert 0.0 <= dr < 1.0, f"versatility_dr={dr} not in [0, 1)"


@given(stamina=STAMINA)
@settings(max_examples=100, deadline=None)
def test_max_hp_positive(stamina):
    char = make_warrior(stamina=stamina, max_hp_override=None)
    assert char.max_hp() > 0


@given(armor=ARMOR, vers=RATING, stamina=STAMINA)
@settings(max_examples=200, deadline=None)
def test_effective_hp_physical_ge_max_hp(armor, vers, stamina):
    """Mitigation must only help — eHP_phys ≥ max_hp for any non-negative stats."""
    char = make_warrior(armor_from_gear=armor, versatility_rating=vers, stamina=stamina)
    assert char.effective_hp_physical() >= char.max_hp() - 1e-6


@given(armor=ARMOR, vers=RATING, stamina=STAMINA)
@settings(max_examples=200, deadline=None)
def test_effective_hp_magic_ge_max_hp(armor, vers, stamina):
    """Vers helps magic eHP; armor does not. Either way eHP_magic ≥ max_hp."""
    char = make_warrior(armor_from_gear=armor, versatility_rating=vers, stamina=stamina)
    assert char.effective_hp_magic() >= char.max_hp() - 1e-6


@given(armor_a=ARMOR, armor_delta=st.integers(min_value=0, max_value=20_000))
@settings(max_examples=100, deadline=None)
def test_armor_monotonic_in_physical_ehp(armor_a, armor_delta):
    """More armor must produce equal-or-greater physical eHP. Non-strict because
    of the armor-DR cap (eventually adding armor stops helping)."""
    a = make_warrior(armor_from_gear=armor_a)
    b = make_warrior(armor_from_gear=armor_a + armor_delta)
    assert b.effective_hp_physical() + 1e-6 >= a.effective_hp_physical()


# ─── apply_mitigation invariants ──────────────────────────────────────────────


@given(raw=RAW_DAMAGE, armor=ARMOR, seed=st.integers(min_value=0, max_value=1 << 30))
@settings(max_examples=150, deadline=None)
def test_dealt_bounded_phys(raw, armor, seed):
    """Physical hit: 0 ≤ dealt ≤ raw, always."""
    char = make_warrior(armor_from_gear=armor)
    state = MitigationState(char)
    result = apply_mitigation(state, phys_event(amount=raw), random.Random(seed))
    assert 0.0 <= result["dealt"] <= raw + 1e-6, (
        f"dealt={result['dealt']} out of [0, {raw}] (armor={armor}, seed={seed})"
    )


@given(raw=RAW_DAMAGE, vers=RATING, seed=st.integers(min_value=0, max_value=1 << 30))
@settings(max_examples=150, deadline=None)
def test_dealt_bounded_magic(raw, vers, seed):
    """Magic hit: 0 ≤ dealt ≤ raw, always."""
    char = make_warrior(versatility_rating=vers)
    state = MitigationState(char)
    result = apply_mitigation(state, magic_event(amount=raw), random.Random(seed))
    assert 0.0 <= result["dealt"] <= raw + 1e-6


@given(raw=RAW_DAMAGE, armor=ARMOR, seed=st.integers(min_value=0, max_value=1 << 30))
@settings(max_examples=100, deadline=None)
def test_avoidance_implies_zero_dealt(raw, armor, seed):
    """If was_avoided is True, dealt must be zero — no exceptions."""
    char = make_warrior(armor_from_gear=armor)
    state = MitigationState(char)
    # Use an avoidable event so dodge/parry can actually fire.
    from simf.core.events import DamageEvent

    event = DamageEvent(
        time_s=0.0,
        source_id="t",
        school="physical",
        raw_amount=raw,
        attack_type="melee",
        is_avoidable=True,
    )
    result = apply_mitigation(state, event, random.Random(seed))
    if result["was_avoided"]:
        assert result["dealt"] == 0.0


@given(
    raw=RAW_DAMAGE,
    armor_a=ARMOR,
    armor_b=ARMOR,
    vers=RATING,
    seed=st.integers(min_value=0, max_value=1 << 30),
)
@settings(max_examples=100, deadline=None)
def test_magic_ignores_armor(raw, armor_a, armor_b, vers, seed):
    """Magic damage must not depend on armor — only versatility."""
    assume(armor_a != armor_b)
    char_a = make_warrior(armor_from_gear=armor_a, versatility_rating=vers)
    char_b = make_warrior(armor_from_gear=armor_b, versatility_rating=vers)
    result_a = apply_mitigation(
        MitigationState(char_a), magic_event(amount=raw), random.Random(seed)
    )
    result_b = apply_mitigation(
        MitigationState(char_b), magic_event(amount=raw), random.Random(seed)
    )
    assert abs(result_a["dealt"] - result_b["dealt"]) < 1e-6, (
        f"magic dealt differs by armor: a={result_a['dealt']} b={result_b['dealt']}"
    )
