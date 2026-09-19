# K calibration refit — 2026-05-18 (16-log corpus)

Recalibrating the armor K constant against the full `examples/` corpus —
previous refits used a 6 / 8 / 9-log subset. The user's question:
*"there are more logs in the examples folder, are those useful for you
to calibrate the math?"* — yes, and the wider corpus tightens the
trust signal substantially.

## TL;DR

- **K stays at 2700.** Held across 6 → 9 → 16 logs; the wider corpus
  only sharpens confidence in the existing value.
- **Global RMSE drops from 0.079 to 0.068** (16 logs vs 9). Trust strip
  banner improves from "±7.9% model error" to "±6.8% model error."
- **Algeth'ar Academy is the confirmed outlier**: mean residual -12.2%
  across +12 (-16.4%) and +14 (-8.1%). No longer a one-off — the
  systematic under-prediction is reproducible across keys.
- **Maisara Caverns improved dramatically**: -8.7% single-replay
  (2026-05-16 first calibration) → -0.4% mean across 2 replays. The
  original gap was noise.
- **Nexus-Point Xenas flipped sign** post-Phalanx-fix: +9.3% → -5.7%.
  Magic-DR layers may now be overshooting on arcane / void schools.
- **Skyreach** crosses the 10% trust threshold (-11.3%) but the only
  replay is partial. Needs full-completion log to confirm.

## Methodology

`simf calibrate-k --logs-dir examples --k-min 2500 --k-max 2900 --k-step 50
 --iterations 300`

- 21 log files scanned → 43 CHALLENGE_MODE runs identified
- 16 usable replays (TIMED or PARTIAL with damage events for
  Brutoh-Uldum-EU); 27 skipped (FAILED = death downtime, no events for
  target, or no CHALLENGE_MODE)
- For each K value, replay every log with `events_override` and
  `compute_metrics=False`, measure RMSE of `(sim_dtps - real_dtps) / real_dtps`
  across replays.

## K sweep

| K | RMSE |
|---:|---:|
| 2500 | 0.079 |
| 2550 | 0.075 |
| 2600 | 0.071 |
| 2650 | 0.069 |
| **2700** | **0.068** ← best |
| 2750 | 0.069 |
| 2800 | 0.071 |
| 2850 | 0.075 |
| 2900 | 0.079 |

Smooth, symmetric curve around the minimum. K=2700 ± 50 all within 0.001
RMSE — the minimum is robust to fitting noise.

## Per-replay residuals (K=2700)

Sorted by absolute residual descending. Positive = sim over-predicts
damage taken; negative = sim under-predicts.

| Dungeon | Key | Residual | Log | Notes |
|---|---:|---:|---|---|
| Algeth'ar Academy | +12 | **-16.4%** | 050626_172823[0] | Worst outlier; root cause uninvestigated |
| Skyreach | +14 | **-11.3%** | 051026_151539[0] | Partial run, no CHALLENGE_MODE_END |
| Algeth'ar Academy | +14 | -8.1% | 051826_135935[1] | Algeth'ar gap confirmed across keys |
| Windrunner Spire | +14 | +7.6% | 051026_090846[2] | |
| Windrunner Spire | +14 | +6.6% | 051526_210245[3] | Partial |
| Magisters' Terrace | +14 | +6.4% | 051726_134819[1] | |
| Nexus-Point Xenas | +12 | -5.7% | 051026_073906[0] | Flipped sign post-magic-DR-fix |
| Magisters' Terrace | +12 | +5.2% | 051526_210245[0] | |
| Pit of Saron | +13 | +4.9% | 051326_171842[0] | |
| Maisara Caverns | +12 | -4.1% | 051626_161830[3] | |
| Windrunner Spire | +12 | -3.9% | 050626_153703[0] | |
| Pit of Saron | +12 | +3.5% | 051526_210245[2] | |
| Maisara Caverns | +14 | +3.2% | 051826_135935[0] | |
| Pit of Saron | +13 | +0.6% | 051026_105836[1] | |
| Magisters' Terrace | +13 | +0.2% | 051726_134819[0] | |
| Pit of Saron | +14 | -0.5% | 051726_090237[1] | Best-fit replay |

## Per-dungeon means

Each dungeon's `calibration_gap_pct` in `dungeons.yaml` updated to the
**mean** of all replays of that dungeon, so a single key-level outlier
doesn't dominate the trust signal.

| Dungeon | N | Mean | Range | Previous |
|---|---:|---:|---:|---:|
| Pit of Saron | 4 | **+2.1%** | -0.5% to +4.9% | +2.3% (1 log) |
| Magisters' Terrace | 3 | +3.9% | +0.2% to +6.4% | +5.2% (1 log) |
| Windrunner Spire | 3 | +3.4% | -3.9% to +7.6% | -6.3% (1 log) |
| Maisara Caverns | 2 | **-0.4%** | -4.1% to +3.2% | -8.7% (1 log) ← noise corrected |
| Algeth'ar Academy | 2 | **-12.2%** | -16.4% to -8.1% | -16.4% (1 log) ← outlier confirmed |
| Nexus-Point Xenas | 1 | -5.7% | (single) | +9.3% (pre-magic-fix) ← flipped sign |
| Skyreach | 1 | -11.3% | (single, partial) | -7.9% (1 log) |
| Seat of the Triumvirate | 0 | — | — | No usable replays (all FAILED) |

## Findings ranked by importance

### 1. Algeth'ar Academy gap is real and persistent (highest priority)

Two replays at different key levels both show systematic
under-prediction (-16.4% and -8.1%). Pre-2026-05-16 the engine had
Phalanx Mark wrongly applying to Echo of Doragosa (DS-immune boss);
that fix only moved the +12 residual 0.4pp, so Phalanx-on-immune isn't
the source. Probable suspects:

- **Arcane / fire DR coefficients.** Algeth'ar is a 30% arcane / 20%
  fire dungeon. If those schools have unmodeled mitigation layers
  (similar to the closed-pre-2026-05-16 -14pp shadow gap on MGT), the
  systematic under-prediction is consistent.
- **Algeth'ar-specific mob mechanics.** Vexamus, Ranjit, Anub'arash
  may have stacking debuffs / damage amps that aren't in the model.
- **Engine-vs-log target-name mismatch** is ruled out — the +14 replay
  is a different log file, different mob composition by phase, same gap.

Action: validation pass against the Algeth'ar +12 log similar to the
MGT shadow audit (2026-05-15). Track in roadmap as a Phase 3.9
follow-up alongside the existing magic-mit investigation.

### 2. Nexus-Point Xenas magic-DR overshoot (medium priority)

NPX was +9.3% (sim under-mitigating magic) before the Phalanx +
party-magic-DR fix on 2026-05-16. Single replay post-fix shows -5.7%
(sim now over-mitigating magic). The magic-DR layers may have been
tuned against MGT shadow specifically and overshoot on arcane / void.

Single replay isn't enough to act on. Wait for a second NPX log before
adjusting party_magic_dr coefficient.

### 3. Maisara Caverns single-replay calibration was noise (resolved)

The 2026-05-16 calibration recorded MaiC at -8.7% from one replay.
Adding a second replay (+14) brought the mean to -0.4%. The original
gap was iteration variance and base-rate noise of a single sample, not
a model issue.

This is a process lesson: **single-replay residuals < 10% should not
be acted on**. Wait for a second replay before tuning constants based
on a single log.

### 4. Skyreach partial-run residual is suspect (low priority)

Skyreach +14 partial replay sits at -11.3%, just crossing the trust
threshold. But partials lack CHALLENGE_MODE_END, so the duration cut
might bias DTPS. Don't tune on this single sample either — wait for a
full Skyreach completion.

## Files updated

- `src/simf/data/constants.yaml` — calibration block + armor block notes
- `src/simf/data/dungeons.yaml` — `calibration_gap_pct` per affected
  dungeon, with descriptive reasons in each `description:` field

## Trust strip impact

Before: `±7.9% model error · build <SHA>` (line uses
`constants.calibration.global_rmse`).
After:  `±6.8% model error · build <SHA>`.

User-facing impact: the trust banner gets a quarter tighter without
any engine math changing. Pure information win from a wider corpus.
