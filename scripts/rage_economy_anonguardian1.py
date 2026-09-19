"""Guardian rage-economy extractor — grounds the haste→rage→Ironfur elasticity.

The haste→Ironfur survival loop's strength = the fraction of rage generation that
SCALES WITH HASTE (auto-attacks, ability GCDs, DoT tick rate) vs the fraction that
does NOT (e.g. rage from damage taken, fixed procs). AnonGuardian1's haste is gear-locked
across their runs, so the elasticity coefficient k must come from this source
breakdown, not from observed haste variation.

Per timed Guardian run, a single windowed forward pass sums SPELL_ENERGIZE rage
(powerType 1) by source spell, plus Ironfur cast cadence — so we can see what
share of rage each source contributes and how rage-starved Ironfur is.

  .venv/bin/python -u scripts/rage_economy_anonguardian1.py --logs-dir examples/anonguardian1-guardian [--limit N]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from simf.io.combat_log import detect_party_roles, parse_challenge_modes, parse_combat_log_line

IRONFUR_ID = "192081"


def _guardian_timed_runs(logs_dir: Path):
    for lf in sorted(logs_dir.glob("*.txt")):
        try:
            runs = parse_challenge_modes(lf)
        except Exception:
            continue
        for i, run in enumerate(runs):
            if run.success is not True:
                continue
            try:
                party = detect_party_roles(
                    lf,
                    start_time_s=run.start_time_s,
                    end_time_s=run.end_time_s,
                    start_byte_offset=run.start_byte_offset,
                )
            except Exception:
                continue
            for t in (p for p in party if p.role == "tank"):
                if (getattr(t, "class_spec", "") or getattr(t, "spec", "")) == "guardian_druid":
                    yield lf, i, run, t


def _rage_pass(log_path: Path, tank: str, run) -> dict:
    start, end = run.start_time_s, run.end_time_s
    by_source: dict[str, float] = defaultdict(float)
    if_casts = 0
    if_fail_rage = 0
    with log_path.open() as f:
        if run.start_byte_offset > 0:
            f.seek(run.start_byte_offset)
        for line in f:
            if tank not in line:
                continue
            if "SPELL_ENERGIZE" not in line and IRONFUR_ID not in line:
                continue
            parsed = parse_combat_log_line(line)
            if parsed is None:
                continue
            t, etype, fields = parsed
            if t < start:
                continue
            if t > end:
                break
            if etype == "SPELL_ENERGIZE" and len(fields) >= 6 and fields[5] == tank:
                # suffix: amount, overEnergize, powerType, maxPower
                try:
                    power_type = fields[-2]
                    amount = float(fields[-4])
                except (ValueError, IndexError):
                    continue
                if power_type == "1":  # rage
                    by_source[f"{fields[8]} {fields[9]}"] += amount
            elif fields[8:9] == [IRONFUR_ID] and fields[1] == tank:
                if etype == "SPELL_CAST_SUCCESS":
                    if_casts += 1
                elif etype == "SPELL_CAST_FAILED" and "rage" in fields[-1].lower():
                    if_fail_rage += 1
    dur = run.duration_s()
    total = sum(by_source.values())
    return {
        "by_source": dict(by_source),
        "total_rage": total,
        "rage_per_s": total / dur if dur else 0.0,
        "if_casts": if_casts,
        "if_fail_rage": if_fail_rage,
        "dur": dur,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-dir", default="examples/anonguardian1-guardian")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    runs = list(_guardian_timed_runs(Path(args.logs_dir)))
    if args.limit:
        runs = runs[: args.limit]
    print(f"# Rage economy — {len(runs)} timed Guardian runs\n", flush=True)

    agg: dict[str, float] = defaultdict(float)
    agg_total = 0.0
    for lf, i, run, t in runs:
        st = _rage_pass(lf, t.name, run)
        label = f"{lf.name[:20]}[{i}] {run.map_name[:14]}+{run.key_level}"
        top = sorted(st["by_source"].items(), key=lambda kv: -kv[1])[:6]
        share = (
            ", ".join(
                f"{name.split(' ', 1)[-1][:14]}={amt / st['total_rage'] * 100:.0f}%"
                for name, amt in top
            )
            if st["total_rage"]
            else ""
        )
        print(
            f"{label} | dur {st['dur']:.0f}s | rage/s {st['rage_per_s']:.1f} | "
            f"IF {st['if_casts']}cast/{st['if_fail_rage']}fail | {share}",
            flush=True,
        )
        for name, amt in st["by_source"].items():
            agg[name] += amt
            agg_total += amt

    if agg_total:
        print("\n# AGGREGATE rage-source breakdown (all runs):", flush=True)
        for name, amt in sorted(agg.items(), key=lambda kv: -kv[1]):
            print(f"  {amt / agg_total * 100:5.1f}%  {name}", flush=True)


if __name__ == "__main__":
    main()
