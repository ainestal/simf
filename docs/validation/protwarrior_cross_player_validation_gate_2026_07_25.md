# Cross-player validation gate — 6th `characterized` → `calibrated` promotion criterion (2026-07-25)

**Status: tooling shipped, thresholds human-ratified, wired into the promotion bar.**
No spec is re-promoted by this doc alone — this establishes the gate itself; a
spec still needs its own independent-player corpus run through it before any
tier bump.

## Why this exists

Prot Warrior was promoted `characterized` → `calibrated` on 2026-07-22
(`docs/validation/protwarrior_loo_cv_vanguard_2026_07_22.md`), clearing every
criterion the promotion bar named at the time: ≥8 F-consistent runs, |mean
bias| ≤5%, ≥75% within ±15%, RMSE ≤0.15, and a passing LOO-CV gate. All five
were measured against the same corpus — Brutoh's own 16 logs.

Three days later (`docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md`),
the exact same bar was applied for the first time to real data from anyone
*other than* Brutoh — 15 independent Prot Warrior players found via WCL's
public `characterRankings` — and failed outright: mean bias +11.0% (bar ≤5%),
67% within ±15% (bar ≥75%), RMSE 0.150 (bar ≤0.15, exactly on the line). Two
rival explanations (a WCL-hydrate data-quality artifact; a key-level-driven
effect) were checked and ruled out — see that doc for the full analysis. The
remaining, load-bearing explanation: the ratified calibration is real and
correct for Brutoh specifically, and does not transfer to other players' gear,
builds, and parties.

This was a real gap in the promotion *process*, not just the one result — a
LOO-CV gate that only ever holds out runs from the SAME player's corpus can
never catch "this doesn't generalize to other players," because it never sees
another player. Guardian Druid's two promotions/downgrades had the identical
blind spot (its own corpus was also single-player). This doc names the
permanent fix: a cross-player check added to the bar itself, so the next
promotion — Prot Warrior's or any other spec's — is checked against this
before anyone claims `calibrated` again.

## What was built

- **`src/simf/cli.py::_run_k_sweep`** gained two purely-additive, off-by-default
  params: `return_result=True` (returns `{best_k, best_rmse, deltas, labels}`
  instead of `None`) and `skip_loo_cv=True` (omits the LOO-CV section — not
  meaningful when "replays" are different players, not repeated runs of one
  player). Every existing caller is untouched; both default to prior behavior.
- **`scripts/cross_player_validation.py`** — consumes a manifest matching
  `src/simf/data/calibration_corpora/prot_warrior_independent_wcl_2026_07_25.yaml`'s
  schema (a `fights:` list of `player`/`report_code`/`fight_id`/`acl` entries),
  reuses `_run_k_sweep` for the actual sim/delta math — the SAME code path
  `calibrate-k --wcl-url` uses, not a re-derivation — and reports mean bias /
  RMSE / %-within-±15% against the ratified gate below. `acl: false` entries
  (no gear-certain hydrate possible) are excluded automatically.

## A real bug found and fixed before this could be trusted

The script's first committed version (`923072f`, WIP) called
`wcl_to_replay_data(code, fight_id, player, cache_dir=cache_dir)` **without**
`buff_ability_ids=`. This silently zeroes any window-gated mitigation credit
(KYFOTG, or any future spec's Painbringer/Metamorphosis-style mechanic) for
every fight in the corpus — the exact same bug class found and fixed twice
before in this project (VDH's `--wcl-url` CLI path originally missing it; this
same branch's local-log path fix earlier). Confirmed empirically, not just by
code inspection: an early smoke test of the same underlying call (also missing
`buff_ability_ids`) gave AnonPlayerX4 +14.0%/Player 3 +5.7%, vs. the correct,
KYFOTG-credited +7.3%/−0.5% recorded in the ratified manifest (measured via
the CLI's own `--wcl-url` path, which does compute and pass
`buff_ability_ids`).

Fixed by mirroring `cli.py`'s own `--wcl-url` branch: for each fight's
character, compute `spec_cfg = load_constants()["specs"][char.class_spec]` and
`buff_ids = {int(v) for k, v in spec_cfg.items() if k.endswith("_spell_id") and v}`,
then pass `buff_ability_ids=buff_ids or None` into `wcl_to_replay_data`. A
regression test (`tests/test_cross_player_validation.py::
test_run_cross_player_validation_passes_real_spec_buff_ids_through`) asserts
this against the REAL `protection_warrior` spec constants (currently Vanguard
spell 71 + KYFOTG spell 438591), not a hand-picked fake set, so it stays
load-bearing if either constant is ever renamed or a third is added.

## Live confirmation (post-fix)

Ran end-to-end against the existing ratified manifest and its on-disk WCL
cache (no new network fetches needed for the WCL side):

```
python scripts/cross_player_validation.py \
  --manifest src/simf/data/calibration_corpora/prot_warrior_independent_wcl_2026_07_25.yaml \
  --wcl-cache-dir examples/wcl_cache --k 3430 --iterations 50
```

Reproduced the already-known aggregate exactly — a correctness check on the
fixed script itself, not a new finding:

| Metric | Result |
|---|---:|
| n players | 15 |
| mean signed bias | +11.0% |
| within ±15% | 67% (10/15) |
| RMSE (reference) | 0.150 |
| **GATE** | **FAIL** |

Every per-player delta matched the manifest's own recorded `delta_at_k3430`
field exactly (e.g. AnonPlayerX4 +7.3%, Player 1 +23.7%, Player 3 −0.5%, ... — see the
manifest for the full list), confirming the fix produces the same
KYFOTG-credited numbers the manifest was originally populated with via the
CLI path, not a different (and therefore suspect) result.

**An unrelated environmental issue surfaced during this run, worth recording**:
the first live attempt hung for 50+ minutes with no output. Diagnosis found a
genuinely broken IPv6 route from this Pi to Wowhead (used for shield/off-hand
stat resolution when a WCL CombatantInfo event doesn't fully resolve gear
stats) — `curl -6 https://www.wowhead.com/` fails to connect while `curl -4`
succeeds in <1s, and the Python process's socket sat in `SYN-SENT` the entire
time (a black-holed route, not an actively refused one, so the OS-level
connect timeout the code already sets doesn't fire cleanly). Worked around by
forcing IPv4-only DNS/connect (`urllib3.util.connection.allowed_gai_family`)
for this one invocation via a local, uncommitted bootstrap script — not baked
into the shipped script, since this is this Pi's local network condition on
this day, not a code defect to permanently work around. If this recurs on a
future run, the same one-line monkeypatch is the fix; it is not itself part
of this gate's design.

## The ratified thresholds

Presented to the user as four options (proposed-as-is, match-the-single-corpus-bar,
looser, custom) with the exact PASS/FAIL consequence for today's known-bad
Prot Warrior result shown for each. **The proposed-as-is option was chosen**:

```python
MIN_PLAYERS = 5
MAX_MEAN_ABS_BIAS_PCT = 8.0
MIN_WITHIN_15PCT_FRACTION = 0.70
```

Rationale for these specific numbers, not the same-player bar's 5%/75%: a
cross-player corpus is fundamentally noisier per data point — one fight per
player, no repeated same-player runs to average per-pull variance out of the
way Brutoh's 16-log corpus does. Requiring the identical 5%/75% bar would
conflate "doesn't generalize" with "cross-player samples are inherently
noisier," making the gate too easy to fail for a spec that actually does
generalize reasonably well. 8%/70% is looser without being toothless — it was
explicitly checked against today's known-bad result before being chosen
(the whole point of the exercise): Prot Warrior's current +11.0%/67% still
fails on BOTH numeric criteria under this bar, so the threshold is not so loose
that the exact generalization gap it exists to catch would slip through.

`MIN_PLAYERS = 5` is a floor on statistical meaningfulness, not tuned to
today's 15-player sample — fewer than 5 independent players makes a mean
bias/percentage figure too noisy to trust as a promotion criterion either way.

## How this wires into the promotion bar

`characterized` → `calibrated` now requires ALL of the pre-existing four
criteria (≥8 F-consistent runs, |mean bias| ≤5%, ≥75% within ±15%, RMSE ≤0.15,
LOO-CV pass — all same-player) **plus** this 6th: ≥5 independent players' WCL
fights run through `scripts/cross_player_validation.py`, with |mean signed
bias| ≤8% and ≥70% of players within ±15%. See `core/constants.py`'s
`CALIBRATION_TIERS` docstring and `docs/calibration.md`'s `calibration_tier`
section for the authoritative, always-current statement of this bar — this
doc is the historical record of why the 6th criterion exists and how its
numbers were chosen, not the place to look for the current bar if it's ever
revised again.

This gate does not itself re-promote or demote any spec — it is checked
manually per promotion decision, the same as every other criterion in this
list (`spec_is_calibrated()` reads only `calibration_tier`, a human-set field;
no code path evaluates or enforces the promotion bar automatically).

## What this does NOT do

- **Does not restore Prot Warrior to `calibrated`.** The 15-player corpus
  already checked here fails the gate it exists to define — Prot Warrior
  needs either a materially different independent-player result (unlikely
  without an engine fix that closes the generalization gap itself) or the
  underlying gap to be understood and fixed before re-promotion is even a
  live question.
- **Does not identify WHY the gap exists.** The downgrade doc ruled out two
  candidate explanations (hydrate quality, key-level effect) but the
  remaining explanation — "the ratified constants are real but
  Brutoh-specific" — is not itself a mechanism; no engine change follows
  from this doc.
- **Does not run LOO-CV cross-player.** `skip_loo_cv=True` is intentional —
  leave-one-out only tests same-corpus generalization; a cross-player LOO-CV
  variant (leave-one-PLAYER-out) is a plausible future extension, not
  something this gate currently does.

## Files

- `src/simf/cli.py` — `_run_k_sweep`'s `return_result`/`skip_loo_cv` params.
- `scripts/cross_player_validation.py` — the gate script itself.
- `tests/test_cli_run_k_sweep_return_result.py`,
  `tests/test_cross_player_validation.py` — unit tests for both.
- `src/simf/core/constants.py` — `CALIBRATION_TIERS` docstring, 6th criterion.
- `docs/calibration.md` — `calibration_tier` section, 6th criterion + the
  2026-07-25 downgrade folded into the tier-history narrative.
- `src/simf/data/calibration_corpora/prot_warrior_independent_wcl_2026_07_25.yaml`
  — the 15-player corpus this gate was measured against.
- `docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md` — the
  downgrade this gate is a direct response to.
- `docs/validation/protwarrior_independent_players_2026_07_25.md` /
  `protwarrior_independent_players_lowkey_2026_07_25.md` — the raw
  independent-player data.
