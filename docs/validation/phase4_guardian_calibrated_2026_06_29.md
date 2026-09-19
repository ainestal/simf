# Guardian Druid → `calibrated: true` (2026-06-29)

Records the decision and evidence for flipping `specs.guardian_druid.calibrated`
from `false` to `true`. Caps the Guardian calibration arc (#199–#218).

## The bar: single-player parity, not cross-player ±15%

`calibrated: true` is **not** "every real player within ±15%" — that conflates
model error with execution/build variance and is unachievable for an optimal-play
sim. The precedent is Prot Warrior: **aggregate RMSE 0.068 across 16 logs of ONE
player (Brutoh)** — single-player, many-fights. Guardian is held to the same bar:
**AnonGuardian1, 16 timed local logs, aggregate fit.**

The 11-log cross-player WCL set is the **generalization check** (it scatters wider
because of build variance), not the pass/fail gate.

## The evidence (AnonGuardian1 16 logs, post-all-fixes)

| Metric | Guardian (AnonGuardian1 16) | Warrior bar |
|---|---|---|
| Within ±15% | **12/16** | — |
| Mean bias | **+0.2% (unbiased)** | ~0 |
| Mean \|Δ\| | **9.9%** | — |
| Aggregate RMSE | **0.119** | 0.068 |

The model's **center is correct** (unbiased). The 3 outliers (+16.6 / +23.4 /
+23.1) are all positive and magic-heavy — the documented **magic residual**, not
bias.

## Two bugs the calibration push surfaced and closed (PR #218)

The flip was *not* possible until these were fixed — and they were caught
precisely by validating on the single-player corpus:

1. **Phantom Rage of the Sleeper.** The engine credited a ~3.3% all-school baseline
   DR for an ability **removed from the game in patch 12.0.0** (verified vs SimC +
   Warcraft Wiki). Removing it made the model unbiased (the phantom was masking
   over-prediction).
2. **#214 haste-elasticity coupling regression.** The 2026-06-28 secondary-conversion
   recal (haste 100→44 rating/pct) re-expressed haste in real units but left the
   Ironfur haste-elasticity `ref_haste_pct=0.121` (old units) stale, so every
   Elune's-Chosen Guardian over-stacked Ironfur (4.87 vs live ~2.45) → ~20%
   over-mitigation on the **live path**. Fixed by re-expressing the anchor
   (0.121 × 100/44 = 0.275). This alone moved AnonGuardian1 from 6/16 back to 12/16.

## Caveats (real, named, surfaced in-UI — decoupled from the flag)

`calibrated: true` means "trust the absolute numbers," not "every lever is equally
tight." The UI renders these for a loaded Guardian regardless of the flag
(`_SPEC_MODELING_CAVEAT`, decoupled from `_uncalibrated_spec_warning`):

- **Single-build (Elune's-Chosen) un-regressed haste slope** — `k=0.75` is a
  single-profile fit (AnonGuardian1's haste is gear-locked). A Druid-of-the-Claw or
  different-haste build would regress it.
- **Medium-confidence mastery→healing** conversion (single mastery point).
- **Small magic residual** — slightly over-predicts magic-heavy content (the 3
  AnonGuardian1 outliers; the cross-player Nexus-Point fights). Likely reactive-CD timing /
  Ursol's-Warding magnitude. The biggest open refinement (see below).
- **RMSE 0.119 > Warrior's 0.068** — Guardian is an intrinsically spikier spec (no
  parry, heavy magic exposure) on top of the magic residual.

## What did NOT gate the flip (and why)

- **Cross-player WCL scatter** (8/11 → ~6/11 within ±15% across the fix sequence):
  generalization/build variance, not single-player model error. Documented, not a
  blocker (Warrior was also single-build).
- **"±15% on all real players":** unachievable by an optimal-play sim; not Warrior's
  bar either.

## Follow-ups (post-calibration, non-blocking)

1. ~~**Close the magic residual** — the 3 positive outliers. Confirm reactive-CD
   timing vs an Ursol's-Warding magnitude tweak; re-run AnonGuardian1 + the WCL set.~~
   **RESOLVED 2026-06-30 (SimC source audit, docs-only outcome).** ~30% is a real
   omitted mechanic (Glistening Fur effect #13, −3% non-arcane magic, ~2.6pp of the
   shadow gap); ~70% is reactive-CD-timing execution (not a constant). Ursol's
   Warding is `NYI` in SimC — its value is spell-data-derived, not SimC-sourced, so
   it is NOT the lever. Decision (user): document the gap + correct the provenance,
   no engine change. Full record: `phase4_guardian_magic_mit_2026_06_29.md`
   (2026-06-30 section).
2. **Audit #214 for other conversion-coupled constants** (the haste-ref bug pattern).
3. **A 2nd Guardian build** (DotC / different haste) to regress the slope and tighten
   RMSE toward Warrior's — promotes from single-build-calibrated to multi-build.
