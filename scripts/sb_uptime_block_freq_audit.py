"""Post-shield-block-fix bias decomposition, Leads 2 + 3
(docs/validation/protwarrior_post_shield_block_bias_decomposition_2026_07_21.md).

Lead 2 — block FREQUENCY, not magnitude (already fixed 2026-07-21):

  * Real block chance OUTSIDE Shield Block windows vs modeled `base_block()`.
  * Real block chance INSIDE Shield Block windows vs modeled
    `block_chance_during: 1.0`.
  * Real Shield Block UPTIME (merged aura-window duration / run duration,
    ground truth from the log's own SPELL_AURA_APPLIED/REMOVED events) vs
    the SIM's OWN MODELED Shield Block uptime (`SimResult.mean_sb_uptime`,
    averaged across Monte Carlo iterations at the corpus's default
    `skill_modifier=1.0` — "in the zone," press-on-cooldown-with-zero-lag).
    This is a genuinely new comparison in this thread: Shield Block's press
    TIMING during replay is policy-driven (`core/policy.py`'s rage/charge/
    skill-gated cadence), never read from the log's own real cast times —
    unlike block VALUE and block CHANCE-GIVEN-SB-UP, which both roll against
    the log's real per-hit ground truth. A press-rate mismatch here would be
    additive to whatever the chance/magnitude comparisons find.

Lead 3 — avoidance (dodge/parry) frequency: reports the modeled
`base_dodge() + base_parry()` and the real observed miss+dodge+parry rate
from the corpus's own SWING_MISSED/SPELL_MISSED lines, for completeness —
but this comparison is measured **for characterization only**. `io/log_replay.py`
sets `is_avoidable=False` unconditionally on every mob-sourced replayed event
("Log events already survived avoidance — don't roll dodge/parry again") —
avoided attacks never generate a landed-damage event in the first place, so
they're excluded from `replay.events` before `run_simulation` ever sees them,
and the avoidance ROLL (`mitigation.py` step 1) never fires during a
`calibrate-k` sweep. This is structurally identical to the already-documented
Riposte/crit-to-parry finding (`docs/validation/protwarrior_riposte_crit_parry_gap_2026_07_12.md`):
real, but calibration-INERT — it cannot be part of the +23% bias by
construction, regardless of how well or badly it matches.

Usage:
  python scripts/sb_uptime_block_freq_audit.py --logs-dir /path/to/examples
"""

from __future__ import annotations

import argparse
from pathlib import Path

from simf.cli import load_character
from simf.core.bleed_detection import is_bleed
from simf.core.constants import DATA_DIR, load_constants
from simf.core.profiles import load_healing_profile
from simf.core.runner import run_simulation
from simf.io.calibration_corpus import load_calibration_corpus
from simf.io.character_from_combatant_info import _resolve_target_guid
from simf.io.combat_log import parse_challenge_modes
from simf.io.combat_log_buffs import parse_self_buff_windows
from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_damage import parse_damage_event
from simf.io.log_replay import load_replay

SHIELD_BLOCK_BUFF_ID = 132404
LOG_TARGET = "Brutoh-Uldum-EU"
CORPUS_MANIFEST = DATA_DIR / "calibration_corpora" / "prot_warrior_2026_05.yaml"

_DAMAGE_EVENT_TYPES = (
    "SPELL_DAMAGE",
    "SPELL_PERIODIC_DAMAGE",
    "SWING_DAMAGE_LANDED",
    "RANGE_DAMAGE",
)
_MISS_EVENT_TYPES = ("SWING_MISSED", "SPELL_MISSED")


def _in_any_window(windows: list[tuple[float, float]], t: float) -> bool:
    return any(a <= t <= b for a, b in windows)


def _merged_window_duration(windows: list[tuple[float, float]]) -> float:
    if not windows:
        return 0.0
    ws = sorted(windows)
    total = 0.0
    cur_s, cur_e = ws[0]
    for s, e in ws[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s
            cur_s, cur_e = s, e
    total += cur_e - cur_s
    return total


def _miss_type(fields: list[str], event_type: str) -> str | None:
    """missType field for SWING_MISSED (index 8) / SPELL_MISSED (index 11)."""
    idx = 8 if event_type == "SWING_MISSED" else 11
    if len(fields) <= idx:
        return None
    return fields[idx]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logs-dir", default="examples", type=Path)
    ap.add_argument("--iterations", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    corpus = load_calibration_corpus(CORPUS_MANIFEST)
    char = load_character(corpus.character_path)
    heal = load_healing_profile("m+_high_key_healer")
    c = load_constants()

    modeled_base_block = char.base_block()
    modeled_dodge_parry = char.base_dodge() + char.base_parry()
    print("=== Modeled (character-sheet) figures — brutoh-calibration-2026-05.yaml ===")
    print(f"  base_block() [outside SB]:        {modeled_base_block:.4f}")
    print(
        f"  block_chance_during [inside SB]:  "
        f"{c['active_mitigation']['shield_block']['block_chance_during']:.4f}"
    )
    print(
        f"  base_dodge()+base_parry():         {modeled_dodge_parry:.4f}  (calibration-INERT, see docstring)"
    )
    print()

    # Real-log tallies, pooled across the whole ratified corpus.
    blocked_in_sb = 0
    total_in_sb = 0
    blocked_out_sb = 0
    total_out_sb = 0
    avoided_n = 0
    landed_n = 0
    real_sb_duration_total = 0.0
    real_run_duration_total = 0.0

    modeled_sb_time_total = 0.0
    modeled_run_duration_total = 0.0

    per_run_rows: list[tuple[str, float, float]] = []  # label, real_uptime, modeled_uptime

    for filename, run_index in corpus.replays:
        lf = args.logs_dir / filename
        label = f"{filename}[{run_index}]"
        if not lf.exists():
            print(f"  SKIP {label}: file not found at {lf}")
            continue
        runs = parse_challenge_modes(lf)
        replay = load_replay(lf, LOG_TARGET, run_index=run_index, runs=runs)
        tank_guid = _resolve_target_guid(
            lf, LOG_TARGET, start_byte_offset=replay.run.start_byte_offset
        )
        if tank_guid is None:
            print(f"  SKIP {label}: no per-hit live-armor data")
            continue

        windows = parse_self_buff_windows(
            lf,
            LOG_TARGET,
            frozenset({SHIELD_BLOCK_BUFF_ID}),
            start_time_s=replay.run.start_time_s,
            end_time_s=replay.run.end_time_s,
            start_byte_offset=replay.run.start_byte_offset,
        )
        sb_windows = windows.get(SHIELD_BLOCK_BUFF_ID, [])
        real_sb_duration = _merged_window_duration(sb_windows)
        real_sb_duration_total += real_sb_duration
        real_run_duration_total += replay.duration_s

        # Per-hit block-chance tally (physical, non-bleed hits only — same
        # filter as _sb_layer_check.py) + avoidance tally (all physical
        # attacks, landed or missed).
        with lf.open(encoding="utf-8", errors="replace") as f:
            if replay.run.start_byte_offset:
                f.seek(replay.run.start_byte_offset)
            for line in f:
                if LOG_TARGET not in line and tank_guid not in line:
                    continue
                parsed = parse_combat_log_line(line)
                if not parsed:
                    continue
                t, et, fields = parsed
                if t < replay.run.start_time_s:
                    continue
                if replay.run.end_time_s is not None and t > replay.run.end_time_s:
                    break

                if et in _MISS_EVENT_TYPES:
                    # Only count misses landing ON the tank, from a source
                    # that isn't the tank itself (avoid self-buff noise).
                    if len(fields) < 9 or fields[4] != tank_guid:
                        continue
                    if fields[1] == LOG_TARGET or fields[0] == tank_guid:
                        continue
                    mt = _miss_type(fields, et)
                    if mt in ("DODGE", "PARRY"):
                        avoided_n += 1
                    continue

                if et not in _DAMAGE_EVENT_TYPES:
                    continue
                spoof = "SWING_DAMAGE" if et == "SWING_DAMAGE_LANDED" else et
                evt = parse_damage_event(t, spoof, fields, LOG_TARGET)
                if evt is None or evt.source_name == LOG_TARGET:
                    continue
                if evt.school == "physical":
                    landed_n += 1
                info_start = 8 if spoof.startswith("SWING") else 11
                try:
                    info_guid = fields[info_start]
                except IndexError:
                    continue
                if info_guid != tank_guid or evt.base_amount <= 0:
                    continue
                is_periodic = et == "SPELL_PERIODIC_DAMAGE"
                bleed = is_bleed(evt.spell_name, is_periodic=is_periodic)
                if evt.school != "physical" or bleed:
                    continue
                was_blocked = evt.blocked > 0
                if _in_any_window(sb_windows, t):
                    total_in_sb += 1
                    blocked_in_sb += int(was_blocked)
                else:
                    total_out_sb += 1
                    blocked_out_sb += int(was_blocked)

        # Modeled SB uptime — the SAME run_simulation call calibrate-k makes
        # (skill_modifier defaults to 1.0), read back from SimResult.
        from dataclasses import replace as dc_replace

        heal_r = dc_replace(heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1)
        sim = run_simulation(
            char,
            None,
            heal_r,
            iterations=args.iterations,
            seed=args.seed,
            events_override=replay.events,
            duration_override=replay.duration_s,
            compute_metrics=False,
        )
        modeled_uptime = sim.mean_sb_uptime
        modeled_sb_time_total += modeled_uptime * replay.duration_s
        modeled_run_duration_total += replay.duration_s

        real_uptime = real_sb_duration / replay.duration_s if replay.duration_s > 0 else 0.0
        per_run_rows.append((label, real_uptime, modeled_uptime))
        print(
            f"  {label}: real_sb_uptime={real_uptime:.1%}  modeled_sb_uptime={modeled_uptime:.1%}"
            f"  diff={modeled_uptime - real_uptime:+.1%}"
        )

    print()
    print("=== Aggregate ===")
    if total_out_sb:
        print(
            f"  real block chance OUTSIDE SB windows: {blocked_out_sb / total_out_sb:.4f} "
            f"(n={total_out_sb})  vs modeled base_block()={modeled_base_block:.4f}"
        )
    if total_in_sb:
        print(
            f"  real block chance INSIDE SB windows:  {blocked_in_sb / total_in_sb:.4f} "
            f"(n={total_in_sb})  vs modeled block_chance_during=1.0"
        )
    if real_run_duration_total > 0:
        print(
            f"  real SB uptime (time-weighted, merged windows / duration): "
            f"{real_sb_duration_total / real_run_duration_total:.1%}"
        )
    if modeled_run_duration_total > 0:
        print(
            f"  modeled SB uptime (time-weighted mean across corpus, skill_modifier=1.0): "
            f"{modeled_sb_time_total / modeled_run_duration_total:.1%}"
        )
    if landed_n + avoided_n > 0:
        print(
            f"  real dodge+parry rate on physical attacks: {avoided_n / (landed_n + avoided_n):.4f} "
            f"(avoided={avoided_n}, landed={landed_n})  vs modeled "
            f"base_dodge()+base_parry()={modeled_dodge_parry:.4f}  [CALIBRATION-INERT — see docstring]"
        )


if __name__ == "__main__":
    main()
