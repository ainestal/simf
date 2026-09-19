"""Holy-Power / Word-of-Glory economy extraction for Protection Paladin ACL logs.

Measures, per timed ProtPal tank-run, everything the sim's Holy-Power economy
model (`classes/protection_paladin.py` + `specs.protection_paladin.*`) asserts:

  income   SPELL_ENERGIZE powerType=9 on the tank — `amount` is the SPENDABLE
           gain (waste at cap is reported separately in `overEnergize`; verified
           on Bruttah's logs: at pool 5 the lines read amount=0, over=3).
  spend    SPELL_CAST_SUCCESS advanced fields for Shield of the Righteous —
           each cast line carries (powerType, pre-cast pool, max, cost), i.e.
           direct per-cast ground truth. Paid casts read (9, n, 5, 3); free
           proc casts read powerType=0 cost=0.
  WoG      Word of Glory is MANA-funded (powerType=0, cost=50000, pool 250000)
           for the large majority of casts (~93% of the corpus) — but ~6-7%
           of casts read a PIPE-JOINED dual-resource block (e.g.
           `ptype="9|0" cur="3|250000" cost="3|50000"`), meaning that press
           ALSO drew 3 Holy Power alongside the mana. `_parse_resource_block`
           below handles both shapes; a hybrid press is charged against the
           Holy Power pool in `_pool_conservation` exactly like a paid SotR
           cast, and against the mana pool like any other WoG. Mechanism of
           the hybrid draw is uncharacterized (possibly a talent/proc
           interaction); materiality to the shipped constants is negligible
           (~1% of total HP income across the corpus).
  uptime   SotR buff (132403) window union / run duration.
  heals    self-target Word of Glory SPELL_HEAL amounts, summed PER CAST
           (a press sometimes fires more than one self-target heal event —
           e.g. a bonus/echo heal alongside the main one — so per-event
           averaging would dilute the real per-press value) as % of the max
           HP field on those events (checks `wog_heal_pct_of_max_hp`).
  mana     regen estimated from consecutive mana-bearing cast lines
           (m2 − (m1 − cost1)) / dt, excluding capped endpoints.

Back-solves `holy_power_per_second_base` two ways at each run's hydrated haste:
  from-uptime  3 × uptime / (sotr_duration_s × (1 + haste))   [matches the
               sim's press-near-expiry policy: what income reproduces the
               observed SotR coverage]
  from-press   3 × N_sotr_total / (T × (1 + haste))           [what income
               funds her actual press count if every press cost 3 — an upper
               bound; overcap duration wasted by off-expiry presses makes
               this exceed from-uptime]

Usage:
    python scripts/analyze_protpal_holy_power.py --logs-dir examples/bruttah-prot
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from itertools import pairwise
from pathlib import Path

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io.character_from_combatant_info import hydrate_character
from simf.io.combat_log import detect_party_roles, parse_challenge_modes
from simf.io.combat_log_buffs import parse_self_buff_windows
from simf.io.combat_log_casts import parse_energize_events
from simf.io.combat_log_core import parse_combat_log_line

# Spell ids as observed in Bruttah's Midnight 12.0.5 ACL logs (and consistent
# with the coaching registry's SotR buff id in constants.yaml).
SOTR_CAST_ID = 53600
SOTR_BUFF_ID = 132403
WOG_ID = 85673
HOLY_POWER_TYPE = 9  # WoW PowerType enum
TARGET_SPEC = "protection_paladin"


def _tank_runs(logs_dir: Path):
    for lf in sorted(logs_dir.glob("*.txt")):
        try:
            runs = parse_challenge_modes(lf)
        except Exception:
            continue
        for i, run in enumerate(runs or []):
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
                if spec == TARGET_SPEC and run.success is True:
                    yield lf, i, run, t


def _parse_resource_block(fields) -> dict[int, tuple[float, float, float]]:
    """Parse the 4-field power block (type, current, max, cost) at
    fields[-9:-5], keyed by powerType.

    Normally a single value per field (one resource type). Some Word of
    Glory casts pipe-join TWO resource types into the same 4 fields (e.g.
    ptype="9|0" cur="3|250000" pmax="5|250000" cost="3|50000" — that press
    drew 3 Holy Power AND 50000 mana). Splitting unconditionally on "|"
    handles both shapes uniformly (a plain value has no "|" and splits to a
    1-element list).
    """
    ptypes = fields[-9].split("|")
    curs = fields[-8].split("|")
    pmaxs = fields[-7].split("|")
    costs = fields[-6].split("|")
    return {
        int(pt): (float(cur), float(pmax), float(cost))
        for pt, cur, pmax, cost in zip(ptypes, curs, pmaxs, costs, strict=True)
    }


def _scan_casts_and_heals(log_path: Path, tank: str, t0: float, t1: float, offset: int):
    """One pass: SotR/WoG cast lines (with advanced power tail) + WoG self-heals.

    Cast-line advanced tail (Midnight ACL): ..., powerType, currentPower,
    maxPower, powerCost, posX, posY, uiMapID, facing, ilvl — i.e. fields[-9:-5]
    carry the power block (see `_parse_resource_block` for the pipe-joined
    dual-resource shape). SPELL_HEAL tail: amount, baseAmount, overhealing,
    absorbed, critical → amount = fields[-5]; advanced maxHP = fields[14].
    """
    name_q = f'"{tank}"'
    casts: list[dict] = []  # spell, t, resources: {ptype: (cur, pmax, cost)}
    heals: list[dict] = []  # t, amount, overheal, max_hp
    with log_path.open() as f:
        if offset > 0:
            f.seek(offset)
        for line in f:
            if name_q not in line:
                continue
            is_cast = "SPELL_CAST_SUCCESS," in line and (
                '"Shield of the Righteous"' in line or '"Word of Glory"' in line
            )
            is_heal = "SPELL_HEAL," in line and '"Word of Glory"' in line
            if not (is_cast or is_heal):
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            ts, etype, fields = parsed
            if ts < t0:
                continue
            if ts > t1:
                break
            if etype == "SPELL_CAST_SUCCESS" and fields[1] == tank:
                try:
                    resources = _parse_resource_block(fields)
                    casts.append(
                        {
                            "spell": int(fields[8]),
                            "t": ts,
                            "resources": resources,
                        }
                    )
                except (ValueError, IndexError):
                    continue
            elif (
                etype == "SPELL_HEAL"
                and fields[1] == tank
                and fields[5] == tank
                and fields[8] == str(WOG_ID)
            ):
                try:
                    heals.append(
                        {
                            "t": ts,
                            "amount": float(fields[-5]),
                            "overheal": float(fields[-3]),
                            "max_hp": float(fields[14]),
                        }
                    )
                except (ValueError, IndexError):
                    continue
    return casts, heals


def _pool_conservation(
    energizes, sotr_casts, wog_casts, pool_cap: float
) -> tuple[int, int, list[str]]:
    """Replay income − (paid SotR + hybrid WoG) and score against pre-cast pools.

    Any cast carrying a Holy Power (`HOLY_POWER_TYPE`) resource entry is
    checkable: paid SotR (always Holy-Power-only) AND the ~6-7% of WoG casts
    that pipe-join a Holy Power leg alongside their mana leg (see
    `_parse_resource_block`). Free-proc SotR (ptype=0 only, no Holy Power
    key) and pure-mana WoG contribute nothing — they carry no Holy Power
    reading to validate against. Returns (n_checked, n_mismatch>0.51, sample
    mismatch descriptions).
    """
    timeline: list[
        tuple[float, int, float, float | None]
    ] = []  # (t, kind 0=energize/1=cast, cost, cur)
    for e in energizes:
        timeline.append((e.time_s, 0, e.amount, None))
    for cst in sotr_casts + wog_casts:
        hp = cst["resources"].get(HOLY_POWER_TYPE)
        if hp is None:
            continue
        cur, _pmax, cost = hp
        timeline.append((cst["t"], 1, cost, cur))
    timeline.sort(key=lambda r: (r[0], r[1]))
    pool = 0.0
    checked = mismatched = 0
    samples: list[str] = []
    for row in timeline:
        if row[1] == 0:
            pool = min(pool_cap, pool + row[2])
        else:
            cost, cur = row[2], row[3]
            checked += 1
            if abs(pool - cur) > 0.51:
                mismatched += 1
                if len(samples) < 5:
                    samples.append(f"t={row[0]:.1f} model={pool:.1f} log={cur:.0f}")
            # Trust the log's own pre-pool over the reconstruction —
            # keeps one missed income event from cascading.
            pool = cur
            pool = max(0.0, pool - cost)
    return checked, mismatched, samples


def _cluster_heals_by_cast(wog_casts, heals, window: float = 0.25) -> list[float]:
    """Sum self-target WoG heal events into one PER-PRESS total.

    A single press sometimes fires more than one self-target SPELL_HEAL
    event (e.g. a bonus/echo heal alongside the main one) — averaging those
    per event double-weights multi-heal presses and understates the real
    per-press value. Groups every heal to its nearest cast timestamp
    (instant-cast spell, so the gap is sub-tick) and sums.
    """
    cast_times = sorted(c["t"] for c in wog_casts)
    if not cast_times:
        return []
    grouped: dict[float, list[dict]] = {}
    for h in heals:
        best = min(cast_times, key=lambda t: abs(t - h["t"]))
        if abs(best - h["t"]) > window:
            continue
        grouped.setdefault(best, []).append(h)
    pcts = []
    for hs in grouped.values():
        max_hp = hs[0]["max_hp"]
        if max_hp <= 0:
            continue
        pcts.append(sum(x["amount"] for x in hs) / max_hp)
    return pcts


def analyze_run(lf: Path, i: int, run, tank_name: str, cfg) -> dict | None:
    t0, t1, off = run.start_time_s, run.end_time_s, run.start_byte_offset
    dur = run.duration_s()

    hyd = hydrate_character(lf, tank_name, start_time_s=t0, end_time_s=t1, start_byte_offset=off)
    haste = None
    if hyd is not None:
        try:
            haste = Character.from_dict(dict(hyd.char_data)).haste_pct()
        except Exception:
            haste = None

    energizes = parse_energize_events(
        lf,
        tank_name,
        power_type=HOLY_POWER_TYPE,
        start_time_s=t0,
        end_time_s=t1,
        start_byte_offset=off,
    )
    casts, heals = _scan_casts_and_heals(lf, tank_name, t0, t1, off)
    windows = parse_self_buff_windows(
        lf, tank_name, {SOTR_BUFF_ID}, start_time_s=t0, end_time_s=t1, start_byte_offset=off
    )[SOTR_BUFF_ID]
    uptime_s = sum(e - s for s, e in windows)

    sotr = [c for c in casts if c["spell"] == SOTR_CAST_ID]
    wog = [c for c in casts if c["spell"] == WOG_ID]
    sotr_paid = [c for c in sotr if HOLY_POWER_TYPE in c["resources"]]
    sotr_free = [c for c in sotr if HOLY_POWER_TYPE not in c["resources"]]
    wog_hybrid = [c for c in wog if HOLY_POWER_TYPE in c["resources"]]
    wog_free = [c for c in wog if all(cost == 0 for (_cur, _pmax, cost) in c["resources"].values())]

    income = sum(e.amount for e in energizes)
    waste = sum(e.over_energize for e in energizes)
    by_spell = Counter()
    for e in energizes:
        by_spell[e.spell_name] += e.amount

    spec = cfg["specs"][TARGET_SPEC]
    pool_cap = float(spec["holy_power_max"])
    sotr_dur = float(spec["sotr_duration_s"])
    checked, mismatched, samples = _pool_conservation(energizes, sotr, wog, pool_cap)

    # Mana economy from mana-bearing cast lines (WoG + free SotR carry a mana
    # (ptype=0) resource entry — hybrid WoG carries mana ALONGSIDE Holy Power).
    mana_lines = sorted((c for c in casts if 0 in c["resources"]), key=lambda c: c["t"])
    mana_max = max((c["resources"][0][1] for c in mana_lines), default=0.0)
    wog_mana_costs = {c["resources"][0][2] for c in wog if 0 in c["resources"]}
    regen_estimates = []
    for a, b in pairwise(mana_lines):
        dt = b["t"] - a["t"]
        if not (2.0 <= dt <= 45.0):
            continue
        a_cur, _a_pmax, a_cost = a["resources"][0]
        b_cur, _b_pmax, _b_cost = b["resources"][0]
        if b_cur >= mana_max - 0.5:  # capped endpoint → estimate is a floor, skip
            continue
        est = (b_cur - (a_cur - a_cost)) / dt
        if est >= 0:
            regen_estimates.append(est)

    wog_gaps = [b["t"] - a["t"] for a, b in pairwise(wog)]
    heal_pcts = _cluster_heals_by_cast(wog, heals)

    uptime = uptime_s / dur if dur else 0.0
    r_income = income / dur
    r_press = 3.0 * len(sotr) / dur
    r_paid = 3.0 * len(sotr_paid) / dur
    base_from_uptime = 3.0 * uptime / (sotr_dur * (1 + haste)) if haste is not None else None
    base_from_press = r_press / (1 + haste) if haste is not None else None

    return {
        "label": f"{run.map_name} +{run.key_level}",
        "dur": dur,
        "haste": haste,
        "n_sotr": len(sotr),
        "n_sotr_paid": len(sotr_paid),
        "n_sotr_free": len(sotr_free),
        "n_wog": len(wog),
        "n_wog_hybrid": len(wog_hybrid),
        "n_wog_free": len(wog_free),
        "wog_mana_costs": sorted(wog_mana_costs),
        "wog_gap_min": min(wog_gaps) if wog_gaps else None,
        "wog_gap_med": statistics.median(wog_gaps) if wog_gaps else None,
        "uptime": uptime,
        "implied_sotr_dur": (uptime_s / len(sotr)) if sotr else None,
        "income": income,
        "waste": waste,
        "by_spell": by_spell.most_common(6),
        "r_income": r_income,
        "r_press": r_press,
        "r_paid": r_paid,
        "conservation": (checked, mismatched, samples),
        "mana_max": mana_max,
        "mana_regen": regen_estimates,
        "heal_pcts": heal_pcts,
        "base_from_uptime": base_from_uptime,
        "base_from_press": base_from_press,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs-dir", default="examples/bruttah-prot")
    args = ap.parse_args()
    cfg = load_constants()
    spec = cfg["specs"][TARGET_SPEC]

    rows = []
    for lf, i, run, t in _tank_runs(Path(args.logs_dir)):
        r = analyze_run(lf, i, run, t.name, cfg)
        if r:
            rows.append(r)

    for r in rows:
        print(f"\n=== {r['label']}  (dur {r['dur']:.0f}s, haste {r['haste']:.4f}) ===")
        print(
            f"  SotR casts: {r['n_sotr']} total = {r['n_sotr_paid']} paid @3HP"
            f" + {r['n_sotr_free']} free-proc | buff uptime {r['uptime'] * 100:.1f}%"
            f" | implied avg buff-gain/press {r['implied_sotr_dur']:.2f}s"
        )
        n_mana_only_paid = r["n_wog"] - r["n_wog_hybrid"] - r["n_wog_free"]
        print(
            f"  WoG casts: {r['n_wog']} total = {n_mana_only_paid} mana-only(paid)"
            f" + {r['n_wog_hybrid']} hybrid (mana+3HP, disjoint from free)"
            f" + {r['n_wog_free']} free-proc (cost 0, disjoint from hybrid)"
            f" | mana costs seen {r['wog_mana_costs']}"
            f" | gaps min {r['wog_gap_min'] and f'{r["wog_gap_min"]:.1f}s'}"
            f" med {r['wog_gap_med'] and f'{r["wog_gap_med"]:.1f}s'}"
        )
        print(
            f"  HP income: {r['income']:.0f} spendable (+{r['waste']:.0f} wasted at cap)"
            f" over {r['dur']:.0f}s = {r['r_income']:.3f} HP/s"
        )
        print(f"    by spell: {r['by_spell']}")
        print(
            f"  spend rates: paid 3xSotR {r['r_paid']:.3f} HP/s | all-press {r['r_press']:.3f}"
            f" HP/s | conservation: {r['conservation'][1]}/{r['conservation'][0]} pre-pool"
            f" mismatches {r['conservation'][2] or ''}"
        )
        med_regen = statistics.median(r["mana_regen"]) if r["mana_regen"] else None
        print(
            f"  mana: max {r['mana_max']:.0f} | regen est n={len(r['mana_regen'])}"
            f" median {med_regen and f'{med_regen:.0f}/s'}"
        )
        if r["heal_pcts"]:
            print(
                f"  WoG self-heals: n={len(r['heal_pcts'])}"
                f" mean {statistics.mean(r['heal_pcts']) * 100:.1f}% of maxHP"
                f" median {statistics.median(r['heal_pcts']) * 100:.1f}%"
            )
        print(
            f"  back-solved base: from-uptime {r['base_from_uptime']:.4f}"
            f" | from-press {r['base_from_press']:.4f}"
            f"  (current constant {spec['holy_power_per_second_base']})"
        )

    if rows:
        wsum = sum(r["dur"] for r in rows)
        for key in (
            "r_income",
            "r_paid",
            "r_press",
            "uptime",
            "base_from_uptime",
            "base_from_press",
        ):
            w = sum(r[key] * r["dur"] for r in rows) / wsum
            print(f"\nduration-weighted {key}: {w:.4f}", end="")
        total_wog = sum(r["n_wog"] for r in rows)
        total_hybrid = sum(r["n_wog_hybrid"] for r in rows)
        total_free = sum(r["n_wog_free"] for r in rows)
        print(
            f"\nWoG corpus totals: n={total_wog} | hybrid (mana+3HP) {total_hybrid}"
            f" ({100 * total_hybrid / total_wog:.1f}%) | free-proc {total_free}"
            f" ({100 * total_free / total_wog:.1f}%)"
        )
        all_heals = [p for r in rows for p in r["heal_pcts"]]
        all_regen = [e for r in rows for e in r["mana_regen"]]
        if all_heals:
            print(
                f"\nWoG per-press self-heal pct pooled: n={len(all_heals)}"
                f" mean {statistics.mean(all_heals) * 100:.2f}%"
                f" median {statistics.median(all_heals) * 100:.2f}%"
            )
        if all_regen:
            print(
                f"mana regen pooled: n={len(all_regen)} median {statistics.median(all_regen):.0f}/s"
                f" p25 {statistics.quantiles(all_regen, n=4)[0]:.0f}"
                f" p75 {statistics.quantiles(all_regen, n=4)[2]:.0f}"
            )


if __name__ == "__main__":
    main()
