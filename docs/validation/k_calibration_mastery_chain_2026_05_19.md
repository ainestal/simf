# K calibration — mastery-chain ship attempt + revert (2026-05-19)

Second attempt to ship the mastery-chain redesign (F11 + F7 retune + F13).
Primary-source verified against SimC `sc_warrior.cpp` midnight branch
(`docs/validation/simc_warrior_mastery_2026_05_19.md`). Empirical sweep
refuted the canonical-K fit; reverted constants. This document is the
trail for the next attempt — gate is F12 (shield-item block_value
sourcing).

## TL;DR

| Quantity | Pre-ship | Post-ship | Direction |
|---|---:|---:|---|
| RMSE at canonical K=3430 | ~0.066 | ~0.110 | **+67% worse** |
| Empirical best K | 3200 | **3000** | further from canonical |
| Canonical-vs-empirical K gap | 230 | **430** | widened |
| RMSE at empirical best | 0.062 | 0.065 | tied |
| Per-blockable-hit block mit (Brutoh) | 26.88% | 22.83% | **-4.05pp** |

The ship was reverted. Constants restored to pre-ship values:
- `mastery_block_value_scaling: 0.5` (was flipped to 0.0)
- `mastery_crit_block_scaling: 1.0` (was retuned to 1.5)
- `mastery_block_chance_scaling: 0.5` was added — removed in revert
- `character.py:base_block()` Prot Warr wiring reverted

`constants_version: 10` bump retained for cache invalidation.

## Sweep results (post-ship state)

```
.venv/bin/simf calibrate-k --logs-dir examples --k-min 3000 --k-max 4000 --k-step 100
```

18-replay corpus (17 prior + new Algeth'ar Academy +10).

```
K=3000: RMSE=0.065  ← best
K=3100: RMSE=0.071
K=3200: RMSE=0.081
K=3300: RMSE=0.092
K=3400: RMSE=0.104
K=3500: RMSE=0.116
K=3600: RMSE=0.129
K=3700: RMSE=0.142
K=3800: RMSE=0.153
K=3900: RMSE=0.165
K=4000: RMSE=0.179
```

Per-log residuals at K=3000 (mostly positive — sim under-mitigating):
```
-4.4%, -12.6%, +0.1%, +7.0%, +1.1%, -8.6%, +5.0%, +8.7%, +3.8%, +8.8%,
-2.7%, -0.9%, +4.7%, +10.5%, +5.2%, -5.4%, +5.7%, -6.4%
```

Per-log residuals at K=3400 (much more positive):
```
+3.6%, -7.9%, +3.6%, +16.3%, +8.4%, -2.1%, +13.6%, +14.3%, +10.3%, +16.9%,
+4.7%, +5.8%, +11.1%, +16.8%, +13.2%, -0.1%, +9.7%, -1.2%
```

## Why the math went the wrong way

My pre-sweep mental model was wrong. I computed the **expected** per-blockable-hit
mitigation assuming a flat 10% block chance and forgot that **Shield Block grants
100% block chance during its window** (~50% uptime in Brutoh's play). The dominant
effect is per-block VALUE, not per-block CHANCE:

With SB at 50% uptime:
- Effective block rate (pre-ship): 0.50 × 1.0 + 0.50 × 0.10 = **55%**
- Effective block rate (post-ship): 0.50 × 1.0 + 0.50 × 0.24 = **62%** (+7pp)

But block VALUE per block dropped from 0.44 (with mastery×0.5 fudge) to 0.30 (no
mastery contribution) — that's -14pp on every regular block, and the curve makes
crit blocks proportionally lower too. With block firing 55-62% of hits, the
per-block-value drop dominates.

Verified empirically in a single-call sanity check:
```
mastery_pct: 0.2808
base_block (post-ship): 0.2404      # was 0.10 pre-ship
block_value_pct (post-ship): 0.30   # was 0.44 pre-ship
critical_block_chance (post-ship): 0.4212   # was 0.2808 pre-ship
Effective block rate (SB 50% uptime): 0.6202
Expected per-hit block mit (post-ship): 0.2283
                      (pre-ship): 0.2688
                                  Delta: -0.0405
```

Net: the new model mitigates ~4pp **less** per blockable physical hit. The
calibration sweep responds by lowering K (adding armor DR) to compensate.

## Why the structural fix exposes the gap

SimC's `composite_block_value(s)` (sc_warrior.cpp:8910) reads:
```cpp
double bv = parse_player_effects_t::composite_block_value( s );
// ... Brace for Impact multiplier...
return bv;
```

`parse_player_effects_t::composite_block_value` parses **item-data block_value
contributions** (shield item) plus spell-data base. simf currently has only the
base (a flat `block_value_pct: 0.30` constant), no shield-item contribution.

The old `mastery_block_value_scaling: 0.5` was empirically load-bearing — it was
a Brutoh-fit fudge that happened to put block_value where SimC's full item-data
parser would have put it. Removing the fudge per primary-source verification of
mastery (mastery is chance-only — also true) exposed the unmodeled item
contribution. The two errors were canceling for Brutoh.

This mirrors the F1+F4 finding in v0.11.0 (K=2700 was absorbing missing DS
physical mitigation; landing both atomically was the structurally correct
move). The difference: F1+F4 had a known compensating fix that landed atomically.
F11+F7-retune+F13 has no compensating fix until F12 lands (shield-item
block_value sourcing), and no programmatic spell/item-data source exists in
simf today.

## Decision: revert

The 67% RMSE regression at canonical K is much larger than the 6% penalty the
v0.11.0 ship accepted for structural correctness. User trust (Brutoh sees
"RMSE 0.110") is materially impacted. Same call as the earlier-session F11
revert: structural correctness loses to empirical evidence until the
compensating fix is ready.

What stays:
- The `constants_version: 9 → 10` bump retained for cache invalidation.
- The full code change diff is documented here and in
  `docs/validation/simc_warrior_mastery_2026_05_19.md` so the next attempt
  doesn't re-litigate the SimC source reading.
- AUDIT.md F11/F12/F13 updated to reflect this attempt's outcome.

What reverts:
- `mastery_block_value_scaling: 0.5` (back from 0.0).
- `mastery_crit_block_scaling: 1.0` (back from 1.5).
- `mastery_block_chance_scaling` constant removed (was 0.5).
- `character.py:base_block()` Prot Warr wiring reverted.
- All test assertions restored to pre-ship state.

## Gate for the next F11+F7+F13 attempt

**F12 must land first.** Specifically: source the shield-item `block_value`
contribution from item data (SimC's `parse_player_effects_t::composite_block_value`
inputs), so simf's effective block VALUE matches reality without the
mastery×0.5 fudge.

Once F12 lands:
1. Re-apply the mastery-chain changes (mastery_block_value=0.0,
   mastery_block_chance=0.5, mastery_crit_block=1.5, character.py wiring).
2. Re-run the K sweep. Expectation: RMSE at canonical K=3430 returns to
   ≤0.07 because the item-data block_value now fills in what the
   mastery fudge was masking.

Until then, both axes stay reverted. Do not re-attempt without F12.

## Sources

- `docs/validation/simc_warrior_mastery_2026_05_19.md` — SimC code excerpts
- `docs/simc-reference/AUDIT.md` — F7/F11/F12/F13 entries (updated)
- Sweep log: `/tmp/mastery_chain_calibration.log`
- Prior failed attempt (this session): `docs/validation/k_calibration_f11_revert_2026_05_19.md`
- Pre-ship baseline: `docs/validation/k_calibration_f2f3_2026_05_19.md`
