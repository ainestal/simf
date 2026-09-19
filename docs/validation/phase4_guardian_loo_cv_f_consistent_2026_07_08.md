# Guardian LOO-CV on the F-consistent subset — the pending re-run, completed (2026-07-08)

Completes the explicitly-named pending follow-up from
`phase4_tiered_calibration_loo_cv_2026_07_06.md` ("the live re-run against
the real 17-run Guardian corpus is a pending follow-up, not completed in
this pass"). That doc's open question: is Guardian's full-corpus LOO-CV
FAIL (12/17 held-out folds within ±15%, need ≥75%) attributable to the 4
runs the F-consistency screen independently flags as outliers, or is it a
genuine held-out-prediction miss on the clean corpus?

**Answer: it is NOT attributable to the F-outliers. The F-consistent-subset
gate also FAILS — 9/13 (69%) held-out folds within ±15%, need ≥75% — and
removing the F-outliers makes the ratio slightly worse, not better, because
three of the four excluded runs were held-out *successes*.** The follow-up's
premise (that the excluded runs' deltas were "+16.7pp/+8.3pp/−7.5pp/−7.4pp,
exactly the shape that could single-handedly produce a hard-fail") turns out
to have been a mis-join — see "Correction" below.

This doc records a measurement. It does **not** change
`specs.guardian_druid.calibration_tier` — that stays a human ratification,
same as the 2026-07-06 doc's own discipline.

## Setup

- **Hardware:** Raspberry Pi 5 (4 cores, 7.9 GB RAM) — the run the old Pi 4
  couldn't complete inside any reasonable session budget. Wall-clock at the
  tool's default `--iters 300`: ~25 min total (~3.5 min corpus
  hydrate/F-measurement + ~21 min sweep, run concurrently with an
  `--iters 100` job and an unrelated browser session; ~19 min alone by
  per-iteration probe arithmetic at ~9–25 ms/iter per replay).
- **Engine state:** `master` @ `4bb8a0d` (post-#285–#307; notably AFTER
  PR #288's healer token-bucket rework, which postdates the numbers in the
  2026-07-06 doc).
- **Corpus:** `examples/anonguardian1-guardian/`, 17 timed `guardian_druid` runs
  (tool-enumerated; identical set to the 2026-07-06 doc).
- **Command:**
  `python scripts/calibrate_spec_from_logs.py calibrate guardian_druid
  --logs-dir examples/anonguardian1-guardian --iters 300` (seed 42, canonical
  K=3430), plus an independent `--iters 100` run as the Monte-Carlo-noise
  cross-check.
- **Noise check:** the two runs' outputs are **byte-identical except the
  header line** — every per-run delta (17 runs × 14 K values), both RMSE
  tables, every fold of both gates. Replay-mode iterations do consume RNG
  (avoidance rolls in `apply_mitigation`), and iteration seeds are
  `seed + it`, so the 100-run's iterations are a strict prefix of the
  300-run's — the added 200 iterations moved no printed number by even
  0.05pp. Same behavior the 2026-07-06 doc observed. The gate results below
  are not Monte Carlo noise; `--iters 100` is sufficient for this gate and
  3× cheaper.

## Results

### F-consistency screen — reproduced exactly

13/17 F-consistent, median F=0.824, band ±0.05. Excluded (same 4 as
2026-07-06): F=0.891 (AA+12, `WoWCombatLog-062026_074836.txt[1]`), 0.890
(AA+15, `062026_202902[0]`), 0.706 (MT+15, `062326_182203[1]`), 0.761
(AA+16, `062926_134306[1]`).

### Fit quality at canonical K=3430 (identical at iters 100 and 300)

| Scope | N | Mean signed error | RMSE | Within ±15% |
|---|---|---|---|---|
| Full corpus | 17 | −0.40% | 0.118 | 14/17 |
| F-consistent subset | 13 | **−0.08% (unbiased)** | **0.109** | 11/13 |

Best K = 3250 (RMSE 0.1180) vs canonical 3430 (RMSE 0.1183) — a flat basin;
the canonical anchor costs nothing measurable. The subset is dead-center
unbiased with a slightly tighter RMSE than the full corpus.

### LOO-CV gates

| Gate | K stability (≤300) | Held-out within ±15% (≥75%) | Any fold >±25%? | Verdict |
|---|---|---|---|---|
| Full corpus (17 folds) | max_dev=180 → PASS | 12/17 = 71% → fail | **No** (worst +23.4%) | **FAIL** |
| F-consistent subset (13 folds) | max_dev=250 → PASS | 9/13 = 69% → fail | No (worst +24.9%) | **FAIL** |

Full-corpus held-out reproduces the documented 12/17 = 71% exactly. The
documented "+25.1% hard-fail" fold did **not** reproduce — today's worst
fold is +23.4% (full) / +24.9% (subset), under the ±25% hard bar. Both
gates now fail on the 75% soft criterion alone. Plausible cause for the
~1pp shift: engine drift from PRs #285–#290 (most plausibly #288's healer
rework) between the two measurements, and/or the original fold's refit
landing on K=3500 instead of 3430 in a nearly-tied RMSE basin (at K=3500
the same run reads +24.9% today). Not distinguishable retroactively; either
way the hard-fail component of the 2026-07-06 result is not present in
today's authoritative run.

### Fold anatomy — why removing the F-outliers doesn't rescue the gate

Subset failing folds (4 of 13): AA+12 `062026_171634[0]` −15.3%
(**0.3pp over the bar**), Maisara+12 +16.6%, Maisara+15 +24.9%,
MT+12 −17.3%. Mixed signs — this is dispersion, not bias.

Of the 4 F-excluded runs, only ONE (MT+15, +23.1%) was a failing fold on
the full corpus; the other three were held-out successes (−10.1%, −14.3%,
−12.0%). So exclusion removed 1 failure but 3 successes: 12/17 (71%) →
9/13 (69%). The F-screen and the held-out misses point at mostly
*different* runs.

The three largest positive deltas today (+16.6 / +23.4 / +23.1) are
numerically identical to the "3 outliers (+16.6 / +23.4 / +23.1)" the
2026-06-29 flip doc (`phase4_guardian_calibrated_2026_06_29.md`) already
named as its positive magic-residual outliers. Two of them (both Maisara
Caverns runs) are **F-consistent** — their over-prediction is model
residual, not the run-scoped mob-tuning wedge. The third (MT+15) is the
F=0.706 outlier.

## Correction to the 2026-07-06 doc's follow-up premise

The 2026-07-07 review follow-up stated the F-excluded runs' canonical-K
deltas were "+16.7pp/+8.3pp/−7.5pp/−7.4pp — exactly the shape of the kind
of fold that could single-handedly produce a hard-fail." As measured (both
then-current engine values +16.6/+23.4/+23.1 for the big positive runs
reproduce today, so this is not drift): the excluded runs' actual deltas
are **−7.6% / −11.9% / +23.1% / −9.4%**. The +16.7pp run in that quote is
Maisara+12 — which is F-*consistent* (F≈0.787) and stays in the subset.
The hypothesis that F-contamination drove the gate failure was built on a
mis-join of deltas to runs; the real join, now measured, refutes it.

## Interpretation

1. **The held-out-prediction weakness is real and survives F-screening.**
   It is not a gate artifact (the 2026-07-06 grid bug is fixed and K
   stability passes comfortably at both scopes), and not F-contamination.
   It lives in the model: predominantly the known magic-residual runs
   (both Maisara folds, +16.6/+24.9) plus two negative folds (−15.3,
   −17.3) that the aggregate-RMSE criteria never surfaced.
2. **It is marginal, not a blowout.** The gate needs 10/13; it got 9/13,
   and the nearest failing fold is 0.3pp past the ±15% bar (−15.3%). A
   result this close to the bar should be read as "does not clear the bar"
   — not as evidence the model got worse. The same corpus is unbiased
   (−0.08%) with RMSE 0.109 at canonical K on the clean subset.
3. **Guardian's `calibrated` tier remains grandfathered, and the
   grandfathering is now load-bearing.** Under the 2026-07-06 promotion
   bar (≥8 F-consistent runs ✓ 13, |mean| ≤5% ✓, RMSE ≤0.15 ✓, ≥75% within
   ±15% ✓ 11/13=85% at canonical, LOO-CV gate ✗) Guardian passes every
   criterion EXCEPT the LOO-CV gate, at both scopes. IF a human later
   chooses to re-ratify against the new bar, the honest options are:
   (a) keep `calibrated` explicitly grandfathered with this doc as the
   named caveat (status quo, now with measured backing), (b) downgrade to
   `characterized` until the magic-residual folds are modeled (the
   documented Glistening Fur ~2.6pp + reactive-CD-timing decomposition in
   `phase4_guardian_magic_mit_2026_06_29.md` is the standing lead), or
   (c) revisit whether a single 0.3pp-over-bar fold failing a 76.9%-of-13
   quantized threshold is the right promotion semantics for small corpora.
   This doc deliberately recommends none of them — it supplies the
   numbers a ratification would need.
4. **What a tier decision should NOT cite this doc for:** the absolute
   fit didn't regress — canonical-K RMSE (0.118 full / 0.109 subset) and
   bias (−0.4% / −0.1%) are as good as or better than the 2026-06-29 flip
   evidence (RMSE 0.119, +0.2% on 16 runs). What's new is a *stricter
   question* (held-out prediction per fold), not worse numbers on the old
   question.

## Tooling notes (first-real-application findings, per house precedent)

- **No progress output during the sweep.** `cmd_calibrate` builds the
  whole 14-K table in one dict comprehension before printing anything —
  a ~21-minute silent window at default iters on this corpus (and stdout
  is block-buffered when redirected; run with `PYTHONUNBUFFERED=1`). A
  per-K progress line to stderr would make long runs monitorable. Nit,
  not a correctness bug.
- **`--iters 100` is sufficient for this gate** (byte-identical output to
  300 at print precision, 3× cheaper) — worth documenting as the standard
  gate budget now that two sessions have confirmed it.
- **Label-collision edge case (not triggered here):** run labels truncate
  the filename to 22 chars (`lf.name[:22]`), and the F-consistent subset
  is joined back to folds *by label string*. Two same-hour log files
  (`WoWCombatLog-062026_17xxxx`) with the same run index, dungeon, and key
  level would collide and could mis-admit/mis-exclude a twin. Verified
  distinct on this corpus; worth a defensive index-based join if the
  corpus ever grows automated ingestion.
- **Doc mis-join corrected** (see "Correction" above) — the kind of
  unjoined claim the 2026-07-07 review round itself existed to catch; it
  caught the missing join but then quoted the wrong deltas for the joined
  set.

## What could still be wrong

- This remains a **WEAK CV**: K is the only degree of freedom refit per
  fold. The Guardian ledger constants (Ironfur retune, haste elasticity,
  NG mastery) were fit on this same corpus and are never refit — the real
  overfit risk is unmeasured by this gate in either direction.
- Single player (AnonGuardian1), single build (Elune's Chosen) — the flip doc's
  standing caveats apply unchanged; nothing here tests cross-player
  generalization.
- The tool's own standing contamination banner applies: the cross-spec
  ~5-7% run-scoped wedge and the un-fixed warrior-replay Demo Shout
  double-count (both `phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`)
  mean the thresholds aren't contamination-free.
- The ±15%/75%/±25% thresholds and the ±0.05 F band are same-day-ratified
  design choices; on a 13-run corpus the soft criterion quantizes to
  10/13 = 76.9%, so one fold sitting 0.3pp past the bar flips the verdict.
  That sensitivity is named here, not relitigated.
