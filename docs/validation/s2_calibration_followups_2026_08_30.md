# Season 2 calibration follow-ups: Blood DK corpus growth, VDH key-level sweep, Warrior low-key sweep (2026-08-30)

**Status: measurement and data-collection only. No engine/constants change, no
`calibration_tier` change for any spec.**

## Why this run

The same-day [S2 cross-spec key-level check](s2_cross_spec_keylevel_check_2026_08_30.md)
surfaced three follow-ups (ROADMAP.md items 9-11, added 2026-08-30): Blood DK's
in-repo corpus is Deathbringer-only, VDH's magic-DR residual showed a
possibly-confounded "cliff" shape, and Prot Warrior's key-2 bucket showed a
large negative bias on a thin, single-dungeon-heavy sample. Each was run as an
independent research pass, in parallel, via three `calibration-scientist`
agents. All three are measurement/characterization work — none change any
constant, promote or demote any `calibration_tier`, or touch engine code.

**Verification note:** the two `cross_player_validation.py`-based tracks (VDH,
Warrior) were independently re-run against the committed manifests below and
reproduce their reported numbers exactly. The Blood DK track's central
hero-talent classification claim (San'layn vs. Deathbringer, via buff windows
434034/440289/440290) was spot-checked against 3 of its 17 fights and
reproduces exactly; the specific "Ossuary active at fight start" claim behind
its Player 1 outlier flag was independently confirmed true.

## Track 1 — Blood DK: San'layn hero-talent corpus growth

**Headline correction, not just an addition.** ROADMAP.md item 9 framed the
2026-08-30 cross-spec check's Blood DK result (n=12, mean bias +3.2%, RMSE
0.125, 83% within ±15% — "the first-ever PASS any spec has posted on this
gate") as needing San'layn *added* to a corpus assumed to already be
hero-talent-diverse. Running the buff-window classification
(`wcl_api.fetch_buff_windows(..., ability_ids={434034, 440289, 440290})`)
against all 12 of that manifest's fights found **every single one is
San'layn** — zero Deathbringer. The "first PASS" was San'layn-only; nobody had
checked. The only Deathbringer-side evidence in this project remains the
original 5 zone-47 (Season 1) WCL fights from
[`phase4_blood_dk_characterization_2026_07_03.md`](phase4_blood_dk_characterization_2026_07_03.md).

**What was done:** confirmed the S2 manifest's 12 fights as San'layn; re-ran
the original 5 Deathbringer fights through the same buff-window check (still
Deathbringer today, 68-110 Rune Carved Plates windows each, zero Blood-Soaked
Ground); ran both cohorts through `scripts/calibrate_spec_from_wcl.py` at
canonical K=3430, iterations=300, for a fresh apples-to-apples comparison at
today's constants (which include Blood-Soaked Ground's 2026-08-11 5%→8%
scalar bump, PR #472 — relevant only to San'layn).

**Results:**

| Cohort | n | Mean bias | RMSE | Within ±15% |
|---|---:|---:|---:|---:|
| San'layn (all 12) | 12 | +3.2% | 0.125 | 83% |
| San'layn (excl. 2 flagged outliers) | 10 | +2.1% | 0.095 | 90% |
| Deathbringer (re-run today) | 5 | +3.5% | 0.114 | 80% |
| Deathbringer (original doc, 2026-07) | 5 | -0.2%* | 0.152 | — |

*Original doc's mean bias not restated in this pass; RMSE and per-fight
deltas are the comparable figures — see the doc for its own summary framing.

**Answer to the core question:** hero-talent choice explains essentially none
of Blood DK's residual. San'layn and Deathbringer land within 0.3 percentage
points of each other in mean bias, and their RMSEs (0.125 vs 0.114, or 0.095
vs 0.114 excluding San'layn's flagged outliers) are close enough to read as
sampling noise, not a structural gap. This is a real, encouraging signal that
Blood DK's shared mitigation layers (Bone Shield's 180%-of-Strength armor,
Death Strike healing, party DR, etc.) generalize across hero talent, and that
the two hero-talent-specific ledger entries (Rune Carved Plates' 1.5%/stack vs
Blood-Soaked Ground's flat 8%) are each pulling roughly their fair share of
weight.

**Two flagged outliers in the San'layn-12, both real and independently
confirmed, not removed from the corpus:**
- **Player 1** (Murder Row, +16%): armor snapshot 7,533 vs the corpus's normal
  2,299-3,457 band. Confirmed Ossuary (219788, "Bone Shield ≥5 charges") is
  active at the exact fight-start timestamp (verified: the buff window
  starts at exactly the fight's absolute start time) — the same
  armor-snapshot-pollution bug the original
  characterization doc found on 2 of 7 probed fights. This is a **third**
  confirmed instance of a known, still-unfixed pipeline gap (COMBATANT_INFO
  captured while Bone Shield is up inflates the snapshot armor, which the
  model's own +180%-of-Strength Bone Shield credit then double-counts on top
  of) — worth a dedicated follow-up, not fixed in this pass.
- **Player 2** (Voidscar Arena, +16%): only 250 replay events vs 1,000-4,500
  for every other fight in the corpus — almost certainly a short/partial
  pull, high-noise sample.

**F-consistency / LOO-CV: still not feasible, and this pass doesn't change
that.** Both gates (`scripts/measure_run_f.py`, `_run_loo_cv` in
`calibrate_spec_from_logs.py`) require per-hit live armor from local ACL-on
`.txt` combat logs — a structural requirement `calibrate_spec_from_wcl.py`'s
own docstring states explicitly: a WCL-only corpus "can promote a spec to
`characterized` at most... never `calibrated`, until local ACL-on logs
exist." Zero local Blood DK logs of either hero talent exist in-repo today.
Adding more WCL fights, however many, cannot unlock the `calibrated`-tier
machinery.

**Not resolved by this pass:** the armor-snapshot-pollution bug remains
unfixed (now 3 confirmed instances); Blood-Soaked Ground's credit is still
presence-gated the same simplistic way Rune Carved Plates' is (buff-up = full
8% credit, unconditional on actual Death-and-Decay uptime) — ROADMAP item 1
already names this as a known, separate residual this pass didn't attempt to
quantify; and a broader zone-55 scan (to check whether Deathbringer is now
rare in live S2 play — a real, distinct "did the meta shift?" question)
surfaced ~181 Blood DK fight-candidates in the first 50/300 reports scanned
but was not exhaustively hero-talent-classified — an open, uncompleted
side-thread.

**Manifest:** `src/simf/data/calibration_corpora/blood_death_knight_sanlayn_2026_08_30.yaml`
(12 fights, hero_talent: san_layn).

`specs.blood_death_knight.calibration_tier` is **unchanged** (stays
`characterized`) — this is corpus growth, not a promotion.

## Track 2 — Vengeance DH: key-level 4/6/8 sweep

**Verdict: the "cliff" framing does not survive an unconfounded look.** The
shape is a fast, continuous ramp from near-zero at key 2 to ~+30% by key 4-8,
not a discontinuous jump concentrated above key ~10 — and critically, it is
**not dungeon-specific**: it replicates within the same dungeon across key
levels.

**Corpus:** 9 gear-certain VDH fights at the literal key levels 4/6/8 (no
widening), 3 fights per level, each from a distinct report code and distinct
named player (zero shared sessions — the strongest independence this
project's cross-player checks have had), spanning 6 of the S2 pool's 8
dungeons.

| Key level | n | Mean bias | RMSE | Dungeons |
|---|---:|---:|---:|---|
| 2 (prior check, not re-run) | 2 | -6.6% | — | Altar of Fangs (both) |
| **4** | 3 | **+21.1%** | 0.235 | Den of Nalorakk, Murder Row, Kings' Rest |
| **6** | 3 | **+35.2%** | 0.382 | Temple of Sethraliss, Kings' Rest, Murder Row |
| **8** | 3 | **+32.0%** | 0.348 | Altar of Fangs, Den of Nalorakk, The Blinding Vale |
| 12 (prior check) | 3 | +29.9% | — | — |
| 16 (prior check) | 3 | +41.6% | — | — |

Per-fight deltas: kl4 — Player 3 (Den of Nalorakk) +8.7%, Player 4 (Murder Row)
+33.9%, Player 5 (Kings' Rest) +20.8%. kl6 — Player 6 (Temple of Sethraliss)
+14.3%, Player 7 (Kings' Rest) +43.4%, Player 8 (Murder Row) +47.9%. kl8 —
Player 9 (Altar of Fangs) +13.7%, Player 10 (Den of Nalorakk) +46.8%, Player 11
(The Blinding Vale) +35.5%.

**The dungeon-artifact hypothesis is directly falsified by a same-dungeon,
cross-key comparison this sweep was built to enable:**
- **Altar of Fangs** — the single dungeon behind both of the prior check's
  kl2 fights — reappears here at kl8 (Player 9, +13.7%, well under its own
  bucket's mean). Same dungeon, same fixed geometry/mob kit, a much smaller
  bias at a higher key level than the bucket average. If the kl2 reading were
  a dungeon property, Altar of Fangs should stay low at kl8 too; it's the
  *lowest* value in that bucket instead, but still far from the -6.6% seen
  at kl2.
- **Den of Nalorakk**: kl4 (Player 3, +8.7%, the lowest in its bucket) → kl8
  (Player 10, +46.8%, the highest in its bucket) — a +38pp swing on the
  identical dungeon as key level rises.
- **Murder Row**: kl4 (Player 4, +33.9%) → kl6 (Player 8, +47.9%) — same
  direction.
- **Kings' Rest**: kl4 (Player 5, +20.8%) → kl6 (Player 7, +43.4%) — same
  direction.

Every dungeon appearing at two different key levels in this 9-fight set moves
the same way: higher key level, higher bias. Four independent within-dungeon
comparisons, all pointing the same direction — consistent with a **key-level-
scaling effect** (the residual proportional to magic damage taken, which
itself scales with key level/pull intensity) rather than a **dungeon-identity
effect**.

**Caveats:** n=3 per bucket is thin (kl4 alone spans 8.7% to 33.9% across 3
fights); this sweep uses *different players* at each key level per dungeon
pairing, so some of each within-dungeon swing could be gear/skill variance
rather than pure key-level scaling — the consistency across four independent
pairs, all the same direction, is the strongest available mitigant against
that read, but it isn't a controlled experiment. Key level 2 itself is still
only 2 fights (both Altar of Fangs, from the prior check) — that specific
dungeon-diversity gap is still open; this sweep only fills 4/6/8.
iterations=50 (the tool's default) — adequate for a bias/RMSE read, not for
re-deriving K.

**Recommended framing update:** revise "roughly constant 17-43pp" and the
interim "cliff above key ~10" language (CONTRIBUTING.md's "Carried forward" section)
to: *residual is near-zero at key 2, rises sharply by key 4-6, and plateaus
around +30-45% from key ~4 through at least key 16 — the trend replicates
within-dungeon across four separate dungeons, arguing against a dungeon-
specific artifact and for a key-level/damage-intensity-scaling explanation.*
No constant or tier changes — held per the 2026-07-26 maintainer direction.

**Manifest:** `src/simf/data/calibration_corpora/vengeance_demon_hunter_keylevel_4_6_8_sweep_2026_08_30.yaml`.

## Track 3 — Prot Warrior: low-key (+2 to +5) sweep

**Bottom line: the -27.9% key-2 finding does not hold up at ~2x the sample
and ~2x the dungeon count** — most of its magnitude looks explainable by
small-n/dungeon-concentration noise, though the picture below key 2 (kl3/4)
raises a new, unresolved question rather than closing the file.

**Corpus:** 8 gear-certain Prot Warrior fights at key levels 2-4 (3 of the 8
— Player 12, Player 13, Player 14 — reused verbatim from the prior check's
`target_key_level: 2` bucket; this manifest extends rather than replaces it).
Dungeon spread improved but not solved: 4 distinct dungeons across 8 fights
(Voidscar Arena ×3, Kings' Rest ×3, Temple of Sethraliss ×1, The Blinding
Vale ×1) — better than the original bucket's 67% single-dungeon
concentration, but still 75% of this corpus sits in just 2 of the 8 S2
dungeons, a disclosed limitation.

| Key level | n | Mean bias | RMSE |
|---|---:|---:|---:|
| **2** (expanded from prior n=3) | 6 | **-12.8%** | 0.278 |
| 3 | 1 | -34.7% | — |
| 4 | 1 | -40.5% | — |
| Overall (2-4) | 8 | -19.0% | 0.306 |

Per-fight deltas: Player 15 (Kings' Rest, kl2) +41.7%, Player 16 (Temple of
Sethraliss, kl2) -25.6%, Player 17 (The Blinding Vale, kl2) -26.8%, Player 12
(Voidscar Arena, kl2) -21.3%, Player 14 (Voidscar Arena, kl2) -27.8%,
Player 18 (Voidscar Arena, kl2) -17.1%, Player 13 (Kings' Rest, kl3) -34.7%, Player 19
(Kings' Rest, kl4) -40.5%.

**Does -27.9% replicate?** Largely no, at kl2 specifically. Expanding kl2
from n=3 (2/3 concentrated in Voidscar Arena) to n=6 (4 distinct dungeons)
roughly **halves the magnitude** (-27.9% → -12.8%) and introduces a large
positive outlier (Player 15, +41.7%) the original 3-fight sample never surfaced.
The improved kl2 mean (-12.8%) now sits close to the same check's kl8
(-10.9%) and kl12 (-11.5%) buckets — consistent with one continuous
bias-vs-key-level curve (strongly negative at the very lowest keys, fading
toward the +11.0%/+3.7% seen at keys 16-22) rather than a distinct low-key
crater. That reading would mean the original "distinct low-key phenomenon"
framing was itself mostly an n=3 sampling artifact.

**But this isn't fully settled:** kl3 (-34.7%) and kl4 (-40.5%) — each a
single fight — are both *considerably more negative* than kl2's own
now-larger mean, which doesn't fit a clean monotonic climb toward zero across
2-5. A single fight per bucket cannot distinguish "kl3/4 are a real dip" from
"kl3/4 are single-pull noise" — this project has repeatedly seen individual
fights swing >25 points (e.g. the same-day check's own Warrior kl8 bucket
spanning 26 points across 3 fights).

**A real search-technique finding, worth folding into the discovery memory:**
WCL's `reportData.reports(zoneID)` enforces a hard server-side cap of 25 as
the maximum allowed `page` parameter. The windowless scan used by this and
prior checks hits that cap at ~360 unique reports (with some page-to-page
duplication from live-feed drift). A second, time-windowed scan
(`startTime`/`endTime` slicing across the full season) was started to route
around the cap and chase a kl5 example, but hadn't produced results by the
time this pass was finalized — an honest incompleteness (no kl5 data exists
in this manifest), not a fabricated null result.

**Recommendation:** treat kl2's revised, smaller bias as a real update to the
corpus (worth folding into future Warrior characterization discussion), but
treat kl3-5 as a genuinely open question requiring a second, larger discovery
pass (the time-windowed technique above) targeting ≥3 fights each at kl3,
kl4, and kl5 across more of the 8 S2 dungeons — not a basis for promoting or
demoting anything today. No engine/constants/tier change made — Prot
Warrior's further calibration chasing stays held per the 2026-07-26
maintainer direction; this is characterization only.

**Manifest:** `src/simf/data/calibration_corpora/protection_warrior_lowkey_2to4_2026_08_30.yaml`.

## What this does NOT justify

Per `docs/calibration.md`'s standing discipline, nothing here moves any
spec's `calibration_tier` or any engine constant:

- **Not a Blood DK promotion.** Corpus growth + a same-hero-talent-doesn't-
  matter-much signal, not F-consistency/LOO-CV (still infeasible on a
  WCL-only corpus).
- **Not a VDH engine fix.** The magic-DR gap's documented *shape* changes
  (ramp-then-plateau, not a late cliff); the gap itself stays held per
  2026-07-26 direction.
- **Not a Warrior engine fix or a corpus replacement.** The kl2 bucket's
  reading changes; kl3-5 remain open; further calibration chasing beyond
  named coverage gaps stays held per the same direction.

## Reproduce

```bash
python scripts/cross_player_validation.py \
  --manifest src/simf/data/calibration_corpora/vengeance_demon_hunter_keylevel_4_6_8_sweep_2026_08_30.yaml \
  --wcl-cache-dir examples/wcl_cache

python scripts/cross_player_validation.py \
  --manifest src/simf/data/calibration_corpora/protection_warrior_lowkey_2to4_2026_08_30.yaml \
  --wcl-cache-dir examples/wcl_cache

# Blood DK hero-talent classification (repeat per fight in the sanlayn manifest):
python -c "from simf.io import wcl_api; from pathlib import Path; t=wcl_api._get_token(); \
r=wcl_api.fetch_report('<CODE>', t, cache_dir=Path('examples/wcl_cache')); \
f=next(x for x in r.fights if x.id==<FIGHT>); \
w=wcl_api.fetch_buff_windows(r, f, <SOURCE>, t, ability_ids={434034,440289,440290}); \
print('SANLAYN' if w.get(434034) else ('DEATHBRINGER' if (w.get(440289) or w.get(440290)) else 'AMBIGUOUS'))"

# Blood DK bias comparison (San'layn cohort, then re-run the 5 Deathbringer
# fights from phase4_blood_dk_characterization_2026_07_03.md the same way):
python scripts/calibrate_spec_from_wcl.py blood_death_knight \
  --fight <CODE>:<FIGHT>:<SOURCE>:Player20 --fight <CODE>:<FIGHT>:<SOURCE>:Player21 \
  --fight <CODE>:<FIGHT>:<SOURCE>:Player22 --fight <CODE>:<FIGHT>:<SOURCE>:Player23 \
  --fight <CODE>:<FIGHT>:<SOURCE>:Player1 --iters 300 --ks 3430
```
