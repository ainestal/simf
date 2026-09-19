"""Engine tests for the token-bucket healer-throughput cap (Top-5 #3,
2026-07-06 retrospective) — the headline mechanism in runner.py that gates
the trust-facing death_rate number. Validator review (2026-07-07) found
this had zero direct engine-level coverage (only the log parser was
tested), and separately found a real accounting bug: the bucket must be
charged for healing that actually LANDED (net of overheal), matching how
R/B were measured (scripts/measure_healer_budget.py nets out overhealing)
— charging the full offered/nominal amount drains the bank on overheal a
real healer's throughput was never spent on, and reintroduces the "100%
death-rate artifact" this cap exists to prevent. These tests pin both the
mechanics and the accounting fix.
"""

from __future__ import annotations

from simf.core.character import Character
from simf.core.events import DamageEvent
from simf.core.profiles import HealingProfile
from simf.core.runner import run_simulation


def _warrior(max_hp: float = 1_000_000.0) -> Character:
    return Character(
        name="test_budget",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2000,
        stamina=80000,
        armor_from_gear=5000,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=0,
        versatility_rating=0,
        max_hp_override=max_hp,
    )


def _magic_hit(time_s: float, amount: float) -> DamageEvent:
    """A school=magic, unblockable, unavoidable hit — zero RNG consumed
    (avoidance + block rolls are both gated on school=='physical' /
    is_blockable), so these scenarios are fully deterministic."""
    return DamageEvent(
        time_s=time_s,
        source_id="test",
        school="magic",
        raw_amount=amount,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )


def _run(char, events, heal, seed=1):
    return run_simulation(
        char,
        None,
        heal,
        iterations=1,
        seed=seed,
        events_override=events,
        duration_override=events[-1].time_s + 1.0,
        compute_metrics=False,
        keep_iteration_results=True,
    )


def test_isolated_burst_from_a_fresh_bank_is_fully_funded():
    """A single reactive burst with no prior draws must land exactly the
    same whether the budget is on (bank starts full) or off — "an isolated
    burst is always fully funded" is the design's own stated invariant."""
    char = _warrior()
    events = [_magic_hit(1.0, 900_000.0)]  # well below the 40% reactive threshold
    base_kwargs = dict(
        profile="test",
        baseline_hps_pct_of_dtps=0.0,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.50,
        reactive_cooldown_s=1.0,
    )
    uncapped = HealingProfile(**base_kwargs)
    capped = HealingProfile(
        **base_kwargs,
        healer_budget_capacity_pct_of_max_hp=0.50,
        healer_budget_refill_pct_of_max_hp_per_s=0.0,
    )
    hp_uncapped = _run(char, events, uncapped).iteration_results[0].final_hp_pct
    hp_capped = _run(char, events, capped).iteration_results[0].final_hp_pct
    assert abs(hp_uncapped - hp_capped) < 1e-9


def test_overheal_ticks_do_not_starve_a_later_reactive_burst():
    """Repeated baseline-heal ticks against an already-topped-off tank must
    NOT meaningfully drain the bank — charging is on effective (landed)
    healing, not the gross offered amount. Regression pin for the
    validator-confirmed bug: gross-charging drained the bank on pure
    overheal alone, so a later genuinely-needed burst got starved even
    though total REAL healing demand across the whole sequence was well
    under the bank's capacity.

    One real 50,000 hit is healed back to full (a genuine, correctly-
    charged heal) — then 9 zero-damage "heartbeat" ticks fire the same
    baseline heal against an ALREADY-FULL tank, which is pure overheal and
    must cost ~nothing. A final big hit needs the reactive burst; a bank
    sized to cover (initial real heal + burst) but NOT (10x gross nominal
    heals + burst) distinguishes correct from buggy accounting."""
    char = _warrior()
    events = (
        [_magic_hit(1.0, 50_000.0)]
        + [_magic_hit(float(i), 0.0) for i in range(2, 11)]
        + [_magic_hit(11.0, 900_000.0)]
    )
    base_kwargs = dict(
        profile="test",
        baseline_hps_pct_of_dtps=0.0,
        baseline_hps_abs=100_000.0,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.50,
        reactive_cooldown_s=1.0,
    )
    uncapped = HealingProfile(**base_kwargs)
    # 70% of max_hp = 700,000: comfortably covers the real demand (50,000
    # initial heal + the burst) IF charged correctly, but gross-charging
    # would drain it after just 7 of the 9 pure-overheal ticks alone
    # (9 x 100,000 nominal = 900,000) before the burst ever fires.
    capped = HealingProfile(
        **base_kwargs,
        healer_budget_capacity_pct_of_max_hp=0.70,
        healer_budget_refill_pct_of_max_hp_per_s=0.0,
    )
    hp_uncapped = _run(char, events, uncapped).iteration_results[0].final_hp_pct
    hp_capped = _run(char, events, capped).iteration_results[0].final_hp_pct
    assert abs(hp_uncapped - hp_capped) < 1e-6, (
        f"capped={hp_capped} vs uncapped={hp_uncapped} — the bank should not "
        "have been meaningfully depleted by overheal-only heartbeat ticks"
    )


def test_sustained_draws_are_genuinely_clipped_by_a_small_bank():
    """Positive control: a bank too small to cover real demand (even under
    correct, effective-healing accounting) must still bind — the fix must
    not have accidentally made the cap a no-op."""
    char = _warrior()
    events = [_magic_hit(1.0, 900_000.0)]
    base_kwargs = dict(
        profile="test",
        baseline_hps_pct_of_dtps=0.0,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.50,  # needs 500,000 to fully fund
        reactive_cooldown_s=1.0,
    )
    uncapped = HealingProfile(**base_kwargs)
    starved = HealingProfile(
        **base_kwargs,
        healer_budget_capacity_pct_of_max_hp=0.05,  # only 50,000 — genuinely too small
        healer_budget_refill_pct_of_max_hp_per_s=0.0,
    )
    hp_uncapped = _run(char, events, uncapped).iteration_results[0].final_hp_pct
    hp_starved = _run(char, events, starved).iteration_results[0].final_hp_pct
    assert hp_starved < hp_uncapped - 0.05, (
        "a bank far smaller than the burst it needs to fund should measurably "
        "under-heal relative to the uncapped reference"
    )


def test_budget_disabled_by_default_is_bit_identical_to_unset_profile():
    """A HealingProfile that doesn't mention the budget fields at all (every
    pre-existing profile/test fixture in the repo) must produce identical
    output to one with the fields explicitly zeroed — capacity == 0.0 is the
    documented disable sentinel."""
    char = _warrior()
    events = [_magic_hit(float(i), 20_000.0) for i in range(1, 6)] + [_magic_hit(6.0, 900_000.0)]
    kwargs = dict(
        profile="test",
        baseline_hps_pct_of_dtps=0.0,
        baseline_hps_abs=50_000.0,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.50,
        reactive_cooldown_s=1.0,
    )
    implicit = HealingProfile(**kwargs)
    explicit_zero = HealingProfile(
        **kwargs,
        healer_budget_capacity_pct_of_max_hp=0.0,
        healer_budget_refill_pct_of_max_hp_per_s=0.0,
    )
    a = _run(char, events, implicit).iteration_results[0]
    b = _run(char, events, explicit_zero).iteration_results[0]
    assert a.final_hp_pct == b.final_hp_pct
    assert a.min_hp_pct == b.min_hp_pct


def _guardian(mastery_rating: float, max_hp: float = 1_000_000.0) -> Character:
    return Character(
        name="test_guardian_budget",
        race="tauren",
        class_spec="guardian_druid",
        talents="",
        strength=0,
        armor_from_gear=2941,
        stamina=33375,
        mastery_rating=mastery_rating,
        agility=4000,
        max_hp_override=max_hp,
    )


def test_mastery_helps_even_under_a_binding_budget():
    """Regression pin for a sign-flip bug caught during this PR's own
    development (per its commit message): if the bucket were charged
    post-heal_mult (mastery-scaled) rather than nominal, a higher-mastery
    Guardian would drain the SAME finite bank faster and could end up
    WORSE off under a binding cap. Charging nominal means higher Nature's
    Guardian mastery must only ever help, never hurt, survival."""
    events = [_magic_hit(1.0, 900_000.0)]
    heal = HealingProfile(
        profile="test",
        baseline_hps_pct_of_dtps=0.0,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.50,
        reactive_cooldown_s=1.0,
        healer_budget_capacity_pct_of_max_hp=0.50,
        healer_budget_refill_pct_of_max_hp_per_s=0.0,
    )
    low_mastery = _run(_guardian(0), events, heal).iteration_results[0].final_hp_pct
    high_mastery = _run(_guardian(2000), events, heal).iteration_results[0].final_hp_pct
    assert high_mastery > low_mastery
