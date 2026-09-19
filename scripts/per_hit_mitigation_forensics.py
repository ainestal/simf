"""Per-hit mitigation forensics for a tank in one LOCAL ACL combat-log run.

The instrument behind docs/validation/
phase4_brewmaster_physical_gap_decomposition_2026_07_04.md. For every non-self
damage event on the tank it computes

    r        = (amount + absorbed + blocked) / base_amount
    armor_dr = armor_live / (armor_live + K)          (physical, non-bleed)
    resid    = r / ((1 - armor_dr) * (1 - vers))

where ``armor_live`` is the tank's armor at the instant of THAT hit, read from
the advanced-logging info block (the info unit of a damage event is the
DESTINATION, so ``SPELL_DAMAGE`` / ``SPELL_PERIODIC_DAMAGE`` lines targeting
the tank — and the ``SWING_DAMAGE_LANDED`` twin of every melee swing — carry
the tank's own live armor at field 17). ``resid`` is therefore the multiplier
the armor-curve + versatility pipeline does NOT explain: 1.0 = fully
explained; 0.95 = an extra 5% cut from layers the model doesn't know about.

Also reported:
  * residual bins by school × (tank CD windows / PT / Devotion Aura /
    tank-applied attacker debuffs) — attacker-debuff bins showing NO effect is
    the in-log proof that attacker-side modifiers (Demo Shout, Breath of
    Fire) are already inside ``base_amount``;
  * Stagger conservation (absorbs credited in vs ticks + STAGGER_CLEAR out;
    the CLEAR lines carry only the GUID, never the player name — a name-only
    prefilter silently drops them all);
  * the live-armor timeline (distinct values + per-tick TSV) — this is what
    exposed Blistering Scales on AnonBrewmaster2.

Usage:
  python scripts/per_hit_mitigation_forensics.py LOG --tank Name-Realm-Region \
      [--run-index -1] [--vers 0.036] [--k-override N] [--out-prefix PREFIX]

``--vers`` is the tank's damage-taken versatility fraction (e.g. 0.036 for
3.6%); read it off the COMBATANT_INFO hydrate or character sheet. K defaults
to the canonical ``armor.k_constant``.
"""

from __future__ import annotations

import argparse
import contextlib
from collections import Counter, defaultdict
from pathlib import Path

from simf.core.constants import load_constants
from simf.io.character_from_combatant_info import _resolve_target_guid
from simf.io.combat_log import parse_challenge_modes
from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_damage import parse_damage_event

STAGGER_TICK_ID = 124255
STAGGER_ABSORB_ID = 115069

WATCH_AURAS = {
    # Brewmaster
    120954: "Fortifying Brew",
    122278: "Dampen Harm",
    122783: "Diffuse Magic",
    451230: "Predictive Training",
    115176: "Zen Meditation",
    # externals / cross-spec controls
    465: "Devotion Aura",
    360827: "Blistering Scales",
    871: "Shield Wall",
    12975: "Last Stand",
    23920: "Spell Reflection",
    132404: "Shield Block",
}
# Windows treated as "in a defensive CD" for binning (Last Stand / Shield
# Block / Blistering Scales are tracked but are not damage-taken multipliers).
CD_IDS = (120954, 122278, 122783, 115176, 871, 23920)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", type=Path)
    ap.add_argument("--tank", required=True, help="Name-Realm-Region as it appears in the log")
    ap.add_argument("--run-index", type=int, default=-1)
    ap.add_argument("--vers", type=float, default=0.0, help="damage-taken versatility fraction")
    ap.add_argument("--k-override", type=float, default=None)
    ap.add_argument(
        "--out-prefix", default=None, help="write per-hit / timeline TSVs with this prefix"
    )
    args = ap.parse_args()

    runs = parse_challenge_modes(args.log)
    if not runs:
        raise SystemExit(f"no CHALLENGE_MODE runs in {args.log}")
    run = runs[args.run_index if args.run_index >= 0 else len(runs) + args.run_index]
    tank_guid = _resolve_target_guid(args.log, args.tank, start_byte_offset=run.start_byte_offset)
    if tank_guid is None:
        raise SystemExit(f"could not resolve GUID for {args.tank!r}")
    k = args.k_override if args.k_override is not None else load_constants()["armor"]["k_constant"]
    vers = args.vers
    print(
        f"{run.map_name} +{run.key_level} success={run.success} "
        f"tank={args.tank} guid={tank_guid} K={k:.0f} vers={vers}"
    )

    aura_on: dict[int, float] = {}
    aura_windows: dict[int, list[tuple[float, float]]] = defaultdict(list)
    debuff_on: dict[tuple[str, int], float] = {}
    debuff_windows: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    debuff_names: dict[int, str] = {}
    hits: list[dict] = []
    ticks: list[tuple[float, int, int]] = []
    clears: list[tuple[float, float]] = []
    absorbs_by_spell: dict[tuple[int, str], int] = defaultdict(int)
    last_t = run.start_time_s

    with args.log.open(encoding="utf-8", errors="replace") as f:
        if run.start_byte_offset:
            f.seek(run.start_byte_offset)
        for line in f:
            if args.tank not in line and tank_guid not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if time_s < run.start_time_s:
                continue
            if run.end_time_s is not None and time_s > run.end_time_s:
                break
            last_t = time_s

            if event_type == "STAGGER_CLEAR":
                if fields[0] == tank_guid:
                    with contextlib.suppress(ValueError, IndexError):
                        clears.append((time_s, float(fields[1])))
                continue

            if event_type in ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED"):
                if len(fields) < 12:
                    continue
                try:
                    sid = int(fields[8])
                except ValueError:
                    continue
                if fields[4] == tank_guid and sid in WATCH_AURAS:
                    if event_type == "SPELL_AURA_APPLIED":
                        aura_on.setdefault(sid, time_s)
                    elif sid in aura_on:
                        aura_windows[sid].append((aura_on.pop(sid), time_s))
                elif (
                    fields[0] == tank_guid
                    and fields[4].startswith("Creature")
                    and fields[11] == "DEBUFF"
                ):
                    key = (fields[4], sid)
                    debuff_names[sid] = fields[9]
                    if event_type == "SPELL_AURA_APPLIED":
                        debuff_on.setdefault(key, time_s)
                    elif key in debuff_on:
                        debuff_windows[key].append((debuff_on.pop(key), time_s))
                continue

            if event_type == "SPELL_ABSORBED":
                if len(fields) < 10 or fields[4] != tank_guid:
                    continue
                with contextlib.suppress(ValueError, IndexError):
                    absorbs_by_spell[(int(fields[-6]), fields[-5])] += int(fields[-3])
                continue

            if event_type in (
                "SPELL_DAMAGE",
                "SPELL_PERIODIC_DAMAGE",
                "SWING_DAMAGE_LANDED",
                "RANGE_DAMAGE",
            ):
                # SWING_DAMAGE has the ATTACKER as info unit; its _LANDED twin
                # (same suffix payload) has the tank. Parse _LANDED via the
                # SWING_DAMAGE field layout.
                spoof = "SWING_DAMAGE" if event_type == "SWING_DAMAGE_LANDED" else event_type
                evt = parse_damage_event(time_s, spoof, fields, args.tank)
                if evt is None:
                    continue
                info_start = 8 if spoof.startswith("SWING") else 11
                try:
                    info_guid = fields[info_start]
                    armor_live = int(fields[info_start + 6])
                except (ValueError, IndexError):
                    info_guid, armor_live = "", -1
                if info_guid != tank_guid:
                    armor_live = -1

                if evt.source_name == args.tank:
                    if evt.spell_id == STAGGER_TICK_ID and event_type == "SPELL_PERIODIC_DAMAGE":
                        ticks.append((time_s, evt.amount, armor_live))
                    continue
                hits.append(
                    dict(
                        t=time_s,
                        et=event_type,
                        src=evt.source_guid,
                        src_name=evt.source_name,
                        spell=evt.spell_name,
                        school=evt.school,
                        amount=evt.amount,
                        base=evt.base_amount,
                        absorbed=evt.absorbed,
                        blocked=evt.blocked,
                        crit=evt.is_critical,
                        armor=armor_live,
                    )
                )
                continue

    for sid, t in aura_on.items():
        aura_windows[sid].append((t, last_t))
    for key, t in debuff_on.items():
        debuff_windows[key].append((t, last_t))

    def in_windows(ws: list[tuple[float, float]], t: float) -> bool:
        return any(a <= t <= b for a, b in ws)

    print(
        "watch-aura windows: "
        + (
            ", ".join(
                f"{WATCH_AURAS[s]}×{len(w)} ({sum(b - a for a, b in w):.0f}s)"
                for s, w in sorted(aura_windows.items())
            )
            or "none"
        )
    )
    by_spell_count: Counter[int] = Counter()
    for (_, sid), w in debuff_windows.items():
        by_spell_count[sid] += len(w)
    print(
        "tank-applied attacker debuffs: "
        + (
            ", ".join(f"{debuff_names.get(s, s)}×{n}" for s, n in by_spell_count.most_common(8))
            or "none"
        )
    )

    stagger_in = sum(v for (sid, _), v in absorbs_by_spell.items() if sid == STAGGER_ABSORB_ID)
    if stagger_in:
        out_total = sum(a for _, a, _ in ticks) + sum(a for _, a in clears)
        print(
            f"stagger conservation: credited_in={stagger_in:,.0f} "
            f"ticks={sum(a for _, a, _ in ticks):,.0f} clears(n={len(clears)})={sum(a for _, a in clears):,.0f} "
            f"out/in={out_total / stagger_in:.3f}"
        )
    armors = Counter(a for _, _, a in ticks if a > 0)
    if armors:
        print(
            "live armor (from stagger ticks): "
            + ", ".join(f"{v}×{n}" for v, n in armors.most_common(6))
        )

    rows = []
    for h in hits:
        if h["base"] <= 0 or h["armor"] <= 0:
            continue
        r = (h["amount"] + h["absorbed"] + h["blocked"]) / h["base"]
        armor_dr = h["armor"] / (h["armor"] + k)
        expl = (1 - armor_dr) * (1 - vers) if h["school"] == "physical" else (1 - vers)
        resid = r / expl
        cds = [WATCH_AURAS[s] for s in CD_IDS if in_windows(aura_windows[s], h["t"])]
        pt = in_windows(aura_windows[451230], h["t"])
        devo = in_windows(aura_windows[465], h["t"])
        dbfs = sorted(
            sid for (g, sid), w in debuff_windows.items() if g == h["src"] and in_windows(w, h["t"])
        )
        rows.append((h, r, resid, ",".join(cds), pt, devo, dbfs))

    def pctl(v: list[float], q: float) -> float:
        v = sorted(v)
        return v[max(0, min(len(v) - 1, int(q * (len(v) - 1))))] if v else float("nan")

    def summ(label: str, sel) -> None:
        ws = [
            (h["base"], resid) for h, r, resid, cds, pt, devo, d in rows if sel(h, cds, pt, devo, d)
        ]
        if not ws:
            print(f"   {label}: n=0")
            return
        rs = [x for _, x in ws]
        wsum = sum(b for b, _ in ws)
        wmean = sum(b * x for b, x in ws) / wsum
        print(
            f"   {label}: n={len(rs)}  resid p10={pctl(rs, 0.1):.3f} p50={pctl(rs, 0.5):.3f} "
            f"p90={pctl(rs, 0.9):.3f}  wmean={wmean:.4f}"
        )

    for school in ("physical", "shadow", "nature", "fire", "arcane", "frost", "holy"):
        if not any(h["school"] == school for h, *_ in rows):
            continue
        print(f"\nresidual — {school}:")
        summ("ALL", lambda h, cds, pt, devo, d, s=school: h["school"] == s)
        summ(
            "clean (noCD noPT noDebuff)",
            lambda h, cds, pt, devo, d, s=school: (
                h["school"] == s and cds == "" and not pt and not d
            ),
        )
        summ(
            "clean +attacker-debuff",
            lambda h, cds, pt, devo, d, s=school: h["school"] == s and cds == "" and not pt and d,
        )
        summ(
            "+PT (noCD)",
            lambda h, cds, pt, devo, d, s=school: h["school"] == s and cds == "" and pt,
        )
        summ("in CD", lambda h, cds, pt, devo, d, s=school: h["school"] == s and cds != "")

    if args.out_prefix:
        t0 = rows[0][0]["t"] if rows else run.start_time_s
        with open(args.out_prefix + "_hits.tsv", "w") as fh:
            fh.write(
                "t\tet\tsrc_name\tspell\tschool\tamount\tbase\tabsorbed\tblocked\tcrit\tarmor\tr\tresid\tcds\tpt\tdevo\ttank_debuffs\n"
            )
            for h, r, resid, cds, pt, devo, dbfs in rows:
                fh.write(
                    f"{h['t'] - t0:.3f}\t{h['et']}\t{h['src_name']}\t{h['spell']}\t{h['school']}\t"
                    f"{h['amount']}\t{h['base']}\t{h['absorbed']}\t{h['blocked']}\t{int(h['crit'])}\t{h['armor']}\t"
                    f"{r:.4f}\t{resid:.4f}\t{cds}\t{int(pt)}\t{int(devo)}\t{'|'.join(map(str, dbfs))}\n"
                )
        with open(args.out_prefix + "_armor_timeline.tsv", "w") as fh:
            fh.write("t\ttick_amount\tarmor\n")
            for t, a, armor in ticks:
                fh.write(f"{t - t0:.1f}\t{a}\t{armor}\n")
        print(f"\nwrote {args.out_prefix}_hits.tsv / _armor_timeline.tsv")


if __name__ == "__main__":
    main()
