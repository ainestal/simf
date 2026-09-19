# Per-school mitigation gap remeasurement — post-F11/F12/F13/F14, K=3430 (2026-05-23)

**Verdict: DEFER Phase C predictive per-segment risk.**

Per-school p90 absolute gap is 12–28pp on every non-frost school across 18
Brutoh runs. The post-F14 engine's mitigation chain is structurally sound on
physical (block / mastery / armor curve all wired correctly), but a
systematic ~10–13pp under-prediction of DR persists on **both physical and
magic**, in the **same direction** — the engine consistently predicts more
damage taken than the player actually took. Phase C predictions on a
mixed-school segment would therefore be confidently miscalibrated.

The K=3430 / RMSE 0.065 global calibration is preserved (and not contradicted)
because the per-event gap aggregates against post-mitigation DTPS, where the
log's actual healing-side absorbs + party-DR auras + Phalanx uptime variation
+ Brace-for-Impact stacking + spell-reflect compensate at the run-level. Per
segment those compensations are NOT stationary, which is exactly why Phase C
needs them modeled before it can ship.

## Method

`scripts/per_school_mitigation_gap.py` (added this session). For each damage
event in every CHALLENGE_MODE run across the 17 logs in `examples/` (16
Brutoh-Uldum-EU + AnonGuardian3's spec-import — no AnonGuardian3 log in tree;
the 28 files scanned include duplicates and other-target archives):

1. Reconstruct the buff-window state (Shield Block, Demoralizing Shout,
   Shield Wall, Last Stand) from `SPELL_AURA_APPLIED/REMOVED` events,
   timestamps absolute.
2. For each `DAMAGE` event taken by the target, stamp the engine state
   (`shield_block_until`, etc.) so the mitigation chain sees the exact
   real-world button state at that timestamp.
3. Call `apply_mitigation(state, event, rng)` directly. The `dealt` output
   is the engine's predicted pre-absorb post-block damage.
4. Compare to the log's `amount + absorbed` (pre-absorb, post-block actual).
5. `residual_pp = (predicted_DR - actual_DR) × 100`. Positive = engine
   over-mitigates; negative = engine under-mitigates.
6. Bucket by school. Bleeds (physical school + DOT tick + spell-name
   fragment in {`Rip`, `Rake`, `Rend`, `Mortal Wound`, `Deep Wounds`,
   `Bleed`, `Crippling Bleed`, `Vicious Wound`, `Hemorrhage`, `Lacerate`,
   `Open Wound`, `Gushing Wound`, `Savage Slash`, `Garrote`}) split into a
   separate `bleed` bucket.

This isolates engine math from policy / CD-timing noise — the engine sees
the actual real-world buff state at every timestamp.

**Assumptions / caveats:**

- `state.party_magic_dr_active` left FALSE by default (user-facing reality
  per `mitigation.py:94`). A second pass with the opt-in ON is captured
  alongside.
- Brutoh's shield armor in YAML is 989 (post-2026-05-19 voidcore upgrade);
  the calibration logs are pre-upgrade at shield_armor=931. The current
  audit uses the YAML 989, which OVER-states block_value by ~6%, which
  OVER-states predicted block DR by ~1pp. Direction: the engine in this
  audit predicts MORE DR than it would with log-era gear, so the true
  physical under-mitigation in real-world conditions is closer to
  **−14pp** (not −13pp). Confirms the gap is real, slightly larger, not
  an artifact of measuring against a wrong character config.
- `cached_total_armor` / `talent_set` re-populated per state construction
  (state was manually built outside `runner.run_simulation`). All other
  per-iteration cached properties left as initialised — fine for replay,
  not adequate for a synthetic Monte Carlo prediction.
- Block rolls are RNG-stochastic with seed=42. Per-event roll noise
  averages out across thousands of events.

Output: `docs/validation/per_school_data_brutoh.json` (party_magic_dr OFF,
the default) and `docs/validation/per_school_data_brutoh_pmd.json` (opt-in
ON, for comparison).

## Aggregate per-school gap distribution (party_magic_dr OFF, 16 Brutoh
logs, 18 valid Mythic+ runs, 25,534 physical / 9,852 magic / 117 bleed
events)

| Bucket    | n_evts | n_runs | weighted gap | mean \|gap\| | p50 \|gap\| | p90 \|gap\| | max \|gap\| | direction |
|-----------|-------:|-------:|-------------:|------------:|-----------:|-----------:|-----------:|-----------|
| physical  | 25,534 |     18 |   **−12.79** |    12.66 pp |  13.03 pp  |  14.08 pp  |  14.08 pp  | 18/18 under-mit |
| bleed     |    117 |      2 |   **+37.22** |    37.10 pp |  37.10 pp  |  37.98 pp  |  37.98 pp  | 2/2 over-mit (engine bug) |
| shadow    |  5,022 |     14 |   **−10.41** |    10.18 pp |  10.50 pp  |  15.64 pp  |  15.84 pp  | 13/14 under-mit |
| fire      |    413 |     12 |   **−12.22** |    11.45 pp |  11.86 pp  |  17.90 pp  |  18.97 pp  | 12/12 under-mit |
| frost     |    688 |      6 |   **+1.82**  |     5.22 pp |   3.41 pp  |  15.23 pp  |  15.23 pp  | 3 neg / 3 pos |
| arcane    |  2,224 |     12 |   **−11.36** |    10.99 pp |   9.33 pp  |  17.37 pp  |  17.46 pp  | 12/12 under-mit |
| nature    |  1,221 |     14 |   **−13.20** |    13.01 pp |  13.12 pp  |  25.16 pp  |  27.50 pp  | 13/14 under-mit |
| holy      |    284 |      2 |   **−10.67** |    10.54 pp |  10.54 pp  |  12.22 pp  |  12.22 pp  | 2/2 under-mit |

`|gap|` = absolute value of the run-level signed gap.
"under-mit" = engine predicts LESS DR than the log actually shows.

## Per-dungeon per-school gap (mean across runs, weighted by base damage)

| Dungeon              | physical          | shadow            | fire              | frost            | arcane           | nature            | holy            |
|----------------------|------------------:|------------------:|------------------:|-----------------:|-----------------:|------------------:|----------------:|
| Algeth'ar Academy    | −10.4 (3 runs)    | —                 | —                 | —                | **−14.0 (3)**     | −10.0 (2)         | —               |
| Magisters' Terrace   | −14.0 (3)         | −12.3 (3)         | −9.2 (2)          | —                | −7.6 (3)          | —                 | —               |
| Maisara Caverns      | −13.5 (2)         | −9.4 (2)          | —                 | —                | —                 | −10.9 (2)         | —               |
| Nexus-Point Xenas    | −12.4 (2)         | −10.5 (2)         | —                 | —                | −12.2 (2)         | —                 | −10.5 (2)       |
| Pit of Saron         | −13.3 (4)         | −8.2 (4)          | —                 | +1.7 (4)         | —                 | —                 | —               |
| Skyreach             | −11.5 (1)         | —                 | −14.4 (1)         | —                | —                 | −13.6 (1)         | —               |
| Windrunner Spire     | −12.7 (3)         | −14.8 (1)         | —                 | —                | —                 | **−19.3 (3)**     | —               |

## Per-run physical+magic gap (18 valid runs)

| log[idx]                            | dungeon           | +key | phys  | shadow | fire  | frost | arcane | nature | holy  |
|-------------------------------------|-------------------|-----:|------:|-------:|------:|------:|-------:|-------:|------:|
| 050626_153703[0]                    | Windrunner Spire  |  +12 | −11.7 |   —    |  —    |  —    |   —    | −13.6  |  —    |
| 050626_172823[0]                    | Algeth'ar Academy |  +12 |  −9.9 |   —    |  —    |  —    |  −8.9  |  −7.4  |  —    |
| 051026_073906[0]                    | Nexus-Point Xenas |  +12 | −12.9 |  −8.1  |  —    |  —    |  −7.8  |   —    | −8.9  |
| 051026_090846[2]                    | Windrunner Spire  |  +14 | −13.1 |   —    |  —    |  —    |   —    | −16.9  |  —    |
| 051026_105836[1]                    | Pit of Saron      |  +13 | −13.0 |  −6.5  |  —    | +1.4  |   —    |   —    |  —    |
| 051026_151539[0]                    | Skyreach          |  +14 | −11.5 |   —    | −14.4 |  —    |   —    | −13.6  |  —    |
| 051326_171842[0]                    | Pit of Saron      |  +13 | −13.7 |  −6.2  |  —    | −1.3  |   —    |   —    |  —    |
| 051526_210245[0]                    | Magisters' Terrace|  +12 | −14.0 | −15.4  | −11.2 |  —    |  −6.7  |   —    |  —    |
| 051526_210245[2]                    | Pit of Saron      |  +12 | −13.4 | −12.3  |  —    | +2.3  |   —    |   —    |  —    |
| 051526_210245[3]                    | Windrunner Spire  |  +14 | −13.3 | −14.8  |  —    |  —    |   —    | **−27.5** |  —  |
| 051626_161830[3]                    | Maisara Caverns   |  +12 | −12.9 | −11.3  |  —    |  —    |   —    | −13.7  |  —    |
| 051726_090237[1]                    | Pit of Saron      |  +14 | −13.0 |  −7.7  |  —    | +4.5  |   —    |   —    |  —    |
| 051726_134819[0]                    | Magisters' Terrace|  +13 | −14.1 |  −9.7  |  —    |  —    |  −6.6  |   —    |  —    |
| 051726_134819[1]                    | Magisters' Terrace|  +14 | −14.0 | −11.8  |  −7.2 |  —    |  −9.7  |   —    |  —    |
| 051826_135935[0]                    | Maisara Caverns   |  +14 | −14.1 |  −7.5  |  —    |  —    |   —    |  −8.2  |  —    |
| 051826_135935[1]                    | Algeth'ar Academy |  +14 | −11.1 |   —    |  —    |  —    | −15.4  | −12.7  |  —    |
| 051826_212525[0]                    | Nexus-Point Xenas |  +12 | −12.0 | −12.8  |  —    |  —    | −16.6  |   —    | −12.2 |
| 051926_132910[0]                    | Algeth'ar Academy |  +10 | −10.3 |   —    |  —    |  —    | −17.5  |   —    |  —    |

Bleed events appear only in two Maisara runs (Vicious Wound on the wolf trash
pack): +37.98pp / +36.22pp — engine treats bleeds as armor-mitigated, in
reality bleeds bypass armor. Small total damage but clean correctness bug.

## What this means against the decision criteria

| Criterion (pre-committed)                                                  | Observation                                                | Decision |
|----------------------------------------------------------------------------|------------------------------------------------------------|---------:|
| Magic gap <5pp distribution-wide → SHIP Phase C                            | Magic p90 is 12–25pp on shadow/fire/arcane/nature/holy.    | NO       |
| 5–10pp at p90 OR offsetting signs (phys +N, magic −N) → SHIP conditional   | Same-direction (both under-mit), so they ADD UP, not offset. Magnitudes overshoot range.  | NO       |
| >10pp at p90 on any school → DEFER                                         | All six measured schools except frost are >10pp at p90.    | **DEFER**|

## Plausible contributors, in estimated leverage order

The engine's per-event prediction is, on every run, ~13pp short of the
log's actual DR on **physical**, with σ ≈ 1pp across 18 runs (band
−9.9 to −14.1pp). The tightness of that band is a strong signal that
**the gap is dominated by ONE structural missing layer**, not six small
ones — six independent ~2pp contributors with their own variances would
spread the per-run distribution much wider than ±2pp.

These are CANDIDATES for the structural miss; each is plausible but
unmeasured directly. They must be validated post-fix, not assumed.

1. **Brace for Impact stacks** (`talent: brace_for_impact`). Stacking
   0-4 stacks × 1% per stack = **4% max**. Engine constructs state with
   `state.bfi_stacks=0` and never builds (`mitigation.py:60`,
   `mitigation.py:270`). Real player builds to near-cap within seconds.
   At 4% max this CANNOT explain a 13pp gap on its own. Upper bound:
   ~4pp on physical, smaller on dot ticks where BfI doesn't apply.
2. **Phalanx avg_uptime model.** `talents.phalanx.avg_uptime = 0.50` is a
   guess; real player log uptime is ~40-60% but varies by fight (boss
   debuff-immunity prunes Phalanx's value to ~0 on Algeth'ar bosses;
   trash-heavy maps benefit more). Expected contribution: ±3pp per-fight
   variance vs. engine's flat 50%.
3. **Healer / paladin Devotion Aura, Beacon-of-Faith, Earth Shield**
   (`party_magic_dr` opt-in). Off by default; magic-only. Closes magic
   gap when enabled (see PMD pass below). Does NOT close physical gap.
4. **Healer DR auras that DO apply to physical** (Shaman Earth Shield 4%
   all-school, Druid Symbol of Hope 5% all-school, etc.) — not currently
   modeled even in the opt-in (`party_magic_dr` is gated `school !=
   physical` at `mitigation.py:246`).
5. **Spell-reflect refunds.** Reflected damage isn't taken; engine models
   this via `state.spell_reflect_until` BUT only triggers if the
   per-event roll falls during the window. The audit script uses
   `is_avoidable=False` to avoid double-rolling dodge/parry; reflect is
   handled separately (`mitigation.py:187`).
6. **Bonus armor from gear sources** not represented in `brutoh.yaml` —
   trinket procs, weapon enchants. Per `constants.yaml:82` the empirical
   minimum at K=3200 vs canonical K=3430 suggests ~230 of bonus armor
   unmodeled; that maps to ~2pp physical DR on a 5500-armor base.

**Best single-contributor candidate for the 13pp floor:** a healer-aura
layer that includes physical (point 4 above), at ~5-10pp magnitude
depending on group composition. The σ ≈ 1pp on physical IS consistent
with a single ~10pp DR layer that's present on every fight (the group
swaps healers between dungeons but each healer brings approximately the
same all-school DR aura suite). The shield_armor YAML drift (point 6)
contributes a small additional offset (see Assumptions below).

The **magic** gap with PMD OFF tracks the physical gap closely (10-13pp
vs 12pp), which is consistent with the same all-school DR layer being
the dominant miss. With PMD ON, the magic gaps drop ~5pp toward the
physical floor — confirming that the layer is present and is at least
partially school-uniform (Earth Shield 4% all-school, Shaman's
Ancestral Vigor 5% magic, Druid Symbol of Hope 5% all-school).

## party_magic_dr opt-in pass

Re-run with `state.party_magic_dr_active = True` (the
`protection_warrior.party_magic_dr = 0.05` opt-in layer in
`constants.yaml:561`). Same 18 runs, same engine state otherwise:

| Bucket    | n_evts | n_runs | weighted gap | mean \|gap\| | p50 \|gap\| | p90 \|gap\| | max \|gap\| |
|-----------|-------:|-------:|-------------:|------------:|-----------:|-----------:|-----------:|
| physical  | 25,534 |     18 |   **−12.79** |    12.66 pp |  13.03 pp  |  14.08 pp  |  14.08 pp  |
| bleed     |    117 |      2 |   **+37.22** |    37.10 pp |  37.10 pp  |  37.98 pp  |  37.98 pp  |
| shadow    |  5,022 |     14 |    **−6.55** |     6.88 pp |   6.96 pp  | **11.78 pp** | 11.98 pp  |
| fire      |    413 |     12 |    **−8.36** |     7.59 pp |   8.00 pp  | **14.04 pp** | 15.11 pp  |
| frost     |    688 |      6 |    **+5.68** |     6.07 pp |   5.69 pp  | **11.37 pp** | 11.37 pp  |
| arcane    |  2,224 |     12 |    **−7.50** |     7.13 pp |   5.47 pp  | **13.50 pp** | 13.58 pp  |
| nature    |  1,221 |     14 |    **−9.34** |     9.71 pp |   9.26 pp  | **25.16 pp** | 26.68 pp  |
| holy      |    284 |      2 |    **−6.81** |     6.68 pp |   6.68 pp  |   8.36 pp  |  8.36 pp  |

**The opt-in halves magic weighted gaps** (shadow −10.4→−6.6pp, fire
−12.2→−8.4pp, arcane −11.4→−7.5pp, nature −13.2→−9.3pp, holy
−10.7→−6.8pp). But the **p90 distribution remains >10pp on shadow / fire
/ frost / arcane / nature** — Phase C would still produce confidently
wrong predictions on those schools at the 10-percent-worst run.

**Physical is unchanged** (the layer only applies to non-physical).
−12.8pp physical floor persists, with very tight distribution (mean
12.66pp / max 14.08pp / p90 14.08pp — 18/18 runs negative).

The opt-in is a marginal improvement, not a Phase C unblocker. Same-
direction (both physical AND magic under-mitigated) adds in DTPS, so the
opt-in's `−6 to −10pp` magic gap **stacks** with the `−13pp` physical
floor when a segment contains both.

## What would unblock Phase C

A defensible Phase C predictive per-segment risk panel needs the per-event
predicted DR to be within ±5pp of log truth on every school the segment
contains. To get there:

### Engine fixes (ordered by leverage)

1. **Model Brace for Impact stack-up properly.** Either pre-warm the
   audit state to `bfi_stacks_max` (cheap, slightly optimistic) or
   implement BfI stack accumulation in the runner replay path (correct).
   Expected: closes ~4-6pp on physical across every run.
2. **Healer-aura DR layer that includes physical.** Generalise the
   `party_magic_dr` knob into `party_dr_by_school` with school-tagged
   per-aura entries (`shaman.earth_shield: {all: 0.04}`, `druid.symbol_
   of_hope: {all: 0.05}`, `holy_priest.power_word_barrier: {magic:
   0.10}`). When a log shows a buff active on the tank, apply its
   per-school multiplier. Expected: closes ~3-5pp on physical and ~5-8pp
   on magic (group-composition-aware).
3. **Bleed school bypass.** Make armor DR conditional on `not is_bleed`
   (detect via spell-name allowlist matching the audit script's
   `BLEED_SPELL_FRAGMENTS`). The 117 events in this corpus are small,
   but bleed-heavy fights (Algeth'ar, Maisara wolf packs) compound to
   meaningful per-segment damage. Expected: closes the +37pp bleed
   bucket entirely; small DTPS effect overall.
4. **Bonus armor inference.** Add a `bonus_armor` knob to the character
   YAML; populate from log COMBATANT_INFO armor field when available.
   Closes the ~2pp unmodeled-armor floor.
5. **Per-source-immunity inference for Algeth'ar arcane.** Per-dungeon
   table shows Algeth'ar arcane is the worst residual (−14.0pp / 3
   runs); the Echo of Doragosa-style debuff-immunity list already
   exists for Demo Shout, needs an arcane analogue.

### Pipeline / inference fixes

6. **Buff-aura inference at event time.** This audit's buff-window
   reconstruction is a prototype. Productionising it (move into
   `src/simf/io/log_replay.py`, expose `apply_log_buff_state()` on
   `MitigationState`) lets Phase C run on actual log buff state rather
   than the synthetic policy's choices.
7. **Detected talent + gear stats from COMBATANT_INFO.** `iter_combatant_
   info` already exposes this. Wiring the gear stats into the runner
   replay path (currently only talents flow through per `cli.py:368`)
   would handle the Brutoh-shield-armor-989-vs-log-931 drift cleanly.

### What ships AT THIS RESIDUAL

The existing **descriptive** per-segment risk surface
(`ui/log_view.py:render_per_segment_risk`, Phase A 2026-05-15) is sound
because it shows where deaths actually happened and what abilities
contributed — pulled directly from the log, not predicted from the
engine. Keep it. Add a banner that explicitly disclaims predictive
intent (something like "Reconstructed from your log. Predictive
per-pull risk is not yet calibrated to ship — see
`docs/validation/magic_mit_gap_remeasurement_2026_05_23.md`").

## Bleed bucket — the small surprise

Engine treats bleeds (physical + DOT) as armor-mitigated. They aren't
in WoW: bleeds bypass armor. The audit shows two Maisara runs with
**+37pp engine over-mitigation** (predicted 70% DR, actual 33%) — but
the sample is small (117 events / 2 runs / both Maisara) so the magnitude
is "appears to be ~+37pp" rather than "is ~+37pp." Total bleed-bucket
damage is ~7% of Maisara base damage on those two runs; on bleed-heavy
bosses (Algeth'ar Echo of Doragosa applies a bleed in the add-phase)
the per-event residual would replicate but the population is still
small fraction of total damage. Recommend fix order item 3 (bleed
bypass) be tied to the F-series audit chain as **F15**.

## Trust verdict

- **K=3430 / RMSE 0.065 calibration**: unchanged and uncontradicted.
  Per-run aggregate DTPS still lands within ~6.4% across 18 runs because
  the under-prediction is partially compensated by per-run absorb
  variance and the K=3430 over-armoring (intentional structural choice
  per `constants.yaml:101-112`).
- **Relative comparisons** (Δ between two character setups, gear swaps
  on the same dungeon): still trustworthy when both setups share the
  same engine bias. Phase 2.7 sim-derived survivability weights, F12
  block_value math, F11/F13 mastery chain — all correct.
- **Absolute per-event DR prediction**: NOT trustworthy below ±5pp.
  Phase C deferred until items 1-3 above land.
- **Existing Phase A descriptive surface**: trustworthy, ship-as-is.
  Disclaim the predictive frontier.

## Files

- Measurement script: `scripts/per_school_mitigation_gap.py` (new).
- Raw JSON outputs: `docs/validation/per_school_data_brutoh.json` (PMD
  off, the default user-facing case) and
  `docs/validation/per_school_data_brutoh_pmd.json` (PMD opt-in on).
- Original Phase A surface that should ship-as-is:
  `src/simf/ui/log_view.py:render_per_segment_risk`.
- Prior audit this remeasures: `docs/validation/magic_mit_audit_2026_05_16.md`.
