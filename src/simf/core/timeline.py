import random

from .events import DamageEvent
from .profiles import DamageProfile


def events_in_window(
    events: list[DamageEvent],
    current_idx: int,
    window_s: float,
) -> list[DamageEvent]:
    """Return events scheduled AFTER ``events[current_idx]`` within
    ``window_s`` seconds — the lookahead slice for adaptive policies.

    Phase A plumbing for the adaptive-lookahead CD policy: the runner
    computes this slice once per tick and threads it into
    ``policy.decide(..., upcoming_events=...)``. No current policy
    consumes it; Phase B will add the first behaviour gated behind a
    YAML toggle. The helper lives in ``timeline.py`` because that's
    where the canonical ``DamageEvent`` shape and ordering invariant
    are owned — ``generate_events`` already sorts the returned list by
    ``time_s``, and this slicer relies on that order to short-circuit
    once an event past the window cap is hit.

    Args:
        events: the full per-iteration event list (in time-sorted order,
            as guaranteed by ``generate_events`` and replay loaders).
        current_idx: cursor of the event being processed *right now*.
            Returned slice starts at ``current_idx + 1`` — the current
            event isn't "upcoming" from the policy's perspective.
        window_s: lookahead horizon in seconds, measured from the
            current event's time. Non-positive returns an empty list
            so a caller that wants to short-circuit the feature can
            pass ``window_s=0`` and skip allocations downstream.

    Returns:
        new list of events with ``time_s`` in
        ``(events[current_idx].time_s, events[current_idx].time_s + window_s]``.
        Empty list when nothing is in the window — typical at end of
        pull when the cursor hits the tail of the schedule.
    """
    if window_s <= 0 or current_idx < 0 or current_idx >= len(events):
        return []
    horizon = events[current_idx].time_s + window_s
    out: list[DamageEvent] = []
    j = current_idx + 1
    while j < len(events) and events[j].time_s <= horizon:
        out.append(events[j])
        j += 1
    return out


def generate_events(profile: DamageProfile, rng: random.Random) -> list[DamageEvent]:
    events: list[DamageEvent] = []
    duration = profile.duration_s

    for mob_idx, mob in enumerate(profile.mobs):
        for instance in range(mob.count):
            source_id = f"mob{mob_idx}_{instance}"
            t = rng.uniform(0, mob.swing_timer_s)
            while t < duration:
                jitter = 1 + rng.uniform(-mob.swing_damage_variance, mob.swing_damage_variance)
                amount = mob.swing_damage_mean * jitter
                events.append(
                    DamageEvent(
                        time_s=t,
                        source_id=source_id,
                        school=mob.school,
                        raw_amount=amount,
                        attack_type=mob.attack_type,
                        is_avoidable=mob.attack_type in ("melee", "ranged"),
                        is_blockable=(
                            mob.attack_type in ("melee", "ranged") and mob.school == "physical"
                        ),
                    )
                )
                t += mob.swing_timer_s

            for cast in mob.casts:
                cadence = cast["cadence_s"]
                ct = cadence * 0.5 * (1 + rng.uniform(-0.15, 0.15))
                while ct < duration:
                    jitter = 1 + rng.uniform(-0.15, 0.15)
                    events.append(
                        DamageEvent(
                            time_s=ct,
                            source_id=source_id,
                            school=cast["school"],
                            raw_amount=cast["damage_mean"] * jitter,
                            attack_type="spell",
                            is_avoidable=False,
                            is_blockable=False,
                        )
                    )
                    ct += cadence * (1 + rng.uniform(-0.15, 0.15))

    for tb in profile.tank_busters:
        events.append(
            DamageEvent(
                time_s=tb.time_s,
                source_id="tank_buster",
                school=tb.school,
                raw_amount=tb.damage,
                attack_type=tb.attack_type,
                is_tank_buster=True,
                is_avoidable=False,
                is_blockable=tb.attack_type in ("melee", "ranged") and tb.school == "physical",
            )
        )

    affix = profile.affix
    fortified_mult = 1.20 if affix.get("fortified") else 1.0
    tyrannical_mult = 1.30 if affix.get("tyrannical") else 1.0
    seasonal = affix.get("seasonal_modifier", 1.0)
    multiplier = fortified_mult * tyrannical_mult * seasonal
    if multiplier != 1.0:
        for e in events:
            e.raw_amount *= multiplier

    events.sort(key=lambda e: e.time_s)
    return events
