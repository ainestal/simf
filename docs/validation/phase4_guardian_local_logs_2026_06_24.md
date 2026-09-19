# Phase 4 — Guardian Druid characterization vs 16 LOCAL ACL logs (2026-06-24)

First-ever characterization of Guardian against a **local timed-run corpus**. The
#197 pass (`phase4_guardian_characterization_2026_06_22.md`) and the #198 fix
(`1358f53`) were built on **3 public WCL gear-certain fights** because "there are
no timed Guardian local ACL logs in `examples/`". The user has now supplied
**13 local ACL logs (≈1.8 GB, Jun 18–23)** of the Guardian **AnonGuardian1-AnonRealm1-EU**,
yielding **16 timed Guardian M+ runs, +10 → +16, across 6 dungeons**. This is a far
stronger basis than the 3 WCL fights and it materially revises #197/#198.

**No engine code in this pass — this is the characterization / ratification artifact.**
`specs.guardian_druid.calibrated` stays `false`.

Tooling: `scripts/characterize_guardian_anonguardian1.py` — a **streaming, one-run-at-a-time**
variant of `scripts/calibrate_spec_from_logs.py` (the stock tool holds all replays in
memory at once → OOMs the 1.8 GB Pi on 18×200 MB logs). Per run it sims the replay at
the canonical K=3430 and, in a single windowed forward pass, measures Ironfur (192081)
stack-integral + uptime + cast cadence + `"Not enough rage"` fails, plus **live in-form
armor** from the ACL advanced-param block on AnonGuardian1-*sourced* events (the advanced block
describes the acting unit, so the armor field is the tank's only on their own casts/swings).

## Results (canonical K=3430, post-mit DTPS, sim iters=30)

```
run                          key  real_dtps pred  delta%  IF_stk up%  cast:failRage  live_armor  model_armor  armor_from_gear
AA   062026_0748[1]          +12    35,613  24,429 -31.4%  2.35  82  370:1972        10,413      28,076       7,847
AA   062026_1639[0]          +10    21,765  20,136  -7.5%  2.32  77  287:1243        10,509      12,350       2,941
AA   062026_1716[0]          +12    35,523  24,290 -31.6%  2.33  83  367:1886        10,855      39,564      11,310
AA   062026_1716[1]          +14    39,221  28,390 -27.6%  2.35  83  383:1946        11,122      39,038      11,164
AA   062026_1716[2]          +14    25,375  20,462 -19.4%  2.40  86  423:2376        11,381      31,980       8,876
AA   062026_2029[0]          +15    43,181  35,715 -17.3%  2.37  83  563:3449        11,352      12,775       2,958
NPX  062126_0705[0]          +14    38,385  40,773  +6.2%  2.18  84  594:3342         8,876      12,775       2,958
NPX  062126_0705[1]          +14    36,468  35,319  -3.2%  2.40  88  371:2292        11,381      48,798      14,189
Sky  062126_0705[2]          +15    50,889  56,593 +11.2%  2.42  89  419:2184        11,172      12,847       2,958
Mai  062126_1915[0]          +12    27,958  28,790  +3.0%  2.57  83  411:2091        11,627      12,775       2,958
Mai  062126_1915[1]          +15    27,236  23,586 -13.4%  2.66  82  501:2318        11,627      21,537       5,696
WS   062226_1228[0]          +15    32,694  30,492  -6.7%  2.57  88  515:2888        11,352      12,775       2,958
AA   062226_1228[1]          +15    42,156  37,129 -11.9%  2.48  85  483:2640        11,886      14,177       3,396
WS   062226_1645[0]          +16    37,643  35,503  -5.7%  2.55  89  619:3458        10,532      12,775       2,958
MgT  062326_1715[0]          +12    14,180  10,373 -26.9%  2.69  88  419:2457        11,523      22,193       5,845
MgT  062326_1822[1]          +15    22,851  26,392 +15.5%  2.66  91  586:2681        11,523      13,027       2,958

9/16 within ±15%; mean |delta| = 14.9%
```

## TL;DR — the model's headline error is a CALIBRATION-TOOLING artifact, not the shipped model

Split the 16 runs by which COMBATANT_INFO armor snapshot the hydrate happened to grab:

- **Group A — stable base (`armor_from_gear`≈2,958, model armor ≈12.8k ≈ live ~11k): 9 runs.**
  deltas −17.3% … +15.5%, **mean −1.5%, mean |delta| 9.4%, 7/9 within ±15%.**
- **Group B — hydrate grabbed an already-IN-FORM snapshot (`armor_from_gear` ≥5,696),
  ×3.2 double-counts → model armor 21k–49k: 7 runs.** mean **−22%** — every disaster.

The over-mitigation tracks the **inflated model armor almost perfectly**. So the
dominant error is **not** the Guardian mitigation model — it is the **hydrate picking an
unstable armor snapshot.**

## Root cause: COMBATANT_INFO[24] is a LIVE per-pull snapshot, not a stable value

Dumping every AnonGuardian1 COMBATANT_INFO across all 16 runs, armor[24] takes a wide,
form-and-Ironfur-dependent range, **strongly correlated with the stamina field** (the
Bear-Form ×1.40 stamina bonus is the tell):

| state | stamina[5] | armor[24] |
|---|---|---|
| **out of Bear Form (caster)** | ~23,800–25,300 | **919 – 1,940** |
| **Bear Form, 0 Ironfur (base)** | ~33,400–35,500 | **~2,958** |
| Bear Form + 1/2/3/4 Ironfur | ~33,400+ | ~5,800 / ~8,700 / ~11,200 / 14,189 |

- `919 (caster) × 3.2 (Bear Form 5487) = 2,941 ≈ 2,958 (observed Bear base)` — **the
  #198 ×3.2 multiplier is correct, but ONLY when applied to the true caster armor.**
- The hydrate selects the first COMBATANT_INFO in the run window, which is sometimes the
  caster snapshot and **often an already-in-form one** (2,958 … 14,189). Multiplying an
  already-in-form value by ×3.2 is the double-count that produces Group B.
- The Ironfur ladder (2,958 → ~5,800 → ~8,700 → ~11,200 → 14,189) implies **~2,800 armor
  per stack**, *more* than the model's `ironfur_flat_armor_per_agility_per_stack: 1.12 ×
  agi ≈ 2,330`.

**Reconciliation with #197/#198:** on WCL, `events(CombatantInfo)` reported caster armor
~2,961 and ×3.2 landed correctly (+86%→+10–20%, dual-validator PASS). The WCL adapter
evidently surfaces a single stable (out-of-form) value; **local ACL emits a fresh
COMBATANT_INFO per pull reflecting live form/Ironfur state**, so the same ×3.2 lands on a
moving target. The fix is in the **hydrate's snapshot selection**, not the engine model.
(Caveat: WCL "2,961" vs local caster "919–1,940" don't agree in magnitude — the true
caster-armor value needs an in-game tooltip to pin; see open question Q2.)

## Ground truth I AM confident about

- **Live in-form armor ≈ 11,000, remarkably stable across all 16 runs** (8,876–11,886),
  barely moving with agility (1,749→2,129) or key (+10→+16). This is the anchor the model
  should reproduce for AnonGuardian1. (It barely moves with agi because Ironfur stacks are
  **rage-capped** — more agi raises per-stack armor but cannot buy more stacks.)
- **Real Ironfur ≈ 2.45 avg stacks (range 2.18–2.69), rising with key; uptime ~85%
  (77–91%).** The static `ironfur_avg_stacks_m_plus: 1.5` understates it by ~60%.
- **Rage is the binding constraint on Ironfur, universally and severely.** AnonGuardian1 fails
  Ironfur on `"Not enough rage"` **2–9× more often than they land it** (e.g. +15 AA: 563
  casts vs **3,449** fails; +16 WS: 619 vs 3,458). They spam Ironfur and the rage bar, not
  the GCD, gates uptime. **This empirically validates the user's haste→rage→Ironfur
  survival thesis**: every marginal rage point converts directly into Ironfur uptime, and
  haste's only-survival-path is via rage generation (faster autos + more frequent
  Mangle/Thrash). simf currently values haste at 0 for Guardian — a real model gap.

## Why the entanglement means "don't tune piecemeal"

With the *stable* base (Group A), `armor_from_gear 2,958 ×3.2 + 1.5-stack Ironfur ≈
12,775` already slightly *exceeds* live ~11,000. If we naïvely bump stacks 1.5→2.45 the
model armor climbs further and physical over-mitigation worsens. The components
(caster-base, ×3.2, per-stack coeff, avg-stacks) are entangled and must be tuned together
**against the live ≈11,000 anchor**, not adjusted one at a time.

## Reprioritization (vs the pre-corpus plan "haste→Ironfur first")

1. **P0 — Fix hydrate armor-snapshot selection (calibration-path only; engine untouched).**
   Deterministically select the out-of-form caster COMBATANT_INFO (identifiable by the
   low-stamina / minimum-armor snapshot) so ×3.2 applies to the right base — OR source
   effective in-form armor directly from the live advanced-param median when replaying.
   This is the dominant fix, low-risk, and **warrior/BrM/VDH bit-identical** (non-shifters
   have stable snapshots). It makes the local calibration corpus trustworthy so every
   downstream measurement has a clean baseline.
2. **Re-run this characterization on the fixed hydrate** → clean per-run deltas → the TRUE
   residual model error (expected ≈ Group A: mean −1.5%, |delta| 9.4%, plus the known
   cross-spec magic residual on NPX/Skyreach/MgT).
3. **P1 — Ironfur stacks 1.5→~2.45 + per-stack coefficient**, tuned against the live anchor.
4. **P1 — haste→rage→Ironfur uptime** (the user's ask; empirically validated above):
   model Ironfur uptime/stacks as a function of rage rate, rage rate as a function of
   haste. Gives haste a non-zero survival marginal.
5. **P1 — Nature's Guardian mastery→max-HP; agility + Guardian-mastery survivability
   marginals; then the gem suggester** (the original product goal — now correct because
   haste/agi/mastery are valued).

## Open questions for the user (ratification before any engine code)

- **Q1 — confirm the reprioritization**: armor-source fix is P0, ahead of the haste work.
- **Q2 — pin the armor decomposition from your in-game tooltip**: Bear Form armor with
  **0 Ironfur stacks**, your **caster (out-of-form) armor**, and the **per-Ironfur-stack
  armor** number. This resolves the 919-vs-2,961 caster ambiguity and the ~2,800/stack vs
  1.12×agi question directly, instead of me fitting it.
- **Q3 — haste model shape**: uptime-elasticity (`uptime = min(1, base × (1 + k·Δhaste%))`)
  vs an explicit rage-budget sim. The data supports either; elasticity is simpler and
  honest about being an approximation.

---

## ADDENDUM (same day) — P0 hydrate fix landed; clean baseline + Ironfur decomposition

**Ratified by the user:** (Q1) tooling-fix-first, (Q3) uptime-elasticity for haste.

### P0 fix shipped (`37f89dd`): hydrate selects the out-of-form caster snapshot

`_select_combatant_info` now sources `total_armor` for a Guardian from the
minimum-stamina (out-of-form caster) COMBATANT_INFO snapshot, keeping all else from
the last. Re-running the 16-run corpus, `armor_from_gear` collapses from a
**2,941–14,189 scatter to a stable ~919** (a few runs land on 924/1,432/1,940 — the
lowest caster snapshot in *that* window; one, NPX+14[1], had no out-of-form snapshot
at all so the guard correctly left it at 2,958). The wild −31%…+15% noise is gone.

### Clean baseline (post-P0, canonical K=3430)

Deltas are now a **consistent under-armor: +7.5% … +61.7%, mean |delta| 27.4%.** Higher
than the pre-fix 14.9% — but that 14.9% was a *deceptive average* of over-mitigation
(Group B double-count) cancelling under-mitigation. The clean signal is one error mode:
**the Ironfur model under-states in-form armor (model ~6,100–9,500 vs live ~11,000).**
Magnitude tracks the physical share of the dungeon (Maisara/Windrunner/MgT-physical
+42…+62%; magic-heavy NPX/Skyreach +7…+30%).

### The armor decomposition is now pinned exactly

SimC `player_t::composite_armor` (docs/simc-reference/composite_armor.cpp):
`a = gear; a *= base_armor_multiplier; a += bonus_armor; a *= armor_multiplier`.
The COMBATANT_INFO in-form ladder (0→4 Ironfur stacks) reads off cleanly:

| Ironfur stacks | observed in-form armor |
|---|---|
| 0 (bear base) | **2,958** |
| 1 | ~5,800 |
| 2 | ~8,500 |
| 3 | ~11,100 |
| 4 | 14,189 |

- **Bear base = caster(919) × 3.2 = 2,941 ≈ 2,958** → the ×3.2 (and the implicit
  `armor_multiplier`, which cancels in the caster→bear ratio) is correct on the base.
  simf reproduces this exactly.
- **Per-stack ≈ 2,800 armor ≈ 1.42×agi** (Δ across the ladder). simf models Ironfur as
  `1.12×agi` *without* the final `armor_multiplier` (≈1.30) that SimC applies to the
  whole pool — so simf's Ironfur term is under by ~1.30×. Effective per-stack the model
  should produce ≈ `1.12 × 1.30 ≈ 1.46×agi`.
- **avg stacks ≈ 2.45** measured, not the static **1.5**.
- Trinkets (Emberwing=haste, Algeth'ar Puzzle Box=mastery) give **no** agility/armor
  procs, so the ladder is clean Ironfur — not proc-inflated.

`caster×3.2 + agi×1.46×2.45 = 2,941 + 7,047 ≈ 9,988 ≈ live ~11,000.` ✓

### P1 plan (grounded, pending ratification of the two constants)

1. `specs.guardian_druid.ironfur_avg_stacks_m_plus`: **1.5 → 2.45** (measured).
2. `specs.guardian_druid.ironfur_flat_armor_per_agility_per_stack`: **1.12 → ~1.46**
   (the SimC `armor_multiplier` the model omits, folded into the per-stack coeff), OR a
   separate explicit `guardian_armor_multiplier` on the Ironfur term (more SimC-faithful).
3. **Verify the SimC-paste path** feeds the same `armor_from_gear` semantics (post-multiplier
   caster armor) as the fixed hydrate — if it sums *pre*-multiplier item base armor, the
   two paths need reconciling so the constants are correct for both.
4. Then re-add the ratified haste→rage→Ironfur uptime-elasticity, Nature's Guardian
   mastery→HP, agility/Guardian-mastery marginals, and the gem suggester.

**Validation of the proposed P1 constants** (in-memory override via
`--ironfur-stacks 2.45 --ironfur-coeff 1.46`, no constants.yaml change), re-running all 16:

> **13/16 within ±15%, mean |delta| = 9.2%** (from the clean-baseline 27.4% under-armor).
> Physical-heavy Algeth'ar runs land at −10.4% … +3.0%. Model armor for the g919 runs
> rises from ~6,100 to ~9,200–10,100 (≈ live ~10,500–11,000).

The 3 remaining >15% misses (Maisara+15 +23.8%, MgT+15 +27.9%, Maisara+12 +17.7%) are the
runs where AnonGuardian1 ran **more** Ironfur than the 2.45 average (measured 2.57–2.66 stacks) —
a fixed avg-stacks constant cannot capture per-run variation, which is precisely the case
for the **dynamic haste→rage→Ironfur model** (more rage → more stacks). NPX/Skyreach (magic-
heavy) now sit at +0.4% … +13.7%, consistent with the known cross-spec magic residual.

---

## P1 SHIPPED — authoritative grounding (4-agent workflow, same day)

A research+verification workflow (SimC midnight source + Wowhead spell data + a clean ladder
regression + adversarial synthesis) **corrected the mechanism** behind the right number:

- **The "missing ~1.30 `armor_multiplier`" hypothesis is REFUTED.** In SimC
  `druid_t::composite_armor()`: `a = player_t::composite_armor(); a += if_val * cache.agility()`
  — the Ironfur term is added **outside** `composite_armor_multiplier`. There is no ~1.30 pool
  multiplier; simf's `gear×3.2 + agi×coeff×stacks` shape is already SimC-faithful. (Bear Form
  ×3.20 is the only pool multiplier, on the base stage — already modeled.)
- **Ironfur per-stack = 124% of Agility in 12.0.x** (the 112% in the original comment is the
  stale 11.0.2 value; SimC reads it live from DBC `A_MOD_ARMOR_BY_PRIMARY_STAT_PCT`).
- AnonGuardian1's measured ~1.4×agi/stack = **124% base × Killing Strikes (+20%, near-universal in M+
  Guardian builds)** ≈ 1.49 (Reinforced Fur would raise the base to 139%). The fitted **1.46**
  sits in the convergence band [1.34 ladder slope, 1.46 doc/fit, 1.49 SimC+KS].
- **The two P1 constants are PATH-INDEPENDENT** (they ride the *agility* term `agi×coeff×stacks`,
  which both the hydrate and SimC-paste paths populate identically) — so the earlier cross-path
  caveat does **not** touch them. The base-armor semantic divergence only affects the `×3.2` term,
  and reconciles for druids: the repo's Guardian SimC-paste profile (`anonguardian2.yaml`) carries
  `armor_from_gear=789` — same magnitude band as the hydrate caster ~919, both un-Bear-multiplied,
  so ×3.2 applies correctly to each. (The post-talent-multiplier hazard in `_backout_armor` is a
  **warrior-only** concern — Guardian has no armor-multiplier talents.)

**Shipped (constants_version 24→25):** `ironfur_flat_armor_per_agility_per_stack` 1.12→**1.46**,
`ironfur_avg_stacks_m_plus` 1.5→**2.45**. Documented as the *effective* per-stack for a typical
Killing-Strikes build (NOT universal), with per-talent gating flagged as a follow-up.
`specs.guardian_druid.calibrated` **stays false** — the binding overfit is `avg_stacks` (AnonGuardian1's
rage economy; the ratified **haste→rage→Ironfur uptime-elasticity** model is the real fix), and a
**Druid-of-the-Claw** log + the magic residual are still outstanding. The in-game tooltip (Q2) is
now **confirmatory, not blocking** for these constants; it becomes necessary only when generalizing
the coeff to non-Killing-Strikes talent builds.
