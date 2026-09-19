"""Convert combat-log damage events into DamageEvent objects for sim replay."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..core.bleed_detection import is_bleed
from ..core.events import AttackType, DamageEvent, DamageSchool
from .combat_log import (
    ChallengeModeRun,
    iter_damage_events,
    parse_challenge_modes,
)
from .combat_log_buffs import parse_self_buff_windows


@dataclass
class ReplayData:
    run: ChallengeModeRun
    duration_s: float
    events: list[DamageEvent]
    actual_dealt: int  # post-mit damage from log (ground truth)
    actual_blocked: int
    actual_absorbed: int
    actual_resisted: int
    event_count: int


def load_replay(
    log_path: Path,
    target_name: str,
    run_index: int = -1,
    *,
    runs: list[ChallengeModeRun] | None = None,
    buff_ability_ids: set[int] | None = None,
) -> ReplayData:
    """Load one CHALLENGE_MODE run's damage events for replay.

    ``runs`` lets a caller that already parsed this file's run boundaries
    (e.g. iterating several run_index values against the same log) pass
    them in directly instead of re-parsing the whole file per call.

    ``buff_ability_ids`` stamps each non-self damage event's ``active_buffs``
    with the tank's real buff windows (from the log's own SPELL_AURA
    events), the same way ``wcl_replay.adapt_events``'s ``buff_windows``
    does for WCL fights — the input a window-gated mitigation layer (e.g.
    Prot Warrior's Keep Your Feet on the Ground) reads. ``None`` (the
    default) leaves ``active_buffs`` empty on every event, bit-identical to
    the prior behaviour and to every spec/layer that doesn't read it.
    """
    if runs is None:
        runs = parse_challenge_modes(log_path)
    if not runs:
        raise ValueError(f"No CHALLENGE_MODE runs in {log_path}")
    if run_index < 0:
        run_index = len(runs) + run_index
    if not (0 <= run_index < len(runs)):
        raise ValueError(f"run_index {run_index} out of range (have {len(runs)} runs)")
    run = runs[run_index]

    buff_windows: dict[int, list[tuple[float, float]]] = {}
    if buff_ability_ids:
        buff_windows = parse_self_buff_windows(
            log_path,
            target_name,
            buff_ability_ids,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
        )

    def _active_at(t_s: float) -> frozenset[int]:
        if not buff_windows:
            return frozenset()
        return frozenset(
            aid
            for aid, wins in buff_windows.items()
            if any(start <= t_s <= end for start, end in wins)
        )

    events: list[DamageEvent] = []
    actual_dealt = 0
    actual_blocked = 0
    actual_absorbed = 0
    actual_resisted = 0

    for log_evt in iter_damage_events(log_path, target_name, run.start_time_s, run.end_time_s):
        is_self = log_evt.source_name == target_name

        attack_type: AttackType
        if log_evt.event_type.startswith("SWING"):
            attack_type = "melee"
        elif log_evt.event_type == "RANGE_DAMAGE":
            attack_type = "ranged"
        else:
            attack_type = "spell"
        # combat_log.iter_damage_events normalises school strings to the
        # DamageSchool literal set — cast is safe here.
        school = cast(DamageSchool, log_evt.school)

        if is_self:
            # Self-inflicted events (e.g. Stagger DoT ticks): use actual HP amount,
            # no further DR — the DR already happened when the pool was filled.
            events.append(
                DamageEvent(
                    time_s=log_evt.time_s - run.start_time_s,
                    source_id=log_evt.source_name,
                    school=school,
                    raw_amount=float(log_evt.amount),
                    attack_type=attack_type,
                    is_dot_tick=True,
                    is_avoidable=False,
                    is_blockable=False,
                    is_log_replay=True,
                    log_absorbed=0.0,
                    is_self_inflicted=True,
                )
            )
        else:
            is_dot_tick = log_evt.event_type == "SPELL_PERIODIC_DAMAGE"
            events.append(
                DamageEvent(
                    time_s=log_evt.time_s - run.start_time_s,
                    source_id=log_evt.source_name,
                    school=school,
                    raw_amount=float(log_evt.base_amount),
                    attack_type=attack_type,
                    is_dot_tick=is_dot_tick,
                    # Log events already survived avoidance — don't roll dodge/parry again.
                    is_avoidable=False,
                    # Block applies to physical melee/ranged
                    is_blockable=(
                        attack_type in ("melee", "ranged") and log_evt.school == "physical"
                    ),
                    # ROADMAP 3.9.3 — bleeds bypass armor in WoW; the engine
                    # skips its armor-DR step when this flag is set. Detected
                    # via spell-name fragment (Rake / Rip / Rend / Deep Wounds
                    # / ...) AND periodicity — a same-named direct hit (e.g.
                    # Rake's initial strike, a boss's direct "Searing Rend"
                    # swing) is ordinary armor-mitigated physical damage, not
                    # a bleed (2026-07-18, see core/bleed_detection.py).
                    is_bleed=is_bleed(log_evt.spell_name, is_periodic=is_dot_tick),
                    is_log_replay=True,
                    # log_absorbed = absorbed field from the log: includes base stagger (40%
                    # physical / 10% magic) AND Celestial Brew AND healer shields.
                    # Ironskin Brew's EXTRA stagger beyond the base is NOT in this field —
                    # it reduces (amount+absorbed)/base by ~5% per IB charge. This means
                    # K calibration from Brewmaster logs over-estimates K reduction; use
                    # Prot Warrior logs for canonical K calibration.
                    log_absorbed=float(log_evt.absorbed),
                    active_buffs=_active_at(log_evt.time_s),
                )
            )
        actual_dealt += log_evt.amount
        actual_blocked += log_evt.blocked
        actual_absorbed += log_evt.absorbed
        actual_resisted += log_evt.resisted

    return ReplayData(
        run=run,
        duration_s=run.duration_s(),
        events=events,
        actual_dealt=actual_dealt,
        actual_blocked=actual_blocked,
        actual_absorbed=actual_absorbed,
        actual_resisted=actual_resisted,
        event_count=len(events),
    )
