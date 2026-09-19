# Prot Warrior LOO-CV gate, Vanguard live — measured (2026-07-22)

**Status: measurement only. `calibration_tier` NOT changed by this doc —
pending explicit human ratification, per this project's established
twice-over precedent (Demo Shout fix, Guardian LOO-CV, the 2026-07-21
Shield Block ratification).** Closes the single open thread named in
`session_checkpoint_2026_07_21_vanguard_loo_cv_pending.md`: does the
ratified 16-log Prot Warrior corpus, with Vanguard live, actually pass the
LOO-CV gate — the 4th and last `characterized`→`calibrated` promotion
criterion — or was that just an untested assumption?

**Answer: it passes, and so do all four other criteria.** This is the
first time any spec has numerically cleared every promotion-bar gate at
once since the tier system was introduced 2026-07-06.

## What changed to make this runnable

Previously, LOO-CV only existed in `scripts/calibrate_spec_from_logs.py`,
whose own directory scan (`detect_party_roles` over a raw `--logs-dir`)
found a *different, smaller* Prot Warrior corpus (5 runs) than the ratified
manifest `simf calibrate-k` uses (16 runs) — a real, named, never-resolved
discrepancy from `docs/validation/phase4_tiered_calibration_loo_cv_2026_07_06.md`.
Rather than reconciling that discrepancy, this session took the checkpoint's
explicitly-sanctioned alternative: extended `calibrate-k` itself with the
gate (PR #416), reusing the exact same `_run_loo_cv` implementation
(dynamic file-path loader, mirrors the existing `_load_measure_run_f`
pattern) as pure post-processing of the sweep table `calibrate-k` already
builds — zero new simulation runs, and the gate now always runs against
whichever corpus `calibrate-k` itself resolved (the ratified manifest by
default). Also fixed a stale warning inside the reused function that still
claimed the warrior-replay Demo Shout/Phalanx double-count was unfixed (it
was fixed 2026-07-18, PR #387) — leaving it would have printed an actively
wrong claim on every future run of this exact gate, warrior or otherwise.

## Command

```
simf calibrate-k --k-min 2000 --k-max 5000 --k-step 250 --iterations 100 --seed 42
```

Default character/log-target/logs-dir (ratified manifest auto-resolves,
16 replays). K grid: `{2000, 2250, ..., 5000} ∪ {3430}` = 14 points — the
same grid convention `calibrate_spec_from_logs.py` established, now unioned
in automatically by `calibrate-k` regardless of whether `--k-min`/`--k-max`
happen to land on 3430. `--iterations 100` per the established "byte-identical
to 300, 3× cheaper" standard gate budget (first confirmed on Guardian's
corpus, 2026-07-08).

## Results

### Sweep (canonical K=3430, current ratified engine state — Vanguard + all
2026-07-21 Shield Block fixes live)

| K | RMSE |
|---|---|
| 2000 | 0.289 |
| 2500 | 0.183 |
| 3000 | 0.095 |
| **3250** | **0.070 ← empirical best** |
| **3430 (canonical)** | **0.073** |
| 3500 | 0.079 |
| 4000 | 0.161 |
| 5000 | 0.383 |

Per-run deltas at canonical K=3430 (identical to the pinned single-K run
`protwarrior_vanguard_strength_armor_2026_07_21.md` already reported —
reproduces exactly, confirming this sweep didn't accidentally pick up a
different engine state):

```
[-12.2%, -10.0%, -8.5%, +10.4%, +10.0%, +10.8%, +3.0%, +8.1%, +4.0%, +0.6%,
 -1.6%, +5.9%, +6.1%, +0.2%, +4.9%, -3.6%]
```

RMSE=0.073 (0.065 excluding 2 partial/truncated runs, n=14), mean signed
bias +1.8%, **16/16 (100%) within ±15%**.

F-layer diagnostic (measurement-only): median F=0.699, 15/16 within ±0.05
of the median (1 out-of-band: `WoWCombatLog-051026_105836.txt[1]`,
F=0.755) — essentially unchanged from 2026-07-21's reading, as expected
(Vanguard doesn't touch this ratio; see the Vanguard doc).

Empirical best K (3250) sits 180 away from the canonical anchor (3430) —
narrower than the pre-2026-07-18 gap but still real. K stays 3430 on the
same "methodology, not proximity" grounds `docs/calibration.md` already
states — this is a confidence check on the anchor, not a re-fit.

### LOO-CV gate

| Check | Result | Bar | Verdict |
|---|---|---|---|
| K stability | max\|bestK_-i − bestK_full=3250\| = 180 | ≤300 | **PASS** |
| Held-out prediction | 16/16 (100%) within ±15% | ≥75% | **PASS** |
| Any fold beyond ±25%? | No (worst fold −14.9%) | — | **PASS** |

**LOO-CV GATE: PASS.** Every held-out fold's blind-refit K landed on either
3250 or 3430 (never anything else) and every single one predicted its own
held-out run within ±15% — the tightest LOO-CV result this project has
measured for any spec so far (Guardian's own two attempts landed at
71%/69%, both FAIL). Full per-fold table lives in the raw run log; not
reproduced here since every fold cleared the bar with room to spare (worst
case −14.9%, 0.1pp inside the bound).

## Promotion-bar check (`docs/calibration.md`, `characterized`→`calibrated`)

| Gate | Bar | Measured | Clears? |
|---|---|---|---|
| F-consistent runs | ≥8 | 15/16 | ✓ |
| \|mean signed bias\| | ≤5% | +1.8% | ✓ |
| Within ±15% | ≥75% | 16/16 = 100% | ✓ |
| RMSE | ≤0.15 | 0.073 | ✓ |
| LOO-CV gate | PASS | PASS | ✓ |

**All five sub-criteria (four named gates, LOO-CV itself being one) clear
numerically.** This is the first spec, at any point since the 2026-07-06
tier system existed, to clear every criterion simultaneously — Warrior and
Guardian's original `calibrated` awards (2026-05-18 and 2026-06-29
respectively) both predate the LOO-CV gate's existence and were never
checked against it retroactively (Guardian's retroactive check, run later,
FAILED — see `phase4_guardian_loo_cv_f_consistent_2026_07_08.md`).

## What this doc is NOT claiming

- **Not a re-promotion.** `constants.yaml`'s `protection_warrior.calibration_tier`
  stays `characterized`, unedited by this doc. The decision is the user's —
  see the session checkpoint this doc closes out.
- **Not contamination-free.** The LOO-CV gate's own printed banner (now
  corrected for the Demo Shout fix, see above) still names a real, open,
  cross-spec ~5-7% run-scoped wedge
  (`phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`) that this
  measurement does not account for or exclude.
- **Not the whole residual.** The separate ~11% "clean wedge"
  (`scripts/full_chain_wedge.py`, ~81%-magic-weighted) named in the Vanguard
  doc and the session checkpoint is untouched by anything measured here —
  Vanguard is a physical-only armor term and structurally cannot move a
  magic-dominated residual.
- **Still a WEAK CV.** Only K is refit per fold; the spec's other
  mitigation-ledger constants (Vanguard's own 0.70 coefficient included)
  are never refit. A floor, not a proof — same standing caveat every prior
  LOO-CV doc has carried.

## Recommendation

Every number a `characterized`→`calibrated` promotion needs is now on the
table, measured (not assumed) against the current, fully-Vanguard'd engine
state. Whether that's enough to ratify — given the still-open cross-spec
wedge and magic-side residual named above — is the human call this doc
exists to inform, not settle.
