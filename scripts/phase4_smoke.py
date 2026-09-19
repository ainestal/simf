"""Phase 4 death-rate sanity smoke.

Swaps Brutoh's stats onto each new spec (Blood DK, VDH, Brewmaster,
Guardian) and runs a +14 Fortified pull profile. Outputs death rate +
mean DTPS so the validator can spot obviously over/under-tuned chains.

Usage:
    python scripts/phase4_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from simf.core.character import Character  # noqa: E402
from simf.core.key_level_verdict import key_level_multiplier  # noqa: E402
from simf.core.profiles import (  # noqa: E402
    load_damage_profile,
    load_healing_profile,
    scale_damage_profile,
)
from simf.core.runner import run_simulation  # noqa: E402


def make_char(spec: str, talents: str) -> Character:
    """Brutoh's stats with the spec swapped."""
    return Character(
        name="Brutoh-as-" + spec,
        race="earthen",
        class_spec=spec,
        talents=talents,
        strength=2182,
        stamina=34176,
        armor_from_gear=5015,
        haste_rating=2318,
        crit_rating=1391,
        mastery_rating=1608,
        versatility_rating=296,
        max_hp_override=751872,
    )


def scale_for_fortified(profile, fortified_mult=1.30):
    """Apply Fortified non-boss multiplier (tank-busters here treated as boss
    so left at 1.0; we want a pessimistic non-boss view)."""
    # All mobs in m+_pull_caster are trash — fortified applies to all.
    return scale_damage_profile(profile, fortified_mult)


def run_spec(spec: str, talents: str, key_level: int = 14, iterations: int = 500):
    char = make_char(spec, talents)
    damage = load_damage_profile("m+_pull_caster")
    healing = load_healing_profile("m+_high_key_healer")

    # +14 multiplier × Fortified
    kl_mult = key_level_multiplier(key_level)
    damage_kl = scale_damage_profile(damage, kl_mult)
    damage_fort = scale_for_fortified(damage_kl, 1.30)

    result = run_simulation(char, damage_fort, healing, iterations=iterations, seed=42)
    return result


def main():
    cases = [
        ("protection_warrior", "brutoh-actual"),
        ("blood_death_knight", "brutoh-actual"),
        ("vengeance_demon_hunter", "brutoh-actual"),
        ("brewmaster_monk", "anonbrewmaster1-brewmaster"),
        ("guardian_druid", "anonguardian2-guardian"),
        ("protection_paladin", "default-paladin"),
    ]
    for kl in (14, 16, 18, 20):
        print(f"\n=== +{kl} Fortified ===")
        print(f"{'spec':<28} {'talents':<22} {'deaths':<8} {'dtps':<14} {'p99_5s':<14}")
        print("-" * 90)
        for spec, talents in cases:
            r = run_spec(spec, talents, key_level=kl)
            print(
                f"{spec:<28} {talents:<22} {r.death_rate * 100:5.1f}%   "
                f"{r.mean_dtps:>10,.0f}    {r.p99_5s_window:>10,.0f}"
            )


if __name__ == "__main__":
    main()
