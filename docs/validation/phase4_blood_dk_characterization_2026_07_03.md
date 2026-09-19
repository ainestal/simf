# Phase 4 — Blood Death Knight first characterization (2026-07-03/04)

First real-log characterization of Blood Death Knight — the last of the six
tank specs to have any data pointed at it. Everything in
`specs.blood_death_knight` was explicitly PLACEHOLDER ("calibration against
real Blood DK logs is pending"). Source: **five public Warcraft Logs Midnight
(zone 47) M+ fights from four different players**, pulled via the gear-certain
PR #142 pipeline (`events(CombatantInfo)` → the gear AND exact stats worn in
the fight). Same method as the VDH pass
(`phase4_vdh_characterization_2026_06_09.md`); same guardrails as the
Brewmaster pass (named layers with citations, no fudge constants).

Unlike the VDH pass, this one ships engine code in the same change: the
placeholder Bone Shield *mechanism* (flat 3%/stack physical DR) does not exist
in Midnight, and replacing it with the real, SimC-pinned mechanic is a
mechanism correction, not a tuned constant.
`specs.blood_death_knight.calibrated` **stays `false`** (first pass, one hero
build observed, deferred layers below).

## TL;DR

- **Baseline (placeholder model): over-predicts damage taken on all 5 fights**
  — MC sweep at canonical K=3430: **+37.2% to +61.4%, RMSE 0.483**, best-K
  pinned at the sweep floor; pre-absorb +19.1% to +32.8%. One direction, all
  players, all dungeons → structural, not noise. Smaller than VDH's +102–149%
  baseline, bigger than the warrior's calibrated ±5%.
- **The dominant miss was Bone Shield's *mechanism*.** The placeholder modeled
  ~21% flat physical DR (3%/stack × avg 7 stacks). Real Midnight Bone Shield
  (spell 195181) is **bonus armor = 180% of Strength while ≥1 charge** — armor,
  not flat DR; stack-count-independent (SimC parses the buff `IGNORE_STACKS`;
  charges are consumed by auto-attacks). At corpus stats (armor ~2.87k, str
  ~1.9–2.4k) the real mechanic is worth ~35% physical where the placeholder
  gave 21% — the engine under-mitigated physical by +8.2pp aggregate.
- **Second missing layer: Rune Carved Plates** (Deathbringer hero talent) —
  1.5%/stack (max 5) less physical damage per rune *generated* and less magic
  damage per rune *spent*, 5s windows. Measured on the corpus: up 73–86%
  (physical) / 79–83% (magical), damage-weighted 2.91–3.84 stacks (constants
  use the rounded high end, 3.5) → ~3–5%
  effective per school. **All five corpus tanks are Deathbringer** (the M+
  meta) — the layer is buff-gated per log, so a San'layn build gets no credit
  (the honest failure mode, per the Brewmaster PT precedent).
- **New pipeline finding: Bone Shield pollutes the COMBATANT_INFO armor
  snapshot** — the same class of bug as ProtPal's Shield-of-the-Righteous
  snapshot pollution (PR #260), but on the WCL path where there is only ONE
  snapshot per fight (no min-across-snapshots fix available). 2 of 7 probed
  fights had Bone Shield up at the fight-start snapshot → armor ~6.6k instead
  of ~2.87k (×2.29). Corpus selection screened these out; a detector is a
  named follow-up.
- **After the mechanism fixes** (all values cited, zero fitted): MC canonical-K
  deltas move from [+57.5, +38.8, +37.2, +41.0, +61.4]% to
  [**+11.6, −1.9, −6.8, −6.4, +30.5**]% — **RMSE 0.483 → 0.152, 4/5 within
  ±15%**. Pre-absorb over-prediction moves from [+22.9, +19.1, +20.1, +24.7,
  +32.8]% to [**+1.7, −3.9, −5.2, −5.0, +17.2**]%; the aggregate physical gap
  closes from +8.2pp to **−1.0pp**. The remaining outlier (Player 1,
  Nexus-Point) decomposes into named causes: a **mis-classified "bleed"**
  (Searing Rend — a false-positive of the `"Rend"` name fragment; its real
  72% mitigation proves it is armor-affected, ~+15.5M of their +51M pre-absorb
  gap) and the deferred `party_dr` parity gap (Devotion Aura at 97% uptime on
  their group, ~+13M). **`calibrated` stays `false`**: sweep verdict is
  formally False (one fight out of band), single hero build observed,
  absorb-inflation caveat, and the dual-validator audit is pending. This is a
  characterization milestone, not a finished calibration.

## Method

1. **Discovery.** `worldData.zone(id: 47)` (Midnight M+ Season 1 — zone 45 is
   TWW, mis-scales ~30×, never use) → 8 encounters. For each,
   `encounter.characterRankings(className: "DeathKnight", specName: "Blood")`
   page 1. Report codes are present on most rows (~80%; a minority are
   redacted/anonymized — no Playwright scraping needed this time). 650 rows,
   190 distinct players. Candidates probed for ACL (CombatantInfo present),
   sane hydrated stats, and armor-snapshot cleanliness (below).
2. **Calibration sweep** (`scripts/calibrate_spec_from_wcl.py`): gear-certain
   `Character` (`character_from_wcl`) + replay (`wcl_to_replay_data`) per
   fight, judged **at the canonical global K=3430**. Operational note: the
   Pi (1.8 GiB, earlyoom) silently killed the first 6-K × 5-fight × 300-iter
   attempt (empty output, masked exit code) — the final tables come from
   uncontended single-process runs monitored to completion; treat silent
   empty sweep output as a kill, not a pass.
3. **Per-school decomposition** (`scripts/per_school_gap_from_wcl.py`,
   spec-generic): actual engine replay loop, buckets base / real-pre-absorb /
   sim-pre-absorb per school.
4. **Buff forensics** (new for this pass): Bone Shield / RCP stack time series
   reconstructed from WCL `Buffs` events (`applybuffstack` / `removebuffstack`
   carry the running stack count), then damage-weighted against the fight's
   damage stream.

## The five fights (gear-certain, sanity-checked, snapshot-clean)

| Tank | Region | Dungeon | Key | armor | str | HP | ilvl | real DTPS | events |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Player 2 | US | Algeth'ar Academy | +22 | 2,893 | 1,953 | 880k | 273 | 67,797 | 2,925 |
| Player 3 | EU | Windrunner Spire | +22 | 2,879 | 1,946 | 882k | 257 | 92,546 | 3,749 |
| Player 4 | EU | Skyreach | +20 | 2,846 | 2,306 | 862k | 255 | 71,693 | 2,463 |
| Player 4 | EU | Pit of Saron | +22 | 2,893 | 2,400 | 879k | 257 | 118,801 | 4,473 |
| Player 1 | US | Nexus-Point Xenas | +22 | 2,863 | 1,814 | 868k | 256 | 86,130 | 2,891 |

4 players, 5 dungeons, 2 regions, keys +20–+22. All five armor snapshots sit
in a tight 2,846–2,893 band — the unbuffed plate baseline at this gear level —
and none carries the Ossuary marker aura at snapshot time (below). **All five
are Deathbringer** (Rune Carved Plates auras present in-fight; zero San'layn
markers) — a replication of one hero build, not a build contrast. San'layn is
unobserved, exactly like Aldrachi Reaver in the VDH pass.

## Finding: Bone Shield pollutes the WCL armor snapshot (2 of 7 probed fights)

While assembling the corpus, the same player (Player 3) showed armor **2,879**
in one fight and **6,591** (×2.29) in another; a second player (Player 5,
Maisara +21) showed 6,604. The polluted snapshots carry
**Ossuary** (219788, the "Bone Shield ≥5 charges" marker) active at the pull
in their buff streams — i.e. Bone Shield (armor = 180% of Strength, ~+3.5k at
these stats) was up when COMBATANT_INFO snapshotted. This is the ProtPal
F15/PR #260 hydrate-pollution bug shape on the WCL path — but WCL has **one
snapshot per fight**, so the local-log min-observed-armor fix cannot apply.

Consequences:

- **Corpus selection here screened it out** (all five headline fights are
  clean; the two polluted fights are excluded, kept as diagnostics).
- With the new Bone Shield armor model, a polluted snapshot would
  **double-count** (~+3.5k armor in the snapshot AND +1.8×str from the model).
- **Named follow-up:** a mechanical detector is possible — the Bone Shield
  buff windows are already fetched for the replay; if a window covers the
  CombatantInfo timestamp, subtract `1.8 × strength` from the snapshot armor.
  Not wired in this pass (touches the shared WCL import path; engine-batch
  discipline). Until then, `calibrate_spec_from_wcl` users must eyeball the
  armor sanity line.

## Baseline sweep (placeholder model, iters=300, seed=42)

```
  K= 2000: RMSE=0.145  [+11.3%, -3.6%, -1.2%, +0.6%, +30.1%]  <-best
  K= 2500: RMSE=0.260  [+31.0%, +12.6%, +13.2%, +16.7%, +42.5%]
  K= 3000: RMSE=0.386  [+46.1%, +27.6%, +27.8%, +30.6%, +53.4%]
  K= 3430: RMSE=0.483  [+57.5%, +38.8%, +37.2%, +41.0%, +61.4%]  [CANONICAL]
  K= 3500: RMSE=0.497  [+59.2%, +40.5%, +38.6%, +42.4%, +62.5%]
  K= 4000: RMSE=0.595  [+70.6%, +51.6%, +47.8%, +52.7%, +70.5%]
CANONICAL K=3430: RMSE=0.4826   Best K=2000 (RMSE=0.1447)
VERDICT: all per-run deltas within +/-15% at canonical K? False
```

Fight order: Player 2, Player 3, Player 4 Skyreach, Player 4 PoS, Player 1.
**Canonical-K over-prediction +37.2% to +61.4%, RMSE 0.483.** Best-K pins at
the sweep floor — the textbook under-mitigation signature (same as
VDH/Brewmaster). Note the trap the canonical-anchor discipline avoids: at a
self-fit K=2000 the *placeholder* model would have looked nearly calibrated
(4/5 within ±12%) — a wrong mechanism hidden by a wrong constant, exactly the
K=2700 self-fit lesson.

(The Monte-Carlo canonical deltas sit ~3pp below the deterministic
decomposition's post-absorb figures below — policy-path jitter (HP-gated
Icebound/Vampiric-Blood presses under the stochastic healing profile); both
are real reads of the same gap.)

## Per-school decomposition (baseline, aggregate across 5 fights)

| school | base (M) | real mit% | sim mit% | gap |
| --- | --- | --- | --- | --- |
| physical | 3518.7 | 69.8% | 61.6% | **+8.2pp** |
| bleed | 26.2 | 72.3% | 23.9% | +48.3pp (anomaly, see caveats) |
| shadow | 105.9 | 18.0% | 6.1% | +11.9pp |
| fire | 77.9 | 18.7% | 11.7% | +6.9pp |
| frost | 39.2 | 12.1% | 7.4% | +4.7pp |
| arcane | 149.6 | 23.6% | 11.2% | +12.4pp |
| nature | 84.6 | 23.2% | 12.7% | +10.5pp |
| holy | 45.5 | 19.8% | 4.8% | +15.0pp |

Physical is ~87% of all base damage, so the +8.2pp physical gap dominates the
absolute error. Per-fight pre-absorb over-prediction: +22.9% / +19.1% /
+20.1% / +24.7% / +32.8% (deterministic post-absorb: +60.9% / +42.0% /
+40.3% / +43.2% / +66.2%). Post-absorb deltas are larger than pre-absorb
because the log's fixed absorb subtracts from a bigger sim base — anchor
mitigation reasoning on pre-absorb (VDH-pass lesson).

**Reading the physical half.** The real chain back-solves almost exactly:
armor with Bone Shield up (2,879 + 1.80×1,946 ≈ 6,382 → 65.0% at K=3430) ×
versatility × RCP-physical (~4%) × party auras ≈ 69–70% — i.e. the real ~69.8%
physical mitigation **is** the Bone-Shield-as-armor chain. The placeholder's
21% flat (× base armor's 45.6%) reached only 61.6%.

**Reading the magic half.** Sim magic mitigation was versatility + healer
externals only (5–18%); real is 12–24%. The gap decomposes into RCP-magical
(~4–5% where up) + party magic auras (Devotion Aura present on 2 of 5 groups
at 94–97% uptime; `party_dr_by_school` is not applied on the Blood path —
same parity gap VDH #143 deferred) + the cross-spec thin-magic residual every
spec carries. No VDH-style giant flat all-school passive is missing: Blood has
no Demonic-Wards analog, and the magic gap (~5–15pp) is correspondingly small.

## Bone Shield deep-dive (the averaged-vs-real-stacks question)

The module's standing TODO asked whether replay should read real stack counts
instead of the `avg_stacks × dr_per_stack` flat. The stack time series
(reconstructed from WCL `applybuffstack`/`removebuffstack` events) answers it:

| Fight | avg stacks (time-w) | uptime ≥1 | uptime ≥5 | dmg-weighted ≥1 |
| --- | --- | --- | --- | --- |
| Player 2 Algeth'ar +22 | 9.98 | 97.4% | 94.2% | 99.8% |
| Player 3 Windrunner +22 | 10.06 | 99.1% | 96.7% | 99.6% |
| Player 4 Skyreach +20 | 10.44 | 97.9% | 96.6% | 100.0% |
| Player 4 Pit of Saron +22 | 10.22 | 99.8% | 98.8% | 100.0% |
| Player 1 Nexus +22 | 10.59 | 99.9% | 97.0% | 100.0% |

Two conclusions:

1. **The stack count is the wrong question.** Under the real Midnight
   mechanic the armor is stack-count-independent (SimC `IGNORE_STACKS`;
   charges ≠ intensity). The load-bearing quantity is *uptime of ≥1 charge*,
   which is 99.6–100% damage-weighted. The old `avg_stacks: 7` was doubly
   wrong (real average is ~10, and it doesn't matter).
2. **Replay is still window-gated** on the log's real 195181 windows (the
   0.1–2.6% down-windows are real, typically the opening seconds of a pull
   after a full consume), because the machinery is free — the `*_spell_id`
   constants flow through `fetch_buff_windows` → `event.active_buffs` exactly
   like VDH Metamorphosis. The synthetic path treats it as always-on, which
   the measured uptime supports (and matches the old model's assumption).

Per-stack effects that DO scale with stacks (Foul Bulwark's max-HP %/stack,
if talented) are HP-side, don't enter DTPS, and are a named follow-up.

## Named layers (citations, not fits)

| Layer | Status | Value | Source |
| --- | --- | --- | --- |
| **Bone Shield** (195181) | **modeled (this pass)** | bonus armor = **180% of Strength** while ≥1 charge; window-gated in replay, always-on synthetic | SimC midnight `sc_death_knight.cpp` `composite_bonus_armor()`: `ba += buffs.bone_shield->value() * cache.strength()`; buff built `set_default_value_from_effect_type(A_MOD_ARMOR_BY_PRIMARY_STAT_PCT)`; Wowhead 195181 effect #1 "Mod Armor From Stat % Value: 180%" |
| **Rune Carved Plates** (talent 440282; buffs 440289 phys / 440290 magic) | **modeled (this pass, replay-only)** | −1.5%/stack (max 5) per school; presence-gated × measured damage-weighted avg stacks while up (3.5; range 2.91–3.84 over 10 fight×school measurements) | SimC midnight `resource_gain`/`resource_loss` triggers + buff `effectN(1).base_value()/1000`; warcraft.wiki + 11.0.5 patch note ("1.5% per stack, was 2%") |
| **Icebound Fortitude CD** (48792) | **fixed (this pass)** | 180s → **120s** (DR 30%/8s unchanged) | Wowhead live: "2 minutes"; resolves internal contradiction with the coaching registry's `cooldown_s: 120` in the same constants file |
| Will of the Necropolis (206967) | no action needed (replay) | DR below 30% HP is implemented as an **absorb** effect → already inside `log_absorbed` | Wowhead 206967 effect #1 "Absorb Damage (all schools)" |
| Anti-Magic Shell / Blood Shield / AMZ | already handled | logged as absorbs → `log_absorbed` | log semantics |
| Dancing Rune Weapon (49028/81256) | not modeled, deliberate | +parry — avoidance; avoided hits never appear in a replay damage stream. 41–43% observed uptime (Midnight Dance-of-Midnight procs re-summon it) | corpus buff windows |
| Vampiric Blood | HP-side only | 18–36% real uptime (>90s-CD-implied — Red-Thirst-style CDR unmodeled), but heal/HP-side: no DTPS effect | corpus buff windows |
| party_dr_by_school | **deferred** (parity gap) | Devotion Aura 94–97% uptime on 2/5 groups | same deferral as VDH #143 |
| Blood Fortification / Veteran of the Third War / Improved Bone Shield | no action (snapshot) | always-on armor/stamina passives are already inside the COMBATANT_INFO armor/stamina snapshot — re-applying would double-count (the VDH Thick-Skin lesson) | SimC declarations + snapshot semantics |

## LANDED (this pass) — mechanism fixes, all cited, zero fitted DoF

`classes/blood_death_knight.py` + `data/constants.yaml`
(`constants_version` 38→39 — master's VDH PR #262 took 38 first),
warrior/VDH/Brewmaster/Guardian paths untouched:

| | Was (placeholder) | Now |
| --- | --- | --- |
| Bone Shield | flat 21% physical DR (3%/stack × avg 7), always | bonus armor = 1.80 × Strength while ≥1 charge; replay window-gated on buff 195181, synthetic always-on |
| Rune Carved Plates | absent | replay-only, buff-gated per school: −1.5%/stack × 3.5 measured avg stacks (≈−5.25%) while the school's buff is up |
| Icebound Fortitude | 30%/8s/**180s** | 30%/8s/**120s** |

The one measured degree of freedom is RCP's `avg_stacks_while_up: 3.5` — a
tight-range (2.91–3.84) cross-fight measurement, the same class of constant as
Brewmaster PT's measured `avg_uptime: 0.88`, not a residual-tuned value.

### After — per-school decomposition (same corpus, worktree code)

Pre-absorb over-prediction per fight (was → now):

| Fight | pre-absorb (was) | pre-absorb (now) | post-absorb (was) | post-absorb (now) |
| --- | --- | --- | --- | --- |
| Player 2 Algeth'ar +22 | +22.9% | **+1.7%** | +60.9% | +16.2% |
| Player 3 Windrunner +22 | +19.1% | **−3.9%** | +42.0% | +0.2% |
| Player 4 Skyreach +20 | +20.1% | **−5.2%** | +40.3% | −2.8% |
| Player 4 Pit of Saron +22 | +24.7% | **−5.0%** | +43.2% | −4.0% |
| Player 1 Nexus +22 | +32.8% | **+17.2%** | +66.2% | +36.9% |

Aggregate per school (was → now):

| school | base (M) | real mit% | sim mit% (was → now) | gap (was → now) |
| --- | --- | --- | --- | --- |
| physical | 3518.7 | 69.8% | 61.6% → **70.8%** | +8.2pp → **−1.0pp** |
| bleed | 26.2 | 72.3% | 23.9% → 8.4% | +48.3pp → +63.8pp (see below) |
| shadow | 105.9 | 18.0% | 6.1% → 10.5% | +11.9 → +7.6pp |
| fire | 77.9 | 18.7% | 11.7% → 15.8% | +6.9 → +2.9pp |
| frost | 39.2 | 12.1% | 7.4% → 11.4% | +4.7 → +0.7pp |
| arcane | 149.6 | 23.6% | 11.2% → 15.4% | +12.4 → +8.2pp |
| nature | 84.6 | 23.2% | 12.7% → 16.7% | +10.5 → +6.6pp |
| holy | 45.5 | 19.8% | 4.8% → 9.4% | +15.0 → +10.5pp |

- **Physical is closed** (−1.0pp, mild over-mitigation) — the Bone Shield
  armor mechanic accounts for reality's ~70% physical mitigation with no
  tuned constant.
- **The "bleed" row got *worse* by design and exposed a real bug**: the old
  flat Bone-Shield DR incidentally covered bleed-school events; the correct
  armor mechanic doesn't (armor bypass). Reality still mitigates these
  ticks at 72.3% ≈ exactly the fight's armor-chain physical (71.3%) — and
  per-ability forensics pins all 26.2M on **Searing Rend** (1255208 /
  1257736, Lothraxion + Lingering Image, Nexus-Point Xenas), a
  false-positive of the `"Rend"` fragment in `core/bleed_detection.py`.
  It is an armor-affected physical DoT, not an armor-bypassing bleed.
  **Not fixed in this pass**: the fragment set is shared with the
  *calibrated* Warrior and Guardian corpora (Searing Rend appears in ≥9
  `examples/` logs), so reclassifying it requires re-running both corpora —
  filed as a cross-spec follow-up. Est. effect here: ~+15.5M of
  Player 1's +51M gap (their pre-absorb would drop to roughly +12%).
- Magic gaps shrink ~4–5pp where RCP-magical credits; the residual
  (+3–10pp) is the party-aura parity gap (Devotion Aura 94–97% uptime on
  2/5 groups, unapplied) + the cross-spec thin-magic remainder — now the
  largest named residual class.

### After — Monte-Carlo sweep (same corpus, iters=300, seed=42)

```
  K= 2000: RMSE=0.273  [-22.5%, -31.6%, -31.0%, -34.3%, +8.3%]
  K= 2500: RMSE=0.199  [-13.6%, -21.5%, -22.2%, -24.4%, +15.2%]
  K= 3000: RMSE=0.150  [-1.2%, -11.8%, -14.0%, -14.9%, +23.8%]  <-best
  K= 3430: RMSE=0.152  [+11.6%, -1.9%, -6.8%, -6.4%, +30.5%]  [CANONICAL]
  K= 3500: RMSE=0.158  [+13.4%, +0.2%, -5.5%, -5.0%, +31.8%]
  K= 4000: RMSE=0.223  [+26.1%, +11.1%, +3.0%, +5.0%, +40.5%]
CANONICAL K=3430: RMSE=0.1519   Best K=3000 (RMSE=0.1499)
VERDICT: all per-run deltas within +/-15% at canonical K? False
```

- **RMSE at canonical K: 0.483 → 0.152** (3.2×); **4/5 fights within
  ±15%** (+11.6 / −1.9 / −6.8 / −6.4); mean +5.4% (median −1.9%) — the
  positive bias is entirely the Player 1 outlier (+30.5%), whose gap
  decomposes into the named Searing-Rend misclassification + the deferred
  party-aura parity (see the decomposition section).
- **Best-K relocated from the sweep floor (2000, the under-mitigation
  signature) to 3000 ≈ canonical** — RMSE at K=3000 (0.1499) and K=3430
  (0.1519) are statistically indistinguishable; the fit now has an
  *interior* minimum at the DBC anchor instead of pinning at the grid
  edge. This is the strongest evidence the change is the right
  *mechanism*: a compensating flat-DR constant cannot relocate the
  armor-curve minimum, only an armor-shaped term can.
- The formal sweep verdict is **False** (Player 1 out of band) —
  consistent with keeping `calibrated: false` even before the deferred
  layers and the single-build caveat are considered.

## Why `calibrated` stays false

1. **One hero build.** All five fights are Deathbringer. RCP is buff-gated so
   a San'layn log simply loses that credit — but San'layn's own kit
   (Vampiric-Strike leech loops) is uncharacterized, and the flag must not
   certify a build never observed (the VDH Aldrachi-Reaver rule).
2. **Dual-validator audit pending.** Per
   `feedback_dual_validator_pre_merge_audit`, both validator modes
   (engine-math + log-replay) must audit this before merge — this doc is the
   input to that audit, not a substitute.
3. **Absorb-anchoring caveat.** Post-absorb deltas ride on the log's absorbed
   field (Blood's is enormous: Blood Shield + AMS + WotN); the pre-absorb gap
   is the honest mitigation read and it, too, is now small — but a future
   absorb re-derivation could move the headline numbers by more than the
   residual.
4. **Known deferred layers**: party_dr_by_school parity, synthetic-path RCP,
   Foul Bulwark HP, the shipped-WCL-path buff-window plumbing (window-gated
   layers only fire in the characterization scripts today — same staging as
   VDH Meta / Brewmaster PT), snapshot-pollution detector.

## Caveats

- Single-fight noise is ~±20% even for a calibrated spec (PR #142 warrior
  control). Five fights agreeing at +40–66% baseline post-absorb is far
  beyond that. After the fix, the pre-absorb residuals are
  {+1.7, −3.9, −5.2, −5.0, +17.2}% — mean +1.0%, unbiased-with-one-outlier
  rather than systematic; but the per-fight magnitudes remain
  characterization-grade, not ±1pp.
- The decomposition includes the synthetic healer `dr_cooldown` externals
  (so it reproduces the MC sweep) — the engine-only residual is slightly
  larger than displayed (same for every spec; cross-spec comparison holds).
- **Bleed anomaly — RESOLVED to a named cross-spec bug, not fixed here:**
  Player 1's "bleeds" (72.3% real mitigation vs armor-bypass modeling)
  are 100% **Searing Rend** ticks, a false-positive of the `"Rend"`
  fragment in `core/bleed_detection.py` (it is armor-affected: its real
  mitigation equals the fight's physical armor chain to within 1pp).
  Reclassifying touches the calibrated Warrior + Guardian corpora
  (Searing Rend appears in ≥9 `examples/` logs, including Brutoh's own
  Nexus-Point runs) → filed as a follow-up that must re-run both corpora.
  0.6% of corpus base damage; nothing was fitted to it.
- RCP's 3.5 avg-stacks constant is measured on this corpus (10 measurements,
  4 players). A rotationally-different Blood DK would wander ±1 stack
  (≈±1.5% DR) — acceptable spread, flagged for re-measurement when a San'layn
  or off-meta log lands.
- Bone Shield's max-HP side effects (Wowhead 195181 effects #3/#4: max-HP %,
  attack-speed) and Foul Bulwark are unmodeled — HP-side, not DTPS.
- The two polluted-snapshot fights are excluded, not corrected. Player 3's Seat
  run (+21, armor 6,591) would double-count Bone Shield armor under the new
  model — do not add it to a corpus until the de-pollution follow-up lands.

## Dual-validator verdict

**PENDING.** This change touches the validation-critical replay path
(mitigation chain + constants). Per project discipline both validator modes
(engine-math: SimC cross-check of the 1.80/1.5%-per-stack/120s values and the
armor-pipeline order; log-replay: reconstruction faithfulness on at least one
corpus fight) must sign off before merge. Not run in this pass.
