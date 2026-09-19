"""Skill-adjusted verdict ladder — Phase 2.10.

The single-number verdict ("Survivable at +14") implicitly promises optimal
play. A tank reading that doesn't know which version of themselves the model
assumed. This module sweeps the same key level across the playstyle tiers
defined in `constants.yaml.skill_tiers` and returns one survival number per
tier so the player can see how the answer changes with playstyle:

    In the zone              97% survive
    Anticipating the spike   87% survive
    Reading the fight        62% survive
    Learning the buttons     28% survive

The engine layer (`policy.py` + `runner.py`) consumes the `modifier` from
each tier as a Bernoulli gate on opportunistic Shield Block + Demoralizing
Shout presses. Emergency CDs are unaffected — nobody at +14 forgets Shield
Wall at 25% HP. v1 is Prot Warrior only; non-Warrior spec policies ignore
the modifier (engine fidelity gap tracked in ROADMAP).

This module is a thin layer on top of `run_simulation` — one sim per tier
at a fixed key level, packaged into `SkillLadder`. The UI calls it once
at the player's push key (prog_ceiling or comfortable_max from the
existing key-level verdict).
"""

from __future__ import annotations

from dataclasses import dataclass

from .character import Character
from .constants import load_skill_tiers
from .key_level_verdict import key_level_multiplier
from .profiles import DamageProfile, HealingProfile, scale_damage_profile
from .runner import run_simulation


@dataclass(frozen=True)
class SkillLadderPoint:
    """One tier rung in the ladder.

    `mean_sb_uptime` + `mean_sb_rage_starved_pct` + `mean_sb_charge_limited_pct`
    carry the SB-coverage profile of this sim. The top tier
    (modifier=1.0) is the player's theoretical ceiling for their current
    talent build — used by skill inference to (a) match the player's
    measured log uptime to the right rung by nearest-neighbor, and (b)
    surface the build floor in the trust caption (*"limited by rage 18%
    + charges 20% at perfect play"*). The bottleneck pcts describe the
    *build*, not the player's actual rage usage; log-side rage replay
    is Phase 3.4, not here.
    """

    tier_id: str
    label: str
    modifier: float
    death_rate: float
    mean_dtps: float
    p99_5s_window: float
    focus: str
    description: str
    mean_sb_uptime: float = 0.0
    mean_sb_rage_starved_pct: float = 0.0
    mean_sb_charge_limited_pct: float = 0.0


@dataclass(frozen=True)
class SkillLadder:
    """A skill-tier ladder at a fixed key level.

    Points are ordered top-tier-first (in_the_zone → learning). `key_level`
    is the M+ key the ladder was computed at, and `affix` records the
    affix multiplier so the UI can label the ladder ("+17 Fortified —
    survival by playstyle").
    """

    key_level: int
    affix: str
    points: list[SkillLadderPoint]


def compute_skill_ladder(
    character: Character,
    damage_profile: DamageProfile,
    healing_profile: HealingProfile,
    *,
    key_level: int,
    iterations: int = 200,
    seed: int = 42,
    affix: str = "fortified",
    affix_multiplier: float = 1.0,
    scaling: dict | None = None,
    tiers: list[dict] | None = None,
) -> SkillLadder:
    """Sweep the sim across `skill_tiers` at a fixed key level.

    Parallels `compute_key_level_verdict` but holds the key constant and
    walks the tier axis instead. `iterations` defaults to 200 — same
    order of magnitude as the key-level sweep — because the ladder needs
    *relative* survival across tiers, not high precision at one cell.

    Only meaningful for `protection_warrior` in v1. Other specs return
    bit-identical results across tiers because their policies ignore
    `skill_modifier` (this is the engine fidelity gap, not a ladder bug).
    """
    tiers = tiers if tiers is not None else load_skill_tiers()
    if not tiers:
        return SkillLadder(key_level=key_level, affix=affix, points=[])

    base_mult = key_level_multiplier(key_level, scaling)
    total_mult = base_mult * affix_multiplier
    scaled = scale_damage_profile(damage_profile, total_mult)

    points: list[SkillLadderPoint] = []
    for tier in tiers:
        result = run_simulation(
            character=character,
            damage_profile=scaled,
            healing_profile=healing_profile,
            iterations=iterations,
            seed=seed,
            skill_modifier=float(tier["modifier"]),
        )
        label, description, focus = _label_for_key_level(tier, key_level)
        points.append(
            SkillLadderPoint(
                tier_id=str(tier["id"]),
                label=label,
                modifier=float(tier["modifier"]),
                death_rate=result.death_rate,
                mean_dtps=result.mean_dtps,
                p99_5s_window=result.p99_5s_window,
                focus=focus,
                description=description,
                mean_sb_uptime=result.mean_sb_uptime,
                mean_sb_rage_starved_pct=result.mean_sb_rage_starved_pct,
                mean_sb_charge_limited_pct=result.mean_sb_charge_limited_pct,
            )
        )

    return SkillLadder(key_level=key_level, affix=affix, points=points)


def _label_for_key_level(tier: dict, key_level: int) -> tuple[str, str, str]:
    """Return (label, description, focus) for a tier at the requested key.

    A "Learning the buttons" tank doesn't queue +15+ — they got filtered
    out by the +14-17 progression gate. Showing that label at a high
    key describes a population that doesn't exist; the cohort that
    actually presses at modifier=0.40 in the +18 bracket is an
    experienced tank having a degraded night (multi-agent review,
    2026-05-22).

    A tier opts into the high-key relabel by carrying a
    `high_key_min_level` field plus the `high_key_label` /
    `high_key_description` / `high_key_focus` strings. Tiers without
    those fields are returned with their normal copy at any key.
    """
    threshold = tier.get("high_key_min_level")
    if threshold is not None and key_level >= int(threshold):
        return (
            str(tier.get("high_key_label", tier["label"])),
            str(tier.get("high_key_description", tier.get("description", ""))),
            str(tier.get("high_key_focus", tier.get("focus", ""))),
        )
    return (
        str(tier["label"]),
        str(tier.get("description", "")),
        str(tier.get("focus", "")),
    )
