"""Rank ALL tank auras by how much they explain per-hit mitigation residuals.

Companion to scripts/per_hit_mitigation_forensics.py (see
docs/validation/phase4_brewmaster_physical_gap_decomposition_2026_07_04.md).
Use when a run shows an unexplained residual layer and you want to hunt WHICH
buff/debuff on the tank co-moves with it — with no prior watch-list: every
SPELL_AURA_APPLIED/REMOVED window on the tank is scored by the damage-weighted
mean residual inside vs outside its windows.

Caveat: an aura applied BEFORE the run window and never re-applied inside it
is invisible here (no APPLY event to open a window) — cross-check the
COMBATANT_INFO aura lists for those. A null result across all windows +
COMBATANT_INFO constants is itself evidence the layer is not aura-borne
(that null is what pointed the 2026-07-04 investigation at run-scoped
mob-side tuning baked between `base_amount` and applied damage).

Usage:
  python scripts/aura_attribution.py LOG --tank Name-Realm-Region \
      [--run-index -1] [--vers 0.036] [--k-override N] [--top 25]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from simf.core.constants import load_constants
from simf.io.character_from_combatant_info import _resolve_target_guid
from simf.io.combat_log import parse_challenge_modes
from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_damage import parse_damage_event

# Big defensive-CD buffs excluded from the hit pool so their (known, modeled)
# effect doesn't drown the hunt for unknown layers.
CD_BUFFS = {120954, 122278, 122783, 115176, 871, 23920}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", type=Path)
    ap.add_argument("--tank", required=True)
    ap.add_argument("--run-index", type=int, default=-1)
    ap.add_argument("--vers", type=float, default=0.0)
    ap.add_argument("--k-override", type=float, default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-uptime-s", type=float, default=45.0)
    args = ap.parse_args()

    runs = parse_challenge_modes(args.log)
    if not runs:
        raise SystemExit(f"no CHALLENGE_MODE runs in {args.log}")
    run = runs[args.run_index if args.run_index >= 0 else len(runs) + args.run_index]
    tank_guid = _resolve_target_guid(args.log, args.tank, start_byte_offset=run.start_byte_offset)
    if tank_guid is None:
        raise SystemExit(f"could not resolve GUID for {args.tank!r}")
    k = args.k_override if args.k_override is not None else load_constants()["armor"]["k_constant"]

    on: dict[int, float] = {}
    windows: dict[int, list[tuple[float, float]]] = defaultdict(list)
    names: dict[int, str] = {}
    hits: list[tuple[float, str, int, float]] = []
    last_t = run.start_time_s

    with args.log.open(encoding="utf-8", errors="replace") as f:
        if run.start_byte_offset:
            f.seek(run.start_byte_offset)
        for line in f:
            if args.tank not in line and tank_guid not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if time_s < run.start_time_s:
                continue
            if run.end_time_s is not None and time_s > run.end_time_s:
                break
            last_t = time_s

            if event_type in ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED"):
                if len(fields) < 12 or fields[4] != tank_guid:
                    continue
                try:
                    sid = int(fields[8])
                except ValueError:
                    continue
                names[sid] = fields[9]
                if event_type == "SPELL_AURA_APPLIED":
                    on.setdefault(sid, time_s)
                elif sid in on:
                    windows[sid].append((on.pop(sid), time_s))
                continue

            if event_type in (
                "SPELL_DAMAGE",
                "SPELL_PERIODIC_DAMAGE",
                "SWING_DAMAGE_LANDED",
                "RANGE_DAMAGE",
            ):
                spoof = "SWING_DAMAGE" if event_type == "SWING_DAMAGE_LANDED" else event_type
                evt = parse_damage_event(time_s, spoof, fields, args.tank)
                if evt is None or evt.source_name == args.tank:
                    continue
                info_start = 8 if spoof.startswith("SWING") else 11
                try:
                    if fields[info_start] != tank_guid:
                        continue
                    armor_live = int(fields[info_start + 6])
                except (ValueError, IndexError):
                    continue
                if evt.base_amount <= 0 or armor_live <= 0:
                    continue
                r = (evt.amount + evt.absorbed + evt.blocked) / evt.base_amount
                armor_dr = armor_live / (armor_live + k)
                expl = (
                    (1 - armor_dr) * (1 - args.vers)
                    if evt.school == "physical"
                    else (1 - args.vers)
                )
                hits.append((time_s, evt.school, evt.base_amount, r / expl))

    for sid, t in on.items():
        windows[sid].append((t, last_t))

    def inside(sid: int, t: float) -> bool:
        return any(a <= t <= b for a, b in windows[sid])

    cd_ids = [s for s in CD_BUFFS if windows.get(s)]
    clean = [hw for hw in hits if not any(inside(s, hw[0]) for s in cd_ids)]
    wsum = sum(w for _, _, w, _ in clean)
    print(
        f"{run.map_name} +{run.key_level} tank={args.tank}: hits(clean)={len(clean)} "
        f"overall wmean resid={sum(w * x for _, _, w, x in clean) / wsum:.4f}"
    )

    scored = []
    for sid, ws in windows.items():
        up = sum(b - a for a, b in ws)
        if up < args.min_uptime_s:
            continue
        ins = [(w, x) for t, _, w, x in clean if inside(sid, t)]
        outs = [(w, x) for t, _, w, x in clean if not inside(sid, t)]
        if len(ins) < 25 or len(outs) < 25:
            continue
        wi, wo = sum(w for w, _ in ins), sum(w for w, _ in outs)
        mi = sum(w * x for w, x in ins) / wi
        mo = sum(w * x for w, x in outs) / wo
        share = wi / (wi + wo)
        scored.append((abs(mi - mo) * min(share, 1 - share) * 2, sid, up, share, mi, mo, len(ins)))

    scored.sort(reverse=True)
    print(
        f"\n{'power':>6} {'spell':>8} {'name':<30} {'uptime_s':>8} {'dmg_share_in':>12} "
        f"{'resid_in':>9} {'resid_out':>9} {'n_in':>5}"
    )
    for power, sid, up, share, mi, mo, n in scored[: args.top]:
        print(
            f"{power:6.3f} {sid:>8} {names.get(sid, '?'):<30} {up:8.0f} {share:12.2f} "
            f"{mi:9.4f} {mo:9.4f} {n:>5}"
        )


if __name__ == "__main__":
    main()
