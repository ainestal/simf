"""Phase 6.1 — brute-force cooldown-placement optimizer.

Given a character + damage profile (or events from a real log), search a
small set of candidate :class:`CooldownPlan` placements and return the
one that minimises death rate.

The search is intentionally cheap:

1. Scout the timeline once with no plan to recover a representative
   ``damage_timeline`` (re-uses the runner's per-iteration timeline by
   running a single iteration).
2. Detect up to ``top_n_spikes`` damage spikes via
   :func:`find_damage_spikes`.
3. For every long-CD button registered for the spec, enumerate
   plans anchored on each of the top-K spikes (then chained on
   cooldown). Combine across buttons as a Cartesian product but bound
   by ``max_candidates`` (default 24) so 200-iter sims stay snappy.
4. Run :func:`run_simulation` once per candidate at a smaller
   ``search_iterations`` budget. The best plan (lowest death rate,
   tie-broken by lowest p99 5s window) is returned alongside an
   ordered comparison table.

This is the "engine surface" follow-up the roadmap marks as
"one commit away" — it does NOT touch policy.decide, only schedules
emergency-CD presses. The naive baseline (CD-on-CD from first spike)
and the no-plan baseline are always included for sanity-check
comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from ..core.character import Character
from ..core.cooldown_planner import (
    LONG_CD_BUTTONS,
    CooldownPlan,
    CooldownPress,
    find_damage_spikes,
    naive_plan_for_spec,
)
from ..core.events import DamageEvent
from ..core.profiles import DamageProfile, HealingProfile
from ..core.runner import run_simulation

# Half-window in seconds within which a naive plan's first press is
# considered "anchored" to a detected damage spike. The press time and
# the spike centroid are independently float-computed; allowing the same
# 0.5s tolerance the UI uses keeps the rationale path in sync.
_NAIVE_ANCHOR_TOLERANCE_S: float = 2.5


@dataclass(frozen=True)
class CandidateResult:
    """One scored plan in the search.

    ``press_anchors`` is the structured form of the same information
    in ``label`` — a tuple of ``(anchor_time_s, (Pretty Ability,
    ...))`` pairs sorted by time. Consumers that have access to the
    log's segments (the UI, but not the CLI) use this to render
    labels anchored to pull context (``early in Hadrox``) rather
    than absolute timestamps (``at 4:21``). Empty for the baselines.

    ``spike_damage_by_anchor`` maps each anchor timestamp to the
    summed damage in the spike window the optimizer used when picking
    it. The press-timeline annotation surfaces this so a player can
    tell which spike a press covers ("covers a 1.2M-damage spike")
    instead of guessing from the timestamp alone. Empty for the
    baselines and for any candidate whose anchors were not pulled
    from :func:`find_damage_spikes` (currently only the
    spec-has-no-buttons short-circuit).

    ``mean_hrps`` is the Healing Required Per Second this plan
    demands from the healer — net damage-after-self-sustain per
    second, averaged across iterations. Two candidate plans that
    tie on death rate can differ meaningfully on HRPS; the lower
    HRPS plan is the easier real-world ask of the healer. Sourced
    from :attr:`simf.core.metrics.SimResult.mean_hrps`.
    """

    label: str
    plan: CooldownPlan
    death_rate: float
    p99_5s_window: float
    mean_dtps: float
    press_anchors: tuple[tuple[float, tuple[str, ...]], ...] = ()
    spike_damage_by_anchor: tuple[tuple[float, float], ...] = ()
    mean_hrps: float = 0.0


@dataclass(frozen=True)
class OptimizerResult:
    """Outcome of :func:`optimize_cooldown_plan` — the winning plan plus
    every candidate ranked best-first."""

    best: CandidateResult
    candidates: list[CandidateResult]
    baseline_no_plan: CandidateResult
    baseline_naive: CandidateResult


def _chain_press(
    ability: str, anchor_t: float, cd_s: float, duration_s: float
) -> list[CooldownPress]:
    """Return the full press chain for one button, anchored at
    ``anchor_t`` and repeated every ``cd_s`` until ``duration_s``."""
    presses: list[CooldownPress] = []
    t = anchor_t
    while t < duration_s:
        presses.append(CooldownPress(time_s=t, ability=ability))
        t += cd_s
    return presses


def _score_key(result: CandidateResult) -> tuple[float, float]:
    """Sort key: prefer low death rate, then low p99-5s window."""
    return (result.death_rate, result.p99_5s_window)


def optimize_cooldown_plan(
    character: Character,
    damage_profile: DamageProfile | None,
    healing_profile: HealingProfile,
    *,
    events_override: list[DamageEvent] | None = None,
    duration_override: float | None = None,
    search_iterations: int = 200,
    top_n_spikes: int = 6,
    max_candidates: int = 24,
    seed: int = 42,
) -> OptimizerResult:
    """Search for the best CD plan for this character × encounter.

    Either ``damage_profile`` (synthetic) or ``events_override``
    (replay mode) must be supplied. ``duration_override`` overrides the
    profile/events duration; otherwise it is inferred.

    The returned :class:`OptimizerResult` always includes a no-plan and
    a naive baseline so the caller can show how much the search bought
    over "press on cooldown."
    """
    if damage_profile is None and not events_override:
        raise ValueError("optimize_cooldown_plan needs damage_profile or events_override")

    if duration_override is not None:
        duration_s = duration_override
    elif damage_profile is not None:
        duration_s = damage_profile.duration_s
    else:
        assert events_override is not None  # noqa: S101 — internal invariant, not user input
        duration_s = max(e.time_s for e in events_override) + 5.0

    # Step 1 — scout once with no plan to recover the damage_timeline.
    scout_result = run_simulation(
        character=character,
        damage_profile=damage_profile,
        healing_profile=healing_profile,
        iterations=max(1, min(search_iterations, 50)),
        seed=seed,
        events_override=events_override,
        duration_override=duration_override,
        cooldown_plan=None,
    )
    timeline = scout_result.sample_damage_timeline or []

    # Step 2 — find candidate anchor spikes. ``find_damage_spikes``
    # returns ``(centroid_time, damage_sum)`` pairs — we carry both so
    # the press-timeline annotation in the UI can name which spike a
    # press covers.
    spikes_with_damage = find_damage_spikes(timeline, top_n=top_n_spikes)
    if not spikes_with_damage:
        spikes_with_damage = [(0.0, 0.0)]
    spike_damage_map: dict[float, float] = dict(spikes_with_damage)
    spikes: list[float] = [t for t, _ in spikes_with_damage]

    # Step 3 — enumerate Cartesian product of (anchor per button),
    # bounded by ``max_candidates``.
    buttons = LONG_CD_BUTTONS.get(character.class_spec, [])
    if not buttons:
        empty_plan = CooldownPlan(presses=[])
        baseline = _evaluate_plan(
            label="no_plan",
            plan=empty_plan,
            character=character,
            damage_profile=damage_profile,
            healing_profile=healing_profile,
            events_override=events_override,
            duration_override=duration_override,
            iterations=search_iterations,
            seed=seed,
        )
        return OptimizerResult(
            best=baseline,
            candidates=[baseline],
            baseline_no_plan=baseline,
            baseline_naive=baseline,
        )

    per_button_chains: list[list[tuple[float, list[CooldownPress]]]] = []
    for ability, cd_s, _duration_active, _slot, _cd_slot in buttons:
        chains: list[tuple[float, list[CooldownPress]]] = []
        for anchor_t in spikes:
            chain = _chain_press(ability, anchor_t, cd_s, duration_s)
            chains.append((anchor_t, chain))
        per_button_chains.append(chains)

    # Trim to keep the search bounded.
    total = 1
    for chains in per_button_chains:
        total *= len(chains)
    if total > max_candidates:
        # Greedy trim: keep the top-K anchors per button. Spread cap
        # roughly evenly across buttons.
        per_button_keep = max(1, int(max_candidates ** (1.0 / max(1, len(per_button_chains)))))
        per_button_chains = [chains[:per_button_keep] for chains in per_button_chains]

    candidate_plans: list[
        tuple[
            str,
            tuple[tuple[float, tuple[str, ...]], ...],
            tuple[tuple[float, float], ...],
            CooldownPlan,
        ]
    ] = []
    for combo in product(*per_button_chains):
        presses: list[CooldownPress] = []
        # Group abilities by their anchor time so co-anchored buttons
        # collapse into a single "at M:SS" clause. Old format
        # `shield_wall@261s + last_stand@261s` reads as a gear-string;
        # new format `Shield Wall + Last Stand at 4:21` says the same
        # thing in English. Co-anchored is the common case (e.g.
        # stacking Shield Wall + Last Stand on the same spike).
        by_anchor: dict[float, list[str]] = {}
        for anchor_t, chain in combo:
            presses.extend(chain)
            pretty = chain[0].ability.replace("_", " ").title()
            by_anchor.setdefault(anchor_t, []).append(pretty)
        label_parts: list[str] = []
        anchors: list[tuple[float, tuple[str, ...]]] = []
        damages: list[tuple[float, float]] = []
        for anchor_t in sorted(by_anchor):
            abilities = " + ".join(by_anchor[anchor_t])
            mm, ss = divmod(int(anchor_t), 60)
            label_parts.append(f"{abilities} at {mm}:{ss:02d}")
            anchors.append((anchor_t, tuple(by_anchor[anchor_t])))
            damages.append((anchor_t, spike_damage_map.get(anchor_t, 0.0)))
        label = " · ".join(label_parts)
        presses.sort(key=lambda p: p.time_s)
        plan = CooldownPlan(presses=presses)
        candidate_plans.append((label, tuple(anchors), tuple(damages), plan))

    # Step 4 — score baselines + candidates.
    no_plan = _evaluate_plan(
        label="no_plan",
        plan=CooldownPlan(presses=[]),
        character=character,
        damage_profile=damage_profile,
        healing_profile=healing_profile,
        events_override=events_override,
        duration_override=duration_override,
        iterations=search_iterations,
        seed=seed,
    )
    naive_plan = naive_plan_for_spec(character.class_spec, timeline, duration_s)
    # The naive plan anchors every ability on its first press time. We
    # need to look up the matching spike's damage from the full
    # ``find_damage_spikes`` set (not just the trimmed ``spike_damage_map``,
    # which is keyed off ``top_n_spikes`` — naive uses top_n=12 internally,
    # so a small-but-early spike that didn't make the optimizer's cut
    # could still be the naive anchor). Falling back to the optimizer's
    # smaller map is fine when no exact match exists — the worst case is
    # a press that tags "CD refresh", which is still honest.
    naive_first_anchor_t = naive_plan.presses[0].time_s if naive_plan.presses else None
    if naive_first_anchor_t is not None:
        full_spike_map: dict[float, float] = dict(find_damage_spikes(timeline, top_n=12))
        # Closest spike within ``_NAIVE_ANCHOR_TOLERANCE_S`` of the first
        # press wins; otherwise leave the map empty so the UI tags as
        # "CD refresh" instead of fabricating a damage value.
        best_t: float | None = None
        best_delta = _NAIVE_ANCHOR_TOLERANCE_S
        for sp_t in full_spike_map:
            delta = abs(sp_t - naive_first_anchor_t)
            if delta <= best_delta:
                best_t = sp_t
                best_delta = delta
        if best_t is not None:
            # Key by the naive press time, not the spike centroid — the
            # UI matches anchor key against press.time_s within a 0.5s
            # tolerance, and naive's first press may sit a few hundred
            # ms off the centroid depending on how `damage_timeline`
            # binning landed.
            naive_damages: tuple[tuple[float, float], ...] = (
                (naive_first_anchor_t, full_spike_map[best_t]),
            )
        else:
            naive_damages = ()
    else:
        naive_damages = ()
    naive_scored = _evaluate_plan(
        label="naive (CD-on-CD from first spike)",
        plan=naive_plan,
        character=character,
        damage_profile=damage_profile,
        healing_profile=healing_profile,
        events_override=events_override,
        duration_override=duration_override,
        iterations=search_iterations,
        seed=seed,
        spike_damage_by_anchor=naive_damages,
    )

    scored: list[CandidateResult] = [no_plan, naive_scored]
    for label, c_anchors, c_damages, plan in candidate_plans:
        scored.append(
            _evaluate_plan(
                label=label,
                plan=plan,
                character=character,
                damage_profile=damage_profile,
                healing_profile=healing_profile,
                events_override=events_override,
                duration_override=duration_override,
                iterations=search_iterations,
                seed=seed,
                press_anchors=c_anchors,
                spike_damage_by_anchor=c_damages,
            )
        )

    scored.sort(key=_score_key)
    return OptimizerResult(
        best=scored[0],
        candidates=scored,
        baseline_no_plan=no_plan,
        baseline_naive=naive_scored,
    )


def _evaluate_plan(
    *,
    label: str,
    plan: CooldownPlan,
    character: Character,
    damage_profile: DamageProfile | None,
    healing_profile: HealingProfile,
    events_override: list[DamageEvent] | None,
    duration_override: float | None,
    iterations: int,
    seed: int,
    press_anchors: tuple[tuple[float, tuple[str, ...]], ...] = (),
    spike_damage_by_anchor: tuple[tuple[float, float], ...] = (),
) -> CandidateResult:
    result = run_simulation(
        character=character,
        damage_profile=damage_profile,
        healing_profile=healing_profile,
        iterations=iterations,
        seed=seed,
        events_override=events_override,
        duration_override=duration_override,
        cooldown_plan=plan,
        compute_tmi_metrics=False,
    )
    return CandidateResult(
        label=label,
        plan=plan,
        death_rate=result.death_rate,
        p99_5s_window=result.p99_5s_window,
        mean_dtps=result.mean_dtps,
        press_anchors=press_anchors,
        spike_damage_by_anchor=spike_damage_by_anchor,
        mean_hrps=result.mean_hrps,
    )
