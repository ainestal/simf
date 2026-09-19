# K calibration — post F2+F3 (2026-05-19)

Sweep recalibration after F3 (crit-block via armor curve, multiplier=2.0,
capped at 0.85) + F2 structural prep (block value routed through
`calculate_armor_resist`, regular-block output preserved by inverting
the curve).

## TL;DR

- **Best empirical K=3200** (RMSE 0.062) — F2+F3 unwinds F7's K shift
  back to the pre-F7 baseline. F7 raised crit-block *frequency* by
  ~2x; F2+F3 lowers crit-block *magnitude* (sublinear armor curve
  instead of linear ×2). For Brutoh's gear the two cancel net-net.
- **At canonical SimC K=3430**, RMSE is ~0.066 — a slight regression
  from the post-F7 RMSE of 0.063, but still better than the pre-fix
  RMSE of 0.068.
- **The 230 K canonical-vs-empirical gap is back, and is no longer
  attributable to Vanguard's strength→armor passive** (F7 closed that
  hypothesis). New unknown: see Implications below.
- **K stays at 3430** for structural correctness (SimC DBC, level 90).

## Methodology

```
simf calibrate-k --logs-dir examples --k-min 3000 --k-max 3500 --k-step 100
```

Same 17-replay corpus as the F7 calibration. Runtime on the Pi
~45 minutes (vs ~17 minutes for F7's sweep) — F2+F3 added a function
call (`calculate_armor_resist`) to the hot path for every blockable
physical event and every armor application. Inlining is a follow-up
perf item; correctness took priority.

## Full sweep results

| K | RMSE | Notes |
|---:|---:|---|
| 3000 | 0.074 | |
| 3100 | 0.067 | |
| **3200** | **0.062** ← **empirical minimum** | |
| 3300 | 0.063 | |
| 3400 | 0.066 | (was empirical min post-F7) |
| **3430** | **~0.066** | **CHOSEN: SimC DBC level 90, structurally canonical** |
| 3500 | 0.073 | |

## Why F2+F3 unwound the F7 K shift

The pre-fix block code had two compensating errors:

1. **Too few crit blocks** — `mastery_crit_block_scaling = 0.5`,
   meaning at 15% mastery only ~7.5% of blocks were crit. SimC says
   1:1, so should be ~15% of blocks.
2. **Each crit block was overcounted** — `block_value * 2` clamped
   at 1.0, vs SimC's `calculate_armor_resist(BV, K, 2.0)` clamped at
   0.85. At Brutoh's BV ~36%, the bug computed crit-block DR as 72%
   instead of the correct ~53%.

These errors were near-cancelling on Brutoh's DTPS:

- F7 alone fixed #1 → ~2x more crit blocks, each still overcounted →
  too much total mitigation → K best-fit shifted UP to 3400 to
  compensate (less armor needed because crit blocks were doing extra work).
- F2+F3 then fixed #2 → crit blocks now mitigate ~19pp less each →
  total mitigation drops back to its pre-F7 level → K best-fit shifts
  back DOWN to 3200.

The net is structurally correct on both axes (frequency AND magnitude)
but empirically equivalent to the pre-F7 state for Brutoh.

## Implications

- **The 230 K canonical-vs-empirical gap is reopened**, but no longer
  fits the Vanguard hypothesis (F7's analysis showed crit-block
  frequency closed it; F2+F3 shows crit-block magnitude reopened
  it). It's not a missing armor source.
- **Candidate explanations for the residual gap:**
  1. F11 — `mastery_block_value_scaling: 0.5` may be wrong. If modern
     Critical Block mastery doesn't grant block value (post-DF rework
     per Wowhead / Icy Veins), then `block_value_pct` is overcounted
     at Brutoh's mastery by ~7.5pp. Removing it would reduce regular
     block DR by ~5pp and crit-block DR by ~8pp — net less mitigation
     → empirical K would shift UP again toward canonical.
  2. F12 — block_value should be sourced from spell data in armor-equivalent
     units, not inverted from the legacy `_pct` formula.
  3. Per-school party DR (F9 deferred) on magic-heavy logs.
- **Algeth'ar Academy remains the worst outlier** at -13.0% (consistent
  through every fix in this session). Mostly magic damage → not affected
  by block mechanics.
- **F11 is the highest-leverage next move.** A single Wowhead/spell-data
  check could resolve it. Modeled impact would shift K best-fit by ~100,
  closing about half the canonical-vs-empirical gap.

## Per-log residuals at canonical K=3430

Interpolating from K=3400 (closest sweep step):

```
-3.4%, -13.0%, -0.8%, +8.3%, +2.2%, -9.5%, +6.4%, +8.5%,
+5.3%, +9.0%, -2.6%, +0.5%, +4.3%, +9.1%, +4.6%, -5.1%, +4.8%
```

Distribution: 11 of 17 residuals are within ±5%. Worst-magnitude
outlier is Algeth'ar Academy +14 at -13.0% (consistent across the
session). Positive residuals (sim under-mitigating) dominate the
mid-range — consistent with the F11 hypothesis above.

## Sources

- `src/simf/core/mitigation.py:8-21` — `calculate_armor_resist`
- `src/simf/core/character.py:223-245` — `block_value_rating()`
- `docs/simc-reference/AUDIT.md` — F2, F3, F11, F12 entries
- Prior calibrations:
  - `docs/validation/k_calibration_f7_2026_05_19.md`
  - `docs/validation/k_calibration_2026_05_18_post_structural.md`
