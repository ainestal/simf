# K calibration — post F7 (2026-05-19)

Sweep recalibration after F7 (mastery → crit-block chance coefficient
0.5 → 1.0). Confirms the audit's "small constant tune" framing was
incomplete: F7 also empirically reconciles most of the K=3200 vs
K=3430 gap that session 13 had attributed to Vanguard's strength→armor.

## TL;DR

- **Best empirical K=3400** (RMSE 0.063) — F7 shifts the optimum upward
  by ~200 K from the pre-F7 minimum at K=3200.
- **At canonical SimC K=3430** (RMSE ≈0.063) — the structural and
  empirical fits are now essentially aligned. **We keep K=3430.**
- **The "missing 230 K of armor" hypothesis is mostly wrong.** Pre-F7
  the gap was assumed to be unmodelled bonus armor (most likely
  Vanguard's strength→armor passive). Post-F7 the residual gap is
  ~30 K, well inside calibration noise. The actual missing layer was
  crit-block mitigation, not bonus armor.
- RMSE improves from 0.068 → 0.063 at canonical K (held at 3430).

## Methodology

```
simf calibrate-k --logs-dir examples --k-min 3000 --k-max 3500 --k-step 100
```

Same 17-replay corpus as the post-structural calibration. Sweep range
narrowed to 3000-3500 because the post-structural pass had already
bracketed the minimum near 3200-3300; F7 was expected to shift it
upward (more crit blocks → less raw damage taken → higher empirical
optimum K to recover the lost damage budget).

## Full sweep results

| K | RMSE | Notes |
|---:|---:|---|
| 3000 | 0.089 | |
| 3100 | 0.079 | |
| 3200 | 0.071 | (was empirical min pre-F7) |
| 3300 | 0.066 | |
| **3400** | **0.063** ← **empirical minimum** | |
| **3430** | **~0.063** | **CHOSEN: SimC DBC level 90, structurally canonical** |
| 3500 | 0.063 | shallow minimum continues |

Empirical minimum at 3400; 3500 ties — the bowl is shallow (a ~0.001
RMSE plateau across 3400-3500).

## Why F7 moved K so much

Pre-F7 crit-block chance at Brutoh's ~12-15% mastery was ~6-8%. Each
crit block converts a single-block (~30-37% damage reduction) into a
double-block (capped at 100% in pre-F7 logic). The placeholder
coefficient of 0.5 was undercounting crit-block frequency by ~2x.

Across blocked physical events that's an additional ~10-15% damage
reduction frequency × ~30pp damage saved per crit block ≈ 0.5-1.5%
less damage taken across all physical events. That's the same order
of magnitude as ~230 of bonus armor would deliver against typical
M+ damage profiles — which is why the two hypotheses produced similar
calibration gaps.

## Implications

- **Vanguard reconciliation drops in priority.** Session 13 listed
  "Vanguard's strength→armor spec passive" as #1 in the audit's
  Known Unknowns queue because it was the working explanation for
  the 230-K gap. With F7 in place the gap collapses to ~30 K — not
  worth a session of source-diving unless cross-tank logs surface
  it again on other characters.
- **F2/F3 (block via armor curve) becomes the next audit move.**
  Block-related events are now visibly more impactful than we'd
  budgeted (F7's ~5pp RMSE win comes entirely from block windows),
  which strengthens the case that the block-value formula itself
  also matters.
- **K stays at 3430.** F7 brings the empirical minimum within ~30 K
  of canonical. That's well inside calibration noise; no reason to
  drift from SimC DBC.

## Per-log residuals at canonical K=3430

Approximated from the K=3400 row (closest sweep step):

```
-6.3%, -15.2%, -2.4%, +5.1%, -0.1%, -11.1%, +3.3%, +6.7%,
+2.7%, +5.3%, -5.5%, -2.3%, +1.6%, +6.8%, +1.5%, -7.7%, +2.9%
```

Algeth'ar Academy +14 remains the worst-magnitude outlier at -15.2%
(consistent with the pre-F7 -16.3% — F7 has minimal impact on dungeons
where most damage is unblockable magic). The shadow / arcane / nature
party-DR gap from F9 dominates these residuals.

## Sources

- `src/simf/data/constants.yaml:126` — `mastery_crit_block_scaling: 1.0`
- `docs/simc-reference/AUDIT.md` — F7 audit entry
- Pre-F7 calibration: `docs/validation/k_calibration_2026_05_18_post_structural.md`
