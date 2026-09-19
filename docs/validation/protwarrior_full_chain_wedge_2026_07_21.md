# Prot Warrior full-chain wedge — the +11% bias is not what it looks like; a real, validator-confirmed Shield Block double-count was masking a much larger gap (2026-07-21)

**Status: measurement + a new engine-bug lead, both validator-audited. No constants.yaml value
changed, no `calibration_tier`/`global_rmse` change.** This closes out the fast-follow named in
`docs/validation/protwarrior_f_layer_measurement_2026_07_21.md` ("a properly-scoped follow-up
would use `scripts/per_hit_mitigation_forensics.py`... dividing out the full modeled chain per
hit across all 16 logs") — but the honest answer is more consequential than that doc anticipated,
and the naive way to answer it (compare a per-hit wedge against calibrate-k's sim-vs-real delta)
turned out to be **methodologically invalid**, caught by an independent validator audit before
this doc was finalized — the same discipline that caught the invalid `sim × F` shortcut one doc
earlier in this same thread. Read that in full first if you haven't:
`docs/validation/protwarrior_f_layer_measurement_2026_07_21.md`.

## Summary

A new corpus-wide tool (`scripts/full_chain_wedge.py`) measures, per hit, the fraction of real
logged damage the model's full mitigation chain does NOT explain — extending
`docs/validation/phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`'s "Finding A" (which
divided out armor × vers × the warrior's modeled always-on chain by hand, on exactly 2 of the 16
ratified logs, giving a clean residual ×0.94 bracketed 0.91-0.95 on Brace for Impact stack
uncertainty) to **all 16 ratified logs**, with every stack/window read from the log's own ground
truth instead of guessed:

- **Brace for Impact's real per-hit stack count** comes from the log's own aura dose events
  (spell 386029) — no more bracket.
- **Shield Wall / Battle-Scarred Veteran's real windows** come from
  `io.combat_log_buffs.parse_self_buff_windows` (spell 871 / 386397).
- **`Character._always_on_dr()` and `Character._talent_set()`** are called directly on the real
  hydrated calibration character — Defensive Stance × Indomitable is never hand-derived from
  constants.yaml.
- **The armor-DR cap** (`min(armor/(armor+K), max_armor_dr)`) is applied per hit — the two
  existing sibling scripts (`measure_run_f.py`, `per_hit_mitigation_forensics.py`) both compute
  this uncapped. Checked and confirmed **inert on this corpus**: max observed live armor across
  33,235 ratified hits is 6,262 (K=3430's cap threshold is ≈19,437) — 0 hits ever capped. Fixed in
  this tool for correctness; not touched in the two older scripts (named, not fixed, per the
  no-scope-creep instruction).

While building this, the tool surfaced something the original plan didn't anticipate: **Shield
Block's modeled `active_mitigation.shield_block.physical_dr: 0.30` layer (`mitigation.py`'s "step
5b" — a flat −30% physical damage-taken cut applied while Shield Block is up, on top of block
value) does not correspond to anything in Brutoh's real combat logs, and a validator audit
independently confirmed it is very likely a real double-count bug against SimC's own mitigation
model** — see "The Shield Block finding" below. That single layer, once divided into a "full
chain" wedge, drove the aggregate wedge to 1.26 (mathematically impossible to trust — a wedge
above 1.0 means the chain is over-explaining reality, the signature of dividing by something that
never happened). Excluding just that one factor drops the wedge to a sane 0.92 — but a validator
check of what happens if you actually remove it from the live sim (not just this diagnostic tool)
shows the reconciliation is **not that simple**: see below.

## Method

`scripts/full_chain_wedge.py`'s `compute_run_wedge()`, per hit:

```
r          = (amount + absorbed + blocked) / base_amount        (real, from the log)
armor_dr   = min(armor_live/(armor_live+K), max_armor_dr)        [physical, non-bleed]
base_chain = (1 - armor_dr) * (1 - vers) * always_on_dr * bfi_factor
full_chain = base_chain * sb_factor * sw_factor * bsv_factor     (whichever are active)
resid      = r / chain
```

Two residual figures per run, both base_amount-weighted:

- **clean** — `resid` with `chain = base_chain`, restricted to hits OUTSIDE every Shield
  Block / Shield Wall / Battle-Scarred Veteran window. Directly comparable to Finding A's ×0.94.
- **full** — `resid` with `chain = full_chain`, over ALL hits — reported in two variants,
  `full` (as `mitigation.py` literally specifies, includes the Shield Block factor) and
  `full_no_sb` (same, Shield Block factor omitted) — see the SB finding for why both are shown.

**Deliberately NOT divided out** (each already checked, not assumed):

- **Demoralizing Shout / Phalanx** — attacker-side, already inside `base_amount`
  (`docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md`).
- **Block chance / block value / dodge / parry** — already reflected in `r` itself
  (`amount + blocked + absorbed` is what really happened).
- **`party_dr_by_school`** — its own constants.yaml comment claims it applies "in replay mode",
  but grepping every write site of `state.party_magic_dr_active` shows it is **never** set `True`
  in `runner.py`/`log_replay.py`'s production path — only in tests and a standalone measurement
  script. It is structurally inert during a real `calibrate-k` sweep today (validator-confirmed,
  see below) — a real, pre-existing doc/comment inaccuracy, named here, not fixed (out of scope).
- **Fight Through Flames / Unyielding Stance** — not in the `brutoh-actual` loadout every ratified
  log runs; the code still gates on the talent being present, so a future corpus with a different
  loadout stays correct without changes.
- **Last Stand** — surprising against the task brief, which assumed it was a DR layer: it has NO
  `damage *=` line in `mitigation.py`. It only raises max_hp. Zero DR factor, so it's not tracked
  at all.

Last Stand aside, this is the exact set of layers `mitigation.py`'s warrior replay path applies —
confirmed by a validator's line-by-line enumeration of every `damage *=` in the file.

## Results — ratified 16-log corpus (`simf calibrate-k`'s manifest, K=3430, 300 iters, seed 42)

| Run | n_hits | clean | full (as-modeled) | full (SB excluded) | raw sim-vs-real delta |
|---|---:|---:|---:|---:|---:|
| Algeth'ar Academy +12 | 2,109 | 0.8917 | 1.3418 | 0.9935 | −6.9% |
| Skyreach +14 [partial] | 938 | 0.9063 | 1.2559 | 0.9089 | +0.1% |
| Algeth'ar Academy +14 | 2,114 | 0.8542 | 1.3354 | 0.9766 | +2.2% |
| Windrunner Spire +14 | 2,270 | 0.8569 | 1.2439 | 0.8921 | +20.4% |
| Windrunner Spire +14 [partial] | 1,473 | 0.8217 | 1.2018 | 0.8649 | +20.9% |
| Magisters' Terrace +14 | 2,925 | 0.8408 | 1.1821 | 0.8652 | +19.6% |
| Nexus-Point Xenas +12 | 1,318 | 0.8863 | 1.1803 | 0.9076 | +7.0% |
| Magisters' Terrace +12 | 2,902 | 0.8790 | 1.1874 | 0.8855 | +19.6% |
| Pit of Saron +13 | 2,084 | 0.9225 | 1.3207 | 0.9530 | +12.3% |
| Maisara Caverns +12 | 2,038 | 0.8449 | 1.2002 | 0.8804 | +10.3% |
| Windrunner Spire +12 | 1,913 | 0.9848 | 1.3469 | 0.9639 | +7.0% |
| Pit of Saron +12 | 1,805 | 0.9123 | 1.2921 | 0.9369 | +15.3% |
| Maisara Caverns +14 | 1,884 | 0.8813 | 1.2165 | 0.8820 | +15.6% |
| Pit of Saron +13 (2nd) | 2,273 | 0.9115 | 1.3240 | 0.9540 | +8.1% |
| Magisters' Terrace +13 | 2,658 | 0.8786 | 1.2343 | 0.9082 | +14.4% |
| Pit of Saron +14 | 2,531 | 0.9685 | 1.3150 | 0.9523 | +9.3% |

**Aggregate (hit-count-weighted, n=33,235 hits, 16 runs):**

| | value |
|---|---:|
| clean wedge | **0.8896** |
| full wedge, AS MODELED (incl. Shield Block) | **1.2608** |
| full wedge, Shield Block excluded | **0.9201** |
| armor-DR cap check | 0/33,235 hits exceeded max_armor_dr before capping; max observed live armor 6,262 |
| measured mean raw sim-vs-real delta (calibrate-k, same corpus) | **+11.0%** |

Extending Finding A: the clean wedge (0.8896) is in the same direction and same rough
neighbourhood as the Brewmaster-doc's ×0.94 Nexus-Point/Windrunner numbers, now measured
precisely (exact BfI stacks, not a 0.91-0.95 bracket) across all 16 logs instead of 2 — the
"universal unexplained cut" is real, and closer to ~11% than ~6% once Shield Block/Shield
Wall/Battle-Scarred-Veteran windows are properly excluded from the comparison set (the original
2026-07-04 hand-calc's "clean" bin did not exclude Shield Block windows at all — Shield Block's
own aura, real in this corpus, covers 80%+ of most fights' physical-hit time, so its inclusion in
that bin's denominator was itself part of what masked the true clean-hit gap).

## Wide corroboration set — 25 runs / 13 files, systematic sample (NOT part of the K-calibration corpus)

Per this session's brief: use the wider personal log archive as corroboration for the wedge
question only — never as an input to `calibration_tier` or `global_rmse`, and never silently
promoted to "the corpus" (see `docs/validation/corpus_expansion_rmse_drift_2026_07_12.md` for why
that distinction matters).

`examples/` (repo root, gitignored, main checkout only) contains 119 raw `WoWCombatLog-*.txt`
files; 49 contain `Brutoh-Uldum-EU`, of which 12 unique files (16 manifest entries) are the
ratified corpus. Of the remaining 37, a **systematic sample** (sorted chronologically by
filename/timestamp, every 3rd file, 13 files spanning 2026-05-18 to 2026-07-11 — roughly the full
2-month archive, ~1.9 GB) was scanned. No sim-delta reproduction on this set (that would require
re-hydrating each log's own gear snapshot to stay honest about 2 months of gear drift — out of
scope for a corroboration pass); only the per-hit wedge (pure log parsing, no Monte Carlo,
borrowing the frozen calibration character's talents only — Indomitable/BfI/BSV, which don't drift
week to week the way gear does).

**Aggregate (25 runs, 47,923 hits):**

| | value |
|---|---:|
| clean wedge | **0.8373** |
| full wedge, AS MODELED (incl. Shield Block) | **1.2081** |
| full wedge, Shield Block excluded | **0.8924** |
| armor-DR cap check | 0/47,923 hits capped; max observed live armor 7,716 |

Both the universal wedge (0.8373 vs the ratified corpus's 0.8896 — same neighbourhood, slightly
lower) and the Shield Block anomaly (full-as-modeled 1.2081 > 1.0, full-SB-excluded 0.8924 — same
shape) **reproduce independently** on a sample more than 50% larger by hit count and spanning key
levels +10 to +17 across a different two months of play. This is real corroboration, not a
one-corpus artifact.

## The Shield Block finding — validator-confirmed likely double-count, coupled to a second bug

### What was found

`scripts/_sb_layer_check.py` pooled every ratified-corpus physical, non-bleed hit into "inside a
real logged Shield Block window (spell 132404)" vs "outside every SB/SW/BSV window", comparing
armor+versatility-only residuals with real statistical power:

| | n | armor+vers-only residual (wmean) |
|---|---:|---:|
| SB-active | 21,402 | 0.7438 |
| SB-inactive | 433 | 0.7720 |
| **ratio** | | **0.9635** |

A real −30% layer predicts a ratio of ~0.70. The measured ratio is 0.9635 — at most a ~3.65% real
difference, not 30%.

### Independent validator audit (not taken on faith)

A `validator` agent (engine-math mode) independently re-derived this from source, without being
handed the interpretation above to check against, and found:

1. **SimC source confirms the layer is spurious.** `docs/simc-reference/target_mitigation.cpp` and
   `warrior_target_mitigation.cpp` are the SimC warrior mitigation chain: `da_multiplier` → armor
   → block (block value through the same `calculate_armor_resist` curve simf's step 3 already
   uses) → crit block. **There is no separate flat physical-DR term for Shield Block anywhere in
   it.** Shield Block's entire real-game benefit is guaranteeing the block; `mitigation.py`'s step
   5b (`damage *= 1 - 0.30`, unconditional on `is_log_replay` — fires in both synthetic and
   replay) is an extra layer stacked on top of an already-correct block model.
2. **A stress-test that could have falsified the finding, didn't.** Could the parsed SB "windows"
   be over-credited (duration-extended beyond the tooltip's 6s by repeated Shield Slam casts),
   filling the "SB-active" bucket with false positives that would *mask* a real layer? Decisive
   check: Shield Block guarantees a block, so genuine SB windows must show ~100% of hits blocked.
   Measured: **91.2% blocked inside parsed windows vs 25.8% outside** (n=21,744 / 457) — the
   windows are real, not a parser artifact; the masking hypothesis is refuted.
3. **A second, coupled bug, found independently by the validator, not anticipated by this doc's
   plan:** the sim's own block-VALUE model under-represents real block magnitude. Real in-game
   block reduction on blocked hits (weighted, n=19,946, `blocked/(amount+blocked+absorbed)`) is
   **~57%**; the sim's `calculate_armor_resist(block_value_rating, K, multiplier)` model gives
   ~42% regular / ~59% crit, blending to **~49%** — an ~8pp under-estimate.
4. `compute_run_wedge`'s own code was independently audited end-to-end (every `damage *=` line in
   `mitigation.py` enumerated and matched against what the wedge chain divides out or correctly
   excludes) and found correct; `_sb_layer_check.py`'s methodology was found sound; the
   `party_dr_by_school`-is-inert conclusion was independently confirmed; the new tests were
   hand-re-derived and found meaningful.

### Why this does NOT mean "the +11% bias becomes ~+9% once you exclude Shield Block" — the trap this doc almost fell into

The naive move — divide the wedge by everything except Shield Block, get 0.9201, predict a
`1/0.9201 - 1 ≈ +8.7%` bias, compare to the measured +11.0%, declare a "+2.3pp residual, mostly
explained" — **is invalid, and the validator caught it before this doc was finalized** (the same
gate that caught the invalid `sim × F` shortcut one doc earlier in this thread). Verified directly
(not just asserted) by monkeypatching `shield_block.physical_dr` to 0.0 in memory and re-running
`run_simulation` against a subset of the ratified replays — see the verified numbers below.

The reason the naive reconciliation fails: the wedge's divisor never includes a block term (block
is already inside `r`, correctly), but the **sim** re-rolls block using the character's own
(under-estimating, ~49% vs real ~57%) block-value model. Removing the spurious Shield Block layer
from the sim doesn't just delete a double-count — it also deletes the (accidental) compensation
that layer was providing for the sim's separate block-value under-estimate. The two errors
partially cancel today; removing only one makes the corpus **worse**, not better — the same
"leaked degree of freedom" pattern this project has hit twice before (the old K=2700 self-fit
masking missing Defensive Stance; the Demo Shout double-count masking the mob-side wedge).

The validator reported (on the full 16-run corpus, in-memory constant patch, not a file change):
removing `shield_block.physical_dr` alone swings the corpus from +11.0% to **+32.4%** over-predict
(10/16 → 1/16 within ±15%). Independently re-verified here (not taken on faith) on a 5-run subset
of the same corpus, same method (`iterations=300, seed=42, K=3430`, in-memory patch only, no file
changed): mean delta **+7.4% (as-shipped, SB=0.30) → +27.7% (SB removed, SB=0.0)**, a +20.3pp
swing in the same direction and the same rough magnitude as the validator's full-corpus figure.
Per-run: Algeth'ar Academy +12 −6.9%→+7.7%; Skyreach +14 [partial] +0.1%→+16.5%; Algeth'ar
Academy +14 +2.2%→+18.3%; Windrunner Spire +14 +20.4%→+51.8%; Windrunner Spire +14 [partial]
+20.9%→+44.0%. Confirmed: the swing is large, consistent in direction across every one of the 5
runs checked, and not a validator-only claim.

**Conclusion: the wedge's log-side residuals (clean 0.8896, full-SB-excluded 0.9201) are valid,
defensible measurements of "how much of what really happened is unexplained by the non-block
chain" — comparable to Finding A's ×0.94 and worth keeping. They do NOT reconcile against
calibrate-k's sim-vs-real delta via `1/wedge - 1`, because the sim's own block/armor computation
differs from this tool's ground-truth reconstruction in ways (re-rolled, under-modeled block;
static rather than live per-hit armor) that have nothing to do with the always-on/window chain
this tool measures.** Every "predicted delta" figure `full_chain_wedge.py` prints is retained as
diagnostic output (useful for seeing the SB anomaly's shape) but should not be read as a
reconciliation with calibrate-k's own numbers.

## What this means

- **A new, specific, two-part engine-bug lead for `validator`/`lead` to prioritize**: Shield
  Block's flat `physical_dr: 0.30` (`mitigation.py` step 5b, `constants.yaml`'s
  `active_mitigation.shield_block.physical_dr`) is very likely a double-count of block value
  already modeled correctly at step 3, COUPLED with the block-value model itself under-estimating
  real block magnitude (~49% modeled vs ~57% real). **These must be fixed together, not
  separately** — fixing only the double-count (deleting step 5b) would swing the corpus
  materially worse, not better (see the verification numbers above). This is now the sharpest,
  best-evidenced lead the "structural physical-mit gap" (roadmap item 5) has had since the
  Brewmaster wedge discovery, and it is bigger than that item's old "~10pp" framing — this is a
  validator-lane fix, not a calibration-scientist one, and not undertaken in this branch.
- **`calibration_tier` stays `characterized`. `global_rmse` stays 0.138. No constants.yaml value
  changed.**
- **The clean/full-SB-excluded wedge numbers are a real, if partial, extension of Finding A** —
  worth keeping as the corpus's best current per-hit "always-on + window chain" measurement, and
  worth re-running once the coupled Shield-Block/block-value fix lands, since that fix will change
  both the sim-vs-real delta AND this wedge's own "clean"/"full_no_sb" readings (block's real
  effect is baked into `r`, so a corrected block-value model doesn't change the wedge tool's
  inputs at all — but it WILL change the corpus's measured raw_bias, which is the number the wedge
  was trying to reconcile against).

## Recommendation (for human ratification, not a decision made here)

**Do not re-promote `protection_warrior` to `calibrated` off this measurement.** The wedge
extension is real, corroborated on an independent 25-run sample, and validator-audited — but it
surfaced a live, unresolved, two-part engine bug materially affecting the corpus's own
sim-vs-real bias, which is the opposite of a "clean enough to promote" signal. The responsible
next step is:

1. Hand the Shield Block double-count + block-value under-estimate finding to `validator`
   (engine-math mode) for a proper coupled fix in `mitigation.py`/`character.py` — NOT a
   calibration-scientist task, and NOT attempted in this branch (out of scope, per the task's own
   framing and this project's engine-batch-ratification norm).
2. Re-run `simf calibrate-k` against the same ratified manifest once that fix lands, to get the
   corpus's real post-fix bias (today's +11.0% is itself propped up by the double-count; the
   validator's spot-check above shows the "with SB removed alone" direction, which is the wrong
   half of the fix to ship in isolation).
3. Re-run `scripts/full_chain_wedge.py --corpus` at that point — the wedge tool itself needs no
   changes; its inputs (real per-hit `r`, real windows/stacks) are untouched by a block-value fix,
   only the number it's being compared against will move.

## Caveats / what could still be wrong

- The block-rate cross-check (91.2% vs 25.8%) and the block-value-magnitude figure (~57% real vs
  ~49% modeled) both come from the validator's own audit pass, re-derived from the same corpus but
  not independently re-verified by a third party in this doc — a second validator pass before any
  engine fix ships would be the same "dual-validator" precedent this project already follows for
  calibration-critical changes.
- The wide corroboration set uses the frozen 2026-05-06 calibration character's talents (assumed
  stable across the 2-month span) — if Brutoh's build changed talents mid-archive, some of those
  25 runs' Indomitable/BfI/BSV gating could be wrong. Not checked per-log (would require a
  COMBATANT_INFO talent-entry-id decode per file, which — per `character.py`'s own documented
  finding — doesn't reliably map to these talents anyway; see `total_armor()`'s comment).
- The systematic sample (every 3rd of 37 remaining files, 13 total) is a real, stated method, not
  cherry-picked — but it is still one sampling scheme; a different stride could show a different
  slice of the archive. The corroboration held up at this N; a full-archive run (not attempted
  here, time-bounded) would close this gap completely.
- This doc's "clean" bin definition differs from the original Finding A doc's (excludes Shield
  Block windows too, which Finding A's hand-picked `CD_IDS` did not) — a deliberate, documented
  improvement, not a discrepancy, but it means the two numbers (0.8896 here vs ×0.94 there) are
  not a strict apples-to-apples re-measurement of the identical quantity.

## Files changed

- `scripts/full_chain_wedge.py` — new corpus-wide full-chain wedge tool.
- `scripts/_sb_layer_check.py` — new, one-off: the pooled SB-active vs SB-inactive check.
- `tests/test_full_chain_wedge.py` — new: BfI stack timeline, armor-DR cap, Shield Wall/BSV window
  gating, Shield Block full/full_no_sb divergence, no-talent baseline, no-data guard.
- `docs/validation/protwarrior_full_chain_wedge_2026_07_21.md` — this doc.
