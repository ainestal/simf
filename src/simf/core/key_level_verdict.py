"""Key-level suitability verdict — Phase 2.1.

Answers the core M+ tank question: "Am I tankable enough for +X?"

The user-visible promise (per ROADMAP):

    "Survivable at +15 Fortified (death rate 2.3%) — not yet at +16
    (death rate 18.7%)."

`compute_key_level_verdict(...)` walks a range of key levels, scales the
baseline damage profile to each, runs the sim, and bins each key into
one of three bands based on the death-rate thresholds in
`key_level_scaling.yaml`:

    comfortable  → death_rate < 5%   (safe; you can chain these)
    progression  → death_rate < 25%  (hard but doable; this is your push)
    danger       → death_rate ≥ 25%  (don't take this key without a plan)

The two thresholds come from `key_level_scaling.yaml.death_rate_thresholds`
so they can be retuned without code changes.

Distinct from `runner.run_simulation`: this module is the verdict layer
on top, not a new engine. Each sweep point reuses the existing sim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import load_key_level_scaling
from .profiles import DamageProfile, HealingProfile, scale_damage_profile
from .runner import run_simulation


@dataclass(frozen=True)
class KeyLevelPoint:
    """One key-level sample in the verdict sweep."""

    key_level: int
    damage_multiplier: float
    death_rate: float
    mean_dtps: float
    p99_5s_window: float
    band: str  # "comfortable" | "progression" | "danger"
    # Phase 6.2 / 6.3 surfacing — Brutoh user-feedback 2026-05-21:
    # "didn't we compute a coefficient of survivability that takes into
    # account self healing, absorbs and other stuff?" These come from
    # SimResult unchanged; carried through so the UI can render them in
    # the per-key breakdown.
    mean_hrps: float = 0.0
    normalized_tank_score: float = 0.0
    # Tail-risk surfacing (2026-07-06 retrospective, Top-5 #2) — carried
    # through from SimResult unchanged. sample_max_hp is needed alongside
    # the window fields to express them as "% of max HP", not a raw number.
    p99_10s_window: float = 0.0
    p99_15s_window: float = 0.0
    sample_max_hp: float = 0.0
    p5_min_hp_pct: float | None = None
    # HP-over-time trace (2026-07-28) — the same worst-spike sample the
    # tail-risk panel already describes, carried through so the UI can
    # reconstruct a real HP curve without a second sim run.
    sample_duration_s: float = 0.0
    sample_damage_timeline: list[tuple[float, float]] = field(default_factory=list)
    sample_heal_timeline: list[tuple[float, float]] = field(default_factory=list)
    sample_died: bool = False
    sample_time_to_die_s: float | None = None


@dataclass(frozen=True)
class KeyLevelVerdict:
    """Composite verdict across a key-level sweep.

    `comfortable_max` is the highest key where death_rate < comfortable
    threshold; `prog_ceiling` is the highest where < progression
    threshold. Either may be `None` when no key in the swept range
    qualifies (e.g. character is undergeared for +10, or the sweep
    didn't reach the prog ceiling).
    """

    points: list[KeyLevelPoint]
    comfortable_max: int | None
    prog_ceiling: int | None
    affix: str  # "fortified" | "tyrannical"

    def displayable_prog_ceiling(self, prog_keys_past_comfort: int = 3) -> int | None:
        """`prog_ceiling` capped to the last key the chain actually renders.

        The headline and the per-key chain must agree. If the chain trims
        +21..+24 past the cliff, the headline can't go on to name +24 as
        the stress ceiling — it would point at a row the user can't see
        and contradict the truncation rationale. This returns the highest
        progression key within the displayable window, falling back to
        the absolute prog_ceiling when nothing is trimmed."""
        if self.prog_ceiling is None:
            return None
        shown = self.displayable_points(prog_keys_past_comfort=prog_keys_past_comfort)
        if not shown:
            return None
        last_shown = shown[-1].key_level
        return min(self.prog_ceiling, last_shown)

    def headline(self) -> str:
        """Sentence-shaped verdict — frames the answer as *damage-stream
        survivability*, not key completion.

        The previous copy said "Push key tonight: +X" which read as a
        recommendation. The sweep measures whether the character survives
        the damage stream of +X — not whether the group clears the timer.
        Brutoh feedback 2026-05-22 (logs against a +24 push): "absolutely
        no way I can try a +24 tonight." Mechanic damage, group wipes,
        and timer pressure are the failure modes at high keys, and the
        engine sees none of them. Headline now leads with "Damage stream
        survives" so the player reads it correctly.

        Examples (per verdict band):
            "Damage stream survives +14 Fortified. Holds under stress to +18 — mechanics decide it above the line."
            "Damage stream survives through +20 Fortified — top of the simulated range."
            "Stream holds under stress through +14 Fortified — but the comfortable line is below this sweep. Gear up."
            "Undergeared at +10 Fortified. Gear up — +10 is dying."
        """
        if self.comfortable_max is None and self.prog_ceiling is None:
            # Every key in danger band — the user is below the floor.
            return (
                f"Undergeared at +{self.points[0].key_level} {self.affix.title()}. "
                f"Gear up — +{self.points[0].key_level} is dying."
            )
        if self.comfortable_max is None and self.prog_ceiling is not None:
            # Use the displayable cap so the headline can't name a key the
            # truncated chain doesn't render. With no comfortable_max the
            # truncation falls back to the full sweep, so the displayable
            # ceiling equals prog_ceiling in practice — but route through
            # the helper so any future trim policy stays consistent.
            shown_prog: int | None = self.displayable_prog_ceiling() or self.prog_ceiling
            return (
                f"Stream holds under stress through +{shown_prog} "
                f"{self.affix.title()} — but the comfortable line is below "
                "this sweep. Gear up."
            )
        if self.comfortable_max is not None and self.prog_ceiling is None:
            # All comfortable — over-geared past the sweep top. Celebrate it.
            # "Comfortable" kept in copy because over-geared characters have
            # no mechanic-vs-damage tension at the swept range.
            return (
                f"Comfortable damage stream through +{self.comfortable_max} "
                f"{self.affix.title()} — top of the simulated range."
            )
        # both populated
        assert self.comfortable_max is not None and self.prog_ceiling is not None  # noqa: S101 — internal invariant, not user input
        shown_prog = self.displayable_prog_ceiling()
        if shown_prog is not None and shown_prog > self.comfortable_max:
            return (
                f"Damage stream survives +{self.comfortable_max} "
                f"{self.affix.title()}. Holds under stress to +{shown_prog} "
                "— mechanics decide it above the line."
            )
        return f"Damage stream survives +{self.comfortable_max} {self.affix.title()}."

    def displayable_points(self, prog_keys_past_comfort: int = 3) -> list[KeyLevelPoint]:
        """Trim the chain to what's useful — don't dump +18→+24 as if those
        were pushable.

        The full sweep (+2..+24) is correct data but the wrong default
        view. Showing 23 rows invites "the sim thinks I can push +24"
        when reality is "the model ran out of comfortable keys at +17
        and the rest is fictional pushability." Cap the rendered list at
        the first key past `comfortable_max`, plus `prog_keys_past_comfort`
        rows of headroom, so the player still sees the cliff but not
        keys the model has no business labelling.

        Falls back to the full point list when there's no comfortable
        ceiling (undergeared sweep — all rows are useful) or when every
        key is comfortable (over-geared — celebrate the whole range).

        Brutoh feedback 2026-05-22: "Absolutely no way I can try a +24
        tonight." The chain was showing +24 at 19.5% deaths labelled
        "progression" — true to the model, false to reality.
        """
        if not self.points:
            return []
        if self.comfortable_max is None:
            return list(self.points)
        first_above_comfort = next(
            (i for i, p in enumerate(self.points) if p.key_level > self.comfortable_max),
            None,
        )
        if first_above_comfort is None:
            return list(self.points)
        return list(self.points[: first_above_comfort + prog_keys_past_comfort])

    def detail(self) -> str:
        """One-line breakdown of the cliff.

        Compressed by state — every branch returns at most one sentence.
        The previous "list every non-comfortable point" implementation
        produced an 11-arrow chain on the undergeared branch (ui-critic
        Phase 2.1 round 1 #3).
        """
        if not self.points:
            return ""

        # Undergeared — every key in danger. The chain is noise; give the
        # user a single useful number (death-rate at the floor).
        if self.comfortable_max is None and self.prog_ceiling is None:
            floor = self.points[0]
            return (
                f"+{floor.key_level} dies {floor.death_rate * 100:.0f}% — "
                f"the simulated floor is already past your survival ceiling."
            )

        # All comfortable — no cliff in the swept range. Silence is the
        # right answer; the headline already says it.
        non_comfortable = [p for p in self.points if p.band != "comfortable"]
        if not non_comfortable:
            return ""

        # Mixed — show the cliff. Caps at 3 transition points so a wide
        # progression band doesn't run on. Pick the FIRST cliff (where
        # comfortable → progression) plus up to two more.
        return " → ".join(
            f"+{p.key_level} ({p.death_rate * 100:.1f}%)" for p in non_comfortable[:3]
        )


def key_level_multiplier(key_level: int, scaling: dict | None = None) -> float:
    """Damage multiplier for `key_level` relative to the calibration
    baseline (typically +11 per `key_level_scaling.yaml`). Returns 1.0
    when the level isn't in the table — caller decides whether that's a
    fatal config error or a benign default."""
    s = scaling or load_key_level_scaling()
    levels = s.get("levels", {})
    row = levels.get(key_level)
    if not row:
        return 1.0
    return float(row.get("damage_multiplier", 1.0))


def compute_key_level_verdict(
    character,
    damage_profile: DamageProfile,
    healing_profile: HealingProfile,
    *,
    key_levels: list[int] | None = None,
    iterations: int = 200,
    seed: int = 42,
    affix: str = "fortified",
    scaling: dict | None = None,
) -> KeyLevelVerdict:
    """Sweep the sim across a key-level range and return a verdict.

    `iterations` is intentionally lower than the full-sim default (1000+)
    because the verdict needs *trend* across many key levels rather than
    high-precision death-rate at a single key. 200 iterations × 8 levels
    is faster than 1 × 1600 iterations at a single level and gives the
    answer the user actually asked for.

    `affix` selects the multiplier to apply on top of the per-level
    scaling. "fortified" is the conservative default for tank-survival
    verdicts (more deaths come from trash pulls than tank-buster spikes
    per WCL aggregates).
    """
    s = scaling or load_key_level_scaling()
    affixes_cfg = s.get("affixes") or {}
    thresholds = s.get("death_rate_thresholds") or {}
    comfortable_pct = float(thresholds.get("comfortable", 0.05))
    prog_pct = float(thresholds.get("progression", 0.25))

    if affix == "fortified":
        affix_mult = float(affixes_cfg.get("fortified_non_boss_multiplier", 1.0))
    elif affix == "tyrannical":
        affix_mult = float(affixes_cfg.get("tyrannical_boss_multiplier", 1.0))
    else:
        affix_mult = 1.0

    if key_levels is None:
        # Default sweep: every key level defined in the table — Mythic 0
        # (+2) up to the title-pushing ceiling (+24). Brutoh user-feedback
        # 2026-05-21: "should start in mythic 0 (or +2) and then go up to
        # +24 or so where the world records are, just for comparison."
        # The verdict bands (comfortable / progression / danger) compress
        # the low end naturally for over-geared tanks; rendering them
        # explicitly is the point.
        key_levels = sorted(int(k) for k in s.get("levels", {}))

    points: list[KeyLevelPoint] = []
    for kl in key_levels:
        base_mult = key_level_multiplier(kl, s)
        total_mult = base_mult * affix_mult
        scaled = scale_damage_profile(damage_profile, total_mult)
        result = run_simulation(
            character=character,
            damage_profile=scaled,
            healing_profile=healing_profile,
            iterations=iterations,
            seed=seed,
        )
        if result.death_rate < comfortable_pct:
            band = "comfortable"
        elif result.death_rate < prog_pct:
            band = "progression"
        else:
            band = "danger"
        points.append(
            KeyLevelPoint(
                key_level=kl,
                damage_multiplier=total_mult,
                death_rate=result.death_rate,
                mean_dtps=result.mean_dtps,
                p99_5s_window=result.p99_5s_window,
                band=band,
                mean_hrps=getattr(result, "mean_hrps", 0.0),
                normalized_tank_score=getattr(result, "normalized_tank_score", 0.0),
                p99_10s_window=getattr(result, "p99_10s_window", 0.0),
                p99_15s_window=getattr(result, "p99_15s_window", 0.0),
                sample_max_hp=getattr(result, "sample_max_hp", 0.0),
                p5_min_hp_pct=getattr(result, "p5_min_hp_pct", None),
                sample_duration_s=getattr(result, "sample_duration_s", 0.0),
                sample_damage_timeline=getattr(result, "sample_damage_timeline", []),
                sample_heal_timeline=getattr(result, "sample_heal_timeline", []),
                sample_died=getattr(result, "sample_died", False),
                sample_time_to_die_s=getattr(result, "sample_time_to_die_s", None),
            )
        )

    comfortable_max = max(
        (p.key_level for p in points if p.band == "comfortable"),
        default=None,
    )
    prog_ceiling = max(
        (p.key_level for p in points if p.band in ("comfortable", "progression")),
        default=None,
    )
    if comfortable_max is not None and prog_ceiling == comfortable_max:
        # Caller distinguishes "the prog ceiling is a real push beyond
        # comfortable" from "everything's comfortable" via a higher value.
        prog_ceiling = None

    return KeyLevelVerdict(
        points=points,
        comfortable_max=comfortable_max,
        prog_ceiling=prog_ceiling,
        affix=affix,
    )
