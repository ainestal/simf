# Guardian magic-mit fix + physical characterization — and the coaching reframe

**2026-06-29.** Follows the 11-log WCL validation
(`phase4_guardian_wcl_validation_2026_06_28.md`) which found a ~15pp magic
under-mitigation. This chapter: wired the sourced magic-DR layers, re-ran the
regression, and characterized the residual — which turned out to be **execution,
not a fixable mechanic**, and reframed the whole gap as a *coaching* signal.

## The magic fix (wired, ratified)

Two SimC-sourced layers, both baseline-on (ratified 2026-06-29 — near-universal,
WCL carries no talent data to gate on):
- **Ursol's Warding (471492):** magic DR = 10% of the tank's armor DR (~8.5%),
  all non-physical schools.
- **Bear Form (5487 Eff #14):** extra −6% arcane, on top of the −3% all-school.

Applied school-aware in `apply_guardian_mitigation` (physical + bleeds + warrior
bit-identical). constants_version 29→30.

## Re-run: the fix is correct, but it unmasked a second error

Per-school (Player 1, the cleanest magic-heavy fight): arcane sim-mit
20.9%→31.8%, shadow 22.6%→29.0% — the magic gap roughly halved; **arcane gap
+16.3pp→+3.3pp**. The two Nexus-Point over-predictions dropped (+21%→+12% *now
passes*; +33%→+23%). Mechanically the fix does exactly what it should.

But the headline did **not** improve: RMSE 0.155→**0.147** (marginally better),
within-±15% **8/11→7/11**, mean bias **+3.2%→−4.0%**. Every fight moved ~7pp more
negative. The pre-fix "unbiased +3.2%" was a **coincidence** — a magic
*under*-mitigation was cancelling a physical *over*-mitigation. Fixing magic
unmasked the physical over-credit, pushing physical-heavy fights (Algeth'ar,
Maisara) outside ±15% on the negative side.

## The physical over-credit is NOT Ironfur (hypothesis refuted)

Measured each fight's **actual** time-averaged Ironfur stacks from the WCL buff
events (`scratchpad/ironfur_uptime.py`, stack-integral) vs the modeled 2.45:

- **Aggregate actual = 2.42 — the model's 2.45 is spot-on.** The fixed average is
  not the problem.
- **No clean correlation:** Player 2 is over-mitigated (−20.4%) while running *more*
  Ironfur than the model (2.69 stacks); AnonPlayerX11 ran 1.92. Per-fight Ironfur
  would help low-stack fights and *worsen* high-stack ones — a wash.

So the ~4–7pp physical over-credit is the sim's **optimal-play assumption**: real
physical mit is 80–83%, the sim's 86–87% is correct armor + flat-averaged
defensive baselines (Barkskin/Rage-of-the-Sleeper assumed on-cooldown forever +
a 5% party aura always up) + no avoidable hits. Real tanks have coverage gaps and
eat the occasional unmitigated swing → they take **more** than the clean-play
sim. **That is the model floor / execution variance, not a fixable constant** —
and it is exactly what the existing "assumes proper defensive use — real death
rates are higher" caveat discloses.

## Decision

- **The magic fix lands.** It corrected a genuine mechanical error (the validation
  did its job catching it); it makes magic-content predictions accurate; RMSE
  improved; warrior bit-identical.
- **`guardian_druid.calibrated` stays `false`** — now for a *precisely understood*
  reason: mechanically sound (Ironfur 2.45 confirmed; magic DR corrected), but the
  sim is an **optimal-play model** whose per-fight DTPS vs real *execution* scatters
  beyond ±15%. **"Reach ±15% against real logs" is not achievable by mechanics** —
  that bar conflates model error with the optimal-vs-real-execution gap.
- Mechanical Guardian calibration is at its honest end. Remaining magic residual
  (~7–10pp on non-arcane magic, e.g. shadow +8.4pp) is the *reactive-CD-timing*
  effect (real tanks pop Barkskin/SI onto magic spikes; the sim averages flat) —
  also execution, not a clean mechanic.

## The reframe: the gap IS the product (coaching)

The sim-vs-log gap is not noise — it is the **counterfactual no other tool
computes**. WCL shows what happened; SimC/Raidbots ignore survival; simf has the
optimal-play model, so it can show a tank **where they leak survivability, how,
and the fix**, quantified by the sim.

**Prototyped on AnonPlayerX11 (the −23% outlier)** — `scratchpad/coach_prototype.py`.
The −23% (sim over-predicted their mitigation) is fully explained by their own play:
Ironfur 1.92 vs 2.45, Rage of the Sleeper 0 casts, Survival Instincts 2.1% vs 5%,
and 2 of their 8 biggest hits (415–420k) eaten with every cooldown available
(Incarnation correctly covered the other 6). **The calibration "error" is the
coaching signal.**

Design choice locked in: coach on **directly-measured per-lever gaps** (CD
uptime, Ironfur stack-integral, unmitigated big hits), NOT the raw DTPS delta
(which mixes model error with execution). Each line is model-independent and
action-specific. Open honesty traps (validator-flagged, to resolve before
shipping): talent ambiguity (RotS "0 casts" = unused vs untalented), the
on-cooldown benchmark being tactically imperfect, and a Barkskin
uptime>achievable measurement detail. Productization TBD by the user.

---

## 2026-06-30 — SimC source audit of the magic residual (resolves calibrated-doc follow-up #1)

Follow-up #1 of `phase4_guardian_calibrated_2026_06_29.md` asked: is the magic
residual a **reactive-CD-timing** effect or an **Ursol's-Warding-magnitude** error?
A validator engine-math audit against SimC source (`engine/class_modules/sc_druid.cpp`,
build cached for the pass) settled it. **Verdict: ~30% a real missing mechanic,
~70% execution. Decision: docs-only — no engine change (user, 2026-06-30).**

**Findings (the two `.cpp` claims spot-verified against the cached source):**

1. **A real omitted layer — Glistening Fur effect #13.** `druid_t::target_mitigation`
   (`sc_druid.cpp:14603-14613`) gates Bear Form's magic split on
   `talent.glistening_fur.ok()`: arcane → effect #14 (−6%), **everything else →
   effect #13 (−3%, shadow/fire/frost/nature/holy)**. simf models only #14
   (`classes/guardian_druid.py:94-95`); **#13 is omitted.** Adding it would close
   **~2.6pp of the ~8.4pp shadow gap** (marginal on the multiplicative chain:
   `0.03 × (1 − existing_shadow_DR≈0.138)`). This explains why arcane closed to
   +3.3pp while shadow stayed +8.4pp — arcane has the modeled #14, shadow has no
   modeled equivalent.

2. **The remaining ~5.8pp is execution.** No other always-on magic-DR layer exists
   in SimC's `target_mitigation` for Guardian (Thick Hide #1 all-school, Ursine
   Adept effectN(2) all-school in Bear Form, Glistening Fur #13/#14 — that's the
   whole kit; SotF / Pelt of the Wild / Empowered Shapeshifting are not passive
   magic DR). So the residual beyond #13 is reactive-CD timing (Barkskin/SI/external
   concentrated on real magic spikes vs the sim's flat average) — **not a constant**;
   tuning to close it would be fudging.

3. **Ursol's Warding is NOT SimC-sourced (provenance correction).** `sc_druid.cpp:1115`
   and `11269` mark `ursols_warding` `// TODO: NYI` — declared, never implemented,
   absent from `target_mitigation`. simf's 10%-of-armor value is spell-data
   interpretation, not the SimC code path. The constants comment claiming
   "SimC-sourced" was corrected (constants.yaml). This also dissolves the earlier
   "tuning Ursol's = fudging because it's SimC-sourced" framing — the *reason* not to
   tune it is that the residual is execution, not that Ursol's is SimC-validated.

4. **Latent build-gating bug (documented, not fixed).** Both Glistening Fur effects
   are `talent.glistening_fur.ok()`-gated in SimC (Elune's-Chosen-only), but simf
   applies the arcane −6% **baseline-on (unconditional)** → over-credits a
   non-Glistening-Fur / Druid-of-the-Claw bear by 6% arcane. AnonGuardian1 (the calibration
   corpus) is Elune's Chosen, so the calibration is unaffected. Left baseline-on per
   the docs-only decision; the constants comment now states the assumption explicitly.

**Why docs-only (user, 2026-06-30):** the fixable slice is small (~2.6pp on one
school of one spec), the correct gating had real downsides either way
(baseline-on over-credits non-EC; EC-gating zeroes the layers on the no-log
gear-verdict surface where EC can't be detected), and ~70% of the residual is
unfixable execution. So: record the audit, correct the provenance, document the
omitted #13 + the baseline-on assumption — but leave engine behavior bit-identical.
The Guardian magic model is now **honestly characterized as school-incomplete-by-
choice**; the remaining residual is the coaching signal, surfaced in the UI caveat.
Uncertainties carried forward: effect-#13/#14 + Ursine-Adept magnitudes are
Wowhead/DBC-sourced (the .cpp reads them from DBC), and AnonGuardian1's Glistening-Fur node
selection is inferred from the closed arcane residual, not a decoded talent string.
