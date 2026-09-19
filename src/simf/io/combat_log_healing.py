"""Healing-received event parsing — the HealReceivedEvent model + its extractors.

Mirrors ``combat_log_damage.py``'s split (real events, ACL-optional
position tail sliced from the end since the ACL power-type block in the
middle is variable-length). Built for Top-5 #3 (2026-07-06 retrospective):
measuring real healing-received-on-tank from the existing log corpus, to
parameterize the healer-throughput cap from data instead of a free fit.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from simf.io.combat_log_core import HEAL_EVENTS, parse_combat_log_line


@dataclass
class HealReceivedEvent:
    time_s: float  # absolute Unix timestamp seconds
    event_type: str  # "SPELL_HEAL" | "SPELL_PERIODIC_HEAL"
    source_name: str
    spell_name: str
    amount: int  # gross heal INCLUDING overhealing; effective = amount - overhealing
    overhealing: int
    absorbed: int
    is_critical: bool


def parse_heal_event(
    time_s: float, event_type: str, fields: list[str], target_name: str
) -> HealReceivedEvent | None:
    if event_type not in HEAL_EVENTS:
        return None
    if len(fields) < 11:
        return None
    if fields[5] != target_name:
        return None

    source_name = fields[1]
    spell_name = fields[9]

    # Suffix is the last 5 fields regardless of ACL: amount, base_amount,
    # overhealing, absorbed, critical. The ACL power-type block in the
    # middle is variable-length (a unit can report multiple power bars),
    # so — same trick as combat_log_damage.py — slice from the END, never
    # from a fixed positive offset.
    suffix = fields[-5:]
    try:
        amount = int(suffix[0])
        overhealing = int(suffix[2])
        absorbed = int(suffix[3])
        is_critical = suffix[4] not in ("nil", "0", "")
    except (ValueError, IndexError):
        return None

    return HealReceivedEvent(
        time_s=time_s,
        event_type=event_type,
        source_name=source_name,
        spell_name=spell_name,
        amount=amount,
        overhealing=overhealing,
        absorbed=absorbed,
        is_critical=is_critical,
    )


def iter_heal_events(
    log_path: Path,
    target_name: str,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
) -> Iterator[HealReceivedEvent]:
    """Yield healing-received events on `target_name`, optionally bounded by a time window."""
    with log_path.open() as f:
        for line in f:
            if target_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if start_time_s is not None and time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                continue
            evt = parse_heal_event(time_s, event_type, fields, target_name)
            if evt is not None:
                yield evt
