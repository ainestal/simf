"""Cross-player validation gate — the promotion criterion added 2026-07-25
alongside Prot Warrior's `calibrated` -> `characterized` downgrade (see
docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md and
docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md).

Every prior `characterized` -> `calibrated` promotion (Prot Warrior, Guardian
Druid) was judged entirely against ONE player's own log corpus. Prot
Warrior's 2026-07-22 promotion passed its own bar cleanly, then failed the
exact same bar three days later when checked against 15 independent players
(mean bias +11.0% vs the <=5% bar) — a generalization gap the promotion
process had never actually tested for. This script formalizes that check
into a reusable, repeatable gate for ANY spec's future promotion, instead of
the ad-hoc shell-script-plus-manual-arithmetic process used to discover the
gap the first time.

Usage:
  python scripts/cross_player_validation.py --manifest path/to/fights.yaml
  python scripts/cross_player_validation.py --manifest ... --wcl-cache-dir examples/wcl_cache

The manifest is a YAML file with a top-level `fights:` list, each entry
carrying at least `player`, `report_code`, `fight_id` (matching
data/calibration_corpora/prot_warrior_independent_wcl_2026_07_25.yaml's
schema — that file can be passed directly). `acl: false` entries are
excluded automatically (no gear-certain hydrate possible — see that
manifest's own Player 1 entry for why this matters).

Reuses cli._run_k_sweep (return_result=True, skip_loo_cv=True) for the
actual per-fight simulation and delta computation — the SAME code path
`calibrate-k --wcl-url` uses — rather than re-deriving that math, the same
discipline `_run_k_sweep`'s own docstring establishes ("single source of
truth for the per-K simulation, RMSE computation"). skip_loo_cv=True because
LOO-CV is a same-corpus, held-out-RUN check; these "replays" are different
PLAYERS entirely, so leaving one out doesn't test what LOO-CV is for.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import yaml

from simf.cli import _run_k_sweep, load_healing_profile
from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io import item_db
from simf.io.wcl_combatant_info import character_from_wcl
from simf.io.wcl_replay import wcl_to_replay_data

# RATIFIED gate (human sign-off 2026-07-25) — see this file's own module
# docstring and docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md
# for why these specific numbers, not the single-corpus bar's 5%/75%: looser
# on purpose, since a cross-player corpus is one fight per player (no
# same-player repeat runs to average per-pull noise out of), while still
# failing decisively on Prot Warrior's own known-bad result (+11.0%/67%).
MIN_PLAYERS = 5
MAX_MEAN_ABS_BIAS_PCT = 8.0
MIN_WITHIN_15PCT_FRACTION = 0.70


def load_fights_from_manifest(manifest_path: Path) -> list[dict]:
    with manifest_path.open() as f:
        d = yaml.safe_load(f)
    fights = d.get("fights", [])
    usable = [f for f in fights if f.get("acl", True)]
    skipped = len(fights) - len(usable)
    if skipped:
        print(f"  Skipping {skipped} fight(s) with acl: false (no gear-certain hydrate possible)")
    return usable


def run_cross_player_validation(
    fights: list[dict],
    *,
    k: int,
    iterations: int,
    seed: int,
    healing_profile: str,
    cache_dir: Path | None,
) -> dict:
    heal = load_healing_profile(healing_profile)
    replays = []
    for entry in fights:
        player = entry["player"]
        code = entry["report_code"]
        fight_id = entry["fight_id"]
        wcl_char = character_from_wcl(
            code,
            fight_id,
            player,
            resolve_stats_fn=item_db.resolve_equipped_stats,
            cache_dir=cache_dir,
        )
        if wcl_char is None:
            print(f"  SKIP {player} ({code}#{fight_id}): no CombatantInfo (ACL off)")
            continue
        char = Character.from_dict(wcl_char.char_data)

        # Window-gated mitigation layers (KYFOTG, VDH Metamorphosis, Painbringer)
        # are credited off event.active_buffs, stamped from the log's real buff
        # windows. Mirror cli.py's --wcl-url branch: collect the spec's
        # `*_spell_id` constants and pass them through, or every such layer is
        # silently zeroed for this player (the exact bug confirmed in
        # docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md).
        spec_cfg = load_constants().get("specs", {}).get(char.class_spec, {})
        buff_ids = {int(v) for k, v in spec_cfg.items() if k.endswith("_spell_id") and v}

        replay = wcl_to_replay_data(
            code, fight_id, player, buff_ability_ids=buff_ids or None, cache_dir=cache_dir
        )
        if replay.event_count == 0:
            print(f"  SKIP {player} ({code}#{fight_id}): 0 events for this target name")
            continue
        real_dtps = replay.actual_dealt / replay.duration_s
        replays.append((replay, real_dtps, player, char))

    if not replays:
        return {"n": 0, "mean_bias": None, "rmse": None, "within_15pct": None, "deltas": []}

    result = _run_k_sweep(
        replays=replays,
        heal=heal,
        k_min=k,
        k_max=k,
        k_step=1,
        iterations=iterations,
        seed=seed,
        skip_loo_cv=True,
        return_result=True,
    )
    if result is None:  # pragma: no cover — return_result=True guarantees a dict
        raise RuntimeError("_run_k_sweep returned None despite return_result=True")
    deltas = result["deltas"]
    n = len(deltas)
    mean_bias = sum(deltas) / n
    rmse = math.sqrt(sum(d**2 for d in deltas) / n) / 100
    within_15 = sum(1 for d in deltas if abs(d) <= 15) / n
    return {
        "n": n,
        "mean_bias": mean_bias,
        "rmse": rmse,
        "within_15pct": within_15,
        "deltas": list(zip(result["labels"], deltas, strict=True)),
    }


def print_gate_report(stats: dict) -> bool:
    """Print a PASS/FAIL report against the proposed gate; returns overall pass/fail."""
    n = stats["n"]
    print(f"\n=== Cross-player validation gate ({n} independent players) ===")
    if n == 0:
        print("  No usable fights (all ACL-off or 0-event) — cannot evaluate the gate.")
        return False

    for label, delta in stats["deltas"]:
        print(f"  {label}: {delta:+.1f}%")

    mean_bias = stats["mean_bias"]
    rmse = stats["rmse"]
    within_15 = stats["within_15pct"]

    n_ok = n >= MIN_PLAYERS
    bias_ok = abs(mean_bias) <= MAX_MEAN_ABS_BIAS_PCT
    within_ok = within_15 >= MIN_WITHIN_15PCT_FRACTION

    print(f"\n  n players:        {n} (bar >={MIN_PLAYERS}) {'PASS' if n_ok else 'FAIL'}")
    print(
        f"  mean |bias|:      {mean_bias:+.1f}% (bar <={MAX_MEAN_ABS_BIAS_PCT:.0f}%) "
        f"{'PASS' if bias_ok else 'FAIL'}"
    )
    print(
        f"  within +/-15%:    {within_15:.0%} (bar >={MIN_WITHIN_15PCT_FRACTION:.0%}) "
        f"{'PASS' if within_ok else 'FAIL'}"
    )
    print(f"  RMSE (reference): {rmse:.3f} (not itself gated at this sample size)")

    overall = n_ok and bias_ok and within_ok
    print(f"\n  GATE: {'PASS' if overall else 'FAIL'}")
    return overall


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--k", type=int, default=None, help="defaults to constants.yaml's k_constant")
    ap.add_argument("--iterations", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--healing-profile", default="m+_high_key_healer")
    ap.add_argument("--wcl-cache-dir", type=Path, default=None)
    args = ap.parse_args()

    k = args.k if args.k is not None else load_constants()["armor"]["k_constant"]
    fights = load_fights_from_manifest(args.manifest)
    print(f"Loaded {len(fights)} usable fight(s) from {args.manifest}")

    stats = run_cross_player_validation(
        fights,
        k=k,
        iterations=args.iterations,
        seed=args.seed,
        healing_profile=args.healing_profile,
        cache_dir=args.wcl_cache_dir,
    )
    print_gate_report(stats)


if __name__ == "__main__":
    main()
