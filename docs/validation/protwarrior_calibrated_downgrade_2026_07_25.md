# Prot Warrior `calibrated` → `characterized` — downgrade (2026-07-25)

**Status: tier change, human-ratified. No engine/constants numeric change** — `global_rmse`
(0.073) is untouched, matching the Guardian downgrade precedent below: the model itself is
unchanged and the Brutoh-only fit is still excellent. Only `calibration_tier` moves.

**Note: `global_rmse` moved anyway, later the same day, for an unrelated reason.** The
KYFOTG merge (see `docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md`)
was a separate decision made after this downgrade, moving `global_rmse` 0.073→0.080. Every
"0.073" figure below describes the state at the time THIS decision was made — it is no
longer the live value, but this doc's own conclusion (a scope finding, not a regression) is
unaffected either way.

## What changed

`protection_warrior.calibration_tier`: `calibrated` → `characterized`, three days after
being ratified `calibrated` (2026-07-22, PR #418, RMSE 0.073, LOO-CV PASS).

## Why

The `calibrated` tier's own documented meaning (`docs/calibration.md`): clearing ALL of
≥8 F-consistent runs, |mean signed bias| ≤5%, ≥75% of runs within ±15%, RMSE ≤0.15, and a
passing LOO-CV gate — against "whichever corpus `simf calibrate-k` resolves." For Prot
Warrior that has only ever meant one corpus: 16 logs, one player (Brutoh), one gear
progression. The tier's practical promise to a reader is stronger than that scope — "K, the
spec's mitigation constants, and its policy dispatch are all log-anchored well enough to
trust the absolute numbers unconditionally."

On 2026-07-25, that promise was tested for the first time against real data from anyone
other than Brutoh: 15 independent Protection Warrior players, found via WCL's public
`characterRankings`, run through the identical `calibrate-k` machinery at the identical
K=3430. See `docs/validation/protwarrior_independent_players_2026_07_25.md` (7 players,
+20-22 keys) and `docs/validation/protwarrior_independent_players_lowkey_2026_07_25.md`
(8 players, +17-18 keys — the lowest key level WCL's rankings index reaches at all,
confirmed by paging deep across all 8 dungeons).

Applying the SAME promotion bar to that independent set:

| Criterion | Bar | Ratified Brutoh corpus | 15 independent players |
|---|---|---:|---:|
| \|mean signed bias\| | ≤5% | +1.8% ✓ | **+11.0% ✗** |
| within ±15% | ≥75% | 100% (16/16) ✓ | **67% (10/15) ✗** |
| RMSE | ≤0.15 | 0.073 ✓ | 0.150 (exactly at the line) |

Two of three checkable numeric criteria fail outright on independent data; the third sits
exactly on the boundary. (F-consistency and LOO-CV aren't directly computable on a
15-single-fight-per-player set the same way — see the two source docs' own caveats.)

Before treating this as decisive, two alternative explanations were checked and ruled out,
not assumed away:

- **Not a hydrate-quality artifact.** A spot-check of 4 independent players' WCL-hydrated
  stats found `shield_armor` correctly populated (989-1007, matching Brutoh's own 989),
  and armor/crit/haste/mastery/vers all plausible and non-degenerate — the specific,
  previously-known hydrate bugs this project has hit before (dropped shield_armor,
  mis-sourced crit) are absent here.
- **Not primarily a key-level effect.** Independent bias rises mildly with key level (+9.3%
  at +17-18 → +13.0% at +20-22), but even at +17-18 — squarely inside Brutoh's own tested
  +10-17 range — independent players still miss by +9.3% mean, while Brutoh's own corpus at
  the identical keys sits at +1.8%. A genuine spec-wide, key-level-driven gap would predict
  near-zero bias for independent players at keys Brutoh's own data already fits well. That
  isn't what happened.

The remaining, load-bearing explanation: the ratified calibration (K=3430, Vanguard's 0.70
coefficient, the 2026-07-21 Shield Block fixes) is real and correct for Brutoh specifically,
and does not transfer to other players' gear/builds/parties. Both validators on the
KYFOTG branch confirmed the engine math itself is correct — this is not a bug to fix, it's
a scope the tier's current definition doesn't disclose.

## Precedent

This is the same evidential class — and the same response — as the project's two prior
`calibrated` downgrades, both real findings rather than mystery regressions:

- **Guardian Druid, 2026-07-17**: downgraded when its own LOO-CV gate (a same-corpus,
  held-out-run check) failed at both the full-corpus and F-consistent-subset scope (71%/69%,
  both under the ≥75% bar) — "reflects the project's own cross-validation standard not being
  cleared yet, not a regression in the underlying fit."
- **Prot Warrior, 2026-07-18**: downgraded when a Demo Shout/Phalanx replay double-count was
  found — fixing it made the honestly-measured bias worse (RMSE 0.068 → 0.138), because the
  bug had been masking a real gap, not creating a fake one.

This downgrade differs from both in kind: no bug was found in the engine, and the LOO-CV gate
(a within-Brutoh check) still legitimately passes. What failed is a check this project had
never run before — across players, not across runs of the same player. That gap is real
precisely because the check is new, not because anything regressed.

## What would restore `calibrated`

Not simply re-running today's LOO-CV again — that only re-validates within Brutoh's own
corpus, which was never in question. Per the new promotion criterion added alongside this
downgrade (see `docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md`),
Prot Warrior would need to clear a genuine cross-player check: multiple independent players'
WCL fights, run through the same pipeline, landing within the new gate's bar. Until then,
`characterized` is the honest tier — the model is well-understood and Brutoh's own fit is
excellent; it just isn't yet shown to generalize.

## Caveats

- Every one of the 15 independent players is drawn from WCL's "notable parse" index, which
  selects for unusually competent, low-death, efficient play — if anything this should bias
  results TOWARD looking more like a well-piloted Brutoh, not away from it. It doesn't
  obviously explain the gap away.
- Single fight per independent player — real per-pull noise is not fully averaged out the
  way it is across Brutoh's 16 logs. A larger independent sample would sharpen this further,
  not likely reverse its direction given the size of the current gap.
- `global_rmse` and every other Prot Warrior constant (Vanguard's coefficient, K=3430, the
  Shield Block fixes) are unchanged by this doc — none of that work is being questioned, only
  the tier label's scope.

## Files

- This doc.
- `docs/validation/protwarrior_independent_players_2026_07_25.md` — round 1 data (+20-22).
- `docs/validation/protwarrior_independent_players_lowkey_2026_07_25.md` — round 2 data
  (+17-18) and the combined analysis this downgrade is based on.
- `src/simf/data/constants.yaml` — `specs.protection_warrior.calibration_tier` flipped to
  `characterized`, with an inline comment pointing here.
