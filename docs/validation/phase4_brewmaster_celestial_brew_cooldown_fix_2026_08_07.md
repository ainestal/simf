# Brewmaster Celestial Brew cooldown fix — a correctness fix with measured zero effect (2026-08-07)

`constants.yaml`'s `specs.brewmaster_monk.celestial_brew_cooldown_s` was `60.0`,
disagreeing with this project's OWN prior research on the same spell (322507):
`coaching.defensives.brewmaster_monk`'s registry entry (a few lines below in
the same file) already carries `cooldown_s: 45`, researched 2026-07-02 against
Wowhead/Icy Veins/Method.gg/Maxroll/Peak of Serenity live spell data (see
`docs/validation/coaching_registry_audit_2026_07_02.md`, "Fixed to `coverage`
+ `cooldown_s: 45`"). A 33% discrepancy inside one file, unnoticed because the
two entries feed different systems (the coaching registry vs.
`BrewmasterPolicy.decide()`'s press-scheduling gate in
`classes/brewmaster_monk.py:214`).

**Hypothesis going in** (from a 2026-08-06 launch-readiness audit, itself
building on the residual named in
`docs/validation/phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`):
Brewmaster over-predicts damage taken by +22.5%/+47.4% at canonical K=3430
(2-log corpus: AnonBrewmaster2 +12 Windrunner Spire, AnonBrewmaster1 +17 Seat of the
Triumvirate), and under-crediting Celestial Brew's press cadence by a third
was floated as a *plausible contributor* — explicitly caveated as needing
verification before trusting.

## Result: the hypothesis is false, and the fix has zero measured benefit anywhere checked

The value (45.0) is correct and worth fixing on game-accuracy grounds alone —
it now agrees with this project's own independently-researched coaching
registry entry for the identical spell. But it is **not** a mitigation
improvement. Two separate measurements, both showing exactly zero (or
negative) effect:

### 1. Replay-based calibration: exactly zero, by construction

Re-ran `scripts/calibrate_spec_from_logs.py calibrate brewmaster_monk` before
and after the constant change — byte-identical at the printed precision.
Re-verified at full float precision (seed=42, iters=300, k_constant=3430,
same two replays) to rule out a display-rounding coincidence:

```
cooldown=60.0: deltas=[22.496964, 47.439175]
cooldown=45.0: deltas=[22.496964, 47.439175]
DIFF (45 - 60): [0.000000, 0.000000]
```

**This is deductive, not an N=2 empirical result** — it holds at any corpus
size, because of how the mitigation math is structured, not because these
two logs happen to be insensitive to it. `apply_brewmaster_mitigation`
(`classes/brewmaster_monk.py:67-160`) branches on `event.is_log_replay`:

```python
if event.is_log_replay:
    # Replay: log's absorbed field already captures stagger; use the
    # original flat-DR approximation so the chain doesn't double-count.
    absorbed = min(damage, event.log_absorbed)          # :123-128
    ...
else:
    ...
    if state.healer_absorb > 0 and now < state.healer_absorb_until:  # :150
        ...
```

`calibrate_spec_from_logs.py` feeds real logged events via `events_override`,
which carry `is_log_replay=True` — every event takes the replay branch, which
substitutes the log's own `event.log_absorbed` and never reads
`state.healer_absorb` at all. Celestial Brew's press schedule (confirmed by
direct instrumentation to fire correctly on the new 45s cadence — traced
actual press timestamps at 30.4s, 75.4s, 120.5s, ... in a single-iteration
replay) sets `state.healer_absorb`, but nothing in the replay branch ever
reads it. Correct scoping: **CB press cadence cannot contribute to the
replay-measured residual, by construction — the corpus already credits the
player's real CB from the log's own `log_absorbed` field** (see
`log_replay.py:143-149`). The original lead's premise was wrong, not just its
magnitude — there was nothing on the replay path to under-credit.

The +22.5%/+47.4% residual from 2026-07-04 stands exactly as measured. This
lead is **closed, refuted** — Brewmaster's over-prediction gap needs a
different explanation. One live candidate named in the same
`log_replay.py:145-148` comment: Ironskin Brew's stagger bonus *beyond* the
base fraction is not captured in `log_absorbed` — worth checking before the
synthetic-stagger-over-ticking strand already on ROADMAP.md.

### 2. Forward-sim verdict sweep: also exactly zero for a geared tank, slightly negative for an undergeared one

The plausible-sounding alternative — "fine on replay, but this should still
help the verdict sweep / gear recs / cooldown planner, which DO read
`state.healer_absorb`" — was checked directly rather than assumed true, and
is also false.

**Geared tank (2.64M HP), `m+_boss_tankbuster` profile, key levels +2 through
+24:** Celestial Brew presses **0 times at every key level, before and
after the fix.** `death_rate`, `mean_dtps`, `p99_5s`, and `etmi_12` are all
bit-identical. Minimum `hp_pct` seen across 11,229 `decide()` calls: 0.842 at
+18, 0.750 at +24 — HP never crosses the `hp_pct < 0.70` press gate. Same
result for `m+_pull_melee` and `m+_dungeon_physical_chain`. A realistic
~7M-HP Brewmaster is strictly safer than this test character, so the gap
only widens.

**Undergeared tank (1.32M HP), +18, 5 seeds — the only regime where the gate
fires at all:** press count is identical (400 presses either cooldown value
— fight length and the HP gate bind, not the cooldown), but the fix makes
the modeled character take **more** damage: absorb totals drop ~12.8% and
`mean_dtps` rises +3.9% to +4.3% (consistent sign across seeds). Mechanism:
the absorb is a flat 20%-of-max-HP shield on an 8s window refreshed by
`max()` (`brewmaster_monk.py:216-217`), not an accruing pool. A shorter
cooldown doesn't buy extra presses when the press count is already
fight-length-bound — it just slides the same 8s window onto a different (in
this case lower-damage) moment before it expires.

## What this means going forward

- **Ship as a correctness/game-accuracy fix, not a mitigation improvement.**
  Its measured effect is exactly zero everywhere a real user's number lives
  (calibration corpus, geared-tank verdict sweep) and mildly negative in the
  one narrow regime where it does anything at all (undergeared, +18).
  `celestial_brew_cooldown_s` has exactly one consumer in the whole
  codebase — `BrewmasterPolicy.decide()`. The cooldown planner and coaching
  surfaces read the *separate* `coaching.defensives.brewmaster_monk` registry
  entry (`constants.yaml:506`), which already carried the correct 45 and is
  unchanged by this PR. This fix brings the one divergent copy into line
  with it; total blast radius is one call site whose measured output is
  unchanged.
- **The "under-crediting CB cadence" lead is closed, refuted** — not fixed.
  Brewmaster's +22.5%/+47.4% over-prediction residual needs a different
  explanation.
- **The real forward-sim defect is the `hp_pct < 0.70` reactive gate, not the
  cooldown — separate, bigger, and NOT fixed here.** A geared Brewmaster gets
  **zero** Celestial Brew credit in every gear recommendation, stat weight,
  and key-level verdict simf produces, because the policy only presses it
  reactively on a threshold that a well-geared character's HP trajectory
  never actually crosses. Real Brewmasters press Celestial Brew proactively
  on cooldown (for the absorb AND Purified Chi stacks), not reactively. This
  is the same code region as the companion Celestial Infusion item — worth
  scoping together rather than adding Infusion support on top of a gate that
  already suppresses both abilities' forward-sim credit. See
  [[brewmaster_celestial_brew_never_presses_geared_forward_sim]] in memory.

## Verification

`.venv/bin/pytest -q` full suite green both before and after the constant
change (2931 passed, 7 skipped). `tests/test_brewmaster_celestial_brew_policy.py`
pins the press schedule directly (the only thing this fix actually changes)
since neither the calibration corpus nor the verdict sweep can see it. No
existing test hardcoded the old 60.0 value or the residual numbers.
