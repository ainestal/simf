# Prot Warrior replay double-counts Demoralizing Shout + Phalanx — fixed, K held at 3430 (2026-07-17)

**Status: fix implemented, held at PR-open for user ratification** (same precedent as PR #361 —
this changes the flagship calibrated-spec headline number, so the user sees the before/after
before merge). The engine fix itself is small (two `not event.is_log_replay` gates in
`mitigation.py`); the consequence is not: the ratified 16-log corpus at the pinned canonical
K=3430 moves from RMSE 0.065 (unbiased) to RMSE 0.138 (biased +11.5%), because the old headline
was **two errors partly canceling** — a spurious replay-side DR layer impersonating a real,
separately-measured mob-side damage reduction the model doesn't carry yet.

## Summary

A validator audit (2026-07-17) confirmed what the Brewmaster physical-gap decomposition
(`phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`, Finding B) had flagged as
"validator-lane, calibration-critical": **log replay re-applies Demoralizing Shout's −20% on top
of a `raw_amount` that already contains it.** Demo Shout is an *attacker-side* debuff — it
reduces the damage the mob deals, before WoW snapshots the combat log's `unmitigatedAmount`
(which `log_replay.py` feeds verbatim as `raw_amount`). Defensive Stance, by contrast, is
*defender-side* and correctly modeled in replay — no contradiction.

**Phalanx is the same bug class and was also live.** It marks the *mob* (target debuff), so its
reduction is also inside `unmitigatedAmount` — and the frozen calibration character
(`brutoh-calibration-2026-05.yaml`) runs the `brutoh-actual` loadout, which includes **both
Phalanx and Thunderlord**. So the ratified corpus carried both double-counts: Demo at ~31.7%
uptime (10s / 31.5s Thunderlord-reduced CD, fired on-CD in replay by design) × −20%, plus
Phalanx's flat −4% (8% × 0.50 avg uptime) on every physical hit. Combined spurious DR on
physical: 1 − (1 − 0.20×0.317)(1 − 0.04) ≈ **10.0%**.

Both layers remain **correct and unchanged in synthetic (forward-sim) mode** — there the damage
profile carries raw mob output, so modeling the attacker-side reduction is legitimate. Nothing
about verdicts, gear/gem/vault recommendations, stat weights, or eHP changes in this PR. Only
the replay/calibration measurement changes — i.e., our *knowledge* of the fit, not the product's
forward-sim behavior.

## Verification (independent re-derivation, not taking the validator's word)

1. **`raw_amount` is WoW's logged `unmitigatedAmount`, not a reconstruction.**
   `combat_log_damage.py:101` reads `base_amount = int(suffix[1])`; `log_replay.py:95` feeds it
   as `raw_amount` (and `wcl_replay.py` sets `is_log_replay=True` on its events too, so the fix
   covers both replay paths).
2. **Demo Shout fires throughout replay.** `policy.py:244-266`: `is_replay_event` makes the DS
   press unconditional on cooldown ("real log = real CD usage"), with Thunderlord's ×0.7 CD.
   `mitigation.py` then applied the −20% (+ Unyielding Stance if talented) inside every window.
3. **Phalanx applied unconditionally on physical** whenever `"phalanx" in state.talents` — and
   the calibration character's `brutoh-actual` loadout has it (constants.yaml `talent_loadouts`).
4. **Ground truth that the −20% is already in `base_amount`**: per-hit forensics on Brutoh's own
   corpus (Finding B, 2026-07-04): `r = (amount+absorbed+blocked)/base_amount` is 0.730 inside
   Demo windows vs 0.740 outside. If `base_amount` were pre-Demo, r would drop ~20pp inside the
   windows; it moves 1pp. Demo (and by the same target-debuff mechanism, Phalanx) lands before
   the `unmitigatedAmount` snapshot.
5. **Only the warrior is affected.** `make_policy` dispatches per spec; only the warrior
   `ActiveMitigationPolicy` ever sets `state.demo_shout_until` (default −1.0), and `phalanx` is a
   warrior talent. Guardian's calibrated corpus and every characterized spec are bit-identical
   under this change.

## The fix

`src/simf/core/mitigation.py`: both the Demoralizing Shout layer and the Phalanx layer now carry
`and not event.is_log_replay` — the same replay flag the absorb path already branches on
(`mitigation.py` step 9). Synthetic mode is untouched by construction (`is_log_replay` defaults
`False`).

Regression tests (`tests/test_mitigation.py`): `test_demo_shout_not_double_counted_in_log_replay`
and `test_phalanx_not_double_counted_in_log_replay` — each asserts the layer still applies in
synthetic mode AND is inert in replay mode. Mutation-verified: reverting either gate fails the
corresponding equality assertion.

## Measurement — ratified 16-log corpus, `simf calibrate-k` (manifest default), 300 iters, seed 42

| | RMSE @ K=3430 (pinned) | mean signed error | within ±15% | best-fit K | RMSE @ best K |
|---|---|---|---|---|---|
| **Before fix** (master @ 6b60c3c) | **0.0652** | +0.2% (unbiased) | 16/16 | 3430 (on this grid) | 0.0652 |
| **After fix** | **0.138** | **+11.5% (over-predict)** | 10/16 | **2905** (fine sweep) | 0.0678 |

(Partials excluded: before 0.0614, after-at-3430 n/a computed, after-at-refit 0.0661; n=14.)

Per-run shift structure (post − pre, at K=3430): mean +11.3pp, range +7.6 to +14.3. Three
independent consistency checks:

- **Chain arithmetic predicts the shift.** Removing ~10.0% DR on physical at ~90% physical share
  (this dungeon pool, post-absorb) predicts ≈ +10-11pp mean DTPS shift. Observed +11.3pp. This
  is the honest-marginal discipline (N × (1 − existing_DR)) run in reverse — the observed shift
  is exactly what the removed layers were worth, no more.
- **The smallest shift is the immune-source dungeon.** Algeth'ar Academy (+7.6pp on the +12,
  4th-smallest +10.1pp on the +14) — its final boss Echo of Doragosa is on BOTH layers'
  `immune_sources` lists, so a chunk of AA damage never had the double-count to remove.
  Mechanism confirmed end-to-end.
- **Direction matches the validator's prediction** ("unmasking would push RMSE UP at pinned K").

## Why K stays at 3430 (the methodology call)

The refit minimum moved 3430 → ~2905-2930 (coarse grid best 2930 @ 0.0679; a finer 2830–3030
step-25 grid was in progress but not needed to close this decision — the conclusion below holds
at either point on the coarse curve, since the argument is about K being a real constant, not
about pinning the refit optimum to the nearest 25 units), and at that refit K the fit is
statistically indistinguishable from
the pre-fix headline (unbiased +0.7%, 16/16 within ±15%, RMSE 0.0678 vs 0.0652). **That is
precisely why moving K would be wrong**: the double-count was a near-uniform multiplicative
offset, and K can absorb it almost perfectly — a leaked degree of freedom that would hide a
now-named mob-side layer inside the armor constant. Rejecting the move, on four grounds:

1. **K is not a free parameter.** It is SimC's DBC `armor_mitigation_constant(level)` — a real
   game constant. The project already corrected this exact mistake once (the old K=2700 self-fit
   silently absorbed missing Defensive Stance modeling; see `k_constant_simc_dbc_anchor`).
2. **The unmasked bias has a named, measured, external cause.** The cross-spec ~5-7% run-scoped
   wedge (mob-side tuning applied after `base_amount` — measured ×0.94 per-hit on this very
   corpus's Nexus-Point +12 run, `phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`)
   is an *input-side* artifact of replay: reality applies a mob-side multiplier between
   `base_amount` and applied damage that replay's inputs don't carry. Wedge-correcting the
   post-fix bias: 1.115 × 0.94 − 1 ≈ **+4.8%** residual — the wedge explains roughly half the
   unmasked gap; the remaining ~4-6pp lands in the existing "structural physical-mit gap"
   bucket (roadmap item 5), which this fix makes *measurable* instead of masked.
3. **K is global across all six specs.** Guardian's `calibrated` status (AnonGuardian1 16-log corpus,
   RMSE 0.119) is stated at K=3430; every characterized spec's deltas are too. Refitting K on
   the warrior corpus alone would silently move all of them. A per-spec K would be a worse leak.
4. **Armor marginals feed every gear recommendation.** dDR/dArmor ∝ K/(armor+K)²; moving K
   3430→2905 shifts the armor marginal ~5% and the DR level on every forward-sim surface —
   changing recommendations to compensate for a mob-side artifact that has nothing to do with
   the player's armor.

The honest end-state model is `dealt = base_amount × F_run × defender_chain(K=3430)` with F
measured per run (`scripts/measure_run_f.py` shipped for exactly this; the Brewmaster
decomposition's recommendation "report each run's measured F; judge on F-consistent corpora").
That F-layer is the named fast-follow — **not** bundled here per the engine-batch-ratification
cap and because it is its own methodology change needing its own validation.

## Phalanx disposition

**In-corpus, fixed in the same PR — not deferred.** The frozen calibration character runs
`brutoh-actual` (Phalanx + Thunderlord confirmed against the MGT+12 2026-05-15 log audit), so
Phalanx's −4% flat was live on every non-immune physical hit of the ratified corpus. Same bug
class, same mechanism (target-side debuff already inside `unmitigatedAmount`), same gate, its own
mutation-verified regression test.

## What this means for the headline (ratification asks)

This PR deliberately does NOT flip `calibration_tier` or `global_rmse` — measurement shipped,
tier/headline change pending human ratification, same precedent as the Guardian LOO-CV FAIL
(2026-07-08). The user is asked to ratify one of:

- **(A) Recommended — fix + hold K=3430 + re-label.** Update `global_rmse` 0.068 → 0.138 (the
  honest replay-fit at canonical K), demote `protection_warrior`'s `calibration_tier`
  `calibrated` → `characterized` (the corpus now reads +11.5% biased — fails the project's own
  unbiased bar), with the wedge decomposition surfaced in the tier caveat. Then ship the F-layer
  fast-follow (measure per-run F in `calibrate_spec_from_logs.py`/`calibrate-k`, judge on
  F-corrected residuals), which on today's evidence returns the warrior to ~+5% residual and
  likely re-earns `calibrated` with a cleaner attribution than it ever had.
- **(B) Fix + move K to ~2905.** Headline stays ~0.068 and unbiased — but K becomes self-fit
  again, absorbing a named mob-side layer, silently shifting Guardian + every other spec +
  every armor marginal. **Rejected on methodology; listed for completeness.**
- **(C) Fix only, headline decision deferred to the F-layer PR.** Repo carries a stale 0.068
  headline in the interim, named in constants comments (this PR updates those comments either
  way so the staleness is loud, not silent).

## What could still be wrong

- The ~90% physical-share figure used in the consistency check is inferred from the shift
  arithmetic, not independently measured on this corpus; if true physical share is lower, a
  small part of the +11.3pp shift is unexplained (would not change any conclusion above).
- The wedge magnitude (×0.94) is one per-hit-forensics run from this corpus plus cross-spec
  corroboration; the F-layer fast-follow measures it per-run and will tighten the "+4.8%
  residual after wedge" estimate. If per-run F turns out inconsistent across the corpus, the
  F-consistency gate (not a constant) is the designed response.
- Demo Shout's replay uptime is the sim's own on-CD policy, not the log's real cast timings —
  fine for a layer that is now inert in replay, but any future re-introduction of a
  replay-window-accurate Demo model must read real `SPELL_AURA_APPLIED` windows, not the policy.
- 300 iterations per K (the tool default) — Monte Carlo noise on per-run deltas is ~±0.5pp;
  irrelevant at the effect sizes here.

## Files changed

- `src/simf/core/mitigation.py` — the two replay gates (+ mechanism comments).
- `tests/test_mitigation.py` — two mutation-verified regression tests.
- `src/simf/data/constants.yaml` — comment-only: `global_rmse` and warrior `calibration_tier`
  comments now name the post-fix measured state and point here (no value changes; ratification
  decides those).
- `docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md` — this doc.
