# Prot Warrior Riposte crit→parry gap — characterized AND SHIPPED (2026-07-12)

**Update, same day**: the second data point this doc called for arrived within the session — the
user pulled a second tooltip reading at a different crit level (870 crit rating → 16.73% parry),
disambiguating the coefficient cleanly (see "Second data point" section below). The engine fix
shipped: `character.py::base_parry()` now credits Riposte, `constants.yaml`'s
`parry_rating_per_pct` placeholder is corrected from 100 to the confirmed 52. Everything below this
point is the original same-day characterization; treat the "Held" section at the bottom as
historical (superseded by "Shipped, part 2" at the very end).

## Summary

The user (playing this exact character, Brutoh) challenged the UI's own copy: `stat_mechanisms.yaml`
claimed "without Brutal Vitality, crit does nothing for your survival" for Protection Warrior. That
claim is false. Live in-game tooltip (Brutoh, 2026-07-12):

> Critical Strike: 490 rating [+10.65%]. Increases parry chance by 9.42%.

**Riposte** — a Protection Warrior specialization ability (always active, not a talent choice) —
converts critical strike rating into bonus parry chance. This is currently **completely unmodeled**
in the engine.

## Evidence (confidence noted per claim)

- **Mechanism is real — confirmed.** Three independent sources: Wowhead spell 161798's effect text
  ("Apply Aura: Converts one stat into another, 100% Value" — a rating-conversion aura, matching how
  WoW auras of this class typically work); SimC's `midnight` branch (`sc_warrior.cpp`) declares
  `spec.riposte = find_specialization_spell("Riposte")`, confirming it's a real, current
  Protection-Warrior spec spell in Blizzard's data — though SimC itself never implements it (zero
  consuming code beyond the declaration, the same "declared but not implemented" gap class as VDH's
  Fel Flame Fortification); Noxxic ("Crit also increases your chance to Parry via Riposte") and Icy
  Veins guide text, both fetched directly at their current (12.0.7) URLs.
- **This project already half-discovered it — confirmed from source.** `io/armory.py:93`'s
  online-hydrate path deliberately excludes `parry_rating` from the fields it maps, with the comment
  "a warrior's parry rating_bonus is Riposte-derived from crit, so mapping it would double-credit
  crit." The exclusion (avoiding double-counting) was implemented; the offsetting credit
  (crit→parry itself) never was. `grep -ri riposte src/simf data/` found zero other hits before this
  session — no dormant/half-built implementation exists.
- **Exact coefficient — single data point, NOT confirmed.** Naive reading (crit rating converts 1:1
  into a parry-rating pool, which then converts to % via parry's own rating cost):
  `parry_rating_per_pct = 490 / 9.42 ≈ 52.0`. Notable: this would replace `constants.yaml:581`'s
  `parry_rating_per_pct: 100`, which is explicitly marked `# PLACEHOLDER — consistent with other
  rating conversions` — i.e. never researched, a self-fit guess, same issue class as
  `mastery_rating_per_pct` (see `mastery_rating_per_pct_warrior_paladin_real_value_2026_07_11`
  memory). **But** there's a real, unresolved ambiguity: is the tooltip's 9.42% the
  crit-*attributable* delta, or Brutoh's *total* sheet parry (base + strength + crit combined)? If
  the latter, the crit-attributable slice is much smaller — current modeled `base_parry` for Brutoh
  is base 3% + strength-derived ~4.36% ≈ 7.36%, leaving only ~2pp attributable to crit, implying a
  coefficient near 238 rating/pct instead of 52 (a 4.6× swing). Nothing in the single tooltip line
  disambiguates this.

## Does this affect the calibrated Warrior RMSE (0.065–0.068, 16 logs)? No.

This is the decisive structural fact the investigation surfaced (missed by the first-pass impact
analysis, caught in synthesis): **`io/log_replay.py` and `io/wcl_replay.py` both hardcode
`is_avoidable=False` on every replayed damage event.** Log-replay calibration never rolls avoidance
(dodge/parry/miss) at all — a logged event already reflects whatever avoidance did or didn't happen
in the real fight, and replaying it as "avoidable" a second time would double-count. So adding a
crit→parry term to `base_parry()` would move the calibrated corpus's RMSE by **exactly zero**. The
long-standing "~10pp structural physical-mitigation gap" (CONTRIBUTING.md roadmap item #5) is **not**
explained by this mechanic — that gap lives entirely within the replayed-damage math (armor, block,
DR layers), not avoidance, by construction of how replay works.

## What this DOES affect: every forward-simulation surface

The key-level verdict sweep, skill ladder, `optimizer/stat_weights.py`'s stat weights, and every
gem/enchant/gear/vault ΔeHP recommendation all run live Monte Carlo simulation, where avoidance rolls
DO happen (`core/mitigation.py`'s avoidance roll). Crit currently carries **zero survival marginal**
for Protection Warrior on every one of these surfaces. Practical consequence: every crit-related
gear/gem/enchant recommendation this tool has ever produced for a Prot Warrior has undervalued crit
relative to its real defensive contribution. This is a real recommendation-quality gap, orthogonal to
calibration — it should be evaluated and prioritized on its own terms ("does this change what gear we
recommend, and by how much"), not folded into "does it close the gap" framing.

Rough order-of-magnitude (naive ~52 rating/pct coefficient, NOT confirmed): at the frozen calibration
character's `crit_rating: 640`, this would add ~12.3pp of parry chance — since a parry is a full
(100%) negation of that swing in this engine's avoidance-roll model, that's a meaningful chunk of
avoidance, plausibly a double-digit percentage reduction in forward-sim DTPS. This number should NOT
be used to make any decision — it depends entirely on the unconfirmed coefficient above, which could
be off by up to ~4.6×.

## Shipped

- **Copy-only fix (PR #358):** `stat_mechanisms.yaml`'s `protection_warrior.crit_rating` gloss
  reworded to name Riposte and the gap honestly, dropping the false "without Brutal Vitality, crit
  does nothing" claim. Zero engine risk.

## Held — engine-fix protocol for a future session

1. **Get a second (crit_rating, parry_pct) data point** at a different crit level to disambiguate
   the ~52 vs. ~238 rating/pct reading — e.g. Brutoh temporarily unequipping/swapping a crit
   gem/enchant and re-reading the tooltip, or finding an addon/tooltip breakdown that separates the
   crit-attributable parry delta from total sheet parry directly.
2. Add the term to `character.py::base_parry()`, gated to `protection_warrior` only — Riposte is
   Warrior-specific; do not extrapolate to Prot Paladin or other specs without independently
   verifying each one has (or lacks) an equivalent mechanic.
3. Correct `constants.yaml:581`'s `parry_rating_per_pct: 100` placeholder alongside, once the real
   coefficient is confirmed.
4. Measure and report a before/after delta on the affected forward-sim surfaces (verdict sweep, stat
   weights, gem/vault recommendations) — user-ratified before merge, since it changes real
   recommendations tanks act on, even though it's inert on the calibrated RMSE.

No code or constants beyond the copy fix were changed as part of this investigation.

## Second data point (same day) — coefficient confirmed, engine fix shipped

The user pulled a fresh `/simc` export with a second live tooltip reading, at a much higher crit
level (a "focus on critical" gear setup): **870 crit rating [+18.91%]. Increases parry chance by
16.73%.** ("Parry of 870 adds 16.73% Parry" — the tooltip phrasing itself makes explicit that this
is the crit-attributable delta, not total sheet parry, resolving the ambiguity this doc's original
"Held" section named.)

```
490 / 9.42  = 52.017
870 / 16.73 = 52.002
```

Both independently land within 0.02 of exactly **52** — and both displayed percentages reproduce
exactly at `parry_rating_per_pct = 52` (490/52 = 9.4231% → displays 9.42%; 870/52 = 16.7308% →
displays 16.73%), the same clean-integer pattern as the project's other confirmed rating
conversions (crit 46, haste 44, versatility 54). This is as strong a confirmation as a two-point
empirical read can give — coefficient treated as confirmed, not single-datapoint speculation.

**Shipped:**
- `character.py::base_parry()` — new `from_riposte = self.crit_rating / parry_rating_per_pct / 100`
  term, gated to `protection_warrior` only (does not leak to Paladin/DK/VDH, each has its own
  distinct avoidance branch).
- `constants.yaml`'s `stat_conversion.parry_rating_per_pct` corrected from the self-fit placeholder
  100 to the confirmed 52. Note: this also steepens the (currently near-dead — no modern gear
  itemizes raw Parry Rating) generic `parry_rating` gear-stat term for all four parry-using specs,
  not just Riposte's crit-derived credit; flagged, not a concern in practice today.
- `stat_mechanisms.yaml`'s crit_rating gloss updated to describe the modeled mechanism (no longer
  "isn't modeled here yet").
- Two regression tests (`tests/test_mitigation.py`) pin both empirical readings exactly and confirm
  no cross-spec leakage.

**A real, separate bug surfaced as a side effect**: `tests/conftest.py`'s shared `brutoh` fixture
(used across 45 test files) was still carrying pre-2026-06-28-rescale secondary-stat values
(`haste_rating=2318, crit_rating=1391, versatility_rating=296` — the old self-fit ×100-per-pct
convention, same in-game % but wrong units vs. today's real 44/46/54 rating-per-pct). Harmless until
now because `crit_pct()`/`haste_pct()`/`versatility_pct()` all pass through `apply_secondary_dr()`,
which happened to compress the ~2x-oversized linear values back down near their intended
percentages — but the new Riposte term has no DR curve, so the stale crit_rating produced a real,
uncompressed, roughly-2.17×-too-large parry bonus. This broke
`test_skill_modifier.py::test_modifier_zero_still_survives_via_emergency_cds` (100% death rate
instead of the expected <100% — the reactive `baseline_hps_pct_of_dtps=0.40` healer model scales
absolute healing to measured DTPS, so a large unintended avoidance spike shrank the healing pool
faster than it removed danger, in this test's tightly-tuned scenario). Fixed the fixture to
`1020/640/160` (matching `brutoh-calibration-2026-05.yaml` exactly). A validator pass swept the
other ~13 test files independently carrying the same stale literals and confirmed all but one
(`test_metrics.py`) are structurally insensitive to the exact values (relative comparisons, opaque
cache keys, text-presence assertions) — `test_metrics.py`'s `_make_brutoh()` was also fixed (its
DTPS-plausible-range guard had silently lost margin in exactly the direction it exists to catch).
The other ~12 files' stale literals are a known, named, non-blocking cleanup item — deferred, not
silently dropped.

**Verified**: calibrated Prot Warrior RMSE is bit-identical before/after (0.0652 at K=3430, 16
replays) — confirms the structural prediction that log-replay calibration (hardcoded
`is_avoidable=False`) is completely inert to this change. Forward-sim impact: crit_rating's
survivability marginal for the living `brutoh.yaml` character moved from ~0 (pre-fix) to ~547
(post-fix, now comparable to versatility's ~558) — crit is no longer systematically undervalued in
every gear/gem/vault recommendation this tool makes for Prot Warrior. Full suite: 2730 passed, 0
regressions. mypy clean.
