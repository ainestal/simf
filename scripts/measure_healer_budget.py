"""Measure real healing-received-on-tank from the calibrated log corpora, to
parameterize the Top-5 #3 healer-throughput-cap replacement (2026-07-06
retrospective) from data instead of a free fit.

Design (calibration-scientist review, 2026-07-06): a token-bucket budget
with capacity B and refill rate R. R = the sustainable HPS ceiling; B =
the bank size needed to fully cover one realistic burst episode on top of
R. Measured as:

    R = p95 across all TIMED runs' rolling 60s heal-received-HPS windows
    B = max observed rolling 10s heal-received-SUM across all runs, minus R*10s

Only "success=True" (timed) runs from the same corpora CONTRIBUTING.md calls
calibrated (Prot Warrior: examples/*.txt, target Brutoh-Uldum-EU; Guardian
Druid: examples/anonguardian1-guardian/*.txt, target AnonGuardian1-AnonRealm1-EU) — reuses
the same run-detection path as scripts/calibrate_spec_from_logs.py
(parse_challenge_modes + detect_party_roles) so "the corpus" means the
same thing in both tools.

Usage: python scripts/measure_healer_budget.py [--logs-dir examples] [--target Brutoh-Uldum-EU]
"""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path

import numpy as np

from simf.core.character import Character
from simf.io.character_from_combatant_info import hydrate_character
from simf.io.combat_log import detect_party_roles, parse_challenge_modes
from simf.io.combat_log_healing import iter_heal_events


def _timed_tank_runs(logs_dir: Path, target_name: str):
    """Yield (log_path, run) for every TIMED run whose detected tank matches target_name."""
    for lf in sorted(logs_dir.glob("*.txt")):
        try:
            runs = parse_challenge_modes(lf)
        except Exception:
            continue
        for run in runs:
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
            if any(p.role == "tank" and p.name == target_name for p in party):
                yield lf, run


def _rolling_window_sums(
    timeline: list[tuple[float, float]], window_s: float, duration_s: float
) -> np.ndarray:
    """All rolling-window sums (not just the max) — 1s bins, half-open [t, t+window_s)."""
    if not timeline:
        return np.array([])
    dt = 1.0
    times = np.fromiter((t for t, _ in timeline), dtype=np.float64, count=len(timeline))
    amounts = np.fromiter((a for _, a in timeline), dtype=np.float64, count=len(timeline))
    n_starts = int(duration_s / dt) + 1
    window_bins = max(1, round(window_s / dt))
    n_bins = n_starts + window_bins
    bin_idx = (times / dt).astype(np.int64)
    in_range = (bin_idx >= 0) & (bin_idx < n_bins)
    bin_idx = bin_idx[in_range]
    amounts = amounts[in_range]
    if bin_idx.size == 0:
        return np.zeros(n_starts)
    per_bin = np.zeros(n_bins, dtype=np.float64)
    np.add.at(per_bin, bin_idx, amounts)
    cs = np.empty(n_bins + 1, dtype=np.float64)
    cs[0] = 0.0
    np.cumsum(per_bin, out=cs[1:])
    return cs[window_bins : window_bins + n_starts] - cs[:n_starts]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs-dir", default="examples")
    ap.add_argument("--target", default="Brutoh-Uldum-EU")
    args = ap.parse_args()

    logs_dir = Path(args.logs_dir)
    all_60s_hps: list[float] = []
    max_10s_sum = 0.0
    n_runs = 0
    n_heal_events = 0
    max_hp_values: list[float] = []

    for lf, run in _timed_tank_runs(logs_dir, args.target):
        n_runs += 1
        duration_s = run.duration_s()
        hyd = hydrate_character(
            lf,
            args.target,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
        )
        if hyd is not None:
            with contextlib.suppress(Exception):
                max_hp_values.append(Character.from_dict(dict(hyd.char_data)).max_hp())
        timeline: list[tuple[float, float]] = []
        for evt in iter_heal_events(lf, args.target, run.start_time_s, run.end_time_s):
            # Exclude self-heals (Ignore Pain, Word of Glory, Death Strike,
            # Purifying Brew, ...) — those are already modeled per-spec
            # elsewhere in the engine, not through the shared HealingProfile
            # this budget caps. Counting them here would attribute the
            # tank's own self-sustain to "the healer's" throughput ceiling.
            if evt.source_name == args.target:
                continue
            net = max(0, evt.amount - evt.overhealing)
            if net <= 0:
                continue
            timeline.append((evt.time_s - run.start_time_s, float(net)))
            n_heal_events += 1
        if not timeline:
            continue
        w60 = _rolling_window_sums(timeline, 60.0, duration_s)
        if w60.size:
            all_60s_hps.extend((w60 / 60.0).tolist())
        w10 = _rolling_window_sums(timeline, 10.0, duration_s)
        if w10.size:
            max_10s_sum = max(max_10s_sum, float(w10.max()))

    if not all_60s_hps:
        print(f"No matching timed runs / heal events for target={args.target!r} in {logs_dir}")
        return

    hps_arr = np.array(all_60s_hps)
    r_p95 = float(np.percentile(hps_arr, 95))
    b_capacity = max(0.0, max_10s_sum - r_p95 * 10.0)

    print(f"target={args.target!r} logs_dir={logs_dir} runs={n_runs} heal_events={n_heal_events}")
    print(f"R (p95 sustained 60s-window HPS)  = {r_p95:,.0f} HP/s")
    print(f"max observed 10s heal-received sum = {max_10s_sum:,.0f} HP")
    print(f"B (bank capacity, max_10s - R*10)  = {b_capacity:,.0f} HP")
    if max_hp_values:
        mean_max_hp = float(np.mean(max_hp_values))
        print(
            f"mean max_hp over {len(max_hp_values)} hydrated runs = {mean_max_hp:,.0f} "
            f"(range {min(max_hp_values):,.0f}-{max(max_hp_values):,.0f})"
        )
        print(f"R as %-of-max-hp per second = {r_p95 / mean_max_hp * 100:.2f}%")
        print(f"B as %-of-max-hp            = {b_capacity / mean_max_hp * 100:.1f}%")


if __name__ == "__main__":
    main()
