# Phase 4 — Brewmaster "physical armor-magnitude shortfall": root-caused (2026-07-04)

Closes the strand `phase4_brewmaster_magic_layers_2026_06_07.md` left open as
"Real physical mitigation exceeds `armor/(armor+K)` at the snapshot. Candidate
causes — raid/group armor buffs, snapshot staleness, or `base_amount` semantics
on staggered hits — **unresolved; do not model yet**." All three candidates now
have verdicts, plus two findings nobody predicted. **Doc + tooling only — no
engine constants changed, no fudge fitted.**

Corpus: the same two timed Brewmaster runs (Player 1 +12 Windrunner Spire,
AnonBrewmaster1 +17 Seat of the Triumvirate, `examples/Logs/Archive-*.txt`), plus —
new in this pass — AnonBrewmaster1's *failed* +17 attempt (same player/build/dungeon,
8 h earlier) and two Brutoh Prot-Warrior positive controls (Nexus-Point Xenas
+12, Windrunner Spire +14).

## Method — per-hit forensics with live armor

Two facts about ACL logs unlock a much sharper instrument than the aggregate
per-school decomposition used on 2026-06-07:

1. **Every damage line targeting the tank carries the tank's LIVE armor** in
   its advanced-info block (the info unit is the *destination*): field 17 on
   `SPELL_DAMAGE`/`SPELL_PERIODIC_DAMAGE`, and on the `SWING_DAMAGE_LANDED`
   twin of every melee swing. No more trusting one COMBATANT_INFO snapshot —
   armor is observable per hit.
2. Per hit, `r = (amount + absorbed + blocked) / base_amount` is the fraction
   of logged pre-mitigation damage that survived to the post-armor/pre-absorb
   stage. Dividing out the armor curve at the hit's live armor and versatility
   leaves a **residual multiplier**: 1.0 = fully explained; 0.95 = an extra 5%
   cut the model doesn't know about.

Tooling: `scripts/per_hit_mitigation_forensics.py` (residuals by school ×
buff-state × attacker-debuff-state, stagger conservation, live-armor timeline)
and `scripts/aura_attribution.py` (rank ALL tank auras by inside/outside-window
residual difference). Both reuse the engine's own `parse_damage_event` so the
semantics are identical to replay.

## Verdicts on the three named candidates

### 1. Raid/group armor buffs — CONFIRMED (Player 1 only): Blistering Scales

Player 1's COMBATANT_INFO armor was never one number: their five in-window
snapshots read 1259 / 1641 / 1259 / 1641 / 1641, and the live-armor timeline
(3,536 stagger ticks) splits ~47% / 47% between exactly those two states (plus
small armor-shred excursions). The +382 delta is **exactly 0.20 × 1908** — 20%
of the party Augmentation Evoker's own armor — and the log shows
`SPELL_CAST_SUCCESS` + `SPELL_AURA_APPLIED` of **Blistering Scales (360827)**
from that evoker (Player 2, armor 1908 in their own cast line's info block)
onto Player 1 33 s before their first 1641 snapshot. Scales deplete on melee hits
and get re-applied → the mid-run oscillation. AnonBrewmaster1's party has no evoker
and their armor never moves (1312 on all 3,052 ticks) — the cross-check holds.

Consequences:
- The 2026-06-07 docs' "armor 1641" for Player 1 was itself the *buffed* state
  (the old last-snapshot hydrate happened to grab scales-up instants).
- The 2026-07-03 min-armor hydrate fix (`_select_combatant_info`) now picks
  1259 for them — the correct **self** baseline. Neither number is "the" armor:
  they genuinely spend half the fight at each. The engine models no external
  armor layers, so replay under-armors them ~half the fight regardless; see
  the calibration-numbers section for the measured effect. Hydrate itself is
  left unchanged — its contract (the character's own unbuffed armor) is met.

### 2. Snapshot staleness — REFUTED

Snapshots fire per boss pull (5-6 per run), gear item-ids are identical across
every snapshot of both tanks, and the per-tick live armor agrees with the
snapshots in every unbuffed window (AnonBrewmaster1: 14/14 snapshots and 3,052/3,052
ticks at 1307-1312). Nothing is stale; Player 1's variation is real gameplay
(scales), not measurement error.

### 3. `base_amount` semantics on staggered hits — REFUTED as stated, but see finding B

The Stagger-specific form is dead:
- The wedge is **school-uniform to three decimals** on AnonBrewmaster1 (physical
  0.8294 vs shadow 0.8306 median residual). A stagger-side artifact would cut
  physical ~4× deeper than magic (40% vs 10% stagger fraction). It doesn't.
- Stagger absorbs credited on hits and Stagger DoT ticks appear identically on
  both sides of the sim-vs-real comparison, so they cancel; absorbed-field
  bookkeeping cannot drive the gap.
- Only ~30-41% of credited Stagger intake ever ticks or purifies out
  (STAGGER_CLEAR properly counted — note the lines carry only the GUID, not
  the player name). Midnight Stagger forgives the remainder. Irrelevant for
  replay (cancels), but the **synthetic** stagger model (`stagger_pct_*` →
  full pool → ticks) over-ticks vs this reality — noted for Phase 4.4.

## Finding A — the wedge that remains after everything modeled

After dividing out live-armor curve @K=3430 × vers × each spec's modeled
always-on chain (warrior: DS 0.85 × Indomitable 0.96 × BfI; Brewmaster: PT
ledger / Fortifying Brew windows), per-hit residuals in the cleanest bins
(no CDs, no PT, no attacker-debuffs) are:

| Run | Spec / build | Clean residual | Unexplained factor |
| --- | --- | --- | --- |
| Brutoh Nexus-Point **+12** | Prot Warrior (calibrated) | 0.740 | ×0.94 (0.91-0.95 given BfI-stack uncertainty) |
| Brutoh Windrunner **+14** | Prot Warrior | 0.736 | ×0.94 — key level moved nothing |
| Player 1 Windrunner **+12** | BrM, Shado-Pan | 0.948 | ×0.95 |
| AnonBrewmaster1 Seat **+17, failed, 15:50** | BrM, Master of Harmony | 0.921-0.934 | ×0.93 |
| AnonBrewmaster1 Seat **+17, timed, 23:47** | BrM, Master of Harmony | 0.829-0.831 | **×0.83** |

Four of five runs — two specs, three dungeons, keys +12/+14/+17, four
different parties — agree on a **universal ~5-7% always-on all-school cut**
the model doesn't know about. It exists on the *calibrated warrior corpus
too*, i.e. it is part of whatever K=3430's corpus fit absorbed, and is NOT a
Brewmaster-specific gap. (It is likely a large slice of the long-standing
"structural physical-mit gap ~10pp", now measured per-hit for the first time.)

Exactly one run — AnonBrewmaster1's timed +17 — carries an **additional ~11%**
(×0.83 total). That extra layer survived every falsification attempt:

- constant from the first hit to the last (p25 = p50 = p75 = 0.8294);
- uniform across every trash mob and all three bosses;
- school-uniform (physical = shadow);
- unmoved by any aura window on the tank (full-aura attribution: max
  explanatory power 0.038, nothing structural);
- unmoved by the Holy Paladin dying 4× (residual median identical 0.8294
  while they're dead — kills every HPal-aura/proximity theory);
- **absent in the same player + build + dungeon + key level 8 h earlier**
  (failed run: 0.93);
- Balanced Stratagem (451508/451514) stack level: residual flat across 0-5
  stacks in both schools → **refuted as a DR**; and since the failed run shows
  only the universal wedge, the "Master of Harmony hidden DR layer" from the
  2026-06-07 doc is **refuted** as well — what looked build-dependent was
  run-dependent.

## Finding B — the generalized `base_amount` semantics result

The only mechanism consistent with all of Finding A's shape: **run-scoped,
mob-side damage multipliers that apply after the logged `base_amount` is
computed** — the signature of server-side tuning (mid-week M+ hotfix nerfs are
deployed exactly this way) plus, plausibly, a stable global component. Direct
in-log corroboration that attacker-side modifiers do NOT flow into
`base_amount`… actually flow the OTHER way (they do NOT appear in `r`):

- **Demoralizing Shout windows change nothing per-hit** on Brutoh (+Debuff
  residual 0.730 vs noDebuff 0.740): Demo's −20% is already inside
  `base_amount`. ⚠️ simf's warrior replay applies Demo DR *on top of* base →
  **double-counts Demo**, partially canceled by the universal wedge above.
  Flagged for `validator` (engine-math + log-replay) — NOT fixed here; it is
  calibration-critical for the warrior corpus and needs its own pass.
- Monk Breath of Fire / Keg Smash debuff windows likewise show no per-hit
  effect (Midnight BoF carries no damage-taken reduction, or it's in base).

Implication for calibration methodology: each replay run carries a
multiplicative factor F (base→applied) the model cannot predict from player
state. Measured F here: ~0.93-0.95 (four runs) vs 0.83 (one run). A spec
corpus whose runs have F near the warrior-corpus norm calibrates cleanly; a
run with divergent F produces exactly the "over-predicts, worse at the
pre-absorb-leveraged schools" signature — and for a Brewmaster the fixed
`log_absorbed` subtraction **amplifies** a 13% pre-absorb wedge into a 30-45%
landed-physical error (same leverage mechanism documented for PT in the
magic-layers doc).

**Recommendation (needs lead + validator ratification, not implemented):**
`scripts/calibrate_spec_from_logs.py` should compute and report each run's
measured F (the tooling now exists) alongside the delta, and `calibrated:`
judgments should be made on F-consistent corpora — or on F-normalized
replays. Do NOT bake any F into constants.yaml: it is a per-run log artifact,
not a property of the character. (This is the no-fudge rule applied: a
constant fitted to AnonBrewmaster1's 0.83 would silently break on their own next run.)

## What this means for the Brewmaster numbers

Decomposition of the two headline residuals (post-PT state, 2026-06-07:
Player 1 +11.8%, AnonBrewmaster1 +56.8%):

- **Player 1 +11.8%** ≈ Blistering Scales armor unmodeled (~half the fight at
  +382 armor; hydrate now uses 1259, worsening baseline vs the old
  buffed-snapshot 1641 — measured below) + the universal ~5% wedge amplified
  by the absorb-leverage. Their build-specific model (PT 8% @ 0.88) is
  **validated to the decimal** per-hit: PT-active bins are ×0.92 of PT-idle
  bins; Fortifying Brew bins are ×0.80. The modeled constants are right.
- **AnonBrewmaster1 +56.8%** ≈ (universal ~6% + their run's extra ~11%) pre-absorb,
  amplified ~2.5-3× by the fixed-absorb leverage on their physical-heavy
  profile + no PT to credit. Their armor input is perfect; there is no missing
  monk passive of that size and no MoH DR layer. **The bulk of their residual
  is un-modelable run-scoped tuning (F), not model error.**

Hydrate re-check (the cheap pending item from ROADMAP): armor inputs under
the 2026-07-03 `_select_combatant_info` fix — Player 1 1641 → **1259** (the fix
can only lower armor; for an over-predicting spec this *worsens* the delta;
the value itself is the correct self-baseline, see candidate 1), AnonBrewmaster1
1312 → **1312** (no-op). Calibration deltas at canonical K=3430 (iters=300,
seed=42, current master incl. the fix):

| Run | hydrate armor | Δ at K=3430 | vs 2026-06-07 (+11.8% / +56.8%) |
| --- | --- | --- | --- |
| Player 1 +12 (PT ledger active) | **1259** (min-armor pick, current master) | **+22.5%** | worse by ~11pp — the fix picks the scales-down state |
| Player 1 +12, counterfactual | 1641 (old last-snapshot pick = scales-up state) | **+8.4%** | the June +11.8% minus the #214 vers-recal shift |
| Player 1 +12, counterfactual | 1452 (live-armor tick-time-weighted mean) | **+15.1%** | the "fair" single-armor replay input |
| AnonBrewmaster1 +17 (no PT) | **1312** (identical under old and new selection) | **+47.4%** | −9.4pp, entirely the #214 secondary recalibration (armor input unchanged) |

The 14pp Player 1 swing between their two real armor states quantifies the
Blistering Scales stakes; even at the generous 1641 input the +8.4% still
contains the universal wedge (and PT's known over-credit pushes the other
way — see the magic-layers doc's post-audit section). AnonBrewmaster1's +47.4% with
a *perfect* armor input is the cleanest demonstration that their residual is
not a character-model error.

## Status

- `specs.brewmaster_monk.calibrated` stays **false** — correctly so, but the
  reason has changed: the remaining gap is dominated by (a) a cross-spec
  calibration-methodology issue (F) that the warrior corpus quietly absorbs,
  and (b) a party-external armor layer (scales). Neither is a missing
  Brewmaster constant. The named follow-ups from the magic-layers doc are
  re-scoped: physical armor-magnitude strand **closed** (this doc);
  Master-of-Harmony attribution **closed as refuted-for-DR** (any MoH absorb
  remains inside `log_absorbed` where it always was).
- Cheapest path to a fair Brewmaster verdict: 2+ fresh logs from the same
  build/party through `calibrate-k`-style replay **with F reported per run**,
  judged on F-consistent runs.

## Caveats

- One run carries the ×0.83 anomaly; its attribution to server-side tuning is
  by elimination (every player-visible mechanism falsified in-log), not by an
  independent record of the hotfix. A second divergent-F run, or a Blizzard
  hotfix-notes cross-reference for 2026-05-03, would harden it.
- The universal ~5-7% component is bracketed (warrior chain has BfI-stack
  uncertainty: 0.91-0.95). Its cause is unidentified; candidates that survive
  this data: a global base-vs-applied multiplier, or a shared unmodeled tank
  passive. It is *shared with the calibrated baseline*, so it does not block
  per-spec work the way a spec gap would.
- Live-armor field semantics (info-block field 17) were verified against
  COMBATANT_INFO values in unbuffed windows on both tanks; if a future log
  version reorders the advanced block, `scripts/per_hit_mitigation_forensics.py`
  guards by checking the info-GUID is the tank and skipping otherwise.
