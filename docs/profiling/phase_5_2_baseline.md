# Phase 5.2 baseline profile

Hand-maintained reference document — re-running `make profile` does NOT
overwrite this file. The raw top-20s from each run are written to
`phase_5_2_raw.md` next to this one; update this doc only when the
priority list of hot-path candidates changes meaningfully.

Captured 2026-05-16 on the Raspberry Pi host.

**Inputs**

- character: `brutoh.yaml` (Prot Warrior, brutoh-actual loadout)
- replay log: `WoWCombatLog-050626_153703.txt` — Windrunner Spire +12, 1,913 events, 1298s
- healing profile: `m+_high_key_healer` (baseline_hps scaled to log DTPS × 1.1)

**Wall-clock**

- `run_simulation` (2 000 iterations): **395.67s**
- `optimize_cooldown_plan` (24 candidates × 100 iter): **448.17s**

Re-run via `make profile` or `.venv/bin/python scripts/profile_sim.py`.

---

### `run_simulation` (2 000 iter)

Top 20 by cumulative time. Columns: `ncalls`, `tottime` (excl. sub), `cumtime` (incl. sub), `function`.

```
204502970 function calls (204502345 primitive calls) in 387.079 seconds

   Ordered by: cumulative time
   List reduced from 525 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1   68.886   68.886  387.090  387.090 ~/simf/src/simf/core/runner.py:47(run_simulation)
  3826000   78.602    0.000  126.932    0.000 ~/simf/src/simf/core/mitigation.py:86(apply_mitigation)
  3826000   12.111    0.000   77.096    0.000 ~/simf/src/simf/core/mitigation.py:78(recent_damage_total)
  6498001   38.534    0.000   59.365    0.000 {built-in method builtins.sum}
  3826000   35.359    0.000   54.640    0.000 ~/simf/src/simf/core/policy.py:68(tick)
  3826000   22.539    0.000   30.127    0.000 ~/simf/src/simf/core/policy.py:100(decide)
  3826000   17.408    0.000   17.408    0.000 ~/simf/src/simf/core/mitigation.py:74(trim_recent_damage)
 32700116   16.703    0.000   16.703    0.000 {built-in method builtins.min}
 58176000   16.689    0.000   16.689    0.000 ~/simf/src/simf/core/mitigation.py:80(<genexpr>)
  2668000    5.462    0.000   12.166    0.000 ~/simf/src/simf/core/mitigation.py:82(shield_block_charges_available)
  7654000   10.365    0.000   10.365    0.000 ~/simf/src/simf/core/character.py:58(haste_pct)
  3826000    4.080    0.000    9.405    0.000 ~/simf/src/simf/core/character.py:93(versatility_dr)
 13021144    8.970    0.000    8.970    0.000 {method 'get' of 'dict' objects}
  2140055    4.288    0.000    7.971    0.000 ~/simf/src/simf/core/character.py:149(block_value_pct)
 11045169    7.671    0.000    7.671    0.000 {method 'append' of 'list' objects}
 15534006    7.356    0.000    7.363    0.000 {built-in method builtins.max}
  3316000    6.081    0.000    6.081    0.000 ~/simf/src/simf/core/character.py:46(total_armor)
  3826000    5.325    0.000    5.325    0.000 ~/simf/src/simf/core/character.py:86(versatility_pct)
  4930055    3.993    0.000    3.993    0.000 {method 'random' of '_random.Random' objects}
  2140055    3.683    0.000    3.683    0.000 ~/simf/src/simf/core/character.py:69(mastery_pct)
```

Top 20 by total time (excluding sub-calls).

```
204502970 function calls (204502345 primitive calls) in 387.079 seconds

   Ordered by: internal time
   List reduced from 525 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
  3826000   78.602    0.000  126.932    0.000 ~/simf/src/simf/core/mitigation.py:86(apply_mitigation)
        1   68.886   68.886  387.090  387.090 ~/simf/src/simf/core/runner.py:47(run_simulation)
  6498001   38.534    0.000   59.365    0.000 {built-in method builtins.sum}
  3826000   35.359    0.000   54.640    0.000 ~/simf/src/simf/core/policy.py:68(tick)
  3826000   22.539    0.000   30.127    0.000 ~/simf/src/simf/core/policy.py:100(decide)
  3826000   17.408    0.000   17.408    0.000 ~/simf/src/simf/core/mitigation.py:74(trim_recent_damage)
 32700116   16.703    0.000   16.703    0.000 {built-in method builtins.min}
 58176000   16.689    0.000   16.689    0.000 ~/simf/src/simf/core/mitigation.py:80(<genexpr>)
  3826000   12.111    0.000   77.096    0.000 ~/simf/src/simf/core/mitigation.py:78(recent_damage_total)
  7654000   10.365    0.000   10.365    0.000 ~/simf/src/simf/core/character.py:58(haste_pct)
 13021144    8.970    0.000    8.970    0.000 {method 'get' of 'dict' objects}
 11045169    7.671    0.000    7.671    0.000 {method 'append' of 'list' objects}
 15534006    7.356    0.000    7.363    0.000 {built-in method builtins.max}
  3316000    6.081    0.000    6.081    0.000 ~/simf/src/simf/core/character.py:46(total_armor)
  2668000    5.462    0.000   12.166    0.000 ~/simf/src/simf/core/mitigation.py:82(shield_block_charges_available)
  3826000    5.325    0.000    5.325    0.000 ~/simf/src/simf/core/character.py:86(versatility_pct)
  2140055    4.288    0.000    7.971    0.000 ~/simf/src/simf/core/character.py:149(block_value_pct)
  3826000    4.080    0.000    9.405    0.000 ~/simf/src/simf/core/character.py:93(versatility_dr)
  4930055    3.993    0.000    3.993    0.000 {method 'random' of '_random.Random' objects}
  2140055    3.683    0.000    3.683    0.000 ~/simf/src/simf/core/character.py:69(mastery_pct)
```

---

### `optimize_cooldown_plan` (24 × 100 iter)

Top 20 by cumulative time. Columns: `ncalls`, `tottime` (excl. sub), `cumtime` (incl. sub), `function`.

```
220608139 function calls in 443.654 seconds

   Ordered by: cumulative time
   List reduced from 230 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.049    0.049  443.728  443.728 ~/simf/src/simf/optimizer/cooldown_planner_optimizer.py:88(optimize_cooldown_plan)
       19   69.005    3.632  442.460   23.287 ~/simf/src/simf/core/runner.py:47(run_simulation)
       18    1.175    0.065  432.360   24.020 ~/simf/src/simf/optimizer/cooldown_planner_optimizer.py:241(_evaluate_plan)
  3539050   73.715    0.000  119.741    0.000 ~/simf/src/simf/core/mitigation.py:86(apply_mitigation)
  3539050   11.651    0.000   71.940    0.000 ~/simf/src/simf/core/mitigation.py:78(recent_damage_total)
       19    0.050    0.003   57.573    3.030 ~/simf/src/simf/core/runner.py:291(_aggregate)
  6020077   35.850    0.000   55.666    0.000 {built-in method builtins.sum}
  3539050   33.761    0.000   52.113    0.000 ~/simf/src/simf/core/policy.py:68(tick)
       76   36.606    0.482   38.032    0.500 ~/simf/src/simf/core/metrics.py:142(compute_tmi)
  3539050   21.262    0.000   28.533    0.000 ~/simf/src/simf/core/policy.py:100(decide)
     5550    1.145    0.000   19.425    0.004 ~/simf/src/simf/core/metrics.py:104(compute_window_max)
    11100    9.724    0.001   17.163    0.002 {built-in method numpy.fromiter}
  3443400   12.764    0.000   16.655    0.000 ~/simf/src/simf/core/cooldown_planner.py:162(consume_due_presses)
 30250604   15.871    0.000   15.871    0.000 {built-in method builtins.min}
 53812800   15.818    0.000   15.818    0.000 ~/simf/src/simf/core/mitigation.py:80(<genexpr>)
  3539050   15.756    0.000   15.756    0.000 ~/simf/src/simf/core/mitigation.py:74(trim_recent_damage)
  2467900    5.236    0.000   11.568    0.000 ~/simf/src/simf/core/mitigation.py:82(shield_block_charges_available)
 15234266   10.599    0.000   10.599    0.000 {method 'get' of 'dict' objects}
  7079950    9.862    0.000    9.862    0.000 ~/simf/src/simf/core/character.py:58(haste_pct)
  3539050    3.924    0.000    9.169    0.000 ~/simf/src/simf/core/character.py:93(versatility_dr)
```

Top 20 by total time (excluding sub-calls).

```
220608139 function calls in 443.654 seconds

   Ordered by: internal time
   List reduced from 230 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
  3539050   73.715    0.000  119.741    0.000 ~/simf/src/simf/core/mitigation.py:86(apply_mitigation)
       19   69.005    3.632  442.460   23.287 ~/simf/src/simf/core/runner.py:47(run_simulation)
       76   36.606    0.482   38.032    0.500 ~/simf/src/simf/core/metrics.py:142(compute_tmi)
  6020077   35.850    0.000   55.666    0.000 {built-in method builtins.sum}
  3539050   33.761    0.000   52.113    0.000 ~/simf/src/simf/core/policy.py:68(tick)
  3539050   21.262    0.000   28.533    0.000 ~/simf/src/simf/core/policy.py:100(decide)
 30250604   15.871    0.000   15.871    0.000 {built-in method builtins.min}
 53812800   15.818    0.000   15.818    0.000 ~/simf/src/simf/core/mitigation.py:80(<genexpr>)
  3539050   15.756    0.000   15.756    0.000 ~/simf/src/simf/core/mitigation.py:74(trim_recent_damage)
  3443400   12.764    0.000   16.655    0.000 ~/simf/src/simf/core/cooldown_planner.py:162(consume_due_presses)
  3539050   11.651    0.000   71.940    0.000 ~/simf/src/simf/core/mitigation.py:78(recent_damage_total)
 15234266   10.599    0.000   10.599    0.000 {method 'get' of 'dict' objects}
  7079950    9.862    0.000    9.862    0.000 ~/simf/src/simf/core/character.py:58(haste_pct)
    11100    9.724    0.001   17.163    0.002 {built-in method numpy.fromiter}
 10221080    7.026    0.000    7.026    0.000 {method 'append' of 'list' objects}
 14383873    6.944    0.000    6.952    0.000 {built-in method builtins.max}
  3067300    5.789    0.000    5.789    0.000 ~/simf/src/simf/core/character.py:46(total_armor)
  3539050    5.245    0.000    5.245    0.000 ~/simf/src/simf/core/character.py:86(versatility_pct)
  2467900    5.236    0.000   11.568    0.000 ~/simf/src/simf/core/mitigation.py:82(shield_block_charges_available)
 10617150    4.239    0.000    4.239    0.000 ~/simf/src/simf/core/metrics.py:116(<genexpr>)
```

---

## Findings (read before deciding what to vectorize)

`generate_events` does not appear in either top-20. **The Phase 5.2
roadmap entry as written ("NumPy-vectorize `generate_events`") would
help the synthetic-damage-profile path, but the user-visible blocker
— the cd-plan optimizer — runs in replay mode where the event list is
pre-built from the combat log.** Optimizer wall-clock is dominated by
`run_simulation` itself, not event generation.

Distribution of CPU under cProfile (rough buckets):

| Section | run_simulation | optimize_cooldown_plan |
|---|---:|---:|
| `apply_mitigation` (per-event mit chain) | 33% | 27% |
| `runner.run_simulation` framing (event loop) | 18% | 16% |
| `policy.tick` + `policy.decide` (AM policy) | 15% | 12% |
| `recent_damage_total` (per-event 5s window sum) | 20% | 16% |
| `compute_tmi` + `compute_window_max` (metrics) | — | 13% |
| Character property accessors (haste_pct, total_armor, vers_pct…) | 7% | 6% |
| Other / RNG / builtins | balance | balance |

Notes on each:

- **`recent_damage_total`** re-sums the recent-damage list every call;
  the list is also re-trimmed every call. Both add up to ~20% of
  `run_simulation` time. Making this an incremental rolling sum
  (subtract on trim, add on append) would be a single-file change with
  a clear regression test.
- **`apply_mitigation` 3.5–3.8M calls** in a single profile. The
  per-call constant lookups (`load_constants()['active_mitigation']['shield_block']…`)
  are cached at module level but the dict-walk path is hot. Hoisting
  constants to module locals at the top of the function would remove
  ~10M dict lookups.
- **`compute_tmi`** runs 76 times in the optimizer (once per candidate
  × 4 baselines) at ~0.5s each — 36s total. The optimizer ranks by
  `death_rate`, not TMI; passing `compute_metrics=False` and skipping
  TMI in the optimizer alone would eliminate ~8% of wall-clock.
- **Character properties** are pure functions of immutable character
  state. They are called ~10M times across the run. Pre-computing
  them once into the `MitigationState` at construction would cost ten
  attribute reads instead of ten function calls per event.
- **`generate_events`** does not need vectorization for the replay
  path. If we still want it for synthetic sims, that's a separate,
  smaller win.

## Follow-ups landed

### 1. Skip `compute_tmi` in the cd-plan optimizer — landed 2026-05-17

The optimizer ranks plans by `death_rate` / `p99_5s_window` / `mean_dtps`
and does not read `tmi_*`. A narrow `compute_tmi_metrics` flag on
`run_simulation` zeroes those fields without disabling the metrics the
optimizer actually needs; `_evaluate_plan` passes `False`.

Micro-benchmark on the same WR+12 replay (300 iterations × 4 runs,
no cProfile overhead):

| Mode | Wall-clock | Notes |
|---|---:|---|
| `compute_tmi_metrics=True` (full) | 29.91s | tmi_6 = 147 992 |
| `compute_tmi_metrics=False` (fast) | 25.88s | tmi_6 = 0 |
| **Speedup** | **+13.5%** (-4.03s) | `mean_dtps`, `p99_5s` identical |

Predicted savings from the cProfile share-of-time was ~8%; actual was
13.5%. cProfile overhead is unevenly distributed across NumPy vs Python
code, so the share-of-time understated the wall-clock win here. The
sign and magnitude both match the model — proceeding with subsequent
optimizations.

### 2. Incremental `recent_damage_total` — landed 2026-05-17

`MitigationState.recent_damage_total` rebuilt the recent-damage list
via comprehension and re-summed every call. Two changes:

- `recent_damage_window` is now a `collections.deque`, popped from the
  left by `trim_recent_damage` (O(1) per trimmed entry).
- A running `_recent_damage_sum` is incremented by `add_recent_damage`
  and decremented on trim. Reads return the cached sum directly.

All seven append sites across the spec files (warrior + paladin +
guardian + bdk + vdh + brewmaster ×3) now go through
`state.add_recent_damage(t, d)` so the cache and window stay in
lockstep. A boundary case clears the cache to exactly `0.0` when the
deque empties to prevent float drift.

Micro-benchmark (same WR+12 replay, 300 iters × 4 runs):

| Mode | Pre-rolling-sum | Post-rolling-sum | Delta |
|---|---:|---:|---:|
| full (TMI on) | 29.91s | 26.24s | -12.3% |
| fast (TMI off, optimizer path) | 25.88s | 22.04s | -14.8% |

Cumulative speedup from the original baseline on the optimizer path:
22.04s vs 29.91s = **-26.3%**. `mean_dtps` and `p99_5s_window` are
bit-identical between the two implementations across all 300×4 = 1200
iterations.

Synthetic-mode check (300 iter of `m+_pull_caster`): 3.57s avg, sane
metrics (`mean_dtps=55 062`, `death_rate=0.000`). The rolling-sum
helper is on the same hot path for stat-weight sweeps and talent
search; no regression in that path is expected and none was observed.

### 3. Cache character properties on MitigationState — landed 2026-05-17

`Character.total_armor()`, `versatility_dr()`, `base_dodge/parry/block()`,
`block_value_pct()`, `critical_block_chance()`, and `haste_pct()` are
pure functions of immutable `Character` state. Each was called once per
event in the warrior path of `apply_mitigation` (3.8M times in a
2 000-iter sim) and twice per second-tick in `policy.tick` — all
recomputing the same arithmetic against the same constants.

Pre-compute these once at `MitigationState.__init__` into `cached_*`
fields. `apply_mitigation` (warrior path) and `policy.tick` read the
cached values; the methods on `Character` are unchanged.

Last Stand bumps `state.max_hp`, not `Character`, so the cached values
remain valid for the whole iteration.

Micro-benchmark (same WR+12 replay, 300 iters × 4 runs):

| Mode | Pre-cache | Post-cache | Delta |
|---|---:|---:|---:|
| full (TMI on) | 26.24s | 22.87s | -12.8% |
| fast (TMI off, optimizer path) | 22.04s | 18.44s | -16.3% |

Cumulative speedup on the optimizer path since the Phase 5.2 baseline:
25.88s → 18.44s = **-28.7%**. All three wins compose; `mean_dtps`,
`p99_5s_window`, and TMI parity all preserved across 1 200 iterations.

## Caveats

- Wall-clock under cProfile is **~10–20× slower than no-profile**
  (the memory note says 2000-iter sim runs in ~20s normally; here it
  took 396s under cProfile). Treat the percentages, not the absolute
  seconds, as the signal.
- All numbers are single-core; the runner does not currently use
  multiprocessing. Speedup from `ProcessPoolExecutor` is orthogonal
  to vectorization and would multiply against any single-thread
  improvement.

## Re-running

```
.venv/bin/python scripts/profile_sim.py
```

writes `profiles/*.prof` (binary cProfile dumps; load with
`pstats.Stats('profiles/...prof')`) plus this markdown. After any
change targeting a hot path, re-run and append a follow-up section so
the delta is auditable.
