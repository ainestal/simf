# Magic-cast damage doesn't scale with key level — `scale_damage_profile` gap (2026-07-09)

## Summary

`scale_damage_profile` (`src/simf/core/profiles.py`) scales a dungeon's melee
swings (`MobSpec.swing_damage_mean`) and tank-buster hits (`TankBuster.damage`)
by the key-level damage multiplier, but never touches `MobSpec.casts` — a
mob's spell-cast damage. Every consumer of a scaled profile (the key-level
verdict sweep, the skill ladder, build-compare, and the new disposition
ledger — see `docs/validation/` and CONTRIBUTING.md's eHP-transparency entry) reads
casts unscaled, so the modeled magic-damage share silently shrinks as key
level rises: on the `m+_boss_tankbuster` profile, magic damage is ~24% of the
raw stream at damage_multiplier 1.0 and shrinks to ~14% at 2.0. Melee/physical
damage keeps scaling normally, so the *shape* of the fight drifts toward
"all physical" the higher the modeled key climbs — the opposite of the real
game, where magic bursts are what actually kill tanks at high keys.

## How this was found

Not a dedicated audit — surfaced as a side effect of a 2026-07-09 live
validation round on an unrelated feature (the eHP/eHPS mechanism-transparency
ship, PRs #325-333; see CONTRIBUTING.md's "Current state" entry for that session).
The new "Where your survivability comes from" disposition-ledger panel
(Protection Warrior only) carried a caption asserting a directional claim —
"Absorbed shrinks and Landed as damage grows somewhat at higher keys even
though block/armor/versatility stay flat percentages." The calibration-
scientist reviewer checked that claim against the ledger's own sim path
rather than trusting the copy, and found the shipped engine contradicts it:

Running `_compute_ledger_result` (the exact code the panel runs) on the demo
Protection Warrior at damage multipliers 1.0 / 1.3 / 1.6 / 2.0:

| multiplier | blocked | armor | avoided | dealt (landed) |
|---|---|---|---|---|
| 1.0 | 24.3% | 28.4% | 5.9% | 21.8% |
| 1.3 | ~26% | ~29% | ~6.3% | ~19% |
| 1.6 | ~28% | ~30% | ~6.6% | ~18% |
| 2.0 | 29.7% | 31.0% | 6.9% | **16.3%** |

The caption's claim was inverted: **Landed fell** (21.8%→16.3%) and
**Blocked/Armor rose** (24.3%→29.7% / 28.4%→31.0%) — the opposite of "stay
flat" and "grows." `death_rate` stayed ≤0.8% across all four runs, ruling out
a truncation/early-death artifact as the explanation.

## Root cause

- `src/simf/core/profiles.py`'s `scale_damage_profile` (~line 80-86) builds
  the scaled profile via `replace(mob, swing_damage_mean=..., tank_busters=
  [replace(tb, damage=tb.damage * multiplier) for tb in mob.tank_busters])`
  — `casts` is never named, so it passes through unscaled via `replace`'s
  default (keep-existing-field) behavior. The function's own docstring
  claims it scales "all damage amounts," which is not true of `casts`.
- `src/simf/core/timeline.py` builds cast-event damage directly from the
  (unscaled) `cast['damage_mean']` field.
- Confirmed callers that receive a profile through this path and are
  therefore all affected: `key_level_verdict.py:280`, `skill_ladder.py:110`,
  `build_compare.py:141`, `disposition_ledger.py:156` (via
  `_compute_ledger_result`).
- **Log-replay calibration corpora are NOT affected** — those replay real
  logged events and never call `scale_damage_profile`. The Warrior K=3430
  RMSE=0.068 headline and Guardian's `calibrated: true` LOO-CV result stand
  unchanged.

## Why this wasn't fixed same-day

This is a real, calibration-affecting engine change touching both currently-
calibrated specs' (Protection Warrior, Guardian Druid) *synthetic, scaled*
high-key verdicts — the key-level push recommendation, the skill ladder, and
build-compare's cross-key comparisons all shift once casts scale correctly.
Per this project's own "engine batch ratification cap" convention (see
CONTRIBUTING.md's Key design rules), an engine change of this kind should not be
chained onto an already-large same-day batch (the 2026-07-09/10 session
already shipped 9 PRs) without the user seeing and ratifying the measured
before/after delta first. **Deferred, not forgotten** — tracked as a new
`Next priorities` item in CONTRIBUTING.md.

What *was* fixed same-day, since it doesn't depend on the engine change: the
disposition-ledger caption's specific false directional claim was reworded to
stop asserting something the shipped engine currently contradicts (a live
copy-honesty bug regardless of source) — see PR #333.

## Suggested fix (not yet implemented)

Scale casts too, mirroring how swings/tank-busters are already handled:

```python
casts=[{**c, "damage_mean": c["damage_mean"] * multiplier} for c in mob.casts]
```

Add a mutation-verified regression test asserting school-mix invariance
under scaling (i.e. the physical/magic split of a profile's *unscaled* raw
totals should be recoverable from the scaled profile at any multiplier).
Then re-check the key-level verdict banding and skill-ladder numbers, since
both are expected to shift in the pessimistic (more honest) direction at
high keys once magic casts scale correctly.

## Fix implemented + measured (2026-07-12), pending merge ratification

Shipped on branch `fix/magic-cast-scaling-gap` (commit `2728859`), exactly
as suggested above, plus the prescribed mutation-verified school-mix
invariance test in the new `tests/test_profiles.py`. Full suite green
(2733 passed), mypy clean, dual-validated (engine-math validator +
calibration-scientist, both independent passes — see below).

**Isolated measurement** (git-worktree isolated against unmodified `master`
so only this fix differs — the raw before/after also contains an unrelated,
already-merged change, the Riposte crit→parry PR #360, which is *not* this
fix and is called out separately below): on the demo Protection Warrior
(Brutoh), "landed" damage share by multiplier —

| multiplier | landed — master (bug) | landed — fixed |
|---|---|---|
| 1.0 | 20.9% | 20.9% (fix is a no-op at mult=1.0, as expected) |
| 1.3 | 18.6% | 21.0% |
| 1.6 | 16.9% | 20.8% |
| 2.0 | 15.3% | 20.2% |

Confirms the school-mix invariance analytically: `m+_boss_tankbuster.yaml`'s
raw totals give magic share 24.6% at every multiplier once casts scale
uniformly with everything else — matching this doc's original "~24%" figure
exactly, and matching "~14%" at mult=2.0 for the pre-fix bug.

**Practical verdict-sweep impact** — `compute_key_level_verdict` (iterations
200, seed 42, `m+_boss_tankbuster`/`m+_high_key_healer`, fortified), before
= master worktree, after = fixed branch:

- **Protection Warrior (Brutoh, the real geared corpus/demo character)**:
  comfortable ceiling **+20 → +15**, progression ceiling **+23 → +16**. At
  +18 specifically (inside Brutoh's actual +14-18 push range per CONTRIBUTING.md),
  death_rate moves 0.0% → 87.0% (comfortable → danger).
- **Guardian Druid (AnonGuardian2)**: no visible banding change — they're a
  synthetic all-zero-secondaries test stub (see
  `docs/validation/secondary_stat_recalibration_2026_06_28.md`) already
  dying at the +2 floor before and after — but their raw `mean_dtps` moves the
  same +17-33% direction/magnitude as Brutoh's at +14/+18/+22, confirming
  the fix's mechanism engages for Guardian too. No realistically-geared
  Guardian character file exists in-repo today to show a practical
  ceiling-shift the way Brutoh does.
- **Log-replay calibration is untouched, confirmed by grep** (not just
  asserted): `src/simf/io/` has zero references to `DamageProfile` /
  `scale_damage_profile` / `generate_events`; log-replay constructs
  `DamageEvent`s directly from logged lines. Warrior K=3430 RMSE=0.068 and
  Guardian's LOO-CV headline are unaffected.

**Dual validation:**
- *Engine-math validator*: CONFIRMED — the fix scales the only
  damage-bearing cast field that exists (`damage_mean`; `cadence_s` is
  timing, there's no per-cast variance field), no consumer reads `casts`
  assuming unscaled data, no double-scaling path, log-replay calibration
  provably untouched, and the mutation-verification was independently
  re-run and reproduced. Flagged two now-stale comments (in
  `talent_search.py` and this doc's sibling test file) describing the old
  buggy behavior in the present tense — both corrected in the same PR.
- *Calibration-scientist*: reproduced every number from first principles
  (school mix, absolute DTPS, the death-rate cliff, a second-spec
  cross-check on Guardian) purely from the YAML/constants, independent of
  the sim output, and confirmed the ±5-7 key-level band shift is correctly
  sized — not an overcorrection (a naive equal-mean-dtps bound would have
  been *more* pessimistic, dropping to +12-13; the sim's +15 is gentler).
  Verdict: **ratify**, with two non-blocking named caveats: (1) the
  healer-budget R/B constants were measured on a +12-17 corpus and now
  carry more weight at the newly-danger-banded +18-23 range — a caveat to
  surface, not a reason to re-tune from zero data; (2) `key_level_scaling.yaml`'s
  absolute per-key anchors were set under the buggy scaler and are worth a
  cheap re-check against the calibrated log corpus (could shift banding by
  ~1 level, cannot reverse the fix's direction).

**Status:** code + tests + docs done, PR opened, **held for user
ratification before merge** per this doc's own note above (the shift is
large and user-facing — Brutoh's own push recommendation drops noticeably)
— not merged same-day as a batch, per the engine-batch-ratification-cap
convention.
