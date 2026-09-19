# F11 investigation — hypothesis refuted, revert (2026-05-19)

Investigation pass on AUDIT.md F11: whether `mastery_block_value_scaling: 0.5`
is wrong (modern Prot Warr Critical Block mastery being CHANCE-only post-DF
rework). The audit predicted that flipping the coefficient to 0.0 would shift
empirical K best-fit toward canonical 3430. Empirical sweep refuted the
prediction; the constant is reverted to 0.5.

## TL;DR

- **Tested:** `mastery_block_value_scaling: 0.5 → 0.0` against the 17-replay
  Brutoh corpus, K-sweep 3200–3500 step 100.
- **Predicted:** best-K shifts UP toward canonical 3430; RMSE at 3430
  improves from ~0.066 → ~0.063.
- **Actual:**
  - RMSE at canonical K=3430 regressed from ~0.066 → ~0.135 (~+105%).
  - Best-K stayed at 3200 (RMSE 0.106 — up from 0.062 pre-flip).
  - 14 of 17 per-log residuals went positive (sim systematically
    under-mitigating).
- **Reverted** `mastery_block_value_scaling` to 0.5.
- **Kept** `constants_version: 9` bump — test surface changed; cache
  invalidation still warranted.

## Sweep result (post-flip, mastery_block_value_scaling = 0.0)

```
K=  3200: RMSE=0.106  ← best
K=  3300: RMSE=0.121
K=  3400: RMSE=0.135
K=  3500: RMSE=0.148
```

Per-log residuals at K=3200:
```
+3.2%, -7.8%, +3.7%, +15.7%, +8.1%, -1.8%, +13.3%, +14.3%, +10.0%,
+16.8%, +5.0%, +5.3%, +11.5%, +16.9%, +13.3%, +0.0%, +9.8%
```

Compare to pre-flip (from `k_calibration_f2f3_2026_05_19.md`, K=3200
RMSE 0.062): residuals were mixed, distribution roughly symmetric around
zero. Post-flip residuals are visibly biased positive — the sim is now
predicting more damage taken than the logs actually show, consistent
with removed mitigation.

## Why the audit's K-direction was wrong

The audit reasoned: "less block mitigation → empirical K shifts UP toward
canonical (3430), closing the canonical-vs-empirical gap."

The math is the other way. `armor_dr = armor / (armor + K)`. Higher K
gives LOWER DR for a fixed armor pool — less armor mitigation, not more.
If the model now under-mitigates (block contribution removed), the
re-fit move is MORE armor DR, which means LOWER K. That's what the
data showed: best-K stayed at the bottom of the sweep range (3200),
not the top.

The audit's directional prediction in `k_calibration_f2f3_2026_05_19.md`
("K best-fit would shift UP toward canonical") was mechanically inverted.
The hypothesis itself (mastery chance-only) wasn't empirically tested in
the audit — it was a soft "per Wowhead / Icy Veins" reference paired
with an explicit "test path: read spell data for spell 76857" that was
never executed before F11 was promoted to priority #1.

## What this empirically means

Two competing readings:

1. **The 0.5 coefficient is approximately right.** Modern Prot Warr
   Critical Block mastery DOES contribute to block value in Midnight
   12.0.5, contrary to the Wowhead-summary reading. The DF mastery
   rework removed the chance-side ghost (F7) but kept value scaling.
2. **The 0.5 coefficient is masking an unmodelled mitigation source.**
   Real mastery is chance-only; the 0.5 was a happy accident
   compensating for missing Vanguard strength→armor, school-specific
   party DR, Stance Mastery chunk DR, or another passive. Removing
   it surfaced the underlying gap.

Without primary-source spell-data verification, (1) is the parsimonious
read. (2) is plausible — the canonical-vs-empirical 230 K gap has been
the unresolved residual through three audit cycles — but it requires
both proving the mastery hypothesis AND locating the compensating
mechanism. Two unknowns, one constraint.

## Decision

- **Revert** `mastery_block_value_scaling` to 0.5.
- **Keep** `constants_version: 9` bump (cache invalidation: test surface
  changed; downstream consumers should re-derive).
- **Do not re-attempt F11** until primary-source spell-data verification
  resolves the mastery effect type (`BLOCK_VALUE` vs `BLOCK_CHANCE`).
- **Document the trail** so this exact investigation isn't relitigated.

## Gate for any future F11 attempt

1. WebFetch / Wowhead API on the current Critical Block mastery spell
   (id 76857 historically; verify current id). Read effect 2/3 type.
2. If spell modifies `BLOCK_VALUE`: F11 is wrong, 0.5 stays. Possibly
   tune the coefficient up/down to match empirical residuals more
   tightly, but keep the structural contribution.
3. If spell modifies `BLOCK_CHANCE` only: F11 is right structurally,
   AND we've localised an unmodelled mitigation source. Re-open
   Vanguard / school-DR / Stance Mastery as the next investigation;
   queue the compensating fix BEFORE re-applying F11.

## Sources

- Calibration sweep: `simf calibrate-k --logs-dir examples --k-min 3200 --k-max 3500 --k-step 100`
- Prior empirical baseline: `docs/validation/k_calibration_f2f3_2026_05_19.md`
- Hypothesis source: `docs/simc-reference/AUDIT.md` F11 (status: INVESTIGATED, REFUTED).
- Code touched: `src/simf/data/constants.yaml`, `tests/test_block_armor_curve.py`.
