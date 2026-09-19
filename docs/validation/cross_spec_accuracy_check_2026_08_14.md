# Cross-spec accuracy spot-check — 5 independent WCL players per tank spec (2026-08-14)

**Status: measurement only. No engine/constants change, no `calibration_tier` change.**
This is the first time ALL SIX modeled tank specs have been checked against independent
players (not the single-player corpora each spec was characterized on) in one pass, and
the first live check since the 12.1.0 "Curse of Ula'tek" scalar-bump work (PR #472,
#477-480) shipped 2026-08-11.

## Why this run

The user asked to pull 5 real WarcraftLogs fights per tank spec and check whether simf's
predictions hold up. Prot Warrior got exactly this treatment on 2026-07-25 (see
`protwarrior_independent_players_2026_07_25.md`) and it surfaced a real generalization gap
the single-player corpus never could. The other five specs had never had an independent-
player check at all — only their own (much smaller, single- or few-player) characterization
corpora. This run extends the same method to all six in one pass, on fresh post-12.1.0 logs.

## Method

**Discovery.** `characterRankings` (used for the 2026-07-25 Warrior check) redacts report
codes on this API — a deliberate anti-scraping measure, confirmed again this session.
Public rankings-page scraping (the documented Playwright workaround) is now also blocked:
`warcraftlogs.com` returns a persistent Cloudflare "Just a moment..." 403 to this session's
browser, where it worked in July. Neither prior discovery technique works anymore.

**New discovery path that does work:** `reportData.reports(zoneID: 47, page: N)` — a
global recent-public-reports feed, distinct from `characterRankings`, that does **not**
redact `code`. Paged through it once, checking each report's largest keyed (M+) fight's
`playerDetails` for a tank matching each target spec, and confirming `CombatantInfo`
(ACL-on) via `character_from_wcl` + a non-empty `wcl_to_replay_data` event stream before
accepting a candidate. Found all 30 fights (5 × 6 specs) within the first **125** reports
scanned — plenty of live zone-47 data exists.

One wrinkle hit and worked around: `wcl_api._icon_to_class_spec_slug` renders WCL's
`"DeathKnight-Blood"` / `"DemonHunter-Vengeance"` icon strings as `blood_deathknight` /
`vengeance_demonhunter` (no underscore before the compound class name) — a difference
from this project's own `constants.yaml` slugs (`blood_death_knight` /
`vengeance_demon_hunter`) that's already known and explicitly tested
(`test_icon_to_class_spec_slug_death_knight_blood`, `tests/test_wcl_api.py`), not a new
bug. The discovery script aliases the two before bucketing; nothing in `src/simf/` needed
a change.

**Validation.** Each spec's 5 fights ran through `scripts/cross_player_validation.py`
unmodified — the exact tool + gate built for the Warrior 2026-07-25 check — at canonical
K=3430, `iterations=50`, `seed=42`, `healing_profile=m+_high_key_healer`. Manifests are
committed at `src/simf/data/calibration_corpora/<spec>_cross_player_wcl_2026_08_14.yaml`;
results below are reproduced exactly by running the tool against them directly (spot-
checked for Vengeance DH, bit-for-bit match).

## Results

| Spec | n | Mean signed bias | RMSE | Within ±15% | Cross-player gate |
|---|---:|---:|---:|---:|---|
| Prot Warrior | 5 | **+7.6%** | 0.148 | 4/5 (80%) | — (measurement only, see below) |
| Blood DK | 5 | **+11.9%** | 0.143 | 3/5 (60%) | FAIL (bias >8%) |
| Guardian Druid | 5 | **−10.5%** | 0.155 | 3/5 (60%) | FAIL (bias >8%) |
| Prot Paladin | 5 | **+31.4%** | 0.351 | 0/5 (0%) | FAIL |
| Brewmaster Monk | 5 | **+42.3%** | 0.431 | 0/5 (0%) | FAIL |
| Vengeance DH | 5 | **+54.2%** | 0.566 | 0/5 (0%) | FAIL |

Gate is `scripts/cross_player_validation.py`'s ratified bar (≥5 players, mean |bias| ≤8%,
≥70% within ±15%) — the same one Prot Warrior failed on 2026-07-25. Per-fight deltas:

- **Prot Warrior:** ProtWarr Player 1 −3.4%, ProtWarr Player 2 +27.6%, ProtWarr Player 3
  −8.4%, ProtWarr Player 4 +11.3%, ProtWarr Player 5 +11.1%
- **Blood DK:** BloodDK Player 1 +16.3%, BloodDK Player 2 +6.4%, BloodDK Player 3 +4.0%,
  BloodDK Player 4 +7.5%, BloodDK Player 5 +25.4%
- **Guardian Druid:** Guardian Player 1 −26.3%, Guardian Player 2 −9.1%, Guardian Player 3
  −17.2%, Guardian Player 4 +8.3%, Guardian Player 5 −8.2%
- **Prot Paladin:** ProtPal Player 1 +27.7%, ProtPal Player 2 +15.6%, ProtPal Player 3
  +30.5%, ProtPal Player 4 +21.8%, ProtPal Player 5 +61.3%
- **Brewmaster Monk:** Brew Player 1 +32.6%, Brew Player 2 +35.5%, Brew Player 3 +56.2%,
  Brew Player 4 +46.7%, Brew Player 5 +40.4%
- **Vengeance DH:** VDH Player 1 +51.5%, VDH Player 2 +69.3%, VDH Player 3 +32.0%,
  VDH Player 4 +75.9%, VDH Player 5 +42.2%

All 30 fights are gear-certain (`CombatantInfo`/ACL-on), zone 47 ("Mythic+ Season 1"),
key levels +6 to +22, spanning all 8 Season 1 dungeons.

## Interpretation

**The ranking matches the documented `calibration_tier` history closely — this is a
consistency check the model passes, even though 4 of 6 specs fail the accuracy gate
outright.** The two specs that have ever reached `calibrated` (Prot Warrior, Guardian
Druid) are the two best fits here (RMSE 0.148/0.155, both ~60-80% within ±15%). Blood
DK — documented as "closest" of the never-calibrated specs — is essentially tied with
them (RMSE 0.143). The three specs with the largest known, named, still-open coverage
gaps (Prot Paladin's unmodeled Bulwark of Order/Solace/Avenging-Wrath heal-inflation;
Brewmaster's Celestial-Brew-never-presses-in-forward-sim + stagger over-ticking;
Vengeance DH's long-standing unresolved magic-DR residual) are, in the same order, the
three worst fits (RMSE 0.351 / 0.431 / 0.566). Nothing here contradicts what CONTRIBUTING.md
and `docs/calibration.md` already say — it corroborates it with fresh, independent,
post-12.1.0 data across specs that had never had more than a handful of logs each.

**Prot Warrior:** +7.6% mean bias, RMSE 0.148, 80% within ±15% — close to (slightly
*better* than) the 2026-07-25 cross-player finding (+11.0%, RMSE 0.150, 67%) on a
completely different set of 5 players, post-12.1.0. The known generalization gap is
still there and of similar size; it hasn't gotten worse under the new scalars, and this
run alone doesn't move the `characterized` tier (the existing 15-player finding already
set that bar).

**Guardian Druid:** −10.5% mean bias (the *first* cross-player check this spec has ever
had — its `characterized` downgrade came from a same-corpus LOO-CV failure, not a
cross-player one). Sign flipped versus the near-zero same-player bias (+0.2%, AnonGuardian1
corpus) and RMSE moved from 0.119 to 0.155 — a real but not shocking amount of spread
for n=5 independent players.

**Blood DK:** +11.9% mean bias, RMSE 0.143 — in line with the documented local-log
figure (RMSE 0.152, 4/5 within ±15%) and the best-generalizing of the four
never-calibrated specs, on players NOT confined to the single known hero-talent build
(Deathbringer) this project's own corpus was limited to.

**Prot Paladin:** +31.4% mean bias, 0/5 within ±15%, one outlier at +61.3%
(ProtPal Player 5). Large, consistent over-prediction is exactly the signature you'd expect
from missing defensive levers (engine doesn't credit Bulwark of Order, Solace, or
Avenging Wrath's heal-inflation) — this is a fresh, larger-sample confirmation of a
gap ROADMAP.md already names, not a new finding.

**Brewmaster Monk:** +42.3% mean bias, 0/5 within ±15%, tight-ish range (+32.6% to
+56.2%) — consistent with the documented "Celestial Brew never presses in geared
forward-sim" (0 credit for a major defensive CD) plus the known stagger-over-ticking
gap. The consistency (low variance vs. the other two failing specs) suggests one
dominant missing mechanism rather than several small ones.

**Vengeance DH:** +54.2% mean bias, RMSE 0.566, 0/5 within ±15%, widest spread (+32.0%
to +75.9%). This is *worse* than the last documented figure (+37.5/+62.0/+36.5%, RMSE
0.468, 3 same-hero-talent players, 2026-07-04) — plausibly because this 5-player sample
pulls in hero-talent/gear diversity (only Fel-Scarred was ever characterized; Aldrachi
Reaver was flagged as "one data point, uncharacterized" back then) the old corpus never
had, not because anything regressed. Confirming that needs a hero-talent breakdown this
doc doesn't attempt — flagged as a follow-up, not a conclusion.

## Caveats

- n=5 per spec is small; per-player variance is real (see Guardian's −26.3% to +8.3%
  spread). This is a spot-check, not a promotion-grade run — the ratified cross-player
  gate needs n≥5 players AND was designed around exactly this sample size for Warrior,
  so it's being applied consistently, not loosened.
- No hero-talent / hero-talent-build breakdown was done per fight (would matter most
  for Vengeance DH and Blood DK, where the existing docs already flag hero-talent
  corpus gaps).
- `iterations=50` (matching the existing cross-player tool's default), not the
  16-log ratified corpus's deeper settings — adequate for a bias/RMSE read, not for
  re-deriving K.
- This does not change any `calibration_tier`. Promotion/demotion requires the full
  criteria set in `docs/calibration.md`, not a single 5-player spot-check.

## Reproduce

```bash
for spec in protection_warrior protection_paladin blood_death_knight \
            vengeance_demon_hunter brewmaster_monk guardian_druid; do
  python scripts/cross_player_validation.py \
    --manifest src/simf/data/calibration_corpora/${spec}_cross_player_wcl_2026_08_14.yaml \
    --wcl-cache-dir examples/wcl_cache
done
```
