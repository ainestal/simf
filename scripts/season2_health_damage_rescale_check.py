"""Season 2 "+25% player health / +25% creature damage" rescale sanity check.

Blizzard confirmed a global +25% player health / +25% creature damage
rescale at max level for Season 2 (official forum post, cross-checked
2026-07-26 — see ROADMAP.md's Season 2 Readiness section). This script
answers the launch-readiness question directly: does simf's model, run
through that exact rescale, produce the same death_rate/eHP-derived
metrics as an unscaled run? A "no" would mean the rescale needs real
engine changes before Season 2 launch, not just new dungeon data.

Runs paired (common-random-number, same seed) sims against the same
character + damage profile at two comparisons:

  1. baseline vs scaled_naive — character max_hp x1.25, damage profile x1.25
     (mob swings + casts + tank-busters via ``scale_damage_profile``),
     healing profile UNCHANGED.
  2. scaled_naive vs scaled_consistent — same scaled character/damage, but
     the healing profile's ABSOLUTE ``HealingExternal.amount`` fields (flat
     absorbs in raw HP units, e.g. 8,000,000) are ALSO scaled x1.25.

Everything else in ``HealingProfile`` (``baseline_hps_pct_of_dtps``,
``reactive_burst_pct_of_max_hp``, the healer-budget fields) is already
expressed as a percentage of DTPS/max_hp, so it self-scales automatically
under a uniform rescale — the two ``amount`` fields in
``m+_high_key_healer.yaml`` are the ONLY absolute-valued quantities in the
default profile pairing that don't.

A real methodological gotcha found while writing this check: scaling
``Character.max_hp()`` (the post-talent-multiplier value) instead of the
raw ``max_hp_override`` field double-applies the Indomitable talent's +4%
max-HP multiplier on the scaled character (``max_hp()`` re-applies it).
Always scale the raw field.

Usage::

    python scripts/season2_health_damage_rescale_check.py
    python scripts/season2_health_damage_rescale_check.py --stress-mult 2.2
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from simf.core.character import Character
from simf.core.profiles import (
    HealingProfile,
    load_damage_profile,
    load_healing_profile,
    scale_damage_profile,
)
from simf.core.runner import run_simulation

ITERATIONS = 5000
SEED = 42
SCALE = 1.25

BRUTOH = Character(
    name="Brutoh",
    race="earthen",
    class_spec="protection_warrior",
    talents="kiratank-defensive",
    strength=2182,
    stamina=34176,
    armor_from_gear=5015,
    haste_rating=1020,
    crit_rating=640,
    mastery_rating=1608,
    versatility_rating=160,
    max_hp_override=751872,
)


def _scale_healing_profile_externals(hp: HealingProfile, scale: float) -> HealingProfile:
    scaled_externals = [
        replace(e, amount=(e.amount * scale if e.amount is not None else None))
        for e in hp.externals
    ]
    return replace(hp, externals=scaled_externals)


def _run(label: str, char: Character, dmg, heal: HealingProfile):
    r = run_simulation(char, dmg, heal, iterations=ITERATIONS, seed=SEED)
    print(
        f"{label:22s}  death_rate={r.death_rate * 100:6.2f}%  "
        f"mean_dtps={r.mean_dtps:12,.0f}  etmi_12={r.etmi_12:12.4f}  "
        f"p99_10s={r.p99_10s_window:12,.0f}  "
        f"p5_min_hp={(r.p5_min_hp_pct or 0) * 100:6.2f}%"
    )
    return r


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--stress-mult",
        type=float,
        default=2.2,
        help="extra damage-profile multiplier for the stress-test comparison (default 2.2x, tuned to land near a non-saturated death_rate against Brutoh's stats — raise/lower if a future constants.yaml change shifts that)",
    )
    args = ap.parse_args()

    damage_profile = load_damage_profile("m+_boss_tankbuster")
    healing_profile = load_healing_profile("m+_high_key_healer")

    # Starve the healer so a real, well-geared character has meaningful death
    # risk against the standard tank-buster profile — otherwise death_rate is
    # 0% everywhere and this check has nothing to compare.
    starved_healing = replace(
        healing_profile,
        baseline_hps_pct_of_dtps=0.95,
        healer_budget_capacity_pct_of_max_hp=1.2,
        healer_budget_refill_pct_of_max_hp_per_s=0.030,
    )

    scaled_char = replace(BRUTOH, max_hp_override=int(BRUTOH.max_hp_override * SCALE))
    scaled_damage = scale_damage_profile(damage_profile, SCALE)
    healing_consistent = _scale_healing_profile_externals(starved_healing, SCALE)

    print(
        f"Character max_hp: baseline={BRUTOH.max_hp():,.0f}  scaled={scaled_char.max_hp():,.0f}  "
        f"ratio={scaled_char.max_hp() / BRUTOH.max_hp():.4f}  (expect exactly {SCALE:.4f})"
    )
    print(f"Iterations={ITERATIONS}  seed={SEED}\n")

    print("=== Moderate intensity (m+_boss_tankbuster, unmodified) ===")
    baseline = _run("baseline", BRUTOH, damage_profile, starved_healing)
    naive = _run("scaled_naive", scaled_char, scaled_damage, starved_healing)
    consistent = _run("scaled_consistent", scaled_char, scaled_damage, healing_consistent)
    print(
        f"death_rate delta (naive vs baseline):      {(naive.death_rate - baseline.death_rate) * 100:+.2f}pp"
    )
    print(
        f"death_rate delta (consistent vs baseline): {(consistent.death_rate - baseline.death_rate) * 100:+.2f}pp"
    )
    print(
        f"mean_dtps ratio (naive / baseline):        {naive.mean_dtps / baseline.mean_dtps:.4f}  (expect {SCALE:.4f})"
    )
    print(f"etmi_12 delta (naive vs baseline):         {naive.etmi_12 - baseline.etmi_12:+.4f}")
    print(
        f"etmi_12 delta (consistent vs baseline):    {consistent.etmi_12 - baseline.etmi_12:+.4f}"
    )

    print(
        f"\n=== Stress test at {args.stress_mult}x damage — checking whether the healing profile's flat 8,000,000 absorb ever becomes the binding constraint ==="
    )
    hard_damage = scale_damage_profile(damage_profile, args.stress_mult)
    hard_damage_scaled = scale_damage_profile(damage_profile, args.stress_mult * SCALE)
    hard_baseline = _run("hard_baseline", BRUTOH, hard_damage, starved_healing)
    hard_naive = _run("hard_scaled_naive", scaled_char, hard_damage_scaled, starved_healing)
    hard_consistent = _run(
        "hard_scaled_consistent", scaled_char, hard_damage_scaled, healing_consistent
    )
    print(
        f"hard death_rate delta (naive vs baseline):        {(hard_naive.death_rate - hard_baseline.death_rate) * 100:+.2f}pp"
    )
    print(
        f"hard death_rate delta (naive vs consistent):      {(hard_naive.death_rate - hard_consistent.death_rate) * 100:+.2f}pp"
    )


if __name__ == "__main__":
    main()
