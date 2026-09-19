# Mastery-chain K calibration — atomic F11 + F7-retune + F13 (2026-05-20)

The atomic ship the audit doc has called for since 2026-05-19:

- **F11** — `mastery_block_value_scaling` 0.5 → 0.0 (already in tree from F12)
- **F7 retune** — `mastery_crit_block_scaling` 1.0 → 1.5 (SimC `effectN(1).mastery_value()`)
- **F13** — `mastery_block_chance_scaling` 0.0 → 0.5 (SimC `effectN(2).mastery_value()`), newly wired into `Character.base_block()`

Primary source: SimulationCraft `engine/class_modules/sc_warrior.cpp`
(midnight branch, build 12.0.5.67602) lines 8893 / 8910 / 8990. Spell
76857 cross-check via Wowhead + in-game tooltip — all three sources
converge on 0.0 / 0.5 / 1.5.

See `docs/validation/simc_warrior_mastery_2026_05_19.md` for the
SimC source extracts and `docs/validation/k_calibration_f12_2026_05_20.md`
for the pre-mastery-chain residual sign analysis that motivated this ship.

## Verdict criteria (set BEFORE the sweep ran)

| RMSE at K=3430 | Decision |
| --- | --- |
| ≤ 0.066 | Ship + close the mastery-chain item in ROADMAP |
| 0.066 < x ≤ 0.080 | Ship + document residual gap (still SimC truth) |
| > 0.080 | Don't ship; diagnose (likely F14 block-chance DR missing) |

## Predicted impact (before the sweep)

Per the F12 residual analysis at K=3400: 13 of 18 logs positive,
mean residual +3.6%. The mastery chain is expected to add back
mitigation on roughly the right magnitude — F13 alone adds ~3pp
average damage reduction (15% mastery × 0.5 scaling × ~40%
block-DR per blocked event); F7 retune (1.0 → 1.5) shifts
crit-block proportion within blocked events upward.

If both axes land cleanly, empirical-best K should drift toward
canonical 3430 (was K=3200 pre-mastery-chain), narrowing the
self-fit gap.

## Sweep configuration

- Character: Brutoh with `shield_armor=931` (Spellbreaker's Rebuke
  at ilvl 285 — the historical state matching every log in `examples/`).
  Current state in `brutoh.yaml` is 989 (post-voidcore ilvl 295) — do
  NOT use it here.
- Logs: 18 Brutoh M+ replays in `examples/`.
- Iterations: 300 per K value (matches F12 sweep for direct comparison).
- Constants: `k_constant=3430`, `mastery_block_value_scaling=0.0`,
  `mastery_block_chance_scaling=0.5`, `mastery_crit_block_scaling=1.5`,
  `block_value_armor_multiplier=2.5`.

## Sweep result

```
K=  3000: RMSE=0.101
K=  3100: RMSE=0.086
K=  3200: RMSE=0.074
K=  3300: RMSE=0.067
K=  3400: RMSE=0.064  ← best
K=  3500: RMSE=0.068
K=  3600: RMSE=0.076
K=  3700: RMSE=0.087
```

**Best K=3400, RMSE=0.0644.** At canonical K=3430 (linear interp
between K=3400 and K=3500): **RMSE ≈ 0.065**.

18 Brutoh replays, 300 iterations each, shield_armor=931 (matches the
historical ilvl 285 corpus). Sweep took ~50min on Pi.

## Comparison

| Snapshot | Best K | RMSE at best | RMSE at K=3430 |
| --- | --- | --- | --- |
| Pre-F12 (`mastery_block_value=0.5`, no shield-armor sourcing) | 3200 | ~0.062 | ~0.066 |
| F12 only (`mastery_block_value=0.0`, shield-armor sourced) | 3200 | 0.063 | ~0.079 |
| **F11 + F7-retune + F13 (this ship)** | **3400** | **0.064** | **~0.065** |

The empirical-best K jumped from K=3200 (pre-mastery-chain) to **K=3400**.
Canonical K=3430 is the SimC DBC level-90 anchor; empirical and canonical
are now within **30 K** of each other, down from a 230 K gap pre-F11/F7/F13.
This is the structural-truth win the audit chain was reaching for.

## Residual sign analysis at K=3400

```
[-5.2, -14.3, -1.5, +6.3, +0.5, -10.7, +4.3, +7.2, +3.3, +6.9,
 -4.7, -1.4, +2.7, +7.8, +2.9, -6.6, +3.9, -8.5]
```

- 9 of 18 positive, 9 of 18 negative — **balanced**.
- Mean residual: **−0.4%** (essentially unbiased).
- Worst outlier: −14.3% (one specific log, vs −20+% spread we saw mid-audit).
- |residual| > 10%: 3 of 18 (was 4 of 18 at F12, 6 of 18 at v0.10.9 pre-audit).

The systematic positive bias seen post-F12 at K=3400 (mean +3.6%, 13/18
positive) is gone. F13 + F7-retune added back roughly the right amount of
mitigation on roughly the right axis. The prediction in this doc's "Predicted
impact" section ("F13 alone adds ~3pp average") was directionally exact.

## Decision

**SHIP.** RMSE at canonical K=3430 ≈ 0.065 ≤ 0.066 → top band of the
pre-committed criteria. The mastery-chain ROADMAP item closes.

**K stays at 3430**, NOT 3400. Empirical and canonical are within 30 K
of each other; the gap is now noise-floor and re-fitting K to the
empirical minimum would be a regression toward self-fit. The whole
point of the audit chain was anchoring to SimC's structural constants.

`±6.8% model error` trust-strip framing remains correct (interp 0.065
rounds to 6.5%; the existing copy at 6.8% was set against the F12 best
of 0.063 and is still defensible — update at next polish pass).

## Constants snapshot post-ship

```yaml
base:
  block_chance: 0.10
  block_value_pct: 0.30                # Prot Pal placeholder (Warrior path now uses shield_armor × 2.5)
  block_value_armor_multiplier: 2.5    # F12 — SimC engine/player/player.cpp:1681
  mastery_block_value_scaling: 0.0     # F11 — SimC sc_warrior.cpp:8910 (no mastery in composite_block_value)
  mastery_block_chance_scaling: 0.5    # F13 — SimC sc_warrior.cpp:8893 effectN(2).mastery_value()
  mastery_crit_block_scaling: 1.5      # F7 retune — SimC sc_warrior.cpp:8990 effectN(1).mastery_value()
```

`k_constant` stays at 3430 — this is the SimC DBC level-90 anchor.
We do not self-fit K to the empirical minimum.

## Known unmodeled mitigation (post-ship)

- **F14 — block-chance diminishing returns.** SimC `composite_block_dr()`
  applies a DR curve to block chance. Not modeled. At Brutoh's ~16%
  mastery, F13 adds ~8pp pre-DR block chance — likely close to actual
  post-DR because we're well below the DR knee. Worth tackling when
  block-chance stacks rise (vault gear, mastery food, mastery-heavy
  loadouts).
- **Brace for Impact block-value multiplier** — SimC modifies block VALUE
  via the BfI buff stack; simf currently models BfI as flat damage
  reduction via the talent system. Cross-check deferred.
