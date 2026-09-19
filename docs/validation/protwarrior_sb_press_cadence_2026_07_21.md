# Prot Warrior Shield Block press-cadence investigation — SimC's real condition, tested and rejected (2026-07-21)

**Status: investigated, NO code fix shipped, independently validator-confirmed.
`constants_version` unchanged (still 43 — nothing in `constants.yaml` changed
value, only a comment pointer added). `calibration_tier`
(`characterized`) and `calibration.global_rmse` (0.138) untouched, same as
every prior doc in this thread.** This closes the lead named at the end of
`docs/validation/protwarrior_post_shield_block_bias_decomposition_2026_07_21.md`:
*"SimC's real APL bank-ahead press pattern (`remains<=10`) vs simf's reactive
near-expiry trigger — traced and confirmed real but deliberately deferred."*
Read that doc first if you haven't; this one picks up exactly where it left
off.

## Task

Find SimC's exact real Shield Block press condition, characterize precisely
how it differs from `core/policy.py`'s current logic, and determine whether
adopting it would close more of the ratified corpus's remaining +13.7% bias
(RMSE 0.162, `SimResult.mean_sb_uptime` 69.6% vs the corpus's real logged
90.1%).

## SimC's real press condition

Fetched fresh via `curl` (not `WebFetch`, which a prior round in this thread
found truncates mid-file on `sc_warrior.cpp` — checked here too;
`apl_warrior.cpp` is 426 lines, no truncation) from
`https://raw.githubusercontent.com/simulationcraft/simc/fd60a6384dcd55f3737f18f31755141c1fe1e540/engine/class_modules/apl/apl_warrior.cpp`
— the same vendored `midnight`-branch commit this project's other SimC
citations use (`docs/simc-reference/README.md`).

The real default Protection APL lives in `void protection( player_t* p )`
(lines 335-373 of that file) — **not** the separate
`warrior_t::init_blizzard_action_list()` in `sc_warrior.cpp` (a distinct
"Blizzard smart-assist" list that happens to carry the identical line, which
is how the prior doc's citation, sourced from that function, still landed on
the right condition). Line 372, inside the real APL:

```
default_->add_action( "shield_block,if=buff.shield_block.remains<=10" );
```

Exact, no charge-count or rage term in the condition itself — SimC's generic
action-readiness machinery (not this `if=` string) gates on charges/cost
separately. **Vendored confidence: this specific file was live-fetched this
session (not previously vendored under `docs/simc-reference/`), matching the
commit `docs/simc-reference/README.md` already pins — same trust tier as
every other live-fetched citation in this thread, one step below a
long-vendored file.**

Also fetched `engine/buff/buff.cpp` from the same commit. `buff.shield_block`
is a 6s-base-duration buff (`spell.shield_block_buff = find_spell(132404)`)
with **no** duration-hasted flag (only the charge *cooldown* is hasted, via
`shield_block_t`'s constructor — already modeled by the prior fix).
`shield_block_t::execute()` (`sc_warrior.cpp:6881`) calls
`p()->buff.shield_block->extend_duration_or_trigger(p()->buff.shield_block->buff_duration())`.
`buff_t::extend_duration_or_trigger()` (`buff.cpp:2101`): if the buff is up,
calls `extend_duration(d)`, which **adds** `d` (6s) to the buff's *current*
remaining time (`buff.cpp:2069`, `reschedule(remains() + extra_seconds)`),
uncapped; if not up, triggers fresh.

**Given the buff's own base duration is only 6s, `remains<=10` is true across
virtually the entire buff lifetime** — it only goes false once repeated
presses have banked remaining time above 10s via the additive extension
above, a state reachable only by pressing faster than the buff decays. In
other words: SimC's real condition is a *near-no-op* for this ability. The
real throttle on press frequency is the charge/cooldown economy (2 charges,
~13s hasted recharge each at Brutoh's calibration haste), not the `if=`
clause — SimC presses essentially the instant a charge (+ rage) is
available.

## How this differs from simf's logic — and why it matters

`core/policy.py`'s `ActiveMitigationPolicy.decide()` gates a press on:

```python
sb_preconditions = (
    now >= state.shield_block_until - 1.5
    and state.shield_block_charges_available(now) > 0
    and state.rage >= sb["rage_cost"]
)
```

The `-1.5` term withholds a press until the *current* buff is within 1.5s of
expiring — a reactive, "wait until nearly out of coverage" trigger, the
opposite shape from SimC's "press whenever a charge is ready" pattern. The
prior doc's hypothesis (echoed in the task brief for this round) was that
this reactive shape, combined with `decide()` only firing at real
combat-log event times (not a continuous clock), could strand a ready charge
for however long it took the next event to arrive, explaining part of the
69.6%-vs-90.1% gap.

**Checked the event-sparsity part of that hypothesis directly first.**
Across the ratified corpus's replayed events (32,859 gaps measured), the
median inter-event gap is 0.285s and only 10.6% of gaps exceed the current
1.5s buffer (3.0% exceed 3.0s, 1.2% exceed 6.0s). Events are dense enough
that a narrow reactive window is rarely missed outright — this alone cannot
explain a 22pp uptime shortfall (69.6% modeled vs. the ~92% naive
charge-economy ceiling the prior doc derived).

## The experiment: mirror SimC's condition literally, and measure

Implemented the literal mirror in `core/policy.py`: dropped the `-1.5` term
entirely, leaving `sb_preconditions = charges_available>0 and rage>=cost`
(mirroring that `remains<=10` is a near-no-op and the charge economy is the
real gate). Measured via the pre-existing `scripts/_sb_charge_diagnostic.py`
(unmodified) against the ratified 16-log corpus, and via
`simf calibrate-k --logs-dir examples --k-min 3430 --k-max 3430 --k-step 1`
(canonical K, single-K sweep, same manifest, seed=42, 300 iterations):

| Variant | `mean_sb_uptime` | `mean_sb_charge_limited_pct` | calibrate-k RMSE | mean bias |
|---|---:|---:|---:|---:|
| Current master (`-1.5s` reactive gate) | 69.6% | 26.9% | 0.162 | +13.7% |
| Literal SimC mirror (gate removed) | 51.7% | 46.0% | 0.323 | +30.7% |

**This is a large, clear regression, not an improvement.** Removing the
reactive gate made the sim believe Shield Block was down MORE often, not
less, and both the uptime proxy and the authoritative calibrate-k metric
agree on the direction and rough magnitude.

### Characterizing the shape: a buffer sweep

To check whether this was a fluke of the extreme "fully removed" case,
swept the reactive-gate buffer width in both directions (each measured via
the same unmodified diagnostic script, applied as a temporary, uncommitted
edit and reverted after each measurement):

| Buffer (seconds before expiry) | `mean_sb_uptime` |
|---:|---:|
| 0.5 (tighter than shipped) | 71.2% |
| **1.5 (shipped, unchanged)** | **69.6%** |
| 3.0 (wider) | 64.8% |
| removed entirely (= literal SimC mirror) | 51.7% |

A clean **monotonic** trend, in the opposite direction from what a literal
SimC mirror predicts: tighter reactive windows give *higher* uptime; wider
or absent ones give *lower* uptime.

## Root cause

SimC's real mechanic banks remaining buff time **additively**
(`extend_duration_or_trigger`); simf's press line —
`state.shield_block_until = now + sb["duration_s"]` — is an **absolute
reset**, unchanged by this investigation and confirmed unchanged since
before it. Under reset semantics, a press only ever banks up to
`duration_s` (6s) of total coverage — pressing while `remaining` seconds are
still on the clock nets only `duration_s - remaining` of real gain, which is
small whenever `remaining` is large. Pressing "as soon as possible" spends a
charge for that small gain, pulling the charge-recharge clock earlier and
producing a *bigger* coverage gap later once both charges end up
simultaneously recharging — exactly the pattern the rising
`mean_sb_charge_limited_pct` (26.9% → 32.4% → 46.0% across the sweep) shows.
Verified directly against `runner.py`'s own accounting
(`sb_active_in_interval = min(shield_block_until, now) - max(last_t, 0)`),
not just inferred from the aggregate numbers.

## Conclusion — no fix shipped, and why

SimC's real press condition is real, correctly sourced, and precisely
characterized (`remains<=10`, a near-no-op given the buff's 6s duration,
backed by `extend_duration_or_trigger`'s additive semantics). **Adopting it
literally regresses this engine's calibration**, because simf's press-cadence
state machine models Shield Block's remaining-duration bookkeeping as a
reset, not SimC's additive extension — an architectural mismatch, not a
tunable parameter. This is a "confirmed real mechanism, *negative* (not
merely negligible) calibration effect" outcome.

**Deliberately not shipped, even though the buffer sweep's tighter end (0.5s)
empirically outperformed the current 1.5s** (71.2% vs 69.6% uptime,
untested against calibrate-k): there is no SimC citation for any specific
buffer width — 1.5s was itself always an uncited "one GCD" heuristic, and
picking a different uncited number because it fits this one 16-log corpus
better would be exactly the self-fit-constant pattern this project's
calibration norms reject. The current `-1.5s` value stays unchanged, not
because it's provably optimal, but because there is no principled basis
(SimC-sourced or otherwise) to prefer a different number over it, and this
investigation's brief was to check alignment with SimC's real logic, not to
re-tune an unrelated heuristic against the calibration corpus.

**The real fix, if this is ever worth doing:** re-architect
`ActiveMitigationPolicy`'s Shield Block state to track banked/additive
remaining duration (mirroring `extend_duration_or_trigger` properly) instead
of an absolute reset, and re-derive the Phase 2.10 skill-gate Bernoulli
semantics for that model. This is exactly what the prior doc already flagged
as "a bigger, riskier change... needing its own dedicated investigation" —
this session's empirical results confirm that assessment was correct, and it
is **not attempted here**, per this task's own instruction to name further
leads rather than chase them.

## What's still left

The ratified corpus's remaining +13.7% bias (RMSE 0.162) is **not** explained
by SB press cadence — the current reactive-window heuristic already
outperforms every tested alternative in the direction SimC's real logic
would suggest. Whatever remains of the bias sits elsewhere (block-value
magnitude / mastery-base question held in the shield-block-fix doc; the
broader "structural physical-mit gap," roadmap item 5). No new lead is named
by this investigation beyond the additive-banking re-architecture noted
above, which is itself a re-statement of the prior doc's own deferred item,
not a new discovery.

## Independent validator audit

A `validator` agent (engine-math mode) independently re-derived every claim
before this doc was finalized:

- **Re-fetched SimC source directly via `curl`**, confirmed `apl_warrior.cpp`
  is 426 lines with no truncation, confirmed the exact `remains<=10` line at
  the cited location inside the real `protection()` APL function (not only
  the Blizzard-assist list), and confirmed `extend_duration_or_trigger` /
  `extend_duration`'s additive, uncapped, non-duration-hasted semantics
  "verbatim."
- **Audited the diff directly**: confirmed `core/policy.py`'s shipped logic
  is byte-for-byte unchanged except comments, and that the absolute-reset
  line was already there, unchanged, before this session.
- **Independently reproduced both endpoints** of the buffer sweep exactly:
  baseline `mean_sb_uptime=69.6%`/`charge_limited=26.9%`, and — applying the
  literal-mirror edit independently rather than trusting mine —
  `mean_sb_uptime=51.7%`/`charge_limited=46.0%`, plus the full `calibrate-k`
  regression (0.1620 → 0.3227) exactly. Reverted the edit afterward and
  reran `test_sb_diagnosis.py` (9/9 pass) to confirm no residual state.
  **Did not independently reproduce the two interior sweep points** (0.5s →
  71.2%, 3.0s → 64.8%) — those are confirmed only by this session's own
  measurement, not by the validator; given the exact match on both
  endpoints, the validator called the monotonicity claim "highly likely
  correct but not personally confirmed across all four points."
- **Verified the mechanism explanation** directly against `runner.py`'s
  `sb_active_in_interval` accounting (not just the aggregate stats), and
  caught one wording overclaim in the code comment (an early press "gains
  nothing" — the precise statement is it gains `duration_s - remaining`,
  small but not literally zero) — **fixed** in `core/policy.py` before this
  doc was finalized.
- **Audited the new tests**: confirmed the `runner_mod.make_policy`
  monkeypatch resolves correctly at call time, mutation-verified the pinned
  "withholds a press" unit test actually fails if the literal-mirror edit is
  reapplied (not vacuous), and confirmed the `_PressASAPPolicy` test
  subclass's full override of `decide()` (skipping Ignore Pain/Demo
  Shout/Shield Wall/Last Stand/Spell Reflect) does not confound the
  `mean_sb_uptime` comparison, since death doesn't short-circuit the
  per-iteration event loop and `mean_sb_uptime` is computed purely from
  SB-state accounting.
- **Verdict, verbatim**: "CONFIRMED... SimC's real press condition is
  correctly identified and sourced, but a literal implementation of it
  regresses this engine's calibration due to a real reset-vs-additive-
  extension architectural mismatch; correctly not shipped."
- One process gap caught before this doc existed: the code comments and test
  docstring cited this doc's path before the file existed on disk — a dead
  link at review time. **Fixed** by writing this doc.

## Caveats / what could still be wrong

- The two interior buffer-sweep points (0.5s, 3.0s) were measured once each,
  by this session only, not independently re-verified — treat the exact
  percentages as directional, not precision-grade; the endpoints (which
  bound the effect and which the validator did independently reproduce) are
  the load-bearing evidence for the conclusion.
- This investigation did not instrument a per-press event trace; the root-
  cause mechanism is derived from aggregate `mean_sb_charge_limited_pct`
  trends plus a direct read of `runner.py`'s accounting formula, not a
  literal step-by-step replay log.
- `mean_sb_rage_starved_pct` stayed roughly flat (2.0-4.9%) across every
  buffer setting tested — rage is not the binding constraint in any variant,
  consistent with the charge economy (not rage) being the real throttle SimC
  relies on, as reasoned in the "SimC's real press condition" section above.
- As with every prior doc in this thread, `calibration_tier`/`global_rmse`
  are deliberately unchanged — there is nothing here for a human to ratify,
  since no engine behavior changed.

## Files changed

- `src/simf/core/policy.py` — comment only, above `sb_preconditions`,
  documenting this investigation and its conclusion. No logic change.
- `src/simf/data/constants.yaml` — comment only, near the `constants_version:
  43` changelog block. No value changed.
- `tests/test_sb_diagnosis.py` — replaced draft tests (written mid-session,
  before the literal-mirror edit was reverted) with: two small unit tests
  pinning the current `-1.5s` gate's intentional behavior
  (`test_shield_block_reactive_gate_withholds_press_far_from_expiry`,
  `test_shield_block_reactive_gate_presses_once_within_window`), and one
  end-to-end regression guard
  (`test_shield_block_reactive_gate_beats_a_wider_one_on_uptime`) that
  monkeypatches `simf.core.runner.make_policy` to substitute a literal
  SimC-mirror policy and asserts the shipped policy reaches higher
  `mean_sb_uptime` — so a future attempt to "fix" this toward SimC's literal
  condition fails a test immediately instead of silently regressing
  calibration again.
- `docs/validation/protwarrior_sb_press_cadence_2026_07_21.md` — this doc.

## Tests / lint / mypy

Full suite: **2712 passed, 21 skipped** (was 2709 passed pre-this-session;
+3 net — two new small unit tests plus the end-to-end guard, replacing three
draft tests written mid-investigation before the literal-mirror edit was
reverted). `ruff check` + `ruff format --check` clean repo-wide. `mypy src/`
shows the same 28 pre-existing errors as before this change, none in touched
files.
