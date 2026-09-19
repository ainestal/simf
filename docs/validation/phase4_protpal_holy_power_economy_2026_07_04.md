# ProtPal Holy-Power / Word-of-Glory economy — measured and corrected (2026-07-04)

Follow-up to `phase4_protpal_ardent_defender_2026_07_03.md`, which named a
"holy-power economy shortfall" as the clearest post-AD-fix lead: Bruttah
appeared to spend ~0.70 HP/s (SotR + WoG at 3 Holy Power each) against the
model's ~0.60 HP/s generation. That arithmetic turned out to be built on a
false premise — the lead was real, but the defect is structural, not a rate
shortfall. New tooling: `scripts/analyze_protpal_holy_power.py`.

Corpus: the same 4 timed Bruttah runs (`examples/bruttah-prot/`) — Pit of
Saron +2 (976s), Algeth'ar Academy +2 (1062s), Magisters' Terrace +5 (1204s),
Seat of the Triumvirate +9 (1536s).

## TL;DR

- **Word of Glory is overwhelmingly mana-funded in Midnight 12.0.5, not
  Holy-Power-funded** — 93.5% of casts (145/155) cost 50,000 mana (of a
  250,000 pool) and never touch Holy Power. Proven two independent ways from
  the logs (below). A minority, 6.5% (10/155), are hybrid: they pipe-join a
  Holy Power leg alongside the mana leg (e.g. `9|0` / `3|50000`) and draw
  BOTH 3 Holy Power and 50k mana — mechanism uncharacterized, materiality
  negligible (~1% of corpus HP income), included in the conservation
  reconciliation below. The old model charged EVERY WoG 3 Holy Power *with
  first priority*, so in the sim's low-HP regime WoG starved SotR coverage —
  a competition that in reality only the rare hybrid press creates. This is
  the structural half of the fix and the driver of the fake "gap grows with
  key level" gradient: at +2 the sim rarely dropped below the 55% WoG
  threshold (SotR got everything); at +9 it lived there.
- **`holy_power_per_second_base` 0.55 → 0.46**, re-derived from Bruttah's
  *measured* SotR buff uptime at each run's hydrated haste (19.8–23.3%),
  replacing the single-player AnonPPal1 back-solve at 9.58% haste. The
  constant went *down* while the hypothesis said "under-generation" — the old
  value was partially compensating for the WoG drain, and the old sim ran a
  fictitious ~99–100% SotR uptime at low keys (real: 78–86%).
- **`wog_heal_pct_of_max_hp` 0.30 → 0.18** — measured PER PRESS (summing
  same-cast self-heal events, since a press sometimes fires more than one —
  e.g. a bonus/echo heal alongside the main one — before dividing by max HP)
  over 143 presses across the corpus: mean 18.34%, median 16.86%. The
  placeholder was ~1.6× too generous. (An earlier per-*event*, not
  per-*press*, read of this same corpus measured 0.15 — diluted by the
  extra events — and shipped briefly before this correction.)
- **Behavioral policy deliberately NOT retuned** (same discipline as the AD
  doc's deferred proactive-press question): the WoG-first press priority and
  the 0.55 HP threshold are the sim's own heuristic and stay untouched. Only
  the resource ledger (which currency, how much, how fast it refills) and the
  measured heal size changed — all telemetry-pinned.
- `constants_version` → 40 (38 was taken by master's VDH PR #262, which this
  branch forked from; 39 was taken by Blood DK's PR #264, merged first).
  **`calibrated` stays `false`** — see the before/after
  table; the corpus lands nowhere near ±15% at canonical K and this pass
  makes one *low*-key run honestly worse (Algeth'ar) while dramatically
  improving the high-key one (Seat of the Triumvirate, +47.5%→+9.3%).

## Method — what the logs directly expose

Two log surfaces make the whole economy directly measurable (no back-solving
from damage needed):

1. **`SPELL_CAST_SUCCESS` advanced block** carries
   `(powerType, currentPower, maxPower, powerCost)` per cast:
   - SotR lines read `(9, <pre-cast pool>, 5, 3)` — 909 paid casts across the
     corpus, each one a ground-truth pool reading.
   - WoG lines read `(0, <mana>, 250000, 50000)` — power type 0 is MANA.
     Free-proc WoGs and SotRs appear as `cost=0` lines.
2. **`SPELL_ENERGIZE` powerType=9** carries every Holy Power gain. `amount`
   is the *spendable* gain; overflow at cap is separated into `overEnergize`
   (verified in-corpus: at pool 5 the lines read `amount=0, over=3`).

`scripts/analyze_protpal_holy_power.py` combines these with
`parse_self_buff_windows` (SotR buff 132403 uptime), self-target WoG
`SPELL_HEAL` amounts (vs the line's own advanced max-HP field), and the
hydrated per-run haste.

## The proof WoG is overwhelmingly mana-funded, not Holy-Power-funded

**Direct:** 93.5% of WoG cast lines (145/155) across all 4 runs read
`powerType=0, cost=50000` (mana) with no Holy Power component at all. The
remaining 6.5% (10/155) pipe-join a Holy Power leg onto the same line
(`ptype="9|0" cost="3|50000"`) — a real, if uncharacterized, hybrid draw.
Several pure-mana WoGs are *reconstructed* at a Holy Power pool of 1–2 at
cast time (not directly observed — WoG's own advanced block carries mana,
not Holy Power, for these lines) — impossible if they cost 3 HP each.

**Conservation:** replay `income − (paid SotR + hybrid-WoG Holy Power draw)`
through time and score the reconstruction against every pre-cast Holy Power
pool reading carried by a paid SotR *or* hybrid WoG cast line:

| Run | pool readings checked | mismatches (>0.5 HP) |
|---|---:|---:|
| Pit of Saron +2 | 159 | 1 |
| Algeth'ar Academy +2 | 197 | 0 |
| Magisters' Terrace +5 | 250 | 0 |
| Seat of the Triumvirate +9 | 313 | 1 |
| **Total** | **919** | **2 (0.2%)** |

(919 = 909 paid-SotR readings + 10 hybrid-WoG readings.) Charging the 10
hybrid casts their real 3 Holy Power — instead of the naive "WoG never
touches Holy Power" — improves the ledger from 8/909 mismatches to 2/919:
including the hybrid draw doesn't weaken the mana-funding conclusion, it
completes it. The 2 residual mismatches are within noise (an occasional
untracked drain or missed waste event), not evidence of a further hidden
draw. Charging *every* WoG 3 HP (the old model's assumption) instead breaks
the ledger badly — independently re-run as a counterfactual: 115/909
subsequent SotR pre-cast pool readings (12.6%) go out of tolerance, a
14× jump from the 8/909 baseline.

## Per-run economy (measured)

| Run | dur | haste | SotR casts (paid+free) | SotR uptime | buff-gain/press | WoG casts (paid-mana + hybrid + free) | HP income (spendable + wasted-at-cap) | income HP/s | base back-solve (from uptime) |
|---|---:|---:|---|---:|---:|---:|---|---:|---:|
| Pit of Saron +2 | 976s | 19.8% | 184 (158+26) | 78.1% | 4.15s | 27 (22+1+4) | 483 + 367 | 0.495 | 0.4348 |
| Algeth'ar Academy +2 | 1062s | 23.3% | 238 (195+43) | 81.4% | 3.63s | 26 (24+2+0) | 594 + 218 | 0.559 | 0.4401 |
| Magisters' Terrace +5 | 1204s | 19.8% | 293 (248+45) | 85.5% | 3.51s | 34 (29+2+3) | 752 + 283 | 0.625 | 0.4759 |
| Seat of the Triumvirate +9 | 1536s | 21.1% | 370 (308+62) | 85.8% | 3.56s | 68 (60+5+3) | 952 + 345 | 0.620 | 0.4725 |

(WoG counts corrected from an earlier pass — 26/24/32/63 — that silently
dropped the 10 pipe-joined hybrid-resource lines via a bare `except`; true
totals are 27/26/34/68, 155 corpus-wide.)

Duration-weighted: uptime **83.2%**, spendable income **0.582 HP/s**,
back-solved base **0.4584** (from uptime) / 0.5631 (from press count).
14.1–18.1% of SotR casts are free procs on every run. Income by source is
stable across runs: Judgment ≈ half, then a generic "Holy Power" proc line, then
Blessed Hammer, then Divine Toll.

Mana side: pool 250,000 everywhere; every paid WoG in this corpus costs
50,000 mana (a lower cost of 37,500 appears on dual-resource lines
elsewhere in the raw logs, but those belong to a different paladin outside
the 4-run corpus, not Bruttah); some casts are free (cost=0). Regen
estimated from between-cast mana trajectories: pooled median ≈ 2,644/s over
n=34 pairs (p25 2,096, p75 10,339 — noisy upper tail from untracked income)
→ shipped `mana_regen_per_s: 2500` (~1% of max/s), which sustains one WoG
per ~20s after a 5-cast pool burst — matching her observed 12.2–31.6s
median cadence and 1.1–1.3s minimum gaps.

WoG heal size, measured PER PRESS (summing every self-target heal event
attributed to one cast, not averaging per event — see TL;DR): n=143 presses,
mean **18.34%** / median 16.86% of max HP → `wog_heal_pct_of_max_hp: 0.18`
(was 0.30 PLACEHOLDER).

## Choosing the generation constant (and what was rejected)

The sim's rotation abstraction is *smooth income + press SotR near expiry*,
which realizes the full 4.5s buff per press. Bruttah presses at pool cap and
loses pandemic extension — her realized buff-gain is 3.5–4.2s per press. So
three candidate derivations give different numbers, and only one targets the
observable that mitigation actually consumes (fraction of time SotR is up):

- **from-uptime (chosen): 0.46.** `base = 3 × uptime / (4.5 × (1 + haste))`,
  duration-weighted across the 4 runs (0.4348/0.4401/0.4759/0.4725 →
  0.4584). Reproduces real SotR *coverage* under the sim's own press policy.
  Robust to free-proc accounting and income attribution entirely.
- from-income (0.48): spendable energize parity. Overshoots coverage ~87% vs
  the real 83% because the sim doesn't lose extension the way her overcap
  presses do.
- from-press (0.56): funds her press *count* at 3 HP each. Worst overshoot
  (sim coverage → ~100%); rejected for the same reason, amplified.

Cross-player anchor: the old constant's comment records AnonPPal1 at 86–87%
uptime / 9.58% haste → back-solves to 0.526 under the same formula. In
*absolute* terms the two players agree remarkably well (0.55–0.58 HP/s
uptime-equivalent income) across an 11pp haste gap — which suggests the
`(1 + haste_pct)` scaling on generation is too aggressive (builder cast
rates are hasted, but proc income and Divine Toll's cooldown are not linear
in haste). Flagged as a single-build caveat, not fixed here: settling it
needs a second player measured with this tooling at a genuinely different
haste, and the constant is only exercised at ~20% haste (this corpus) and
~10% (AnonPPal1) today.

## The mana model (what it does and doesn't do)

`MitigationState.mana` starts full; `ProtPalPolicy.tick()` regenerates
`mana_regen_per_s`; the WoG branch in `decide()` now gates on and spends
`wog_mana_cost` instead of Holy Power. Emergent behavior: up to 5 rapid WoGs
from a full pool, then ~1 per 20s sustained — shaped like her real bursts.

- **DTPS (the calibration metric) is unaffected by WoG cadence** — WoG heals,
  it doesn't mitigate. The calibration movement below comes entirely from
  SotR coverage no longer being starved (and the honest generation rate).
- HP-trajectory surfaces (deaths, TTD, HRPS) DO see the change: WoG heals
  ~40% less per cast (0.30→0.18) but is almost never Holy-Power-blocked, and
  low HP no longer suppresses SotR. ProtPal death-rate calibration was never
  established (and `runner.py`'s cheat-death gate bug is still open), so no
  death-surface claim is made here.
- Free-proc WoGs (6.5% of her casts corpus-wide, 10/155) are not modeled →
  sim WoG is slightly scarcer than the real one. No GCD floor on WoG bursts
  (sim can chain per-event; her real minimum gap is 1.1s) — named, not
  modeled.

## Calibration: 4-log corpus, canonical K=3430, 300 iterations

| Run | real_dtps | delta, pre-fix (HP-funded WoG, post-AD-fix) | delta, post-fix (mana-funded WoG) |
|---|---:|---:|---:|
| Pit of Saron +2 | 16,634 | +19.8% | +14.7% |
| Algeth'ar Academy +2 | 18,599 | +1.6% | −5.3% |
| Magisters' Terrace +5 | 21,220 | +29.7% | +28.2% |
| Seat of the Triumvirate +9 | 33,540 | +47.5% | +9.3% |

RMSE: 0.297 pre-fix → **0.168 post-fix** (300 iter, canonical K=3430,
`scripts/calibrate_spec_from_logs.py calibrate protection_paladin
--logs-dir examples/bruttah-prot --iters 300`). Full post-fix K-sweep:
best-K=2750 (RMSE=0.119); canonical K=3430 sits up the RMSE curve from
there (monotonic from K=2750 up). The fix makes the low-key runs *worse*
(Pit of Saron +19.8%→+14.7% improves, but Algeth'ar flips from a small
over-prediction to a small under-prediction, −5.3%) while dramatically
improving the high-key runs (Seat of the Triumvirate +47.5%→+9.3%, now
inside ±15%) — consistent with the fake key-level gradient thesis: the old
WoG-vs-SotR Holy Power competition only bit hard once WoG pressing got
frequent at high keys and low HP. `specs.protection_paladin.calibrated`
**stays `false`** — 2 of 4 runs (Algeth'ar, Seat of the Triumvirate) now
clear ±15% but Pit of Saron and Magisters' Terrace don't; no run set clears
uniformly at canonical K.

## Deliberately NOT changed (the overfit line)

- `wog_threshold_hp_pct: 0.55` and the WoG-before-SotR decision order — the
  sim's own emergency heuristic. Retuning either to match Bruttah's personal
  press pattern is the exact trap the AD doc already named for the
  proactive-AD question.
- The reactive Ardent Defender press policy (unchanged, still deferred).
- SotR pandemic/extension cap — irrelevant while the sim presses near expiry
  (it never overbanks); becomes load-bearing only if the press policy ever
  changes.
- Free-proc economies (Shining-Light-style free WoG, free SotR procs). The
  SotR side is implicitly inside the uptime-derived base; the WoG side is
  conservatively omitted.
- `runner.py`'s buff-window-blind cheat-death gate (separate, still-open
  finding from the AD doc).
- `sotr_holy_power_cost: 3` — re-confirmed by 909 cast lines (every paid
  press cost exactly 3).

## Next steps

1. Divine Bulwark's spell-block chance + the blocked-DoT absorb — the
   magic-side layers named since F15, still the leading unmodeled mitigation.
2. The cross-spec ~5-7% run-scoped wedge the Brewmaster decomposition
   surfaced on the calibrated warrior corpus (see
   `phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`) — ProtPal's
   residual should be re-read once that lands, since it isn't spec-local.
3. `runner.py` cheat-death buff-window fix (own PR, own characterization).
4. Higher-key Bruttah logs (+14–18) to shrink the extrapolation distance.
5. Generation haste-scaling: measure a second player at a different haste
   with `scripts/analyze_protpal_holy_power.py` to test `(1 + haste)` vs a
   flatter model (the AnonPPal1/Bruttah absolute-rate agreement above says the
   current form is probably too steep).
