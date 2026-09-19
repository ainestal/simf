# VDH Fiery Brand double-counted in log replay (2026-07-17)

**Status: fixed. No calibration_tier or published-delta change in this PR** — VDH is
already `calibration_tier: characterized` (not `calibrated`), so no headline trust
number is at stake here the way the Warrior's was; see
`docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md` for that sibling
fix. This one only makes an already-open investigation's residual honest.

## Summary

A sweep for the same bug class that hit Prot Warrior's Demoralizing Shout/Phalanx
(both attacker-side mob debuffs re-applied during log replay on top of a
`raw_amount` that already reflects them) found a third instance: VDH's **Fiery
Brand** (207744, "−40% target-source DR"). Fiery Brand marks the *mob*, reducing
what it deals — attacker-side, same as Demo Shout/Phalanx — so its reduction is
already inside the log's `unmitigatedAmount`/`raw_amount` by the time simf sees
it. `vengeance_dh.py` applied it unconditionally (no `is_log_replay` guard),
double-counting it on every replayed hit.

Confirmed the classification is unambiguous from the ability's own tooltip/effect
("target-source DR" = the branded target's damage output is reduced) and from the
fact that every *sibling* VDH layer (Metamorphosis, Painbringer) is already
window-gated to replay-only via `event.active_buffs` — Fiery Brand was the only
layer applied identically in both modes, which is the tell.

## Impact direction

VDH already **under-mitigates** (`calibrated: false`, canonical-K deltas
+37.5/+62.0/+36.5% — the model predicts too much damage relative to real logs).
The spurious Fiery Brand credit was masking part of that gap: removing it raises
modeled physical+magic damage taken by
`1 − (1 − fiery_brand_target_dr × fiery_brand_avg_uptime_in_m_plus)` ≈ **6.8%**
across the board (both schools — Fiery Brand's DR is all-school per
`constants.yaml`). This will **widen**, not close, the published VDH deltas —
correctly: the true gap was always this large, part of it was just hidden behind
a bug. The ongoing magic-layer characterization work (roadmap priority #1 —
Immolation Aura's DT hook, Fel Flame Fortification, Frailty) should use the
post-fix residual, not the pre-fix one.

No fresh WCL sweep re-run in this PR (VDH's characterization corpus is
WCL-based, a separate pipeline from the local-log Warrior corpus, and re-running
it is the job of the next magic-layer characterization pass, not this bug fix) —
the ~6.8% shift is exact by construction (it is the removed multiplicative
factor), not an estimate needing a sweep to confirm.

## The fix

`src/simf/classes/vengeance_dh.py`, step 5 (Fiery Brand): gated behind
`if not event.is_log_replay:`, the same flag and pattern used for the Warrior
fix. Live/synthetic mode is untouched — that damage profile carries raw mob
output, so modeling the reduction there remains correct.

Regression test: `tests/test_vengeance_dh.py::test_vdh_fiery_brand_not_double_counted_in_log_replay`
— mutation-verified (reverting the gate makes the replay-mode with/without pair
diverge, failing the equality assertion; confirmed by hand before this doc was
written).

## What's still open

- ~~The exact per-run impact on VDH's published deltas hasn't been re-measured
  against the real WCL corpus~~ **DONE 2026-07-18** —
  `docs/validation/phase4_vdh_magic_residual_leads_2026_07_18.md` re-ran the
  3-fight corpus: canonical-K deltas moved +37.5/+62.0/+36.5% →
  +48.7/+75.1/+47.9% (RMSE 0.468→0.586), matching this doc's ~6.8% analytic
  prediction. That doc also characterizes the three magic-residual leads
  named below and finds they close at most ~5-7pp of the (still much larger)
  gap — held for user ratification, not shipped.
- The sibling sweep that found this (2026-07-17) checked Prot Paladin, Blood DK,
  and Brewmaster too and found no instances of this bug class in those three —
  see the sweep's findings folded into this note for provenance. Should still be
  re-checked whenever those specs' mitigation chains change meaningfully.
