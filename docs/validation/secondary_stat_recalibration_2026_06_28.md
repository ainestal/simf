# Secondary-stat recalibration — real 12.0.7 rating conversions + Midnight DR

**2026-06-28.** simf modeled secondary stats with a **self-fit** `rating_per_pct = 100`
anchor (rating values = percent × 100). That is internally consistent for
hand-authored profiles, but **real-rating imports** (log/WCL COMBATANT_INFO and the
/simc paste resolver) feed the game's actual rating numbers into the same `/100`,
producing secondary percentages **~2.3× too low** — so the stat-DR brackets never
triggered for realistic gear, and per-stat scaling (and gem/enchant advice) was off in
absolute terms. This recalibration moves the uniform secondaries to their real
level-90 conversions and updates the DR brackets to the Midnight table.

## The real 12.0.7 values (source: Maxroll stat-DR page + in-game ground truth)

**Uniform secondary conversions (rating per 1%, level 90):** haste **44**, crit **46**,
versatility **54**.

**Mastery is PER-SPEC and stays ~100 — NOT a uniform 46.** Ground truth (AnonGuardian1
Guardian, in-game tooltips):

| mastery rating | displayed mastery % |
|---|---|
| 332 (current gear) | 10.6% |
| 668 (older log, proc-pairing) | ~14% |

Two-point fit → **~99 rating per 1%, ~7.2% base** — i.e. ≈100. So simf's existing
per-spec `mastery_rating_per_pct_* = 100` is correct for guardian; warrior/paladin have
no in-game counter-evidence and their mastery *effect* is calibrated via the separate
spec coefficient (`mastery_crit_block_scaling`, `natures_guardian_heal_coeff`), so
mastery is left at 100 across the board (every mastery path stays bit-identical). This
also **validates PR #211** (guardian mastery coeff 1.0 + conversion 100 → ×1.147 ≈ the
proc-pairing 0.14). Parry stays 100 (no ground truth; avoidance, not a gem/DR stat).

**DR brackets (Midnight):** linear 30/40/50/60/70/200 at efficiency
1.0/0.9/0.8/0.7/0.6/0.5 (0 past 200%) → ~125% effective hard cap. simf had the stale
classic tail (54-66/66-100, cap 113.3%); the first four brackets were already correct.
Only matters above ~54% effective in a single stat.

## Why the warrior K calibration is a NO-OP

Hand-authored profiles are self-fit (`haste 2318 = 23.18%`). Rescaling their
haste/crit/vers to real units **while preserving the %** makes the change a
mathematical no-op for those characters. Verified — `brutoh-calibration-2026-05.yaml`
computes bit-identical effective %:

```
haste 23.18 / crit 18.91 / mastery 28.08 / vers 2.96 (vers DR 1.481%)
```

`calibrate-k` uses that profile (not log-hydration), so **K=3430 / RMSE 0.068 are
preserved**. (Mastery ratings are unchanged — mastery conversion stays 100.)

## What actually changes — real-rating imports

Log/WCL/paste-resolved characters carry the game's real rating numbers. Under the old
`/100` their secondary % were ~2.3× low; now they're correct. Verified on AnonGuardian1
(log-hydrated): haste **12.4% → 28.1%**, vers **6.0% → 11.1%** (DR 5.54%). So DR now
triggers at the right point and stat scaling / gem-enchant advice is right.

Side effect: the non-warrior **characterization profiles** (`anon_pwar1`, `anon_ppal1`,
`anonbrewmaster1` — built from COMBATANT_INFO, i.e. real ratings) now compute higher (correct)
secondary % than before, so their predicted survival shifts. These specs are all
`calibrated: false`; the shift is the intended correction (their secondary % were
previously under-counted). The characterization deltas in those docs should be re-read
under the new conversions.

## Changes

- `constants.yaml stat_conversion`: `versatility_rating_per_pct 100→54`,
  `haste_rating_per_pct 100→44`, `crit_rating_per_pct 100→46`. Mastery (all specs) +
  parry stay 100.
- `constants.yaml secondary_dr.breakpoints`: Midnight tail
  (`[0.60,0.60]`, `[1.25,0.50]`); `hard_cap 1.133→1.25`.
- Self-fit profiles rescaled to real haste/crit/vers (preserving %):
  `brutoh-calibration-2026-05.yaml`, `brutoh.yaml`. (anon_pwar1/anon_ppal1/anonbrewmaster1 are real —
  left untouched; example_warrior is synthetic; anonguardian2 is zeros.)
- Tests updated (`test_secondary_dr.py` ratings rescaled + Midnight cap; plus any
  conversion-derived assertions the recalibration shifts).

## Status

`calibrated` flags unchanged — this is a units/correctness change with K preserved, not
a re-fit. Ships to a PR for ratification. Guardian/Brewmaster/VDH stay `calibrated:
false` (their characterizations should be re-read under the corrected secondary %).
