"""One-off, READ-ONLY check: is the combat log's own `resisted` suffix field
ever nonzero in the ratified Prot Warrior corpus, and if so, does it
correlate with magic-school damage specifically?

Motivation: `scripts/full_chain_wedge.py`'s `r = (amount + absorbed +
blocked) / base_amount` formula deliberately excludes `resisted` from the
numerator (confirmed by reading the script, not assumed). If real logs
carry a nonzero `resisted` value on magic-school hits specifically (a
partial-resist mechanic driven by a mob-level vs. player-level differential,
plausible in Mythic+ where mobs can be effectively over-leveled relative to
the player), that would show up as `resisted/base_amount` unaccounted for in
`r`, i.e. as a chunk of the "clean wedge" residual that isn't really a
missing DR layer at all -- it's already being subtracted by Blizzard before
`amount` is ever written to the log, but not folded back into what the
wedge script calls "what really happened".

This script does NOT touch mitigation.py/character.py/policy.py/
constants.yaml or any existing validation doc. It only reads raw combat log
lines already sitting in examples/ against the ratified 16-log manifest, and
prints an aggregate table. Mirrors the scripts/_sb_layer_check.py one-off
convention (this is a new file, not an edit to an existing script).
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simf.io.combat_log import iter_damage_events, parse_challenge_modes

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "src/simf/data/calibration_corpora/prot_warrior_2026_05.yaml"
EXAMPLES_DIR = REPO_ROOT / "examples"

# Rough physical-vs-magic split by simf's own school_name() strings.
PHYSICAL_SCHOOLS = {"physical"}


def main() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text())
    target_name = manifest["log_target"]

    # by school: [n_hits, n_hits_with_resisted>0, sum(resisted), sum(base_amount)]
    by_school: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    per_run_rows = []

    for replay in manifest["replays"]:
        log_path = EXAMPLES_DIR / replay["file"]
        if not log_path.exists():
            print(f"MISSING: {log_path}")
            continue
        runs = parse_challenge_modes(log_path)
        run_index = replay["run_index"]
        if run_index < 0:
            run_index = len(runs) + run_index
        if not (0 <= run_index < len(runs)):
            print(f"SKIP (run_index {run_index} out of range): {log_path.name}")
            continue
        run = runs[run_index]

        run_resisted_total = 0
        run_base_total = 0
        run_hits = 0
        run_resisted_hits = 0

        for evt in iter_damage_events(log_path, target_name, run.start_time_s, run.end_time_s):
            if evt.source_name == target_name:
                continue  # self-inflicted (e.g. Shield Slam bleed) -- not mob->tank
            if evt.base_amount <= 0:
                continue
            row = by_school[evt.school]
            row[0] += 1
            row[3] += evt.base_amount
            run_hits += 1
            run_base_total += evt.base_amount
            if evt.resisted > 0:
                row[1] += 1
                row[2] += evt.resisted
                run_resisted_hits += 1
                run_resisted_total += evt.resisted

        pct = (run_resisted_total / run_base_total * 100) if run_base_total else 0.0
        per_run_rows.append(
            (
                replay["dungeon"],
                run_hits,
                run_resisted_hits,
                run_resisted_total,
                run_base_total,
                pct,
            )
        )

    print("=" * 100)
    print("Per-run: resisted-field usage against the tank (mob -> tank hits only)")
    print("=" * 100)
    print(
        f"{'dungeon':35s} {'hits':>7s} {'resisted_hits':>14s} "
        f"{'sum_resisted':>13s} {'sum_base':>13s} {'resisted_pct':>13s}"
    )
    for dungeon, hits, r_hits, r_sum, base_sum, pct in per_run_rows:
        print(f"{dungeon:35s} {hits:7d} {r_hits:14d} {r_sum:13d} {base_sum:13d} {pct:12.4f}%")

    print()
    print("=" * 100)
    print("Aggregate by school (mob -> tank hits, whole corpus)")
    print("=" * 100)
    print(
        f"{'school':12s} {'n_hits':>8s} {'n_resisted_hits':>16s} "
        f"{'sum_resisted':>14s} {'sum_base_amount':>16s} {'resisted_pct_of_base':>21s}"
    )
    total_hits = total_resisted_hits = total_resisted = total_base = 0
    for school, (n_hits, n_r_hits, r_sum, base_sum) in sorted(by_school.items()):
        pct = (r_sum / base_sum * 100) if base_sum else 0.0
        print(f"{school:12s} {n_hits:8d} {n_r_hits:16d} {r_sum:14d} {base_sum:16d} {pct:20.4f}%")
        total_hits += n_hits
        total_resisted_hits += n_r_hits
        total_resisted += r_sum
        total_base += base_sum

    print("-" * 100)
    overall_pct = (total_resisted / total_base * 100) if total_base else 0.0
    print(
        f"{'TOTAL':12s} {total_hits:8d} {total_resisted_hits:16d} "
        f"{total_resisted:14d} {total_base:16d} {overall_pct:20.4f}%"
    )


if __name__ == "__main__":
    main()
