"""Guardian characterization from LOCAL ACL logs — streaming, Pi-memory-safe.

Unlike scripts/calibrate_spec_from_logs.py (which holds ALL replays in memory at
once — fine on a workstation, OOMs a 1.8 GB Pi on 18 runs × 200 MB logs), this
processes ONE run at a time and discards the replay before the next, so peak
memory is a single run.

Per timed Guardian run it reports:
  * real vs predicted post-mit DTPS at the canonical K  -> validates the #198
    physical fix on LOCAL data (previously only 3 WCL gear-certain fights).
  * Ironfur (192081) average stacks + uptime + cast cadence -> grounds the
    static ``ironfur_avg_stacks_m_plus: 1.5`` and the haste->rage->Ironfur model.
  * Ironfur SPELL_CAST_FAILED("...rage") count -> direct rage-starvation signal.
  * live in-form armor median (advanced-param fields[17] on AnonGuardian1-SOURCED
    events) -> ground-truth cross-check of total_armor() incl. live Ironfur.
  * haste_rating / agility / stamina / mastery from COMBATANT_INFO.

Run unbuffered + backgrounded:
  .venv/bin/python -u scripts/characterize_guardian_anonguardian1.py \
      --logs-dir examples/anonguardian1-guardian --iters 30 [--limit N]
"""

from __future__ import annotations

import argparse
import gc
from dataclasses import replace
from pathlib import Path

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.profiles import load_healing_profile
from simf.core.runner import run_simulation
from simf.io.character_from_combatant_info import hydrate_character
from simf.io.combat_log import (
    detect_active_buffs,
    detect_party_roles,
    parse_challenge_modes,
    parse_combat_log_line,
)
from simf.io.log_replay import load_replay

SEED = 42
IRONFUR_ID = "192081"
# Elune's Chosen capstone BUFF (Fury of Elune) — gates the Ironfur haste model
# in Character._guardian_ironfur_avg_stacks (Druid of the Claw lacks it).
FURY_OF_ELUNE_BUFF = 202770
_AURA_APPLY = {"SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH"}
_SRC_ARMOR_EVENTS = {
    "SPELL_CAST_SUCCESS",
    "SWING_DAMAGE",
    "SPELL_DAMAGE",
    "SPELL_PERIODIC_DAMAGE",
    "SPELL_HEAL",
    "SPELL_PERIODIC_HEAL",
}


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
                spec = getattr(t, "class_spec", "") or getattr(t, "spec", "")
                if spec == "guardian_druid":
                    yield lf, i, run, t


def _ironfur_and_armor_pass(log_path: Path, tank_name: str, run) -> dict:
    """Single windowed forward pass: Ironfur stack-integral + uptime + casts +
    rage-fails, plus median live armor from AnonGuardian1-sourced advanced params."""
    start, end = run.start_time_s, run.end_time_s
    stacks = 0
    last_t: float | None = None
    stack_seconds = 0.0
    up_seconds = 0.0
    cast_success = 0
    cast_fail_rage = 0
    armors: list[int] = []
    tank_guid: str | None = None

    with log_path.open() as f:
        if run.start_byte_offset > 0:
            f.seek(run.start_byte_offset)
        for line in f:
            # cheap prefilter: only Ironfur lines or lines naming the tank
            if IRONFUR_ID not in line and tank_name not in line:
                continue
            parsed = parse_combat_log_line(line)
            if parsed is None:
                continue
            t, etype, fields = parsed
            if t < start:
                continue
            if t > end:
                break

            # --- Ironfur aura stack integral (filter by spell id, log is localised) ---
            if len(fields) >= 12 and fields[8] == IRONFUR_ID and fields[5] == tank_name:
                new_stacks = stacks
                if etype in _AURA_APPLY:
                    new_stacks = max(1, stacks)
                elif etype == "SPELL_AURA_APPLIED_DOSE" or etype == "SPELL_AURA_REMOVED_DOSE":
                    try:
                        new_stacks = int(fields[12])
                    except (ValueError, IndexError):
                        new_stacks = stacks
                elif etype == "SPELL_AURA_REMOVED":
                    new_stacks = 0
                if last_t is not None and stacks >= 0:
                    dt = t - last_t
                    stack_seconds += stacks * dt
                    if stacks > 0:
                        up_seconds += dt
                stacks = new_stacks
                last_t = t
                continue

            # --- Ironfur cast cadence + rage starvation ---
            if fields[8:9] == [IRONFUR_ID] and fields[1] == tank_name:
                if etype == "SPELL_CAST_SUCCESS":
                    cast_success += 1
                # last field is the failure reason string ("Not enough rage")
                elif etype == "SPELL_CAST_FAILED" and fields and "rage" in fields[-1].lower():
                    cast_fail_rage += 1
                continue

            # capture the tank's GUID (srcGUID on the first event they source)
            if tank_guid is None and fields[1] == tank_name and fields[0].startswith("Player-"):
                tank_guid = fields[0]

            # --- live armor from AnonGuardian1-SOURCED advanced-param events ---
            # The advanced block starts right after the event prefix: SWING_* has
            # NO spell info so infoGUID is at idx 8; SPELL_* has spellId/name/school
            # so infoGUID is at idx 11. Armor is infoGUID + 6 (infoGUID, ownerGUID,
            # curHP, maxHP, AP, SP, armor). Anchoring on infoGUID == tank GUID
            # guarantees the block is the tank's stats (not the victim's).
            if etype in _SRC_ARMOR_EVENTS and fields[1] == tank_name and tank_guid:
                info_idx = 8 if etype.startswith("SWING") else 11
                if len(fields) > info_idx + 6 and fields[info_idx] == tank_guid:
                    tok = fields[info_idx + 6]
                    if tok.isdigit():
                        armors.append(int(tok))

    # close the final open interval to end of fight
    if last_t is not None and last_t < end:
        dt = end - last_t
        stack_seconds += stacks * dt
        if stacks > 0:
            up_seconds += dt

    dur = run.duration_s()
    armors.sort()
    n = len(armors)
    median_armor = armors[n // 2] if n else 0
    return {
        "avg_stacks": stack_seconds / dur if dur else 0.0,
        "uptime_pct": 100.0 * up_seconds / dur if dur else 0.0,
        "casts": cast_success,
        "fail_rage": cast_fail_rage,
        "median_armor": median_armor,
        "p25_armor": armors[n // 4] if n else 0,
        "p75_armor": armors[(3 * n) // 4] if n else 0,
        "armor_n": n,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-dir", default="examples/anonguardian1-guardian")
    ap.add_argument("--iters", type=int, default=30)
    ap.add_argument("--limit", type=int, default=None, help="only first N runs (smoke test)")
    ap.add_argument(
        "--ironfur-stacks",
        type=float,
        default=None,
        help="OVERRIDE specs.guardian_druid.ironfur_avg_stacks_m_plus in-memory (does NOT "
        "touch constants.yaml — for validating a proposed P1 fix read-only)",
    )
    ap.add_argument(
        "--ironfur-coeff",
        type=float,
        default=None,
        help="OVERRIDE specs.guardian_druid.ironfur_flat_armor_per_agility_per_stack in-memory",
    )
    args = ap.parse_args()

    c = load_constants()
    # In-memory overrides for proposed-fix validation. load_constants() returns a
    # shared cached dict (same object the engine reads), so mutating it here
    # propagates to total_armor()/the sim — exactly how calibrate_spec_from_logs
    # sweeps K. Nothing is written to disk.
    g = c["specs"]["guardian_druid"]
    if args.ironfur_stacks is not None:
        g["ironfur_avg_stacks_m_plus"] = args.ironfur_stacks
    if args.ironfur_coeff is not None:
        g["ironfur_flat_armor_per_agility_per_stack"] = args.ironfur_coeff
    canon_k = c["armor"]["k_constant"]
    if args.ironfur_stacks is not None or args.ironfur_coeff is not None:
        print(
            f"# OVERRIDE ironfur stacks={g['ironfur_avg_stacks_m_plus']} "
            f"coeff={g['ironfur_flat_armor_per_agility_per_stack']}"
        )
    heal = load_healing_profile("m+_high_key_healer")
    try:
        from simf.io import item_db as _item_db

        resolver = _item_db.resolve_equipped_stats
    except ImportError:
        resolver = None

    print(f"# Guardian characterization (canonical K={canon_k}, iters={args.iters})")
    hdr = (
        "log[run] dungeon+key | dur | real_dtps pred_dtps  delta% | "
        "IF_stacks IF_uptime% casts failRage | armor(model/med p25-p75) | "
        "haste agi stam mast vers"
    )
    print(hdr)
    print("-" * len(hdr))

    runs = list(_guardian_timed_runs(Path(args.logs_dir)))
    if args.limit:
        runs = runs[: args.limit]
    print(f"# {len(runs)} timed Guardian runs\n", flush=True)

    deltas = []
    for lf, i, run, t in runs:
        label = f"{lf.name[:24]}[{i}] {run.map_name[:16]}+{run.key_level}"
        hyd = hydrate_character(
            lf,
            t.name,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
            resolve_stats_fn=resolver,
        )
        if hyd is None:
            print(f"{label}: !! hydrate None", flush=True)
            continue
        cd = dict(hyd.char_data)
        # Detect Elune's Chosen (Fury of Elune buff) so the Ironfur haste model
        # activates for this build — mirrors the Brewmaster ledger buff-gating.
        ec = detect_active_buffs(
            lf,
            t.name,
            {FURY_OF_ELUNE_BUFF},
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
        )
        if ec:
            cd["active_buff_spell_ids"] = ec
        char = Character.from_dict(cd)
        model_armor = char.total_armor()

        stats = _ironfur_and_armor_pass(lf, t.name, run)

        try:
            replay = load_replay(lf, t.name, run_index=i)
        except Exception as e:
            print(f"{label}: !! load_replay {e}", flush=True)
            continue
        if replay.event_count == 0:
            print(f"{label}: !! 0 events", flush=True)
            continue
        real_dtps = replay.actual_dealt / replay.duration_s
        heal_r = replace(heal, baseline_hps_abs=real_dtps * 1.1)
        r = run_simulation(
            char,
            None,
            heal_r,
            iterations=args.iters,
            seed=SEED,
            events_override=replay.events,
            duration_override=replay.duration_s,
            compute_metrics=False,
        )
        pred = r.mean_dtps
        delta = (pred - real_dtps) / real_dtps * 100 if real_dtps else 0.0
        deltas.append((label, delta))

        print(
            f"{label} | {run.duration_s():.0f}s | "
            f"{real_dtps:>8,.0f} {pred:>8,.0f} {delta:>+6.1f}% | "
            f"{stats['avg_stacks']:>4.2f} {stats['uptime_pct']:>5.1f}% "
            f"{stats['casts']:>4d} {stats['fail_rage']:>5d} | "
            f"g{char.armor_from_gear:>6,.0f} m{model_armor:>6,.0f}/live{stats['median_armor']:>6,d} "
            f"({stats['p25_armor']:>5,d}-{stats['p75_armor']:<6,d}) | "
            f"h{char.haste_rating:.0f} a{char.agility:.0f} "
            f"s{char.stamina:.0f} m{char.mastery_rating:.0f} v{char.versatility_rating:.0f}",
            flush=True,
        )
        del replay
        gc.collect()

    if deltas:
        within = sum(1 for _, d in deltas if abs(d) <= 15.0)
        mean_abs = sum(abs(d) for _, d in deltas) / len(deltas)
        print(
            f"\n# {within}/{len(deltas)} runs within +/-15%; mean |delta| = {mean_abs:.1f}%",
            flush=True,
        )


if __name__ == "__main__":
    main()
