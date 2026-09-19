"""Guardian Mastery: Nature's Guardian — healing-received lever (155783).

NG's healing-taken aura scales non-percent healing & absorbs received. SimC
realizes it as spell 227034 (a proc that heals for ``received_heal ×
mastery_value`` on each non-pct heal); simf models the same effect as
``Character.incoming_healing_multiplier()``, consumed at every heal/absorb site
(runner.py baseline/external/reactive + guardian_druid.py Tooth & Claw). The
coefficient was recalibrated 0.70 → 1.54 (2026-06-27) so the at-AnonGuardian1 multiplier
matches the log-measured ×1.226 (see phase4_guardian_self_heal_mastery_2026_06_27.md).

Frenzied Regeneration (22842) is added as a reactive %-max-HP self-heal in
GuardianPolicy.decide; it is MASTERY-NEUTRAL (a pct heal, excluded from the 227034
proc) and so is deliberately NOT routed through the multiplier.

NG's max-HP aura is deliberately NOT modeled — simf's calibrated stamina→HP
already reproduces the always-on-NG-inclusive in-game sheet HP, so an explicit
max_hp() term would double-count (see phase4_guardian_mastery_hp_2026_06_25.md).
These tests pin both the new behaviour and the bit-identity of every non-Guardian path.
"""

from __future__ import annotations

import pytest

from simf.core.character import Character, apply_secondary_dr
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.profiles import HealingProfile
from simf.core.runner import run_simulation
from simf.core.survivability_weights import compute_survivability_marginals

NON_GUARDIAN_SPECS = (
    "protection_warrior",
    "protection_paladin",
    "blood_death_knight",
    "vengeance_demon_hunter",
    "brewmaster_monk",
)


def _guardian(mastery_rating: int = 668, stamina: int = 33375) -> Character:
    return Character(
        name="test_guardian",
        race="tauren",
        class_spec="guardian_druid",
        talents="",
        strength=0,
        armor_from_gear=2941,
        stamina=stamina,
        mastery_rating=mastery_rating,
        agility=4000,
    )


# ─── mastery_pct() now has its own Guardian branch ───────────────────────────


def test_mastery_pct_guardian_uses_own_base_and_conversion():
    c = load_constants()
    spec = c["specs"]["guardian_druid"]
    ch = _guardian(mastery_rating=668)
    expected = spec["base_mastery_pct"] + apply_secondary_dr(
        668 / spec["mastery_rating_per_pct_guardian"] / 100, c
    )
    assert ch.mastery_pct() == pytest.approx(expected)


def test_mastery_pct_guardian_distinct_from_warrior_fallthrough():
    # Before this change Guardian fell through to the warrior branch (base 0.12).
    # Its own branch uses base 0.08, so the same rating gives a lower mastery%.
    g = _guardian(mastery_rating=668)
    w = Character(
        name="w",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2000,
        stamina=30000,
        armor_from_gear=5000,
        mastery_rating=668,
        max_hp_override=700_000,
    )
    assert g.mastery_pct() < w.mastery_pct()


def test_mastery_pct_warrior_unchanged():
    # Regression guard: warrior branch formula must not move.
    c = load_constants()
    w = Character(
        name="w",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2000,
        stamina=30000,
        armor_from_gear=5000,
        mastery_rating=1608,
        max_hp_override=700_000,
    )
    expected = c["base"]["mastery_pct_warrior_prot"] + apply_secondary_dr(
        1608 / c["stat_conversion"]["mastery_rating_per_pct_warrior_prot"] / 100, c
    )
    assert w.mastery_pct() == pytest.approx(expected)


# ─── incoming_healing_multiplier() ───────────────────────────────────────────


def test_incoming_healing_multiplier_guardian_scales_with_mastery():
    c = load_constants()
    coeff = c["specs"]["guardian_druid"]["natures_guardian_heal_coeff"]
    ch = _guardian(mastery_rating=668)
    assert ch.incoming_healing_multiplier() == pytest.approx(1.0 + coeff * ch.mastery_pct())
    # Strictly monotonic in mastery rating.
    assert _guardian(mastery_rating=4000).incoming_healing_multiplier() > (
        _guardian(mastery_rating=0).incoming_healing_multiplier()
    )
    # Always a buff (>= 1.0) since base mastery > 0.
    assert _guardian(mastery_rating=0).incoming_healing_multiplier() > 1.0


@pytest.mark.parametrize("spec", NON_GUARDIAN_SPECS)
def test_incoming_healing_multiplier_one_for_non_guardian(spec):
    ch = Character(
        name="t",
        race="human",
        class_spec=spec,
        talents="",
        strength=2000,
        stamina=40000,
        armor_from_gear=5000,
        mastery_rating=4000,
    )
    # Exactly 1.0 (not approx) — the heal sites multiply by this, so any non-unit
    # value for a non-Guardian would silently change their results.
    assert ch.incoming_healing_multiplier() == 1.0


# ─── the lever flows through the sim ─────────────────────────────────────────


def _magic_events(n: int, raw: float) -> list[DamageEvent]:
    # Unavoidable magic hits → no dodge/block RNG → the sim is deterministic, so
    # the ONLY difference between two mastery levels is healing received.
    return [
        DamageEvent(
            time_s=float(t),
            source_id="boss",
            school="shadow",
            raw_amount=raw,
            attack_type="spell",
            is_avoidable=False,
            is_blockable=False,
        )
        for t in range(1, n + 1)
    ]


def _healing_total(mastery_rating: int) -> float:
    # raw=45000 keeps the tank in the (50%, 100%) HP band for the whole pull:
    #   - ABOVE the 50% Frenzied-Regen trigger, so FrR (a mastery-NEUTRAL
    #     %-max-HP self-heal, now recorded to heal_timeline) never fires and
    #     can't dilute the mastery signal; and
    #   - BELOW max HP throughout, so heals never overheal (clamp to 0) — in a
    #     survive-comfortably regime the tank ends near full and total
    #     *effective* healing converges between mastery levels regardless of
    #     the lever (effective heal is bounded by missing HP, which is
    #     mastery-independent). raw=28000 sat in that converged regime.
    # Here total healing = healer-model + Tooth & Claw, BOTH scaled by
    # incoming_healing_multiplier(), so the mastery lever shows cleanly (~1.47×).
    events = _magic_events(30, 45000.0)
    res = run_simulation(
        _guardian(mastery_rating=mastery_rating, stamina=55000),
        damage_profile=None,
        healing_profile=HealingProfile(
            profile="t", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=8000.0
        ),
        events_override=events,
        duration_override=31.0,
        iterations=2,
        min_iterations=1,
        seed=42,
        keep_iteration_results=True,
    )
    return res.iteration_results[0].healing_total


def test_guardian_higher_mastery_receives_more_healing():
    low = _healing_total(0)
    high = _healing_total(6000)
    assert low > 0.0
    # heal_mult ratio is ~1.37/1.056 ≈ 1.30; in the missing-HP band the observed
    # total-healing ratio is ~1.47 (high mastery also ends fuller, so more of its
    # larger heals land effectively). Assert a clear, non-flaky gap well above the
    # heal_mult floor — deterministic (magic-only, seed=42), so not flaky.
    assert high > low * 1.10


def test_non_guardian_healing_unchanged_by_mastery():
    # Warrior heal_mult is 1.0 regardless of mastery → identical healing (no leak).
    def warrior_heal_total(mastery_rating: int) -> float:
        ch = Character(
            name="w",
            race="human",
            class_spec="protection_warrior",
            talents="kiratank-defensive",
            strength=2000,
            stamina=55000,
            armor_from_gear=5000,
            mastery_rating=mastery_rating,
            max_hp_override=1_100_000,
        )
        res = run_simulation(
            ch,
            damage_profile=None,
            healing_profile=HealingProfile(
                profile="t", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=8000.0
            ),
            events_override=_magic_events(30, 28000.0),
            duration_override=31.0,
            iterations=2,
            min_iterations=1,
            seed=42,
            keep_iteration_results=True,
        )
        return res.iteration_results[0].healing_total

    assert warrior_heal_total(0) == warrior_heal_total(6000)


def test_guardian_mastery_has_positive_survival_marginal():
    # The product goal: mastery is now a survival stat the gem suggester + gear
    # surface (which use the SIM path) will value. The sim perturbs mastery_rating
    # → more effective healing → lower ETMI → positive marginal.
    w = compute_survivability_marginals(
        _guardian(mastery_rating=668), iterations=200, seed=42, n_workers=4
    )
    assert w.marginals["mastery_rating"]["p"] > 0.0


# ─── recalibration: the multiplier matches the log-measured anchor ────────────


def test_natures_guardian_multiplier_matches_raw_mastery_value():
    # Recalibrated 2026-06-27 (constants_version 29): SimC heals 227034 for
    # `received_heal × mastery_value` (coeff 1.0, not 0.7), so the multiplier is
    # `1 + mastery_value ≈ 1.147` at AnonGuardian1's 668 mastery. The raw coefficient
    # (~0.147) was confirmed by a proc-pairing measurement on the logs; the
    # whole-fight EFFECTIVE ratio (×1.226) is overheal-inflated and is deliberately
    # NOT used (it would over-credit a clamped offered stream). Pin the corrected
    # anchor and that it exceeds the pre-recal ×1.103.
    ch = _guardian(mastery_rating=668)
    assert ch.incoming_healing_multiplier() == pytest.approx(1.147, abs=0.005)
    assert ch.incoming_healing_multiplier() > 1.103  # above the old 0.7-coeff value


# ─── Frenzied Regeneration (22842) — reactive, mastery-NEUTRAL self-heal ──────


def _state_at_hp_pct(ch: Character, hp_pct: float):
    from simf.core.mitigation import MitigationState

    state = MitigationState(ch)
    state.hp = hp_pct * state.max_hp
    return state


def test_frr_fires_when_low_and_heals():
    from simf.classes.guardian_druid import GuardianPolicy

    ch = _guardian()
    # 45% HP: below FrR's 50% trigger but ABOVE Incarnation's 40%, so the heal we
    # observe is FrR alone (no Incarnation confound).
    state = _state_at_hp_pct(ch, 0.45)
    pol = GuardianPolicy(ch)
    before = state.hp
    pol.decide(state, now=10.0, recent_dtps=0.0)
    assert state.hp > before  # FrR healed
    assert state.hp <= state.max_hp  # never overheals (clamp)
    # exactly one of the two charges consumed (set to now + recharge)
    assert sum(1 for t in pol.frr_charges_ready_at if t <= 10.0) == 1


def test_frr_does_not_fire_when_healthy():
    from simf.classes.guardian_druid import GuardianPolicy

    ch = _guardian()
    state = _state_at_hp_pct(ch, 0.80)  # above the 50% trigger
    pol = GuardianPolicy(ch)
    before = state.hp
    pol.decide(state, now=10.0, recent_dtps=0.0)
    assert state.hp == before  # no FrR (and no Incarnation at 80%)
    assert all(t < 0 for t in pol.frr_charges_ready_at)  # no charge consumed


def test_frr_capped_by_charges_within_recharge():
    from simf.classes.guardian_druid import GuardianPolicy

    ch = _guardian()
    spec = load_constants()["specs"]["guardian_druid"]["frenzied_regeneration"]
    pol = GuardianPolicy(ch)
    fires = 0
    # Hammer the policy at 45% HP every second for less than one recharge window:
    # only `charges` presses should land before any charge recharges.
    for t in range(0, int(spec["recharge_s"]) - 1):
        state = _state_at_hp_pct(ch, 0.45)
        before = state.hp
        pol.decide(state, now=float(t), recent_dtps=0.0)
        if state.hp > before:
            fires += 1
    assert fires == spec["charges"]


def test_frr_is_mastery_neutral():
    from simf.classes.guardian_druid import GuardianPolicy

    # FrR is a percent-of-max-HP heal, which the 227034 mastery proc excludes, so
    # its magnitude (as a fraction of max HP) must NOT change with mastery rating.
    def frr_frac(mastery: int) -> float:
        ch = _guardian(mastery_rating=mastery)
        state = _state_at_hp_pct(ch, 0.45)
        pol = GuardianPolicy(ch)
        before = state.hp
        pol.decide(state, now=10.0, recent_dtps=0.0)
        return (state.hp - before) / state.max_hp

    assert frr_frac(0) == pytest.approx(frr_frac(6000))
    assert frr_frac(0) > 0.0  # it did fire


def test_frr_absent_constants_block_is_noop():
    # Defensive: a GuardianPolicy built when the constants lack the FrR block has
    # no charges and never heals via FrR.
    from simf.classes.guardian_druid import GuardianPolicy

    ch = _guardian()
    pol = GuardianPolicy(ch)
    pol.frr_charges_ready_at = []  # simulate absent block
    state = _state_at_hp_pct(ch, 0.45)
    before = state.hp
    pol.decide(state, now=10.0, recent_dtps=0.0)
    assert state.hp == before
