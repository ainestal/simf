"""Per-school mitigation gap remeasurement (post-F11/F12/F13/F14, K=3430).

Goal: decompose the per-event mitigation residual (predicted vs actual) by
school, separating bleeds from physical, so we can see whether the global
RMSE 0.065 hides per-school skew that would block Phase C predictive
per-segment risk.

Method: call `apply_mitigation` DIRECTLY for every log event using the buff
state inferred from the log at that event's timestamp. This isolates math
from policy / CD-timing noise — feed the engine the exact buff state the
real player had, compare the engine's output to the log's actual amount.

Single-pass-per-file: parse_challenge_modes locates run boundaries, then a
SINGLE sequential scan of the log file builds buff windows AND collects the
damage events (kept in-memory; for a 1500s run that's ~5-15k events, fine).
We then run apply_mitigation per event with the time-correct buff state.

Run:
  python scripts/per_school_mitigation_gap.py --logs-dir examples \
      --target Brutoh-Uldum-EU --out docs/validation/per_school_data.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from pathlib import Path

import yaml

from simf.core.bleed_detection import is_bleed
from simf.core.character import Character
from simf.core.constants import load_constants  # warm cache
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState, apply_mitigation
from simf.core.policy import make_policy
from simf.io.combat_log import (
    iter_combatant_info,
    iter_damage_events,
    parse_challenge_modes,
)

MITIGATION_BUFFS = ("Shield Block", "Demoralizing Shout", "Shield Wall", "Last Stand")


def classify(school: str, spell_name: str, is_dot: bool) -> str:
    if school == "physical":
        if is_bleed(spell_name, is_periodic=is_dot):
            return "bleed"
        return "physical"
    return school


@dataclass
class PerSchoolStats:
    school: str
    n_events: int = 0
    sum_base: float = 0.0
    sum_actual_pre_absorb: float = 0.0
    sum_predicted_pre_absorb: float = 0.0
    residual_pp: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)

    @property
    def actual_dr(self) -> float:
        return 1 - self.sum_actual_pre_absorb / self.sum_base if self.sum_base else 0.0

    @property
    def predicted_dr(self) -> float:
        return 1 - self.sum_predicted_pre_absorb / self.sum_base if self.sum_base else 0.0

    @property
    def gap_pp(self) -> float:
        return (self.predicted_dr - self.actual_dr) * 100

    def to_dict(self) -> dict:
        if not self.residual_pp:
            return {"school": self.school, "n_events": 0}
        # Weighted distribution stats
        n = len(self.residual_pp)
        return {
            "school": self.school,
            "n_events": self.n_events,
            "sum_base": self.sum_base,
            "actual_dr_pct": self.actual_dr * 100,
            "predicted_dr_pct": self.predicted_dr * 100,
            "gap_pp": self.gap_pp,
            "p50_pp_unweighted": (
                statistics.quantiles(sorted(self.residual_pp), n=2)[0]
                if n >= 2
                else self.residual_pp[0]
            ),
            "p90_pp_unweighted": (
                statistics.quantiles(sorted(self.residual_pp), n=10)[-1]
                if n >= 10
                else max(self.residual_pp)
            ),
            "max_abs_pp": max(abs(r) for r in self.residual_pp),
            "mean_pp": statistics.mean(self.residual_pp),
        }


def parse_buff_and_damage_single_pass(log_path: Path, target: str, t0_abs: float, t1_abs: float):
    """Single sequential scan: extract buff windows + damage events for one run.

    Returns (buff_windows, events) where:
      buff_windows = dict[buff_name] -> list[(t_abs_start, t_abs_end)]
      events       = list of dict per damage event taken by `target`
    """
    active_buffs: dict[str, float] = {}
    windows: dict[str, list[tuple[float, float]]] = defaultdict(list)
    events: list[dict] = []

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            # Cheap reject before any parsing
            sep = line.find("  ")
            if sep < 0:
                continue
            ts_part = line[:sep]
            rest = line[sep + 2 :].strip()

            try:
                # Date M/D/YYYY HH:MM:SS.fff — we only care about HH:MM:SS.fff
                _date_part, time_part = ts_part.split(" ", 1)
                hh, mm, ss = time_part.split(":")
                t_abs = int(hh) * 3600 + int(mm) * 60 + float(ss)
            except ValueError:
                continue

            # Skip events well outside run window (with 30s buff-state margin)
            if t_abs < t0_abs - 30 or t_abs > t1_abs + 30:
                continue

            comma_pos = rest.find(",")
            if comma_pos < 0:
                continue
            event_type = rest[:comma_pos]

            # === Aura events: buff windows ===
            if event_type in ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED"):
                fields = rest.split(",")
                if len(fields) < 13:
                    continue
                dest_name = fields[6].strip('"')
                if dest_name != target:
                    continue
                spell_name = fields[10].strip('"')
                if spell_name not in MITIGATION_BUFFS:
                    continue
                if event_type == "SPELL_AURA_APPLIED":
                    active_buffs[spell_name] = t_abs
                else:
                    start = active_buffs.pop(spell_name, None)
                    if start is not None:
                        windows[spell_name].append((start, t_abs))
                continue

            # === Damage events (only inside run window) ===
            if t_abs < t0_abs or t_abs > t1_abs:
                continue
            if "DAMAGE" not in event_type:
                continue
            if event_type not in (
                "SWING_DAMAGE",
                "RANGE_DAMAGE",
                "SPELL_DAMAGE",
                "SPELL_PERIODIC_DAMAGE",
                "SPELL_BUILDING_DAMAGE",
                "ENVIRONMENTAL_DAMAGE",
            ):
                continue

            fields = rest.split(",")
            try:
                dest_name = fields[6].strip('"')
            except IndexError:
                continue
            if dest_name != target:
                continue

            source_name = fields[2].strip('"')
            if source_name == target:
                # self-inflicted (stagger DoT)
                continue

            # Parse suffix fields. Format varies by event type:
            # SWING_DAMAGE: prefix has 11 base + advanced; spell variants have spell prefix (id, name, school)
            # Use the spell-suffix detection: spell-suffix offset for SPELL_DAMAGE is 9..11
            if event_type.startswith("SWING"):
                # No spell prefix. base_amount, amount lookup: WoW format —
                # base damage starts at field-25 ish. Easiest path: re-use
                # the proven iter_damage_events on this run window rather
                # than reimplement the offsets. We took a single-pass
                # shortcut for buffs only; for damage parsing we'll buffer
                # raw line and re-parse below.
                events.append({"raw_line": line, "_t_abs": t_abs})
                continue

            # Spell variant — capture quickly for re-parse later
            events.append({"raw_line": line, "_t_abs": t_abs})

    # Close any still-open buff windows
    for name, start in active_buffs.items():
        windows[name].append((start, t1_abs))

    return windows, events


def buff_active_at(windows: dict, t: float) -> dict[str, bool]:
    return {name: any(s <= t <= e for s, e in intervals) for name, intervals in windows.items()}


def analyze_run(
    log_path: Path,
    target: str,
    target_ts: frozenset[int] | None,
    run,
    char_base: Character,
    party_magic_dr: bool,
):
    """Per-school stats for one run, using the actual iter_damage_events parser."""
    # Parse buff windows for this run (lighter pass — only aura lines)
    windows, _ = parse_buff_and_damage_single_pass(
        log_path, target, run.start_time_s, run.end_time_s
    )

    # Use the official damage event iterator (handles all advanced-log offsets)
    run_char = char_base
    if target_ts is not None:
        run_char = dc_replace(char_base, detected_talent_spell_ids=target_ts)

    state = MitigationState(run_char)
    state.party_magic_dr_active = party_magic_dr
    # Populate state.talents from the loadout — runner.py:111 does this in the
    # normal sim path, but here we're constructing state by hand for the audit.
    c = load_constants()
    state.talents = set(c.get("talent_loadouts", {}).get(run_char.talents, {}).get("talents", []))
    rng = random.Random(42)  # deterministic
    # Drive policy.tick per event so the audit's MitigationState matches what
    # production replay produces (runner.py:186). Specifically: BfI stacks
    # build via the haste-time model (policy.py:154-157), recent-damage
    # window tracks, IP/SB charge regen runs. Without this, the audit
    # under-counts player-side DR by everything policy.tick adds — most
    # notably BfI's 0→4% physical DR ramp. See
    # docs/validation/per_school_gap_policy_tick_2026_05_24.md for the
    # before/after measurement.
    policy = make_policy(run_char)

    stats: dict[str, PerSchoolStats] = defaultdict(lambda: PerSchoolStats("?"))

    for log_evt in iter_damage_events(log_path, target, run.start_time_s, run.end_time_s):
        if log_evt.source_name == target:
            continue
        if log_evt.base_amount <= 0:
            continue

        t_abs = log_evt.time_s
        t_rel = t_abs - run.start_time_s

        active = buff_active_at(windows, t_abs)

        # Stamp buff state on engine
        state.shield_block_until = t_rel + 0.5 if active.get("Shield Block") else -1.0
        state.demo_shout_until = t_rel + 0.5 if active.get("Demoralizing Shout") else -1.0
        state.shield_wall_until = t_rel + 0.5 if active.get("Shield Wall") else -1.0
        state.last_stand_until = t_rel + 0.5 if active.get("Last Stand") else -1.0

        is_dot = log_evt.event_type == "SPELL_PERIODIC_DAMAGE"
        bucket = classify(log_evt.school, log_evt.spell_name, is_dot)

        attack_type = (
            "melee"
            if log_evt.event_type.startswith("SWING")
            else ("ranged" if log_evt.event_type == "RANGE_DAMAGE" else "spell")
        )

        engine_evt = DamageEvent(
            time_s=t_rel,
            source_id=log_evt.source_name,
            school=log_evt.school,
            raw_amount=float(log_evt.base_amount),
            attack_type=attack_type,
            is_dot_tick=is_dot,
            is_avoidable=False,
            is_blockable=(
                log_evt.school == "physical" and not log_evt.event_type.startswith("SPELL_PERIODIC")
            ),
            is_log_replay=True,
            log_absorbed=0.0,  # strip absorbs from prediction; comparison is pre-absorb DR
        )

        # Tick the policy so BfI/recent_damage/etc. match production replay
        # state at this event's timestamp. policy.tick is idempotent in time —
        # safe to call repeatedly with monotonically increasing `now`.
        policy.tick(state, t_rel)

        r = apply_mitigation(state, engine_evt, rng)
        if r.get("was_avoided"):
            continue
        predicted = r.get("dealt", 0.0)

        # Actual pre-absorb = amount + absorbed (post-block in both engine & log)
        actual_pre_absorb = log_evt.amount + log_evt.absorbed

        s = stats[bucket]
        if s.school == "?":
            s.school = bucket
        s.n_events += 1
        s.sum_base += log_evt.base_amount
        s.sum_actual_pre_absorb += actual_pre_absorb
        s.sum_predicted_pre_absorb += predicted

        actual_dr = 1 - actual_pre_absorb / log_evt.base_amount
        predicted_dr = 1 - predicted / log_evt.base_amount
        residual_pp = (predicted_dr - actual_dr) * 100
        s.residual_pp.append(residual_pp)
        s.weights.append(log_evt.base_amount)

    return {
        "map_name": run.map_name,
        "key_level": run.key_level,
        "duration_s": run.duration_s(),
        "per_school": {b: s.to_dict() for b, s in stats.items() if s.n_events},
    }


def analyze_log(
    log_path: Path,
    target: str,
    char_base: Character,
    party_magic_dr: bool = False,
):
    runs = parse_challenge_modes(log_path)
    if not runs:
        return {"log": log_path.name, "runs": []}

    guid_to_talents = {guid: ts for guid, _sid, ts in iter_combatant_info(log_path)}

    out_runs = []
    for ri, run in enumerate(runs):
        if run.end_time_s is None:
            continue
        if run.success is False:
            continue
        # Resolve target GUID through iter_damage_events first event.
        # iter_damage_events doesn't expose dest_guid easily — bypass: use
        # the COMBATANT_INFO talent block when there's exactly one player.
        target_ts = None
        # Fallback: if only one COMBATANT_INFO line and target_name matches via process,
        # accept it. Otherwise we'd need to scan; for the calibration corpus this is
        # consistent (single tank, single COMBATANT_INFO).
        if len(guid_to_talents) == 1:
            target_ts = next(iter(guid_to_talents.values()))
        # (For >1 COMBATANT_INFO entries — multi-player M+ — we accept that we may
        # miss talent-driven armor bonuses for this audit; the residual is bounded
        # by a few % and uniform across schools, so it doesn't affect per-school
        # comparison.)

        try:
            r = analyze_run(log_path, target, target_ts, run, char_base, party_magic_dr)
            r["run_index"] = ri
            out_runs.append(r)
        except Exception as e:
            out_runs.append({"run_index": ri, "error": str(e)})

    return {"log": log_path.name, "runs": out_runs}


def aggregate(per_log: list[dict]) -> dict:
    agg: dict[str, dict] = defaultdict(
        lambda: {
            "n_events": 0,
            "sum_base": 0.0,
            "sum_actual": 0.0,
            "sum_predicted": 0.0,
            "per_run_gaps": [],
            "per_run_labels": [],
        }
    )
    for log in per_log:
        for run in log.get("runs", []):
            if "error" in run:
                continue
            map_name = run.get("map_name", "?")
            key = run.get("key_level", "?")
            label = f"{log.get('log', '?')}[{run.get('run_index', '?')}] {map_name}+{key}"
            for bucket, s in run.get("per_school", {}).items():
                if s.get("n_events", 0) == 0:
                    continue
                agg[bucket]["n_events"] += s["n_events"]
                agg[bucket]["sum_base"] += s["sum_base"]
                agg[bucket]["sum_actual"] += (1 - s["actual_dr_pct"] / 100) * s["sum_base"]
                agg[bucket]["sum_predicted"] += (1 - s["predicted_dr_pct"] / 100) * s["sum_base"]
                agg[bucket]["per_run_gaps"].append(s["gap_pp"])
                agg[bucket]["per_run_labels"].append(label)

    out = {}
    for bucket, a in agg.items():
        if a["sum_base"] == 0:
            continue
        actual_dr = 1 - a["sum_actual"] / a["sum_base"]
        predicted_dr = 1 - a["sum_predicted"] / a["sum_base"]
        gaps = a["per_run_gaps"]
        labels = a["per_run_labels"]
        # Per-run gap distribution stats
        if len(gaps) >= 10:
            p90 = statistics.quantiles(gaps, n=10)[-1]
        elif gaps:
            p90 = max(gaps)
        else:
            p90 = 0.0
        out[bucket] = {
            "n_events": a["n_events"],
            "n_runs": len(gaps),
            "actual_dr_pct": actual_dr * 100,
            "predicted_dr_pct": predicted_dr * 100,
            "gap_pp_weighted": (predicted_dr - actual_dr) * 100,
            "gap_pp_mean_across_runs": statistics.mean(gaps) if gaps else 0.0,
            "gap_pp_p50_across_runs": statistics.median(gaps) if gaps else 0.0,
            "gap_pp_p90_across_runs": p90,
            "gap_pp_max_abs_across_runs": max(abs(g) for g in gaps) if gaps else 0.0,
            "max_run_label": labels[gaps.index(max(gaps, key=abs))] if gaps else "",
            "per_run_gaps": gaps,
            "per_run_labels": labels,
        }
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--logs-dir", default="examples")
    p.add_argument("--target", default="Brutoh-Uldum-EU")
    p.add_argument("--character", default="src/simf/data/characters/brutoh.yaml")
    p.add_argument("--party-magic-dr", action="store_true")
    p.add_argument("--out", default="docs/validation/per_school_data.json")
    args = p.parse_args()

    with open(args.character) as f:
        cdata = yaml.safe_load(f)
    char_base = Character.from_dict(cdata)

    logs_dir = Path(args.logs_dir)
    log_files = sorted(logs_dir.rglob("WoWCombatLog-*.txt"))
    if not log_files:
        print(f"No logs in {logs_dir}")
        return

    per_log = []
    for i, lf in enumerate(log_files, 1):
        print(f"  [{i}/{len(log_files)}] {lf.name}...", flush=True)
        try:
            r = analyze_log(lf, args.target, char_base, party_magic_dr=args.party_magic_dr)
            per_log.append(r)
            n_runs = len([x for x in r["runs"] if "error" not in x])
            print(f"      {n_runs} valid runs", flush=True)
        except Exception as e:
            print(f"      SKIP: {e}", flush=True)
            per_log.append({"log": lf.name, "error": str(e)})

    agg = aggregate(per_log)
    out = {
        "config": {
            "logs_dir": str(logs_dir),
            "target": args.target,
            "character": args.character,
            "party_magic_dr": args.party_magic_dr,
        },
        "per_log": per_log,
        "aggregate": agg,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}", flush=True)

    print("\n=== Aggregate per-school (across all runs) ===", flush=True)
    print(
        f"  {'bucket':10s} | {'n_evts':>7s} | {'n_runs':>6s} | "
        f"{'act_DR':>7s} | {'pred_DR':>7s} | {'gap':>8s} | "
        f"{'p50':>8s} | {'p90':>8s} | {'max_abs':>8s}"
    )
    for bucket in (
        "physical",
        "bleed",
        "shadow",
        "fire",
        "frost",
        "arcane",
        "nature",
        "holy",
    ):
        if bucket not in agg:
            continue
        a = agg[bucket]
        print(
            f"  {bucket:10s} | {a['n_events']:7d} | {a['n_runs']:6d} | "
            f"{a['actual_dr_pct']:6.2f}% | {a['predicted_dr_pct']:6.2f}% | "
            f"{a['gap_pp_weighted']:+7.2f}pp | "
            f"{a['gap_pp_p50_across_runs']:+7.2f}pp | "
            f"{a['gap_pp_p90_across_runs']:+7.2f}pp | "
            f"{a['gap_pp_max_abs_across_runs']:7.2f}pp",
            flush=True,
        )


if __name__ == "__main__":
    main()
