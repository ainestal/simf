# `calibrate-k` corpus-manifest fix — 2026-07-12

Closes the RMSE-drift incident from the same day: the documented Prot
Warrior baseline (16 logs, RMSE 0.068 at K=3430,
`docs/validation/k_calibration_2026_05_18.md`) silently grew to 79
"qualifying" replays and RMSE 0.124, entirely via corpus scope creep — no
engine or constants change.

## What happened

`simf calibrate-k` defaulted `--logs-dir` to `examples` and did a recursive
`Path.rglob("*WoWCombatLog-*.txt")`. As the user's personal combat-log
archive grew — new root-level sessions across May/June/July, plus dedicated
subdirectories for other tanks (`anonguardian1-guardian/` for Guardian Druid,
`bruttah-prot/` for a second Prot Paladin, misc archives in `mai/`/`Logs/`)
— the scan silently absorbed all of it whenever a log happened to contain
the calibration target's name (`Brutoh-Uldum-EU`).

A `calibration-scientist` investigation (see the memory note
`corpus_expansion_rmse_drift_2026_07_12`) measured, via a full-corpus
`calibrate-k` run (79 replays) decomposed into buckets:

| Bucket | n | RMSE | bias | share of total squared error |
|---|---:|---:|---:|---:|
| Frozen 16 (ratified) | 16 | 0.065 | +0.2% | 6% |
| New May-era root logs | 4 | 0.214 | −6.0% | 15% |
| Late-May root logs | 16 | 0.121 | +2.2% | 19% |
| June/July root logs | 35 | 0.135 | +7.2% | 52% |
| Cross-tank subdirs | 8 | 0.108 | +1.8% | 8% |

Re-running today's code against the exact frozen 16-replay set reproduces
**RMSE 0.065, unbiased (+0.2%)** — within noise of the 0.068 headline. K=3430
and the mitigation engine are unaffected; **~79% of the error comes from
post-May logs replayed against the frozen May-2026 calibration character**
(`brutoh-calibration-2026-05.yaml`, pinned to a 2026-05-06 gear snapshot —
its own header already warned "replaying logs against a later gear state
shifts the baselines"). Three compounding defects, all pure corpus-selection
bugs:

1. **Temporal mismatch** (dominant) — June/July logs scored against May gear.
2. **Byte-identical duplicate files** (MD5-verified) existing at both
   `examples/` root and inside another tank's subdirectory, double-counted.
3. **No provenance check** — a target can legitimately take real tanking
   damage inside another character's dedicated corpus directory (e.g. a
   co-tank pull), with no flag that it's outside this corpus's own scope.

The flagged "-38% outlier present at every K" resolved to
`WoWCombatLog-051826_150937.txt[0]`, an untimed partial Algeth'ar Academy +14
fragment — a data-hygiene artifact (AA is also the corpus's worst-modeled
dungeon), not a model signal.

## What shipped

- **`data/calibration_corpora/prot_warrior_2026_05.yaml`** — the ratified
  16-replay Prot Warrior corpus, pinned as an explicit `file[run_index]`
  list (reconstructed exactly from `k_calibration_2026_05_18.md`'s residual
  table, including the one fragment — `051526_210245.txt[1]` — the original
  curation deliberately excluded).
- **`src/simf/io/calibration_corpus.py`** — manifest loader +
  `find_manifest_for_character()` (matches by resolved `character:` path) +
  `filename_embedded_date()` (parses WoW's `MMDDYY` log-filename convention).
- **`calibrate-k` CLI**: now auto-resolves and uses a matching manifest by
  default — plain `simf calibrate-k` loads exactly the ratified 16 replays,
  no directory scanning at all. `--full-scan` opts back into the old
  recursive-scan behavior (for exploring new logs before they earn their own
  manifest); `--corpus-manifest PATH` overrides explicitly. A character with
  no manifest falls back to the scan automatically, with a visible notice —
  this is how any *other* spec's calibration (none of which route through
  this CLI command today; see `scripts/calibrate_spec_from_logs.py`, which
  already does its own `detect_party_roles` check) keeps working unchanged.
- **Hardened opt-in scan** (`--full-scan` / no-manifest fallback):
  content-signature dedup (size + head/tail-64KB hash — catches the
  confirmed byte-identical duplicates without a second full-file hash pass
  on ~1GB logs), a gear-drift warning when a log's filename date is >30 days
  from the calibration character's documented snapshot date, and a
  subdirectory-provenance note flagging any replay sourced from a nested
  directory (potentially another character's own corpus).
- **Partial-run visibility**: partial (`success is None`, log truncated
  mid-run) replays still count toward the headline RMSE for continuity with
  existing ratified corpora, but the sweep output now also prints a second,
  partial-excluding RMSE so a bad fragment can't silently skew the number.

## Verification

- Plain `simf calibrate-k` (no arguments): auto-resolves the new manifest,
  loads exactly 16 replays, **RMSE 0.0652 at K=3430** — matches the ratified
  headline. New visibility line: `2/16 replays are partial/truncated runs;
  RMSE excluding them at K=3430: 0.0613, n=14`.
- `--full-scan` manually verified against the 4 confirmed duplicate pairs
  (dedup fires), a nested-subdirectory file (provenance note fires), and
  logs 43/58 days from the frozen snapshot (gear-drift warning fires).
- 22 new tests (`tests/test_calibration_corpus.py`,
  `tests/test_cli_calibrate_corpus_manifest.py`) — manifest round-trip, the
  shipped manifest's exact 16-entry content (pinned, changing it is now a
  test failure), auto-resolution by character path, dedup/date/provenance/
  partial-visibility behavior via synthetic tmp_path logs (the real
  `examples/` corpus is gitignored and unavailable in CI). Full suite:
  2728 passed, 0 regressions. mypy clean.

## Review round (8-angle code review, before merge)

An 8-angle review (correctness × 3, reuse, simplification, efficiency,
altitude, CONTRIBUTING.md conventions) caught real gaps in the first pass, all
fixed before merge:

- **The manifest path skipped the failed-run/death-downtime filter** the
  `--full-scan` path applied — a manifest entry pointing at a `success=False`
  run would have silently counted toward the RMSE. Fixed by moving the check
  into the shared `_build_replay_entry`, applied uniformly to both paths, plus
  a regression test (`test_manifest_path_still_excludes_a_failed_run`).
- **A narrowed exception scope** meant a division-by-zero or attribute error
  after `load_replay()` succeeded (e.g. a corrupted run object) could crash
  the whole CLI invocation instead of skipping just that one replay — widened
  back to match the original per-run try/except.
- **One malformed manifest could break calibration for every character**, not
  just the one it belongs to — `find_manifest_for_character` scans and parses
  every `*.yaml` in the corpus directory looking for a match; a broken file
  now gets skipped during the search (the actually-matching manifest, once
  found, still parses for real and raises loudly if IT is broken). Regression
  test: `test_malformed_manifest_does_not_break_lookup_for_an_unrelated_character`.
- **Two magic numbers** (30-day date-drift threshold, 65536-byte dedup hash
  chunk size) hardcoded in Python, violating CONTRIBUTING.md's "all constants in
  constants.yaml" rule — moved to `constants.yaml`'s new
  `calibration.corpus_scan` section.
- **`find_manifest_for_character` was called twice** per invocation (once to
  pick the replay source, again inside the scan path for the date-check) —
  now computed once and shared.
- Simplifications applied: a hand-rolled per-file memoization dict replaced
  with `functools.lru_cache`; the RMSE formula (previously duplicated between
  the headline and partial-exclusion figures) extracted into one `_rmse()`
  helper; `best_sims` now captured at comparison time in the sweep loop
  instead of re-derived via a linear search afterward; a defensive `getattr`
  that only existed to accommodate an incomplete test mock was removed in
  favor of fixing the mock (`tests/test_cli_calibrate_wcl_buffs.py`) to carry
  a real `success` field.
- **Efficiency**: manifest entries repeating the same file (the shipped
  manifest lists `WoWCombatLog-051526_210245.txt` three times) no longer
  re-parse it from scratch each time — `parse_challenge_modes` results are
  now cached per file and `load_replay` gained an optional `runs=` parameter
  to accept them (backward compatible — every other caller still parses
  fresh).
- **`--full-scan`'s help text** was corrected: it was written to imply
  manifests play no role at all, but the gear-drift date check intentionally
  still consults a manifest's `character_snapshot_date` if one exists for
  `--character` — the flag bypasses manifest-based replay *selection*, not
  that one hygiene check. Reworded rather than removing the (useful, cheap)
  date check.
- **CONTRIBUTING.md's own quick-start example** (`simf calibrate-k --logs-dir
  examples --k-min 3300 ...`) had its semantics silently changed by this PR —
  it would now use the manifest (ignoring `--logs-dir`) rather than scanning,
  since manifest selection isn't gated on whether `--logs-dir` was explicitly
  passed. Updated to show both the new default and the explicit `--full-scan`
  form.

**Deliberately deferred** (named, not silently dropped — this review round
was already large):
- The subdirectory-provenance check is a shallow path-depth heuristic, not a
  real tanking-role check. `combat_log_roles.detect_party_roles` (which
  `scripts/calibrate_spec_from_logs.py` already uses for exactly this) would
  be the deeper fix, but wiring it in means an extra full-file scan per
  candidate replay on top of the ones this PR already added — a real cost
  worth its own measurement, not a same-session addition.
- `calibrate_k`'s nested-closure structure (now ~6 local functions) is a
  maintainability watch item, not yet at the size that triggered this repo's
  prior app.py/log_view.py/combat_log.py splits.
- Parallelizing the full-scan loop across files (I/O-bound, no cross-file
  dependency until final dedup) would speed up exploring a large unratified
  corpus, but risks the deterministic first-seen-wins dedup ordering and
  wasn't attempted here.

## Explicitly deferred (not this PR)

The 35 genuine June/July Brutoh root-level runs are real, valuable tanking
data — not contamination — but need their own fresh character snapshot (a
July `/simc` export) and their own ratification as a **separate** corpus
(e.g. `prot_warrior_2026_07.yaml`), not folded into the existing 0.068 claim.
No engine, constants, or K-value change of any kind is part of this fix.
