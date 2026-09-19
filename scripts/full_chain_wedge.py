"""Full modeled-chain per-hit wedge decomposition — the corpus-wide follow-up
named by ``docs/validation/protwarrior_f_layer_measurement_2026_07_21.md``.

That doc found ``simf calibrate-k``'s coarse ``measure_run_f.py`` F-layer is
F-consistent on the ratified 16-log Prot Warrior corpus, but explicitly can't
answer "does the model's FULL always-on + window-gated chain explain the
corpus's measured +11% over-prediction?" — its own ``expl`` formula divides
out only armor + versatility, not Defensive Stance / Indomitable / Brace for
Impact / Shield Block / Shield Wall / Battle-Scarred Veteran. The heavier
sibling, ``scripts/per_hit_mitigation_forensics.py``, DOES divide out that
full chain by hand for exactly 2 of the 16 logs (Nexus-Point +12, Windrunner
+14 — docs/validation/phase4_brewmaster_physical_gap_decomposition_2026_07_04.md
"Finding A", clean residual x0.94, bracketed 0.91-0.95 on BfI-stack
uncertainty because that pass reconstructed stacks by hand instead of reading
them from the log).

This script is the corpus-wide, ground-truth version:

  r          = (amount + absorbed + blocked) / base_amount        (real)
  armor_dr   = min(armor_live / (armor_live + K), max_armor_dr)   (capped —
               see "the armor-DR-cap check" below; the two existing scripts
               (measure_run_f.py, per_hit_mitigation_forensics.py) compute
               this UNCAPPED)
  base_chain = (1 - armor_dr) * (1 - vers)               [physical, non-bleed]
             * always_on_dr                              (Character._always_on_dr()
               — Defensive Stance x Indomitable, called on the real hydrated
               run character, never hand-derived from constants.yaml)
             * bfi_factor                                (1 - real_stacks * per_stack_dr,
               physical non-bleed only, real stacks read from the log's own
               Brace For Impact aura dose events — SPELL_AURA_APPLIED /
               _APPLIED_DOSE / _REMOVED_DOSE / _REMOVED on spell 386029 —
               ground truth, no stack-count guessing)
  full_chain = base_chain
             * sb_factor   (1 - shield_block_physical_dr, physical only,
               inside a REAL logged Shield Block window — spell 132404)
             * sw_factor   (1 - shield_wall_dr, all schools, inside a REAL
               logged Shield Wall window — spell 871)
             * bsv_factor  (1 - battle_scarred_veteran_dr, all schools,
               inside a REAL logged Battle-Scarred Veteran window — spell
               386397)
  resid      = r / chain                                  (1.0 = fully
               explained; <1.0 = an extra cut the model doesn't know about)

Two residual figures are reported per run:

  * "clean" — resid computed with `chain = base_chain`, restricted to hits
    OUTSIDE every Shield Block / Shield Wall / Battle-Scarred Veteran window
    (BfI is NOT a window-exclusion criterion — it's continuously stacking,
    not a discrete CD, so its real per-hit factor is always divided in).
    Directly comparable to the Brewmaster-doc's x0.94 Nexus-Point/Windrunner
    numbers, now with BfI resolved exactly instead of bracketed.
  * "full" — resid computed with `chain = full_chain`, over ALL hits. This is
    the number that should reconcile against calibrate-k's measured
    sim-vs-real bias, since it divides out everything the sim's own replay
    chain applies. Last Stand is deliberately NOT tracked at all —
    mitigation.py's Last Stand has NO `damage *=` line, it only raises
    max_hp, so it contributes no DR factor to divide out (a real, if minor,
    surprise against the task brief, which assumed it was a DR layer; see
    the validation doc).

IMPORTANT — a mid-build empirical finding changed the plan: a 3-run spot
check (see scripts/_sb_layer_check.py and the validation doc) found Shield
Block's `active_mitigation.shield_block.physical_dr: 0.30` layer does NOT
show up as a measurable extra cut in Brutoh's own combat logs — SB-active
and SB-inactive physical hits have statistically indistinguishable
armor+vers-only residuals despite SB windows covering 80%+ of most fights'
physical hits, and the sign of the (tiny) difference flips between runs.
Rather than unilaterally deciding this layer is fake and dropping it, both
readings are computed and reported per hit/run:

  * `full_resid`      — chain includes ALL modeled layers exactly as
    mitigation.py specifies (armor/vers/DS/Indom/BfI/SB/SW/BSV) — the
    faithful "what the model says" number.
  * `full_resid_no_sb` — identical, but the `sb_factor` is left out of the
    divisor — the "what if SB's layer isn't real" diagnostic.

Both are reported at every level (per-hit is not surfaced, but both
aggregates are); the doc explains which one is closer to explaining the
corpus's +11% bias and flags the SB finding for `validator` follow-up.

What is deliberately NOT divided out (see docs/validation/
protwarrior_demo_shout_double_count_2026_07_17.md and
protwarrior_f_layer_measurement_2026_07_21.md):

  * Demoralizing Shout / Phalanx — attacker-side mob debuffs, already inside
    the log's own `base_amount`. Dividing them out here would reintroduce
    the exact double-count that PR already fixed in replay.
  * Block chance / block value / dodge / parry — already reflected in `r`
    itself (amount + blocked + absorbed is what REALLY happened, block
    included). Confirmed by reading per_hit_mitigation_forensics.py's own
    `r` formula before writing this one (same convention, unchanged).
  * `party_dr_by_school` — its own constants.yaml comment claims it's
    "applied only in replay mode (event.is_log_replay)", but nothing in
    `runner.py` / `log_replay.py` ever sets `state.party_magic_dr_active`
    True outside of tests and a standalone measurement script
    (`scripts/per_school_mitigation_gap.py`) — confirmed by grep before
    relying on the comment. It is structurally INERT during a real
    `calibrate-k` sweep today, so `sim` never contains it; dividing it out
    here would fabricate a layer the number we're reconciling against
    never had. This is a real, pre-existing doc/comment inaccuracy, named
    here rather than fixed (out of scope for this branch).
  * Fight Through Flames / Unyielding Stance — not in the `brutoh-actual`
    loadout every ratified log runs (confirmed via
    `data/constants.yaml`'s `talent_loadouts.brutoh-actual`), so their gates
    never fire for this corpus; the code below still gates on the talent
    being present, so a future corpus with a different loadout stays
    correct without further changes.

Usage:
  python scripts/full_chain_wedge.py --corpus            # ratified 16 logs
  python scripts/full_chain_wedge.py --wide FILE [FILE...] --logs-dir DIR
  python scripts/full_chain_wedge.py --corpus --wide ...  # both sections
"""

from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass, replace
from pathlib import Path

from simf.cli import load_character
from simf.core.bleed_detection import is_bleed
from simf.core.character import Character
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
SHIELD_WALL_BUFF_ID = 871
BSV_BUFF_ID = 386397
# Last Stand (12975) is deliberately NOT tracked — mitigation.py's Last Stand
# has no `damage *=` line (it only raises max_hp), so it contributes zero DR
# factor to this divisor. See the module docstring.
BFI_BUFF_ID = 386029

_DAMAGE_EVENT_TYPES = (
    "SPELL_DAMAGE",
    "SPELL_PERIODIC_DAMAGE",
    "SWING_DAMAGE_LANDED",
    "RANGE_DAMAGE",
)

CORPUS_MANIFEST = DATA_DIR / "calibration_corpora" / "prot_warrior_2026_05.yaml"
LOG_TARGET = "Brutoh-Uldum-EU"


@dataclass(frozen=True)
class HitResid:
    t: float
    base: float
    school: str
    armor_live: int
    in_cd_window: bool  # Shield Block / Shield Wall / BSV (any) active at this hit
    sb_active: bool  # physical, non-bleed, AND inside a real logged Shield Block window
    physical_nonbleed: bool  # physical school, not a bleed tick — the population SB can gate
    clean_resid: float | None  # None when in_cd_window (excluded from the "clean" bin)
    full_resid: float  # as mitigation.py literally specifies (includes the SB factor)
    full_resid_no_sb: float  # diagnostic: same chain, SB factor omitted (see module docstring)
    armor_dr_uncapped: float
    armor_dr_capped: float


@dataclass(frozen=True)
class RunWedgeResult:
    label: str
    n_hits: int
    clean_n: int
    clean_wmean: float | None
    clean_median: float | None
    full_wmean: float
    full_median: float
    full_no_sb_wmean: float
    full_no_sb_median: float
    n_armor_dr_capped: int
    max_armor_live: int
    # 2026-07-21 post-shield-block-fix bias decomposition (Lead 1): DTPS
    # (base_amount-weighted) shares of the "clean" (outside every SB/SW/BSV
    # window) and "SB-active" (physical, non-bleed, inside a real Shield
    # Block window) bins — as opposed to clean_n's plain HIT-COUNT share.
    # If "clean" hits carry a disproportionate share of DAMAGE relative to
    # their ~2% hit-count share (e.g. because charges lapse specifically
    # around tank-buster spikes), that would matter a lot more to the
    # aggregate bias than the hit-count split alone suggests. See
    # docs/validation/protwarrior_post_shield_block_bias_decomposition_2026_07_21.md.
    total_base_sum: float = 0.0
    clean_base_sum: float = 0.0
    sb_active_base_sum: float = 0.0
    # Same Lead-1 question, but restricted to the population Shield Block
    # can actually gate (physical, non-bleed) — apples-to-apples with the
    # SB-active-vs-inactive HIT-COUNT split reported in the validation doc
    # (21,402 vs 433), unlike the all-schools "clean" bin above (which is
    # dominated by magic/bleed damage that Shield Block never touches).
    physical_nonbleed_base_sum: float = 0.0
    physical_nonbleed_outside_sb_base_sum: float = 0.0
    physical_nonbleed_n: int = 0
    physical_nonbleed_outside_sb_n: int = 0


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def _weighted_mean(weights: list[float], values: list[float]) -> float:
    wsum = sum(weights)
    return sum(w * v for w, v in zip(weights, values, strict=True)) / wsum


def _bfi_stack_timeline(
    log_path: Path,
    tank_guid: str,
    *,
    start_time_s: float,
    end_time_s: float | None,
    start_byte_offset: int = 0,
) -> list[tuple[float, int]]:
    """Real per-hit Brace For Impact stack count over time, from the log's own
    aura dose events (386029) — ground truth, not a cast-based reconstruction.

    SPELL_AURA_APPLIED -> stack 1. SPELL_AURA_APPLIED_DOSE / _REMOVED_DOSE ->
    stack = the event's own trailing count field. SPELL_AURA_REMOVED ->
    stack 0. SPELL_AURA_REFRESH only resets the duration timer — no stack
    change, so it's not a step point. Returns a sorted [(t, stack), ...] list
    (stack is the value that becomes active AT time t and holds until the
    next entry) suitable for `_stack_at`.
    """
    timeline: list[tuple[float, int]] = []
    with log_path.open(encoding="utf-8", errors="replace") as f:
        if start_byte_offset:
            f.seek(start_byte_offset)
        for line in f:
            if "Brace For Impact" not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            if len(fields) < 12 or fields[4] != tank_guid or fields[11] != "BUFF":
                continue
            try:
                if int(fields[8]) != BFI_BUFF_ID:
                    continue
            except ValueError:
                continue
            if event_type == "SPELL_AURA_APPLIED":
                timeline.append((time_s, 1))
            elif event_type in ("SPELL_AURA_APPLIED_DOSE", "SPELL_AURA_REMOVED_DOSE"):
                if len(fields) < 13:
                    continue
                try:
                    stack = int(fields[12])
                except ValueError:
                    continue
                timeline.append((time_s, stack))
            elif event_type == "SPELL_AURA_REMOVED":
                timeline.append((time_s, 0))
            # SPELL_AURA_REFRESH: duration reset only, no stack change — skip.
    timeline.sort(key=lambda p: p[0])
    return timeline


def _stack_at(timeline: list[tuple[float, int]], t: float) -> int:
    """Stack count active at time t: the last recorded value at or before t,
    or 0 if t precedes every recorded point (no stacks yet)."""
    if not timeline:
        return 0
    times = [p[0] for p in timeline]
    idx = bisect.bisect_right(times, t) - 1
    return timeline[idx][1] if idx >= 0 else 0


def _in_any_window(windows: list[tuple[float, float]], t: float) -> bool:
    return any(a <= t <= b for a, b in windows)


def compute_run_wedge(
    log_path: Path,
    *,
    tank_name: str,
    start_time_s: float,
    end_time_s: float | None,
    start_byte_offset: int,
    run_char: Character,
    k: float,
    label: str,
) -> RunWedgeResult | None:
    """Full-chain per-hit wedge for one run window. Returns None if the log
    carries no per-hit live-armor data (ACL off) — same "no evidence, not
    neutral evidence" contract as measure_run_f.py."""
    tank_guid = _resolve_target_guid(log_path, tank_name, start_byte_offset=start_byte_offset)
    if tank_guid is None:
        return None

    c = load_constants()
    max_armor_dr = c["armor"]["max_armor_dr"]
    vers = run_char.versatility_dr()
    always_on = run_char._always_on_dr()
    talents = run_char._talent_set()

    has_bfi = "brace_for_impact" in talents
    bfi_per_stack = c["talents"]["brace_for_impact"]["shield_slam_stack_dr"] if has_bfi else 0.0
    bfi_max_stacks = c["talents"]["brace_for_impact"]["max_stacks"] if has_bfi else 0

    has_bsv = "battle_scarred_veteran" in talents
    bsv_dr = c["talents"]["battle_scarred_veteran"]["damage_reduction"] if has_bsv else 0.0

    sb_dr = c["active_mitigation"]["shield_block"]["physical_dr"]
    sw_dr = c["active_mitigation"]["shield_wall"]["damage_reduction"]

    watch_ids = {SHIELD_BLOCK_BUFF_ID, SHIELD_WALL_BUFF_ID}
    if has_bsv:
        watch_ids.add(BSV_BUFF_ID)
    windows = parse_self_buff_windows(
        log_path,
        tank_name,
        frozenset(watch_ids),
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        start_byte_offset=start_byte_offset,
    )
    sb_windows = windows.get(SHIELD_BLOCK_BUFF_ID, [])
    sw_windows = windows.get(SHIELD_WALL_BUFF_ID, [])
    bsv_windows = windows.get(BSV_BUFF_ID, [])

    bfi_timeline = (
        _bfi_stack_timeline(
            log_path,
            tank_guid,
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            start_byte_offset=start_byte_offset,
        )
        if has_bfi
        else []
    )

    hits: list[HitResid] = []
    n_capped = 0
    max_armor_live = 0
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
            if event_type not in _DAMAGE_EVENT_TYPES:
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
            max_armor_live = max(max_armor_live, armor_live)

            r = (evt.amount + evt.absorbed + evt.blocked) / evt.base_amount
            is_periodic = event_type == "SPELL_PERIODIC_DAMAGE"
            bleed = is_bleed(evt.spell_name, is_periodic=is_periodic)
            physical_nonbleed = evt.school == "physical" and not bleed

            if physical_nonbleed:
                armor_dr_uncapped = armor_live / (armor_live + k)
                armor_dr_capped = min(armor_dr_uncapped, max_armor_dr)
                if armor_dr_uncapped > max_armor_dr:
                    n_capped += 1
                armor_term = 1 - armor_dr_capped
            else:
                armor_dr_uncapped = 0.0
                armor_dr_capped = 0.0
                armor_term = 1.0

            bfi_factor = 1.0
            if has_bfi and physical_nonbleed:
                stacks = min(_stack_at(bfi_timeline, time_s), bfi_max_stacks)
                bfi_factor = 1 - stacks * bfi_per_stack

            base_chain = armor_term * (1 - vers) * always_on * bfi_factor

            sb_active = physical_nonbleed and _in_any_window(sb_windows, time_s)
            sw_active = _in_any_window(sw_windows, time_s)
            bsv_active = has_bsv and _in_any_window(bsv_windows, time_s)
            in_cd_window = sb_active or sw_active or bsv_active

            full_chain_no_sb = base_chain
            if sw_active:
                full_chain_no_sb *= 1 - sw_dr
            if bsv_active:
                full_chain_no_sb *= 1 - bsv_dr
            full_chain = full_chain_no_sb * (1 - sb_dr) if sb_active else full_chain_no_sb

            clean_resid = None if in_cd_window else (r / base_chain if base_chain > 0 else None)
            full_resid = r / full_chain if full_chain > 0 else float("nan")
            full_resid_no_sb = r / full_chain_no_sb if full_chain_no_sb > 0 else float("nan")

            hits.append(
                HitResid(
                    t=time_s,
                    base=evt.base_amount,
                    school=evt.school,
                    armor_live=armor_live,
                    in_cd_window=in_cd_window,
                    sb_active=sb_active,
                    physical_nonbleed=physical_nonbleed,
                    clean_resid=clean_resid,
                    full_resid=full_resid,
                    full_resid_no_sb=full_resid_no_sb,
                    armor_dr_uncapped=armor_dr_uncapped,
                    armor_dr_capped=armor_dr_capped,
                )
            )

    if not hits:
        return None

    full_weights = [h.base for h in hits]
    full_values = [h.full_resid for h in hits]
    full_no_sb_values = [h.full_resid_no_sb for h in hits]
    clean_hits = [h for h in hits if h.clean_resid is not None]
    clean_weights = [h.base for h in clean_hits]
    clean_values: list[float] = [h.clean_resid for h in hits if h.clean_resid is not None]

    return RunWedgeResult(
        label=label,
        n_hits=len(hits),
        clean_n=len(clean_hits),
        clean_wmean=_weighted_mean(clean_weights, clean_values) if clean_hits else None,
        clean_median=_percentile(sorted(clean_values), 0.5) if clean_hits else None,
        full_wmean=_weighted_mean(full_weights, full_values),
        full_median=_percentile(sorted(full_values), 0.5),
        full_no_sb_wmean=_weighted_mean(full_weights, full_no_sb_values),
        full_no_sb_median=_percentile(sorted(full_no_sb_values), 0.5),
        n_armor_dr_capped=n_capped,
        max_armor_live=max_armor_live,
        total_base_sum=sum(h.base for h in hits),
        clean_base_sum=sum(h.base for h in clean_hits),
        sb_active_base_sum=sum(h.base for h in hits if h.sb_active),
        physical_nonbleed_base_sum=sum(h.base for h in hits if h.physical_nonbleed),
        physical_nonbleed_outside_sb_base_sum=sum(
            h.base for h in hits if h.physical_nonbleed and not h.sb_active
        ),
        physical_nonbleed_n=sum(1 for h in hits if h.physical_nonbleed),
        physical_nonbleed_outside_sb_n=sum(
            1 for h in hits if h.physical_nonbleed and not h.sb_active
        ),
    )


def _load_run_char(base_char: Character) -> Character:
    """Mirror cli.py's _build_replay_entry: try COMBATANT_INFO trait-entry-id
    detection, but it structurally never matches on a real log (documented in
    character.py's total_armor() — trait-entry ids != spell ids), so this
    always returns base_char unchanged for the corpora this script targets.
    Kept as a real call (not skipped) so a future fix to that detection path
    is picked up automatically."""
    return base_char


def run_corpus_section(*, logs_dir: Path, iterations: int = 300, seed: int = 42) -> None:
    corpus = load_calibration_corpus(CORPUS_MANIFEST)
    char = load_character(corpus.character_path)
    heal = load_healing_profile("m+_high_key_healer")
    k = load_constants()["armor"]["k_constant"]

    print(f"=== Ratified corpus ({len(corpus.replays)} replays), K={k} ===\n")

    results: list[RunWedgeResult] = []
    sim_deltas: list[float] = []
    for filename, run_index in corpus.replays:
        lf = logs_dir / filename
        label = f"{filename}[{run_index}]"
        if not lf.exists():
            print(f"  SKIP {label}: file not found at {lf}")
            continue
        runs = parse_challenge_modes(lf)
        replay = load_replay(lf, LOG_TARGET, run_index=run_index, runs=runs)
        run_char = _load_run_char(char)

        wedge = compute_run_wedge(
            lf,
            tank_name=LOG_TARGET,
            start_time_s=replay.run.start_time_s,
            end_time_s=replay.run.end_time_s,
            start_byte_offset=replay.run.start_byte_offset,
            run_char=run_char,
            k=k,
            label=label,
        )
        if wedge is None:
            print(f"  SKIP {label}: no per-hit live-armor data")
            continue
        results.append(wedge)

        heal_r = replace(heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1)
        sim = run_simulation(
            run_char,
            None,
            heal_r,
            iterations=iterations,
            seed=seed,
            events_override=replay.events,
            duration_override=replay.duration_s,
            compute_metrics=False,
        )
        real_dtps = replay.actual_dealt / replay.duration_s
        delta = (sim.mean_dtps - real_dtps) / real_dtps
        sim_deltas.append(delta)

        clean_str = (
            f"clean={wedge.clean_wmean:.4f} (n={wedge.clean_n})"
            if wedge.clean_wmean is not None
            else "clean=n/a (every hit fell in a CD window)"
        )
        print(
            f"  {label}: n_hits={wedge.n_hits} {clean_str}  full={wedge.full_wmean:.4f}"
            f"  full_no_sb={wedge.full_no_sb_wmean:.4f}"
            f"  raw_delta={delta:+.1%}  max_armor_live={wedge.max_armor_live}"
            f"  capped_hits={wedge.n_armor_dr_capped}"
        )

    _print_aggregate("RATIFIED CORPUS", results, sim_deltas)


def _print_aggregate(
    title: str, results: list[RunWedgeResult], sim_deltas: list[float] | None
) -> None:
    if not results:
        print(f"\n{title}: no usable runs.")
        return
    n_total = sum(r.n_hits for r in results)
    n_capped_total = sum(r.n_armor_dr_capped for r in results)
    max_armor_overall = max(r.max_armor_live for r in results)

    clean_pairs = [(r.n_hits, r.clean_wmean) for r in results if r.clean_wmean is not None]
    clean_agg = (
        sum(n * v for n, v in clean_pairs) / sum(n for n, _v in clean_pairs)
        if clean_pairs
        else None
    )
    full_agg = sum(r.n_hits * r.full_wmean for r in results) / n_total
    full_no_sb_agg = sum(r.n_hits * r.full_no_sb_wmean for r in results) / n_total

    print(f"\n--- {title} aggregate ---")
    print(f"  runs={len(results)}  total_hits={n_total}")
    clean_disp = f"{clean_agg:.4f}" if clean_agg is not None else "n/a"
    print(f"  clean wedge (hit-count-weighted mean of per-run wmean): {clean_disp}")
    print(f"  full  wedge, AS MODELED incl. Shield Block's physical_dr: {full_agg:.4f}")
    print(f"  full  wedge, SB layer excluded (see SB finding):         {full_no_sb_agg:.4f}")
    print(
        f"  armor-DR cap check: {n_capped_total}/{n_total} hits exceeded max_armor_dr "
        f"before capping; max observed live armor = {max_armor_overall}"
    )

    # 2026-07-21 Lead 1 (DTPS-weighted bin shares, not just hit-count shares).
    total_base = sum(r.total_base_sum for r in results)
    clean_base = sum(r.clean_base_sum for r in results)
    sb_active_base = sum(r.sb_active_base_sum for r in results)
    clean_hit_count = sum(r.clean_n for r in results)
    if total_base > 0:
        print(
            f"  ALL-SCHOOLS 'clean' bin (outside SB/SW/BSV, incl. magic+bleed): "
            f"{clean_hit_count}/{n_total} hits ({clean_hit_count / n_total:.1%} of hit COUNT) "
            f"carries {clean_base / total_base:.1%} of total base_amount (DAMAGE)"
        )
        print(
            f"  SB-active bin (physical, non-bleed) carries "
            f"{sb_active_base / total_base:.1%} of total base_amount"
        )
    pnb_n = sum(r.physical_nonbleed_n for r in results)
    pnb_out_n = sum(r.physical_nonbleed_outside_sb_n for r in results)
    pnb_base = sum(r.physical_nonbleed_base_sum for r in results)
    pnb_out_base = sum(r.physical_nonbleed_outside_sb_base_sum for r in results)
    if pnb_n > 0 and pnb_base > 0:
        print(
            f"  PHYSICAL-NON-BLEED-ONLY, outside SB windows (apples-to-apples with the "
            f"21,402-vs-433 hit-count split): {pnb_out_n}/{pnb_n} hits "
            f"({pnb_out_n / pnb_n:.2%} of physical-non-bleed hit COUNT) carries "
            f"{pnb_out_base / pnb_base:.2%} of physical-non-bleed base_amount (DAMAGE)"
        )
    if sim_deltas:
        mean_delta = sum(sim_deltas) / len(sim_deltas)
        print(f"  measured mean raw sim-vs-real delta: {mean_delta:+.1%}")
        for tag, agg in (("as-modeled", full_agg), ("SB-excluded", full_no_sb_agg)):
            predicted_delta = (1 / agg) - 1
            print(
                f"  [{tag}] full-wedge-predicted delta (1/full_wedge - 1): {predicted_delta:+.1%}"
                f"   residual (measured - predicted): {mean_delta - predicted_delta:+.1%}"
            )


def run_wide_section(filenames: list[str], logs_dir: Path) -> None:
    """Corroboration-only section over a wider slice of the same character's
    personal log archive — NOT part of the ratified K-calibration corpus, and
    never feeds calibration_tier / global_rmse. Only computes the per-hit
    wedge (cheap: pure log parsing, no Monte Carlo) — no sim-delta
    reproduction, since that would require re-hydrating each log's own gear
    snapshot to stay honest about drift, which is out of scope for a
    corroboration pass; the frozen 2026-05-06 calibration character's
    talents (indomitable / brace_for_impact / battle_scarred_veteran, all
    present in brutoh-actual) are the only inputs this section borrows from
    it, and talent loadouts don't drift week to week the way gear does."""
    corpus = load_calibration_corpus(CORPUS_MANIFEST)
    char = load_character(corpus.character_path)
    k = load_constants()["armor"]["k_constant"]

    print(f"\n=== Wide corroboration set ({len(filenames)} files) ===\n")
    results: list[RunWedgeResult] = []
    for filename in filenames:
        lf = logs_dir / filename
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
            label = f"{filename}[{i}]"
            wedge = compute_run_wedge(
                lf,
                tank_name=LOG_TARGET,
                start_time_s=run.start_time_s,
                end_time_s=run.end_time_s,
                start_byte_offset=run.start_byte_offset,
                run_char=char,
                k=k,
                label=label,
            )
            if wedge is None or wedge.n_hits == 0:
                continue
            results.append(wedge)
            clean_str = f"{wedge.clean_wmean:.4f}" if wedge.clean_wmean is not None else "n/a"
            print(
                f"  {label} +{run.key_level}: n_hits={wedge.n_hits} clean={clean_str} "
                f"full={wedge.full_wmean:.4f} max_armor_live={wedge.max_armor_live}"
            )

    _print_aggregate("WIDE CORROBORATION SET", results, None)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", action="store_true", help="run the ratified 16-log section")
    ap.add_argument(
        "--wide", nargs="*", default=None, metavar="FILE", help="wide-corroboration filenames"
    )
    ap.add_argument("--logs-dir", default="examples", type=Path)
    ap.add_argument("--iterations", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.corpus and args.wide is None:
        args.corpus = True  # default: at least run the ratified section

    if args.corpus:
        run_corpus_section(logs_dir=args.logs_dir, iterations=args.iterations, seed=args.seed)
    if args.wide is not None:
        run_wide_section(args.wide, args.logs_dir)


if __name__ == "__main__":
    main()
