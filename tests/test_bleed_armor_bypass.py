"""ROADMAP 3.9.3 — bleed armor-bypass.

In WoW, physical-school DOT events tagged as bleeds (Rake, Rip,
Rend, Deep Wounds, Lacerate, ...) bypass armor: the engine should
NOT apply the armor curve to them. Pre-this-PR the engine ran
``armor / (armor + K)`` DR on every physical event uniformly,
over-mitigating bleeds by ~+37pp in the per-school audit
(`docs/validation/magic_mit_gap_remeasurement_2026_05_23.md`).

This file pins the post-fix behaviour across every spec's
``apply_*_mitigation`` AND through the runner. The
``DamageEvent.is_bleed`` flag is the contract;
``bleed_detection.is_bleed(spell_name, is_periodic=...)`` is how the
log-replay path sets it (periodicity required — 2026-07-18, a same-named
direct hit like a boss's "Searing Rend" swing is not a bleed).
"""

from __future__ import annotations

import random

import pytest

from simf.classes.blood_death_knight import apply_blood_dk_mitigation
from simf.classes.brewmaster_monk import apply_brewmaster_mitigation
from simf.classes.guardian_druid import apply_guardian_mitigation
from simf.classes.protection_paladin import apply_protection_paladin_mitigation
from simf.classes.vengeance_dh import apply_vengeance_dh_mitigation
from simf.core.bleed_detection import BLEED_SPELL_FRAGMENTS, is_bleed
from simf.core.character import Character
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState, apply_mitigation

# ─── bleed_detection helper contract ─────────────────────────────────────────


def test_is_bleed_recognises_every_fragment_in_the_allowlist() -> None:
    """Every fragment in the set must produce is_bleed=True when used as
    the spell name verbatim AND flagged periodic. Catches typos /
    accidental case-sensitivity drift in the allowlist."""
    for frag in BLEED_SPELL_FRAGMENTS:
        assert is_bleed(frag, is_periodic=True), f"is_bleed({frag!r}) should be True"


def test_is_bleed_handles_substring_match() -> None:
    """Real combat-log spell names often carry suffixes (e.g.
    "Vicious Wound (DR)"); the fragment-based match should still
    catch them."""
    assert is_bleed("Rake (Glyph)", is_periodic=True)
    assert is_bleed("Crippling Bleed Tick", is_periodic=True)
    assert is_bleed("Mass Rend", is_periodic=True)


def test_is_bleed_rejects_non_bleeds() -> None:
    """A non-bleed physical event (auto-attack, Shield Slam DOT) must
    NOT trip the flag — otherwise non-bleed physical loses armor DR."""
    for name in (
        "",
        "auto-attack",
        "Shield Slam",
        "Thunderclap",
        "Frost Bolt",
        "Fireball",
        "Holy Fire",
        None,
    ):
        assert not is_bleed(name, is_periodic=True)


def test_is_bleed_rejects_non_periodic_events_even_on_name_match() -> None:
    """Found 2026-07-18: a boss's direct, non-periodic "Searing Rend" /
    "Rending Gore" hit fragment-matches "Rend", but is NOT a bleed — its
    real mitigation tracks the fight's ordinary physical armor chain to
    within ~3pp, not a bleed's ~50pp bypass gap (see
    docs/validation/bleed_fragment_periodicity_gap_2026_07_18.md). WoW
    has no such thing as a one-shot bleed — is_periodic=False must
    always return False regardless of name."""
    for frag in BLEED_SPELL_FRAGMENTS:
        assert not is_bleed(frag, is_periodic=False), (
            f"is_bleed({frag!r}, is_periodic=False) should be False — "
            "a non-periodic hit can never be a bleed"
        )
    assert not is_bleed("Searing Rend", is_periodic=False)
    assert not is_bleed("Rending Gore", is_periodic=False)


def test_is_bleed_true_for_periodic_fragment_match() -> None:
    """Sanity check the positive path still fires: a genuine periodic
    tick with a bleed-fragment name is a real bleed."""
    assert is_bleed("Rend", is_periodic=True)
    assert is_bleed("Open Wound", is_periodic=True)


# ─── DamageEvent carries the flag ────────────────────────────────────────────


def test_damage_event_defaults_is_bleed_false() -> None:
    """Backward-compat: every pre-this-PR DamageEvent constructor that
    didn't pass ``is_bleed=...`` keeps is_bleed=False. Without this
    default, hundreds of test fixtures across the suite would break."""
    e = DamageEvent(
        time_s=0.0,
        source_id="t",
        school="physical",
        raw_amount=1000.0,
        attack_type="melee",
    )
    assert e.is_bleed is False


def test_damage_event_accepts_is_bleed_kw() -> None:
    e = DamageEvent(
        time_s=0.0,
        source_id="t",
        school="physical",
        raw_amount=1000.0,
        attack_type="spell",
        is_dot_tick=True,
        is_bleed=True,
    )
    assert e.is_bleed is True


# ─── Engine skips armor DR for bleed events (every spec) ─────────────────────


def _char(spec: str) -> Character:
    return Character(
        name="t",
        race="human",
        class_spec=spec,
        talents="brutoh-actual" if spec == "protection_warrior" else "default-paladin",
        strength=2000,
        stamina=50000,
        armor_from_gear=5000,
    )


def _phys(raw: float = 1_000_000.0, *, is_bleed: bool = False) -> DamageEvent:
    """Physical event with avoidance OFF (so dodge/parry don't randomise the
    test) and blockable OFF (so block doesn't randomise either)."""
    return DamageEvent(
        time_s=0.0,
        source_id="t",
        school="physical",
        raw_amount=raw,
        attack_type="spell",  # spells aren't dodgeable/parryable/blockable
        is_avoidable=False,
        is_blockable=False,
        is_dot_tick=is_bleed,
        is_bleed=is_bleed,
    )


def _run_warrior(is_bleed: bool) -> float:
    char = _char("protection_warrior")
    state = MitigationState(char)
    state.talents = set()
    rng = random.Random(0)
    result = apply_mitigation(state, _phys(is_bleed=is_bleed), rng)
    return result["dealt"]


def test_warrior_bleed_takes_more_damage_than_non_bleed_physical() -> None:
    """The directional contract: a bleed event should deal MORE damage
    than an equivalent non-bleed physical, because the engine no longer
    discounts the bleed by armor DR."""
    bleed_dealt = _run_warrior(is_bleed=True)
    non_bleed_dealt = _run_warrior(is_bleed=False)
    assert bleed_dealt > non_bleed_dealt, (
        f"bleed event dealt {bleed_dealt:.0f} but non-bleed dealt "
        f"{non_bleed_dealt:.0f} — bleed should NOT benefit from armor DR"
    )
    # Magnitude sanity: at Brutoh-baseline armor + K=3430, armor DR
    # is ~45%. So a bleed event should deal roughly 1/(1-0.45) ≈ 1.8x
    # the post-armor non-bleed value. We don't pin the exact ratio
    # (other layers — vers, DS — still apply identically), just that
    # the bleed is substantially larger.
    assert (bleed_dealt / non_bleed_dealt) > 1.4, (
        f"bleed/non-bleed ratio {bleed_dealt / non_bleed_dealt:.2f} "
        f"smaller than expected ~1.4x — armor DR may still be applying"
    )


@pytest.mark.parametrize(
    "spec,apply_fn",
    [
        ("protection_paladin", apply_protection_paladin_mitigation),
        ("blood_death_knight", apply_blood_dk_mitigation),
        ("vengeance_demon_hunter", apply_vengeance_dh_mitigation),
        ("brewmaster_monk", apply_brewmaster_mitigation),
        ("guardian_druid", apply_guardian_mitigation),
    ],
)
def test_every_spec_skips_armor_dr_for_bleeds(spec, apply_fn) -> None:
    """Cross-spec coverage: every per-spec ``apply_*_mitigation`` must
    skip armor DR when the event is a bleed. Without parametrising
    across all 5 specs, a future regression that adds a new spec
    without the gate would silently re-introduce the bug."""
    char = _char(spec)
    state = MitigationState(char)
    state.talents = set()
    rng_bleed = random.Random(0)
    rng_non_bleed = random.Random(0)
    bleed_dealt = apply_fn(state, _phys(is_bleed=True), rng_bleed)["dealt"]
    # Reset state for the second call to avoid stagger / IP / sentinel
    # carry-over from the bleed-only run.
    state2 = MitigationState(char)
    state2.talents = set()
    non_bleed_dealt = apply_fn(state2, _phys(is_bleed=False), rng_non_bleed)["dealt"]
    assert bleed_dealt > non_bleed_dealt, (
        f"{spec}: bleed event dealt {bleed_dealt:.0f}, non-bleed dealt "
        f"{non_bleed_dealt:.0f} — armor DR is still applying to bleeds"
    )


# ─── Brace for Impact also skips bleeds ──────────────────────────────────────


def test_bfi_does_not_apply_to_bleed_events() -> None:
    """Brace for Impact's per-stack physical DR must skip bleed events.
    The buff tooltip excludes bleed damage; the engine's gate at
    ``mitigation.py`` must mirror the armor-bypass logic at line 217.
    Found 2026-05-24 when adding ``policy.tick`` to the audit script
    widened the bleed-bucket engine over-mit by +1.18pp because BfI
    stacks ramped to 4 on every event including bleeds."""
    char = _char("protection_warrior")

    def _measure(is_bleed: bool, talents: set[str], bfi_stacks: int = 4) -> float:
        state = MitigationState(char)
        state.talents = talents
        state.bfi_stacks = bfi_stacks
        rng = random.Random(0)
        return apply_mitigation(state, _phys(is_bleed=is_bleed), rng)["dealt"]

    # Bleed event with BfI talent + 4 stacks must match a bleed event
    # WITHOUT BfI talent — i.e. BfI's 4% DR layer did not apply.
    bleed_with_bfi = _measure(is_bleed=True, talents={"brace_for_impact"}, bfi_stacks=4)
    bleed_without_bfi = _measure(is_bleed=True, talents=set(), bfi_stacks=4)
    assert abs(bleed_with_bfi - bleed_without_bfi) < 1.0, (
        f"BfI must not reduce bleed damage: with-talent dealt "
        f"{bleed_with_bfi:.0f}, without dealt {bleed_without_bfi:.0f}"
    )

    # Sanity check the contract still holds for non-bleed physical:
    # 4 BfI stacks must reduce non-bleed physical by ~4%.
    non_bleed_with_bfi = _measure(is_bleed=False, talents={"brace_for_impact"}, bfi_stacks=4)
    non_bleed_without_bfi = _measure(is_bleed=False, talents=set(), bfi_stacks=4)
    ratio = non_bleed_with_bfi / non_bleed_without_bfi
    assert 0.95 < ratio < 0.97, (
        f"non-bleed BfI 4-stack ratio {ratio:.4f} should be ~0.96 "
        f"(4 × 1% multiplicative DR); got with={non_bleed_with_bfi:.0f}, "
        f"without={non_bleed_without_bfi:.0f}"
    )


# ─── log_replay populates is_bleed from the spell name ───────────────────────


def test_log_replay_sets_is_bleed_from_spell_name_fragment_match() -> None:
    """End-to-end contract: when a log event arrives with a bleed spell
    name, the constructed ``DamageEvent`` must carry ``is_bleed=True``.
    A regression that drops the call to ``is_bleed()`` in
    ``log_replay.py`` would silently re-introduce the armor-DR-on-bleeds
    bug for the entire replay surface."""
    import inspect

    from simf.io import log_replay

    src = inspect.getsource(log_replay)
    # The log-replay constructor must call ``is_bleed`` against the
    # log event's ``spell_name`` and assign to the DamageEvent's
    # ``is_bleed`` kw. Source-level assertion because the actual
    # log-parsing path is harder to drive without a fixture log.
    assert "is_bleed=is_bleed(" in src, (
        "log_replay.py no longer wires is_bleed= from the spell name — "
        "the engine's bleed-bypass would never trigger in replay mode"
    )
    # 2026-07-18: the periodicity gate must travel with it, or a same-named
    # direct hit (e.g. a boss's direct "Searing Rend" swing) silently
    # reintroduces the armor-DR-skipped-on-non-bleeds bug.
    assert "is_periodic=is_dot_tick" in src, (
        "log_replay.py's is_bleed() call lost its is_periodic= gate — "
        "a direct hit sharing a bleed's name would wrongly bypass armor DR"
    )


def test_wcl_replay_sets_is_bleed_gated_on_tick_flag() -> None:
    """Same contract as log_replay, for the WCL import path (used by
    Blood DK / VDH calibration): is_bleed must be gated on tick_flag,
    not derived from spell name alone."""
    import inspect

    from simf.io import wcl_replay

    src = inspect.getsource(wcl_replay)
    assert "is_bleed=is_bleed(" in src, (
        "wcl_replay.py no longer wires is_bleed= from the spell name"
    )
    assert "is_periodic=dte.tick_flag" in src, (
        "wcl_replay.py's is_bleed() call lost its tick_flag gate — a "
        "direct hit sharing a bleed's name would wrongly bypass armor DR"
    )
