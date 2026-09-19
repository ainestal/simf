# Phase 4 — Brewmaster missing-mitigation layer model (2026-06-07)

Follow-up to `phase4_brewmaster_mitigation_characterization_2026_06_07.md`
(which refuted the absorb-clip thesis and isolated the gap to *flat pre-absorb
mitigation the engine lacks*). This memo names the real 12.0.5 layers, tests
each against the two timed Brewmaster logs, and recommends a ratifiable model.
**Doc-only — no engine code.** The headline is the one the cross-run check
forced into the open:

> **The missing Brewmaster mitigation is HERO-TALENT-DEPENDENT.** A single flat
> DR constant for all Brewmasters would be wrong — it would over-credit builds
> that don't run the talent. The fix is a **talent-gated mitigation-layer
> ledger**, not a constant.

## The two runs are two different builds — and that's the finding

| Tank | Key | Hero talent (verified in-log) | Baseline over-prediction |
| --- | --- | --- | --- |
| Player 1 | +12 Windrunner Spire | **Shado-Pan** — "Predictive Training" ×1339 | +27.3% |
| AnonBrewmaster1 | +17 Seat of the Triumvirate | **Master of Harmony** — "Aspect of Harmony", "Balanced Stratagem"; PT ×0 | +57% |

Predictive Training (Shado-Pan, **8% all-damage**, ~100% AoE/M+ uptime via
Mastery dodge procs) is present on Player 1 and **absent on AnonBrewmaster1**. So PT
cannot be a universal Brewmaster layer.

## Engine-wiring gap (verified)

`Character._always_on_dr()` branches on `class_spec` and only handles
`protection_warrior` (Defensive Stance + Indomitable). For
`brewmaster_monk` it returns `1.0` — **no flat DR of any kind.** Every
Brewmaster hero-talent passive DR (Predictive Training, Aspect of Harmony, …)
is therefore unmodeled in replay. Magic Stagger and Yu'lon's Grace are *not*
the gap — they are logged as `SPELL_ABSORBED` and already subtracted via
`log_absorbed` (Stagger is 89–91% physical / 9–11% magic across the two runs;
the magic portion is in the absorbed field the engine already removes).
Fortifying Brew at the modeled CD (20% × 15s/360s ≈ 0.8% averaged) is
negligible.

## Reconstruction — named layers vs the measured gap

Analytic replay reconstruction (reproduces the Monte-Carlo +27.3% exactly).
"+PT+Fort" applies Predictive Training 8% all-school + averaged Fortifying Brew
*before* the absorb subtract. Per school, M = $10^6$:

**Player 1 — Shado-Pan (PT real):**

| school | base | real | sim baseline | +PT+Fort | gap0 | gap after |
| --- | --- | --- | --- | --- | --- | --- |
| physical | 237.2M | 37.3M | 54.4M | 40.6M | +17.1 | +3.3 |
| nature | 14.0M | 4.5M | 7.8M | 6.6M | +3.2 | +2.0 |
| fire | 6.2M | 2.2M | 3.4M | 2.8M | +1.2 | +0.6 |
| shadow | 3.5M | 1.1M | 1.7M | 1.4M | +0.6 | +0.3 |
| arcane | 2.2M | 0.8M | 1.3M | 1.1M | +0.5 | +0.3 |

Baseline +22.6M → **+6.6M (PT+Fort closes 71%)**. PT is all-school, so it fixes
the physical *and* magic halves at once. Residual +6.6M (~8%).

**AnonBrewmaster1 — Master of Harmony (PT FICTIONAL — they have no PT):**

| school | base | real | sim baseline | +PT (hypothetical) | gap0 | gap after |
| --- | --- | --- | --- | --- | --- | --- |
| physical | 400.4M | 49.0M | 103.8M | 79.4M | +54.8 | +30.4 |
| shadow | 63.0M | 22.1M | 33.4M | 28.1M | +11.3 | +6.0 |

Baseline +66.1M. Even crediting a hypothetical 8% they don't have, +36.3M
remains — overwhelmingly physical (+30.4M). AnonBrewmaster1's gap is **not** PT; it is
dominated by the physical armor-magnitude shortfall (worse at their lower armor
snapshot 1312 → armor_dr 27.7%) plus a Master-of-Harmony layer not yet
attributed.

## The two components of the gap

1. **Hero-talent flat DR the engine omits (build-specific).**
   - Shado-Pan → **Predictive Training, 8% all-school** — *validated*: closes
     71% of Player 1's over-prediction, no fudge.
   - Master of Harmony → **Aspect of Harmony** (accumulating absorb/heal) — not
     yet attributed; partly may already be in `log_absorbed`. Needs a
     Master-of-Harmony log decomposition before modeling.
   - **Must be gated on the detected hero talent** (`detected_talent_spell_ids`
     from COMBATANT_INFO is already available). A flat constant breaks the
     other build.

2. **Physical armor-magnitude shortfall (talent-independent).** Present on both
   (Player 1 +3.3M residual after PT; AnonBrewmaster1 +30.4M even with hypothetical PT),
   and it grows as the COMBATANT_INFO armor snapshot falls (1641 → 1312). Real
   physical mitigation exceeds `armor/(armor+K)` at the snapshot. Candidate
   causes — raid/group armor buffs, snapshot staleness, or `base_amount`
   semantics on staggered hits — **unresolved; do not model yet.** This is the
   larger driver of AnonBrewmaster1's +57%.

## Recommended model (for ratification — engine PR follows your sign-off)

**Build the per-spec hero-talent mitigation-layer ledger the cross-run check
proved necessary**, and consume it from `_always_on_dr` for Brewmaster:

- New `data/constants.yaml` structure (constants.yaml discipline): per spec, a
  list of always-on / averaged-uptime DR layers keyed by talent spell_id →
  `{school_coverage, dr_pct, avg_uptime}`. First rows:
  `predictive_training: {talent_spell_id: <Shado-Pan PT id>, school: all, dr: 0.08, uptime: 1.0}`
  (M+ AoE-saturated; gate or down-weight for single-target later).
- `Character._always_on_dr()` for `brewmaster_monk`: multiply in each ledger
  layer whose `talent_spell_id ∈ detected_talent_spell_ids`. Warrior path
  unchanged → **bit-identical for every existing warrior/Prot test** (the
  testable invariant).
- Leave the **physical armor-magnitude** strand and the **Master-of-Harmony /
  Aspect-of-Harmony** attribution as explicit follow-ups; do not fit them now.

**Validation gate.** After wiring PT, re-run
`scripts/calibrate_spec_from_logs.py calibrate brewmaster_monk`: Player 1 should
move from +27% toward ~+8% (within or near ±15%); AnonBrewmaster1 should be *largely
unchanged* (no PT) — which is the correct, honest behaviour and confirms the
model isn't a global fudge. Brewmaster stays `calibrated: false` until both
builds land within ±15% (AnonBrewmaster1 needs the armor + Master-of-Harmony strands
first).

## LANDED 2026-06-07 — ledger wired, gate met, mechanism changed

The engine PR shipped the ledger. **The detection mechanism changed from the
literal ratification** and this is the most important note in this section:

> **Gate on the BUFF aura `spell_id` (Predictive Training = 451230), NOT
> `detected_talent_spell_ids`.** The ratified premise ("`detected_talent_spell_ids`
> … already available") was *false*: that set is populated from COMBATANT_INFO,
> whose talent block is `(traitNodeID, traitNodeEntryID, rank)` — i.e. **trait-
> node-entry ids (124xxx range), not spell ids**. PT's buff 451230 is not in it
> (verified on Player 1's row), and the existing warrior `spell_id: 382939/1234769`
> matching is dormant for the same reason (a real Prot Warrior log has no entry
> ids > 300000). Resolving an entry id needs a DB2/simc trait map and *fails
> silently* if wrong (gate never fires → manufactures tuning pressure). The buff
> aura is already in the data and is the reliable replay signal. The intent
> (credit PT only when present, no flat fudge) is preserved.

Implementation: `Character.active_buff_spell_ids` (new field, distinct from
`detected_talent_spell_ids`) carries the buff ids observed on the tank in a
replay; `Character._always_on_dr()` multiplies in each ledger row whose
`detect_buff_spell_id` is present; `apply_brewmaster_mitigation` calls
`_always_on_dr()` **after versatility, before the stagger/absorb subtract** (the
replay path never called it before — that pre-absorb placement is the leverage).
`scripts/calibrate_spec_from_logs.py` detects the ledger buffs per run via the
new `combat_log.detect_active_buffs`. **Warrior path bit-identical**
(`_always_on_dr` gates strictly on `brewmaster_monk`; verified by test).
`hydrate_character` is deliberately untouched — populating
`detected_talent_spell_ids` there flips `total_armor()` to the spell_id branch
and would zero warrior armor multipliers. COMBATANT_INFO/entry-id gating for the
synthetic/production (non-replay) path is an explicit follow-up.

**Result** (`calibrate brewmaster_monk`, seed=42, canonical K=3430):

| Run | Hero talent | Before | After (uptime 0.88) | In ±15%? |
| --- | --- | --- | --- | --- |
| Player 1 +12 | Shado-Pan (PT detected) | +27.3% | **+11.8%** | ✅ |
| AnonBrewmaster1 +17 | Master of Harmony (no PT) | +57% | **+56.8%** | ❌ (out of scope) |

PT moved Player 1 into band and left AnonBrewmaster1 essentially untouched — exactly the
honest, non-fudge signal the model demanded. **Brewmaster stays
`calibrated: false`**: AnonBrewmaster1's residual is the physical armor-magnitude +
Master-of-Harmony strands, both explicit follow-ups. The 8% is the audited
value, applied as-is — *not* tuned to close Player 1's residual.

### Post-audit corrections (two-validator review, 2026-06-08)

A dual `validator` pass (engine-math + log-replay) confirmed the *mechanism* is
sound — 8% all-school is the correct Midnight value (2026-04-07 hotfix cut PT
10% → 8%; talent 450992 / buff 451230), the buff discriminator is clean in both
directions (Player 1 63 PT applies / 1213 refreshes; AnonBrewmaster1 + Player 2 **zero**
PT auras in any log), composition is correct, warrior path bit-identical — but
caught two things the first pass got wrong:

1. **`avg_uptime` was `1.0`; the log says 0.88.** PT is a 6 s dodge/parry proc,
   not always-on. Measured on Player 1: **87.7 % damage-weighted** (75.2 % wall-
   clock — the gap is the unmitigated opening hit of each of 63 pulls). Corrected
   to **0.88**, which is why the honest figure is **+11.8 %**, not the +9.6 % the
   over-credited 1.0 produced. Still in ±15 %, but upper-middle of the band — and
   it rests on one Shado-Pan run; refine when a second lands.

2. **The pre-absorb leverage over-credits PT more than "modestly".** Because the
   logged Stagger absorb (`log_absorbed`) is held *fixed* while PT shrinks the
   pre-absorb hit, a nominal ~7 % effective cut becomes a **~24 % net cut on the
   physical to-HP** (engine-math agent). So the in-band result is achieved partly
   by PT's over-credit **canceling the known physical armor-magnitude under-
   mitigation** that pushes the other way — it is "the right answer via two
   partially-canceling errors", **not a clean PT validation**. Read +11.8 % as a
   characterization milestone, not proof PT is modeled to ±1 %. A future
   absorb-re-derivation (rescaling Stagger against the post-PT hit) would shrink
   PT's apparent contribution — expected, not a regression.

**AnonBrewmaster1 reframe:** their +56.8 % residual is *not* a missing flat-DR ledger
row. Master of Harmony / Aspect of Harmony (450521/450711) shows apply/remove
with **0 refreshes** — a bursty absorb/heal capstone, i.e. a **throughput /
self-heal model gap**, plus the physical armor-magnitude strand. The buff gate
correctly does not credit them. Both remain explicit follow-ups.

## Why no fudge constant (the guardrail held)

Fitting one flat magic-DR or one effective-armor number to close 20pp on Player 1
would have passed their RMSE and **silently broken on AnonBrewmaster1** (different build,
+30M physical residual). The named-layer, talent-gated model is the guardrail:
each layer is independently checkable against any future log, gated on a talent
the log states, and a wrong row fails visibly on the build that lacks it. This
is the K=2700 self-fit lesson applied.

## Caveats

- One log per build; "PT closes 71%" is one Shado-Pan run. The cross-run value
  here is *contrast* (two builds), not replication of one build.
- The reconstruction applies PT before the fixed `log_absorbed`; in replay the
  absorb is not re-derived, so this matches the engine's replay semantics but
  may modestly over-credit PT vs the live game (where a smaller post-PT hit
  would stagger less). Note for the engine PR; not gap-closing-critical.
- Master of Harmony's Aspect of Harmony is unattributed (one run, gap
  physical-dominated); its modeling waits on a dedicated decomposition.
- The physical armor-magnitude shortfall is a candidate, not a finding.

## RESOLVED 2026-07-04 — both open strands closed by per-hit forensics

See `phase4_brewmaster_physical_gap_decomposition_2026_07_04.md` (tooling:
`scripts/per_hit_mitigation_forensics.py`, `scripts/aura_attribution.py`):

- **Physical armor-magnitude strand — closed.** Player 1's armor genuinely
  oscillates 1259↔1641 in-combat: the +382 is **Blistering Scales** from their
  party's Augmentation Evoker (exactly 20% of the evoker's 1908 armor,
  cast + aura confirmed in-log) — the "raid/group armor buffs" candidate,
  confirmed. AnonBrewmaster1's 1312 snapshot is *perfect* (3,052/3,052 live-armor
  ticks agree); their residual is NOT armor. Snapshot staleness refuted.
- **Master-of-Harmony strand — closed as refuted-for-DR.** Balanced
  Stratagem stacks (451508/451514) do not move per-hit residuals at all, and
  AnonBrewmaster1's *failed* +17 run (same build) shows only the universal ~6%
  wedge — the extra ~11% on their timed run is **run-scoped** (uniform across
  every mob, all-school, aura-independent, survives the party paladin dying),
  i.e. mob-side tuning between `base_amount` and applied damage, not a
  hero-talent layer. PT (this doc's ledger row) and Fortifying Brew were
  re-validated per-hit at exactly ×0.92 / ×0.80.
- PT's 8%/0.88 row therefore remains the ONLY talent-gated flat layer the
  Brewmaster ledger needs on current evidence.
