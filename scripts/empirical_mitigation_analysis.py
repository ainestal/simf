"""Empirical mitigation analysis for K and Defensive Stance tuning.

Goal: back-solve simf's armor K and Defensive Stance DR directly from a real
M+ combat log, by isolating events with no active mitigation buttons (Shield
Block, Demo Shout, Shield Wall) and measuring the achieved mitigation ratio.

Outputs the empirical mitigation ratio per school, and what (K, DS) values
would match that ratio under simf's formula structure.

Run: python scripts/empirical_mitigation_analysis.py <log_path>
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from simf.io.combat_log import (
    iter_damage_events,
    parse_challenge_modes,
)

# Buff aura events that change mitigation. We parse SPELL_AURA_APPLIED /
# SPELL_AURA_REMOVED to track buff intervals.
MITIGATION_BUFFS = {
    "Shield Block": 132404,
    "Demoralizing Shout": 1160,
    "Shield Wall": 871,
    "Last Stand": 12975,
    "Ignore Pain": 190456,  # not a DR but track for context
}


def parse_buff_windows(log_path: Path, target_name: str, t0: float, t1: float):
    """Return dict[buff_name] -> list[(start, end)] active intervals for target."""
    active: dict[str, float] = {}  # buff -> start time
    windows: dict[str, list[tuple[float, float]]] = defaultdict(list)

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "SPELL_AURA_" not in line:
                continue
            # Parse timestamp manually (first two whitespace-separated fields)
            # Format: "4/14 23:09:01.123  SPELL_AURA_APPLIED,..."
            try:
                ts_part, rest = line.split("  ", 1)
            except ValueError:
                continue
            try:
                # Time format: M/D HH:MM:SS.fff
                _date_part, time_part = ts_part.split(" ", 1)
                h, m, s = time_part.split(":")
                t = int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                continue
            if t < t0 - 30 or t > t1 + 30:
                continue

            fields = rest.strip().split(",")
            if len(fields) < 13:
                continue
            event_type = fields[0]
            if event_type not in ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED"):
                continue

            # Destination is field 5/6 (dest_guid, dest_name)
            try:
                dest_name = fields[6].strip('"')
            except IndexError:
                continue
            if dest_name != target_name:
                continue

            try:
                spell_name = fields[10].strip('"')
            except IndexError:
                continue
            if spell_name not in MITIGATION_BUFFS:
                continue

            if event_type == "SPELL_AURA_APPLIED":
                active[spell_name] = t
            elif event_type == "SPELL_AURA_REMOVED":
                start = active.pop(spell_name, None)
                if start is not None:
                    windows[spell_name].append((start, t))

    # Close any still-active at end-of-log
    for name, start in active.items():
        windows[name].append((start, t1))

    return windows


def any_active(buff_windows: dict[str, list[tuple[float, float]]], t: float) -> dict[str, bool]:
    """Return which mitigation buffs are active at time t."""
    return {
        name: any(s <= t <= e for s, e in intervals) for name, intervals in buff_windows.items()
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("log_path", type=Path)
    p.add_argument("--target", default=None, help="Player name (default: detect)")
    args = p.parse_args()

    runs = parse_challenge_modes(args.log_path)
    if not runs:
        print("No challenge modes in log")
        return

    for run in runs:
        if run.end_time_s is None:
            continue
        # Need target name — detect from first damage event if not given
        target = args.target
        if target is None:
            from simf.io.combat_log import detect_destination_player_names

            names = detect_destination_player_names(
                args.log_path,
                start_time_s=run.start_time_s,
                end_time_s=run.end_time_s,
            )
            if not names:
                continue
            # names is a list of (name, count) tuples — take the most-frequent name
            target = names[0][0] if isinstance(names[0], tuple) else names[0]

        print(f"\n=== {run.map_name} +{run.key_level} ===")
        print(f"Target: {target}  Duration: {run.end_time_s - run.start_time_s:.0f}s")

        # Parse buff windows for this run
        bufs = parse_buff_windows(args.log_path, target, run.start_time_s, run.end_time_s)
        for name, intervals in bufs.items():
            total = sum(e - s for s, e in intervals)
            print(f"  {name}: {len(intervals)} applications, {total:.0f}s total")

        # Iterate damage events; bucket by school + button-state
        buckets: dict[tuple[str, str], list[tuple[int, int, int, int]]] = defaultdict(list)
        # bucket key: (school, label) where label encodes which buttons active
        # value: (base_amount, amount, absorbed, blocked)

        for evt in iter_damage_events(args.log_path, target, run.start_time_s, run.end_time_s):
            if evt.source_name == target:
                continue  # self-inflicted (stagger)
            if evt.base_amount <= 0:
                continue
            t = evt.time_s
            active = any_active(bufs, t)
            # Label by which CDs are active
            tags = []
            if active.get("Shield Block"):
                tags.append("SB")
            if active.get("Demoralizing Shout"):
                tags.append("DS_shout")  # demo shout (different from defensive stance)
            if active.get("Shield Wall"):
                tags.append("SW")
            if active.get("Last Stand"):
                tags.append("LS")
            label = "+".join(tags) if tags else "no_CDs"

            school = evt.school
            buckets[(school, label)].append(
                (evt.base_amount, evt.amount, evt.absorbed, evt.blocked)
            )

        # Report
        print(
            "\n  School | CD state | events | sum(base) | sum(amount) | mit_ratio | dmg_taken_pct"
        )
        for (school, label), entries in sorted(buckets.items()):
            sb = sum(b for b, a, ab, bl in entries)
            sa = sum(a for b, a, ab, bl in entries)
            sab = sum(ab for b, a, ab, bl in entries)
            sbl = sum(bl for b, a, ab, bl in entries)
            if sb == 0:
                continue
            # mit_ratio = amount / base (post-mitigation / pre-mitigation)
            # dmg_taken_pct = (amount + absorbed + blocked) / base
            #   — this is "what mitigation actually achieved" before absorbs/block, i.e.,
            #     just armor + vers + buffs.
            mit_ratio = sa / sb
            full_taken_ratio = (sa + sab + sbl) / sb
            print(
                f"  {school:9s} | {label:25s} | {len(entries):5d} | "
                f"{sb:12,d} | {sa:12,d} | {mit_ratio:.4f} | {full_taken_ratio:.4f}"
            )

        # Back-solve K and DS for the "no_CDs" buckets — pure armor + vers + DS
        # Assumption: Brutoh has 5584 armor, 4% versatility (from his current profile)
        ARMOR = 5584
        VERS = 0.04  # from his rough current value

        for school in ("physical", "shadow", "fire", "frost", "arcane", "nature", "holy"):
            key = (school, "no_CDs")
            if key not in buckets:
                continue
            entries = buckets[key]
            sb = sum(b for b, a, ab, bl in entries)
            sa = sum(a for b, a, ab, bl in entries)
            sab = sum(ab for b, a, ab, bl in entries)
            sbl = sum(bl for b, a, ab, bl in entries)
            if sb == 0:
                continue
            # "Raw mitigation ratio" — strip absorbs and block from the picture.
            # Pre-block, pre-absorb damage = amount + absorbed + blocked
            # This is what made it through armor + vers + DS.
            pre_block_amount = sa + sab + sbl
            armor_vers_ds_ratio = pre_block_amount / sb
            # For physical: ratio = (1 - armor_DR) * (1 - vers) * (1 - DS_phys)
            # For magic:    ratio = 1.0           * (1 - vers) * (1 - DS_magic)
            if school == "physical":
                # Two unknowns — assume DS_phys, solve for K
                for ds_assumed in (0.00, 0.10, 0.15, 0.20):
                    armor_dr = 1 - armor_vers_ds_ratio / ((1 - VERS) * (1 - ds_assumed))
                    if 0 < armor_dr < 0.90:
                        K = ARMOR * (1 / armor_dr - 1)
                        print(
                            f"    [{school}] If DS_phys = {ds_assumed:.2f} → "
                            f"armor_DR = {armor_dr:.4f} → K = {K:.0f}"
                        )
            else:
                ds_solved = 1 - armor_vers_ds_ratio / (1 - VERS)
                print(
                    f"    [{school}] empirical (armor+vers+DS) ratio = {armor_vers_ds_ratio:.4f} → "
                    f"DS_{school} = {ds_solved:.4f}"
                )


if __name__ == "__main__":
    main()
