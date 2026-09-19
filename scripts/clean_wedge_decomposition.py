"""Further decomposition of the Prot Warrior "clean wedge" residual.

Read-only, investigation-only companion to ``scripts/full_chain_wedge.py``
(``docs/validation/protwarrior_full_chain_wedge_2026_07_21.md`` — the
aggregate 0.8896 "clean wedge" figure, ~81%-magic-weighted per the same-day
Vanguard doc). That script reports only two aggregates over the "clean"
population (hits with ``in_cd_window == False`` — outside every real logged
Shield Block/Shield Wall/Battle-Scarred-Veteran window, all schools): a
single corpus-wide weighted mean, and a per-run weighted mean. Neither keeps
per-hit ability/mob/dungeon/aura identity, so neither can answer whether the
~11% shortfall is a flat missing passive (uniform everywhere) or a specific
missing mechanic / parsing artifact (concentrated on certain abilities,
mobs, dungeons, key levels, or correlated with some other tank buff).

This script re-derives the IDENTICAL ``clean_resid = r / base_chain`` value
for the IDENTICAL population, but keeps every hit's spell name, source
(mob) name, school, dungeon, key level, and tank-aura-window membership, so
it can be bucketed four ways:

  1. By (school, ability) — is the residual flat across every magic-damage
     ability, or concentrated on a few?
  2. By dungeon and by key level — does it vary with key level (pointing at
     a scaling-multiplier bug) or dungeon (pointing at mob-roster-specific
     tuning), or is it flat corpus-wide (pointing at a missing passive)?
  3. By every OTHER tank aura window observed in the corpus (the same
     technique as ``scripts/aura_attribution.py``, restricted to the
     "clean" hit population only) — does any buff/debuff co-move with the
     residual, which would suggest a gated mechanic rather than a true
     always-on passive?
  4. Reports exact magnitudes + sample sizes (hit count AND base_amount
     dollar-weight) for whatever pattern emerges.

Reuses ``scripts/full_chain_wedge.py`` directly as an importable sibling
module (Python puts a script's own directory on ``sys.path[0]``, confirmed
before writing this) for its window-parsing helpers (``_bfi_stack_timeline``,
``_stack_at``, ``_in_any_window``, the buff-id constants, ``CORPUS_MANIFEST``,
``LOG_TARGET``) — the per-hit loop itself is re-derived here (not exposed by
the sibling script's aggregate-only ``RunWedgeResult``) but computes the
exact same formula over the exact same population, term for term.

Usage:
  python scripts/clean_wedge_decomposition.py
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import full_chain_wedge as fcw
import yaml

from simf.cli import load_character
from simf.core.bleed_detection import is_bleed
from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io.calibration_corpus import load_calibration_corpus
from simf.io.combat_log import parse_challenge_modes
from simf.io.combat_log_buffs import parse_self_buff_windows
from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_damage import parse_damage_event
from simf.io.log_replay import load_replay

CORPUS_MANIFEST = fcw.CORPUS_MANIFEST
LOG_TARGET = fcw.LOG_TARGET

_KEY_LEVEL_RE = re.compile(r"\+(\d+)")


@dataclass(frozen=True)
class CleanHit:
    label: str
    dungeon: str
    key_level: int | None
    t: float
    base: float
    school: str
    is_bleed: bool
    spell_name: str
    source_name: str
    clean_resid: float


def _dungeon_map(manifest_path: Path) -> dict[tuple[str, int], str]:
    """(filename, run_index) -> raw ``dungeon`` string from the manifest,
    e.g. "Windrunner Spire +14 (partial)". Read directly from the YAML
    since ``CalibrationCorpus`` (io/calibration_corpus.py) deliberately
    drops this field — it isn't needed by calibrate-k itself."""
    with manifest_path.open() as f:
        d = yaml.safe_load(f)
    out: dict[tuple[str, int], str] = {}
    for entry in d["replays"]:
        out[(entry["file"], int(entry["run_index"]))] = entry["dungeon"]
    return out


def _all_tank_aura_windows(
    log_path: Path,
    tank_guid: str,
    *,
    start_time_s: float,
    end_time_s: float | None,
    start_byte_offset: int,
) -> tuple[dict[int, list[tuple[float, float]]], dict[int, str]]:
    """Every SPELL_AURA_APPLIED/REMOVED window on the tank in this run —
    same technique as scripts/aura_attribution.py (no watch-list; anything
    the tank had up gets a window). Unclosed windows at run end are closed
    at the run's last observed timestamp."""
    windows: dict[int, list[tuple[float, float]]] = defaultdict(list)
    names: dict[int, str] = {}
    on: dict[int, float] = {}
    last_t = start_time_s
    with log_path.open(encoding="utf-8", errors="replace") as f:
        if start_byte_offset:
            f.seek(start_byte_offset)
        for line in f:
            if tank_guid not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            last_t = time_s
            if event_type not in ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED"):
                continue
            if len(fields) < 12 or fields[4] != tank_guid:
                continue
            try:
                sid = int(fields[8])
            except ValueError:
                continue
            names[sid] = fields[9]
            if event_type == "SPELL_AURA_APPLIED":
                on.setdefault(sid, time_s)
            elif sid in on:
                windows[sid].append((on.pop(sid), time_s))
    for sid, t in on.items():
        windows[sid].append((t, last_t))
    return dict(windows), names


def collect_clean_hits(
    log_path: Path,
    *,
    label: str,
    dungeon: str,
    tank_name: str,
    start_time_s: float,
    end_time_s: float | None,
    start_byte_offset: int,
    run_char: Character,
    k: float,
) -> tuple[list[CleanHit], dict[int, list[tuple[float, float]]], dict[int, str]]:
    """Re-derive the exact ``clean_resid`` computation from
    full_chain_wedge.compute_run_wedge, but keep per-hit spell/mob identity
    and return the tank's full aura-window set alongside it (for the
    cross-buff correlation pass). Identical formula, identical population
    (``in_cd_window is False``) — verified term-for-term against the
    sibling script before use."""
    from simf.io.character_from_combatant_info import _resolve_target_guid

    tank_guid = _resolve_target_guid(log_path, tank_name, start_byte_offset=start_byte_offset)
    if tank_guid is None:
        return [], {}, {}

    c = load_constants()
    max_armor_dr = c["armor"]["max_armor_dr"]
    vers = run_char.versatility_dr()
    always_on = run_char._always_on_dr()
    talents = run_char._talent_set()

    has_bfi = "brace_for_impact" in talents
    bfi_per_stack = c["talents"]["brace_for_impact"]["shield_slam_stack_dr"] if has_bfi else 0.0
    bfi_max_stacks = c["talents"]["brace_for_impact"]["max_stacks"] if has_bfi else 0

    has_bsv = "battle_scarred_veteran" in talents

    watch_ids = {fcw.SHIELD_BLOCK_BUFF_ID, fcw.SHIELD_WALL_BUFF_ID}
    if has_bsv:
        watch_ids.add(fcw.BSV_BUFF_ID)
    windows = parse_self_buff_windows(
        log_path,
        tank_name,
        frozenset(watch_ids),
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        start_byte_offset=start_byte_offset,
    )
    sb_windows = windows.get(fcw.SHIELD_BLOCK_BUFF_ID, [])
    sw_windows = windows.get(fcw.SHIELD_WALL_BUFF_ID, [])
    bsv_windows = windows.get(fcw.BSV_BUFF_ID, [])

    bfi_timeline = (
        fcw._bfi_stack_timeline(
            log_path,
            tank_guid,
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            start_byte_offset=start_byte_offset,
        )
        if has_bfi
        else []
    )

    m = _KEY_LEVEL_RE.search(dungeon)
    key_level = int(m.group(1)) if m else None

    clean: list[CleanHit] = []
    with log_path.open(encoding="utf-8", errors="replace") as f:
        if start_byte_offset:
            f.seek(start_byte_offset)
        for line in f:
            if tank_name not in line and tank_guid not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            if event_type not in fcw._DAMAGE_EVENT_TYPES:
                continue
            spoof = "SWING_DAMAGE" if event_type == "SWING_DAMAGE_LANDED" else event_type
            evt = parse_damage_event(time_s, spoof, fields, tank_name)
            if evt is None or evt.source_name == tank_name:
                continue
            info_start = 8 if spoof.startswith("SWING") else 11
            try:
                info_guid = fields[info_start]
                armor_live = int(fields[info_start + 6])
            except (ValueError, IndexError):
                continue
            if info_guid != tank_guid or armor_live < 0 or evt.base_amount <= 0:
                continue

            r = (evt.amount + evt.absorbed + evt.blocked) / evt.base_amount
            is_periodic = event_type == "SPELL_PERIODIC_DAMAGE"
            bleed = is_bleed(evt.spell_name, is_periodic=is_periodic)
            physical_nonbleed = evt.school == "physical" and not bleed

            if physical_nonbleed:
                armor_dr_uncapped = armor_live / (armor_live + k)
                armor_dr_capped = min(armor_dr_uncapped, max_armor_dr)
                armor_term = 1 - armor_dr_capped
            else:
                armor_term = 1.0

            bfi_factor = 1.0
            if has_bfi and physical_nonbleed:
                stacks = min(fcw._stack_at(bfi_timeline, time_s), bfi_max_stacks)
                bfi_factor = 1 - stacks * bfi_per_stack

            base_chain = armor_term * (1 - vers) * always_on * bfi_factor

            sb_active = physical_nonbleed and fcw._in_any_window(sb_windows, time_s)
            sw_active = fcw._in_any_window(sw_windows, time_s)
            bsv_active = has_bsv and fcw._in_any_window(bsv_windows, time_s)
            in_cd_window = sb_active or sw_active or bsv_active

            if in_cd_window or base_chain <= 0:
                continue

            clean.append(
                CleanHit(
                    label=label,
                    dungeon=dungeon,
                    key_level=key_level,
                    t=time_s,
                    base=evt.base_amount,
                    school=evt.school,
                    is_bleed=bleed,
                    spell_name=evt.spell_name,
                    source_name=evt.source_name,
                    clean_resid=r / base_chain,
                )
            )

    aura_windows, aura_names = _all_tank_aura_windows(
        log_path,
        tank_guid,
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        start_byte_offset=start_byte_offset,
    )
    return clean, aura_windows, aura_names


def _wmean(hits: list[CleanHit]) -> float:
    w = sum(h.base for h in hits)
    return sum(h.base * h.clean_resid for h in hits) / w


def _bucket_report(title: str, hits: list[CleanHit], key_fn, min_n: int = 15) -> None:
    buckets: dict[object, list[CleanHit]] = defaultdict(list)
    for h in hits:
        buckets[key_fn(h)].append(h)
    total_base = sum(h.base for h in hits)
    rows = []
    for key, bucket in buckets.items():
        if len(bucket) < min_n:
            continue
        rows.append((key, len(bucket), sum(h.base for h in bucket), _wmean(bucket)))
    rows.sort(key=lambda r: -r[2])  # sort by damage share, largest first
    print(f"\n--- {title} (buckets with >= {min_n} hits) ---")
    print(f"{'bucket':<38} {'n_hits':>7} {'dmg_share':>10} {'wmean_resid':>12}")
    for key, n, base, wmean in rows:
        print(f"{key!s:<38} {n:>7} {base / total_base:>9.2%} {wmean:>12.4f}")
    n_shown = sum(n for _, n, _, _ in rows)
    print(f"  ({n_shown}/{len(hits)} hits covered by buckets >= {min_n}; rest too sparse)")


def _run_decomposition(
    replay_specs: list[tuple[Path, int, str]],
    *,
    char: Character,
    k: float,
    title: str,
) -> tuple[list[CleanHit], list[tuple[str, dict[int, list[tuple[float, float]]], dict[int, str]]]]:
    """Shared core: collect clean hits + aura windows across an arbitrary
    list of (log_path, run_index, dungeon_label) triples, print per-run
    lines, and return everything needed for the bucket reports. Used by
    both the ratified-corpus mode and the wide-archive mode below — same
    formula, same population, just a different replay source."""
    all_clean: list[CleanHit] = []
    run_auras: list[tuple[str, dict[int, list[tuple[float, float]]], dict[int, str]]] = []

    print(f"=== {title} ({len(replay_specs)} replays), K={k} ===")

    for lf, run_index, dungeon in replay_specs:
        label = f"{lf.name}[{run_index}]"
        if not lf.exists():
            print(f"  SKIP {label}: file not found at {lf}")
            continue
        try:
            runs = parse_challenge_modes(lf)
            replay = load_replay(lf, LOG_TARGET, run_index=run_index, runs=runs)
        except Exception as e:
            print(f"  SKIP {label}: {e}")
            continue

        clean, aura_windows, aura_names = collect_clean_hits(
            lf,
            label=label,
            dungeon=dungeon,
            tank_name=LOG_TARGET,
            start_time_s=replay.run.start_time_s,
            end_time_s=replay.run.end_time_s,
            start_byte_offset=replay.run.start_byte_offset,
            run_char=char,
            k=k,
        )
        all_clean.extend(clean)
        run_auras.append((label, aura_windows, aura_names))
        wmean = _wmean(clean) if clean else float("nan")
        print(
            f"  {label} ({dungeon}): clean_n={len(clean)} clean_wmean={wmean:.4f} "
            f"base_sum={sum(h.base for h in clean):.0f}"
        )

    return all_clean, run_auras


def _report(all_clean: list[CleanHit], run_auras, k: float) -> None:
    """Every bucket report, factored out so both modes print identically."""
    total_base = sum(h.base for h in all_clean)
    print(
        f"\nTOTAL clean hits across corpus: {len(all_clean)}, "
        f"base_sum={total_base:.0f}, aggregate wmean={_wmean(all_clean):.4f}"
    )

    # ---- 1. By (school, ability) ----
    _bucket_report(
        "By (school, ability)",
        all_clean,
        lambda h: f"{h.school}/{'bleed:' if h.is_bleed else ''}{h.spell_name}",
        min_n=15,
    )

    # ---- 1b. By school alone (coarser) ----
    _bucket_report("By school alone", all_clean, lambda h: h.school, min_n=5)

    # ---- 1c. By mob (source_name) ----
    _bucket_report("By mob (source_name)", all_clean, lambda h: h.source_name, min_n=15)

    # ---- 2a. By dungeon (raw manifest string incl. key level) ----
    _bucket_report("By dungeon (raw manifest label)", all_clean, lambda h: h.dungeon, min_n=15)

    # ---- 2b. By dungeon NAME only (strip key level) ----
    def dungeon_name(h: CleanHit) -> str:
        return re.sub(r"\s*\+\d+.*$", "", h.dungeon).strip()

    _bucket_report("By dungeon NAME only (key levels pooled)", all_clean, dungeon_name, min_n=15)

    # ---- 2c. By key level ----
    _bucket_report("By key level", all_clean, lambda h: h.key_level, min_n=15)

    # ---- 2d. Key-level trend on magic-only hits (excl. physical/bleed) ----
    magic_hits = [h for h in all_clean if h.school != "physical" and not h.is_bleed]
    print(f"\n--- Key-level trend, MAGIC-ONLY hits (n={len(magic_hits)}) ---")
    by_kl: dict[int, list[CleanHit]] = defaultdict(list)
    for h in magic_hits:
        if h.key_level is not None:
            by_kl[h.key_level].append(h)
    for kl in sorted(by_kl):
        b = by_kl[kl]
        print(f"  +{kl}: n={len(b)} base_sum={sum(x.base for x in b):.0f} wmean={_wmean(b):.4f}")

    # ---- 3. Cross-buff correlation on the clean population ----
    print("\n--- 3. Tank-aura correlation (clean population only) ---")
    print(
        "Technique: scripts/aura_attribution.py's inside-vs-outside-window "
        "weighted-mean-residual split, applied to the SAME clean_resid values "
        "computed above (not re-derived), per run, then pooled across the corpus."
    )

    def aura_pass(pop_name: str, population: list[CleanHit]) -> None:
        pop_labels = {h.label for h in population}
        # sid -> list of (weight, resid, inside)
        pooled: dict[int, list[tuple[float, float, bool]]] = defaultdict(list)
        names: dict[int, str] = {}
        uptime: dict[int, float] = defaultdict(float)
        for label, aura_windows, aura_names in run_auras:
            if label not in pop_labels:
                continue
            hits_here = [h for h in population if h.label == label]
            if not hits_here:
                continue
            names.update(aura_names)
            for sid, wins in aura_windows.items():
                uptime[sid] += sum(b - a for a, b in wins)
                for h in hits_here:
                    inside = any(a <= h.t <= b for a, b in wins)
                    pooled[sid].append((h.base, h.clean_resid, inside))

        scored = []
        for sid, triples in pooled.items():
            ins = [(w, x) for w, x, i in triples if i]
            outs = [(w, x) for w, x, i in triples if not i]
            if len(ins) < 20 or len(outs) < 20:
                continue
            wi, wo = sum(w for w, _ in ins), sum(w for w, _ in outs)
            if wi <= 0 or wo <= 0:
                continue
            mi = sum(w * x for w, x in ins) / wi
            mo = sum(w * x for w, x in outs) / wo
            share = wi / (wi + wo)
            power = abs(mi - mo) * min(share, 1 - share) * 2
            scored.append((power, sid, uptime[sid], share, mi, mo, len(ins), len(outs)))
        scored.sort(reverse=True)
        print(f"\n  [{pop_name}, n={len(population)}]")
        print(
            f"  {'power':>6} {'spell':>8} {'name':<30} {'uptime_s':>8} {'dmg_share_in':>12} "
            f"{'resid_in':>9} {'resid_out':>9} {'n_in':>5} {'n_out':>5}"
        )
        for power, sid, up, share, mi, mo, n_in, n_out in scored[:15]:
            print(
                f"  {power:6.3f} {sid:>8} {names.get(sid, '?'):<30} {up:8.0f} {share:12.2f} "
                f"{mi:9.4f} {mo:9.4f} {n_in:>5} {n_out:>5}"
            )
        if not scored:
            print("    (no buff/debuff met the >=20-hits-in-and-out threshold)")

    aura_pass("ALL clean hits", all_clean)
    nonfrost_magic_pop = [
        h
        for h in all_clean
        if h.school not in ("physical",) and not h.is_bleed and h.school != "frost"
    ]
    aura_pass("non-frost-magic only (the population carrying the ~14pp gap)", nonfrost_magic_pop)

    # ---- 4. School-mix per dungeon (tests whether dungeon-level clustering
    # is actually explained by frost-share, given school itself shows a
    # sharply different wedge for frost vs other magic schools) ----
    print(
        "\n--- 4. School damage-mix per dungeon NAME (tests dungeon-clustering-via-school-mix) ---"
    )
    by_dungeon: dict[str, list[CleanHit]] = defaultdict(list)
    for h in all_clean:
        by_dungeon[dungeon_name(h)].append(h)
    print(
        f"{'dungeon':<20} {'n':>6} {'frost%dmg':>10} {'nonfrost_magic_wmean':>20} {'frost_wmean':>12}"
    )
    for dname, hs in sorted(by_dungeon.items(), key=lambda kv: -sum(h.base for h in kv[1])):
        total = sum(h.base for h in hs)
        frost_hits = [h for h in hs if h.school == "frost"]
        nonfrost_magic = [h for h in hs if h.school not in ("physical", "frost") and not h.is_bleed]
        frost_share = sum(h.base for h in frost_hits) / total if total else float("nan")
        nf_wmean = _wmean(nonfrost_magic) if nonfrost_magic else float("nan")
        f_wmean = _wmean(frost_hits) if frost_hits else float("nan")
        print(f"{dname:<20} {len(hs):>6} {frost_share:>9.1%} {nf_wmean:>20.4f} {f_wmean:>12.4f}")

    # ---- 5. Corpus-wide summary: frost vs non-frost-magic vs physical/bleed ----
    print("\n--- 5. Corpus-wide summary by mitigation class ---")
    frost_all = [h for h in all_clean if h.school == "frost"]
    nonfrost_magic_all = [
        h
        for h in all_clean
        if h.school not in ("physical",) and not h.is_bleed and h.school != "frost"
    ]
    phys_bleed_all = [h for h in all_clean if h.school == "physical" or h.is_bleed]
    for name, hs in (
        ("frost (all)", frost_all),
        ("non-frost magic (shadow/arcane/nature/fire/holy, non-bleed)", nonfrost_magic_all),
        ("physical + bleed (any school)", phys_bleed_all),
    ):
        if not hs:
            continue
        base = sum(h.base for h in hs)
        print(
            f"  {name}: n={len(hs)} ({len(hs) / len(all_clean):.1%} of clean hits), "
            f"base_sum={base:.0f} ({base / total_base:.1%} of clean damage), "
            f"wmean={_wmean(hs):.4f}"
        )

    print("\n  per-run non-frost-magic wmean (tests run-to-run stability of the ~14pp gap):")
    by_run: dict[str, list[CleanHit]] = defaultdict(list)
    for h in nonfrost_magic_all:
        by_run[h.label].append(h)
    for label, hs in sorted(by_run.items(), key=lambda kv: kv[0]):
        if len(hs) < 15:
            print(f"    {label}: n={len(hs)} (too sparse)")
            continue
        print(f"    {label}: n={len(hs)} wmean={_wmean(hs):.4f}")

    # ---- 6. resid_out — what's STILL unexplained when Keep Your Feet on
    # the Ground (spell 438591) is confirmed NOT active. Added 2026-07-25:
    # the original session only ever reported resid_out as a single
    # aggregate number (0.90-0.93); this decomposes THAT population the
    # same way section 1-4 decomposed the whole clean population, to see
    # whether the leftover gap is itself uniform (another missing always-on
    # mechanic, findable the same way) or concentrated. ----
    KYFOTG_SPELL_ID = 438591
    kyfotg_labels_with_window: set[str] = set()
    kyfotg_out_hits: list[CleanHit] = []
    for label, aura_windows, _names in run_auras:
        wins = aura_windows.get(KYFOTG_SPELL_ID, [])
        if wins:
            kyfotg_labels_with_window.add(label)
    for h in all_clean:
        wins = next(
            (aw.get(KYFOTG_SPELL_ID, []) for lbl, aw, _n in run_auras if lbl == h.label), []
        )
        if not any(a <= h.t <= b for a, b in wins):
            kyfotg_out_hits.append(h)

    print(
        f"\n--- 6. resid_out decomposition (hits with KYFOTG {KYFOTG_SPELL_ID} confirmed NOT "
        f"active, n={len(kyfotg_out_hits)}/{len(all_clean)}; "
        f"{len(kyfotg_labels_with_window)}/{len(run_auras)} runs saw the buff at all) ---"
    )
    if kyfotg_out_hits:
        print(f"  aggregate resid_out wmean: {_wmean(kyfotg_out_hits):.4f}")
        _bucket_report(
            "resid_out By (school, ability)",
            kyfotg_out_hits,
            lambda h: f"{h.school}/{'bleed:' if h.is_bleed else ''}{h.spell_name}",
            min_n=15,
        )
        _bucket_report("resid_out By dungeon NAME only", kyfotg_out_hits, dungeon_name, min_n=15)
        nf_out = [
            h
            for h in kyfotg_out_hits
            if h.school not in ("physical",) and not h.is_bleed and h.school != "frost"
        ]
        aura_pass("resid_out, non-frost-magic only", nf_out)
    else:
        print("  (no hits found outside a KYFOTG window in this replay set)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--wide",
        nargs="*",
        default=None,
        metavar="FILE",
        help="run over an arbitrary list of log filenames (NOT the ratified corpus) instead — "
        "same decomposition, every successful CHALLENGE_MODE run in each file is included "
        "(no manifest to pin a single run_index), dungeon label derived from the log itself",
    )
    ap.add_argument("--logs-dir", default="examples", type=Path)
    args = ap.parse_args()

    k = load_constants()["armor"]["k_constant"]

    if args.wide is not None:
        corpus = load_calibration_corpus(CORPUS_MANIFEST)
        char = load_character(corpus.character_path)
        replay_specs: list[tuple[Path, int, str]] = []
        for filename in args.wide:
            lf = args.logs_dir / filename
            if not lf.exists():
                print(f"  SKIP {filename}: not found at {lf}")
                continue
            try:
                runs = parse_challenge_modes(lf)
            except Exception as e:
                print(f"  SKIP {filename}: {e}")
                continue
            for i, run in enumerate(runs):
                if run.success is False:
                    continue
                dungeon = f"{run.map_name} +{run.key_level}"
                replay_specs.append((lf, i, dungeon))
        all_clean, run_auras = _run_decomposition(
            replay_specs,
            char=char,
            k=k,
            title=f"Clean-wedge decomposition, WIDE archive set ({len(args.wide)} files requested)",
        )
    else:
        corpus = load_calibration_corpus(CORPUS_MANIFEST)
        char = load_character(corpus.character_path)
        dungeon_by_key = _dungeon_map(CORPUS_MANIFEST)
        logs_dir = Path("examples")
        replay_specs = [
            (logs_dir / filename, run_index, dungeon_by_key.get((filename, run_index), "?"))
            for filename, run_index in corpus.replays
        ]
        all_clean, run_auras = _run_decomposition(
            replay_specs,
            char=char,
            k=k,
            title=f"Clean-wedge decomposition, ratified corpus ({len(corpus.replays)} replays)",
        )

    _report(all_clean, run_auras, k)


if __name__ == "__main__":
    main()
