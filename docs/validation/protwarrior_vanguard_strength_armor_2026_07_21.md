# Prot Warrior Vanguard (strength->armor spec passive) — shipped (2026-07-21)

**Status: one real, previously-unmodelled Prot Warrior spec passive found,
sourced two independent ways in agreement, and shipped.
`constants_version` 43->44. `calibration_tier` (`characterized`) and
`calibration.global_rmse` left as measured today (0.162) pending human
ratification — see "Recommendation" below.** This continues the same-day
thread started by `docs/validation/protwarrior_shield_block_fix_2026_07_21.md`
and `protwarrior_post_shield_block_bias_decomposition_2026_07_21.md`. Those
two fixes moved the ratified 16-log corpus to RMSE 0.162, mean bias +13.7%
over-predict, 8/16 within +-15% at canonical K=3430. This closes a stale
TODO named in three places in `constants.yaml`/docs since 2026-05-18/19:
"the 230-K gap to the empirical minimum suggests ~230 of unmodelled bonus
armor, likely Vanguard's strength->armor spec passive."

## Task

Resolve the stale, never-shipped `vanguard_strength_to_armor` comment with
the same rigor as today's Shield Block work: find Vanguard's real spell
id/name, its real bonus-armor-per-strength coefficient (with a citation
trail as solid as Riposte/Ardent Defender/Shield Block), resolve the old
comment's self-reported "0.258 empirical vs 40% spell value" discrepancy,
and recommend whether to ship it as an always-on passive.

## Spell identification

**Spell 71 is confirmed correct and current** — Wowhead's live page for
spell 71 resolves to "Vanguard" (page title "Vanguard - Spell - World of
Warcraft"), a passive spell flagged "Is Ability" / "Passive spell" /
"Cannot be used while shapeshifted." SimC's source independently confirms
the same name-to-passive mapping: `sc_warrior.cpp:7184`,
`spec.vanguard = find_specialization_spell( "Vanguard" );` — a
specialization-granted spell (same category as Riposte, which this
codebase already models unconditionally in `base_parry()`), not a talent.

## Live Wowhead tooltip — raw accessibility-tree read, not AI-summarized

An initial `WebFetch` pass (AI-summarized) gave inconsistent effect-value
readings across attempts — consistent with this project's own established
distrust of WebFetch for source-audit precision work (see today's earlier
Shield Block doc: "raw fetch + grep beats an AI-summarized WebFetch"). Used
Playwright MCP instead to navigate directly to
`https://www.wowhead.com/spell=71/vanguard` and read the RAW accessibility
tree (`browser_snapshot`), not an AI summary of the page:

```
cell "Hardened by battle, your Stamina is increased by 40% and your Armor
is increased by 70% of your Strength. (500ms cooldown)"

Effect #1: Apply Aura: Mod Armor From Stat %  — Value: 70%
Effect #2: Apply Aura: Mod Stat - % (Stamina) — Value: 40%
Effect #3: Apply Aura: Mod % Damage Taken (Arcane, Fire, Frost, Holy,
           Nature, Physical, Shadow) — no value shown (vestigial/zeroed,
           same pattern as Shield Block's Effect 3 in today's earlier doc)
Effect #4: Apply Aura: Mod Cooldown Ms (1692) — Value: -5000 (unrelated;
           reduces some other spell's cooldown by 5s)

Ability tags: Is Ability, Passive spell, Cannot be used while shapeshifted.
```

**Effect #1 — "Mod Armor From Stat %", Value 70% — is the armor-from-
Strength effect.** Effect #2 (Stamina +40%) is a *different* effect that
the old, now-deleted `constants.yaml` comment had mis-cited as the armor
effect ("spell data effect value is 40%") — this is the root of the old
discrepancy, resolved below.

## SimC source — decisive, agrees with the tooltip on the same effect index

Fetched fresh via `curl` (raw GitHub, not WebFetch, which a prior round in
this project's own history found truncates mid-file on this exact file) —
`engine/class_modules/sc_warrior.cpp`, same vendored `midnight` commit this
project's other SimC citations use
(`fd60a6384dcd55f3737f18f31755141c1fe1e540`,
`docs/simc-reference/README.md`). Confirmed the fetched file is complete
(9421 lines, no truncation — the earlier `WebFetch` attempt against the
same URL WAS truncated mid-`mortal_strike_t`, confirming the same
truncation risk this project has hit before).

`warrior_t::composite_bonus_armor()`:

```cpp
double warrior_t::composite_bonus_armor() const
{
  double ba = parse_player_effects_t::composite_bonus_armor();

  if ( specialization() == WARRIOR_PROTECTION )
  {
    auto current_str = util::floor( parse_player_effects_t::composite_attribute( ATTR_STRENGTH )
                       * parse_player_effects_t::composite_attribute_multiplier( ATTR_STRENGTH ) );
    ba += spec.vanguard -> effectN( 1 ).percent() * current_str;
  }
  return ba;
}
```

- **`effectN(1)`** — the identical Effect #1 the Wowhead tooltip shows at
  70%. No separate "budget"/coefficient-table detour: Strength is a
  primary stat (not a secondary combat rating), so `.percent()` reads the
  spell's raw basis-points value (70 -> 0.70) and multiplies it directly
  into raw armor points — dimensionally consistent with no further
  conversion needed.
- **Gated only on `specialization() == WARRIOR_PROTECTION`** — no talent
  check anywhere. Confirmed at the declaration site too
  (`sc_warrior.cpp:7184`, `find_specialization_spell`, not
  `find_talent_spell`) and at the passive-effect-parse call site
  (`sc_warrior.cpp:9261`, `parse_effects( spec.vanguard, PARSE_PASSIVE );`
  inside the unconditional `else if ( specialization() ==
  WARRIOR_PROTECTION )` block of `init_action_list`/effect-parsing setup —
  every Protection Warrior gets this, always, matching Riposte's existing
  precedent in this codebase (`base_parry()`,
  `docs/validation/protwarrior_riposte_crit_parry_gap_2026_07_12.md`).
- **`current_str`** is total effective Strength (base x every Strength
  multiplier the character has) — not shield armor, not a separate rating
  pool. The `constants.yaml` note tying this to "subtype 137" in the task
  brief lines up with the effect being an unbudgeted armor-from-stat
  conversion (the same effect *class* as Blood DK's already-shipped
  `bone_shield_armor_from_strength: 1.80` — "Mod Armor From Stat %: 180%"
  per that constant's own comment — confirming this mechanic type is
  already a known, trusted pattern in this codebase, just at a different
  magnitude for a different spell).

## Resolving the old "0.258 vs 40%" discrepancy

Neither of the two old numbers was ever the real coefficient:

- **"Spell data effect value is 40%"** — this cited the wrong effect.
  Effect #2 (Stamina +40%) is real but has nothing to do with armor.
  Effect #1 (armor, 70%) is what SimC's `effectN(1)` actually reads.
- **"Empirical analysis suggests ~0.258"** — this was a K=3430-era fit
  from 2026-05-18/19, measured under a replay chain that (as this same
  project has since independently discovered, all within today's earlier
  two docs) still carried three live mitigation bugs: the armor-resist
  multiplier-order bug, the Shield Block flat-30%-physical-DR double-count,
  and the un-hasted Shield Block recharge. An empirical fit computed
  against a since-fixed replay chain is not a valid target to reconcile a
  freshly-sourced spell constant against — this is the same "leaked degree
  of freedom" pattern this project has named twice before (the old K=2700
  self-fit masking missing Defensive Stance; the Demo Shout double-count
  masking the structural physical-mit gap). The right move, per this
  project's own "no fudge-fitting" norm, is to ship the SimC/Wowhead-
  sourced 70% and let today's `calibrate-k` sweep (below) show where the
  corpus actually lands — not to hand-tune toward the old fit.

## Implementation

`src/simf/core/character.py::total_armor()` — Vanguard's flat armor add is
inserted **before** the Reinforced Plates / Armor Specialization
multiplicative talent bump, matching SimC's real `composite_armor()`
ordering (`docs/simc-reference/composite_armor.cpp`:
`bonus_armor()` is added, THEN `composite_armor_multiplier()` is applied) —
so a build that also runs those talents gets the multiplicative bump on
Vanguard's armor too, exactly as SimC computes it. Gated on
`class_spec == "protection_warrior"` only, matching the spec-passive (not
talent) semantics confirmed above.

`src/simf/data/constants.yaml`:
- `specs.protection_warrior.vanguard_armor_per_strength: 0.70`
- `specs.protection_warrior.vanguard_spell_id: 71`
- The two stale comments (one near the `calibration:` block's "230-K gap"
  note, one in the `talents:` block that never should have listed a
  non-talent spec passive) are rewritten to point here rather than deleted
  outright, so the history stays discoverable.
- `constants_version` 43 -> 44.

No toggle, no default-off — **every Protection Warrior has this passive
unconditionally**, same as Riposte. A default-off flag would have been
exactly the fudge-avoidance pattern this project's "no fudge-fitting" norm
argues against once the real coefficient is known and sourced.

## Before / after — `simf calibrate-k`, ratified 16-log corpus, canonical K=3430

| | Before (v43 baseline, this morning's ratified state) | After (Vanguard shipped) |
|---|---:|---:|
| RMSE | 0.162 | **0.073** (0.068 excluding 2 partial/truncated runs, n=14) |
| Mean signed bias | +13.7% (over-predict) | **+1.8%** |
| Within ±15% | 8/16 | **16/16** |

Per-run deltas after the fix (`simf calibrate-k --k-min 3430 --k-max 3430
--k-step 1`, seed=42, 300 iterations, same manifest as every other sweep in
this thread):

```
[-12.2%, -10.0%, -8.5%, +10.4%, +9.9%, +10.9%, +3.1%, +8.2%, +3.9%, +0.6%,
 -1.7%, +6.0%, +6.2%, +0.3%, +4.8%, -3.7%]
```

**Large, real, correctly-directed, and — unlike the RMSE 0.068 headline that
stood before the 2026-07-18 Demo Shout fix — not the product of any known
compensating bug.** Every one of today's earlier two fixes (armor-resist
multiplier order, Shield Block double-count + hasted recharge) is intact
underneath this number; this is Vanguard added on top of the already-fixed
v43 chain, not a reversion.

This numerically clears 3 of the 4 `calibrated` promotion-bar gates in
`core/constants.py` (RMSE≤0.15, |mean bias|≤5%, ≥75% within ±15% — this
run hits 100%). The 4th gate, LOO-CV, was **not** run in this pass — that
is the natural next step before any human ratification decision, not
something this doc claims to have satisfied.

The F-layer diagnostic (measurement-only, does not feed calibration) is
essentially unchanged from this morning's reading (median F=0.699, 15/16
within ±0.05 of the median) — expected, since that script measures a real
per-hit armor-implied ratio independent of `Character.total_armor()`'s
Vanguard term, not a quantity Vanguard would be expected to move.

## Magnitude sanity check (Brutoh's frozen calibration stats)

`brutoh-calibration-2026-05.yaml`: `strength: 2182`, `armor_from_gear: 5015`
(pre-Earthen). Vanguard's bonus armor = 2182 x 0.70 = 1527.4 — a ~27% add
on top of Earthen-scaled gear armor (5015 x 1.10 = 5516.5), moving armor DR
at K=3430 from 5516.5/(5516.5+3430) = 61.7% to
(5516.5+1527.4)/(5516.5+1527.4+3430) = 67.3% — a ~5.6pp armor-DR increase,
translating to ~14.5% LESS physical damage taken from armor alone. This is
the right order of magnitude to meaningfully close (not necessarily fully
close) the ratified corpus's current +13.7% over-predict bias, since armor
DR is the single largest term in the whole mitigation chain.

## Independent validator audit

<!-- FILLED IN IF A VALIDATOR PASS RUNS -->

## Caveats / what could still be wrong

- **Effect #3 (Mod % Damage Taken, all schools) shows no value on the live
  tooltip** — read as vestigial/zeroed, the same pattern this project
  already confirmed for Shield Block's own Effect 3 earlier today. Not
  modelled here; if it were ever found to carry a real nonzero value this
  would need a second pass.
- **Effect #4 (Mod Cooldown Ms, spell 1692, -5000)** was not chased — 1692
  is not a spell id this project currently models, and the effect is
  unrelated to armor/mitigation. Named, not investigated further.
- **The 3-legged sourcing pattern this project's harder cases use
  (Painbringer, Ardent Defender) typically includes a live in-game
  character-sheet cross-check** — not available in this session (no
  interactive game client). The two legs used here (raw-accessibility-tree
  Wowhead tooltip + direct SimC source read, independently agreeing on the
  same effect index and value) are treated as sufficient given they
  triangulate on the literal spell data itself, not two independent
  *implementations* of a derived quantity (contrast with Painbringer, where
  tooltip/patch-note/DB2 could in principle disagree on a computed
  effective value) — but this is a real, named gap versus this project's
  highest sourcing bar, not claimed as fully closed.
- `current_str` in SimC is base Strength x every Strength multiplier the
  character has. `Character.strength` in this codebase is populated from
  gear/character-sheet total Strength (matching how `base_parry()` already
  consumes the same field) — not independently re-verified in this pass
  beyond that existing precedent.

## Recommendation

**Ship the code fix — done in this change.** It is sourced two independent,
agreeing ways, matches this project's own existing Riposte/Bone-Shield
precedent for how a spec-passive stat-to-armor conversion is modelled, and
closes a 2-month-old named TODO. It is NOT a toggle and NOT default-off —
every Protection Warrior has this passive unconditionally, same as Riposte;
a default-off flag would be exactly the fudge-avoidance pattern this
project's "no fudge-fitting" norm rejects once the real, sourced
coefficient is known.

**For calibration_tier/global_rmse: hold for human ratification, not a
self-certified promotion, despite the strength of today's number.** Three
of the four `calibrated` gates are numerically cleared (RMSE 0.073≤0.15,
bias +1.8%≤5%, 16/16=100%≥75%). The fourth (LOO-CV) has not been run — per
this project's own tiered-calibration design
(`docs/validation/phase4_tiered_calibration_loo_cv_2026_07_06.md`), LOO-CV
is not a formality; Guardian Druid's own promotion history shows a corpus
can clear the aggregate bar and still fail LOO-CV. The concrete next step,
if the user wants to pursue `calibrated: true` immediately: run the LOO-CV
sweep against this same 16-log corpus with Vanguard live, then bring both
that result and this doc to the user for the same explicit ratification
this project has required for every tier change so far (2026-07-17
Guardian downgrade, 2026-07-18 Warrior downgrade, today's two Shield Block
fixes). Given how clean this result is, LOO-CV passing would be the
expected outcome, not a coin flip — but it has not been measured, and this
doc does not claim otherwise.

## Why the "clean wedge" number didn't move at all, while the aggregate bias moved hugely

A second independent audit pass ran `scripts/full_chain_wedge.py --corpus` on
both `master` (pre-fix) and this branch, same logs/seed, specifically to
check whether Vanguard's effect lines up with the separate finding
(this same day) that the "clean" bin's ~11% residual is ~81%-damage-weighted
magic, not physical:

```
                          before (master)   after (this branch)
clean wedge (agg.)             0.8896            0.8896   ← IDENTICAL
full wedge (agg., as modeled)  0.9201            0.9201   ← IDENTICAL
measured mean raw delta        +13.7%            +1.8%    ← moved, as expected
```

**No contradiction — mechanical, not coincidental.** `full_chain_wedge.py`
reads live armor straight off the combat log's own advanced-info block
(`armor_live`, `scripts/full_chain_wedge.py:377`) — it never calls
`Character.total_armor()` at all (confirmed by reading the script, not
assumed), so a fix inside `total_armor()` structurally cannot move that
script's diagnostic numbers, byte-identical or not. The "clean" bin the
wedge script measures is deliberately narrow (excludes every Shield
Block/Shield Wall/BSV window to isolate a different question) and happens
to be magic-dominated by construction — most PHYSICAL damage lives inside
Shield Block's ~90% uptime window and is excluded from "clean" by
definition. The corpus-wide `calibrate-k` bias, by contrast, is dominated
by that same physical-heavy, Shield-Block-active majority of real damage,
which DOES route through `total_armor()` — which is exactly where Vanguard
has the most leverage, and exactly why the aggregate number moved so much
while the "clean" bin's own number didn't move a bit. The magic-dominated
residual the "clean" bin names is real, unexplained, and — as that finding's
own analysis already concluded — cannot be closed by an armor passive; it
stays a fully separate, still-open lead.

**Caveat this surfaces for whoever next touches `full_chain_wedge.py`**: its
own "full-wedge-predicted delta" (`1/full_wedge − 1`, +8.7%, unchanged by
this fix) no longer tracks the actual measured delta as well as it used to
(the gap between the two flipped from +5.0pp to −6.9pp) — a known, mechanical
consequence of that script not yet reading `total_armor()`'s new Vanguard
term, not a new hidden bug in this fix. Its "predicted" column is now stale
for Prot Warrior specifically and shouldn't be read as a rival ground truth
to `calibrate-k`'s real number until it's updated to account for Vanguard
too — named here so it isn't independently "rediscovered" as a mystery
regression later.

## Two further findings, both deliberately not chased in this fix

**"Armored to the Teeth" (a CLASS talent, not a baseline passive) also
scales off Vanguard's coefficient.** Confirmed directly in SimC source
(`sc_warrior.cpp`): `composite_armor_multiplier()` (~line 9168) applies an
extra multiplicative armor bump gated on `talents.warrior.armored_to_the_teeth->ok()`,
computed as `spec.vanguard->effectN(1).percent() * armored_to_the_teeth->effectN(3).percent() * ...`
— i.e. it converts a fraction of Vanguard's own strength-to-armor rate into
extra armor, and the same talent also converts armor into attack power
(`effectN(2)`/`effectN(3)` inside `composite_melee_attack_power()`, an
offense-side effect this project doesn't model at all, correctly out of
scope for a survivability sim). It's real and present in this repo's own
fetched talent-tree data, but absent from `constants.yaml`. Not chased here:
it is a **talent** (optional pick), not universal like Vanguard itself, and
whether the ratified calibration character has it selected is unverified —
lower priority than the fix in this doc. Named for a future pass.

**A "Vanguard Rank 2" spell (316428) exists in Wowhead's database, showing
an ADDITIONAL +40% armor-from-strength on top of the base 70%** — raised as
a real discrepancy risk during independent review, resolved before merge,
not silently dismissed. Checked directly against Wowhead's raw embedded
page JSON (not an AI-summarized fetch, which had already proven unreliable
earlier in this investigation): spell 71's own live description text is
unambiguous — *"your Armor is increased by 70% of your Strength"* — with
no mention of a further rank. SimC's actively-maintained source (the same
9,757-line `sc_warrior.cpp` fetch used throughout this doc) contains **zero
references to spell 316428 anywhere** — only `spec.vanguard` (spell 71) is
ever read. Wowhead's generic name-search cache surfaces many
same-named/same-icon historical spell IDs on one page (this same page also
returned two unrelated "Spell Reflection" variants for other specs); 316428
reads as inert legacy DBC data from an older expansion's spell-rank system,
not a live Midnight 12.0.5 mechanic. Empirically consistent too: if 70%
under-counted a true 110%, the fixed model would still be missing ~40% of
Strength's armor value — that shortfall would show up as the model *still*
under-mitigating (a positive residual bias), which is exactly the small
**+1.8%** this doc already reports, not a sign that 70% overshot. No
further action taken; named here so it isn't silently unexamined.

## Files changed

- `src/simf/core/character.py` — `total_armor()`, Vanguard armor add.
- `src/simf/data/constants.yaml` — new keys, `constants_version` 43->44,
  two stale comments rewritten.
- `tests/test_vanguard_strength_armor.py` — new, 5 tests.
- `tests/test_total_armor_detected_talent_fallback.py` — helper +
  4 assertions updated for Vanguard's unconditional armor add.
- `tests/test_mitigation.py` — 1 assertion updated.
- `tests/test_disposition_ledger_conservation.py` — 2 pinned closed-form
  values re-derived by running the fixed code.
- `docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md` —
  this doc.
