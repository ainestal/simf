# Prot Warrior F-layer measurement — corpus is F-consistent, but the "sim × F" fast-follow as specified is invalid (2026-07-21)

**Status: measurement + tooling shipped (branch `feat/f-layer-warrior-measurement`, not merged);
no constants.yaml value changed; `calibration_tier` unchanged.** This closes out the fast-follow
named in `docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md` ("What this means
for the headline", option A) — but the answer is more nuanced than that doc anticipated. The
corpus's F IS consistent (a real, clean, positive result). The specific correction mechanism that
doc sketched ("measure per-run F, judge on F-corrected residuals... likely returns the warrior to
~+5% residual") does not work as stated, and this doc explains why, with an independent
validator audit confirming the mechanism before any code shipped based on it.

## Summary

`simf calibrate-k` now measures and reports each local-log replay's diagnostic F (the per-run
base-to-applied damage multiplier from `scripts/measure_run_f.py`, already used to gate
non-warrior spec corpora in `scripts/calibrate_spec_from_logs.py`) alongside its existing K sweep.
Run against the ratified 16-log Prot Warrior corpus at the pinned canonical K=3430:

- **The corpus is F-consistent**: median F=0.699, 15/16 runs within ±0.05 of the median (the same
  band convention `scripts/calibrate_spec_from_logs.py` already uses to gate other specs' corpus
  admission). This is a real, positive finding — it means the corpus isn't secretly dominated by
  one contaminated log the way Brewmaster's AnonBrewmaster1 ×0.83 run was.
- **The naive "sim × F" residual correction is mathematically invalid for this spec** and was
  caught before shipping: an early version of this tool computed it, and it flipped the corpus
  from +11.0% over-predicting to a **−22.3% mean bias** (RMSE 0.134 → 0.228) — a 33-point swing in
  the wrong direction. That swing is not noise; it is the signature of a double-counted DR layer,
  confirmed by an independent validator audit (below) and by clean arithmetic that recovers the
  raw +11% bias exactly from the same numbers.
- **The Demo Shout doc's "~+5% residual" prediction is neither confirmed nor refuted by this
  measurement.** Testing it validly requires a different tool than the one specified in the
  fast-follow — see "What this means" below.

## Method

`scripts/measure_run_f.py`'s `measure_run_f()` computes, per hit, `F_hit = r / expl` where
`r = (amount + absorbed + blocked) / base_amount` (what really happened) and
`expl = (1 - armor_live/(armor_live+K)) * (1 - vers)` for non-bleed physical hits, or
`expl = (1 - vers)` otherwise (its own module docstring, `scripts/measure_run_f.py:24-37`). The
module is explicit that **F is relative to the armor+versatility model ONLY** — it does not
divide out a spec's own always-on flat DR (Defensive Stance, Thick Hide, etc.), so raw F
magnitudes are not comparable across specs and are only meaningful for within-corpus consistency
(exactly how the existing F-consistency gate in `scripts/calibrate_spec_from_logs.py` uses it).

`src/simf/cli.py`'s `calibrate_k` (local-log path only — `--wcl-url` fights carry no per-hit live
armor and are untouched) now:

1. Loads `measure_run_f` dynamically from `scripts/measure_run_f.py` (`_load_measure_run_f`,
   mirroring how `tests/test_measure_run_f.py` already loads it by file path rather than as an
   installed package module — the script deliberately lives outside `src/`). Returns `None`
   gracefully (never raises, never blocks the sweep) if the script isn't present, e.g. a packaged
   deploy shipping only `src/simf`.
2. Calls it per replay in `_build_replay_entry`, using the SAME `ReplayData.run` fields
   (`start_time_s`/`end_time_s`/`start_byte_offset`) already loaded for the sweep, canonical
   K read from constants.yaml *before* the sweep mutates it, and `run_char.versatility_dr()`.
3. Prints an `=== F-layer diagnostic ===` section after the sweep (`_report_f_layer`): F-consistency
   (median, ±0.05 band, count in/out) and each run's raw (uncorrected) delta — reporting only,
   never fed back into K, `constants.yaml`, or `calibration_tier`.

Run: `simf calibrate-k --k-min 3430 --k-max 3430 --k-step 1 --iterations 300` (pins the sweep to
exactly the canonical K, seed=42, against the ratified manifest
`src/simf/data/calibration_corpora/prot_warrior_2026_05.yaml`, the default corpus for this
character).

## Measurement — all 16 ratified replays, canonical K=3430, iters=300, seed=42

| Label | F (median) | n_hits | IQR | Raw Δ (sim vs real) | In band (±0.05 of 0.699)? |
|---|---:|---:|---|---:|---|
| Algeth'ar Academy +12 (050626_172823[0]) | 0.733 | 2,109 | 0.687–0.768 | −6.9% | yes |
| Skyreach +14 [partial] (051026_151539[0]) | 0.692 | 938 | 0.687–0.752 | +0.1% | yes |
| Algeth'ar Academy +14 (051826_135935[1]) | 0.683 | 2,114 | 0.669–0.750 | +2.2% | yes |
| Windrunner Spire +14 (051026_090846[2]) | 0.675 | 2,270 | 0.655–0.712 | +20.4% | yes |
| Windrunner Spire +14 [partial] (051526_210245[3]) | 0.678 | 1,473 | 0.642–0.716 | +20.9% | yes |
| Magisters' Terrace +14 (051726_134819[1]) | 0.680 | 2,925 | 0.646–0.724 | +19.6% | yes |
| Nexus-Point Xenas +12 (051026_073906[0]) | 0.707 | 1,318 | 0.686–0.769 | +7.0% | yes |
| Magisters' Terrace +12 (051526_210245[0]) | 0.685 | 2,902 | 0.681–0.744 | +19.6% | yes |
| Pit of Saron +13 (051326_171842[0]) | 0.706 | 2,084 | 0.685–0.767 | +12.3% | yes |
| Maisara Caverns +12 (051626_161830[3]) | 0.669 | 2,038 | 0.648–0.727 | +10.3% | yes |
| Windrunner Spire +12 (050626_153703[0]) | 0.725 | 1,913 | 0.703–0.788 | +7.0% | yes |
| Pit of Saron +12 (051526_210245[2]) | 0.719 | 1,805 | 0.666–0.761 | +15.3% | yes |
| Maisara Caverns +14 (051826_135935[0]) | 0.680 | 1,884 | 0.659–0.739 | +15.6% | yes |
| **Pit of Saron +13 (051026_105836[1])** | **0.755** | 2,273 | 0.695–0.779 | +8.1% | **no — 0.056 outside band** |
| Magisters' Terrace +13 (051726_134819[0]) | 0.711 | 2,658 | 0.690–0.750 | +14.4% | yes |
| Pit of Saron +14 (051726_090237[1]) | 0.722 | 2,531 | 0.701–0.785 | +9.3% | yes |

**F measurable on 16/16 runs** — every ratified replay is a local ACL-on log carrying per-hit live
armor, unlike this corpus's WCL siblings elsewhere in the project.

**Aggregate (unweighted, all 16, matches the corpus's existing headline)**: RMSE=0.1339, mean
signed error=+11.0% (consistent with the ratified figure of +11.5%/RMSE 0.138 elsewhere in this
project's docs; the small difference is measurement-instant/rounding, not a discrepancy).

**F-consistency verdict: 15/16 in-band (94%)**, median F=0.699. One mild outlier — Pit of Saron
+13, second replay (051026_105836[1]) — at F=0.755, 0.056 outside the ±0.05 band, a small overshoot
(for comparison, the Brewmaster corpus's own outlier, AnonBrewmaster1's timed +17, missed its band by
~0.11-0.12, roughly double this run's miss). **This corpus is cleaner and more internally
consistent than the Brewmaster corpus that established the F-consistency methodology.**

## Why "sim × F" is invalid (and why the −22.3% overshoot proves it, not just suggests it)

`measure_run_f.py`'s F is explicit that it excludes a spec's own always-on flat DR. For
Protection Warrior, Defensive Stance's −15% all-schools cut (`mitigation.py:306-309`,
`defensive_stance_dr: 0.15`) applies **unconditionally during log replay** — there is no
`and not event.is_log_replay` gate on it, unlike the Demoralizing Shout and Phalanx blocks a few
lines below it, which do carry that gate (per the 2026-07-17 fix this fast-follow continues). The
comment at `mitigation.py:355-356` states the asymmetry is deliberate: Defensive Stance "is NOT in
`unmitigatedAmount` and so MUST be modeled in replay," unlike the attacker-side debuffs.

That means every `sim` value the K sweep computes during replay **already contains** the −15%
Defensive Stance cut (plus, on the hits where they're active, Shield Block's physical DR aura,
Indomitable, and party-aura DR). F, by the module's own explicit design, does **not** divide any
of that back out — so F still carries that same chain as "unexplained." Multiplying `sim` by F
therefore applies that chain a **second time**.

Writing `sim = base·(1-armor)(1-vers)·D` (D = the modeled extra-DR chain beyond armor+vers — DS
and friends) and noting F itself equals the real extra-DR ratio `R` (since armor/vers terms are
identical between the sim's model and F's `expl`, using the same K and the same live armor):

```
sim × F = base·(1-armor)(1-vers)·D · R = real · D
```

i.e. `sim × F` re-multiplies the entire modeled extra-DR chain a second time. This was confirmed
independently by a validator audit before shipping (not just derived by this agent) — see
"Independent validation" below.

**The −22.3% overshoot is quantitatively consistent, not merely suggestive.** From
`sim × F = real · D`, the F-corrected bias equals `D − 1` exactly. A Defensive-Stance-only
double-count (D=0.85) predicts a corrected bias of −15% (a 26pp swing from the raw +11%); the
measured −22.3% implies `D ≈ 0.777` — Defensive Stance (0.85) times a further ~0.914 from the
rest of the modeled chain (Shield Block's averaged uptime, Indomitable, party_dr where active).
Cross-check: the raw bias equals `D/R − 1`; with `D≈0.777` and the measured raw bias +11%,
`R ≈ 0.777/1.11 ≈ 0.700` — matching the measured median F (0.699) to three significant figures.
Every number ties out from first principles; there is no unexplained residual left in this
arithmetic, which is itself evidence the double-counting mechanism (not some other bug) is the
whole explanation.

### Independent validation

A `validator` agent (engine-math audit mode) independently re-derived this from source before
this doc was finalized, without being given the derivation above to check against — only the
claim and where to look. Findings, verbatim conclusion: **"CONFIRMED... Removing `sim×F` was the
right call... The −22.3% overshoot is quantitatively consistent with the double-count... no
evidence of a stray arithmetic error." **It also flagged two additional, independent reasons
`sim × F` is ill-posed even setting Defensive Stance aside: (1) F's armor term uses per-hit live
armor from the log, while `sim` uses the character's static `total_armor()` — these don't cancel
exactly; (2) F is a per-hit median while `sim`/`real` are aggregate DTPS — multiplying an
aggregate by a per-hit median mixes two different aggregations. Neither changes the conclusion;
both reinforce it. The same audit found **zero bugs** in the CLI wiring itself (K capture timing,
label-based F-to-run matching, band math, `k`/`vers` parameter passing) — the only defect was the
"sim × F" formula, not the plumbing around it.

## What this means

- **`calibration_tier` stays `characterized`. `global_rmse` stays 0.138. No constants.yaml value
  changed.** Only comments were added, pointing here (see "Files changed").
- **The corpus's F-consistency is a genuine, positive result**, worth keeping: 15/16 in a tight
  band is exactly the kind of evidence that makes a residual trustworthy to act on later, and it's
  cleaner than the corpus that first established this methodology (Brewmaster).
- **The Demo Shout doc's "~+5% residual" prediction is untested by this measurement, not refuted.**
  `measure_run_f.py` is deliberately the "cheap, gate-tier" sibling of
  `scripts/per_hit_mitigation_forensics.py` (see the former's own docstring) — it was never
  designed to produce a wedge comparable to the Brewmaster doc's per-hit-forensics "unexplained
  factor" (×0.94, the number the "~+5%" prediction was extrapolated from). That number came from
  dividing out the model's **full** always-on chain (armor + vers + Defensive Stance + Indomitable
  + "BfI"), on exactly 2 of these 16 logs (Nexus-Point +12 and Windrunner +14). Testing the "~+5%"
  claim properly means running that heavier per-hit-forensics decomposition across the *whole*
  16-log corpus, not the coarse `measure_run_f` this fast-follow was scoped to wire in.
- **A properly-scoped follow-up would use `scripts/per_hit_mitigation_forensics.py`** (or a new
  corpus-wide driver built on it) to divide out the full modeled chain per hit across all 16 logs,
  producing a wedge figure directly comparable to the Brewmaster doc's ×0.94 and to Nexus-Point's
  own already-measured value. This is real, additional work (a new tool invocation pattern, likely
  its own script, its own validation round) — named here as the next step, not attempted in this
  session per the no-scope-creep instruction.
- A secondary, non-authoritative observation: across the 16 runs, F and raw delta show a moderate
  negative correlation (Pearson r ≈ −0.47, n=16) — runs with a lower (more-unexplained) F tend to
  over-predict more. This is in the direction the "wedge explains part of the gap" theory predicts,
  but n=16 with no significance test is not strong evidence on its own; it's a hint worth checking
  again once the properly-scoped per-hit-forensics pass exists, not a finding to act on.

## Caveats

- F's denominator uses the SAME K (3430) and the log's real per-hit live armor as the sim's own
  armor-DR formula — so the armor and versatility terms are (nearly) identical between "what F
  measures" and "what the sim predicts," which is exactly what makes the `sim × F = real · D`
  identity clean. The validator's caveat about per-hit vs static armor sourcing is a second-order
  effect on top of that, not a contradiction of it.
- The one out-of-band run (Pit of Saron +13[1], F=0.755) is a mild overshoot (0.056 past the
  ±0.05 band), not a AnonBrewmaster1-scale anomaly. No action taken on it — the F-consistency gate exists
  to flag exactly this kind of borderline case for human attention, not to auto-exclude it.
- This measurement is diagnostic-only by construction: it changes zero engine math, zero
  constants, and zero calibration_tier value. Everything printed by `simf calibrate-k`'s new
  F-layer section is reporting on numbers the sweep already computed.
- The Pearson correlation above is exploratory and explicitly flagged as such — do not cite it as
  confirmatory evidence on its own.

## Files changed

- `src/simf/cli.py` — `_load_measure_run_f` (dynamic loader, mirrors
  `tests/test_measure_run_f.py`'s own loading convention), F-measurement wiring in
  `_build_replay_entry` (local-log path only), `_report_f_layer` (F-consistency report; explicitly
  does NOT compute a `sim × F` residual, with the reasoning above inlined as a code comment for
  the next person tempted to add it back).
- `tests/test_cli_calibrate_k_f_layer.py` — new: F reported when per-hit armor data exists, `F=n/a`
  when it doesn't, the WCL branch never prints the F-layer section, and the dynamic loader resolves
  in this checkout.
- `src/simf/data/constants.yaml` — comment-only, two spots (`calibration.global_rmse` and
  `specs.protection_warrior.calibration_tier`): both now name the 2026-07-21 F-consistency result
  and explicitly correct the earlier "~+5%" anticipation to "neither confirmed nor refuted, needs a
  heavier tool." No values changed.
- `docs/validation/protwarrior_f_layer_measurement_2026_07_21.md` — this doc.
