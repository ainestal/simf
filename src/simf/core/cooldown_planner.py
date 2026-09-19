"""Cooldown Planner — Phase 6.1.

Given a damage profile or replay, find the placement of long-CD
emergency buttons (Shield Wall / Last Stand for Prot Warrior; Vampiric
Blood / Icebound for Blood DK; etc) that minimizes death rate.

The MVP scope is bracket-search brute force: for each long-CD button,
try N candidate placements aligned to damage-spike windows, run the
sim at each, pick the lowest death-rate combination. No talent-driven
choices, no rotation-level placement — just the half-dozen long CDs.

Output is `CooldownPlan` — a list of `(time_s, ability)` tuples that
`run_simulation` can consume via a new `CooldownPlanOverride` to fire
at exact times instead of letting `ActiveMitigationPolicy.decide`
choose. UI integration is a follow-up commit; the engine surface lands
first so other consumers can drive plans programmatically.

This is an instrumented evolution of `ActiveMitigationPolicy` — not a
replacement. The default `decide()` heuristic still drives Shield Block
/ Ignore Pain / Demo Shout rotations; only the emergency-CD slots get
overridden.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CooldownPress:
    """A scheduled cooldown press at a fixed timestamp."""

    time_s: float
    ability: str  # "shield_wall" | "last_stand" | "ardent_defender" | ...


@dataclass(frozen=True)
class CooldownPlan:
    """A complete plan for a fight — every long-CD press the policy
    will execute. Plans are spec-agnostic (the ability strings map to
    state slots inside each spec's chain)."""

    presses: list[CooldownPress]

    def for_ability(self, ability: str) -> list[CooldownPress]:
        return [p for p in self.presses if p.ability == ability]


# Long-CD button registry per spec. Keyed by class_spec → list of
# (ability_label, cooldown_s, duration_s, until_slot, cd_slot). A plan
# press authoritatively claims BOTH slots so the policy heuristic
# (policy.decide) treats the ability as on-cooldown and won't double-fire.
LONG_CD_BUTTONS: dict[str, list[tuple[str, float, float, str, str]]] = {
    "protection_warrior": [
        ("shield_wall", 240.0, 8.0, "shield_wall_until", "shield_wall_cd_until"),
        ("last_stand", 180.0, 12.0, "last_stand_until", "last_stand_cd_until"),
    ],
    "protection_paladin": [
        # Kept in sync by hand with specs.protection_paladin.ardent_defender_{cooldown_s,duration_s}
        # in constants.yaml (v37, 2026-07-03) — this registry doesn't read the YAML.
        ("ardent_defender", 90.0, 12.0, "ardent_defender_until", "ardent_defender_cd_until"),
    ],
    "blood_death_knight": [
        ("vampiric_blood", 90.0, 10.0, "last_stand_until", "last_stand_cd_until"),
        # Kept in sync by hand with specs.blood_death_knight.icebound_fortitude_{cooldown_s,duration_s}
        # in constants.yaml (v39, 2026-07-04: 180->120 per Wowhead 48792) — this registry doesn't read the YAML.
        ("icebound_fortitude", 120.0, 8.0, "shield_wall_until", "shield_wall_cd_until"),
    ],
    "vengeance_demon_hunter": [
        ("metamorphosis", 240.0, 15.0, "last_stand_until", "last_stand_cd_until"),
    ],
    "brewmaster_monk": [
        ("fortifying_brew", 360.0, 15.0, "shield_wall_until", "shield_wall_cd_until"),
    ],
    "guardian_druid": [
        ("incarnation", 180.0, 30.0, "last_stand_until", "last_stand_cd_until"),
        ("survival_instincts", 180.0, 6.0, "shield_wall_until", "shield_wall_cd_until"),
    ],
}


def find_damage_spikes(
    damage_timeline: list[tuple[float, float]],
    *,
    window_s: float = 5.0,
    top_n: int = 8,
) -> list[tuple[float, float]]:
    """Return up to ``top_n`` ``(centroid_time, damage_sum)`` pairs for
    the worst damage spikes in the timeline, ordered by centroid time.

    ``centroid_time`` is the damage-weighted centroid of the damage
    inside the window so a CD press anchored to it lands at the spike
    itself rather than ahead of it.

    ``damage_sum`` is the total damage in the window — annotation
    surfaces it so users can tell *which* spike a CD is covering
    ("Shield Wall covers a 1.2M spike midway through Hadrox") instead
    of guessing from a bare timestamp.

    Spikes are non-overlapping by ``window_s`` — the optimizer never
    wants two CDs on the same instant.
    """
    if not damage_timeline:
        return []
    by_time = sorted(damage_timeline, key=lambda t: t[0])
    end_t = max(t for t, _ in by_time)
    dt = 0.5
    n_bins = int(end_t / dt) + 1
    buckets = [0.0] * n_bins
    # Also track damage-weighted time sum per bin so we can compute the
    # centroid of damage in a window cheaply.
    bucket_time_sum = [0.0] * n_bins
    for t, dmg in by_time:
        idx = min(int(t / dt), n_bins - 1)
        buckets[idx] += dmg
        bucket_time_sum[idx] += dmg * t
    window_bins = max(1, int(window_s / dt))
    candidates: list[tuple[float, float]] = []
    for i in range(len(buckets) - window_bins + 1):
        damage_sum = sum(buckets[i : i + window_bins])
        if damage_sum <= 0:
            continue
        time_sum = sum(bucket_time_sum[i : i + window_bins])
        centroid = time_sum / damage_sum
        candidates.append((centroid, damage_sum))
    candidates.sort(key=lambda x: -x[1])
    # Greedy non-overlapping picks
    picked: list[tuple[float, float]] = []
    for t, dmg in candidates:
        if all(abs(t - p[0]) >= window_s for p in picked):
            picked.append((t, dmg))
        if len(picked) >= top_n:
            break
    return sorted(picked, key=lambda x: x[0])


def naive_plan_for_spec(
    class_spec: str,
    damage_timeline: list[tuple[float, float]],
    duration_s: float,
) -> CooldownPlan:
    """Build a naive cooldown plan — for each registered long-CD
    button, schedule it on cooldown starting at the earliest damage
    spike. No multi-button collision avoidance, no death-rate
    feedback — this is the lowest-effort "press on cooldown" baseline
    against which a smarter optimizer can be benchmarked.

    Useful in isolation as: "what would a default CD-on-CD play
    pattern do for me?" The UI surfaces this as the starting plan a
    user can edit; the optimizer (below) is the auto-best answer.
    """
    buttons = LONG_CD_BUTTONS.get(class_spec, [])
    if not buttons or not damage_timeline:
        return CooldownPlan(presses=[])

    spikes = find_damage_spikes(damage_timeline, top_n=12)
    first_spike_t = spikes[0][0] if spikes else 0.0
    presses: list[CooldownPress] = []
    for ability, cd, _duration, _slot, _cd_slot in buttons:
        # Anchor on the FIRST spike, then chain on cooldown.
        t = first_spike_t
        while t < duration_s:
            presses.append(CooldownPress(time_s=t, ability=ability))
            t += cd
    presses.sort(key=lambda p: p.time_s)
    return CooldownPlan(presses=presses)


def consume_due_presses(state, plan: CooldownPlan, now: float, last_idx: int) -> int:
    """Apply every plan press with ``time_s <= now`` and return the
    index of the next un-fired press.

    The runner calls this at the top of each event tick. The plan owns
    its declared abilities — when ``run_simulation`` receives a plan it
    pre-blocks the heuristic by setting the planned ``*_cd_until``
    slots to ``+inf``. Plan presses then ASSIGN both the active-window
    slot (``*_until``) and the cooldown lockout (``*_cd_until``) to
    fresh values; the assignment overrides the inf-block and produces a
    well-formed cooldown sequence.

    ``last_idx`` is the caller-tracked cursor — replaces the
    mutate-the-frozen-list pattern used in earlier iterations.
    """
    presses = plan.presses
    n = len(presses)
    if not presses or last_idx >= n:
        return last_idx
    cls_spec = state.character.class_spec
    cfg = {
        a: (cd, dur, slot, cd_slot)
        for a, cd, dur, slot, cd_slot in LONG_CD_BUTTONS.get(cls_spec, [])
    }
    i = last_idx
    while i < n and presses[i].time_s <= now:
        press = presses[i]
        button = cfg.get(press.ability)
        if button is not None:
            cd, duration, slot, cd_slot = button
            t = press.time_s
            # ASSIGN both slots — the plan is authoritative for its
            # declared abilities. Using max() would let an inf-blocked
            # cd_slot swallow the press.
            setattr(state, slot, t + duration)
            setattr(state, cd_slot, t + cd)
        i += 1
    return i


def planned_abilities(plan: CooldownPlan) -> set[str]:
    """Return the set of ability labels referenced by any press in
    ``plan``. Empty if ``plan`` has no presses."""
    return {p.ability for p in plan.presses}


def cd_slots_for(class_spec: str, abilities: set[str]) -> list[str]:
    """Return every ``*_cd_until`` slot name covered by ``abilities``
    for ``class_spec``. Used by the runner to pre-block heuristic CD
    presses when a plan is supplied."""
    out: list[str] = []
    for ability, _cd, _dur, _slot, cd_slot in LONG_CD_BUTTONS.get(class_spec, []):
        if ability in abilities:
            out.append(cd_slot)
    return out


def apply_plan_at_tick(state, plan: CooldownPlan, now: float) -> None:
    """Mutate-the-plan convenience wrapper around :func:`consume_due_presses`
    for callers that don't track an external cursor (tests, REPL drivers).

    Pops every press up to ``now`` off the plan. The runner uses
    :func:`consume_due_presses` directly to avoid the mutation."""
    if not plan.presses:
        return
    new_idx = consume_due_presses(state, plan, now, 0)
    if new_idx > 0:
        object.__setattr__(plan, "presses", list(plan.presses[new_idx:]))
