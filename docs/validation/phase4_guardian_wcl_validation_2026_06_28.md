# Guardian cross-player validation against 11 WCL logs — stays `calibrated: false`

**2026-06-28.** Before deciding whether to flip `guardian_druid.calibrated:
true`, validated the Guardian model against a spread of **real, recent,
gear-certain** Guardian logs from Warcraft Logs — NOT the single AnonGuardian1 profile
the model was built on. Result: **do not flip.** The model is roughly unbiased
but scatters wider than ±15% across the player population, driven by a magic-
mitigation gap in magic-heavy content.

## Method

- Pulled Guardian Druid rankings from **WCL zone 47 (Midnight Season 1)** —
  confirmed zone 47 = "Mythic+ Season 1" (its 8 encounters match simf's S1
  catalog); zone 45 = "Season 3" (TWW, the ~30× era trap — NOT used).
- Selected **12 distinct players** spanning **+16 / +19 / +22** across 4
  dungeons (Algeth'ar, Magisters', Maisara, Nexus-Point), dated 2026-05-19 →
  2026-06-28.
- `scripts/calibrate_spec_from_wcl.py guardian_druid --iters 200 --ks 3000,3430,3900`.
  For each fight: gear-certain Character from the fight's `COMBATANT_INFO`
  (exact gear/talents/stats worn in *that* run), replay the actual damage-taken
  events, compare **predicted mean DTPS vs actual DTPS** at canonical K=3430.
- 1 of 12 dropped (ACL off → no `COMBATANT_INFO`): **11 usable**.

## Result — at canonical K=3430

| Key | Dungeon | Player | Δ (sim−actual)/actual |
|----|---------|--------|------|
| +16 | Algeth'ar | Player 1 | **+1.7%** |
| +16 | Magisters' | Player 2 | **+1.6%** |
| +16 | Maisara | Player 3 | **−20.1%** ✗ |
| +19 | Algeth'ar | AnonPlayerX11 | −13.7% |
| +19 | Magisters' | Player 4 | **+8.4%** |
| +19 | Maisara | Player 5 | **+12.1%** |
| +19 | **Nexus-Point** | Player 6 | **+21.2%** ✗ |
| +22 | Algeth'ar | Player 7 | −12.6% |
| +22 | Magisters' | Player 8 | **+7.8%** |
| +22 | Maisara | Player 9 | **−4.9%** |
| +22 | **Nexus-Point** | Player 10 | **+33.5%** ✗ |

- **8 / 11 within ±15%** (73%). 3 outliers.
- **RMSE 0.155** at canonical K (vs Prot Warrior's 0.068). Best-fit K = **3000**
  (RMSE 0.145) — only marginally better than canonical, so there is **no large
  systematic flat-mitigation gap**; canonical K is fine.
- **Mean delta ≈ +3.2%** → the model is roughly **unbiased**; the problem is
  **variance**, not bias.

## What the scatter is (and isn't)

- **The two over-prediction outliers (+21%, +33%) are both Nexus-Point Xenas**,
  the most magic-heavy dungeon in the set (**70% magic** — 40% arcane + 30%
  void). The model over-predicts damage there because it **under-credits
  Guardian magic mitigation** — the known "magic residual." Rough decomposition:
  a +33% total over-prediction on a 70%-magic fight ≈ a ~+47% magic-bucket
  over-prediction. **This is the single highest-value Guardian engine follow-up,
  now quantified.** (Candidates: Bear Form's arcane DR and magic-DR talents the
  model doesn't yet credit.)
- **The negative outlier (−20%, Maisara +16)** is in a physical-heavy dungeon
  (55% phys): the sim predicts *less* damage than the tank actually took →
  consistent with **avoidable-mechanic damage the engine deliberately doesn't
  simulate** (the player ate hits), NOT a mitigation modeling error.
- Remaining spread (the ±8–14% band) is the expected mix of build/Ironfur-uptime
  and cooldown-execution variance the single-profile model can't pin per-player —
  exactly the **un-regressed haste/Ironfur slope** caveat.

## Verdict

**`guardian_druid.calibrated` stays `false`.** 8/11 within ±15% and RMSE 0.155
do not clear the bar. Crucially, this **vindicates not flipping on the single
AnonGuardian1 profile** (13/16 within ±15%, mean 9.2% — optimistic): across 11 distinct
players the model is unbiased but scatters wider. The model remains **usable**
(no systematic bias → gear-A/B and relative comparisons are sound), but its
**absolute** per-player DTPS isn't ±15%-tight, so the honest label is
"characterized, not population-calibrated."

## Follow-ups (priority order)

1. **Close the Guardian magic-mitigation residual** — now quantified at ~+20–47%
   magic-bucket over-prediction (Nexus-Point, 70% magic). Highest leverage. A
   re-run of this same 11-log corpus is the regression test.
2. Re-validate after #1; if RMSE drops under ~0.10 and ≥90% within ±15% across a
   ≥10-player corpus, reconsider the flip.
3. The un-regressed haste/Ironfur slope (single Elune's-Chosen build) and
   medium-confidence mastery conversion remain (see
   `phase4_guardian_finish_2026_06_28.md`).
