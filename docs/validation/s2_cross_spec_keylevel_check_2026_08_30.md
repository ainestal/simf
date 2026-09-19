# Cross-spec Season 2 key-level accuracy spot-check (2026-08-30)

**Status: measurement only. No engine/constants change, no `calibration_tier` change.**
One real bug fix shipped alongside this (an IO-layer WCL caching gap, see below) — it
does not touch mitigation math, K, or any spec's calibration constants.

## Why this run

Season 2 ("Curse of Ula'tek", patch 12.1.0) M+ keystones started 2026-08-18 — 12 days
before this check. The maintainer asked whether the engine is still tuned correctly now
that real S2 play exists, specifically: pull 3 real WCL logs per tank spec per key level
(2, 8, 12, 16) and check whether simf's predictions hold up. This extends the
[2026-08-14 cross-spec check](cross_spec_accuracy_check_2026_08_14.md) (5 fights/spec,
key levels uncontrolled +6..+22, all on Season 1 content) along two new axes: live
Season 2 content, and deliberate key-level bucketing to test whether bias scales with
key depth rather than just spec.

## Method

**Zone 55 is confirmed live.** In-repo comments as recent as 2026-08-16
(`wcl_rankings.py`) call WCL's zone 55 ("Mythic+ Season 2") not-yet-live / PTR-only —
true when written (2026-08-13, five days before S2 keystones even started), stale now.
Queried directly this session: `worldData.zone(id: 55)` returns the correct 8-dungeon S2
pool, and `reportData.reports(zoneID: 55)` returns real reports with today's timestamps
and real player/guild names. This is the first calibration work to use zone 55.

**Discovery** used the same technique as 2026-08-14 (same discovery path
documented inline in `cross_spec_accuracy_check_2026_08_14.md`, line 26) —
`reportData.reports(zoneID,
page)`, a global recent-reports feed that (unlike `characterRankings`) doesn't redact
codes — pointed at zone 55 instead of 47. For each of 6 specs, scanned up to 500 reports
(several specs hit that cap) looking for a keyed fight at each target key level (2, 8,
12, 16), confirmed gear-certain via `CombatantInfo` (`character_from_wcl`) and non-empty
via `wcl_to_replay_data`, targeting 3 independent players per bucket. Buckets allowed
widening to ±2 key levels (±1 for the "2" bucket, since there is no lower key) only after
150 reports without an exact match, always recorded per-fight.

**Validation** ran each spec's fights through `scripts/cross_player_validation.py`
unmodified — the same tool + gate as every prior cross-player check — at K=3430,
iterations=50, seed=42, `healing_profile=m+_high_key_healer`. Manifests are committed at
`src/simf/data/calibration_corpora/<spec>_s2_keylevel_2026_08_30.yaml`.

**Bug found and fixed during this run:** running validation for all 6 specs
concurrently against zone 55 hit `429 Too Many Requests` on Blood DK and Vengeance DH,
even with `--wcl-cache-dir` set. Root cause: `wcl_api._fetch_actor_id` — used internally
whenever a spec's window-gated buffs need an actor ID — never accepted or forwarded
`cache_dir` to its `_gql` call, unlike every sibling WCL-fetch function, so it always hit
the live API regardless of the cache flag. Fixed (`_fetch_actor_id` now accepts and
forwards `cache_dir`; all 3 call sites in `wcl_api.py`/`wcl_replay.py`/`wcl_bridge.py`
updated), covered by a new test
(`test_fetch_actor_id_with_cache_dir_hits_network_once`), and both specs' validation runs
then completed cleanly, sequentially, with no further errors. This is an IO-layer fix,
unrelated to any spec's combat math.

## Results

| Spec | n | Mean signed bias | RMSE | Within ±15% | Gate |
|---|---:|---:|---:|---:|---|
| Blood DK | 12 | **+3.2%** | 0.125 | 83% | **PASS** |
| Prot Paladin | 12 | **+10.3%** | 0.134 | 67% | FAIL (bias) |
| Guardian Druid | 10 | **−9.9%** | 0.199 | 40% | FAIL |
| Prot Warrior | 12 | **−11.6%** | 0.228 | 42% | FAIL |
| Brewmaster Monk | 10 | **+26.9%** | 0.281 | 0% | FAIL |
| Vengeance DH | 11 | **+30.7%** | 0.367 | 18% | FAIL |

Gate is the ratified cross-player bar (≥5 players, mean |bias| ≤8%, ≥70% within ±15%).
n<12 for VDH/Brewmaster/Guardian reflects genuinely sparse buckets (see below), not a
search failure — every spec's discovery notes are honest about what wasn't found.

### Per-key-level-bucket bias (mean of that bucket's fights)

| Spec | kl 2 | kl 8 (target) | kl 12 | kl 16 (target) |
|---|---:|---:|---:|---:|
| Prot Warrior | −27.9% (n=3) | −10.9% (n=3) | −11.5% (n=3) | +3.7% (n=3, 1 widened) |
| Prot Paladin | −1.2% (n=3) | +13.4% (n=3, 1 widened) | +9.3% (n=3) | +19.6% (n=3) |
| Blood DK | −6.5% (n=3) | +10.8% (n=3) | +5.8% (n=3) | +2.7% (n=3) |
| Vengeance DH | −6.6% (n=2) | +45.4% (n=3, widened→kl10) | +29.9% (n=3) | +41.6% (n=3, 2 widened) |
| Brewmaster Monk | +34.7% (n=1) | +20.8% (n=3, 2 widened) | +29.3% (n=3) | +28.0% (n=3) |
| Guardian Druid | −21.2% (n=1) | −23.1% (n=3, widened→kl9-10) | −4.2% (n=3) | +1.5% (n=3, 1 widened) |

Per-fight deltas (K=3430, iterations=50, seed=42):

- **Prot Warrior:** Warr Player 1 −21.3, Warr Player 2 −34.7, Warr Player 3 −27.8,
  AnonPlayerX5 −24.4, Warr Player 4 +1.7, Warr Player 5 −9.9, Warr Player 6 +4.2,
  Warr Player 7 −14.7, Warr Player 8 −24.0, Warr Player 9 −11.6, Warr Player 10 +42.5,
  Warr Player 11 −19.7
- **Prot Paladin:** Paladin Player 1 −7.4, Paladin Player 2 +1.9, Paladin Player 3 +1.8,
  Paladin Player 4 +17.1, Paladin Player 5 +14.3, Paladin Player 6 +8.9, Paladin Player 7
  +14.5, Paladin Player 8 +9.5, Paladin Player 9 +3.9, Paladin Player 10 +23.6,
  Paladin Player 11 +15.6, Paladin Player 12 +19.7
- **Blood DK:** BDK Player 1 −6.1, BDK Player 2 −12.2, BDK Player 3 −1.3, BDK Player 4
  +12.3, BDK Player 5 +3.9, BDK Player 6 +16.1, BDK Player 7 −2.0, BDK Player 8 +10.9,
  BDK Player 9 +8.4, BDK Player 10 −9.5, BDK Player 11 +29.2, BDK Player 12 −11.6
- **Vengeance DH:** AnonPlayerX7 −0.5, VDH Player 1 −12.6, VDH Player 2 +37.1,
  VDH Player 3 +40.5, VDH Player 4 +58.6, VDH Player 5 +25.1, VDH Player 6 +31.7,
  VDH Player 7 +32.9, VDH Player 8 +36.3, VDH Player 9 +55.9, VDH Player 10 +32.5
- **Brewmaster Monk:** Brew Player 1 +34.7, Brew Player 2 +26.0, Brew Player 3 +16.3,
  Brew Player 4 +20.0, Brew Player 5 +20.6, Brew Player 6 +25.8, Brew Player 7 +41.4,
  Brew Player 8 +31.0, Brew Player 9 +36.1, Brew Player 10 +17.0
- **Guardian Druid:** Guardian Player 1 −21.2, AnonPlayerX6 −12.7, Guardian Player 2 −34.1,
  Guardian Player 3 −22.6, Guardian Player 4 −11.9, Guardian Player 5 +18.9,
  Guardian Player 6 −19.6, Guardian Player 7 −12.8, Guardian Player 8 +24.2,
  Guardian Player 9 −7.0

All fights are gear-certain (`CombatantInfo`/ACL-on), zone 55 ("Mythic+ Season 2"),
spanning the new 8-dungeon pool.

## Interpretation

**The historical tier ranking does not hold at the top — and the reason matters more
than the reversal itself.** The 2026-08-14 baseline had Prot Warrior (RMSE 0.148) and
Guardian Druid (0.155) as the best fits, Blood DK essentially tied (0.143), then a large
gap to ProtPal/Brewmaster/VDH (0.35–0.57). This run instead ranks Blood DK best (0.125,
the only **PASS** any spec has ever posted on this gate) and Warrior/Guardian mid-pack.
This is largely a sampling-range effect, not a regression: Warrior's worst bucket is
exactly key level 2 (−27.9%), fading to near-zero by key 16 (+3.7%) — Guardian shows the
same shape (−21% to −23% at low keys, +1.5% at key 16). The 2026-08-14 corpus never
sampled below key 6, so it structurally couldn't see this. Blood DK's improvement has no
such confound — its per-bucket bias is flat and small (−6.5% to +10.8%) — and reads as a
more precise measurement of an already-documented small positive bias (Blood DK was
named "closest of the never-calibrated four" well before this run), not a new
improvement or a fluke.

**Vengeance DH's shape is new information, not just confirmation.** The long-documented
"17–43pp magic-DR residual" was framed as roughly constant. This run shows a cliff
instead: near-zero/negative at key 2 (AnonPlayerX7 −0.5%, VDH Player 1 −12.6%, both Altar of Fangs),
then +25% to +59% from key ~10 upward. Two confounds limit the conclusion: both key-2
fights share one dungeon (dungeon-confounded, not proven key-level-general), and the
"key 8" bucket's real average level is 10 — the 2-to-10 range is entirely unsampled. The
residual's documented shape should move from "roughly constant" to "possibly key-level-
dependent, unconfirmed," not further than that.

**Prot Paladin shows the cleanest trend in the batch:** −1.2% (kl2) → +13.4% (kl8) →
+9.3% (kl12) → +19.6% (kl16), every kl16 fight individually over the +15% line. This is
the shape you'd expect from missing defensive-cooldown credit whose impact scales with
pull difficulty (Bulwark of Order, Solace, Avenging Wrath's heal-inflation are all more
load-bearing under stress) — consistent with, and adding a depth dimension to, the
already-named gap.

**Brewmaster stays flat and known** (+20% to +35% across every bucket, no trend) —
the signature of a constant missing-credit bug (Celestial Brew never pressing in
forward-sim) rather than a scaling issue. **Guardian's spread is noise, not a clean
bias** — two large positive outliers (Guardian Player 5 +18.9%, Guardian Player 8 +24.2%) sit inside
otherwise-negative buckets, consistent with a spec whose LOO-CV already fails on
variance rather than a directional miss.

**Tier labels track promotion history, not a live accuracy leaderboard — worth saying
plainly.** Blood DK has never held `calibrated`; it just posted the best result any spec
has posted on this gate, while both specs that have held `calibrated` (Warrior, Guardian)
failed it this round. That doesn't mean today's tiers are wrong — Warrior/Guardian's
`characterized` status was earned honestly, and their miss here is largely sample-range
explained — but `calibration_tier` should be read as "what's been checked and when," not
a standing ranking.

## What this does NOT justify

Per `docs/calibration.md`'s standing discipline, promotion/demotion needs the full
criteria set, not a single spot-check — nothing here moves any spec's `calibration_tier`
or any engine constant. Specifically:

- **Not a Blood DK `calibrated` promotion.** One 12-fight, iterations=50, one-fight-per-
  player spot-check clearing the cross-player gate is real signal, not proof — Blood DK
  still lacks an in-repo corpus diverse enough for F-consistent/LOO-CV checks (the
  Deathbringer-only-build gap is still open). What it does justify: growing Blood DK's
  in-repo corpus with both hero talents is now better-motivated than before.
- **Not an engine fix for Warrior or Guardian.** Both specs' worse aggregate numbers are
  explained by this run's key-level range (which the old baseline never sampled), not
  demonstrated to be a new regression.
- **A scoped VDH follow-up, not a fix attempt:** a deliberate key-level sweep (4/6/8,
  multiple dungeons) to test whether the magic-DR cliff is real or a dungeon artifact,
  before revising the residual's documented shape.
- **A scoped Warrior follow-up:** more low-key (+2 to +5) Warrior fights to establish
  whether the −27.9% key-2 bucket is a distinct new low-key phenomenon or the low tail of
  the already-known ~11% generalization gap, which has never been measured there before.

## Caveats

- n≤3 per bucket; per-fight variance is large (Warrior's kl8 bucket alone spans a
  26-point range across 3 fights). Bucket means are directional, not statistically
  established, at this sample size.
- Key level 2 is genuinely sparse in live data for several specs (Guardian, Brewmaster:
  n=1; VDH: n=2) — three weeks into the season, the logging population has largely moved
  past trivial keys. This is a real play-pattern finding, not a search failure (each
  spec's discovery scanned up to 500 reports).
- Several buckets contain "widened" fights (real key level up to ±2 from the target,
  clearly marked `exact_match: false` in each manifest) — these inject extra noise into
  a bucket mean and are called out inline above wherever they materially affect a
  headline number (Warrior's Warr Player 10, VDH's "kl8" bucket, Guardian's kl8 bucket).
- `iterations=50` (the tool's default), not the 16-log ratified corpus's deeper settings
  — adequate for a bias/RMSE read, not for re-deriving K.
- Separately, and out of scope for this check: `src/simf/data/dungeons.yaml` still points
  the live app's forward-sim (verdicts/gear-recs) at the Season 1 dungeon catalog — the
  Season 2 catalog remains an inert placeholder pending its own promotion work. This does
  not affect the numbers above, since `cross_player_validation.py` replays real logged
  damage-taken events directly and never touches `dungeons.yaml`. Flagged as a separate,
  already-scoped follow-up per the maintainer's direction (not folded into this pass).

## Reproduce

```bash
for spec in protection_warrior protection_paladin blood_death_knight \
            vengeance_demon_hunter brewmaster_monk guardian_druid; do
  python scripts/cross_player_validation.py \
    --manifest src/simf/data/calibration_corpora/${spec}_s2_keylevel_2026_08_30.yaml \
    --wcl-cache-dir examples/wcl_cache
done
```
