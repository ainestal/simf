from dataclasses import dataclass, field
from typing import Literal

DamageSchool = Literal["physical", "fire", "shadow", "frost", "nature", "arcane", "holy"]
AttackType = Literal["melee", "ranged", "spell"]


@dataclass
class DamageEvent:
    time_s: float
    source_id: str
    school: DamageSchool
    raw_amount: float
    attack_type: AttackType
    is_dot_tick: bool = False
    is_tank_buster: bool = False
    is_avoidable: bool = True
    is_blockable: bool = True
    # Replay mode: set to True for events loaded from a real combat log.
    # Drives policy (Demo Shout on CD) and mitigation (log_absorbed replaces IP model).
    is_log_replay: bool = False
    # Actual per-event absorb (IP + healer shields) from the log. Applied instead
    # of the sim's IP model in replay mode to isolate armor/block/K calibration.
    log_absorbed: float = 0.0
    # True for self-inflicted events (e.g. Brewmaster Stagger DoT ticks).
    # In log replay, these carry the actual HP amount and bypass further DR.
    is_self_inflicted: bool = False
    # True for bleed events (physical-school DOTs that bypass armor in
    # WoW). Set by the log-replay path via `bleed_detection.is_bleed` on
    # the spell name. ``apply_mitigation`` skips the armor-DR step for
    # these events; without the skip the engine over-mitigates bleeds by
    # ~+37pp (per-school audit 2026-05-23 — see
    # docs/validation/magic_mit_gap_remeasurement_2026_05_23.md, ROADMAP 3.9.3).
    # Synthetic damage profiles can populate this directly when modelling
    # bleed-heavy bosses (Algeth'ar Echo of Doragosa add-phase, etc.).
    is_bleed: bool = False
    # Spell IDs of the tank's buffs active at this event's timestamp, for
    # window-gated mitigation layers whose value is non-linear over their uptime
    # (e.g. Vengeance Metamorphosis' armor multiplier — averaging armor over a
    # ~50%-uptime buff mis-states armor DR, so it must be credited only during
    # the real buff windows). Populated by the log-replay path from the log's
    # SPELL_AURA windows; empty for synthetic profiles and every spec that
    # doesn't read it (so the warrior path stays bit-identical). The
    # time-windowed evolution of ``Character.active_buff_spell_ids`` (which is a
    # single static set per character; this is per-event).
    active_buffs: frozenset[int] = field(default_factory=frozenset)
