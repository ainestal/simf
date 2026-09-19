"""Conservation gate for the disposition ledger (2026-07-09).

Protection Warrior's `apply_mitigation` (`core/mitigation.py`) now snapshots
`damage` immediately before/after each stage of its existing
`damage *= 1 - x` chain, purely for observation — a "where your
survivability comes from" panel reads the sums, nothing in the chain
itself changed. This file is the MANDATORY gate on that claim: every
event, across a wide matrix of avoidance/block/absorb/talent/racial/DR
combinations, must satisfy

    raw == avoided_raw + blocked_cut + armor_cut + vers_cut
         + dr_layers_cut + absorbed_by_ignore_pain + absorbed_by_healer
         + dealt

within float tolerance. If this identity ever breaks, the ledger is
lying about where damage went on a calibration-critical path — treat a
failure here as a hard stop, not something to loosen the tolerance on.

Also pins `dealt` against independently-derived closed-form values (the
same style `test_mitigation.py` already uses) so a future edit that
nudges the new snapshot lines into actually *altering* `damage` — not
just reading it — gets caught even if it happens to preserve the sum
identity by coincidence.
"""

from __future__ import annotations

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState, apply_mitigation

from .conftest import magic_event, make_warrior, phys_event

TOL = 1e-6  # absolute float tolerance for the conservation identity

_DISPOSITION_KEYS = (
    "avoided_raw",
    "blocked_cut",
    "armor_cut",
    "vers_cut",
    "dr_layers_cut",
    "absorbed_by_ignore_pain",
    "absorbed_by_healer",
    "dealt",
)


def _assert_conserved(result: dict, raw: float) -> None:
    total = sum(result[k] for k in _DISPOSITION_KEYS)
    assert abs(total - raw) < TOL, (
        f"disposition chain doesn't conserve raw damage: raw={raw!r} "
        f"sum={total!r} breakdown={ {k: result[k] for k in _DISPOSITION_KEYS} }"
    )


def _talented_state(char: Character, talents: set[str]) -> MitigationState:
    s = MitigationState(char)
    s.talents = set(talents)
    return s


# ─── Property-based conservation sweep ─────────────────────────────────────

_TALENT_POOL = [
    "fight_through_flames",
    "indomitable",
    "battle_scarred_veteran",
    "brace_for_impact",
    "phalanx",
    "unyielding_stance",
]
_RACES = ["human", "dwarf", "highmountain_tauren", "earthen"]
_MAGIC_SCHOOLS = ["shadow", "fire", "frost", "nature", "arcane", "holy"]


@given(
    raw=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    armor=st.integers(min_value=0, max_value=80_000),
    vers=st.integers(min_value=0, max_value=15_000),
    seed=st.integers(min_value=0, max_value=1 << 30),
    is_physical=st.booleans(),
    school_idx=st.integers(min_value=0, max_value=len(_MAGIC_SCHOOLS) - 1),
    is_avoidable=st.booleans(),
    is_blockable=st.booleans(),
    is_bleed=st.booleans(),
    race_idx=st.integers(min_value=0, max_value=len(_RACES) - 1),
    talents=st.sets(st.sampled_from(_TALENT_POOL)),
    sb_active=st.booleans(),
    shield_wall_active=st.booleans(),
    demo_shout_active=st.booleans(),
    bsv_active=st.booleans(),
    party_dr_active=st.booleans(),
    healer_dr_active=st.booleans(),
    ip_absorb=st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    healer_absorb=st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    bfi_stacks=st.integers(min_value=0, max_value=4),
)
@settings(max_examples=400, deadline=None)
def test_disposition_conserves_raw_damage_across_the_matrix(
    raw,
    armor,
    vers,
    seed,
    is_physical,
    school_idx,
    is_avoidable,
    is_blockable,
    is_bleed,
    race_idx,
    talents,
    sb_active,
    shield_wall_active,
    demo_shout_active,
    bsv_active,
    party_dr_active,
    healer_dr_active,
    ip_absorb,
    healer_absorb,
    bfi_stacks,
):
    """The chain-conservation invariant must hold for EVERY combination of
    avoidance, block, armor, versatility, DR layers, and absorbs — not just
    the handful of named scenarios below. Bleeds are physical-only
    (bypasses armor/BfI in-engine); ``is_blockable``/``is_avoidable`` are
    only meaningful on physical events, matching how real DamageProfiles
    construct events (`profiles.py`), so magic draws force them False."""
    school = "physical" if is_physical else _MAGIC_SCHOOLS[school_idx]
    char = make_warrior(race=_RACES[race_idx], armor_from_gear=armor, versatility_rating=vers)
    state = _talented_state(char, talents)
    state.bfi_stacks = bfi_stacks
    if sb_active:
        state.shield_block_until = 9999.0
    if shield_wall_active:
        state.shield_wall_until = 9999.0
    if demo_shout_active:
        state.demo_shout_until = 9999.0
    if bsv_active:
        state.bsv_active_until = 9999.0
    if party_dr_active:
        state.party_magic_dr_active = True
    if healer_dr_active:
        state.healer_dr_until = 9999.0
        state.healer_dr_amount = 0.2
    state.ignore_pain_absorb = ip_absorb
    if ip_absorb > 0:
        state.ignore_pain_until = 9999.0
    state.healer_absorb = healer_absorb
    if healer_absorb > 0:
        state.healer_absorb_until = 9999.0

    event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school=school,
        raw_amount=raw,
        attack_type="melee" if is_physical else "spell",
        is_avoidable=is_avoidable if is_physical else False,
        is_blockable=is_blockable if is_physical else False,
        is_bleed=is_bleed if is_physical else False,
    )
    result = apply_mitigation(state, event, random.Random(seed))
    _assert_conserved(result, raw)
    # dealt must never exceed raw or go negative — a stronger sanity check
    # riding along with the conservation identity above.
    assert -TOL <= result["dealt"] <= raw + TOL


@given(
    raw=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    seed=st.integers(min_value=0, max_value=1 << 30),
    log_absorbed=st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100, deadline=None)
def test_disposition_conserves_in_log_replay_mode(raw, seed, log_absorbed):
    """Replay mode routes absorbs through `event.log_absorbed` instead of the
    sim's IP model (a different branch of step 9) — conservation must hold
    there too."""
    char = make_warrior(armor_from_gear=5000)
    state = _talented_state(char, set())
    event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="physical",
        raw_amount=raw,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=False,
        is_log_replay=True,
        log_absorbed=log_absorbed,
    )
    result = apply_mitigation(state, event, random.Random(seed))
    _assert_conserved(result, raw)


# ─── Named scenarios from the task brief ───────────────────────────────────
# Explicit, easy-to-read examples covering each named case: avoided, blocked
# + crit-blocked, unblocked physical, magic, absorbed by IP, absorbed by
# healer, mixed.


class _ForcedRNG:
    """Returns a fixed sequence of `.random()` values, then repeats the last."""

    def __init__(self, values: list[float]):
        self._values = values
        self._i = 0

    def random(self) -> float:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


def test_disposition_avoided_dodge():
    char = make_warrior(armor_from_gear=5000)
    state = _talented_state(char, set())
    event = phys_event(amount=1_000_000.0, is_avoidable=True)
    # First .random() call is the dodge/parry roll — force it to succeed.
    result = apply_mitigation(state, event, _ForcedRNG([0.0]))
    assert result["was_avoided"] is True
    assert result["avoided_raw"] == 1_000_000.0
    assert result["dealt"] == 0.0
    _assert_conserved(result, 1_000_000.0)


def test_disposition_avoided_spell_reflect():
    char = make_warrior(armor_from_gear=5000)
    state = _talented_state(char, set())
    state.spell_reflect_until = 9999.0
    event = magic_event(amount=1_000_000.0)
    result = apply_mitigation(state, event, random.Random(0))
    assert result["was_reflected"] is True
    assert result["avoided_raw"] == 1_000_000.0
    _assert_conserved(result, 1_000_000.0)


def test_disposition_blocked_and_crit_blocked():
    # shield_armor > 0 is required — block VALUE (not just block chance)
    # comes from the equipped shield's armor stat (`block_value_rating`);
    # `make_warrior` doesn't expose it, so build the Character directly.
    char = Character(
        name="t",
        race="earthen",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2000,
        stamina=30000,
        armor_from_gear=5000,
        shield_armor=1000,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=3000,
        versatility_rating=0,
        max_hp_override=700_000,
    )
    state = _talented_state(char, set())
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=True)
    # is_avoidable=False means step 1 never calls rng.random() at all (the
    # `event.is_avoidable and ...` guard short-circuits) — the FIRST draw
    # this event consumes is the block roll. .random() sequence: block
    # roll (succeed, low), crit-block roll (succeed, low).
    result = apply_mitigation(state, event, _ForcedRNG([0.0, 0.0]))
    assert result["was_blocked"] is True
    assert result["was_crit_blocked"] is True
    assert result["blocked_cut"] > 0.0
    _assert_conserved(result, 1_000_000.0)


def test_disposition_unblocked_physical():
    char = make_warrior(armor_from_gear=5000)
    state = _talented_state(char, set())
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=False)
    result = apply_mitigation(state, event, random.Random(0))
    assert result["was_blocked"] is False
    assert result["blocked_cut"] == 0.0
    assert result["armor_cut"] > 0.0
    _assert_conserved(result, 1_000_000.0)


def test_disposition_magic():
    char = make_warrior(armor_from_gear=80_000, versatility_rating=5000)
    state = _talented_state(char, set())
    event = magic_event(amount=1_000_000.0)
    result = apply_mitigation(state, event, random.Random(0))
    # Magic never touches armor or block.
    assert result["armor_cut"] == 0.0
    assert result["blocked_cut"] == 0.0
    assert result["vers_cut"] > 0.0
    _assert_conserved(result, 1_000_000.0)


def test_disposition_absorbed_by_ignore_pain():
    char = make_warrior(armor_from_gear=0)
    state = _talented_state(char, set())
    state.ignore_pain_absorb = 5_000_000.0
    state.ignore_pain_until = 9999.0
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=False)
    result = apply_mitigation(state, event, random.Random(0))
    assert result["absorbed_by_ignore_pain"] > 0.0
    assert result["dealt"] == 0.0
    _assert_conserved(result, 1_000_000.0)


def test_disposition_absorbed_by_healer():
    char = make_warrior(armor_from_gear=0)
    state = _talented_state(char, set())
    state.healer_absorb = 5_000_000.0
    state.healer_absorb_until = 9999.0
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=False)
    result = apply_mitigation(state, event, random.Random(0))
    assert result["absorbed_by_healer"] > 0.0
    assert result["dealt"] == 0.0
    _assert_conserved(result, 1_000_000.0)


def test_disposition_mixed_partial_ip_then_partial_healer():
    """IP absorbs part of what's left after mitigation, healer absorb
    covers part of the remainder — both non-zero, dealt still positive."""
    char = make_warrior(armor_from_gear=0)
    state = _talented_state(char, set())
    state.ignore_pain_absorb = 200_000.0
    state.ignore_pain_until = 9999.0
    state.healer_absorb = 100_000.0
    state.healer_absorb_until = 9999.0
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=False)
    result = apply_mitigation(state, event, random.Random(0))
    assert result["absorbed_by_ignore_pain"] > 0.0
    assert result["absorbed_by_healer"] > 0.0
    assert result["dealt"] > 0.0
    _assert_conserved(result, 1_000_000.0)


# ─── Pinned closed-form regression check ───────────────────────────────────
# Cross-checks `dealt` against an independent recomputation of the FULL
# chain (not just one layer, unlike the single-layer tests in
# test_mitigation.py) so a future change that makes the new snapshot lines
# actually mutate `damage` — rather than just read it — gets caught even if
# it happened to preserve the sum-conservation identity above.


def test_dealt_matches_independent_full_chain_recomputation():
    c = load_constants()
    K = c["armor"]["k_constant"]
    ds_dr = c["specs"]["protection_warrior"]["defensive_stance_dr"]
    sb_physical_dr = c["active_mitigation"]["shield_block"]["physical_dr"]

    char = make_warrior(armor_from_gear=20_000, versatility_rating=3000)
    armor_dr = min(20_000 / (20_000 + K), c["armor"]["max_armor_dr"])
    vers_dr = char.versatility_dr()

    state = _talented_state(char, set())
    state.shield_block_until = 9999.0  # SB physical-DR aura active
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=False)
    result = apply_mitigation(state, event, random.Random(0))

    expected = 1_000_000.0 * (1 - armor_dr) * (1 - vers_dr) * (1 - sb_physical_dr) * (1 - ds_dr)
    assert abs(result["dealt"] - expected) < 1.0
    _assert_conserved(result, 1_000_000.0)


# ─── Pinned byte-identical regression (before/after instrumentation) ──────
# Manually verified once (2026-07-09, not re-run automatically): loaded the
# pre-instrumentation `mitigation.py` from `git show HEAD` — this branch's
# fork point — as a second module and diffed `dealt` against the current
# (instrumented) code across 2,520 seed/event/talent/absorb combinations.
# Zero mismatches. The values below are captured from that same run and
# pinned here with EXACT equality (no tolerance) so a future change that
# makes the new snapshot lines actually *mutate* `damage` — not just read
# it — breaks a test even years after the git history that produced this
# comparison is gone.


def _pin_char() -> Character:
    return Character(
        name="t",
        race="earthen",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2000,
        stamina=34000,
        armor_from_gear=5000,
        shield_armor=900,
        haste_rating=1000,
        crit_rating=1000,
        mastery_rating=2000,
        versatility_rating=500,
        max_hp_override=750_000,
    )


def test_dealt_pinned_unblocked_physical():
    """Re-pinned 2026-07-21 (was 311368.77358881844) — Vanguard (spec
    passive, +armor = strength × 70%) now adds real bonus armor for every
    Protection Warrior, raising `_pin_char()`'s total_armor() and therefore
    lowering `dealt` on this unblocked-physical path. See
    docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md.
    Re-derived by running this exact test body against the fixed code, not
    hand-computed."""
    state = _talented_state(_pin_char(), set())
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=False)
    result = apply_mitigation(state, event, random.Random(0))
    assert result["dealt"] == 269169.71424473845


def test_dealt_pinned_blockable_sb_active():
    """Re-pinned 2026-07-21 twice, same day: 94274.45465154514 →
    64685.76634415592 (the Shield Block double-count + armor-resist-formula
    fix — see docs/validation/protwarrior_shield_block_fix_2026_07_21.md) →
    55919.06035366044 (Vanguard's unconditional spec-passive bonus armor,
    strength × 70% — see
    docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md). Both
    are deliberate, evidence-backed engine fixes, not instrumentation
    regressions. Re-derived by running this exact test body against the
    fixed code, not hand-computed."""
    state = _talented_state(_pin_char(), set())
    state.shield_block_until = 9999.0
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=True)
    result = apply_mitigation(state, event, random.Random(7))
    assert result["dealt"] == 55919.06035366044


def test_dealt_pinned_magic_with_fight_through_flames():
    state = _talented_state(_pin_char(), {"fight_through_flames"})
    event = DamageEvent(
        time_s=0.0,
        source_id="test",
        school="shadow",
        raw_amount=500_000.0,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )
    result = apply_mitigation(state, event, random.Random(3))
    assert result["dealt"] == 381004.6296296296


def test_dealt_pinned_bleed():
    state = _talented_state(_pin_char(), set())
    event = phys_event(amount=300_000.0, is_avoidable=False, is_blockable=False)
    event.is_bleed = True
    result = apply_mitigation(state, event, random.Random(1))
    assert result["dealt"] == 243194.44444444444


def test_dealt_pinned_fully_absorbed_by_ignore_pain():
    state = _talented_state(_pin_char(), set())
    state.ignore_pain_absorb = 400_000.0
    state.ignore_pain_until = 9999.0
    event = phys_event(amount=1_000_000.0, is_avoidable=False, is_blockable=False)
    result = apply_mitigation(state, event, random.Random(2))
    assert result["dealt"] == 0.0


# ─── Non-warrior specs: keys absent, not zeroed ────────────────────────────


def test_non_warrior_result_dict_has_no_disposition_keys():
    """Every other spec's apply_*_mitigation must NOT carry the new keys —
    downstream code (runner.py) relies on their absence (via `.get(...,
    0.0)`) to distinguish 'not instrumented' from 'measured zero'."""
    from simf.core.character import Character

    for spec, talents in [
        ("guardian_druid", "guardian-defensive"),
        ("brewmaster_monk", "brewmaster-defensive"),
        ("protection_paladin", "default-paladin"),
        ("blood_death_knight", "blood-defensive"),
        ("vengeance_demon_hunter", "vengeance-defensive"),
    ]:
        char = Character(
            name="t",
            race="human",
            class_spec=spec,
            talents=talents,
            strength=2000,
            stamina=80_000,
            armor_from_gear=5000,
            haste_rating=0,
            crit_rating=0,
            mastery_rating=0,
            versatility_rating=0,
            max_hp_override=1_000_000,
        )
        state = MitigationState(char)
        state.talents = set()
        event = phys_event(amount=100_000.0, is_avoidable=False, is_blockable=False)
        result = apply_mitigation(state, event, random.Random(0))
        for k in ("avoided_raw", "blocked_cut", "armor_cut", "vers_cut", "dr_layers_cut"):
            assert k not in result, f"{spec} result unexpectedly carries disposition key {k!r}"
