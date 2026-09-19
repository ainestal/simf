# Phase B promotion measurement plan (2026-05-24)

Companion to `phase_b_synthetic_delta_2026_05_23.md`: that file
measured the shift, this one specifies the experiment that decides the
flip.

## 1. Hypothesis

Promoting `policy.lookahead_consumers.demo_shout_precast.enabled = true`
with a coupled `skill_tiers` recalibration compensating the raised
modifier=1.0 baseline lands global RMSE on the 18-Brutoh-log set within
**±0.005** of pre-promotion baseline at K=3430 (≤ 0.070, target 0.065).
±0.005 ≈ one σ of the F1-F14 noise floor (0.065 ± 0.003 across the last
five sweeps); smaller shifts are sampling noise.

## 2. Pre-conditions (all must hold)

- **PRs #54-57 ratified against a real new log.** The engine-batch
  ratification cap blocks downstream measurements until Brutoh's next
  +18 re-anchors the baseline.
- **`skill_tiers` recalibration drafted** with explicit per-tier deltas
  (§4). Without compensation, lookahead-ON raises the `in_the_zone`
  baseline and collapses the v0.11.9 ladder spread.
- **`constants_version` bumped** (21 → 22) in the toggle-flip commit;
  tripwire pins the new value.

## 3. Measurement protocol

- **Corpus**: 18 Brutoh logs in `examples/` (`WoWCombatLog-051026_*` →
  `WoWCombatLog-051926_132910.txt` — same set as
  `k_calibration_mastery_chain_2026_05_20.md`). AnonGuardian3 + archived
  excluded.
- **K**: canonical **K=3430**, no sweep — sweeping K and toggling Phase
  B together conflates variables.
- **Iterations**: 2000/log for go/no-go; 500/log for the recalibration
  sweep.
- **RMSE go/no-go**:
  - Pass: ≤ 0.066 (F11-F14 closure band).
  - Marginal: 0.066 < RMSE ≤ 0.072 → revisit deltas before promoting.
  - Fail: > 0.072 → revert; route to narrower-trigger redesign.
- **Per-dungeon ceiling**: no single dungeon residual worse by >2pp vs
  baseline. Algeth'ar is the worst existing outlier at −6.0%; landing it
  at −8.0% or worse fails regardless of global RMSE.

## 4. Skill-tiers recalibration sketch

Apply DR-stacking discipline. The 14-25pp synthetic drop is at
modifier=1.0 (press always fires). At lower tiers the consumer fires
only when the Bernoulli press-gate also fires, so
`δ_tier = m_tier × Δ_synthetic`:

| Tier | modifier | Lookahead δ |
|---|---|---|
| in_the_zone | 1.00 | 14-25pp |
| anticipating | 0.85 | 11.9-21.3pp |
| reading | 0.65 | 9.1-16.3pp |
| learning | 0.40 | 5.6-10pp |

For `learning`, "lower by the full synthetic delta" is 3-5× too
aggressive — the saved DR-stacking-arithmetic trap. Spread
`(in_the_zone − learning)` grows by `0.60 × Δ_synthetic ≈ 8-15pp` if
modifiers stay put.

Sketch deltas (sweep-validated):

- `in_the_zone`: 1.00 → unchanged (anchor).
- `anticipating`: 0.85 → 0.80.
- `reading`: 0.65 → 0.60.
- `learning`: 0.40 → 0.40 (floor; compressing collapses the bottom rung).

Bounding requires a 500-iter coordinate sweep over per-tier deltas in
{-0.10, -0.05, 0, +0.05} (3⁴ = 81 cells, cheap — constants are shared
per cell). If the sweep can't bound modifiers within one σ of the
pre-promotion spread, promotion defers to the narrower-trigger path
(threshold 600k or window 1.0s).

## 5. Rollback trigger

Constant: `policy.lookahead_consumers.demo_shout_precast.enabled`.
Auto-revert (PR revert + constants_version bump back) if any of:

- Global RMSE > 0.072 on the 2000-iter pass.
- Any dungeon residual worsens by > 2pp vs baseline.
- Ladder spread `(in_the_zone − learning)` at +18 falls below +25pp
  (v0.11.9 honesty-pass floor).

## 6. Sign-off

Validator mode A reviews the go/no-go execution log (RMSE,
per-dungeon residuals, ladder spread) before merge of the toggle-flip
+ `skill_tiers` recalibration PR. One objection blocks promotion and
routes work to the narrower-trigger alternative.
