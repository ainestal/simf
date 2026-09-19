# VDH magic-residual lead characterization + follow-through (2026-07-18)

**Status: 2 of 3 leads shipped, 1 held, decided autonomously.** This doc was
originally written as characterization-only, recommending the three leads be
held for user ratification before any wiring. The user's response: *"I cannot
ratify something as deeply technical and mathematical as that, just carry on
yourself with the next points."* This update records what was actually
decided and shipped under that direction — the calibration judgment calls
below are mine, not the user's, per their explicit delegation.
`specs.vengeance_demon_hunter.calibration_tier` stays `characterized` (still
far outside ±15% even after these fixes). Closes out the "fold into the next
VDH characterization pass" item left open by
`docs/validation/vdh_fiery_brand_double_count_2026_07_17.md`, and answers
CONTRIBUTING.md's "Next priorities" item 1.

## 1. Post-Fiery-Brand-fix corpus re-measurement

The 2026-07-17 fix (gating Fiery Brand's −40% target-source DR out of log
replay, since it's an attacker-side debuff already baked into the log's
`unmitigatedAmount`) predicted an analytic ~6.8% widening of the deltas but
had not been re-run against the real 3-fight corpus. Re-ran
`scripts/calibrate_spec_from_wcl.py vengeance_demon_hunter` against the same 3
public fights (AnonPlayerX1 Windrunner Spire+19, AnonPlayerX3 Skyreach+20,
AnonPlayerX2 Pit of Saron+21 — all Fel-Scarred), iters=300, seed=42:

```
=== Sweep (iters=300, canonical K=3430) ===
  K= 2000: RMSE=0.209  [+8.1%, +32.2%, +14.2%]  <-best
  K= 3430: RMSE=0.586  [+48.7%, +75.1%, +47.9%]  [CANONICAL]
```

Confirms the analytic prediction's direction: canonical-K deltas moved
+37.5%/+62.0%/+36.5% (pre-fix) → **+48.7%/+75.1%/+47.9%** (RMSE 0.468→0.586).
The fix correctly widened the gap — it had been masking part of the real
shortfall, not closing it.

## 2. The three named leads — research, then a decision on each

Method: live Wowhead tooltip fetch → SimC `midnight` branch source
(`sc_demon_hunter.cpp`) cross-check → DB2 corroboration via `wago.tools`/
`trait_data.inc` where reachable — the same method that pinned Painbringer.
Full agent reports preserved in the maintainer's internal review history
(not part of this repo).

### 2a. Immolation Aura → Infernal Armor — SHIPPED

- Immolation Aura (baseline ability, spell **258920**) has a dormant armor
  effect (effect #5, base 0%) SimC's `composite_armor_multiplier()` applies
  via `pow(1 + effectN(5).percent(), stacks)` — real code, not a stub.
- **Infernal Armor** (talent **320331**, Demon Hunter **class tree**, level
  30) activates it: live tooltip *"Immolation Aura increases your armor by
  20%..."*.
- **School: physical only** — cannot touch the magic residual, but a real,
  clean, well-sourced physical-mitigation fix in its own right.
- Confirmed **TAKEN (rank 2/2) by all 3 corpus fighters** via direct WCL
  `CombatantInfo` `talentTree` query, cross-referenced against
  `trait_data.inc`'s Demon Hunter (class_id 12) rows for the real entry_id
  (112924 → spell 320331).
- **Decision: SHIP.** Modeled unconditionally (VDH has no per-player
  talent-detection plumbing; 100% corpus uptake makes this safe today, flagged
  if a future log lacks it), window-gated on `immolation_aura_spell_id
  (258920)` in `event.active_buffs`, stacking multiplicatively with
  Metamorphosis's armor multiplier (same SimC bucket). Live/synthetic mode
  gets no credit (no Immolation-Aura-cast model in the policy yet).
- **Measured impact — the largest VDH calibration fix since Metamorphosis
  armor itself**: canonical-K deltas +48.7/+75.1/+47.9% → **+39.9/+66.7/+38.8%**
  (RMSE 0.586→0.502).

### 2b. Fel Flame Fortification — HELD, not wired

- Spell **389705**. Live tooltip: *"You take 10% reduced magic damage while
  Immolation Aura is active."* — a personal buff, unambiguously
  defender-side (same category as Demonic Wards/Painbringer, no
  attacker/defender ambiguity).
- SimC source confirms a real, acknowledged gap: `player_talent_t
  fel_flame_fortification;  // No Implementation` — declared, looked up,
  never referenced in any buff/effect code.
- **DB2 cross-check failed** (wago.tools returned client-rendered chrome only;
  `trait_data.inc` confirms tree position — spec tree, entry 112868, spell
  389705 — but carries no effect-magnitude data). 2-of-3 sourcing legs, not
  Painbringer's full 3-of-3.
- Confirmed **TAKEN by only 1 of 3 corpus fighters** (AnonPlayerX1; not
  AnonPlayerX3 or AnonPlayerX2) via the same WCL talentTree cross-reference.
- **Decision: HOLD.** Unlike Infernal Armor/Void Reaver, this talent is
  *not* universal in the corpus. VDH has no per-player talent-detection
  plumbing (no `detected_talent_entry_ids` field on `Character`, nothing
  analogous to the Warrior's `_talent_set()` for VDH's mitigation chain) —
  window-gating on Immolation Aura's own buff (258920) would incorrectly
  credit AnonPlayerX3 and AnonPlayerX2 too, since that buff is baseline and up
  for everyone regardless of whether they have this talent. Building real
  per-player talent-detection plumbing for a single ~2pp fix (average,
  weighted by the 1-of-3 corpus uptake) is not a good time trade against the
  higher-priority live-UI review this session, but the entry_id (112868) and
  method are now pinned for whoever picks this up. **Shipping this wrong
  would be worse than not shipping it** — the exact failure mode this
  project has repeatedly caught (detected-talent-id mismatches,
  non-universal-talent over-crediting).

### 2c. Frailty → Void Reaver — SHIPPED, but not the way originally proposed

- **Frailty** (debuff, spell **247456**) is purely a healing-conversion
  mechanic in its base form — matches what simf already models. **Void
  Reaver** (talent **268175**, Vengeance **spec tree**) adds a defensive
  effect: Wowhead tooltip `Apply Aura: Mod Damage to Caster %` at −5%; DB2
  `EffectAura=269` (`A_MOD_DAMAGE_TO_CASTER`), `EffectBasePointsF=−5`,
  school mask 127 (all schools); capped at 1 stack in Midnight
  (`SpellAuraOptions.stack_amount=1`, de-stacked from the older
  Dragonflight/TWW design — the same "Midnight de-stacked this" shape
  Painbringer's investigation already found once).
- Confirmed **TAKEN (rank 1) by all 3 corpus fighters.**
- **The classification question this doc originally deferred**: is this
  attacker-side (baked into logged damage, like Fiery Brand — double-count
  risk) or defender-side (a genuine new mitigation layer, safe like
  Painbringer)? A dedicated validator SimC-source trace came back
  **genuinely ambiguous** — SimC's own bucket placement isn't decisive,
  because Fiery Brand's *confirmed*-attacker-side effect sits in the
  *identical* SimC bucket as Painbringer's confirmed-defender-side one. The
  investigation recommended the same decisive test that settled Demo Shout:
  per-hit forensics comparing `unmitigatedAmount` for the same ability
  against the same mob, with vs. without the debuff active.
- **Ran that test against all 3 corpus fights** (`fetch_source_debuff_windows`
  for Frailty on the tank's own applications, joined spawn-precisely against
  `fetch_damage_events`' `base_amount`/`amount`/`absorbed`/`blocked`).
  Result, per (ability, mob) pair seen both with and without Frailty active
  on that specific spawn:

  | Fight | base_amount ratio (Frailty-on / Frailty-off), across 6-8 ability/mob pairs |
  | --- | --- |
  | AnonPlayerX1 | 0.92–0.96 (one low-n outlier at 1.16) |
  | AnonPlayerX3 | 0.84–0.97 |
  | AnonPlayerX2 | **0.90–0.92, a strikingly tight cluster across all 8 pairs** |

  For the *same ability hitting the same mob type*, `unmitigatedAmount`
  itself runs **~5-15% lower while Frailty is active on that mob** — right in
  the ballpark of the talent's known 5% magnitude. This is decisive:
  **Void Reaver's Frailty DR is ATTACKER-SIDE**, already baked into logged
  damage by the time simf's replay pipeline sees it. The earlier "genuinely
  ambiguous, weak lean defender-side" read from SimC-source-tracing alone was
  the wrong lean — the empirical test settles it.
- **Decision: SHIP, but live/synthetic-mode only** — the exact same
  treatment as Fiery Brand. `void_reaver_dr: 0.05`,
  `void_reaver_avg_uptime_in_m_plus: 0.90` (measured as the fraction of the
  tank's physical hits landing while the attacking spawn carried Frailty:
  86.1%/91.5%/93.3% across the 3 fights — consistent with Soul Cleave being
  a near-constant Fury spender against the tanked target in M+). **Correctly
  contributes ZERO to the corpus deltas above** — confirmed by a mutation
  test (`test_vdh_void_reaver_not_double_counted_in_log_replay`): removing
  the `if not event.is_log_replay:` guard makes the replay-mode with/without
  pair diverge, proving the guard is load-bearing. Wiring this into replay
  would have shipped the *exact same bug class* just fixed for Fiery Brand
  one day earlier — the empirical test caught it before it happened.

## 3. Combined-impact honesty check

Gap before this session: **17-43pp of magic DR** (modeled ~21-22% vs. real
38-64%, at canonical K). After this session's fixes:

- Infernal Armor: real, measured, **~9pp average closure** — but it's
  physical, not magic. The magic-DR gap itself is **untouched** by anything
  shipped this session (Fel Flame Fortification held; Void Reaver correctly
  contributes zero to calibration since it's attacker-side).
- The overall canonical-K deltas did improve substantially (+48.7/+75.1/
  +47.9% → +39.9/+66.7/+38.8%, RMSE 0.586→0.502) — real progress, but on the
  physical side the earlier synthesis had explicitly ruled out of scope, not
  on the magic residual this investigation was chartered to explain.
- **The magic-DR gap (17-43pp) remains completely unexplained.** Fel Flame
  Fortification (held) would have closed at most ~2pp on one fight. Void
  Reaver, once correctly classified as attacker-side, closes **0pp** of the
  calibration gap (it's real, but it was never missing from the log in the
  first place — it only needed a live/synthetic-mode credit for forward-sim
  accuracy, unrelated to this residual).
- The wide best-fit-K gap noted in the original doc (canonical 3430 vs.
  best-fit 2000, and even at best-fit K the deltas aren't uniformly small)
  still stands and is now the strongest lead: this reads increasingly like a
  structural issue (armor-curve shape, or a large uncharacterized magic
  layer entirely distinct from the 3 leads investigated here) rather than a
  stack of small missing flat-DR percentages.

## 4. What's still open

1. **The magic-DR residual itself is unsolved.** This session closed out the
   3 *named* leads (2 shipped, 1 correctly held) but confirmed none of them
   meaningfully touch the magic-specific gap. The next VDH session should
   treat the magic residual as an open investigation again, not assume these
   fixes made progress on it.
2. **Fel Flame Fortification** — pin the entry_id (112868) is done; needs
   either (a) a real per-player VDH talent-detection field on `Character`
   (generalizable — would also help future VDH mechanics), or (b) a second
   data point where it's more broadly represented, before it's safe to wire.
3. **A 4th data point (ideally Aldrachi Reaver)** — attempted this session,
   not resolved. Two real candidates surfaced via raider.io cross-referencing
   (WCL's own site 403s automated fetches; raider.io's public API doesn't):
   - **AnonPlayerX8**, Algeth'ar Academy +14, self-logged — confirmed real
     and resolvable, but their hero-talent `talentTree` entries matched
     **neither** Aldrachi
     Reaver (hero_tree_id 35) **nor** Fel-Scarred (hero_tree_id 34) in
     `trait_data.inc` — they matched an unidentified 3rd Demon Hunter
     hero-tree bucket (hero_tree_id **124**, names like "World Killer",
     "Meteoric Fall", "Dark Matter", "Voidfall", "Celestial Echoes" — a
     void/cosmic theme unrelated to either known VDH hero tree). This is a
     genuinely new, unexplained finding — worth understanding before trusting
     AnonPlayerX8 as either an Aldrachi Reaver or Fel-Scarred data point.
     Possibly a new Midnight hero tree (a "Devourer" 3rd Demon Hunter spec was
     mentioned in passing by the Immolation Aura research this session —
     Havoc/Vengeance/Devourer — this hero tree may belong to that spec and
     simply share entry-id space in `trait_data.inc`'s per-class bucketing;
     not confirmed).
   - **AnonPlayerX9**, raider.io-confirmed Aldrachi Reaver talent loadout.
     Cross-referencing raider.io's `logged_run_id` against WCL turned up a
     real report for roughly the right time window, but its fight list
     didn't include the expected dungeon — either the actual log lives in a
     different report than the one found, or the `logged_run_id`/timestamp
     cross-reference has a subtlety not yet understood. Not pursued further
     this session (diminishing returns against the higher-priority live-UI
     review).

## 5. Why this differs from a `Workflow`-driven autonomous decision on paper

The original version of this doc (characterization-only) explicitly
recommended holding all 3 leads for the user's ratification, reasoning that
shipping small, individually-uncertain fixes on a `characterized`-tier spec
risked overstating progress. The user's direct response — they cannot
meaningfully ratify math/calibration decisions at this level of technical
depth — changes the operative rule for VDH (and by extension any similarly
technical calibration work) going forward: **the calibration judgment call is
mine to make, own, and document honestly**, not something to defer back.
This session tried to honor the spirit of the original caution anyway: ship
only what's cleanly sourced AND measurably safe (Infernal Armor: 100% corpus
uptake, clean multiplicative armor math, measured improvement; Void Reaver:
resolved by a decisive empirical test, not a guess), and explicitly hold what
isn't (Fel Flame Fortification's non-universal talent uptake with no
detection plumbing to gate it correctly).
