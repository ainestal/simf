# Prot Warrior Shield Block double-count + block-value under-estimate — coupled fix, shipped (2026-07-21)

**Status: two engine bugs fixed together in `core/mitigation.py` + `data/constants.yaml`,
validator-confirmed. `constants_version` 41→42. `calibration_tier` (`characterized`) and
`calibration.global_rmse` (0.138) explicitly LEFT UNCHANGED — human ratification pending.**
This closes the specific lead named in
`docs/validation/protwarrior_full_chain_wedge_2026_07_21.md` ("Hand the Shield Block
double-count + block-value under-estimate finding to `validator`... for a proper coupled
fix"). Read that doc first if you haven't.

## Summary

Two real, independently-sourced bugs in Prot Warrior's block-mitigation chain were coupled
— fixing either alone made the ratified 16-log calibration corpus materially *worse*, which
is why they had to be diagnosed and fixed together:

1. **`core/mitigation.py::calculate_armor_resist`** applied its `multiplier` parameter to
   the input `value` *before* dividing by `k`. SimC's real formula (fetched fresh from
   GitHub, `engine/util/util.cpp`, branch `midnight`, 2026-07-21) divides first, then
   multiplies the *resulting fraction*:
   ```cpp
   double calculate_armor_resist( double armor, double armor_coeff, double multipler )
   {
     double resist = armor / ( armor + armor_coeff );
     resist *= multipler;
     resist = clamp( resist, 0.0, MAX_ARMOR_DAMAGE_REDUCTION );
     return resist;
   }
   ```
   These two forms are algebraically identical when `multiplier == 1.0` (armor DR, regular
   block, Protection Paladin's SotR — every call site in this codebase except one) but
   diverge sharply once `multiplier != 1.0`. The **only** such call site is Prot Warrior's
   crit-block roll (`multiplier=2.0`, `mitigation.py`'s step 3). At the frozen calibration
   character's block value (`shield_armor=989 × 2.5 = 2472.5`, K=3430): old formula gives
   `(2472.5×2)/((2472.5×2)+3430) = 0.5905`; SimC-correct gives
   `(2472.5/(2472.5+3430))×2 = 0.8377`. A ~24pp under-statement of crit-block magnitude.

2. **`active_mitigation.shield_block.physical_dr: 0.30`** (`mitigation.py`'s step "5b") was
   an extra flat 30% physical-damage-taken cut applied to *every* physical hit while Shield
   Block is active — regardless of whether that specific hit actually blocked. This does not
   exist in SimC's real mitigation chain and was a double-count of block value already
   modeled correctly at step 3.

Deleting bug 2 alone (already measured in the prior doc, using an in-memory constant patch,
not a file change) swings the corpus from +11.0% to **+32.4%** over-predict — much worse.
Fixing bug 1 alone (not separately measured, but derivable) *adds* mitigation credit on the
crit-block subset. The two bugs were coupled: bug 2's flat cut was partly compensating for
bug 1's under-statement of crit-block magnitude. **Both are fixed together in this change.**

## Root-cause investigation — each input checked, per the task brief

### Is the vendored SimC reference current for Midnight, or stale?

**Current, not stale.** `docs/simc-reference/README.md`'s own citation (`midnight` branch,
commit `fd60a6384dcd55f3737f18f31755141c1fe1e540`, WoW build `12.0.5.67602`) matches this
project's own K=3430 citation (`docs/simc-reference/expected_stat_level_90.txt`, same commit
and build). No Shield Block redesign has landed between that commit and today that would
make the vendored snapshot wrong.

### Live Wowhead tooltip for Shield Block (spell 132404)

Fetched 2026-07-21. Four real effects, three with an explicit populated value, one without:

| # | Effect | Value shown |
|---|---|---|
| 1 | Apply Aura: Modify Block % Value | **100%** (block *chance*, not value — see SimC below) |
| 2 | Apply Aura: Modifies Damage/Healing Done (Shield Slam) | **30%** (a DPS buff, not damage-taken) |
| 3 | Apply Aura: Mod % Damage Taken (All) | **no value shown anywhere** |
| 4 | Apply Aura: Modifies Critical Strike Chance (Shield Slam) | (DPS buff) |

Every *real, active* percentage effect on this spell (1, 2) shows an explicit number when
queried three separate ways (direct fetch, targeted effects-table re-fetch, line-by-line
transcription request). Effect 3 — the one that would correspond to `physical_dr` — never
does, across three attempts. Read together with the SimC source below, this is a vestigial,
zeroed DBC effect slot, not an active reduction; Shield Block most likely carried a real flat
damage-taken cut in some earlier expansion and the effect slot was never removed from the
spell record when it was redesigned, only zeroed.

### SimC source — decisive, not just corroborating

`docs/simc-reference/target_mitigation.cpp` (base `player_t::target_mitigation`) and
`warrior_target_mitigation.cpp` (Warrior's crit-block override) together are the complete
Prot Warrior mitigation chain SimC runs. Neither contains a flat physical-DR term for
Shield Block. The validator's independent re-fetch of `engine/class_modules/sc_warrior.cpp`
(`warrior_t::composite_block`, live 2026-07-21) found the literal source comment
`// shield block adds 100% block chance` directly above the line that adds
`effectN(1).percent()` to block **chance** — dispositive on its own: Shield Block's entire
real-game effect is guaranteeing the block roll. `composite_block_value()` is touched only by
Brace for Impact; Shield Block never appears there.

### Empirical corroboration (from the prior doc, re-confirmed here)

A pooled comparison of real logged block rate inside vs. outside parsed Shield Block windows
across the ratified corpus: 91.2% blocked (n=21,744) inside windows vs. 25.8% (n=457)
outside. A real ~30% flat layer predicts nothing about block *rate* directly, but the
ratio's magnitude (0.9635 on armor+versatility-only residuals — see the prior doc) is
inconsistent with a real ~30% layer, which would predict ~0.70.

### `character.py::block_value_rating()` — INPUT correct, not the bug

`shield_armor=989` (Brutoh's frozen calibration YAML) matches the documented in-game
tooltip ground truth (Spellbreaker's Rebuke at ilvl 295, post-voidcore, read 2026-05-20 — see
that file's own comment). `block_value_armor_multiplier: 2.5` is cited to
`engine/player/player.cpp:1681` and unchanged by this fix. This input was already correct;
the bug was downstream in how the resulting rating fed `calculate_armor_resist`.

### `character.py::critical_block_chance()` / mastery — investigated, real lead, HELD not shipped

`mastery_pct_warrior_prot`'s base value (`constants.yaml`, `0.12`) has no citation anywhere
in this file (unlike every coefficient around it — `mastery_crit_block_scaling: 1.5` and
`mastery_block_chance_scaling: 0.5` both cite SimC spell-effect data + an in-game tooltip
cross-check). Git history confirms `0.12` has been present, unchanged, since the very first
skeleton commit of `constants.yaml`, alongside several other now-long-since-replaced
"PLACEHOLDER" values. Three independent internal documents — the frozen calibration
character's own comment (`brutoh-calibration-2026-05.yaml`: "In-game shows: ...16.08%"),
`docs/simc-reference/AUDIT.md`'s F13 entry ("At Brutoh's ~16% mastery"), and
`docs/validation/simc_warrior_mastery_2026_05_19.md`'s own worked example ("at Brutoh's ~16%
mastery") — all implicitly or explicitly treat Brutoh's *total* mastery as ~16.08%, which is
*exactly* what the rating-only term (`mastery_rating/100/100 = 1608/100/100 = 0.1608`)
already produces with **no base added**. A separate 2026-07-11 memory
(`mastery_rating_per_pct_warrior_paladin_real_value`), using live Blizzard Armory API data
independent of the hand-authored calibration YAML, triple-confirms ~16.08% with no base term
needed (Armory `rating_normalized/rating_bonus`, raw COMBATANT_INFO rating, and a structural
identity all converge on the same ~30.67 rating-per-percent with zero residual). Maxroll's
current Midnight stat-DR page (already a source cited elsewhere in this same file) describes
Mastery as purely rating-proportional with no baseline discussion. Set against this: a
general (non-Midnight-specific) wiki.gg page claims Mastery has historically carried an
inherent "~8 base points" baseline for some specs — unconfirmed for the current patch, and
exactly the kind of cross-expansion content collision this project's own memory
(`feedback_websearch_expansion_content_collision`) warns distorts AI web summaries.

**This was NOT shipped in this PR.** The independent validator audit found a sharper reason
to hold it than the sourcing gap alone: with the crit-block formula fixed (bug 1 above) and
mastery **unchanged** (base=0.12, `crit_block_chance=28.08%×1.5=42.12%`), the modeled blended
block-value magnitude moves from **49.1%** (pre-fix; matches the wedge doc's independently
measured "modeled ~49%" exactly) to **59.5%** — close to the corpus's own log-measured real
block magnitude of **~57%** (`docs/validation/protwarrior_full_chain_wedge_2026_07_21.md`).
If the mastery base were also zeroed (`crit_block_chance` drops to `16.08%×1.5=24.12%`), the
blended figure would instead land at **~51.8%** — *below* the corpus's own measured 57%, a
worse fit than leaving the (uncited) base in place. In other words: the crit-block formula
fix alone, using today's mastery constant, already closes the block-value-magnitude half of
the original gap almost exactly; deliberately breaking a second, differently-uncertain
constant to chase a cleaner derivation would make the fit worse, not better, and would bundle
a weakly-evidenced change into a well-evidenced one — this project's own "no fudge-fitting"
and "engine-batch-ratification cap" norms both argue against that. **Held as a named,
unresolved lead.** The validator proposed a concrete, cheap next step that would settle it
independent of both suppositions: measure the real observed `CRIT_BLOCK / BLOCK` hit-count
ratio directly from the corpus's own logs (pure log parsing, no sim). If it's ~42%, the
current (uncited) mastery constant is empirically vindicated and the base-value question
becomes moot for calibration purposes (though it would still deserve a real citation
eventually); if it's closer to ~24%, the base is very likely spurious **and** a second,
currently-unidentified source of block-value magnitude (e.g. a higher effective
`block_value_armor_multiplier`, or an unmodeled spec bonus) would be needed to still reach
57%. Not attempted in this session — this is exactly the kind of "measure per-run F, judge
before touching a constant" work this project routes through `calibration-scientist`, not a
validator constant flip, and mastery's rating-to-percent conversion is itself already a known
open self-fit (`mastery_rating_per_pct_warrior_prot: 100`, documented as synthetic in the
2026-07-11 memory) — changing the base without also resolving the conversion would replace
one coupled fudge with another.

### `calculate_armor_resist()` re-derivation — confirmed the formula bug (see Summary above)

### Block roll frequency (`block_chance_during: 1.0`) and crit-block roll frequency

Block-chance-during-SB (100%, guaranteed) matches SimC's `composite_block()` exactly (see
above) and is corroborated by the corpus's own 91.2% observed block rate inside SB windows —
not a frequency mismatch. Crit-block roll *frequency* is the mastery question above, held.

### `MAX_ARMOR_DAMAGE_REDUCTION` (0.85) cap engagement

Checked explicitly, per the task's specific ask. Across all 33,235 ratified-corpus hits,
maximum observed live armor is 6,262 (`scripts/full_chain_wedge.py`'s own cap-check, 0
capped) — armor DR never approaches the cap. For block value specifically: at Brutoh's
`block_value_rating=2472.5`, the **corrected** crit-block fraction (0.8377) sits close to but
does not exceed 0.85 — not capped for this specific character today, but the cap *is* now
reachable in principle (it never was under the old, buggy formula, which needed an
implausibly large block-value rating to reach 0.85 — see the rewritten
`test_crit_block_caps_at_max_armor_dr_not_one`, which previously asserted an UNcapped 0.75 at
a 60% base block fraction despite its own docstring claiming to test the cap engaging; with
the fix, that exact input now correctly clamps at 0.85).

## The fix

- `src/simf/core/mitigation.py`, `calculate_armor_resist()`: reordered to divide first, then
  multiply the resulting fraction by `multiplier`, then clamp — matching SimC's real
  `util::calculate_armor_resist` exactly (docstring updated with the citation and the
  Brutoh-scale worked example).
- `src/simf/core/mitigation.py`, step "5b": the `damage *= 1 - shield_block.physical_dr` line
  deleted outright, replaced with a comment explaining why (SimC source + live tooltip +
  empirical block-rate evidence, condensed from the sections above).
- `src/simf/data/constants.yaml`: `active_mitigation.shield_block.physical_dr` set to `0.0`
  (kept, not deleted, so any stale reference is a documented no-op rather than a KeyError —
  nothing in the engine reads this key anymore). `constants_version` 41→42 with a full
  changelog entry. Comment-only pointers added near `calibration.global_rmse` and
  `specs.protection_warrior.calibration_tier` — **no values in either location changed.**

## Before / after — `simf calibrate-k`, ratified 16-log corpus, canonical K=3430, seed=42, 300 iterations

| | Before (this session's baseline) | After (both fixes) |
|---|---:|---:|
| RMSE | 0.1339 | 0.2498 |
| Mean signed bias | +11.0% (over-predict) | +23.0% (over-predict) |
| Within ±15% | 10/16 | 4/16 |

Per-run deltas, before → after (same order both runs):

| Run | Before | After |
|---|---:|---:|
| Algeth'ar Academy +12 | −6.9% | +1.6% |
| Skyreach +14 [partial] | +0.1% | +7.4% |
| Algeth'ar Academy +14 | +2.2% | +10.9% |
| Windrunner Spire +14 | +20.4% | +39.6% |
| Windrunner Spire +14 [partial] | +20.9% | +34.6% |
| Magisters' Terrace +14 | +19.6% | +31.4% |
| Nexus-Point Xenas +12 | +7.0% | +13.4% |
| Magisters' Terrace +12 | +19.6% | +29.0% |
| Pit of Saron +13 | +12.3% | +28.4% |
| Maisara Caverns +12 | +10.3% | +22.1% |
| Windrunner Spire +12 | +7.0% | +24.0% |
| Pit of Saron +12 | +15.3% | +28.4% |
| Maisara Caverns +14 | +15.6% | +27.4% |
| Pit of Saron +13 (2nd) | +8.1% | +21.1% |
| Magisters' Terrace +13 | +14.4% | +24.7% |
| Pit of Saron +14 | +9.3% | +23.5% |

**This is worse on every headline number, and this is the correct, honest result of two
real fixes, not a sign either fix is wrong.** Confirmed both analytically and by an
independent validator pass: removing the Shield Block layer removes a *broad, unconditional*
mitigation cut that fired on every physical hit during Shield Block's ~80%+ fight-time
uptime, regardless of whether that specific hit actually blocked; fixing
`calculate_armor_resist` only adds mitigation back on the *narrower* subset of hits that
also roll a crit block (~35-42% of blocked hits, depending on the held mastery question).
The two real fixes' net effect widens the pre-existing, already-documented "structural
physical-mit gap" (`CONTRIBUTING.md` roadmap item 5 — a residual this project has tracked since
well before this session, previously estimated at "~10pp," now measured much more sharply at
~23pp once these two masking bugs are correctly removed) rather than closing it. This is the
same "leaked degree of freedom" pattern this project has hit twice before (the old K=2700
self-fit masking missing Defensive Stance; the Demoralizing Shout double-count masking this
same structural gap one layer up) — a bug canceling a separate, real deficiency, and the
aggregate number getting *more* honest, not *better*, once the bug is removed.

## `scripts/full_chain_wedge.py --corpus`, post-fix

| | Before | After |
|---|---:|---:|
| clean wedge | 0.8896 | 0.8896 (unchanged — doesn't touch block) |
| full wedge, AS MODELED | 1.2608 | 0.9201 |
| full wedge, SB excluded | 0.9201 | 0.9201 |

`full` now equals `full_no_sb` exactly, as expected — the wedge tool needed no code changes;
it reads `active_mitigation.shield_block.physical_dr` live, and with that constant at `0.0`
the "as modeled" and "SB excluded" readings collapse to the same (sane, ≤1.0) number. This
confirms the double-count is gone from the tool's own perspective, independent of the
`calibrate-k` sim run above.

## Independent validator audit

A `validator` agent (engine-math mode) independently re-derived every claim in this doc
before it was finalized — re-fetched SimC's source directly from GitHub itself (not trusting
the vendored snapshot or this doc's transcription), re-read the entire `apply_mitigation`
function end-to-end looking for a wiring bug that could spuriously explain the worse RMSE,
and independently re-derived the 49.1%→59.5% block-magnitude reconciliation from the
character's raw stats rather than checking my arithmetic. Verbatim conclusions: both engine
fixes **CONFIRMED CORRECT** against SimC source ("the fix reproduces it exactly... My own
live fetch... returns exactly... byte-for-byte" for the formula; "confirmed by SimC's own
source... the literal source comment `// shield block adds 100% block chance`" for the
double-count). The worse RMSE was independently confirmed to be the **honest** consequence
of two real fixes, not a new bug ("nothing double-fires, chain order is intact... fully
consistent with +11%→+23%"). The decision to hold the mastery-base question was independently
endorsed, on a sharper basis than this doc's own original reasoning (see that section above —
the validator is the one who ran the 49.1%/59.5%/51.8%-vs-57% reconciliation that settled it).
The validator also caught two process gaps before this doc existed: the fix was sitting
uncommitted in the working tree (addressed — see "Files changed" below), and this doc itself
did not yet exist at the path the code comments already cited (addressed by writing it).

## Caveats / what could still be wrong

- **The mastery-base question is genuinely unresolved**, not just deferred out of caution.
  The evidence for a spurious `0.12` base is real (three internal ground-truth citations, one
  independent Blizzard-API-sourced memory, no citation ever provided for the value itself)
  but the evidence for keeping it is also real (it currently produces a better empirical fit
  to the corpus's own measured block magnitude than removing it would). Both cannot be fully
  right; the validator's proposed real-log `CRIT_BLOCK/BLOCK` ratio measurement is the
  cheapest way to settle it and has not been run.
- **The widened ~23pp structural gap is now the sharpest version of roadmap item 5 this
  project has measured**, but this doc does not attempt to explain *what* the remaining gap
  is — only that it is real, was previously partially masked by these two bugs, and is not
  itself a new finding (the gap's existence was already tracked before this session).
- **`calibration_tier`/`global_rmse` are unchanged and this is an explicit, load-bearing
  choice, not an oversight** — this fix makes the engine's block mechanics more faithful to
  SimC/the real game, which is the mandate this session was given, but it does NOT improve
  the aggregate fit, and this project's own norm is that a human, not the agent making the
  fix, ratifies any tier/RMSE change. See "Recommendation" below.
- The live Wowhead effect-table reads for spell 132404 (Shield Block) came from three
  separate `WebFetch` queries against the same page, not a raw HTML/JSON tooltip payload
  (direct `curl` to wowhead.com 403s from this box's IP — a pre-existing, unrelated
  infrastructure fact, not new to this session). The SimC source-code confirmation is treated
  as decisive on its own; the tooltip reads are corroborating, not load-bearing alone.

## Recommendation (for human ratification, not a decision made here)

**Ship the two engine fixes — they are correct, SimC-source-confirmed, and validator-audited
twice over (once by the agent that found the bug earlier today, once independently by this
session's own validator pass).** Do **not** treat the resulting RMSE 0.250 / +23.0% bias as
grounds to touch `calibration_tier` or `global_rmse` in this same change — that is a separate
decision this project has repeatedly reserved for explicit human ratification after seeing
the number, not something an agent self-certifies in the same PR as the code fix (see the
2026-07-18 Demo Shout downgrade and the K=3430-vs-empirical-refit precedent, both cited
elsewhere in `constants.yaml`, for why). The natural next step, if the user wants to keep
chasing the now-more-sharply-quantified structural physical-mit gap, is the validator's
proposed cheap experiment (real `CRIT_BLOCK/BLOCK` ratio from the corpus's own logs) — that
would resolve the mastery-base tension AND narrow the search for whatever else composes the
remaining ~23pp gap, without touching K (which stays pinned at 3430 per this project's
standing rule) and without inventing a new constant to force the number down.

## Tests

- `tests/test_block_armor_curve.py` — `test_armor_resist_multiplier_two_doubles_the_value`
  rewritten to expect the SimC-correct fraction-doubling formula;
  `test_crit_block_caps_at_max_armor_dr_not_one` now correctly asserts the 0.85 cap actually
  engages (previously asserted an uncapped 0.75, contradicting its own docstring);
  `test_crit_block_below_break_even_lower_than_legacy_double` replaced with
  `test_crit_block_below_cap_equals_flat_double`, documenting the real relationship (curve
  and flat-% doubling coincide below the cap, diverge only above it);
  `test_apply_mitigation_crit_block_matches_armor_curve_with_multiplier_two`'s stale
  "always below legacy flat-30%" assertion removed (no longer true at this fixture's real
  block value) and replaced with a direct `crit_dr == 2×regular_dr` sanity check.
- `tests/test_mitigation.py` — the three Shield-Block-layer tests
  (`test_shield_block_physical_dr_layer` and two siblings) rewritten as
  `test_shield_block_active_adds_no_flat_physical_dr_layer` /
  `..._magic_dr_layer`, asserting SB-active vs SB-inactive produce IDENTICAL dealt damage for
  a non-blockable event — the actual regression test for the double-count bug, not a test
  that reads whatever the (now-dead) constant says and would pass even if it were
  accidentally restored to a nonzero value.
- `tests/test_disposition_ledger_conservation.py` — one pinned closed-form value
  (`test_dealt_pinned_blockable_sb_active`) re-derived by running the fixed code (94274.45→
  64685.77), with a comment explaining why and pointing here.
- `tests/test_full_chain_wedge.py` — `test_shield_block_window_diverges_full_from_full_no_sb`
  renamed and re-purposed to `test_shield_block_window_full_equals_full_no_sb_post_fix`,
  since the two readings now equal each other rather than differing by the (now-zeroed)
  constant; added the missing `import pytest`.
- Full suite: **2705 passed, 21 skipped** (was 2705 passed pre-fix too — no count change,
  purely rewritten assertions). `ruff check` + `ruff format --check` clean on every touched
  Python file. `mypy src/` shows the same 28 pre-existing errors as before this change, none
  in touched files.

## Files changed

- `src/simf/core/mitigation.py` — `calculate_armor_resist()` reordered; step "5b" deleted.
- `src/simf/data/constants.yaml` — `active_mitigation.shield_block.physical_dr` 0.30→0.0;
  `constants_version` 41→42; comment-only notes near `calibration.global_rmse` and
  `specs.protection_warrior.calibration_tier`.
- `tests/test_block_armor_curve.py`, `tests/test_mitigation.py`,
  `tests/test_disposition_ledger_conservation.py`, `tests/test_full_chain_wedge.py` — updated
  per "Tests" above.
- `docs/validation/protwarrior_shield_block_fix_2026_07_21.md` — this doc.
