# F15 — ProtPal block-value armor-curve port + a hydrate-pipeline bug (2026-07-03)

First real-log calibration attempt for Protection Paladin since the F15
architectural debt was named (F12 era, 2026-05-20). Source: 3 fresh ACL-on
local logs the user recorded themselves on a new Paladin alt, "Bruttah"
(`examples/bruttah-prot/`) — Pit of Saron +2, Algeth'ar Academy +2,
Magisters' Terrace +5, all timed. A 4th file in the same drop
(`WoWCombatLog-070326_074225.txt`) is a different character's (Brutoh,
Protection Warrior) incomplete Maisara Caverns +14 attempt — excluded, not
part of this corpus.

Tooling: `scripts/calibrate_spec_from_logs.py` (`inventory` + `calibrate`).

## TL;DR

- **F15 shipped**: ProtPal block value now runs through the same armor curve
  as Warrior (`calculate_armor_resist(state.cached_block_value_rating, K,
  1.0)`), replacing a flat pre-armor `block_value_pct: 0.30`. **No crit-block
  roll** — a validator audit against SimC's `midnight` branch found Paladin
  has no critical-block mechanic at all (it's Warrior-only, off Unwavering
  Sentinel mastery). Landed with two sibling fixes the same audit surfaced:
  `mastery_block_chance_scaling` corrected 0.5→1.0 (tooltip-derived), and
  deletion of a fictional "Divine Bulwark flat DR" step (a DF/TWW-era effect
  not present in Midnight).
- **A second, unrelated bug was found characterizing the first fix**:
  `_select_combatant_info` (the COMBATANT_INFO snapshot picker every spec's
  log-hydrate path shares) always used to take the LAST snapshot for every
  non-Guardian spec. Shield of the Righteous is a temporary self-buff that
  adds armor in Midnight (spell 132403/53600, +192% of Strength for 4.5s) —
  a Paladin chaining it into boss pulls can have every snapshot read buffed,
  including the last one. Fixed by preferring the minimum-observed-armor
  snapshot (mirrors the existing Guardian out-of-form/min-stamina heuristic
  for a different contaminant). **Likely affects VDH's existing #143
  characterization corpus too** (Demon Spikes armor buff, Metamorphosis armor
  multiplier) — flagged in ROADMAP.md, not yet re-run.
- **Protection Paladin: still NOT calibrated.** With the hydrate bug fixed,
  the model **under-predicts mitigation** (over-predicts damage taken) by
  +19.8% / +4.3% / +31.5% at canonical K=3430 across the three runs — all
  positive, a consistent-direction bias rather than noise. `specs.
  protection_paladin.calibrated` stays `false`. Named candidates for the gap:
  Divine Bulwark's mastery-scaled spell-block chance (unmodeled), the
  blocked-DoT absorb (unmodeled), Sanctuary's Consecration-linger DR
  (unmodeled), and a possible Ardent Defender parameter mismatch (spell data
  suggests 30%/12s/90s vs. the modeled 20%/8s/120s — unconfirmed).

## Method

1. `inventory` runs `detect_party_roles` over each CHALLENGE_MODE run to
   auto-detect the tank (name / spec / key / dungeon / success).
2. For the target spec's **timed** runs, `calibrate` hydrates each tank's
   `Character` from that run's `COMBATANT_INFO` (`hydrate_character`), loads
   the replay (`load_replay`), and sweeps K with per-replay characters.
3. **Judge at the canonical global K=3430** (`armor.k_constant`), not the
   sweep's best-K. A spec's best-K drifting far from 3430 is a *spec
   mitigation gap*, not a license to set a per-spec K.

## Inventory (`examples/bruttah-prot/*.txt`)

```
WoWCombatLog-070326_074225.txt[0] Maisara Caverns +14 success=None TANK=Brutoh-Uldum-EU spec=protection_warrior   <- excluded, different char, incomplete
WoWCombatLog-070326_141501.txt[0] Pit of Saron +2      success=True TANK=Bruttah-Uldum-EU spec=protection_paladin
WoWCombatLog-070326_152423.txt[0] Algeth'ar Academy +2 success=True TANK=Bruttah-Uldum-EU spec=protection_paladin
WoWCombatLog-070326_165249.txt[0] Magisters' Terrace +5 success=True TANK=Bruttah-Uldum-EU spec=protection_paladin
```

## F15 — the block-value fix

`classes/protection_paladin.py` used to apply a flat, pre-armor 30% cut on
every successful block. Corrected to route through the same armor-equivalent
curve as Warrior:

```python
if rng.random() < block_chance:
    result["was_blocked"] = True
    K = c["armor"]["k_constant"]
    block_dr = calculate_armor_resist(state.cached_block_value_rating, K, 1.0)
    damage *= 1 - block_dr
```

The original F15 roadmap sketch called for a Warrior-style crit-block roll
(`multiplier=1.0` regular / `2.0` crit). A validator audit against SimC's
`midnight` branch source refuted this before it shipped:
`paladin_t::target_block_resolution` (`sc_paladin_protection.cpp:903-923`)
returns only `BLOCK_RESULT_BLOCKED` / `UNBLOCKED` — crit block is implemented
entirely inside the warrior module, off Unwavering Sentinel mastery (spell
76857). Live Wowhead tooltip data for Divine Bulwark (76671, Paladin's
mastery) confirms no crit-block language. Shipped with **no crit-block roll,
multiplier always 1.0**.

Two sibling fixes landed in the same change, both surfaced by the same audit:

- `specs.protection_paladin.mastery_block_chance_scaling`: `0.5` → `1.0`.
  Spell 76671's live tooltip: 8% base mastery → +8.0% block chance, i.e. a
  1:1 coefficient; the shipped value was half that.
- Deleted a "Divine Bulwark mastery flat DR" step
  (`damage *= 1 - char.mastery_pct() * spec_cfg["mastery_dr_scaling"]`) that
  modeled a Dragonflight/TWW-era rank-2 Divine Bulwark effect. SimC's
  `midnight` branch shows rank 2 was repurposed into spell-block chance, and
  the old flat-DR role moved to the Sanctuary talent (not modeled). The
  deleted step was numerically a ~0.08% no-op with a landmine unit bug — its
  own comment implied a value 100× larger than what was coded.

Reviewed by an independent validator pass against the actual diff: correct
port, no mistakes, no live readers left of the deleted constants. One
adjacent, not-yet-landed finding from that review: the mastery block-chance
contribution should also flow through the F14 `_composite_block_dr` curve
(SimC applies it there; Warrior's path already does) — skipping it
over-credits Bruttah's block chance by an estimated ~1.5-2pp at her mastery
level. Left as-is for this PR; named here for the next pass.

## The hydrate bug — COMBATANT_INFO snapshot contamination

The first calibration attempt (armor hydrated from each run's LAST
COMBATANT_INFO snapshot, the pre-existing behavior) produced a **mixed-sign,
confusing** result:

| Run | real_dtps | armor (last snapshot) | delta @ K=3430 |
|---|---:|---:|---:|
| Pit of Saron +2 | 16,634 | 9,099 | −9.7% |
| Algeth'ar Academy +2 | 18,599 | 8,782 | −20.4% |
| Magisters' Terrace +5 | 21,220 | 4,880 | +31.5% |

RMSE 0.224, best-K=3430 (exactly canonical). The author's first hypothesis —
that the Magisters' Terrace armor (4,880) was an unrepresentative outlier and
the ~9,000+ values were the "steady state" — was **backwards**, caught by two
independent adversarial validator passes before it shipped. Forensic
cross-referencing of every COMBATANT_INFO snapshot's timestamp against Shield
of the Righteous buff-application events (spell 132403) on Bruttah showed a
12-for-12 correlation: every inflated snapshot coincided with SotR being
active at that exact instant, and the **unbuffed** value (5,124) replicated
identically across all three runs' CHALLENGE_MODE_START emissions (armor
can't be buffed before the key has even begun). Cross-check: AnonPPal1
(`anon_ppal1.yaml`, same spec, one tier earlier) sits at 4,669 — consistent
direction. Ruled out as alternatives: a mid-run gear swap (equipped item ids
identical across snapshots), deaths/durability loss (zero player deaths in
the window), and a genuine sustained armor-reducing effect (the low state at
Magisters' Terrace lasted 14.2 seconds, not the rest of the run).

Since the engine already models SotR's own damage reduction separately (the
100%-block window + a flat physical-DR term), hydrating its buffed armor on
top would double-count it. `_select_combatant_info`
(`io/character_from_combatant_info.py`) is now fixed for every non-Guardian
spec to prefer the minimum-observed total_armor across the run's snapshots —
the same trick the existing Guardian branch already uses (minimum stamina as
a proxy for "out of form") applied to a different contaminant. Re-hydrating
the same three runs now yields clean armor: 5,124 / 5,124 / 4,880 (the
Magisters' Terrace run's last snapshot happened to already be near-clean —
see table below).

**Full test suite (1926 tests) passes unchanged after the fix** — confirmed
no regression against the already-`calibrated: true` Warrior (16-log corpus)
and Guardian (16-log corpus), whose logs don't exhibit this contamination
pattern in the runs checked (Warrior armor was bit-identical in one spot-
checked log, ~1.4% oscillation in another — nowhere near Paladin's SotR-scale
swings).

## Calibration with clean armor

| Run | real_dtps | armor (clean) | delta @ K=3430 |
|---|---:|---:|---:|
| Pit of Saron +2 | 16,634 | 5,124 | +19.8% |
| Algeth'ar Academy +2 | 18,599 | 5,124 | +4.3% |
| Magisters' Terrace +5 | 21,220 | 4,880 | +31.5% |

RMSE 0.216 (300 iter), best-K=2500. **All three deltas are now positive** —
the sim over-predicts damage (under-predicts mitigation) on every run, a
consistent bias rather than the previous mixed-sign noise. Best-K landing
*below* canonical (2500 vs 3430) is the textbook signature of a model that
needs more DR credit than the armor curve alone provides — matching the
shape of Brewmaster's and VDH's own pre-fix over-prediction signatures,
though at much smaller magnitude here.

`specs.protection_paladin.calibrated` **stays `false`** — none of the three
runs are within ±15% at canonical K. This is nonetheless real progress: the
gap is now a single-direction, plausibly-explainable mitigation shortfall
instead of a confusing mix of over- and under-prediction masking a hydrate
bug.

## What was NOT done (named, not fixed)

- **Divine Bulwark's spell-block chance** — Midnight's mastery also grants a
  chance to block *spells* (SimC `sc_paladin.cpp`, "Mastery: Divine Bulwark
  Rank 2"), unmodeled in simf. Would reduce magic damage taken.
- **Blocked-DoT absorb** — SimC applies block-value-derived absorb to
  blocked periodic damage; unmodeled.
- **Sanctuary talent** — Midnight moved the old rank-2 Divine Bulwark
  flat-DR role here (5% DR while standing in Consecration + a 4s linger).
  `consecration_dr: 0.05` may already be a reasonable stand-in; unconfirmed.
- **Ardent Defender parameters** — a validator's live tooltip fetch suggested
  Midnight's AD may be 30% DR / 12s duration / 90s cooldown, versus the
  modeled `ardent_defender_dr: 0.20` / `_duration_s: 8.0` / `_cooldown_s:
  120.0`. Not independently confirmed by a second source — needs its own
  check before touching, since AD is load-bearing in the sim's death-
  prevention logic.
- **Re-running VDH's #143 characterization against the fixed hydrate path.**
  Demon Spikes (armor buff) and Metamorphosis (armor multiplier) expose VDH's
  existing corpus to the exact same contamination class. Cheap (same logs,
  no new data needed) — flagged in ROADMAP.md as the natural next move before
  further VDH magic-residual work.
  **RESOLVED 2026-07-03 — the WCL path is NOT exposed.** VDH's corpus hydrates
  through `wcl_combatant_info.character_from_wcl`, which never touches
  `_select_combatant_info` (no multi-snapshot selection exists to get wrong):
  WCL emits exactly ONE CombatantInfo event per player per fight, stamped at
  fight start. Verified empirically on all 3 corpus fights — snapshot ts sits
  at +0.0s while the first Demon Spikes window opens at +19.2/+6.5/+11.9s and
  the first Meta window at +21.4/+18.7/+11.9s, so no armor buff was up at
  snapshot time in any fight. The #143 armor inputs stand. See
  `phase4_vdh_painbringer_2026_07_03.md`.
- **Extending the corpus.** All three Bruttah runs are +2/+2/+5 — fine for
  characterization, but a wide extrapolation from the +14-18 range the key-
  level verdict actually targets. More logs across a wider key spread would
  tighten this.

## Next steps

1. ~~Re-run VDH's #143 characterization against the fixed `_select_combatant_info`~~
   DONE 2026-07-03: the WCL hydrate path turned out not to be exposed at all
   (one pre-combat snapshot per fight, verified outside every DS/Meta window on
   all 3 corpus fights) — see `phase4_vdh_painbringer_2026_07_03.md`.
2. Confirm or refute the Ardent Defender parameter mismatch against a second
   source (in-game tooltip or an independent SimC read) before touching
   `ardent_defender_dr` / `_duration_s` / `_cooldown_s`.
3. Model Divine Bulwark's spell-block chance and the blocked-DoT absorb —
   likely the two highest-leverage unmodeled layers given the magic-adjacent
   direction of the residual.
4. More Bruttah logs at higher key levels, when available, to reduce
   extrapolation distance and firm up the RMSE read.
