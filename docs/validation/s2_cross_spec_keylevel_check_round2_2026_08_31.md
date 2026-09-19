# Cross-spec Season 2 key-level accuracy check — round 2 (2026-08-31)

**Status: measurement only. No engine/constants change, no `calibration_tier`
change for any spec.** Two real code bugs were found and fixed alongside this
(both IO-layer WCL caching gaps, described below) — neither touches
mitigation math, K, or any spec's calibration constants.

## Why this run

A repeat of the [2026-08-30 cross-spec key-level check](s2_cross_spec_keylevel_check_2026_08_30.md)
(6 tank specs × 4 target key levels × 3 fights, discovered fresh from WCL
zone 55), one day later, at the maintainer's request: an independent
replication to see whether round 1's numbers were stable signal or
small-sample noise, now with 13+ days of live Season 2 play instead of 12.
Same methodology throughout — 6 parallel `calibration-scientist` agents, one
per spec, `scripts/cross_player_validation.py` at K=3430/iterations=50/
seed=42/`healing_profile=m+_high_key_healer`.

**Deliberate improvements riding along, since they were cheap and closed
known round-1 gaps:** Blood DK fights are hero-talent classified this round
(round 1's corpus turned out to be accidentally 12/12 San'layn — see
[the same-day follow-up doc](s2_calibration_followups_2026_08_30.md)); agents
were told to prefer ≥2 distinct dungeons per bucket where the data allows.

**Verification note:** all 6 specs' `cross_player_validation.py` results were
independently re-run against the committed manifests and reproduce their
reported numbers exactly (including the two that initially failed to
validate — see "Infrastructure findings" below). The Prot Paladin `kill:
false` corpus-hygiene finding was independently confirmed via a direct WCL
query on 3 fights (2 flagged, 1 control).

## Results

| Spec | Round 1 (n, bias, RMSE, %<15) | Round 2 (n, bias, RMSE, %<15) | R1 gate | R2 gate |
|---|---|---|---|---|
| **Blood DK** | 12, +3.2%, 0.125, 83% | 12, **-1.6%**, 0.087, 92% | PASS | **PASS** |
| Prot Warrior | 12, -11.6%, 0.228, 42% | 12, -8.4%, 0.186, 42% | FAIL | FAIL |
| Prot Paladin | 12, +10.3%, 0.134, 67% | 12, +13.7%, 0.192, 58% | FAIL | FAIL |
| Guardian Druid | 10, -9.9%, 0.199, 40% | 12, -21.2%, 0.251, 50% | FAIL | FAIL |
| Brewmaster Monk | 10, +26.9%, 0.281, 0% | 11, +25.0%, 0.273, 27% | FAIL | FAIL |
| Vengeance DH | 11, +30.7%, 0.367, 18% | 12, +30.4%, 0.364, 17% | FAIL | FAIL |

**Blood DK is the only spec to pass this gate — twice, on two fully
independent 12-fight corpora, one day apart.** That's the single most
important result of this round. Every other spec failed both times, several
with striking bias-magnitude stability (VDH: +30.7% → +30.4%; Brewmaster:
+26.9% → +25.0%) that argues these are real, reproducible generalization
gaps rather than round-1 sampling artifacts — not evidence the gaps are
closing, evidence they're *real*.

## Per-spec findings

### Blood Death Knight — PASS again, and hero-talent-diverse this time

Round 2's corpus (7 San'layn, 5 Deathbringer — a real mix, unlike round 1's
unchecked all-San'layn sample) posted mean bias -1.6%, RMSE 0.087, 92% within
±15%. This is a stronger fit than round 1's own PASS (+3.2%/0.125/83%), on a
corpus this time deliberately diverse in hero talent. Two consecutive
independent passes, now covering both hero talents, is real evidence Blood
DK's model generalizes well — still not grounds for a `calibrated` promotion
by itself (F-consistency/LOO-CV remain structurally infeasible on a WCL-only
corpus, per the same-day follow-up doc), but the strongest track record any
spec has posted on this gate.

### Prot Warrior — kl2 confirmed real; kl12 replicates tightly; kl8 not a clean comparison

Three independent kl2 samples now exist (round 1's original n=3: -27.9%;
round 1's own lowkey follow-up n=6: -12.8%; this round's fresh n=3: -18.8%)
— always negative, always outside the ±8% bar. This is no longer plausibly
single-dungeon noise; key level 2 has a real, meaningfully negative bias for
this spec. kl12 replicated tightly across two fully independent samples
(-11.5% → -13.5%, within 2 points). **kl8 is not a like-for-like
replication**: no exact key-8 Warrior fight existed in the scanned window,
so this round's "kl8" bucket is actually kl10 (all 3 fights) — its near-zero
mean (-2.3% vs round 1's -10.9%) should not be read as "the kl8 bias
improved." Overall: mean bias -8.4% (vs -11.6%), RMSE 0.186 (vs 0.228),
within-15% statistically flat (42% vs 42%) — same underlying miss, a
different small sample of the same population.

**Data-quality issues found and handled, not silently absorbed:** one
candidate fight's WCL label didn't match any real Season 2 dungeon name
(excluded as a malformed WCL artifact); one candidate fight failed hydration
with `ValueError: versatility_rating must be >= 0, got -62` — a real
upstream WCL stat-parsing data-quality problem for that specific
player/fight, not a manifest bug (swapped for the next-best candidate,
flagged for calibration-tooling awareness, not fixed here).

### Prot Paladin — bias reproduces, shape doesn't; a real corpus-hygiene bug found

Overall bias reproduces closely (+10.3% → +13.7%, same direction, same order
of magnitude) — real signal that Prot Paladin has a persistent positive-bias
generalization gap. But round 1's own "cleanest trend in the batch"
(monotonic -1.2% → +13.4% → +9.3% → +19.6% with key depth) does **not**
replicate: round 2 shows +14.6% → +25.5% → +1.9% → +12.8%, non-monotonic,
with kl8 and kl12 swapping rank order. In hindsight, round 1's clean curve
reads like it fit n=3-per-bucket noise into a compelling but unproven
narrative — the robust, twice-reproduced finding is "positive bias at every
bucket, roughly 2-25% depending on which," not a clean depth-scaling curve.
One thing IS 2-for-2: kl8 was among the worse buckets both rounds
(+13.4%/+25.5%) — suggestive, not proven, of something under-modeled around
that depth (6 total fights of evidence).

**A real, previously-undisclosed corpus-hygiene bug, found and fixed for
this round, and backported as a disclosure note to round 1's manifest:**
WCL's `completeRaid` field (assumed usable) is uniformly `false` across
every M+ compound fight regardless of real outcome — `kill` is the field
that actually distinguishes a finish from a wipe/abandon. Round 2's first
kl16 pass landed 3 `kill: false` (failed/depleted) fights, caught because
two of their deltas were unusually extreme (+49.1%); re-scanned for
`kill: true` exact matches and replaced all 3. **Round 1's own manifest
(`protection_paladin_s2_keylevel_2026_08_30.yaml`) was independently checked
and confirmed to have the same problem**: 2 of its 12 fights (Player 1
@ kl12, Player 2 @ kl16) are `kill: false`, undisclosed at the time. Neither
delta (+3.9%, +15.6%) was an extreme outlier, so this most likely didn't
materially skew round 1's headline numbers — but it wasn't caught or
disclosed then. A disclosure note is now added to that file; the shipped
numbers are unchanged (not retroactively re-run). **Recommendation for any
future round or spec: filter on `kill: true`, not just gear-certainty and
key-level match, before accepting a fight into a cross-player corpus.**

### Brewmaster Monk — strong replication of a flat, key-level-independent gap

Round 1 (n=10): +26.9%/0.281/0%. Round 2 (n=11, ~91% freshly discovered):
+25.0%/0.273/27%. Two independent samples, different days, largely different
reports/players/dungeons, land within 2 points of each other on both bias
and RMSE — round 1's number was not a fluke. Per-bucket, no bucket flipped
sign and none trended toward zero at either end of the key-level range
(kl2 +34.7%→+19.1%, kl8 +20.8%→+27.9%, kl12 +29.3%→+22.0%, kl16
+28.0%→+29.0%) — a flat, roughly-constant-with-key-level shape, consistent
with a constant per-fight missing-credit bug (the already-named
`brewmaster_celestial_brew_never_presses_geared_forward_sim` lead: Celestial
Brew's `hp<0.70` press gate essentially never firing for a geared tank in
forward-sim) rather than a scaling-with-difficulty one. Now measured twice
against the same conclusion.

### Guardian Druid — bias magnitude roughly doubled; sign consistency newly strong

Round 1: -9.9%/0.199/40% (its own writeup called the spread "noise, not a
clean bias," built partly on 2 of 10 fights landing positive). Round 2 (12
fresh, fully independent fights, zero overlap with round 1): -21.2%/0.251/
50% — bias magnitude roughly doubled, RMSE worsened, but **all 12 fights are
negative, zero positive outliers** — the tightest same-direction run either
round has produced. Pooled across both rounds: 20 of 22 independent fights
negative. This upgrades the characterization from "noisy, direction
uncertain" to "consistently negative, magnitude uncertain and possibly
worse than round 1 suggested" — a real update, not just a confirmation, and
worth the tier-holders' attention even though no tier/constant change is
made here.

### Vengeance DH — near-exact reproduction of an already-large gap

+30.7%/0.367/18% (round 1) vs +30.4%/0.364/17% (round 2) — the tightest
bias/RMSE reproduction of any spec this round (aside from kl2/kl16 bucket
overlap noted below). Strong evidence the magic-DR gap's magnitude is
stable, not sampling noise. **Caveat: this round's kl2 (n=2) and kl16 (n=3)
buckets are 100% reused from round 1** (see "Infrastructure findings" —
discovery was cut short by a rate-limit lockout before fresh fights were
found for those buckets), so they are a replay, not independent confirmation,
of round 1's own numbers for those two buckets specifically. kl8 (4/4 fresh)
and kl12 (2/3 fresh) are genuinely new evidence.

## Infrastructure findings

Running 6 `calibration-scientist` agents concurrently against the same
shared WCL API token — much heavier simultaneous load than any prior
check — surfaced two real, previously-undiscovered bugs and hit two distinct
rate-limit mechanisms.

**Bug found and fixed: `fetch_buff_windows()` and `fetch_player_details()`
never accepted a `cache_dir` parameter at all**, unlike every sibling
WCL-fetch function in `wcl_api.py` (`fetch_report`, `fetch_damage_events`,
`fetch_combatant_info_events`, and `_fetch_actor_id` — the last one fixed
for this exact class of bug on 2026-08-30). Both functions always hit the
live network regardless of `--wcl-cache-dir`. `fetch_buff_windows` is called
internally by `wcl_replay.wcl_to_replay_data()` whenever `buff_ability_ids`
is set — which every cross-player validation run does, for window-gated
mitigation stamping — so every fight in every spec's manifest was paying an
uncacheable live call on top of its (cached) damage-event fetch.
`fetch_player_details` is called once per candidate fight during WCL
discovery scans, compounding exposure on any large scan. Both fixed in
`wcl_api.py` (added `cache_dir: Path | None = None`, forwarded to `_gql`),
with call sites updated in `wcl_replay.py` and `wcl_bridge.py`, and
regression tests added (`test_fetch_buff_windows_threads_cache_dir_to_gql`,
`test_fetch_player_details_with_cache_dir_hits_network_once`).

**Two of six specs (Blood DK, Vengeance DH) hit a Cloudflare edge burst
limiter** (distinct from WCL's hourly points budget, which showed headroom
at the time) during their `cross_player_validation.py` run, most plausibly
from the combined load of 6 agents making the newly-discovered uncached
`fetch_buff_windows()` calls concurrently against one shared token. Both
runs were blocked mid-session with a real `retry-after` cooldown (~37 min).
Rather than fabricate or estimate numbers, both agents reported the blocker
honestly and returned complete, verified manifests with no bias/RMSE
figures. After the cache_dir fix above and the natural cooldown elapsing,
both were re-run successfully — see the Blood DK and VDH results above,
which are real, not estimated.

**A separate, distinct rate limit hit Brewmaster Monk's discovery pass**: a
WCL request-*count* limit (message: "subscribe on Patreon to increase their
request limit"), not the points-based budget. The agent worked around this
in its own scratch discovery script by pacing GraphQL calls (~0.35s between
requests) with 429/backoff retry — a tooling lesson for future large-scan
discovery work, not a product-code fix (no repo file was touched for this).

**Guardian Druid's run also hit a transient 429** from the other 5 agents'
concurrent load on the shared hourly points budget; let it clear via the
natural hourly rollover rather than route around it. Cost time, not
correctness.

**Takeaway for future concurrent multi-spec workflows:** the WCL API token
is shared across every concurrent session hitting it, and this project's own
tooling had two real uncached-call gaps that made 6-way concurrency far more
rate-limit-prone than it needed to be. Both are now fixed; a future 6-spec
concurrent run should be meaningfully less fragile.

## What this does NOT justify

Per `docs/calibration.md`'s standing discipline, nothing here moves any
spec's `calibration_tier` or any engine constant:

- **Not a Blood DK `calibrated` promotion**, despite two consecutive PASSes
  now spanning both hero talents. F-consistency/LOO-CV remain infeasible on
  a WCL-only corpus (needs local ACL-on logs) — this is stronger
  cross-player evidence, not a different kind of evidence.
- **Not evidence any of the other 5 specs' gaps are stable-and-thus-fine.**
  Stable, reproducible bias is still bias — Prot Warrior/Paladin/Brewmaster/
  VDH/Guardian all remain `characterized`/`calibrated: false` (or
  `characterized`/`false` per their existing tier), unaffected.
- **Not a Guardian Druid regression finding requiring urgent action** — the
  larger round-2 bias could reflect real corpus variance at n=12, a
  genuinely worse fit than round 1 happened to sample, or both; distinguishing
  those needs more data, not a code change.
- **Not a retroactive re-scoring of round 1's Prot Paladin numbers** — the
  `kill: false` disclosure is added for transparency; the shipped bias/RMSE
  figures from 2026-08-30 are unchanged.

## Caveats

- n=11-12 total, n=2-3 per bucket for every spec — per-fight variance is
  large throughout (e.g. Prot Warrior's kl8-equivalent bucket spans 46
  points across 3 fights). Bucket means remain directional, not
  statistically established, at this sample size.
- `iterations=50` (the tool's default) — adequate for a bias/RMSE read, not
  for re-deriving K.
- Several buckets across several specs contain widened fights (real key
  level up to ±2 from target) or, for VDH, fights reused verbatim from round
  1 — both are disclosed inline in each manifest and called out above where
  they materially affect a headline number.
- This round's improvements (hero-talent classification, dungeon-diversity
  preference, `kill` filtering) were applied inconsistently across specs
  (only Blood DK was asked to classify hero talent; only Prot Paladin's
  agent happened to catch the `kill: false` issue) — a future round should
  bake all three into the shared discovery technique for every spec, not
  rely on each agent independently rediscovering them.

## Reproduce

```bash
for spec in protection_warrior protection_paladin blood_death_knight \
            vengeance_demon_hunter brewmaster_monk guardian_druid; do
  python scripts/cross_player_validation.py \
    --manifest src/simf/data/calibration_corpora/${spec}_s2_keylevel_2026_08_31.yaml \
    --wcl-cache-dir examples/wcl_cache
done
```
