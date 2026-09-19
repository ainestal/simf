# Phase B synthetic-mode delta — empirical pre-flight (2026-05-23)

Phase B (PR #46, `feat/lookahead-demo-shout-precast`) shipped the first
adaptive-lookahead consumer: `ActiveMitigationPolicy._lookahead_demo_shout_precast`
pre-casts Demoralizing Shout when the sum of physical damage scheduled
inside `policy.lookahead_consumers.demo_shout_precast.upcoming_window_s`
(default 1.5s — roughly one GCD) exceeds the configured
`upcoming_physical_threshold` (default 375,000 — symmetric to the
existing past-burst `recent_dtps > 250_000` gate × 1.5s window).

The consumer ships **default-OFF** (`enabled: false`) to preserve
bit-identity at every pinned seed. The director's natural follow-up
question was: **should the default flip to ON?**

This file captures the empirical pre-flight measurement that should
gate that decision. Numbers are NOT a green-light for the flip — they
quantify the shift so a future flip PR can land alongside an explicit
`skill_tiers` recalibration if the magnitude warrants it.

## Method

Brutoh's character (`src/simf/data/characters/brutoh.yaml`,
class_spec `protection_warrior`) run through `core.runner.run_simulation`
on each of four shipped damage profiles, 300 iterations per profile,
seed `42`, with the toggle OFF (shipped default) and again with the
toggle ON. Replay mode was NOT measured because `is_replay_event`
already fires DS unconditionally — the K calibration sweep against
the 18 Brutoh logs returned bit-identical results with the toggle on
or off (best K=3400 RMSE 0.0641; canonical K=3430 RMSE ≈ 0.064).

The measurement script monkeypatched `core.runner.load_constants`,
`core.policy.load_constants`, `core.character.load_constants`, and
`core.mitigation.load_constants` to a deep-copied constants dict
with the toggle flipped, leaving every other constant unchanged.
This isolates the demo_shout_precast consumer from any other Phase
2.10 / F14 / Phase A behaviour.

## Results

| Damage profile | OFF death_rate | ON death_rate | Δ death | OFF mean DTPS | ON mean DTPS |
|---|---|---|---|---|---|
| `m+_pull_melee` | 0.140 | 0.000 | **−14.0pp** | 40,688 | 37,012 |
| `m+_pull_caster` | 1.000 | 1.000 | 0 (saturated, magic-only) | 55,506 | 54,361 |
| `m+_boss_tankbuster` | 1.000 | 1.000 | 0 (saturated, magic-only) | 39,291 | 38,464 |
| `m+_dungeon_physical_chain` | 0.993 | 0.747 | **−24.6pp** | 39,992 | 37,642 |

### Reading the table

- **Physical-heavy profiles (`m+_pull_melee`, `m+_dungeon_physical_chain`)**
  show double-digit death-rate drops when the consumer fires. This is
  the consumer doing its job — DS DR landing on imminent physical
  spikes that the past-burst gate would have missed.
- **Magic-only saturated profiles (`m+_pull_caster`, `m+_boss_tankbuster`)**
  are unaffected — Demo Shout is a physical-DR debuff; the helper's
  school filter correctly excludes magic events from the threshold
  sum, so a magic spike can't trigger a pre-cast.
- **DTPS shifts are small (−2k to −4k)** because DS uptime gains are
  marginal in absolute terms; the death-rate drop comes from
  *timing* — DS covering the spike instant rather than arriving 5s
  after the past-burst gate trips.

## Why the magnitude is bigger than the director's pre-empirical prediction

The director's promotion-PR pick (2026-05-23) framed the flip as
"modifier=1.0 just plays a touch better." Empirically it's a
**−14 to −24.6pp shift** on the two profiles where it can fire at
all. Two reasons the prediction missed:

1. Demo Shout's DR is **20% all-physical** (`active_mitigation.demoralizing_shout.damage_taken_reduction: 0.20`),
   which is a larger DR than the +5% / +6% talent-mediated DRs the
   pre-cast tip was framed against in coaching guides.
2. The shipped synthetic damage profiles deliberately put the
   past-burst gate `recent_dtps > 250_000` BELOW the spike threshold
   for many tank-buster instants, so the old policy fires DS late.
   The lookahead pre-cast catches those windows.

The shift is **directionally correct** — it moves the synthetic
verdict toward "less pessimistic on physical-heavy content," which
matches Brutoh's repeated "+24 makes no sense" feedback. But the
magnitude is large enough that flipping the default without a
coupled `skill_tiers` recalibration would silently shift the
modifier=1.0 baseline and break the spread the v0.11.9 honesty pass
established.

## Gate for promotion

Before flipping `demo_shout_precast.enabled` from false to true by
default, EITHER of the following must hold:

1. **Couple the flip with `skill_tiers` modifier recalibration.**
   The `reading` / `learning` tier modifiers were calibrated against
   a modifier=1.0 baseline that didn't pre-cast. Flipping the
   default raises that baseline; lower-tier modifiers need to
   compensate or the ladder spread collapses. Acceptance criterion:
   re-run the modifier-vs-death-rate sweep at +18 and confirm
   `learning` still produces ≥ +25pp delta from `in_the_zone` on
   physical-chain profiles.

2. **OR** narrow the consumer's trigger so the synthetic shift is
   smaller. Candidates: raise `upcoming_physical_threshold` from
   375,000 to 600,000 (only fires on TRUE tank-buster spikes, not
   sustained melee), or shrink `upcoming_window_s` from 1.5s to 1.0s
   (less false-positive triggering on auto-attack swings). Either
   tuning would land the flip with a smaller magnitude.

Until one of those gates clears, the toggle stays default-OFF. The
consumer is in tree and opt-in available; flipping it is a
deliberate calibration event, not a routine default update.

## Sources

- Phase B implementation: `src/simf/core/policy.py::_lookahead_demo_shout_precast`
  (commit `8f8b574`, PR #46).
- Constants block: `src/simf/data/constants.yaml::policy.lookahead_consumers.demo_shout_precast`.
- Phase 2.10 honesty pass (v0.11.9) memory:
  `session_21_2026_05_22_skill_ladder_honesty.md`.
- Brutoh's "+24 makes no sense" feedback:
  `session_20_2026_05_22_honest_verdict.md`.
