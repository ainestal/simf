"""Ad-hoc profiler for Phase 5.2 — measures where simf spends time
during a 2000-iteration `run_simulation` and a `cd-plan` brute-force
optimize. Produces:

  - profiles/run_simulation_2000.prof          (binary, cProfile)
  - profiles/cd_plan_optimize.prof             (binary, cProfile)
  - docs/profiling/phase_5_2_raw.md            (auto-generated top-20 tables)

The curated analysis lives in `docs/profiling/phase_5_2_baseline.md`
and is hand-maintained — re-running this script does NOT touch it.
Update that doc when the hot-path picture changes meaningfully.

Invocation:

  .venv/bin/python scripts/profile_sim.py

Re-run after any change that intends to shift the hot path.
"""

from __future__ import annotations

import cProfile
import pstats
from dataclasses import replace as dc_replace
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG = REPO / "examples" / "WoWCombatLog-050626_153703.txt"  # WR+12 Brutoh-Uldum-EU
CHAR = REPO / "src" / "simf" / "data" / "characters" / "brutoh.yaml"
OUT_PROFILE_DIR = REPO / "profiles"
OUT_DOC = REPO / "docs" / "profiling" / "phase_5_2_raw.md"

OUT_PROFILE_DIR.mkdir(exist_ok=True)
OUT_DOC.parent.mkdir(parents=True, exist_ok=True)


def _load_inputs():
    from simf.cli import load_character, load_healing_profile
    from simf.io.log_replay import load_replay

    char = load_character(str(CHAR))
    heal = load_healing_profile("m+_high_key_healer")
    replay = load_replay(LOG, "Brutoh-Uldum-EU", run_index=-1)
    heal = dc_replace(heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1)
    return char, heal, replay


def _profile_run_simulation():
    from simf.core.runner import run_simulation

    char, heal, replay = _load_inputs()
    prof = cProfile.Profile()
    prof.enable()
    run_simulation(
        char,
        None,
        heal,
        iterations=2000,
        seed=42,
        events_override=replay.events,
        duration_override=replay.duration_s,
        compute_metrics=False,
    )
    prof.disable()
    out = OUT_PROFILE_DIR / "run_simulation_2000.prof"
    prof.dump_stats(str(out))
    return out, prof, replay


def _profile_cd_plan():
    from simf.optimizer.cooldown_planner_optimizer import optimize_cooldown_plan

    char, heal, replay = _load_inputs()
    prof = cProfile.Profile()
    prof.enable()
    optimize_cooldown_plan(
        character=char,
        damage_profile=None,
        healing_profile=heal,
        events_override=replay.events,
        duration_override=replay.duration_s,
        search_iterations=100,
        top_n_spikes=6,
        max_candidates=24,
        seed=42,
    )
    prof.disable()
    out = OUT_PROFILE_DIR / "cd_plan_optimize.prof"
    prof.dump_stats(str(out))
    return out, prof


def _top20_table(prof: cProfile.Profile, header: str) -> str:
    lines: list[str] = []
    lines.append(f"### {header}")
    lines.append("")
    lines.append(
        "Top 20 by cumulative time. Columns: `ncalls`, `tottime` (excl. sub),"
        " `cumtime` (incl. sub), `function`."
    )
    lines.append("")
    lines.append("```")
    s2 = StringIO()
    ps2 = pstats.Stats(prof, stream=s2).sort_stats("cumulative")
    ps2.print_stats(20)
    lines.append(s2.getvalue().strip())
    lines.append("```")
    lines.append("")
    lines.append("Top 20 by total time (excluding sub-calls).")
    lines.append("")
    lines.append("```")
    s3 = StringIO()
    ps3 = pstats.Stats(prof, stream=s3).sort_stats("tottime")
    ps3.print_stats(20)
    lines.append(s3.getvalue().strip())
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    import time

    print("Profiling run_simulation (2000 iter, WR+12 Brutoh replay)...")
    t0 = time.perf_counter()
    sim_path, sim_prof, replay = _profile_run_simulation()
    sim_wall = time.perf_counter() - t0
    print(f"  wall: {sim_wall:.2f}s → {sim_path}")

    print("Profiling cd-plan optimize (24 candidates × 100 iter)...")
    t0 = time.perf_counter()
    plan_path, plan_prof = _profile_cd_plan()
    plan_wall = time.perf_counter() - t0
    print(f"  wall: {plan_wall:.2f}s → {plan_path}")

    from datetime import date

    md: list[str] = []
    md.append("# Phase 5.2 raw profile (auto-generated)")
    md.append("")
    md.append(
        "This file is overwritten on every `make profile` run. The curated"
        " analysis lives in `phase_5_2_baseline.md` next to it."
    )
    md.append("")
    md.append(f"Captured {date.today().isoformat()} on the Raspberry Pi host.")
    md.append("")
    md.append("**Inputs**")
    md.append("")
    md.append(f"- character: `{CHAR.name}` (Prot Warrior, brutoh-actual loadout)")
    md.append(
        f"- replay log: `{LOG.name}` — {replay.run.map_name} +{replay.run.key_level},"
        f" {replay.event_count:,} events, {replay.duration_s:.0f}s"
    )
    md.append("- healing profile: `m+_high_key_healer` (baseline_hps scaled to log DTPS × 1.1)")
    md.append("")
    md.append("**Wall-clock**")
    md.append("")
    md.append(f"- `run_simulation` (2 000 iterations): **{sim_wall:.2f}s**")
    md.append(f"- `optimize_cooldown_plan` (24 candidates × 100 iter): **{plan_wall:.2f}s**")
    md.append("")
    md.append("Re-run via `make profile` or `.venv/bin/python scripts/profile_sim.py`.")
    md.append("")
    md.append("---")
    md.append("")
    md.append(_top20_table(sim_prof, "`run_simulation` (2 000 iter)"))
    md.append("---")
    md.append("")
    md.append(_top20_table(plan_prof, "`optimize_cooldown_plan` (24 × 100 iter)"))
    md.append("---")
    md.append("")
    md.append("## What's in this file")
    md.append("")
    md.append("Just the raw cProfile top-20s. The interpretation — what's actually")
    md.append("worth optimizing, what's a measurement artifact, what the trend is —")
    md.append("is in `phase_5_2_baseline.md`. Update that doc when the numbers above")
    md.append("shift in a way that changes the priority list.")
    md.append("")

    OUT_DOC.write_text("\n".join(md))
    print(f"Wrote summary: {OUT_DOC}")


if __name__ == "__main__":
    main()
