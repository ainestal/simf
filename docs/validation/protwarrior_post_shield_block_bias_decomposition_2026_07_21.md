# Prot Warrior post-Shield-Block-fix bias decomposition — a real, sourced Shield Block haste-recharge bug found and fixed (2026-07-21)

**Status: one further real engine bug found, sourced, fixed, and independently
validator-confirmed. `constants_version` 42→43. `calibration_tier`
(`characterized`) and `calibration.global_rmse` (0.138) explicitly LEFT
UNCHANGED — human ratification pending, same as the two changes earlier
today.** This continues the same-day thread started by
`docs/validation/protwarrior_shield_block_fix_2026_07_21.md` (the coupled
`calculate_armor_resist` multiplier-order + Shield Block double-count fix,
which correctly and deliberately made the ratified corpus *worse*: RMSE
0.138→0.250, mean bias +11.0%→+23.0%). Read that doc first if you haven't —
this one picks up exactly where its "Recommendation" section left off.

## Task

Decompose the new +23.0% bias concretely: find where it actually comes from,
using tools that already exist, and either find another real, sourceable bug
or characterize the gap's real shape for the roadmap.

## Method and findings, in the order chased

### Lead 1 — does the "outside Shield Block" minority carry disproportionate damage? REFUTED.

Extended `scripts/full_chain_wedge.py`'s `RunWedgeResult` with base_amount
(DTPS-weighted) sums per bin, not just hit counts (`total_base_sum`,
`clean_base_sum`, `sb_active_base_sum`, and — added after the first pass
surfaced a school-mix confound, see below —
`physical_nonbleed_base_sum`/`physical_nonbleed_outside_sb_base_sum`).

**First result was a red herring.** The tool's original "clean" bin (outside
*any* of SB/SW/BSV) is ALL-SCHOOLS — magic damage and bleeds are never
gated by Shield Block, so they always land in "clean" regardless of whether
SB happens to be up. That bin reads 26.8% of hit count / 12.4% of damage —
looks disproportionate at first glance, but it's dominated by magic/bleed
damage that has nothing to do with Shield Block lapses.

Restricting to the population Shield Block can actually gate (physical,
non-bleed hits — the same population the validator's earlier
21,402-vs-433 SB-active-vs-inactive hit-count split used) gives the real
answer:

| | hit-count share | damage share |
|---|---:|---:|
| Physical-non-bleed, OUTSIDE a real SB window | 2.07% (499/24,134) | 2.49% |

**Proportionate, not disproportionate.** The "charges lapse specifically
around tank-buster spikes" hypothesis is refuted — the tiny outside-SB
slice's damage share tracks its hit-count share almost exactly. Two new
regression tests (`tests/test_full_chain_wedge.py`:
`test_base_amount_shares_no_windows`,
`test_base_amount_shares_sb_window_reclassifies_physical_only`) pin both the
all-schools "clean" bin's school-mix caveat and the physical-non-bleed-only
comparison, so a future reader can't repeat the same red-herring reading.

### Lead 3 — avoidance (dodge/parry) frequency: real, but calibration-INERT. Not chased further as a bias driver.

Modeled `base_dodge()+base_parry()` for the frozen calibration character =
22.67%. Real observed dodge+parry rate on physical attacks across the
ratified corpus (from `SWING_MISSED`/`SPELL_MISSED` lines' `missType`
field, pooled) = 23.58% (avoided=7,484, landed=24,251). These match within
~1pp — but the match is irrelevant to this bias by construction, not just
"close enough":

- `src/simf/io/combat_log_damage.py::parse_damage_event` only handles event
  types in `DAMAGE_EVENTS` (`SWING_DAMAGE`, `SPELL_DAMAGE`,
  `SPELL_PERIODIC_DAMAGE`, `RANGE_DAMAGE`, `SPELL_BUILDING_DAMAGE`) —
  `SWING_MISSED`/`SPELL_MISSED` return `None` immediately. A dodged/parried
  attack never becomes a `DamageTakenEvent`, so it never enters
  `replay.events` in the first place.
- `src/simf/io/log_replay.py::load_replay` sets `is_avoidable=False`
  unconditionally on every mob-sourced replayed `DamageEvent` ("Log events
  already survived avoidance — don't roll dodge/parry again").
- `mitigation.py`'s step 1 (`if event.is_avoidable and ...: rng.random() <
  ...`) therefore never fires during a `calibrate-k` sweep, regardless of
  what `cached_base_dodge + cached_base_parry` says.

This is structurally identical to the already-documented Riposte/crit-to-parry
precedent (`docs/validation/protwarrior_riposte_crit_parry_gap_2026_07_12.md`)
— a real forward-sim-relevant quantity, zero calibration relevance. Not
pursued further here.

### Lead 2 — block frequency: close on magnitude the earlier fix already checked, but this pass found something new underneath it

Measured (new tool: `scripts/sb_uptime_block_freq_audit.py`, corpus-wide):

| | real (log) | modeled | gap |
|---|---:|---:|---:|
| Block chance OUTSIDE Shield Block windows | 24.05% (n=499) | 22.13% (`base_block()`) | 1.9pp |
| Block chance INSIDE Shield Block windows | 88.13% (n=23,635) | 100% (`block_chance_during`) | 11.9pp, WRONG direction for over-predict (over-crediting block *reduces* modeled damage) |
| **Shield Block UPTIME** (time-weighted, merged log windows / duration) | **90.1%** | **59.1%** (`SimResult.mean_sb_uptime`, `skill_modifier=1.0`) | **31.0pp** |

The chance-given-SB-up and chance-outside-SB numbers are both close and, if
anything, point the wrong direction to explain an over-predict bias. The
**uptime** number is a different quantity entirely, and it is the real
driver: the sim's own Shield Block PRESS-CADENCE model — driven by
`core/policy.py`'s rage/charge/skill-gate logic, never read from the log's
real cast times — only kept the buff active 59.1% of the time. Real Brutoh
kept it up 90.1% of the time. For ~31% of every fight, the sim believed
Shield Block was down when the real player had it up, taking un-blocked-value
damage during that gap far more often than reality.

### Root cause: Shield Block's charge recharge is HASTED, and `core/policy.py` wasn't applying that

Fetched SimC source directly (`engine/class_modules/sc_warrior.cpp`,
commit `fd60a6384dcd55f3737f18f31755141c1fe1e540`, `midnight` branch — the
same commit this project's other SimC citations use, per
`docs/simc-reference/README.md`):

```cpp
struct shield_block_t : public warrior_spell_t
{
  shield_block_t( warrior_t* p, util::string_view options_str )
    : warrior_spell_t( "shield_block", p, p->spell.shield_block )
  {
    parse_options( options_str );
    cooldown->hasted = true;
    cooldown->charges += as<int>( p->spec.shield_block_2->effectN( 1 ).base_value() );
  }
  ...
```

The independent validator audit (below) traced this further than a bare
flag read: `spell_base_t::recharge_multiplier()` multiplies the cooldown's
base duration by `player->cache.spell_haste()` when `cooldown->hasted` is
set, and `cache.spell_haste()` is SimC's standard `1/(1+haste_pct)`
reciprocal convention — so the real mechanism is exactly
`recharge_duration = base_duration / (1 + haste_pct)`. The validator also
found this ISN'T an automatic DBC-attribute parse — a neighboring comment on
`mortal_strike_t`'s identical pattern reads "Doesn't show up in spelldata for
some reason" — meaning SimC's own developers hand-verified this against
real game behavior rather than trusting the datamine, which is corroborating
evidence on top of the source read itself.

`src/simf/core/policy.py`'s Shield Block press-cadence code
(`ActiveMitigationPolicy.decide()`, section "1. Shield Block") was scheduling
`state.sb_charges_ready_at[i] = now + sb["recharge_s"]` — a flat 16s
(`constants.yaml`'s `active_mitigation.shield_block.recharge_s`), completely
un-hasted. **This project already has the correct convention for a hasted
ability two lines above in the same file**, for Shield Slam's cycle:
`ss_cycle_s = 6.0 / (1 + state.cached_haste_pct)` (in `tick()`, feeding Brace
for Impact's stack tracking) — Shield Block's recharge just never got the
same treatment.

At Brutoh's calibration haste (`haste_rating=1020` → 23.18%,
`brutoh-calibration-2026-05.yaml`), effective recharge ≈ 16/1.2318 ≈ 13.0s.
With 2 charges, naive steady-state uptime ≈ 6/(13.0/2) ≈ 92% — close to the
real logged 90.1%, versus the un-hasted model's 59.1%.

### The fix

`src/simf/core/policy.py`: introduced `hasted_recharge_s = sb["recharge_s"]
/ (1 + state.cached_haste_pct)`, used both where a spent charge's next-ready
time is scheduled and in the skill-gate retry-cadence line
(`self._sb_next_opportunity_at`). `active_mitigation.shield_block.recharge_s`
itself is unchanged (16, the correct base value) — only the point of use is
now haste-aware, mirroring the pre-existing Shield Slam convention exactly.

Two new tests pin this precisely (`tests/test_sb_diagnosis.py`):
- `test_shield_block_recharge_is_hasted_at_policy_level` — drives
  `ActiveMitigationPolicy.decide()` directly with ample rage (isolating the
  recharge-timer change from the ALSO-haste-scaled rage-income channel) and
  asserts the scheduled recharge time equals `now + recharge_s/(1+haste_pct)`
  exactly, not the old flat value.
- `test_shield_block_recharge_faster_with_more_haste` — an end-to-end
  confirmation that a higher-haste character reaches a higher
  `SimResult.mean_sb_uptime` than a zero-haste one under the same seed/profile.

## Before / after — `simf calibrate-k`, ratified 16-log corpus, canonical K=3430, seed=42, 300 iterations

| | Before (this fix, i.e. the v42 baseline) | After |
|---|---:|---:|
| RMSE | 0.2498 | 0.1620 |
| Mean signed bias | +23.0% (over-predict) | +13.7% (over-predict) |
| Within ±15% | 4/16 | 8/16 |

Per-run deltas, before → after (same order both runs):

| Run | Before | After |
|---|---:|---:|
| Algeth'ar Academy +12 | +1.6% | −4.3% |
| Skyreach +14 [partial] | +7.4% | −1.0% |
| Algeth'ar Academy +14 | +10.9% | +2.0% |
| Windrunner Spire +14 | +39.6% | +27.5% |
| Windrunner Spire +14 [partial] | +34.6% | +25.0% |
| Magisters' Terrace +14 | +31.4% | +22.9% |
| Nexus-Point Xenas +12 | +13.4% | +10.0% |
| Magisters' Terrace +12 | +29.0% | +16.6% |
| Pit of Saron +13 | +28.4% | +18.1% |
| Maisara Caverns +12 | +22.1% | +12.5% |
| Windrunner Spire +12 | +24.0% | +15.0% |
| Pit of Saron +12 | +28.4% | +20.0% |
| Maisara Caverns +14 | +27.4% | +17.8% |
| Pit of Saron +13 (2nd) | +21.1% | +10.4% |
| Magisters' Terrace +13 | +24.7% | +16.0% |
| Pit of Saron +14 | +23.5% | +10.2% |

**Large, real, correctly-directed improvement** — every run moved in the
same direction (less over-prediction), consistent with closing a real
mitigation-uptime under-crediting gap. Still worse than the pre-Shield-Block-fix
headline (0.138/+11.5%, 10/16) — expected and correct, since that number was
itself propped up by the two now-fixed double-counting bugs (see the earlier
doc). `calibration_tier` and `global_rmse` are NOT changed by this fix — same
reasoning as before: this is a code-correctness fix, not a self-certified
recalibration.

## What's still left — a second, deeper, well-evidenced lead, deliberately NOT chased this session

Post-fix, `SimResult.mean_sb_charge_limited_pct` still reads ~26.9% (the sim
still believes both charges are recharging ~27% of the time), and a quick
diagnostic (`scripts/_sb_charge_diagnostic.py`, one-off, not a shipped tool)
puts the fixed model's own steady-state uptime at ~69.6% on the calibration
character specifically — still meaningfully below the naive ~92% ceiling and
the real 90.1%. Chasing why surfaced a second, genuinely distinct mechanism:

`core/policy.py`'s press trigger is **reactive** — it only tries to press a
new charge when `now >= state.shield_block_until - 1.5` (wait until the
*current* buff is nearly expired). SimC's real default Protection APL
(`sc_warrior.cpp:8557`) reads:

```
cooldowns->add_action( "shield_block,if=buff.shield_block.remains<=10" );
```

Since the buff's own base duration is only 6s, a `remains<=10` condition is
only meaningful if pressing while the buff is still active can push its
remaining time **past** 6s. The validator independently confirmed this via
`buff_t::extend_duration_or_trigger` (`buff.cpp:2101-2113`): pressing while
already up **extends** remaining time by another full `buff_duration()`
(6s), uncapped in the generic function. Real SimC play "banks" Shield Block
well ahead rather than topping it up one 6s window at a time — a genuinely
different *shape* of press policy, not a parameter this project can nudge
(the validator specifically checked and rejected "just tighten the `-1.5`
buffer" as a cheap substitute: the buffer controls timing precision *within*
one window, not whether a press is allowed to stack additional banked
duration onto an already-active buff at all).

**Deliberately deferred, not fixed here.** Implementing it correctly means
re-deriving the Phase 2.10 skill-gate/Bernoulli-miss semantics for a
"spend-ASAP-and-bank" model instead of the current "one discrete cycle"
model, and it would also change what `sb_rage_starved_s`/`sb_charge_limited_s`
mean for the skill-ladder diagnostics that already consume them — a bigger,
riskier change than a one-line formula fix, needing its own dedicated
investigation and calibration re-run. This branch already carries one real,
unratified engine change; per this project's own
engine-batch-ratification-cap norm, this is the right place to stop and
name the lead rather than keep going. The independent validator's verdict
(below) is DEFER, not fix now.

### Lead 4 — input cross-check: not needed this pass

Given the haste-recharge fix closed most of the newly-measured gap with a
clean, well-sourced mechanism, and the remaining residual has its own
distinct, well-evidenced explanation (the press-cadence-policy lead above),
re-verifying `shield_armor`/mastery/secondary-stat inputs against tooltips
again wasn't necessary this session — no unexplained residual is left over
that would point at a stale input rather than the two named mechanisms.

## Independent validator audit

A `validator` agent (engine-math mode) independently re-derived every claim
in this doc before it was finalized, without taking any of my numbers on
faith:

- **Re-fetched SimC source directly via `curl`** (not a WebFetch summary,
  which it found truncates mid-file and misses `shield_block_t` entirely —
  "a good reminder that for source-audit work, raw fetch + grep beats an
  AI-summarized WebFetch"). Confirmed `cooldown->hasted = true;` at the
  cited line, then traced the flag all the way through
  `spell_base_t::recharge_multiplier()` and `cache.spell_haste()`
  (cross-checked against a second, independent usage site) to the literal
  `base_duration / (1+haste_pct)` mechanism — "not an approximation of the
  mechanism, the literal mechanism."
- **Confirmed the sibling haste convention** (`ss_cycle_s = 6.0 / (1 +
  state.cached_haste_pct)`) is pre-existing and untouched by this diff, and
  that the fix's form matches it exactly — "you didn't invent a new
  convention."
- **Audited the diff itself**: `hasted_recharge_s` computed once, used in
  exactly two consistent places, no double-application; `make_policy()`'s
  dispatch confirmed by reading it (not assumed) to route every other spec to
  its own policy class, so `ActiveMitigationPolicy`'s changed code path is
  structurally unreachable for any non-`protection_warrior` character; the
  pre-existing hasted rage-income line is a separate mechanic, not a second
  application of the same haste effect on the same resource;
  `SimResult.mean_sb_uptime`'s dependency chain traced end-to-end in
  `runner.py` to confirm it's wired to the actual change, not a decorative
  metric.
- **Independently re-ran `calibrate-k` on both sides of the diff** (git
  stash / pop), reproducing RMSE 0.2498→0.1620 and +23.0%→+13.7% exactly,
  including the full per-log delta list — not a spot-check, a full
  independent reproduction.
- **Verdict, verbatim**: "CONFIRMED... a clean, well-sourced, correctly
  directed fix... no double-counts, no artifacts found."
- **On the deferred press-cadence lead**: independently confirmed the APL
  citation and the `extend_duration_or_trigger` semantics from source, agreed
  the `-1.5` buffer isn't a cheap substitute, and recommended DEFER —
  "document it as a named lead... and treat it as its own investigation with
  its own calibration re-run, consistent with the project's own
  engine-batch-ratification-cap discipline."

## Caveats / what could still be wrong

- The remaining +13.7% bias is **not fully explained** — the press-cadence-policy
  lead is well-evidenced (APL citation, `extend_duration_or_trigger` semantics,
  a measured `mean_sb_charge_limited_pct` residual) but not yet quantified as
  a predicted delta the way the haste fix was; it could close most, some, or
  little of what's left, and there may be additional smaller contributors not
  surfaced by this pass.
- `scripts/_sb_charge_diagnostic.py`'s 69.6%/26.9%/3.2% split is a one-off,
  quick diagnostic (not test-covered, not part of any shipped tool) — treat it
  as a directional confirmation, not a precise measurement on par with the
  wedge/uptime-audit tools that DO have tests.
- The block-chance-inside-SB gap (88.1% real vs 100% modeled) was measured but
  not chased — it points the WRONG direction for this bias (over-crediting
  block-during-SB should make the sim under-predict, not over-predict), so it
  was correctly not a priority this session, but it's a real ~12pp gap that
  could matter for a future pass focused on getting block CHANCE exactly right
  independent of uptime.
- As with the two earlier fixes today, `calibration_tier`/`global_rmse` are
  deliberately unchanged pending human ratification — this doc does not
  decide whether/when the headline number should move.

## Files changed

- `src/simf/core/policy.py` — hasted Shield Block recharge
  (`hasted_recharge_s`), citation inline.
- `src/simf/data/constants.yaml` — `constants_version` 42→43 with a full
  changelog entry; comment-only pointers near `calibration.global_rmse` and
  `specs.protection_warrior.calibration_tier` — no values in either location
  changed.
- `scripts/full_chain_wedge.py` — extended with DTPS-weighted bin-share
  fields (`total_base_sum`, `clean_base_sum`, `sb_active_base_sum`,
  `physical_nonbleed_base_sum`, `physical_nonbleed_outside_sb_base_sum`) and
  a new aggregate print section (Lead 1).
- `scripts/sb_uptime_block_freq_audit.py` — new: block-chance-in/out-of-SB,
  real-vs-modeled SB uptime, and (characterization-only) real-vs-modeled
  avoidance rate, across the ratified corpus.
- `scripts/_sb_charge_diagnostic.py` — new, one-off: real-vs-modeled
  `mean_sb_uptime`/`mean_sb_charge_limited_pct`/`mean_sb_rage_starved_pct`
  split, haste=0 vs real haste.
- `tests/test_full_chain_wedge.py` — two new tests for the Lead-1 base-amount
  share fields (`test_base_amount_shares_no_windows`,
  `test_base_amount_shares_sb_window_reclassifies_physical_only`).
- `tests/test_sb_diagnosis.py` — two new tests for the haste-recharge fix
  (`test_shield_block_recharge_is_hasted_at_policy_level`,
  `test_shield_block_recharge_faster_with_more_haste`).
- `docs/validation/protwarrior_post_shield_block_bias_decomposition_2026_07_21.md`
  — this doc.

## Tests / lint / mypy

Full suite: **2709 passed, 21 skipped** (was 2705 passed pre-this-session;
+4 from the new tests above). `ruff check` + `ruff format --check` clean on
every touched Python file. `mypy src/` shows the same 28 pre-existing errors
as before this change, none in touched files.
