# Prot Warrior independent players, round 2 — targeting Brutoh's own key range (2026-07-25)

**Status: measurement only. No engine/constants change, no calibration_tier change.**
Follow-up to `docs/validation/protwarrior_independent_players_2026_07_25.md`, which found
+13.0% mean bias across 7 independent players at +20-22 — the opposite direction from the
KYFOTG-on-Brutoh regression, and unexplained by two competing hypotheses (Brutoh-corpus
overfit vs. a real high-key-specific gap). This round tries to distinguish them by testing
key levels closer to Brutoh's own ratified range (+10-17).

## A real constraint found along the way

WCL's `characterRankings` index for Mythic+ does not go as low as hoped. Probing page depth
against Algeth'ar Academy: page 5 → key 19, page 12 → key 18, page 20 → key 17, page 22-30 →
empty (index exhausted). All 8 dungeons independently converged on the same floor: **page
~18 is the deepest usable page, and +17-18 is the lowest key level reachable at all** — not
the +12-14 originally wanted. WCL's rankings feature indexes *notable* parses; apparently
nothing below roughly +17 registers as notable enough to be indexed, regardless of how deep
you page. Getting genuine +12-14 independent data would need a different discovery method
entirely (e.g. a specific guild/character already known by name — `reportData.reports`
requires a `guildID`/`guildName`/`userID`, it has no general "browse this zone" mode).

+17-18 still meaningfully overlaps Brutoh's own tested range (his ratified corpus tops out
at +17), so this is a real, useful comparison point even though it isn't the low end
originally requested.

## Method

Same as round 1: `characterRankings` (zone 47, all 8 dungeons), EU/US, deduped to distinct
players not already used, one per dungeon, page ~18 instead of page 1. Ran each through
`calibrate-k --wcl-url` on `calibration/protwarrior-kyfotg-magic-dr` (KYFOTG live), K=3430,
iterations=50. Confirmed KYFOTG present in all 8 (same check as round 1, not repeated in
detail here). All 8 had ACL on this time (round 1 had one ACL-off exclusion).

This round also used the new opt-in WCL cache (`io.wcl_api._gql`'s `cache_dir`, shipped
same day on this branch) via the new `--wcl-cache-dir` CLI flag. The round-1 (+20-22) batch
was originally run before this cache existed; it was re-run afterward with
`--wcl-cache-dir` so all 15 fights end up cached, not just this round's 8 — the re-run
reproduced every one of round 1's deltas exactly (a free correctness check on the cache
itself). All 15 fights' raw WCL data now live at `examples/wcl_cache/` (48 files, 56MB).

## Results (K=3430, KYFOTG live)

| Player | Dungeon | Key | Delta |
|---|---|---:|---:|
| Player 1 | Algeth'ar Academy | +18 | +8.4% |
| Player 2 | Magisters' Terrace | +18 | +26.0% |
| Player 3 | Maisara Caverns | +18 | −0.5% |
| Player 4 | Nexus-Point Xenas | +17 | +21.9% |
| Player 5 | Pit of Saron | +18 | +6.0% |
| Player 6 | Seat of the Triumvirate | +18 | +11.4% |
| Player 7 | Skyreach | +18 | +1.9% |
| Player 8 | Windrunner Spire | +18 | −0.9% |

**Mean: +9.3%** (n=8, all ACL-certain), range −0.9% to +26.0%.

## Combined picture across both rounds

| Batch | n | Key range | Mean bias |
|---|---:|---|---:|
| Ratified Brutoh corpus | 16 | +10-17 | **+1.8%** (no KYFOTG) / **−4.4%** (KYFOTG live) |
| Independent, +17-18 | 8 | +17-18 | **+9.3%** (KYFOTG live) |
| Independent, +20-22 | 7 | +20-22 | **+13.0%** (KYFOTG live) |
| Independent, combined | 15 | +17-22 | **+11.0%** (KYFOTG live) |

## Interpretation

**Both hypotheses turn out to have some truth, but one dominates.** There IS a real,
mild key-level trend among independent players: bias rises from +9.3% at +17-18 to +13.0%
at +20-22, a ~3.7pp increase over roughly 3-4 key levels. That's real signal, not nothing —
but it's a small fraction of the total gap.

The much bigger, load-bearing fact: **even at +17-18 — squarely inside Brutoh's own tested
+10-17 range — independent players still show +9.3% mean bias, vs. Brutoh's own −4.4%
(with KYFOTG) or +1.8% (without) at the same K.** A genuine, spec-wide, key-level-driven gap
would predict near-zero bias for independent players at key levels Brutoh's own corpus
already fits well. That's not what happened. The dominant explanation remains: **the
ratified calibration (K=3430, Vanguard's 0.70 coefficient, the Shield Block fixes) is tuned
specifically to Brutoh and does not transfer to other players' gear/builds/parties, even at
matched key levels.** The key-level effect is real but secondary.

## What this means, concretely

- The ratified `calibrated` tier's headline numbers (RMSE 0.073, bias +1.8%, LOO-CV 16/16)
  are an honest description of how well the model fits Brutoh specifically. They should not
  be read as "Prot Warrior in general is calibrated to within 2%" — on this evidence, a
  random other Prot Warrior's log would more plausibly land around +9-13% over-predicted.
- This is a single-player-corpus overfitting risk this project has flagged as a caveat
  before (every one of Prot Warrior's ratified docs notes "single-player corpus") but never
  previously measured. This measures it for the first time.
- KYFOTG's own effect (real, ~8%, reconfirmed across both batches via presence-check) is
  small relative to this cross-player gap. Whatever is driving the +9-13% independent-player
  bias is a bigger, still-unidentified factor — likely something Brutoh's own gear/build/
  party composition happens to sit close to correctly, that other players' don't.

## Caveats

- Single fight per player, same as round 1 — real per-pull noise not fully averaged out.
- All 15 independent players are drawn from WCL's "notable parse" index, which by
  construction selects unusually competent/optimized play (good routing, few deaths,
  efficient kills) — if anything this should bias TOWARD looking more like a well-piloted
  Brutoh, not away from it, so it doesn't obviously explain the gap away.
- No attempt was made to control for talent build differences beyond the ~13 modeled
  talents (reliably decoded per player, see round 1's verification) — a real difference in
  an unmodeled talent between Brutoh and these 15 players remains a live, unexplored
  candidate explanation for part of the gap.

## Recommendation (for the user, not decided here)

The evidence base is now big enough (16 ratified + 15 independent = 31 real fights) to treat
this as a genuine, load-bearing finding rather than noise. Before any further KYFOTG
decision, it may be worth deciding how to treat Prot Warrior's `calibrated` tier given it
has now been shown not to generalize — options include re-scoping the tier's meaning
("calibrated to Brutoh" vs. "calibrated to Prot Warrior generally"), seeking a genuinely
independent LOCAL-log corpus (a second player's raw combat logs, not just WCL spot-checks)
to actually re-calibrate against, or explicitly documenting the single-player caveat more
prominently in the UI than it is today.

## Files produced

- `src/simf/data/calibration_corpora/prot_warrior_independent_wcl_2026_07_25.yaml` — full
  record of all 15 fights (both batches), report codes, fight IDs, deltas, ACL/KYFOTG
  status. NOT the ratified manifest — a diagnostic reference for reuse.
- `examples/wcl_cache/` — raw WCL API responses for every fight above, gitignored,
  reusable via `--wcl-cache-dir examples/wcl_cache` without re-hitting the network.
- `io/wcl_api.py`'s opt-in `_gql` cache (`cache_dir` parameter) and `cli.py`'s
  `--wcl-cache-dir` flag — new, general-purpose, reusable capability, not specific to this
  investigation.
- This doc.
