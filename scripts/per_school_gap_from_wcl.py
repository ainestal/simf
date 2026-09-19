"""Per-school mitigation-gap decomposition for a WCL fight (spec-generic).

The WCL analog of ``scripts/per_school_mitigation_gap.py`` (which is local-log
+ warrior-hardcoded with buff-stamping). This one sources the gear-certain
character and the damage stream from a public WCL report (PR #142) and runs the
**actual engine replay loop** — ``policy.tick`` / ``policy.decide`` /
``apply_mitigation``, exactly as ``core/runner.py`` does in replay mode — so the
per-school split reflects what the engine really emits (policy-driven Demon
Spikes, etc.), not an analytic guess.

For each non-self event it buckets by school:
  * ``base``  = unmitigated amount (engine ``raw_amount``).
  * ``real``  = pre-absorb landed in the LOG = ``amount + absorbed``.
  * ``sim``   = pre-absorb landed in the ENGINE = ``dealt`` + the absorb the
    engine subtracted (added back so both sides are pre-absorb — this isolates
    the DR-magnitude gap from absorb handling, like the local tool).

``gap`` is ``sim - real`` in millions (positive = the engine OVER-predicts
damage taken = under-mitigates). The script prints a per-school table per fight
plus the implied total over-prediction, and cross-checks it against the
single-pass total (deterministic in replay: avoidance is off, no block roll).

Usage::

    python scripts/per_school_gap_from_wcl.py vengeance_demon_hunter \
        --fight ExampleCode1111111:1:2:AnonPlayerX1

Each ``--fight`` is ``CODE:FIGHT_ID:SOURCE_ID:TARGET_NAME`` (SOURCE may be
empty). ERA-CRITICAL: Midnight zone-47 fights only (see PR #142).
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from dataclasses import dataclass

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.mitigation import MitigationState, apply_mitigation
from simf.core.policy import make_policy
from simf.core.profiles import load_healing_profile
from simf.core.runner import _tile_externals
from simf.io import item_db, wcl_api
from simf.io.wcl_api import fetch_damage_events, fetch_report, fight_to_run
from simf.io.wcl_combatant_info import character_from_wcl
from simf.io.wcl_replay import adapt_events

M = 1_000_000.0
SCHOOL_ORDER = ["physical", "bleed", "shadow", "fire", "frost", "arcane", "nature", "holy"]


@dataclass
class Bucket:
    base: float = 0.0
    real: float = 0.0  # pre-absorb landed in the log
    sim: float = 0.0  # pre-absorb landed in the engine
    n: int = 0


def _parse_fight(value: str):
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--fight must be CODE:FIGHT:SOURCE:NAME")
    code, fight_s, source_s, name = parts
    return code, int(fight_s), (int(source_s) if source_s.strip() else None), name


def decompose(
    spec: str,
    code: str,
    fight_id: int,
    source_id,
    name: str,
    token: str,
    *,
    healer_ext: bool = True,
) -> dict:
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
        return {}
    char = Character.from_dict(wcl_char.char_data)
    if char.class_spec != spec:
        print(f"  !! {name}: gear-certain spec {char.class_spec!r} != {spec!r}")
        return {}

    report = fetch_report(code, token)
    fight = next((f for f in report.fights if f.id == fight_id), None)
    raw = fetch_damage_events(report, fight, name, token, target_actor_id=source_id)
    run = fight_to_run(fight, report.start_time_ms)

    # Window-gated buffs (e.g. Vengeance Metamorphosis armor) — same set the
    # calibrate script feeds, so the decomposition matches the K-sweep.
    spec_cfg = load_constants().get("specs", {}).get(spec, {})
    buff_ids = {int(v) for k, v in spec_cfg.items() if k.endswith("_spell_id") and v}
    buff_windows = None
    if buff_ids:
        from simf.io.wcl_api import _fetch_actor_id, fetch_buff_windows

        sid = source_id if source_id is not None else _fetch_actor_id(code, name, token)
        if sid is not None:
            buff_windows = fetch_buff_windows(report, fight, sid, token, ability_ids=buff_ids)

    events, agg = adapt_events(
        raw, target_name=name, run_start_time_s=run.start_time_s, buff_windows=buff_windows
    )
    duration = run.duration_s()

    c = load_constants()
    talent_set = set(c.get("talent_loadouts", {}).get(char.talents, {}).get("talents", []))
    state = MitigationState(char)
    state.talents = talent_set
    policy = make_policy(char)
    rng = random.Random(42)

    # Same healer externals run_simulation applies in replay mode: the
    # `dr_cooldown` windows are an all-school flat DR the engine credits, so we
    # tile + apply them here too. Without this the decomposition over-states the
    # gap by ~14pp vs the K-sweep (the absorb externals are a no-op in replay —
    # the spec path uses event.log_absorbed, not state.healer_absorb).
    heal = load_healing_profile("m+_high_key_healer")
    externals = _tile_externals(heal.externals, duration) if healer_ext else []
    next_ext = 0

    buckets: dict[str, Bucket] = defaultdict(Bucket)
    sim_total = 0.0
    real_total = 0.0  # post-absorb, to cross-check against agg['actual_dealt']

    # events from adapt_events are 1:1 with raw (same order). Zip to recover the
    # log's actual `amount` (engine events don't carry it).
    for raw_e, eng_e in zip(raw, events, strict=True):
        now = eng_e.time_s
        policy.tick(state, now)
        while next_ext < len(externals) and externals[next_ext].time_s <= now:
            ext = externals[next_ext]
            if ext.type == "dr_cooldown":
                state.healer_dr_until = now + ext.duration_s
                state.healer_dr_amount = ext.amount_pct or 0.0
            elif ext.type == "absorb":
                state.healer_absorb = max(state.healer_absorb, ext.amount or 0.0)
                state.healer_absorb_until = now + ext.duration_s
            next_ext += 1
        recent = state.recent_damage_total(now, 5.0)
        recent_dtps = recent / 5.0 if recent > 0 else 0.0
        policy.decide(state, now, recent_dtps, eng_e, upcoming_events=None)
        r = apply_mitigation(state, eng_e, rng)
        if eng_e.is_self_inflicted:
            continue
        if r.get("was_avoided"):
            # replay sets is_avoidable=False, so this never fires — guard anyway
            continue
        base = float(eng_e.raw_amount)
        if base <= 0:
            continue
        sim_pre = (
            r.get("dealt", 0.0)
            + r.get("absorbed_by_ignore_pain", 0.0)
            + r.get("absorbed_by_healer", 0.0)
        )
        real_pre = float(raw_e.amount) + float(raw_e.absorbed)
        school = "bleed" if (eng_e.school == "physical" and eng_e.is_bleed) else eng_e.school
        b = buckets[school]
        b.base += base
        b.real += real_pre
        b.sim += sim_pre
        b.n += 1
        sim_total += r.get("dealt", 0.0)
        real_total += float(raw_e.amount)

    return {
        "label": f"{name} {run.map_name}+{run.key_level}",
        "armor": char.total_armor(),
        "hp": char.max_hp(),
        "duration": duration,
        "buckets": buckets,
        "sim_dtps": sim_total / duration,
        "real_dtps": agg["actual_dealt"] / duration,
        "real_dtps_check": real_total / duration,
    }


def print_report(res: dict) -> None:
    if not res:
        return
    print(
        f"\n=== {res['label']} | armor={res['armor']:,.0f} hp={res['hp']:,.0f} "
        f"dur={res['duration']:.0f}s ==="
    )
    print(
        f"  {'school':9} | {'base(M)':>8} | {'real(M)':>8} | {'sim(M)':>8} | "
        f"{'real_mit':>8} | {'sim_mit':>8} | {'gap(M)':>8}"
    )
    tot_base = tot_real = tot_sim = 0.0
    for s in SCHOOL_ORDER:
        if s not in res["buckets"]:
            continue
        b = res["buckets"][s]
        if b.base <= 0:
            continue
        real_mit = 1 - b.real / b.base
        sim_mit = 1 - b.sim / b.base
        gap = (b.sim - b.real) / M
        tot_base += b.base
        tot_real += b.real
        tot_sim += b.sim
        print(
            f"  {s:9} | {b.base / M:8.1f} | {b.real / M:8.1f} | {b.sim / M:8.1f} | "
            f"{real_mit * 100:7.1f}% | {sim_mit * 100:7.1f}% | {gap:+8.1f}"
        )
    if tot_base > 0:
        over = (tot_sim - tot_real) / tot_real * 100
        print(
            f"  {'TOTAL':9} | {tot_base / M:8.1f} | {tot_real / M:8.1f} | {tot_sim / M:8.1f} | "
            f"{(1 - tot_real / tot_base) * 100:7.1f}% | {(1 - tot_sim / tot_base) * 100:7.1f}% | "
            f"{(tot_sim - tot_real) / M:+8.1f}"
        )
        print(
            f"  pre-absorb over-prediction: {over:+.1f}%  |  "
            f"post-absorb sim_dtps={res['sim_dtps']:,.0f} vs real_dtps={res['real_dtps']:,.0f} "
            f"({(res['sim_dtps'] - res['real_dtps']) / res['real_dtps'] * 100:+.1f}%)"
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("spec")
    ap.add_argument("--fight", action="append", dest="fights", type=_parse_fight, required=True)
    ap.add_argument(
        "--no-healer-ext",
        action="store_true",
        help="strip the healer dr_cooldown externals to isolate the spec's own DR "
        "layers (sim no longer reproduces the K-sweep, but the per-school shape is "
        "cleaner — magic sim_mit collapses to vers + averaged Fiery Brand only)",
    )
    args = ap.parse_args()
    token = wcl_api._get_token()

    agg_buckets: dict[str, Bucket] = defaultdict(Bucket)
    for code, fid, sid, name in args.fights:
        res = decompose(args.spec, code, fid, sid, name, token, healer_ext=not args.no_healer_ext)
        print_report(res)
        for s, b in res.get("buckets", {}).items():
            ab = agg_buckets[s]
            ab.base += b.base
            ab.real += b.real
            ab.sim += b.sim
            ab.n += b.n

    print("\n=== AGGREGATE across all fights ===")
    print(f"  {'school':9} | {'base(M)':>8} | {'real_mit':>8} | {'sim_mit':>8} | {'gap_pp':>8}")
    for s in SCHOOL_ORDER:
        if s not in agg_buckets:
            continue
        b = agg_buckets[s]
        if b.base <= 0:
            continue
        real_mit = 1 - b.real / b.base
        sim_mit = 1 - b.sim / b.base
        print(
            f"  {s:9} | {b.base / M:8.1f} | {real_mit * 100:7.1f}% | {sim_mit * 100:7.1f}% | "
            f"{(real_mit - sim_mit) * 100:+7.1f}pp"
        )


if __name__ == "__main__":
    main()
