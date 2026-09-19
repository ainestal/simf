from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .constants import DATA_DIR
from .events import AttackType, DamageSchool


@dataclass
class MobSpec:
    count: int
    swing_timer_s: float
    swing_damage_mean: float
    swing_damage_variance: float
    school: DamageSchool
    attack_type: AttackType = "melee"
    casts: list[dict] = field(default_factory=list)


@dataclass
class TankBuster:
    time_s: float
    damage: float
    school: DamageSchool
    attack_type: AttackType = "spell"
    is_avoidable_by_spell_reflect: bool = False


@dataclass
class DamageProfile:
    profile: str
    duration_s: float
    mobs: list[MobSpec]
    tank_busters: list[TankBuster] = field(default_factory=list)
    affix: dict = field(default_factory=dict)


@dataclass
class HealingExternal:
    time_s: float
    type: str  # dr_cooldown | absorb | heal
    amount_pct: float | None = None
    amount: float | None = None
    duration_s: float = 0.0


@dataclass
class HealingProfile:
    profile: str
    baseline_hps_pct_of_dtps: float
    externals: list[HealingExternal] = field(default_factory=list)
    # Reactive healing — healer dumps a burst when tank drops below threshold.
    # Models the real M+ pattern of a healer responding to spike damage.
    reactive_threshold_hp_pct: float = 0.0  # 0 disables; 0.30 = trigger when HP < 30%
    reactive_burst_pct_of_max_hp: float = 0.0  # heal amount as fraction of max HP
    reactive_cooldown_s: float = 8.0  # min seconds between bursts
    # When > 0, overrides coefficient-based HPS calculation entirely.
    # Set from actual_dealt/duration_s in log-replay mode so the healer model
    # doesn't contaminate mitigation calibration via IP absorb timing.
    baseline_hps_abs: float = 0.0
    # Token-bucket healer-throughput cap (Top-5 #3, 2026-07-06 retrospective).
    # Both baseline AND reactive heals draw from a shared bank of capacity
    # `healer_budget_capacity_pct_of_max_hp * max_hp` that refills at
    # `healer_budget_refill_pct_of_max_hp_per_s * max_hp` per second — bounds
    # SUSTAINED total healing demand without capping any single isolated
    # burst (a burst the bank can't fully fund is scaled down, never
    # skipped outright, so it doesn't reintroduce the "100% death-rate
    # artifact" the reactive layer exists to prevent). capacity == 0.0
    # disables the cap entirely (uncapped, current behavior) — the default
    # for any profile that doesn't set it, including ad-hoc test fixtures.
    # (capacity > 0 with refill == 0.0 is a valid, intentionally harsh
    # configuration — a bank that never refills — NOT a disabled cap.)
    # Values measured from the Warrior + Guardian calibrated log corpora,
    # not free-fit — see scripts/measure_healer_budget.py.
    healer_budget_refill_pct_of_max_hp_per_s: float = 0.0
    healer_budget_capacity_pct_of_max_hp: float = 0.0


def scale_damage_profile(profile: DamageProfile, multiplier: float) -> DamageProfile:
    """Return a copy of profile with all damage amounts scaled by multiplier."""
    # replace() silently no-ops on any field you don't name explicitly, so
    # casts must be passed here too -- this is exactly how the magic-damage
    # scaling gap hid for so long (see docs/validation/magic_cast_scaling_gap_2026_07_09.md).
    scaled_mobs = [
        replace(
            m,
            swing_damage_mean=m.swing_damage_mean * multiplier,
            casts=[{**c, "damage_mean": c["damage_mean"] * multiplier} for c in m.casts],
        )
        for m in profile.mobs
    ]
    scaled_tbs = [replace(tb, damage=tb.damage * multiplier) for tb in profile.tank_busters]
    return replace(profile, mobs=scaled_mobs, tank_busters=scaled_tbs)


def load_damage_profile(name_or_path: str) -> DamageProfile:
    path = _resolve_profile(name_or_path, "damage")
    with path.open() as f:
        d = yaml.safe_load(f)
    return DamageProfile(
        profile=d["profile"],
        duration_s=d["duration_s"],
        mobs=[MobSpec(**m) for m in d["mobs"]],
        tank_busters=[TankBuster(**tb) for tb in d.get("tank_busters", [])],
        affix=d.get("affix", {}),
    )


def load_healing_profile(name_or_path: str) -> HealingProfile:
    path = _resolve_profile(name_or_path, "healing")
    with path.open() as f:
        d = yaml.safe_load(f)
    return HealingProfile(
        profile=d["profile"],
        baseline_hps_pct_of_dtps=d["baseline_hps_pct_of_dtps"],
        externals=[HealingExternal(**e) for e in d.get("externals", [])],
        reactive_threshold_hp_pct=d.get("reactive_threshold_hp_pct", 0.0),
        reactive_burst_pct_of_max_hp=d.get("reactive_burst_pct_of_max_hp", 0.0),
        reactive_cooldown_s=d.get("reactive_cooldown_s", 8.0),
        healer_budget_refill_pct_of_max_hp_per_s=d.get(
            "healer_budget_refill_pct_of_max_hp_per_s", 0.0
        ),
        healer_budget_capacity_pct_of_max_hp=d.get("healer_budget_capacity_pct_of_max_hp", 0.0),
    )


def _resolve_profile(name_or_path: str, kind: str) -> Path:
    p = Path(name_or_path)
    if p.exists():
        return p
    return DATA_DIR / "profiles" / kind / f"{name_or_path}.yaml"
