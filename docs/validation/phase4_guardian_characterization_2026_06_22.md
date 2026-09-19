# Phase 4 — Guardian Druid over-prediction characterization (2026-06-22)

First real-log characterization of Guardian Druid, the Guardian analog of the
VDH pass (`phase4_vdh_characterization_2026_06_09.md`) and the Brewmaster pass
(`phase4_brewmaster_calibration_2026_06_07.md`). Source: **three public
Warcraft Logs Midnight (zone 47) M+ fights from three different players /
dungeons / keys**, pulled via the gear-certain PR #142 pipeline
(`events(CombatantInfo)` → the gear AND exact stats actually worn in the fight).
There are **no timed Guardian local ACL logs** in `examples/`
(`scripts/calibrate_spec_from_logs.py inventory` finds only Brutoh/prot_warrior
and spec-undetected tanks), so — like VDH — this had to use WCL's public corpus.

**No engine code in this pass — this is the ratification/redirect artifact.**
`specs.guardian_druid.calibrated` stays `false`.

Tooling (both pre-existing, used by the VDH pass):
`scripts/calibrate_spec_from_wcl.py` (multi-fight K-sweep judged at the
canonical K) and `scripts/per_school_gap_from_wcl.py` (per-school pre-absorb DR
decomposition). Both run unchanged here.

## TL;DR

- **Guardian Druid: NOT calibrated.** At the canonical global K=3430 the sim
  **over-predicts** pre-absorb damage taken by **+86.6% / +93.4% / +86.1%**
  across the three fights — far outside the ±15% Phase-4 bar. The deltas are
  **tightly clustered** (σ ≈ 3pp) across 3 players / 3 dungeons / 2 key levels →
  structural, not noise. (Post-absorb / Monte-Carlo-equivalent magnitude is
  +127–148%; pre-absorb is the apples-to-apples mitigation comparison, per the
  VDH-doc caveat.)
- **The gap is flat pre-absorb mitigation the Guardian model lacks — both halves,
  the same shape as VDH and Brewmaster:**
  - **Physical (the dominant absolute driver, ~94% of all damage): +15.7pp.**
    Real physical mit **85.2%**, engine **69.5%**. The root is a **missing
    armor multiplier**: the WCL `COMBATANT_INFO[24]` armor field for a druid is
    the **out-of-form (caster) armor (~2,961)** — it does **not** include Bear
    Form's **+220% Mod Base Resistance (Physical)** (spell 5487, SimC
    `base_armor_multiplier` ×3.2 on gear armor). The engine instead applies a
    coincidental Ironfur ×2.125 multiplier (`6,292` effective) that lands *below*
    even bare Bear-Form armor. **This is the exact VDH-Metamorphosis-armor shape —
    except Bear Form is permanent, not a cooldown.**
  - **Magic: flat ~25–39pp across every school** (shadow/fire/frost/arcane/
    nature/holy + bleed). Real magic mit ≈ 22–49%; the engine reaches ≈ 10–17%
    (versatility + averaged Barkskin + averaged Rage-of-the-Sleeper only). Armor
    can't touch magic → a **missing flat magic-DR layer**, exactly the Brewmaster/
    VDH shape.
- **Structural root (verified in code):** `apply_mitigation` early-returns into
  `apply_guardian_mitigation`, which applies **only** dodge + armor + versatility
  + averaged Barkskin + averaged RotS + Survival Instincts + healer DR + absorb.
  It **never calls `char._always_on_dr()`** (which has **no Guardian branch** —
  returns 1.0) and **never applies `party_dr_by_school`**. The Prot Warrior path
  applies **both**. So Guardian runs with **zero flat passive DR and zero party
  magic DR** — identical to the VDH asymmetry.
- **Leading mechanisms (all grounded in SimC midnight spell-data dump):**
  1. **Bear Form base-armor multiplier** (5487, +220% → ×3.2 on gear armor) —
     the physical answer, the Demonic-Wards/Metamorphosis analog for armor.
  2. **Thick Hide** (16931, **−4% all-school**) + **Bear Form passive**
     (−3% all-school / −6% arcane) — the baseline flat-DR analog to the warrior's
     Defensive Stance 15% and VDH's Demonic Wards 12%. **Completely unmodeled.**
  3. **Ironfur is modeled with the wrong mechanic** — the engine uses "+75% of
     the *total armor pool* per stack"; SimC/Midnight Ironfur (192081) is
     **"+112% of *Agility* as flat bonus armor per stack."** A percentage of the
     armor pool happens to be close in magnitude at current gear but will not
     scale correctly and is conceptually wrong.
  4. **`party_dr_by_school` parity** — the heavy healer/party aura stack on these
     logs (Mark of the Wild, Blessing of the Bronze, Renewing Mist) is never read
     on the Guardian path.
- **Consequence:** the Guardian fix is a **hybrid of the warrior and VDH shapes**
  — a Bear-Form base-armor multiplier (the big physical lever), a baseline
  `_always_on_dr` Guardian branch (Thick Hide + Bear Form flat DR), an Ironfur
  mechanic correction (flat-Agi), and `party_dr_by_school` wiring. **It is NOT a
  talent-gated ledger (Brewmaster-shaped)** — every layer is baseline, which is
  why the over-prediction is uniform across all three logs. **Director re-consult
  before any engine work.**

## Method

1. **Discovery.** `worldData.zone(id:47)` (= Midnight M+ Season 1; zone 45 is
   TWW S3 — ~30× stats, mis-scales, never use) → 8 encounters. For each,
   `encounter.characterRankings(className:"Druid", specName:"Guardian")` yields
   rows carrying `report.code` + `report.fightID` + `bracketData` (key) + player.
   Probed candidates from different dungeons/players for ACL (CombatantInfo
   present), a Guardian-tank pick, and sane hydrated stats. The top-key Guardian
   meta is monolithic (all three are **Elune's Chosen** — Lunar Beam ~60% +
   Lycara's Teachings ~95% buff auras; zero Druid-of-the-Claw markers), so this
   is a **replication** across 3 players/dungeons/keys, not a two-build contrast.
2. **Calibration sweep** (`calibrate_spec_from_wcl.py`): build the gear-certain
   `Character` (`character_from_wcl`) + the replay (`wcl_to_replay_data`) for
   each fight, sweep K, **judge every per-run delta at the canonical global
   K=3430**.
3. **Per-school decomposition** (`per_school_gap_from_wcl.py`): run the **actual
   engine replay loop** (`policy.tick`/`policy.decide`/`apply_mitigation`, incl.
   the healer `dr_cooldown` externals `run_simulation` applies) and bucket each
   non-self event by school into base / real-pre-absorb (`amount + absorbed`) /
   sim-pre-absorb (`dealt` + the absorb the engine subtracted). The post-absorb
   total reproduces the Monte-Carlo sweep delta, so the per-school split is
   trustworthy.

## The three fights (gear-certain, sanity-checked)

| Tank | Dungeon | Key | armor (engine) | HP | ilvl | Hero tree (in-log) | Δ pre-absorb at K=3430 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Player 1 | Magisters' Terrace | +24 | 6,292 | 757k | 290 | Elune's Chosen | **+86.6%** |
| Player 2 | Maisara Caverns | +24 | 6,286 | 760k | 273 | Elune's Chosen | **+93.4%** |
| Player 3 | Pit of Saron | +24 | 6,350 | 776k | 293 | Elune's Chosen | **+86.1%** |

The "armor (engine)" column is `Character.total_armor()` = the **out-of-form
COMBATANT_INFO armor (~2,961) × the engine's Ironfur 2.125 multiplier**. HP ~760k
and agility ~2,656 are plausible for Midnight leather tanks (no shield → no
`shield_armor` hydrate hazard) — the positive-control instinct says these deltas
are a model gap, not a pipeline artifact. The deltas cluster within 7pp across 3
players/dungeons/keys, far beyond the ~±20% single-fight noise band → real.

## Sweep (iters=120, seed=42; trimmed K set on the Pi)

These are the Monte-Carlo (post-absorb) deltas — the absorb-inflated magnitude;
anchor on the pre-absorb +86–93% above for the mitigation comparison. The MC
numbers reproduce the per-school post-absorb deltas (+143/+148/+128%) within
iter noise at 120 iters, the trust check that the sweep and decomposition agree.

```
=== Sweep (iters=120, canonical K=3430) ===
  K= 2000: RMSE=0.612  [+70.0%, +61.2%, +51.1%] <-best
  K= 3000: RMSE=1.128  [+118.1%, +118.2%, +101.5%]
  K= 3430: RMSE=1.321  [+135.8%, +139.5%, +120.1%] [CANONICAL]
  K= 4000: RMSE=1.564  [+160.4%, +165.0%, +142.9%]
CANONICAL K=3430: RMSE=1.3209   Best K=2000 (RMSE=0.6125)
VERDICT: all per-run deltas within +/-15% at canonical K? False
```

Even the sweep floor (K=2000) leaves a large over-prediction (+51–70%) — the missing
mitigation is **not armor K**; it is flat DR layers (Bear Form base-armor
multiplier + flat all-school DR) below/beside the armor formula. A best-K far
below canonical is the textbook signature of the model under-mitigating, not a
license to set a per-spec K.

## Per-school decomposition (aggregate across all 3 fights, M = 10⁶)

| school | base (M) | real mit% | sim mit% | gap |
| --- | --- | --- | --- | --- |
| physical | 3,756.0 | 85.2% | 69.5% | **+15.7pp** |
| holy | 1.3 | 49.1% | 9.9% | +39.2pp |
| bleed | 11.5 | 59.3% | 20.7% | +38.5pp |
| arcane | 68.8 | 42.7% | 13.6% | +29.1pp |
| fire | 8.3 | 42.0% | 13.2% | +28.8pp |
| nature | 26.0 | 39.5% | 12.0% | +27.5pp |
| shadow | 178.8 | 40.0% | 14.2% | +25.8pp |
| frost | 39.8 | 22.2% | 10.8% | +11.4pp |

Per-fight pre-absorb over-prediction: **+86.6% / +93.4% / +86.1%**
(post-absorb +143.1% / +148.0% / +127.5%). Each fight individually shows the same
flat-magic / physical-shortfall shape.

### Reading the two halves

**Physical (armor-magnitude — source identified: Bear Form base-armor
multiplier).** Real physical mitigation is **85.2%**; the engine reaches **69.5%**
(armor DR 64.7% at the engine's 6,292 × versatility × averaged Barkskin ~2.7% ×
averaged RotS ~3.3%). Physical is ~94% of all damage taken, so the +15.7pp here is
the bulk of the over-prediction (+590M aggregate).

The cause is verified, not "TBD": the WCL `COMBATANT_INFO[24]` armor for these
druids is **2,961 / 2,958 / 2,988 — the out-of-form (caster) armor, missing Bear
Form's +220% Mod Base Resistance (Physical) (spell 5487)**. SimC's
`composite_armor()` applies "Mod Base Resistance" as `base_armor_multiplier` to
gear armor (`docs/simc-reference/composite_armor.cpp`), so the real in-Bear-Form
armor is **2,961 × 3.2 ≈ 9,475 before Ironfur**. The engine never applies it —
it applies only a coincidental Ironfur ×2.125, landing at 6,292, **below even
bare Bear-Form armor**. Backing out the SimC-correct chain:

| | gear armor | × Bear Form 3.2 | + Ironfur flat (Agi×1.12×1.5) | armor DR | + vers + Thick Hide/BF flat (6.9%) | phys mit |
| --- | --- | --- | --- | --- | --- | --- |
| **engine today** | 2,961 | — (uses Ironfur ×2.125) | — | 64.7% (6,292) | | 69.5% |
| **SimC-grounded** | 2,961 | 9,475 | + ~4,462 = **13,937** | **80.3%** | | **~82.2%** |

The SimC-grounded ~82% (before crediting the modeled Barkskin/RotS at their real
~27%/low uptimes) lands within striking distance of the real 85.2% — i.e. the
physical gap is **almost entirely the missing Bear Form base-armor multiplier +
the wrong Ironfur mechanic**, exactly the VDH-Metamorphosis-armor finding but for
a permanent stance.

**Magic (airtight — missing flat all-school DR).** Real magic mitigation sits at
~22–49% across six schools (roughly flat, school-independent) while the engine
gives only versatility (~1–4%) + averaged Barkskin (2.7%) + averaged RotS (3.3%)
+ an intermittent healer-DR window — a ~10–17% floor. The missing ~25–39pp is
armor-immune and school-flat → **flat magic-DR layers the Guardian model lacks
entirely**: Thick Hide (16931, −4% all-school), Bear Form passive (−3% all-school
/ −6% arcane), and the party magic auras the Guardian path never reads
(`party_dr_by_school` parity gap). This is the dominant *relative* miss (the
engine mitigates roughly a third of what reality does on magic). frost is the
smallest gap (+11.4pp) because real frost mit is itself low here (22.2% — a
high-frost Pit-of-Saron stream the player mitigates least).

## Structural root (verified in code, not inferred)

- `core/mitigation.py:144-147` — `apply_mitigation` early-returns into
  `classes/guardian_druid.py:apply_guardian_mitigation` for Guardian.
- That function (`guardian_druid.py:23-94`) applies, in order: dodge (off in
  replay) → armor (physical, Ironfur ×2.125 baked into `total_armor()`) →
  versatility → averaged Barkskin → averaged RotS → Survival Instincts (emergency)
  → healer DR → absorb subtract. **It never calls `char._always_on_dr()`**, and
  `Character._always_on_dr()` (`core/character.py:343-361`) has branches only for
  `protection_warrior` and `brewmaster_monk` → **returns 1.0 for Guardian.**
  **It never applies `party_dr_by_school`** (the warrior path does,
  `mitigation.py:266-267`, gated on `state.party_magic_dr_active`).
- The Ironfur model in `Character.total_armor()` (`character.py:192-196`) applies
  `armor *= 1 + avg_stacks(1.5) × per_stack(0.75)` = ×2.125 on the **armor pool**.
  SimC/Midnight Ironfur (192081) is **+112% of Agility as flat bonus armor per
  stack**, added (not multiplied) — wrong mechanic.
- Net: Guardian runs with **zero flat passive DR, zero party magic DR, no Bear
  Form base-armor multiplier, and the wrong Ironfur formula.**

## How to close the gap — the missing layers, from SimC spell data

All magnitudes below are from the **SimC midnight SpellDataDump** + Wowhead spell
data (not fitted to the residual):

| Layer (spell) | Source value | Engine today | What it does | Closes |
| --- | --- | --- | --- | --- |
| **Bear Form base armor** (5487) | **+220% Mod Base Resistance (Physical)** → `base_armor_multiplier` ×3.2 on gear armor | absent (snapshot is pre-form; uses Ironfur ×2.125 instead) | triples gear armor, permanent | **most of physical (+15.7pp)** |
| **Ironfur** (192081) | **+112% of Agility as flat bonus armor / stack**, ~1.5 eff M+ stacks (uptime ~89–92%) | +75% of *armor pool* / stack (wrong mechanic) | flat bonus armor on top of Bear Form | physical (correct scaling) |
| **Thick Hide** (16931) | **−4% Mod % Damage Taken, all schools** | absent | baseline flat all-school DR | ~4pp all schools |
| **Bear Form passive** (1178) | **−3% all-school, −6% arcane Mod % Damage Taken** | absent | baseline flat all-school DR | ~3pp all + extra arcane |
| **Party magic auras** (Mark of the Wild, Renewing Mist, Blessing of the Bronze, healer auras — high uptime) | high | **nothing on Guardian path** (`party_dr_by_school` parity gap) | magic | magic |
| **Barkskin** (22812) | 20% DR, real uptime **~26–29%** on this corpus → ~5.4% avg | avg DR **0.027** (assumes ~13% uptime) | ~½ the real averaged value | physical/all (minor) |
| **Incarnation: Guardian of Ursoc** (102558) | +HP +armor, real uptime **~25%** (Player 1) | rare <40%-HP emergency only | maintained armor/HP CD in this meta | physical (minor) |

**The two big, grounded levers:**

1. **Bear Form base-armor multiplier is the physical answer.** It is the clean
   armor analog of VDH's Metamorphosis fix — except Bear Form is **permanent**, so
   it's a flat `base_armor_multiplier` on `total_armor()`, **not** a window-gated
   cooldown. Pairing it with the **flat-Agi Ironfur correction** reproduces ~82%
   physical mit (vs real 85%) at SimC values — essentially the whole physical gap.
2. **Thick Hide + Bear Form flat DR is the baseline magic answer (with party
   auras).** A baseline ~7% flat all-school DR (Thick Hide −4% × Bear Form −3%)
   the engine has no concept of — the Defensive-Stance / Demonic-Wards analog.
   With the party magic auras (parity wiring), this is the bulk of the ~25–39pp
   magic gap; the remainder is the long-standing cross-spec thin-magic model
   (shared with warrior and VDH).

## Recommended path (Director-scoped, ratified, multi-step — NOT a one-shot)

This is real engine work touching the validation-critical replay path, so it
follows `feedback_engine_batch_ratification` + `feedback_dual_validator_pre_merge_audit`:

1. **Pin the values** (read-only, done here): Bear Form +220% (×3.2), Thick Hide
   −4%, Bear Form passive −3%/−6%, Ironfur +112% Agi/stack — all from the SimC
   midnight dump. (Nature's Guardian mastery is **max HP + healing-received + AP**,
   **not** armor — the engine correctly gives Guardian no mastery→armor.)
2. **Bear Form base-armor multiplier + Ironfur mechanic fix** — apply the ×3.2
   `base_armor_multiplier` to gear armor in `Character.total_armor()` for Guardian,
   and replace the armor-pool Ironfur multiplier with flat-Agi bonus armor
   (`agility × 1.12 × avg_eff_stacks`). Highest leverage; this is the physical
   half. **Verify the COMBATANT_INFO snapshot is genuinely pre-form** (confirmed
   here: 2,961 ≈ caster armor, not the ~9.5k in-form value) so the multiplier is
   not double-applied.
3. **Guardian `_always_on_dr` branch** — a **baseline** all-school row (Thick Hide
   −4% × Bear Form −3% ≈ −6.9%) plus the −6% arcane refinement (school-aware
   follow-up). Warrior/Brewmaster branches stay bit-identical.
4. **`party_dr_by_school` parity wiring** on the Guardian path (replay opt-in),
   mirroring the warrior `mitigation.py:266-267`.
5. **Re-run + validate** — `scripts/calibrate_spec_from_wcl.py`, then the dual
   validator (both modes). **Keep `calibrated: false`** until deltas land within
   ±15% AND a **Druid-of-the-Claw** log confirms the build doesn't break the
   model (top-key meta is monolithically Elune's Chosen; DotC is unobserved here).

Stated up front so the re-run is an honest gate, not a fit: each layer is applied
at its **SimC-audited** value (not tuned to close the residual); the warrior path
stays bit-identical (the testable invariant). Expect Bear-Form-armor + flat-Agi
Ironfur to close the **bulk of the physical** half, and Thick-Hide/Bear-Form flat
DR + party-DR to close the **bulk of the magic** half; a residual from the
cross-spec thin-magic model may remain (the same one warrior and VDH carry).

## LANDED 2026-06-24 — SimC-grounded layers wired (physical closed, magic residual)

The recommended path was implemented on `feat/guardian-mitigation-layers`
(commit `093b2f3`), every value taken from the SimC midnight dump (NOT fitted to
the deltas), and re-validated against the **same three zone-47 fights** the
characterization used (re-discovered via `worldData.zone(id:47)` →
`characterRankings(className:"Druid", specName:"Guardian")`: Player 1, Player 2,
Player 3). **The armor formula + K=3430 + 0.85 cap were confirmed bit-correct against SimC
`composite_armor.cpp`** — only the Guardian spec layers were wrong/missing. What
changed (`core/character.py` + `classes/guardian_druid.py` + `data/constants.yaml`,
warrior path bit-identical):

| Layer | Was | Now (SimC 12.0.5) |
| --- | --- | --- |
| Bear Form base armor (5487) | absent; gear armor used a coincidental Ironfur ×2.125 pool-multiplier (6,292 effective) | **×3.2 `base_armor_multiplier` on gear armor** (permanent, not a CD — the VDH-Metamorphosis shape) |
| Ironfur (192081) | +75% of the *armor pool* per stack (wrong mechanic) | **+112% of Agility as FLAT bonus armor / stack × ~1.5 eff stacks**, added on top of Bear Form (SimC `composite_armor`: base×mult THEN +bonus) |
| Thick Hide × Bear Form passive | absent (`_always_on_dr` returned 1.0 for Guardian) | **−6.9% baseline all-school DR** in a new `_always_on_dr` Guardian branch (Defensive-Stance / Demonic-Wards analog) |
| `party_dr_by_school` | never read on the Guardian path | **wired (replay opt-in), `{all: 0.05, magic: 0.00}` — mirrors `protection_warrior`** |

Armor pipeline (verified by direct hydrate, matches the doc's predicted table to
the unit): gear **2,961 × 3.2 = 9,475**, **+ 2,656 agi × 1.12 × 1.5 = 4,462**,
total **13,937** → armor DR **80.3%** (was 64.7% at the old 6,292). `always_on_dr`
= (1−0.04)(1−0.03) = **0.9312**.

**Result — before → after (pre-absorb, the apples-to-apples mitigation
comparison) at canonical K=3430:**

| Fight | Before | After (decomposition) | After (MC sweep, 120 it) |
| --- | --- | --- | --- |
| Player 1 MgT +24 | +86.6% | **+19.8%** | +31.3% |
| Player 2 Mais +24 | +93.4% | **+13.1%** | +19.8% |
| Player 3 PoS +24 | +86.1% | **+10.6%** | +13.9% |

Canonical-K MC **RMSE 1.321 → 0.228**. The MC sweep reproduces the deterministic
per-school decomposition within iter/avoidance noise (same ordering, same band) —
the trust check holds: the sweep and the decomposition agree. The deltas land at
or near the ±15% bar (Player 2 + Player 3 within it pre-absorb; Player 1's
arcane-heavy Magisters' stream is the worst residual).

**Per-school (aggregate across all 3 fights, before → after):**

| school | base (M) | real mit | sim mit (was → now) | gap (was → now) |
| --- | --- | --- | --- | --- |
| physical | 3,756.0 | 85.2% | 69.5% → **84.1%** | +15.7pp → **+1.1pp** |
| shadow | 178.8 | 40.0% | 14.2% → 20.1% | +25.8pp → +19.9pp |
| arcane | 68.8 | 42.7% | 13.6% → 19.6% | +29.1pp → +23.1pp |
| frost | 39.8 | 22.2% | 10.8% → 16.9% | +11.4pp → +5.2pp |
| nature | 26.0 | 39.5% | 12.0% → 18.1% | +27.5pp → +21.4pp |
| bleed | 11.5 | 59.3% | 20.7% → 26.2% | +38.5pp → +33.1pp |
| fire | 8.3 | 42.0% | 13.2% → 19.2% | +28.8pp → +22.8pp |
| holy | 1.3 | 49.1% | 9.9% → 16.1% | +39.2pp → +33.0pp |

**Physical is closed** — +15.7pp → **+1.1pp** (real 85.2%, engine 84.1%), and
since physical is ~94% of all damage taken this is the bulk of the over-prediction
gone. The Bear-Form-armor + flat-Agi-Ironfur chain reproduces ~84% physical mit
vs SimC-predicted ~82% and real 85.2% — applied at SimC values, **not tuned**.

**Magic is still ~20–33pp short** across schools. The −6.9% baseline flat DR moved
every magic school up ~6pp (e.g. shadow 14.2% → 20.1%, frost 10.8% → 16.9%), but
the bulk of the magic residual remains: the **−6% arcane Bear-Form refinement is
deferred** (school-aware follow-up), `party_dr_by_school`'s `magic` slice is 0.00
(mirrors warrior), and the rest is the **cross-spec thin-magic model simf shares
with warrior and VDH**. This is exactly the predicted "physical closed, magic
residual" outcome. `specs.guardian_druid.calibrated` **stays `false`** — the deltas
are not all within ±15% (Player 1 pre-absorb +19.8%, MC +31.3%) AND the sampled
meta is monolithically **Elune's Chosen** (a Druid-of-the-Claw log is still
required before any flip).

### Dual-validator verdict (pre-merge, both modes)

Per `feedback_dual_validator_pre_merge_audit`, both validator modes audited the
change before merge:

- **Engine-math: PASS (clean).** Armor pipeline order correct — Bear Form ×3.2
  multiplies gear armor FIRST, flat-Agi Ironfur ADDED after (matches SimC
  `composite_armor`: `a *= composite_base_armor_multiplier(); a += bonus_armor()`).
  `_always_on_dr` Guardian branch correct (0.9312 = Thick Hide × Bear Form passive)
  and **applied exactly once** on the runtime chain (`guardian_druid.py:68`); the
  eHP/marginals path reuses the same method for its own *separate* flat-DR layer
  with **no double-count** (armor DR and flat DR are distinct multipliers).
  `party_dr_by_school` mirrors the warrior block byte-for-byte (`{all:0.05,
  magic:0.00}`) with no double-apply. **Warrior path BIT-IDENTICAL** — every
  changed line in `character.py` is fenced inside an `if/elif self.class_spec ==
  "guardian_druid"` guard; pinned by a new agility-invariance test
  (`test_warrior_path_bit_identical_after_guardian_change`). 8/8 guardian +
  invariance tests green.
- **Log-replay: PASS (reconstruction faithful).** Player 1's fight reconciles to the
  unit: WCL meta duration **1917s** = decomposition duration; **5,080 raw events =
  5,080 adapted** (no drop/dup); **pre-absorb total 207.0M** = decomposition real
  total; **0 self-inflicted events** leaked into the damage-taken stream; school
  mix sane (physical-dominant, arcane/shadow the magic, no bleed miscategorization;
  Player 2's bleed correctly bucketed at 11.5M). Replay is **deterministic**
  (`adapt_events` sets `is_avoidable=False`, absorbs from `log_absorbed`), so the
  cited per-school + post-absorb numbers are stable, not seed-noise. The magic
  over-prediction is a **real engine residual** (top magic hits take 16–22% DR vs
  reality's 35–49% with the absorb already added back), not a reconstruction bug.

Two honesty refinements carried from the original characterization still apply:
**anchor on the pre-absorb deltas** (+19.8/+13.1/+10.6%), not the absorb-inflated
post-absorb (+35.3/+24.7/+17.9%); and the decomposition is handed the **synthetic
healer `dr_cooldown` external**, so the engine-*only* residual is larger than
displayed (bracket with `--no-healer-ext`).

## Caveats

- A single WCL fight carries ~±20% player/build/key noise even for a calibrated
  spec (PR #142: warrior control +21% on one log). Three fights agree at
  +86–93% pre-absorb, well beyond that band → the gap is real, but the per-fight
  *magnitude* is characterization-grade, not ±1pp.
- The decomposition runs the engine's replay path including the **synthetic**
  healer `dr_cooldown` externals (so it reproduces the MC sweep) — these are the
  harness's approximation of real healer cooldowns, not this log's actual external
  DR. Same profile for every spec (incl. the warrior +21% control), so cross-spec
  comparison holds; but the **engine-only residual is larger than displayed**.
  Bracket with `--no-healer-ext`.
- **Anchor on pre-absorb over-prediction (+86–93%), not the absorb-inflated
  post-absorb +127–148%**: a fixed real absorb subtracted from the larger sim base
  inflates the post-absorb ratio.
- Layer magnitudes are from the SimC midnight SpellDataDump + Wowhead spell data,
  not in-game reads: Bear Form +220% Base Resistance (Physical) (5487), Thick Hide
  −4% all-school (16931), Bear Form passive −3%/−6% (1178), Ironfur +112% Agi/stack
  (192081). Wiring an unconfirmed number into replay mitigation is the
  manufactured-tuning-pressure trap; closure step-1 has now pinned each.
- All three logs are **Elune's Chosen** (Lunar Beam ~60% + Lycara's Teachings
  ~95% auras). **Druid of the Claw is uncharacterized**; its defensive profile
  (and any Ravage/Claw-specific DR) may differ, so the closure must be re-judged
  against a DotC log before `calibrated: true`.
- The COMBATANT_INFO snapshot is the **at-pull** stat block; Ironfur stacks /
  Barkskin / Incarnation are dynamic. The engine averages them; the buff-uptime
  pull (Ironfur ~89–92%, Barkskin ~26–29%, Incarnation ~25%, Bear Form ~95%)
  scopes *which* layers matter and confirms Ironfur is near-permanently up — so a
  high average-stack model is defensible, but the **mechanic** (flat-Agi, not
  pool-percent) still must be corrected.
