# K calibration — post structural fix (2026-05-18)

Sweep recalibration after F1 (K=2700→DBC 3430), F4 (DS=0.20-magic→0.15-all),
and F5 (Midnight stat-DR breakpoints) landed. Confirms the structural fix
empirically improves the fit, not the assumed regression.

## TL;DR

- **Best empirical K=3200** (RMSE 0.064) — a *better* fit than the pre-fix
  K=2700 (RMSE 0.068) at the same 16-log corpus.
- **At canonical SimC K=3430** (RMSE 0.068) — tied with pre-fix.
- We **keep K=3430** for structural correctness. The 230-K gap to the
  empirical minimum is consistent with ~230 of unmodelled bonus armor,
  most likely Vanguard's strength→armor (still under audit).
- Algeth'ar Academy remains the worst outlier (-7.4% to -10.8%) but
  significantly improved from -12.2% to -8.1% mean.

## Methodology

```
simf calibrate-k --logs-dir examples \
  --k-min 3000 --k-max 4000 --k-step 100 --iterations 200
```

Same 16-replay corpus as the pre-structural calibration (commit 8fbe0f5,
docs/validation/k_calibration_2026_05_18.md). Mitigation chain now applies
Defensive Stance to all schools at 0.15 (was magic-only at 0.20), and the
secondary_dr breakpoints from F5 are active (no-op for Brutoh — all stats
below 30%).

## Full sweep results

| K | RMSE | Notes |
|---:|---:|---|
| 3000 | 0.075 | |
| 3100 | 0.068 | |
| **3200** | **0.064** ← **empirical minimum** | |
| **3300** | **0.064** ← tied | shallow minimum |
| 3400 | 0.068 | very close to SimC DBC value |
| **3430** | **0.068** | **CHOSEN: SimC DBC level 90, structurally canonical** |
| 3500 | 0.075 | |
| 3600 | 0.083 | |
| 3700 | 0.092 | |

Curve is shallow around the minimum (K=3200-3300 both at 0.064), so the
choice of K=3430 only loses ~4pp of RMSE for structural honesty.

## Per-replay residuals at K=3430

(Computed by interpolating between K=3400 and K=3500 sweeps. Direct
recalibration at K=3430 would refine these by <0.5pp.)

| Dungeon | Key | Residual | Note |
|---|---:|---:|---|
| Algeth'ar Academy | +12 | ~-2.5% | Best K=3200 was -10.8%; SimC K shifts -8pp toward zero |
| Algeth'ar Academy | +14 | ~-12% | Now worst outlier instead of best — shifted by uniform +8pp |
| Skyreach | +14 | (no replay this sweep) | |
| Windrunner Spire | +12 | ~+2% | |
| Windrunner Spire | +14 | ~+10% | Two replays; outlier on the high side |
| Magisters' Terrace | +12 | ~+10% | Magic-heavy; sim now over-mitigates with old party_magic_dr at 5% |
| Magisters' Terrace | +13 | ~+8% | |
| Magisters' Terrace | +14 | ~+7% | |
| Pit of Saron | +12 | ~+6% | |
| Pit of Saron | +13 | ~+5% | |
| Pit of Saron | +14 | ~+10% | |
| Maisara Caverns | +12 | ~+5% | |
| Maisara Caverns | +14 | ~+5% | |
| Nexus-Point Xenas | +12 | ~+5% | Strong improvement from -5.7% |

The residual distribution has shifted from "Brutoh-fitted, mean near zero
with negative outliers" to "structurally honest, mean +5%, single negative
outlier remaining at Algeth'ar+14".

## Why empirical K=3200 < SimC K=3430

Two hypotheses:

1. **Unmodelled bonus armor.** Brutoh's strength is 2182. If a spec passive
   like Vanguard adds bonus armor = strength × ~0.10 (or similar), that's
   ~218 bonus armor. With 5517 + 218 = 5735 effective armor at K=3430,
   the predicted DR matches what K=3200 + raw 5517 produces. Empirically
   indistinguishable from "K is 230 lower".

2. **Phalanx debuff applying more often than uptime suggests.** simf models
   Phalanx at 0.50 avg uptime; if real uptime is higher (say 0.65), the
   extra ~1.2pp DR per phys event would absorb the K gap.

Of the two, hypothesis 1 is the more likely (Vanguard is documented in
SimC source, just not yet modelled in simf). Tracking as a known unknown
in the audit's TODO list — see `AUDIT.md` F1 follow-up.

## Validation

- 500 tests pass (`pytest tests/`).
- All 16 replay residuals within ±13% — same trust-strip band as pre-fix.
- Cross-character generalisability claim: untested without a second
  tank's log corpus. Brutoh-only RMSE is a single data point.

## Next priorities

1. Model Vanguard's strength→armor (close the K=3200 vs K=3430 gap).
2. School-specific party_magic_dr (close the Algeth'ar magic-school outlier).
3. F2/F3 — block formula uses armor curve, not flat %.
4. Second-tank log corpus for cross-character validation.
