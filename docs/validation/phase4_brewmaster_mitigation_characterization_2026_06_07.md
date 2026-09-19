# Phase 4 — Brewmaster over-prediction characterization (2026-06-07)

This is the characterization the Director scoped as step 1 of the
"absorb-and-return engine phase" — quantify *what* mechanic drives the
Brewmaster +27%/+57% over-prediction (`phase4_brewmaster_calibration_2026_06_07.md`)
before writing any engine code. **The data refutes the going-in thesis.** It is
not the per-event absorb clip, and not Stagger-deferral. It is **flat pre-absorb
mitigation the replay engine lacks** — most clearly a ~20pp magic layer.

No engine code in this pass — this note is the ratification/redirect artifact.

## TL;DR

- **The per-event clip is NOT the bug.** `min(damage, log_absorbed)` discards
  **0** absorb on the Brewmaster runs (and ~0 on the warrior control). Every
  event's `absorbed` ≤ its post-armor-vers damage. The leading hypothesis
  (clip loses absorb) is empirically dead.
- **Stagger nets correctly.** Stagger is logged as `SPELL_ABSORBED` (spell
  124255); the engine subtracts it from the incoming hit and adds back the
  self Stagger-DoT ticks. Sim and reality both count the same ~37M/45M of
  ticked Stagger as damage taken; the staggered-then-purified remainder is
  removed on both sides and cancels in the comparison.
- **The gap is flat pre-absorb mitigation the engine doesn't model:**
  - **Magic: ~18–20pp, consistent across every school and both tanks** — real
    magic mitigation ≈ 65%, sim reaches ≈ 45–47% (absorb-subtract + ~2% vers
    only). Magic can't be armor → this is unambiguously a **missing flat
    magic-DR layer**.
  - **Physical: ~7–14pp** — real physical mitigation higher than the armor
    formula yields at the COMBATANT_INFO armor snapshot. An **armor-magnitude**
    candidate (source TBD — buffs vs snapshot vs base_amount semantics on
    staggered hits); do not commit to a mechanism on this half yet.
- **Consequence:** this **re-scopes the phase** away from "absorb-and-return /
  deferred-damage modeling" toward **unmodeled flat Brewmaster mitigation
  (magic DR + physical/armor magnitude)**. The Stagger/absorb machinery is
  already correct. → **Director re-consult before any engine work.**

## Method

Analytic reconstruction of the replay engine's deterministic Brewmaster path
(`armor@K=3430` on physical + versatility − per-event `min(damage,
log_absorbed)`, plus self Stagger ticks added) computed directly from the log
events, so each bucket is attributable. Validation: the reconstruction
reproduces the Monte-Carlo result exactly (**+27.3% = +27.3%** on AnonBrewmaster2), so
the per-school split is trustworthy. Absorb attribution uses `SPELL_ABSORBED`
`absorbSpellName`; purified Stagger uses `STAGGER_CLEAR`. Tooling:
`scripts/calibrate_spec_from_logs.py` + the decomposition scripts.

## Per-school decomposition (sim_dealt and real_landed are both post-absorb)

**AnonBrewmaster2 — Windrunner Spire +12** (armor 1641 → armor_dr 32.4%, vers 1.9%):

| school | base | landed | real mit% | sim mit% | gap |
|---|---|---|---|---|---|
| physical | 237.2M | 37.3M | 84.3% | 77.1% | +17.1M |
| nature | 14.0M | 4.5M | 67.6% | 44.4% | +3.2M |
| fire | 6.2M | 2.2M | 64.7% | 45.6% | +1.2M |
| shadow | 3.5M | 1.1M | 67.5% | 50.8% | +0.6M |
| arcane | 2.2M | 0.8M | 64.1% | 42.0% | +0.5M |

Total over-prediction +22.6M (+27.3%). Self Stagger ticks 37.1M (both sides).
Clip loss **0**. Absorbs: Stagger 111.8M, Celestial Infusion 6.5M, Yu'lon's
Grace 2.9M.

**AnonBrewmaster1 — Seat of the Triumvirate +17** (armor 1312 → armor_dr 27.7%, vers 3.6%):

| school | base | landed | real mit% | sim mit% | gap |
|---|---|---|---|---|---|
| physical | 400.4M | 49.0M | 87.8% | 74.1% | +54.8M |
| shadow | 63.0M | 22.1M | 64.9% | 47.0% | +11.3M |

Total over-prediction +66.1M (+57%). Self Stagger ticks 45.3M (both sides).
Clip loss **0**.

**Warrior control — Brutoh, Nexus-Point Xenas +12** (clip check only; the
analytic reconstruction omits Shield Block so its absolute % is not the
engine's): clip loss = 5,116 ≈ 0 on 30.7M real damage. Ignore Pain is a pure
absorb pool whose per-hit absorb never exceeds the hit → the clip is a no-op.
Confirms the clip is not load-bearing for either spec.

## Reading the two halves

**Magic (airtight).** Real magic mitigation is ~65% across nature/fire/shadow/
arcane on AnonBrewmaster2 and 64.9% shadow on AnonBrewmaster1 — strikingly flat. The engine, in
replay mode, applies only versatility (~2%) plus the logged absorbs to magic,
landing ~45–47%. The missing ~18–20pp is school-independent and armor-immune →
a **flat magic-DR layer the Brewmaster model lacks in replay mode.**
*Candidate models (cannot separate from one pull each):* an always-on passive
magic DR, vs an averaged active CD (Dampen Harm / Diffuse Magic). These imply
different engine models — resolve in the next phase, do not assume here.

**Physical (candidate).** Real physical mitigation exceeds what `armor/(armor+K)`
gives at the COMBATANT_INFO armor snapshot (1641 / 1312). Backing out an
effective armor (~2250 for AnonBrewmaster2) is murky — it assumes `base_amount` is strictly
pre-armor on staggered hits, and the source (raid/group armor buffs vs snapshot
staleness vs base_amount semantics) is unestablished. Treat as **armor-magnitude
candidate**, not a finding. The physical gap grows as the snapshot armor falls
(7.2pp at 1641 → 13.7pp at 1312), consistent with armor magnitude but not
diagnostic of the cause.

## Why this refutes the absorb-and-return phase scope

The Director's thesis: the `min(damage, log_absorbed)` clip and Stagger-as-
deferred-damage are the Brewmaster lever, and the rework is additive (pure
absorbs keep semantics, only Stagger gets deferred-damage queue). The data:
clip loss is **0**, and Stagger already nets correctly. Re-architecting the
absorb/deferred-damage path would change *nothing* about these residuals. The
lever is **mitigation magnitude/layers the model is missing**, chiefly a flat
magic DR.

## Recommended next step (Director's call, not the engineer's)

Re-consult the Director with this refutation to re-scope the phase. Candidate
re-scoped vector: **model the missing flat Brewmaster mitigation** — start with
the magic-DR layer (airtight, ~20pp, well-isolated), with the physical/armor
half as a separate strand pending a source diagnosis. The "absorb-and-return /
deferred-damage" rework is **not** the Brewmaster lever and should be deferred
unless a different spec (Prot Paladin) needs it.

Do **not** implement a "Brewmaster magic DR" off this note alone: one pull each,
candidate (passive vs averaged-CD) unresolved, and it is validation-critical
(touches replay-mode mitigation). It needs the Director's re-scope + the
engine-batch-ratification discipline.
