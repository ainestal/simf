"""Per-spec K-calibration / validation from LOCAL ACL-on combat logs.

Phase 4 needs real M+ logs per non-warrior tank spec to validate the
mitigation model and promote its ``calibration_tier``
(placeholder/characterized/calibrated — see ``core.constants.spec_is_calibrated``
for the promotion bar). This is the tooling used for that — it sources gear
AND fight from the SAME COMBATANT_INFO block (ACL on), so there is no
gear/fight mismatch the WCL ``--wcl-url`` adapter would otherwise introduce.

Two subcommands::

    python scripts/calibrate_spec_from_logs.py inventory [--logs-dir examples/Logs]
    python scripts/calibrate_spec_from_logs.py calibrate brewmaster_monk [--logs-dir examples/Logs] [--iters 300]

``inventory`` enumerates every CHALLENGE_MODE run's auto-detected tank
(name / spec / key / dungeon / success) via ``detect_party_roles`` — use it
to find which specs have >=2 *timed* runs to calibrate against.

``calibrate`` keeps the timed runs whose tank is the target spec, hydrates
each tank's ``Character`` from that run's COMBATANT_INFO, loads the replay,
measures each run's F (base-to-applied damage multiplier — see
``measure_run_f.py``), then sweeps K and JUDGES AT THE CANONICAL GLOBAL K
(``armor.k_constant``). The question is "do the per-run deltas land within
+/-15% at the canonical K?" — NOT "what is this spec's own best K?". A best-K
far from canonical is a spec mitigation gap → stays below the calibrated
tier and the gap gets filed. A leave-one-out cross-validation gate (Top-5 #4,
2026-07-06 retrospective) runs automatically at the end — a WEAK CV (only K
is refit per fold), a floor not a proof, but it catches a corpus where one
log secretly owns the entire fit.

Findings the first run of this tool surfaced (2026-06-07, see
``docs/validation/phase4_brewmaster_calibration_2026_06_07.md``):
  * Brewmaster over-predicts +27%/+57% at K=3430 → real model gap, not flipped.
  * Shield specs (Prot Warr/Pal): hydrate used to drop ``shield_armor`` (→ 0 →
    block value ~0 → ~24pp over-prediction). FIXED 2026-06-07 — hydrate now
    auto-sources it from the off-hand via the item_db resolver this script
    passes; ``--shield-armor N`` remains as a manual override (offline / when
    the item lookup can't resolve the shield).
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import replace
from pathlib import Path

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.profiles import load_healing_profile
from simf.core.runner import run_simulation
from simf.io.character_from_combatant_info import hydrate_character
from simf.io.combat_log import detect_active_buffs, detect_party_roles, parse_challenge_modes
from simf.io.log_replay import load_replay

# scripts/ isn't a package — ensure the sibling module resolves regardless of
# how THIS file was loaded (plain `python scripts/...py`, or path-based
# importlib loading from a test).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_run_f import measure_run_f

CANON_K_KEY = ("armor", "k_constant")
SEED = 42

# F-consistency exclusion band (Top-5 #4, 2026-07-06 retrospective; design
# ratified by calibration-scientist same day). The observed norm band across
# 4 clean runs in the Brewmaster decomposition doc spans 0.04 (0.91-0.95);
# AnonBrewmaster1's 0.83 outlier correctly sits outside it.
_F_CONSISTENCY_BAND = 0.05

# LOO-CV gate thresholds (Top-5 #4, 2026-07-06 retrospective; design ratified
# by calibration-scientist same day, stability check corrected same day after
# its first real application — see _run_loo_cv's docstring). This is a WEAK
# cross-validation — K is the only degree of freedom refit per fold; the
# ledger constants (the real overfit risk) are never refit. Treat as a
# floor, not a proof.
_LOO_K_STABILITY_MAX_SPREAD = 300.0  # max(|bestK_-i - bestK_full|); default sweep steps by 250
_LOO_HELD_OUT_SOFT_PCT = 15.0  # >=75% of held-out folds must be within this
_LOO_HELD_OUT_HARD_PCT = 25.0  # ANY fold beyond this is a hard fail
_LOO_HELD_OUT_SOFT_FRACTION = 0.75


def _tank_runs(logs_dir: Path):
    """Yield (log_path, run_index, run, tank PartyMember) for every CM run."""
    for lf in sorted(logs_dir.glob("*.txt")):
        try:
            runs = parse_challenge_modes(lf)
        except Exception:
            continue
        if not runs:
            continue
        for i, run in enumerate(runs):
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
                yield lf, i, run, t


def cmd_inventory(logs_dir: Path) -> None:
    print(f"Tank-run inventory for {logs_dir} (timed = success is True):\n")
    for lf, i, run, t in _tank_runs(logs_dir):
        spec = getattr(t, "class_spec", "") or getattr(t, "spec", "")
        print(
            f"  {lf.name}[{i}] {run.map_name} +{run.key_level} "
            f"success={run.success} dur={run.duration_s():.0f}s "
            f"TANK={t.name} spec={spec}"
        )


def _run_loo_cv(
    table: dict[int, tuple[float, list[float]]], ks: list[int], labels: list[str]
) -> bool:
    """Leave-one-out cross-validation gate — pure post-processing of the
    sweep `table` already computed by the caller, zero new sim runs.

    For each held-out run i: refit `bestK_-i` on every OTHER run's deltas,
    then check whether that held-out-blind K still predicts run i. Returns
    True iff the gate passes (stability + held-out prediction); always
    prints the report either way. See the module-level threshold constants
    for the exact numbers and their rationale.
    """
    n = len(labels)
    if n < 3:
        print(f"\n=== LOO-CV: skipped ({n} run(s) — need >=3 for a meaningful fold) ===")
        return False

    best_k_full = min(table, key=lambda k: table[k][0])
    best_k_excl: list[int] = []
    held_out_delta: list[float] = []
    for i in range(n):
        # RMSE over every OTHER run's delta at K, for every K in the sweep.
        rmse_excl = {
            k: math.sqrt(sum((d / 100) ** 2 for j, d in enumerate(table[k][1]) if j != i) / (n - 1))
            for k in ks
        }
        bk = min(rmse_excl, key=lambda k: rmse_excl[k])
        best_k_excl.append(bk)
        held_out_delta.append(table[bk][1][i])

    # Stability is measured against the FULL-corpus best K, not the spread
    # of the excl-i estimates' own extremes — a spread-of-extremes check is
    # sensitive to the sweep's own grid granularity (the default sweep steps
    # by 250, so ANY two-point spread across a 250-wide grid reads as
    # "unstable" even when every fold agrees with the full-corpus fit).
    # First real application (2026-07-06) caught exactly this: both the
    # Warrior (5 folds, 100% held-out-within-15%) and Guardian (17 folds)
    # corpora showed bestK_-i landing on {3250, 3430, 3500} — a 250 spread
    # that's really "one grid step away from canonical" on the SAME two
    # points every time, not genuine per-fold disagreement. Anchoring to
    # best_k_full with a tolerance just over one grid step (300 vs the
    # sweep's 250 spacing) fixes this without weakening the check for a
    # corpus that genuinely disagrees fold-to-fold.
    max_dev = max(abs(bk - best_k_full) for bk in best_k_excl)
    stable = max_dev <= _LOO_K_STABILITY_MAX_SPREAD
    within_soft = sum(1 for d in held_out_delta if abs(d) <= _LOO_HELD_OUT_SOFT_PCT)
    soft_frac = within_soft / n
    any_hard_fail = any(abs(d) > _LOO_HELD_OUT_HARD_PCT for d in held_out_delta)
    passes = stable and not any_hard_fail and soft_frac >= _LOO_HELD_OUT_SOFT_FRACTION

    print(f"\n=== LOO-CV gate ({n} folds) — WEAK CV: only K is refit per fold, ===")
    print("=== ledger constants (the real overfit risk) are never refit.   ===")
    print(
        "⚠️  A cross-spec ~5-7% run-scoped wedge (docs/validation/"
        "phase4_brewmaster_physical_gap_decomposition_2026_07_04.md) is still open across "
        "every spec this tool measures, Prot Warrior's own corpus included — treat these "
        "thresholds as not fully contamination-free until it's explained. (The separate "
        "warrior-replay Demoralizing Shout/Phalanx double-count once named alongside it here "
        "WAS fixed 2026-07-18, PR #387 — see CONTRIBUTING.md's Calibration section; don't cite it "
        "as still-open.)"
    )
    for i, label in enumerate(labels):
        mark = " [SOFT-FAIL]" if abs(held_out_delta[i]) > _LOO_HELD_OUT_SOFT_PCT else ""
        mark += " [HARD-FAIL]" if abs(held_out_delta[i]) > _LOO_HELD_OUT_HARD_PCT else ""
        print(
            f"  held out {label}: bestK_-i={best_k_excl[i]:5d}  "
            f"held-out delta={held_out_delta[i]:+.1f}%{mark}"
        )
    print(
        f"K stability: max|bestK_-i - bestK_full={best_k_full}|={max_dev:.0f} "
        f"(max {_LOO_K_STABILITY_MAX_SPREAD:.0f}) -> {stable}"
    )
    print(
        f"Held-out prediction: {within_soft}/{n} within ±{_LOO_HELD_OUT_SOFT_PCT:.0f}% "
        f"({soft_frac:.0%}, need >={_LOO_HELD_OUT_SOFT_FRACTION:.0%}), "
        f"any beyond ±{_LOO_HELD_OUT_HARD_PCT:.0f}%? {any_hard_fail}"
    )
    print(f"LOO-CV GATE: {'PASS' if passes else 'FAIL'}")
    return passes


def cmd_calibrate(spec: str, logs_dir: Path, iters: int, shield_armor: int | None) -> None:
    cands = [
        (lf, i, run, t)
        for lf, i, run, t in _tank_runs(logs_dir)
        if (getattr(t, "class_spec", "") or getattr(t, "spec", "")) == spec and run.success is True
    ]
    print(f"Spec={spec}: {len(cands)} TIMED tank-runs in {logs_dir}")
    if len(cands) < 2:
        print("  WARNING: <2 timed runs — cannot promote past `characterized` on this corpus.")

    # item_db resolver lets hydrate auto-source shield_armor from the off-hand
    # (shield specs). Offline/no-requests → None → hydrate leaves it unset and
    # --shield-armor is the manual override.
    try:
        from simf.io import item_db as _item_db

        _resolver = _item_db.resolve_equipped_stats
    except ImportError:
        _resolver = None

    # Hero-talent mitigation-ledger buff ids for this spec — detected per run
    # from the log so _always_on_dr() credits a flat-DR talent ONLY when its
    # buff aura is observed active on the tank (e.g. Shado-Pan Predictive
    # Training 451230). COMBATANT_INFO carries trait-entry ids, not spell ids,
    # so the buff aura is the reliable replay gate (see brewmaster_monk docs).
    _cfg = load_constants()
    _canon_k = _cfg[CANON_K_KEY[0]][CANON_K_KEY[1]]
    _ledger_buff_ids = frozenset(
        int(layer["detect_buff_spell_id"])
        for layer in _cfg.get("specs", {}).get(spec, {}).get("mitigation_ledger", [])
        if layer.get("detect_buff_spell_id")
    )

    replays = []  # (replay, real_dtps, label, char)
    f_measurements: list[tuple[str, object]] = []  # (label, FMeasurement | None)
    for lf, i, run, t in cands:
        hyd = hydrate_character(
            lf,
            t.name,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
            resolve_stats_fn=_resolver,
        )
        if hyd is None:
            print(f"  !! hydrate None {lf.name}[{i}] (ACL off / no gear)")
            continue
        cd = dict(hyd.char_data)
        if shield_armor is not None:
            cd["shield_armor"] = shield_armor  # explicit override wins
        char = Character.from_dict(cd)
        if _ledger_buff_ids:
            active = detect_active_buffs(
                lf,
                t.name,
                _ledger_buff_ids,
                start_time_s=run.start_time_s,
                end_time_s=run.end_time_s,
                start_byte_offset=run.start_byte_offset,
            )
            if active:
                char = replace(char, active_buff_spell_ids=active)
                print(f"     ledger buffs active: {sorted(active)}")
        try:
            replay = load_replay(lf, t.name, run_index=i)
        except Exception as e:
            print(f"  !! load_replay {lf.name}[{i}]: {e}")
            continue
        if replay.event_count == 0:
            print(f"  !! 0 events {lf.name}[{i}]")
            continue
        rd = replay.actual_dealt / replay.duration_s
        label = f"{lf.name[:22]}[{i}] {run.map_name[:14]}+{run.key_level}"
        replays.append((replay, rd, label, char))
        f_meas = measure_run_f(
            lf,
            t.name,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
            k=_canon_k,
            vers=char.versatility_dr(),
        )
        f_measurements.append((label, f_meas))
        f_str = (
            f"F={f_meas.median_f:.3f} (n={f_meas.n_hits}, IQR {f_meas.p25:.3f}-{f_meas.p75:.3f})"
            if f_meas is not None
            else "F=n/a (no per-hit armor data)"
        )
        print(
            f"  + {label}: real_dtps={rd:,.0f} events={replay.event_count:,} "
            f"armor={char.total_armor():,.0f} hp={char.max_hp():,.0f}"
        )
        print(f"     {f_str}")

    if len(replays) < 1:
        print("NO USABLE REPLAYS — abort")
        return

    heal = load_healing_profile("m+_high_key_healer")
    c = load_constants()
    canon_k = c[CANON_K_KEY[0]][CANON_K_KEY[1]]
    orig_k = canon_k

    def eval_at(k):
        c[CANON_K_KEY[0]][CANON_K_KEY[1]] = k
        sims = []
        for replay, rd, _, char in replays:
            heal_r = replace(heal, baseline_hps_abs=rd * 1.1)
            r = run_simulation(
                char,
                None,
                heal_r,
                iterations=iters,
                seed=SEED,
                events_override=replay.events,
                duration_override=replay.duration_s,
                compute_metrics=False,
            )
            sims.append(r.mean_dtps)
        deltas = [(s - rd) / rd * 100 for s, (_, rd, _, _) in zip(sims, replays, strict=False)]
        rmse = math.sqrt(sum((d / 100) ** 2 for d in deltas) / len(deltas))
        return rmse, deltas

    # F-consistency exclusion (Top-5 #4, 2026-07-06 retrospective) — per the
    # Brewmaster decomposition doc's own recommendation, corpus admission for
    # tier promotion is gated on F-consistency, but deltas above are reported
    # RAW at canonical K regardless (users experience reality including the
    # wedge; F gates corpus admission only, never bakes into constants.yaml).
    f_values = [(label, fm.median_f) for label, fm in f_measurements if fm is not None]
    print(f"\n=== F-consistency ({len(f_values)}/{len(f_measurements)} runs measurable) ===")
    f_consistent_labels: set[str] = set()
    if f_values:
        sorted_f = sorted(v for _, v in f_values)
        n = len(sorted_f)
        mid = n // 2
        f_median = sorted_f[mid] if n % 2 else (sorted_f[mid - 1] + sorted_f[mid]) / 2
        for label, fval in f_values:
            consistent = abs(fval - f_median) <= _F_CONSISTENCY_BAND
            if consistent:
                f_consistent_labels.add(label)
            excl = "" if consistent else " [EXCLUDED — outside band]"
            print(f"  {label}: F={fval:.3f}{excl}")
        print(
            f"F-consistent runs: {len(f_consistent_labels)}/{len(f_values)} "
            f"(median F={f_median:.3f}, band=±{_F_CONSISTENCY_BAND:.2f})"
        )
    else:
        print(
            "  no per-hit armor data on any run — F-consistency can't be assessed (ACL off / WCL import)"
        )

    ks = sorted({*range(2000, 5001, 250), canon_k})
    try:
        print(f"\n=== Sweep (iters={iters}, canonical K={canon_k}) ===")
        table = {k: eval_at(k) for k in ks}
        best_k = min(table, key=lambda k: table[k][0])
        for k in ks:
            rmse, deltas = table[k]
            mark = (" <-best" if k == best_k else "") + (" [CANONICAL]" if k == canon_k else "")
            print(f"  K={k:5d}: RMSE={rmse:.3f}  [{', '.join(f'{d:+.1f}%' for d in deltas)}]{mark}")
        crmse, cdeltas = table[canon_k]
        within = all(abs(d) <= 15.0 for d in cdeltas)
        print(
            f"\nCANONICAL K={canon_k}: RMSE={crmse:.4f}  deltas: "
            f"{', '.join(f'{d:+.1f}%' for d in cdeltas)}"
        )
        print(f"Best K={best_k} (RMSE={table[best_k][0]:.4f})")
        print(f"\nVERDICT: all per-run deltas within +/-15% at canonical K? {within}")
        labels = [label for _, _, label, _ in replays]
        _run_loo_cv(table, ks, labels)

        # F-consistent-subset re-run (2026-07-07 review follow-up) — the gate
        # above runs on the FULL corpus, so a FAIL can be nothing more than an
        # F-outlier run's own contamination, not a genuine held-out-prediction
        # miss. Pure post-processing of the same sweep table (subset the
        # existing per-run delta lists by index) — zero new sims. Only run
        # when F actually excluded something and enough runs remain for a
        # meaningful fold; otherwise it would be identical to the full-corpus
        # gate above and add noise, not signal.
        idx_consistent = [i for i, label in enumerate(labels) if label in f_consistent_labels]
        if (
            f_consistent_labels
            and 0 < len(idx_consistent) < len(labels)
            and len(idx_consistent) >= 3
        ):
            sub_labels = [labels[i] for i in idx_consistent]
            sub_table = {
                k: (
                    math.sqrt(
                        sum((table[k][1][i] / 100) ** 2 for i in idx_consistent)
                        / len(idx_consistent)
                    ),
                    [table[k][1][i] for i in idx_consistent],
                )
                for k in ks
            }
            print(
                f"\n=== LOO-CV on F-CONSISTENT SUBSET ONLY "
                f"({len(idx_consistent)}/{len(labels)} runs) ==="
            )
            _run_loo_cv(sub_table, ks, sub_labels)

        print(
            "\n  -> promote to calibration_tier: calibrated ONLY if the ±15% verdict "
            "AND the LOO-CV gate above both pass AND >=8 F-consistent runs exist "
            "(see core.constants.spec_is_calibrated's docstring for the full bar). "
            "If the full-corpus LOO-CV gate FAILS but the F-consistent-subset gate "
            "PASSES, the failure is attributable to F-contaminated runs, not a "
            "genuine held-out-prediction miss on the clean corpus."
        )
    finally:
        c[CANON_K_KEY[0]][CANON_K_KEY[1]] = orig_k


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("inventory", help="list every CM run's auto-detected tank")
    inv.add_argument("--logs-dir", default="examples/Logs")
    cal = sub.add_parser("calibrate", help="validate one spec at the canonical K")
    cal.add_argument("spec", help="class_spec slug, e.g. brewmaster_monk")
    cal.add_argument("--logs-dir", default="examples/Logs")
    cal.add_argument("--iters", type=int, default=300)
    cal.add_argument(
        "--shield-armor",
        type=int,
        default=None,
        help="override shield_armor (shield specs only — hydrate drops it; see docs)",
    )
    args = ap.parse_args()
    if args.cmd == "inventory":
        cmd_inventory(Path(args.logs_dir))
    else:
        cmd_calibrate(args.spec, Path(args.logs_dir), args.iters, args.shield_armor)


if __name__ == "__main__":
    main()
