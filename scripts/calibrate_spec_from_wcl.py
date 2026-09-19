"""Per-spec K-validation / characterization from PUBLIC Warcraft Logs fights.

The WCL analog of ``scripts/calibrate_spec_from_logs.py``. That tool needs
local ACL-on ``*WoWCombatLog-*.txt`` files, which we only have for a couple
of specs. WCL hosts a public corpus of the same fights, and PR #142 made the
gear-and-stat-certain character available from a report's ``CombatantInfo``
event — so we can characterize a thin spec (Blood DK / VDH / Guardian) against
several DIFFERENT players' real M+ runs without anyone mailing us a log.

Like the local tool, gear AND fight come from the SAME source (the WCL
``CombatantInfo`` event = the gear actually worn in that fight), so there is no
gear/fight mismatch. It judges every per-run delta AT THE CANONICAL GLOBAL K
(``armor.k_constant``) — the question is "do the deltas land within +/-15% at
the canonical K?", NOT "what is this spec's own best K?". A best-K far from
canonical is a spec mitigation gap → stay below the `calibrated` tier and file it.
A WCL-only corpus has no per-hit live armor, so it can promote a spec to
`characterized` at most — the `calibrated` tier's >=8 F-consistent-run bar
(core.constants.spec_is_calibrated) needs local ACL-on logs.

ERA-CRITICAL: only feed it Midnight zone-47 fights. TWW (zone 45, ~30x larger
stats) mis-scales the Midnight-calibrated engine (see PR #142).

Usage::

    python scripts/calibrate_spec_from_wcl.py vengeance_demon_hunter \
        --fight ExampleCode1111111:1:2:AnonPlayerX1 \
        --fight ExampleCode2222222:2:153:AnonPlayerX3 \
        --fight ExampleCode3333333:1:5:AnonPlayerX2 \
        --iters 300

Each ``--fight`` is ``CODE:FIGHT_ID:SOURCE_ID:TARGET_NAME`` (SOURCE_ID may be
empty — ``CODE:FIGHT::Name`` — to fall back to a name lookup; passing the
actor id is more robust for non-Latin names). The target is the tank whose
damage-taken stream drives ``real_dtps``.

NOTE on the hero-talent mitigation ledger: ``calibrate_spec_from_logs.py``
detects ledger buffs (e.g. Brewmaster Predictive Training 451230) from the
local log and credits them. This WCL tool does NOT wire that yet — the only
spec with a ledger today is Brewmaster, which we calibrate from local logs.
If a future spec gets a ledger row, add a WCL Buffs-table buff detector here
(see ``wcl_api`` Buffs table) before trusting its canonical-K deltas.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import replace

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.profiles import load_healing_profile
from simf.core.runner import run_simulation
from simf.io import item_db, wcl_api
from simf.io.wcl_combatant_info import character_from_wcl
from simf.io.wcl_replay import wcl_to_replay_data

CANON_K_KEY = ("armor", "k_constant")
SEED = 42


def _parse_fight(value: str) -> tuple[str, int, int | None, str]:
    """``CODE:FIGHT:SOURCE:NAME`` → ``(code, fight_id, source_id|None, name)``."""
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"--fight must be CODE:FIGHT:SOURCE:NAME (SOURCE may be empty), got {value!r}"
        )
    code, fight_s, source_s, name = parts
    try:
        fight_id = int(fight_s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"FIGHT must be an int, got {fight_s!r}") from e
    source_id = int(source_s) if source_s.strip() else None
    return code, fight_id, source_id, name


def cmd_calibrate(
    spec: str,
    fights: list[tuple[str, int, int | None, str]],
    iters: int,
    ks: list[int],
) -> None:
    print(f"Spec={spec}: {len(fights)} WCL fight(s)")
    if len(fights) < 2:
        print("  WARNING: <2 fights — cannot promote past `characterized` on this corpus.")

    token = wcl_api._get_token()  # one token, reused across all reports

    # Window-gated mitigation layers read the tank's buffs from the log (e.g.
    # Vengeance Metamorphosis' armor multiplier, gated on its buff being up at
    # the event). Collect the spec's buff spell-ids (any `*_spell_id` constant)
    # so the replay carries those windows. Empty set → no extra query.
    spec_cfg = load_constants().get("specs", {}).get(spec, {})
    buff_ids = {int(v) for k, v in spec_cfg.items() if k.endswith("_spell_id") and v}
    if buff_ids:
        print(f"  window-gated buffs fetched: {sorted(buff_ids)}")

    replays = []  # (replay, real_dtps, label, char)
    for code, fight_id, source_id, name in fights:
        wcl_char = character_from_wcl(
            code,
            fight_id,
            name,
            target_actor_id=source_id,
            resolve_stats_fn=item_db.resolve_equipped_stats,
            token=token,
        )
        if wcl_char is None:
            print(f"  !! no CombatantInfo (ACL off) {code}#{fight_id} {name}")
            continue
        char = Character.from_dict(wcl_char.char_data)
        if char.class_spec != spec:
            print(
                f"  !! {code}#{fight_id} {name}: gear-certain spec is "
                f"{char.class_spec!r}, not {spec!r} — skipping"
            )
            continue
        try:
            replay = wcl_to_replay_data(
                code, fight_id, name, target_actor_id=source_id, buff_ability_ids=buff_ids or None
            )
        except Exception as e:
            print(f"  !! fetch replay {code}#{fight_id} {name}: {e}")
            continue
        if replay.event_count == 0:
            print(f"  !! 0 events {code}#{fight_id} {name}")
            continue
        rd = replay.actual_dealt / replay.duration_s
        label = f"{name} {replay.run.map_name[:16]}+{replay.run.key_level}"
        replays.append((replay, rd, label, char))
        # Sanity line — the positive-control instinct: a gear-certain char
        # whose armor/HP/agility look insane means that fight's delta is
        # pipeline noise, not a model gap.
        print(
            f"  + {label}: real_dtps={rd:,.0f} events={replay.event_count:,} "
            f"armor={char.total_armor():,.0f} hp={char.max_hp():,.0f} agi={char.agility:,.0f}"
        )
        print(f"      gear-certain: {wcl_char.summary}")

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

    sweep_ks = sorted({*ks, canon_k})
    try:
        print(f"\n=== Sweep (iters={iters}, canonical K={canon_k}) ===")
        table = {k: eval_at(k) for k in sweep_ks}
        best_k = min(table, key=lambda k: table[k][0])
        for k in sweep_ks:
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
        print(
            "  -> promote to calibration_tier: characterized ONLY if True AND >=2 fights "
            "(ideally diff players). NOTE: the `calibrated` tier requires >=8 F-consistent "
            "runs (core.constants.spec_is_calibrated), which needs per-hit live armor data "
            "this WCL path doesn't carry — a WCL-only corpus can reach `characterized`, "
            "never `calibrated`, until local ACL-on logs exist for this spec."
        )
    finally:
        c[CANON_K_KEY[0]][CANON_K_KEY[1]] = orig_k


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("spec", help="class_spec slug, e.g. vengeance_demon_hunter")
    ap.add_argument(
        "--fight",
        action="append",
        dest="fights",
        type=_parse_fight,
        required=True,
        metavar="CODE:FIGHT:SOURCE:NAME",
        help="A WCL fight to characterize against (repeatable). SOURCE may be empty.",
    )
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument(
        "--ks",
        default="2000,2500,3000,3500,4000",
        help="comma-separated K values to sweep (canonical K is always added)",
    )
    args = ap.parse_args()
    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    cmd_calibrate(args.spec, args.fights, args.iters, ks)


if __name__ == "__main__":
    main()
