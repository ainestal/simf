# F12 K calibration — shield-armor block_value sourcing (2026-05-20)

K sweep after F12.2 + F12.3 lands: `block_value_rating()` now derives from
`shield_armor × 2.5` per SimC `engine/player/player.cpp:1681`, and
`mastery_block_value_scaling` flipped from 0.5 → 0.0 (SimC truth).

## TL;DR

- **Best K:** 3200, RMSE 0.0634. Unchanged from pre-F12 best (also K=3200).
- **At canonical K=3430:** RMSE ≈ 0.079 (linear interp between K=3400/0.076 and
  K=3500/0.087). Below the 0.080 ship threshold set ahead of the sweep.
- **Verdict:** ship F12.2 + F12.3. The pre-set ≤0.066 bar for automatically
  unblocking F11/F7/F13 was not met — that retune attempt remains a separate
  next-session investigation.
- **Residual bias:** at K=3400 most per-log residuals are positive
  (sim over-predicting damage). Consistent with under-mitigation — likely
  missing block-chance mastery (F13) and/or the F7 crit-block retune to 1.5,
  or the Wowhead-sourced shield_armor=931 being too low.

## Verdict criteria (set BEFORE the sweep ran)

| RMSE at K=3430 | Decision |
| --- | --- |
| ≤ 0.066 | Ship + F11/F7/F13 mastery chain unblocked |
| 0.066 < x ≤ 0.080 | Ship + document modest regression bought structural truth |
| > 0.080 | Don't ship; diagnose (Wowhead-931 vs per-enemy armor_coeff) |

**Observed:** ~0.079 → middle band → ship + document.

## Full sweep result

```
K=  2900: RMSE=0.089
K=  3000: RMSE=0.076
K=  3100: RMSE=0.067
K=  3200: RMSE=0.063  ← best
K=  3300: RMSE=0.068
K=  3400: RMSE=0.076
K=  3500: RMSE=0.087
K=  3600: RMSE=0.101
K=  3700: RMSE=0.116
K=  3800: RMSE=0.131
K=  3900: RMSE=0.145
K=  4000: RMSE=0.159
K=  4100: RMSE=0.178
K=  4200: RMSE=0.194
```

18 Brutoh replays, 300 iterations each, `mastery_block_value_scaling=0.0`,
`block_value_armor_multiplier=2.5`, `shield_armor=931` (Spellbreaker's
Rebuke at the historical ilvl 285 the calibration logs were recorded at).

### Note on the 931 vs 989 shield_armor split

The K sweep used **`shield_armor=931`** because every log in `examples/`
is from before 2026-05-19, when Brutoh's Spellbreaker's Rebuke was at
ilvl 285. Wowhead XML returns 931 for that ilvl; the in-game tooltip on
2026-05-20 confirmed the value was right at the time of those logs.

On 2026-05-19 the shield was upgraded with a voidcore to ilvl 295 and
the in-game tooltip now reads **989 armor** (also: 66 str, 952 stam,
42 crit, 42 haste). `data/characters/brutoh.yaml` now stores **989** as
the current state for fresh sim runs of Brutoh's character. The K sweep
above is *not* invalidated — it's correctly matched to the historical
log corpus. Any re-sweep against the same corpus must override
`shield_armor` to 931, or wait for a fresh log set post-upgrade.

This also closes the F12 recon's open question about Wowhead-XML
reliability for crafted items: it was reliable. The number simply
reflected an older ilvl.

## Comparison to pre-F12

Pre-F12 (`mastery_block_value_scaling=0.5`, no shield-armor sourcing) per
session 15 memory: best K=3200 RMSE ~0.062, K=3430 RMSE ~0.066.

| K | Pre-F12 RMSE | Post-F12 RMSE | Δ |
| --- | --- | --- | --- |
| 3200 (best) | ~0.062 | 0.063 | +0.001 |
| 3430 (canonical) | ~0.066 | ~0.079 | +0.013 |

Best K stayed flat — F12 did NOT shift empirical best toward canonical. The
self-fit absorbed the change. Conclusion: there's still missing mitigation
that K is compensating for; F12 alone is not sufficient to close the
empirical-vs-canonical K gap.

## Why F12 alone doesn't close the gap

The pre-F12 `mastery_block_value_scaling: 0.5` was masking more than just the
missing shield-armor contribution — it was likely also masking the missing
`mastery_block_chance_scaling` (F13, currently 0.0 — SimC says 0.5) and/or
the under-tuned `mastery_crit_block_scaling` (currently 1.0 — SimC says 1.5).

Replacing the 0.5 fudge with only the shield-armor piece (F12) supplies one
of three missing axes. The other two are deferred and would need a separate
atomic ship to test. Now that shield_armor=931 has been ground-truth-verified
(see "Note on 931 vs 989 split" above), the residual bias cannot be blamed
on a wrong shield value — it really does point at the deferred F13 + F7
retune.

Residual sign analysis at K=3400 (where the sweep curve crosses zero-bias):

```
Residuals: -1.0%, -11.3%, +0.7%, +11.0%, +4.3%, -7.7%, +8.6%, +10.8%,
           +7.5%, +11.7%, +0.1%, +2.3%, +6.5%, +11.7%, +7.5%, -3.3%,
           +6.5%, -5.3%
```

13 of 18 positive, mean ~+3.6%. Sim systematically under-mitigates →
unmodeled DR source still missing. Adding mastery_block_chance=0.5 would
roughly add `mastery_pct × 0.5 ≈ 15% × 0.5 = 7.5pp` more block events at
~40% block-DR each, i.e. ~3pp damage reduction on average. That's roughly
the magnitude of the observed positive bias.

## Open questions for the next session

1. ~~Is shield_armor=931 the right value?~~ **Resolved 2026-05-20:** in-game
   tooltip confirmed 931 was correct for the ilvl 285 the calibration logs
   were recorded at. Current Brutoh is 989 (ilvl 295 post-voidcore); the
   YAML stores 989 for fresh sim runs. Wowhead XML for crafted items is
   reliable — the F12 recon's framing on this was wrong.

2. **F11/F7/F13 mastery-chain retune.** With F12's shield-armor sourcing
   in place, the structural masking is mostly gone. A targeted ship of
   `mastery_block_chance_scaling: 0 → 0.5` (F13 wiring + scaling) and
   `mastery_crit_block_scaling: 1.0 → 1.5` (F7 retune) should add back
   mitigation. Re-sweep K — target RMSE at canonical ≤ 0.066 to validate.

3. **Per-enemy armor_coeff.** SimC's block resist uses the attacker's
   `armor_coeff` (sc_warrior.cpp:9146), not the player's K. At canonical
   key levels the player K=3430 substitute is probably close, but the
   spread of residuals across keys may have a key-level component.
   Worth one residual-vs-key-level scatter once F13/F7 land.

## Constants snapshot post-ship

```yaml
base:
  block_value_pct: 0.30                # Prot Pal placeholder
  block_value_armor_multiplier: 2.5    # NEW (F12) — SimC engine/player/player.cpp:1681
  mastery_block_value_scaling: 0.0     # F12: SimC truth (was 0.5 fudge)
  mastery_crit_block_scaling: 1.0      # F7 retune still deferred
  # mastery_block_chance_scaling: 0.5  # F13 still deferred
```

`k_constant` stays at 3430. We are explicitly NOT re-fitting K to 3200; the
canonical-vs-empirical gap is a known piece of structural debt that we keep
visible rather than papering over with another self-fit.
