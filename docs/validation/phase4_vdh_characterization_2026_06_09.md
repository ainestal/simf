# Phase 4 — Vengeance DH over-prediction characterization (2026-06-09)

First real-log characterization of Vengeance Demon Hunter, the VDH analog of
the Brewmaster pass (`phase4_brewmaster_calibration_2026_06_07.md` +
`phase4_brewmaster_magic_layers_2026_06_07.md`). Source: **three public
Warcraft Logs Midnight (zone 47) M+ fights from three different players**,
pulled via the gear-certain PR #142 pipeline (`events(CombatantInfo)` → the
gear AND exact stats actually worn in the fight). The Brewmaster pass needed
local ACL `.txt` files we only had for two specs; this one uses WCL's public
corpus so VDH could be characterized at all.

**No engine code in this pass — this is the ratification/redirect artifact.**
`specs.vengeance_demon_hunter.calibrated` stays `false`.

Tooling (new, both committed): `scripts/calibrate_spec_from_wcl.py` (the WCL
analog of `calibrate_spec_from_logs.py` — multi-fight K-sweep judged at the
canonical K) and `scripts/per_school_gap_from_wcl.py` (the WCL analog of
`per_school_mitigation_gap.py` — per-school pre-absorb DR decomposition).

## TL;DR

- **Vengeance DH: NOT calibrated.** At the canonical global K=3430 the sim
  **over-predicts** damage taken by **+101.8% / +149.4% / +113.7%** across the
  three fights — far outside the ±15% Phase-4 bar, and **2–3× larger than
  Brewmaster's +27/+57%.** Best fit pins K at the sweep floor (2000), the
  textbook signature of the model under-mitigating. The gap is consistent
  across 3 players / 3 dungeons / 3 key levels → structural, not noise.
- **The gap is flat pre-absorb mitigation the VDH model lacks — both halves:**
  - **Magic: flat ~27–40pp across every school** (nature/fire/frost/shadow/
    arcane). Real magic mitigation ≈ 39–54%; the engine reaches ≈ 12–16% (and
    just **7.9%** spec-only — versatility + averaged Fiery Brand). Armor can't
    touch magic → this is a **missing flat magic-DR layer**, exactly the
    Brewmaster shape.
  - **Physical: ~17.5pp** even though the engine **over-credits** Demon Spikes
    (it refreshes at expiry−1 s → ~100% uptime vs a real ~50–60%). Largest
    *absolute* driver (physical is ~85% of all damage taken).
- **Structural root (verified):** `apply_mitigation` early-returns into
  `apply_vengeance_dh_mitigation`, which applies **only** armor + versatility +
  Demon Spikes + averaged Fiery Brand. It **never calls `_always_on_dr()`**
  (which has no VDH branch — returns 1.0) and **never applies
  `party_dr_by_school`**. The Prot Warrior path applies **both**. So VDH runs
  with **zero flat passive DR and zero party magic DR.**
- **Leading mechanism candidate (grounded):** **Demonic Wards** (spell 203513)
  — the baseline Vengeance passive, **flat all-school DR, buffed 8% → 12% in
  Midnight** (the stamina/armor halves moved to *Thick Skin* and are already in
  the COMBATANT_INFO armor snapshot). It is the clean analog of the warrior's
  Defensive Stance 15% all-school and is **completely unmodeled**. Unlike
  Brewmaster's Predictive Training, Demonic Wards is **baseline, not hero-talent
  gated** → a flat constant is the *correct* shape for VDH (it applies to every
  build), which is why the over-prediction is uniform across all three logs.
- **Consequence:** the VDH fix is a **baseline `_always_on_dr` VDH branch +
  party-DR wiring** (warrior-shaped), *not* a talent-gated ledger (Brewmaster-
  shaped). Demonic Wards' ~12% closes ~12pp on both halves; the remainder is the
  same multi-source residual (engine magic-mit thinness + armor magnitude) the
  warrior model carries. **Director re-consult before any engine work.**

## Method

1. **Discovery.** `worldData.zone(id:47)` (= Midnight Mythic+ Season 1; zone 45
   is TWW S3 — ~30× stats, mis-scales, never use) → 8 encounters. For each,
   `encounter.characterRankings(className:"DemonHunter", specName:"Vengeance")`
   yields rows carrying `report.code` + `report.fightID` + `bracketData`
   (key level) + the player. Deduped to 188 distinct VDH characters, probed the
   most-recent timed runs for ACL (CombatantInfo present), a VDH-tank pick, sane
   hydrated stats, and hero tree (from in-fight buff auras).
2. **Calibration sweep** (`calibrate_spec_from_wcl.py`): build the gear-certain
   `Character` (`character_from_wcl`) + the replay (`wcl_to_replay_data`) for
   each fight, sweep K, **judge every per-run delta at the canonical global
   K=3430**. A best-K far below canonical is a spec mitigation gap, not a
   license to set a per-spec K.
3. **Per-school decomposition** (`per_school_gap_from_wcl.py`): run the **actual
   engine replay loop** (`policy.tick`/`policy.decide`/`apply_mitigation`, incl.
   the healer `dr_cooldown` externals `run_simulation` applies) and bucket each
   non-self event by school into base / real-pre-absorb (`amount + absorbed`) /
   sim-pre-absorb (`dealt` + the absorb the engine subtracted). The post-absorb
   total reproduces the Monte-Carlo sweep delta **exactly** on all three fights
   (+101.8/+149.4/+113.7%), so the per-school split is trustworthy.

## The three fights (gear-certain, sanity-checked)

| Tank | Dungeon | Key | armor | HP | ilvl | Hero tree (in-log) | Δ at K=3430 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AnonPlayerX1 (US) | Windrunner Spire | +19 | 3,273 | 803k | 290 | Fel-Scarred | **+101.8%** |
| AnonPlayerX3 (US) | Skyreach | +20 | 3,355 | 799k | 258 | Fel-Scarred | **+149.4%** |
| AnonPlayerX2 (EU) | Pit of Saron | +21 | 4,108 | 796k | 290 | Fel-Scarred | **+113.7%** |

Stats are plausible for Midnight leather tanks (armor 3.3–4.1k, HP ~800k, no
shield → no `shield_armor` hydrate hazard) — the positive-control instinct says
these deltas are a model gap, not a pipeline artifact. **All three are
Fel-Scarred** (Soul Barrier + Untethered Rage/Seething Anger auras; **zero**
Aldrachi-Reaver glaive auras). The high-key VDH meta is monolithic, so this is a
**replication** across 3 players/dungeons/keys — not the two-build *contrast*
the Brewmaster pass got. **Aldrachi Reaver is unobserved at the top and its
mitigation profile is uncharacterized** (follow-up).

## Sweep (iters=250, seed=42)

```
  K= 2000: RMSE=0.729  [+54.6%, +95.6%, +61.8%]  <-best
  K= 2500: RMSE=0.931  [+73.7%, +117.4%, +82.4%]
  K= 3000: RMSE=1.104  [+89.9%, +135.7%, +100.3%]
  K= 3430: RMSE=1.233  [+101.8%, +149.4%, +113.7%]  [CANONICAL]
  K= 3500: RMSE=1.253  [+103.6%, +151.4%, +115.8%]
  K= 4000: RMSE=1.382  [+115.4%, +165.0%, +129.4%]
CANONICAL K=3430: RMSE=1.2329   Best K=2000 (RMSE=0.7291)
VERDICT: all per-run deltas within ±15% at canonical K? False
```

Even the sweep floor (K=2000) leaves +55–96% over-prediction — the missing
mitigation is **not armor K**; it is flat DR layers below the armor formula.
(The per-run numbers are independently confirmed through the *shipped*
single-log `simf calibrate-k --wcl-url` path — AnonPlayerX1 +101.8% and
AnonPlayerX3 +149.4% both reproduce exactly — so they are not an artifact of
the new multi-fight wrapper.)

## Per-school decomposition (aggregate across all 3 fights, M = 10⁶)

| school | base (M) | real mit% | sim mit% | gap |
| --- | --- | --- | --- | --- |
| physical | 1839.8 | 83.7% | 66.1% | **+17.5pp** |
| fire | 72.0 | 53.8% | 13.8% | **+40.0pp** |
| arcane | 1.9 | 53.4% | 16.1% | +37.3pp |
| nature | 48.0 | 51.6% | 14.8% | +36.8pp |
| shadow | 36.3 | 48.0% | 13.6% | +34.4pp |
| frost | 30.5 | 38.9% | 11.9% | +27.0pp |

(Per-fight tables in the run output; each fight individually shows the same
flat-magic / physical-shortfall shape. The `--no-healer-ext` view collapses
every magic school's sim mit to a clean **flat 7.9%** = versatility + averaged
Fiery Brand only — the cleanest isolation of the missing flat magic DR.)

### Reading the two halves

**Magic (airtight).** Real magic mitigation sits at ~39–54% across five schools
— roughly flat and school-independent — while the engine gives only versatility
(~1%) + averaged Fiery Brand (6.8%) + an intermittent healer-DR window. The
missing ~30–40pp is armor-immune and school-flat → **flat magic-DR layers the
VDH model lacks entirely.** The closure section identifies them: **Painbringer**
(all-school, ~95% uptime), **Demonic Wards** (baseline), and the **party magic
auras** (Devotion Aura ~98%, Elemental Resistance ~72%) the VDH path never reads.
This is the dominant *relative* miss (the engine mitigates less than a third of
what reality does on magic).

**Physical (armor-magnitude — and the source is now identified: Metamorphosis).**
The engine reaches 66.1% physical mitigation: armor (~48% at 3.3k) × versatility
× Demon Spikes (20% at ~100% uptime — which is *correct*; buff-uptime data below
shows real Demon Spikes uptime is **99.3–99.5%**, so the engine does NOT
over-credit it as an earlier draft claimed) × averaged Fiery Brand. Real physical
mitigation is 83.7% — the engine lets through **33.9% of base vs reality's 16.3%,
a ~2.1× over-prediction** and the largest *absolute* gap (+95–116M per fight;
physical is ~85% of all damage). Backing out the effective armor that would
reproduce 81–84% physical mit with the other layers unchanged gives **~9,300 vs
the ~3,300 snapshot (≈2.9×)**.

That ≈2.9× is **not** unexplained: **Metamorphosis (spell 187827) grants
"Mod Base Resistance (Physical) +200%" — it triples armor — and the three tanks
hold it at ~50% uptime** (Fel-Scarred resets it via Demonsurge). The engine
models Metamorphosis as a *rare <35%-HP emergency that grants +30% max HP* and
**never credits its +200% armor or its real uptime.** Tripling 3,273 → ~9,819
for half the fight time-averages to ~9,300 effective armor — i.e. essentially the
whole physical gap. So the physical half is **armor-magnitude after all (the
Brewmaster-style "candidate, source TBD" was the right caution), with a specific
verified cause** — not a flat all-school DR. Demonic Wards adds a secondary
physical slice. (See the closure section.)

## Structural root (verified in code, not inferred)

- `core/mitigation.py:156-159` — `apply_mitigation` early-returns into
  `classes/vengeance_dh.py:apply_vengeance_dh_mitigation` for VDH.
- That function applies, in order: avoidance (off in replay) → armor (physical)
  → versatility → Demon Spikes (physical) → averaged Fiery Brand (all) → healer
  DR → absorb subtract. **It never calls `char._always_on_dr()`**, and
  `Character._always_on_dr()` (`core/character.py:329`) has **no VDH branch**
  (returns 1.0). **It never applies `party_dr_by_school`** (the warrior path
  does, `mitigation.py:266`).
- Net: VDH runs with **zero flat passive DR and zero party magic DR.** The Prot
  Warrior path has Defensive Stance 15% all-school (`_always_on_dr`) **and**
  `party_dr_by_school`; VDH has neither. That asymmetry is the bulk of the
  +102–149%.

## How to close the gap — the missing layers, from buff uptimes

The gap is **not one constant** — it is a *stack* of high-uptime defensive
layers the engine models little or none of. Buff-uptime pull (WCL `Buffs` table)
for the three tanks, cross-referenced with the verified spell tooltips:

| Layer (spell) | Real uptime | Engine today | What it actually does | Closes |
| --- | --- | --- | --- | --- |
| **Metamorphosis** (187827) | **~47–52%** | rare <35%-HP emergency, +30% HP only | **+200% Base Resistance (Physical) = ×3 armor** + 40% HP + leech/vers | **most of physical** |
| **Painbringer** (212988) | **~95%** | nothing | **Mod % Damage Taken (all 7 schools)** — flat all-school DR, Soul-Fragment-refreshed | big magic + physical |
| **Demonic Wards** (203513) | 100% passive | nothing | **8% all-school + 8% additional physical** (Wowhead DBC; a Midnight 8→12 buff is on PTR — confirm) | ~8pp magic, ~15pp phys |
| **Party magic auras** (Devotion Aura 465 ~98%, Elemental Resistance ~72%, Arcanoweave ~70%) | high | **nothing on VDH path** (`party_dr_by_school` parity gap) | magic |
| **Fiery Brand** (207771) | **~35–42%** | avg DR at uptime **0.17** | target-side 40% DR — modeled uptime is ~½ the real | physical/all |
| **Seething Anger** (1270547, Fel-Scarred) | ~91% | nothing | verify DR vs damage-done | TBD |
| **Demon Spikes** (203819) | 99.3–99.5% | ~100%, 20% phys | uptime is **correct**; confirm Midnight magnitude | (already in) |

**The two big, verified levers:**

1. **Metamorphosis is the physical answer.** Its **+200% armor at ~50% uptime**
   time-averages to ~9,300 effective armor — essentially the entire ≈2.9× armor
   discrepancy backed out above. The engine treats Metamorphosis as a 240 s
   emergency HP button; in the Fel-Scarred Midnight meta it is a maintained
   ~50%-uptime armor cooldown. **Modeling its armor + real uptime is the single
   highest-leverage fix**, and it is a *mechanic/policy* fix, not a constant.
2. **Painbringer is the magic answer (with Demonic Wards + party auras).** A
   ~95%-uptime flat all-school DR the engine has no concept of. With Demonic
   Wards (baseline) and the party magic auras (parity), this is the bulk of the
   ~30–40pp magic gap; the remainder is the long-standing cross-spec thin-magic
   model (shared with warrior).

## Recommended path (Director-scoped, ratified, multi-step — NOT a one-shot)

This is real engine work and it touches the validation-critical replay path, so
it follows `feedback_engine_batch_ratification` + `feedback_dual_validator_pre_merge_audit`:

1. **Pin the values** (read-only, no ratification): per-buff DR-attribution audit
   — Painbringer % per stack + stack count, Metamorphosis armor multiplier, the
   party-aura DR%, Seething Anger (DR or damage?). We have the uptimes; this
   pins the magnitudes so each ledger row is auditable, not fitted.
2. **Metamorphosis mechanic fix** — credit its +200% armor at the policy's real
   maintained uptime (Fel-Scarred), not just the emergency HP. Highest leverage.
3. **VDH `_always_on_dr` + buff-gated `mitigation_ledger`** — VDH is a *hybrid*
   of the warrior and Brewmaster shapes: a **baseline** `_always_on_dr` row
   (Demonic Wards, value pending confirm) **plus** a **buff-gated ledger**
   (Painbringer; Brewmaster's `active_buff_spell_ids` machinery, gate
   on 212988) **plus** `party_dr_by_school` parity wiring + a Fiery-Brand uptime
   fix.
4. **Re-run + validate** — `scripts/calibrate_spec_from_wcl.py`, then the dual
   validator. **Keep `calibrated: false`** until deltas land within ±15% AND an
   **Aldrachi Reaver** log confirms the build doesn't break the model (top-key
   meta is monolithically Fel-Scarred; AR is unobserved here).

Stated up front so the re-run is an honest gate, not a fit: each layer is applied
at its **audited** value (not tuned to close the residual); the warrior path
stays bit-identical (the testable invariant). Expect Metamorphosis-armor +
Painbringer + Demonic Wards + party-DR to close the **bulk** of both halves; a
residual from the cross-spec thin-magic model may remain.

## LANDED 2026-06-09 — SimC-grounded layers wired (physical closed, magic residual)

A SimC source audit (midnight branch, build **12.0.5.67823**) pinned the values,
and the engine now models them. **The armor formula + K=3430 + 0.85 cap were
confirmed bit-correct against SimC** — only the spec layers were wrong/missing.
What changed (`classes/vengeance_dh.py` + `data/constants.yaml`, warrior path
bit-identical):

| Layer | Was | Now (SimC 12.0.5.67823) |
| --- | --- | --- |
| Metamorphosis | +30% HP, 240s CD, 50% leech, **no armor** | **×3.0 base+gear armor** (window-gated on buff 187827), +40% HP, 120s CD, 0 leech |
| Demon Spikes | flat **20% physical DR** (phantom) | **0 flat DR**; **+75% of Agility as armor**; +8% parry; 12s |
| Demonic Wards | absent | **−12% all-school** always-on (Defensive-Stance analog) |
| Fiery Brand | 0.40 × 0.17 | unchanged (SimC value 0.40 confirmed) |

Architecture: Metamorphosis' armor is **window-gated** via a new per-event
`DamageEvent.active_buffs` (the time-windowed evolution of Brewmaster's static
`active_buff_spell_ids`), stamped by `adapt_events` from
`wcl_api.fetch_buff_windows` — averaging a non-linear armor multiplier over its
~50% uptime would mis-state DR. Demon Spikes is read from policy state (~100%
uptime in both paths).

**Result (per-school decomposition, reproduces the MC sweep):**

| Log | Before | After |
| --- | --- | --- |
| AnonPlayerX1 +19 | +101.8% | **+43.6%** |
| AnonPlayerX3 +20 | +149.4% | **+73.0%** |
| AnonPlayerX2 +21 | +113.7% | **+43.1%** |

Canonical-K RMSE **1.233 → 0.551**; best-K still pins at the sweep floor (the
magic residual pulls it below canonical), but at K=2000 two of three logs are now
**within ±15%** ([+4.5%, +30.7%, +10.4%]) — only the fire-heavy AnonPlayerX3
(Skyreach) stays out, exactly where the unmodeled magic layer bites hardest.

| school | real mit | sim mit (was → now) | gap (was → now) |
| --- | --- | --- | --- |
| physical | 83.7% | 66.1% → **77.2%** | +17.5pp → **+6.4pp** |
| magic (avg) | ~48% | ~14% → **~24%** | ~34pp → **~24pp** |

**Physical is largely closed** (+6.4pp residual = Painbringer's all-school slice +
Calcified Spikes + the doubled Fel-Blood-mastery armor during Demon Spikes, all
unmodeled). **Magic is still ~24pp short** — Demonic Wards' −12% helped, but the
bulk is **Painbringer (deliberately unmodeled — SimC live effect base reads 0)**
+ the party magic auras (`party_dr_by_school` parity, off in the replay harness)
+ the cross-spec thin-magic model simf shares with every spec. This is exactly
the predicted "physical closed, magic residual" outcome — applied at SimC values,
**not tuned**. `specs.vengeance_demon_hunter.calibrated` **stays `false`** (still
outside ±15%, and the magic residual + an Aldrachi Reaver build are unaddressed).

**Deliberately deferred** (each gated, not fudged): Painbringer (needs a confirmed
per-stack value), `party_dr_by_school` VDH wiring, the live-path Metamorphosis
uptime model (the replay credits real log windows; the live verdict still only
gets Meta on the policy's emergency — same calibration-path-first staging as
Brewmaster PT), and Demon Spikes' doubled Fel-Blood-mastery armor.

### Dual-validator verdict (pre-merge, both modes)

Per the engine-batch + dual-validator discipline, both validator modes audited the
change before merge:

- **Engine-math: CLEAN.** Armor pipeline order correct (Meta ×3 multiplies the
  snapshot, DS agi-armor added after — matches SimC's `composite_armor`); Demonic
  Wards −12% applied all-school; phantom DS DR fully removed; window-gating has no
  double-count (Meta from `active_buffs` in replay, policy state in live); **warrior
  path bit-identical** (new `active_buffs` defaults empty). Every value matches the
  SimC audit.
- **Log-replay: reconstruction FAITHFUL.** Every WCL-verifiable quantity reconciles
  — real per-school mit to the decimal (physical 81.3%, total 78.7%), duration,
  Metamorphosis uptime (**43.5%**, off the actual buff bands), dtps (62,484≈62,483),
  absorb, zero self-damage; no offset/double-credit/bleed defects. The magic
  over-prediction is a **real engine residual**: top magic hits take 38–64% DR with
  *zero absorb* → reality genuinely mitigates ~46–53%, sim ~25% — the Painbringer
  gap, not a reconstruction bug.

Two honesty refinements from the log-replay pass (folded into the caveats below):
**anchor on pre-absorb +35.7%** (AnonPlayerX1), not the absorb-inflated +43.6%; and the
sim is handed the **synthetic healer `dr_cooldown` external**, so the engine-*only*
residual is larger than displayed (the displayed magnitude is optimistic).

## Caveats

- Single WCL fight carries ~±20% player/build/key noise even for a calibrated
  spec (PR #142: warrior control +21% on one log). Three fights agree at
  +102–149%, well beyond that band → the gap is real, but the per-fight
  *magnitude* is characterization-grade, not ±1pp.
- The decomposition runs the engine's replay path including the **synthetic**
  healer `dr_cooldown` externals (so it reproduces the MC sweep) — these are the
  harness's approximation of real healer cooldowns, not this log's actual external
  DR. Same profile for every spec (incl. the warrior +21% control), so cross-spec
  comparison holds; but it means the **engine-only residual is larger than the
  displayed +43–73%** (the synthetic DR makes the magnitude optimistic). Bracket
  with `--no-healer-ext`.
- **Anchor on pre-absorb over-prediction (+35.7% on AnonPlayerX1), not the
  absorb-inflated +43.6%**: a fixed ~20M real absorb subtracted from the larger
  sim base inflates the post-absorb ratio. The pre-absorb number is the
  apples-to-apples mitigation comparison.
- Demon Spikes is credited from the policy's ~100%-uptime model in replay (its log
  windows are fetched but the engine reads the policy slot). Measured real DS
  uptime on this corpus is **99.3–99.5%**, so the approximation is accurate here —
  but it would over-credit a lower-uptime build (e.g. a less-active player or a
  different hero spec); window-gating DS off `active_buffs` is ready if needed.
- Layer magnitudes are from Wowhead DBC + tooltips, not in-game reads: Demonic
  Wards 8% all + 8% phys (a Midnight 8→12 buff is on PTR), Metamorphosis +200%
  Base Resistance (Physical), Painbringer all-school (per-stack % not yet pinned).
  The closure step-1 audit pins each before the engine PR — wiring an unconfirmed
  number into replay mitigation is the manufactured-tuning-pressure trap.
- All three logs are Fel-Scarred (Metamorphosis ~50% uptime is a Fel-Scarred
  pattern via Demonsurge). Aldrachi Reaver is uncharacterized; its Metamorphosis
  uptime and DR layers may differ, so the closure must be re-judged against an AR
  log before `calibrated: true`.
- Buff uptimes (Painbringer ~95%, Metamorphosis ~50%, Demon Spikes ~99%, Devotion
  Aura ~98%) are damage-weighted WCL `Buffs`-table totals for the three tanks;
  they scope *which* layers matter, not the exact DR each contributes.
