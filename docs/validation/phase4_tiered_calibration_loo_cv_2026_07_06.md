# Tiered calibration claims + LOO-CV gate + F-factor reporting — 2026-07-06

Top-5 #4 from the 2026-07-06 retrospective (`ROADMAP.md`'s Active Triage
Queue): "Tiered calibration claims + a real cross-validation gate + the
mob-tuning wedge as an explicit random effect." Three sub-items, design
ratified by calibration-scientist same day.

## A) Tiers — `calibrated: true/false` replaced with `calibration_tier`

`data/constants.yaml`'s per-spec `calibrated: true/false` boolean replaced
with `calibration_tier: placeholder | characterized | calibrated`. Only one
code call site read the old flag (`ui/state.py:477`) — full replacement, no
compat alias (a second copy of the same fact is exactly what
`feedback_verify_computed_output_not_just_copy` exists to prevent).

New helper: `core.constants.spec_is_calibrated(spec_cfg)` — `calibrated`
tier only; unset/typo'd `calibration_tier` defaults to `placeholder` (never
silently reads as calibrated).

**Current real state (2026-07-06):**

| Spec | Tier | Basis |
|---|---|---|
| Prot Warrior | `calibrated` | 16 logs, RMSE 0.068 |
| Guardian Druid | `calibrated` | 16 logs, mean +0.2%, RMSE 0.119, 12/16 within ±15% |
| Brewmaster Monk | `characterized` | 2+ logs decomposed; blocked by F-factor issue (see C) |
| Blood DK | `characterized` | 5 runs one build, 4/5 within ±15%, RMSE 0.152 |
| Vengeance DH | `characterized` | Magic-DR layer still missing, multiple docs |
| Prot Paladin | `characterized` | Holy-power + AD fixed, gaps remain |

No spec is `placeholder` today — all 6 have real characterization work.
The `placeholder` branch of `ui/state.py`'s warning copy has no real-spec
test coverage as a result; `tests/test_uncalibrated_spec_warning.py` injects
a synthetic fake spec so that code path stays covered.

Promotion criteria (encoded in `spec_is_calibrated`'s docstring, not
enforced by code — a human still ratifies the tier bump alongside a
validation doc, same as always):
- **placeholder → characterized**: ≥2 real logs at canonical K, per-run
  deltas published in a `docs/validation/` doc, dual-validator run.
- **characterized → calibrated**: ≥8 F-consistent runs, |mean signed
  delta| ≤5%, ≥75% of runs within ±15%, RMSE ≤0.15, AND the LOO-CV gate
  (B) passes.

## B) Leave-one-out cross-validation gate

`scripts/calibrate_spec_from_logs.py`'s `cmd_calibrate` already builds a
sweep `table[k] = (rmse, deltas)` across ~14 K values — the LOO-CV gate is
pure post-processing of that table, **zero new simulation runs**. For each
held-out run i: refit `bestK_-i` on every OTHER run's deltas (argmin RMSE
excluding i), then check whether that held-out-blind K still predicts run
i. Two checks:
1. **K stability**: `max(|bestK_-i - bestK_full|) ≤ 300`, anchored to the
   full-corpus best K (see "first real application" below for why it's
   anchored this way, not a raw spread of the excl-i extremes).
2. **Held-out prediction**: `|delta_i(bestK_-i)| ≤ 15%` for ≥75% of folds;
   any fold beyond ±25% is a hard fail.

Explicitly labeled a **WEAK** cross-validation in every place it prints or
is documented: K is the only degree of freedom refit per fold; the ledger
constants (the real overfit risk) are never refit. A floor, not a proof.

### First real application — a bug in the gate, then a real finding

First run, against the Guardian Druid corpus (`examples/anonguardian1-guardian/`,
17 timed runs, iters=300 — confirmed identical to iters=100, so this isn't
Monte Carlo noise), FAILED: `K stability: spread=250 (max 100) -> False`.
Running the SAME check against the Prot Warrior corpus (`examples/`, 5
timed runs — see the note below on why only 5, not the documented 16) ALSO
failed stability, despite a perfect `5/5 within ±15%` held-out prediction.
Two different specs, two different corpora, same 250 spread, same failure
— that pattern means the check, not the corpora, was wrong.

**Root cause:** the original check compared `max(bestK_-i) - min(bestK_-i)`
against a 100-point bar, but the default K sweep steps by 250
(`{2000, 2250, ..., 5000} ∪ {3430}`). On BOTH corpora, `bestK_-i` only ever
landed on `{3250, 3430, 3500}` — every fold agreeing with (or one grid-step
either side of) the full-corpus best K=3430. A 250-wide spread across a
250-wide grid is the grid's own resolution, not corpus instability. Fixed
by anchoring to `bestK_full` (max deviation from the whole-corpus fit, not
the spread of the excl-i estimates' own extremes) with a 300 tolerance —
just over one grid step. Regression-pinned in
`tests/test_calibrate_spec_loo_cv.py` against the exact real Warrior table
that exposed the bug, plus synthetic cases for a genuinely unstable corpus
(must still fail) and a stable-but-bad-held-out corpus (must fail on
held-out specifically, not get relabeled as a stability failure).

**After the fix:**

| Spec | Corpus | K stability | Held-out prediction | LOO-CV GATE |
|---|---|---|---|---|
| Prot Warrior | 5 runs, `examples/` | max_dev=180 ≤300 → stable | 5/5 (100%) within ±15% | **PASS** |
| Guardian Druid | 17 runs, `examples/anonguardian1-guardian/` | max_dev=180 ≤300 → stable | 12/17 (71%, need ≥75%) within ±15%, one fold at +25.1% (hard-fail) | **FAIL** |

This is now a clean, coherent result: Warrior's LOO-CV gate **confirms**
its `calibrated` tier under an independent, stricter check. Guardian's gate
**fails on held-out prediction specifically** — a real, isolated finding
(not a gate artifact) that the aggregate-RMSE criteria used to ratify it
2026-06-29 didn't surface. Guardian's `calibration_tier` is **NOT changed**
by this doc — downgrading a human-ratified tier on the strength of a
same-day gate's first run would repeat exactly the mistake this
retrospective item exists to prevent (a trust claim moving without real
ratification). Named as an explicit follow-up for calibration-scientist:
**investigate which held-out fold(s) drive Guardian's 71% and whether it
points at a real per-run fold-sensitivity worth naming in the existing
Guardian docs, before any tier change.**

### 2026-07-07 review follow-up — join the LOO-CV gate against F-consistency

The FAIL above ran on Guardian's **full** 17-run corpus, which includes the
4 runs the F-consistency screen (section C) independently flags as
outliers. Those 4 runs' deltas at canonical K are +16.7pp/+8.3pp/-7.5pp/
-7.4pp — exactly the shape of the kind of fold that could single-handedly
produce a hard-fail. The original write-up above called the FAIL "a real,
isolated finding (not a gate artifact)" without ever checking whether the
failing fold(s) were the same runs F already flagged — an unjoined claim,
not a proven one.

**Fix:** `_run_loo_cv` now also runs on the F-consistent subset alone (pure
post-processing of the same sweep table — zero new sims) whenever F
excluded anything, printed as a clearly-labeled second gate. Unit-tested
against synthetic tables (`tests/test_calibrate_spec_loo_cv.py`); **the
live re-run against the real 17-run Guardian corpus is a pending
follow-up, not completed in this pass** — a 17-run × 14-K sweep at even 20
iterations exceeded a 3.5-minute budget on this Pi (a real 20-30 minute
dungeon replay is far heavier per-iteration than the short synthetic
profiles most of this project's tooling runs against), and a rushed,
truncated run would risk reporting a number as authoritative that wasn't
actually verified — exactly the failure mode this whole retrospective item
exists to prevent. Run
`calibrate_spec_from_logs.py calibrate guardian_druid --logs-dir
examples/anonguardian1-guardian` (budget 15+ minutes) to get the real subset
numbers before using them to inform any Guardian tier decision.

This doesn't change Guardian's `calibration_tier` (still correctly not
re-ratified off a same-day gate), but it does correct the doc's own
overclaim above: the FAIL should be read as "on the full corpus, including
4 known F-outlier runs, not yet isolated from the held-out failure" —
narrower than "a real, isolated finding."

**COMPLETED 2026-07-08** (Pi 5; `master` @ `4bb8a0d`; iters=300 with an
independent iters=100 cross-check, byte-identical output): **the
F-consistent-subset gate also FAILS** — 9/13 (69%) held-out folds within
±15% (need ≥75%), K stability PASS, no hard-fail. The full-corpus FAIL is
NOT attributable to the F-outlier runs: three of the four excluded runs
were held-out *successes* (−10.1%/−14.3%/−12.0%), so exclusion made the
ratio worse (12/17 → 9/13). This section's premise quoted the wrong deltas
for the excluded runs — they are actually −7.6/−11.9/+23.1/−9.4 at
canonical K, not "+16.7/+8.3/−7.5/−7.4"; the +16.7pp run is Maisara+12,
which is F-*consistent* and stays in the subset. The documented "+25.1%
hard-fail" fold also did not reproduce (worst fold today +23.4% full /
+24.9% subset — both under ±25%). Full numbers, fold anatomy, marginality
analysis (the nearest failing fold is 0.3pp past the ±15% bar), and the
options a tier ratification would choose between:
`phase4_guardian_loo_cv_f_consistent_2026_07_08.md`. Guardian's tier is
unchanged by that doc too — it supplies the measurement a human
ratification would need, and its grandfathering (below) is now
load-bearing rather than precautionary.

### Two things this doc got wrong the first time

1. **Cross-spec F interpretation.** The original write-up (see section C
   below) attributed Warrior's lower F (0.725) vs Guardian's (0.824) to
   "physical-heavy Warrior gear taking more of the wedge's bite" — a
   school-mix amplification story. That's wrong: this tool's F is relative
   to the armor+versatility model ONLY, so it also picks up every
   always-on flat DR layer a spec's own mitigation chain applies
   (Defensive Stance + Indomitable for Warrior, Thick Hide + Bear Form's
   passive for Guardian) — layers that have nothing to do with the
   run-scoped wedge the Brewmaster doc named. Dividing out each spec's
   modeled always-on chain (Warrior ≈0.85×0.96=0.816, Guardian
   ≈0.96×0.97=0.931; ratio 0.877) reproduces the observed raw-F ratio
   (0.725/0.824=0.880) to within 0.3% — meaning the WEDGE itself sits at
   roughly the same ~0.93-0.95 on both specs, which actually **confirms**
   the Brewmaster doc's "universal wedge" finding rather than contradicting
   it with a spec-dependent amplification mechanism. Raw F magnitudes are
   NOT comparable across specs with different always-on chains; only
   within-corpus consistency is meaningful, which is exactly how the
   F-consistency gate uses it. (`measure_run_f.py`'s docstring now states
   this explicitly.)
2. **Bleed ticks ran through the armor curve.** `measure_run_f.py`'s first
   cut checked `event.school == "physical"` only, missing WoW's rule that
   physical bleeds (Rend, Rip, etc.) bypass armor entirely
   (`core/mitigation.py`'s own `not event.is_bleed` gate). Fixed to match
   — `not is_bleed(evt.spell_name)` alongside the school check. Measured
   impact on the two corpora actually run so far: nil (0% bleed ticks on
   6 spot-checked Guardian runs, 0-1.6% on 4 Warrior runs — medians
   unchanged), but it would have silently biased the median/IQR on any
   future bleed-heavier corpus.

### Grandfathering — neither existing `calibrated` tier meets today's bar as measured

Read literally, the new promotion bar (≥8 F-consistent runs, LOO-CV gate
passes) is **not met by either currently-`calibrated` spec** as this
tooling measures them right now: Prot Warrior's tool-visible corpus is 5
runs (<8 — the tool only sees 5 of the headline 16 logs, see the corpus-
definition note below), and Guardian fails the full-corpus LOO-CV gate
(pending the F-consistent-subset re-run above). Both tiers are correctly
**grandfathered** under the pre-2026-07-06 ratification bar — the new bar
governs future promotions, not a retroactive re-litigation of
already-shipped trust claims — but that grandfathering was implicit before
this note; it should not be re-discovered as a surprise later.

### A structural ceiling: WCL-only corpora can't reach `calibrated`

`measure_run_f` returns `None` for WCL imports and ACL-off logs (no
per-hit live armor), so the `calibrated` tier's `≥8 F-consistent runs`
requirement is **mechanically unreachable** from a WCL-only corpus. This
is probably the right call — it codifies the project's existing "WCL is
characterization-grade" norm rather than relaxing it — but it's a real,
previously-unstated gate on a named roadmap priority: Blood DK (roadmap's
"closest spec to calibrated") is currently a 5-fight WCL-only corpus, and
cannot promote past `characterized` without local ACL-on logs, regardless
of how clean its deltas are. `scripts/calibrate_spec_from_wcl.py`'s
promotion guidance now says this explicitly.

Side note on corpus definitions: `calibrate_spec_from_logs.py`'s own
`_tank_runs` (parse_challenge_modes + detect_party_roles, both gates
required) found only **5** timed Prot Warrior runs in `examples/`, not the
documented "16 logs" the K=3430/RMSE=0.068 headline is based on. A
separate check (`simf calibrate-k --logs-dir examples`) found **24** logs
via a looser recursive glob. Neither number is wrong for what each tool
measures, but the discrepancy means "the corpus" isn't a single agreed-on
set across this codebase's calibration tooling — not chased down further
this session, named here so it doesn't get re-discovered from scratch.

## C) F-factor per-run reporting

`scripts/measure_run_f.py` (new) — the cheap, gate-tier sibling of
`scripts/per_hit_mitigation_forensics.py`. Per the Brewmaster decomposition
doc's own unimplemented recommendation ("compute and report each run's
measured F... `calibrated:` judgments should be made on F-consistent
corpora... do NOT bake any F into constants.yaml"):

```
r     = (amount + absorbed + blocked) / base_amount
expl  = (1 - armor_live/(armor_live+K)) * (1 - vers)   [physical]
      = (1 - vers)                                       [other schools]
F_hit = r / expl
```

Deliberately coarser than the full forensics script: no tank-CD-window
exclusion (that script's aura/PT/debuff binning), just the **median**
across every hit in the run — CDs are active a minority of fight time, and
the median is robust to a minority-fraction contaminating subset. For a
root-cause decomposition (not a gate), `per_hit_mitigation_forensics.py`
remains the CD-window-clean, attribution-capable deep-dive tool. Returns
`None` (not a fabricated F=1.0) when the log has no per-hit live armor
(ACL-off logs, WCL import) — honest degradation, not neutral evidence.

Wired into `calibrate_spec_from_logs.py`'s `cmd_calibrate`: prints F per
run, then an F-consistency summary excluding runs where
`|F_i - median(F)| > 0.05` (the observed norm band from the Brewmaster doc
spans 0.04; this is the same band, not re-derived). Deltas at canonical K
are still reported RAW regardless of exclusion — F gates corpus admission
for tier promotion, it never adjusts or hides a real number.

**Guardian corpus result:** 17/17 runs measurable (real ACL logs, so
per-hit armor is present), median F=0.824, 13/17 F-consistent (4 excluded:
0.891, 0.890, 0.706, 0.761 — all outside the ±0.05 band). Confirms the
Brewmaster doc's finding generalizes: the universal run-scoped wedge is
real and visible on Guardian too, not Brewmaster-specific.

**Prot Warrior corpus result:** 5/5 runs measurable, median F=0.725, 5/5
F-consistent (0.725, 0.733, 0.714, 0.682, 0.764 — all within the ±0.05
band of each other) — the whole 5-run corpus clears the F-consistency band
with zero exclusions, unlike Guardian's 4-of-17. Notably LOWER than
Guardian's 0.824 — **corrected 2026-07-07** (see the review-follow-up
section below): this is NOT a school-mix wedge-amplification effect. This
tool's F divides out the armor+versatility model only, so it also carries
each spec's own always-on flat DR chain (Defensive Stance + Indomitable
for Warrior vs. Thick Hide + Bear Form's passive for Guardian) — dividing
that chain back out shows the underlying wedge sits at roughly the same
~0.93-0.95 on both specs, consistent with (not contradicting) the
Brewmaster doc's universal-wedge finding. Raw F magnitudes are comparable
within a spec's own corpus, not across specs with different chains.

## Known pre-existing contamination (flagged, not fixed here)

The Brewmaster decomposition doc names a real, separate bug: warrior
replay double-counts Demoralizing Shout (the -20% is already inside
`base_amount`; the replay path applies it again on top). This is printed
as a standing warning banner in the LOO-CV gate's output every time it
runs, per that doc's explicit ask, but **not fixed in this PR** — it's a
validator-lane fix with its own investigation and testing needs, correctly
scoped separately (matches the project's established pattern: flag real
bugs found during a decomposition, don't silently fold the fix into an
unrelated PR).

## Full test suite

2037 passed, 7 skipped (tier work alone confirmed at 2032/7 before the LOO-CV/F
additions; final count in the PR).
