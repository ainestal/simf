# Survivability coaching — hit-vs-coverage MVP (shipped 2026-06-29)

Builds the validated wedge from the ROADMAP "Survivability coaching from the
sim-vs-log gap" entry (validated 2026-06-29 via `scratchpad/coach_prototype.py`
+ a 3-perspective workflow). This chapter records the build, the
adversarial-review findings, and the fixes made before merge.

## What shipped

A new section on the **Why did I die?** surface — "Defensive coverage on your
biggest hits" — rendered between per-segment risk and the CD-plan panel. The
core is the **hit-vs-coverage join**: the tank's top-N (8) biggest damage-taken
events × which defensive cooldown was active at each hit's timestamp.

- `core/coaching.py` — pure engine (no I/O, no sim). The join, the answer-first
  verdict (**no % in the headline**), school-scope gating, tier-aware
  major/minor classification, continuous-uptime computation.
- `io/combat_log.py` — `parse_self_buff_windows` (tank BUFF aura
  apply→remove intervals) + `parse_source_debuff_windows` (per-enemy-GUID DEBUFF
  intervals, for Demoralizing Shout). Neither ever fabricates a window, so
  coverage can only be **under-credited, never over-credited**.
- `ui/log_view.py` — `render_defensive_coverage` + a disk-cached window parse
  (`_cached_coverage_report`), wired into `render_log_analysis`.
- `data/constants.yaml` — per-spec `coaching:` defensives registry.
  constants_version 32→33.

### Why it's model-independent

It reads 100% from the log's damage + aura events and touches no simulation. So
it works the same on a `calibrated: false` spec as a calibrated one — it's
spec-agnostic and bills nothing to the model. The honesty caption says so
explicitly ("no simulation, so this holds for any spec").

## The continuous-vs-reactive design rule (locked in)

Per the user's 2026-06-29 guidance, the per-lever metric matches how the CD is
meant to be used:

- **`role: coverage`** (reactive/emergency DR — Shield Wall, Survival
  Instincts, Incarnation, Barkskin) → judged by **spike coverage** (the join).
  Pressing on cooldown is wrong; holding for the tank-buster is correct, so they
  never appear as a raw-uptime stat.
- **`role: continuous`** (Shield Block, Ironfur) → judged by **uptime**, shown
  as a plain log-derived line, and **excluded** from the "no defensive up"
  determination (a continuous tool missing one spike is not a failure).

## Live verification

- **Guardian (AnonTank1, Windrunner Spire +13, auto-hydrated example tank):** "You
  ate 5 of your 8 biggest hits with no defensive cooldown up", 3/8 covered by
  Survival Instincts / Barkskin / Incarnation, Ironfur 22% continuous.
  Screenshotted; Incarnation (physical scope) correctly covers physical hits and
  is correctly excluded from Nature/Fire hits.
- **Warrior (Brutoh, Skyreach +12):** 4/8 covered, the per-source Demo Shout
  join correctly distinguishes the 12:02 Shear (mob had Demoralized) from the
  17:22 Shear (different mob, uncovered). Programmatically verified +
  integration-tested.

## Adversarial review (3 reviewers × independent verify) — findings + fixes

A review workflow (validator / honesty-trap auditor / code-quality, each
finding re-verified by a fresh agent) confirmed the core architecture sound (no
sim leak, RotS 200851 absent with an AST tripwire, talent-gating correct,
per-source join conservative, wrong non-warrior spell_ids fail SAFE). It
surfaced and we fixed:

1. **(major) Demoralizing Shout over-credit.** A −20% enemy debuff was a
   first-class coverage lever, indistinguishable from a 40-50% personal CD —
   could flip the verdict to "every hit covered". **Fix:** a `tier` field
   (major/minor). A hit covered ONLY by a minor lever is `partial` — it renders
   "🟡 partial: …", is excluded from `n_covered`, and never flips the verdict to
   "every hit had a major defensive up", but is also not counted as "nothing up".
2. **(major) Discarded continuous uptime + over-blame.** A tank who correctly
   soaked a run on Ironfur/Shield Block uptime and pressed no emergency CD hit
   the no-coverage path, which threw away the measured continuous uptime and
   said "weren't pressed". **Fix:** the no-coverage path now renders the
   continuous uptime and leads with credit ("Your continuous mitigation (Ironfur
   95%) carried this run").
3. **(minor) Physical-scope levers credited bleeds.** Incarnation / Dancing Rune
   Weapon (armor/parry) "covered" bleed ticks, which bypass armor and parry.
   **Fix:** physical-scope coverage is gated on `not is_bleed`; only an
   all-scope %DR lever covers a bleed.
4. **(minor) `SPELL_AURA_BROKEN_SPELL` field offset.** It carries an extra
   spell triple, so auraType is at field 14 not 11 — the field-11 check would
   silently never close the window (latent over-credit). **Fix:** dropped from
   the close-set (it can't legitimately fire on a defensive); `SPELL_AURA_BROKEN`
   (standard layout) is kept.
5. **(minor) `run_end=None` crash** on a truncated log, masked by the UI
   try/except (section silently vanished). **Fix:** `build_coverage_report`
   falls back to the last event timestamp.
6. Nits: singular/plural headline grammar; a YAML note that editing the registry
   requires a constants_version bump; a registry-enum-validation test.

## Tests

57 tests across `tests/test_coaching{,_windows,_render,_integration}.py`:
engine join, verdict text (no %), school scope, talent-gating, tier/partial,
bleed gate, continuous uptime, run_end fallback, the two aura-window parsers,
render branches, the RotS AST tripwire, registry enum hygiene, and a real-log
integration smoke (skips when no local log is present).

## Deliberately deferred

The full per-lever decomposition table, any single headline survivability-gap
%, an on-cooldown/"achievable" benchmark, and sim-derived uptime coaching all
stay CUT (ROADMAP honesty traps) — they would re-introduce the circular "bill
model error to the player" framing.

## Fast-follow SHIPPED — the WCL-fetched-fight path

The panel originally rendered on the local-log flow only; it now also renders
for a Warcraft Logs imported fight. `io/wcl_bridge.build_wcl_coverage_report`
builds the same `CoverageReport` from `io/wcl_api.fetch_buff_windows` (which
already returns the exact `{spell_id: [(start_s, end_s)]}` shape the core join
wants) + the analysis bundle's damage events; `render_log_analysis` gained a
`coverage_report=` param so the WCL surface hands the report to the shared
renderer (it wins over the local-log path). The in-memory join is unchanged —
truly model-independent, so it works on `calibrated: false` WCL specs too.

Engineering notes:
- **No `analysis_version` bump.** The Buffs fetch is cached on its OWN
  coverage-specific WCL key (`{target}::coverage::{spec}`), so it doesn't
  perturb the analysis bundle's cache — bumping `analysis_version` would have
  needlessly nuked the expensive local-log caches too.
- **Public-surface cost.** One extra GraphQL query. `render_log_analysis`
  re-runs on every Streamlit interaction, and `get_or_compute_with_key` only
  disk-caches on *success* — so a *failed* Buffs fetch (rate limit / 429) would
  re-fire on every rerun, ungated, exactly when the WCL budget is already
  exhausted (caught in the adversarial review below). Fix: the call site
  memoises the outcome — **including a failure, recorded as None** — in
  `st.session_state` (`_wcl_coverage_for_render`), so the fetch is attempted at
  most ONCE per (fight, spec) session. Successes are also disk-cached
  cross-session. A fresh analysis has already passed the session-cooldown +
  rolling-window gate (ADR 0001); the fetch is not inside the analyze
  single-flight slot, but the once-per-session memo bounds it.
- **Pull-time-window semantics (a benign divergence from the local path).**
  `fetch_buff_windows` credits a buff seen only via a `removebuff` with no
  in-window `applybuff` as up from fight start (it was applied pre-pull and
  *was* genuinely active) — whereas the local `parse_self_buff_windows`
  conservatively skips an unmatched remove. So the WCL path can credit a
  pre-pull precast (e.g. a Shield Wall popped just before the pull) that the
  local path leaves uncredited. This is *more* accurate, not an over-credit
  (the defensive really was up), and was confirmed not-a-defect by adversarial
  verification — but it is a real local/WCL semantic difference worth recording.
- **Conservative under-credit (the one parity gap).** The WCL path passes
  `debuff_windows_by_source={}`. The only `debuff_on_source` lever in the entire
  registry is Prot Warrior's **Demoralizing Shout** (1160, `tier: minor`, a
  −20% enemy debuff that can never flip a verdict to "covered"). There is no WCL
  source-debuff fetch yet, so a hit covered *only* by Demo Shout shows as
  uncovered on the WCL path where the local path would show "🟡 partial". This
  is a safe under-credit (the coaching invariant is "only ever under-credit,
  never over-credit"), not a misleading over-credit. A WCL source-debuff fetch
  (a `dataType: Debuffs` query keyed on the tank as applier, grouped by enemy
  GUID) is the documented follow-up for full local/WCL parity.

Adversarial review (4 lenses — honesty / correctness / WCL-cost / tests —
each finding re-verified refute-by-default): 13 raw findings, **1 confirmed**
(the failed-fetch rerun re-fire above, fixed). Two notable refutations: the
"over-credit via fabricated pull-time window" was refuted (the buff was
genuinely up at pull — accurate, not fabricated; recorded above), and the
render-guard asymmetry was judged unlikely-to-fire but fixed anyway for
symmetry with the local path's "coaching never takes down the surface"
invariant.

Tests: `tests/test_wcl_coverage.py` (12) — the join credits a buff window over a
big hit and leaves an uncovered hit uncovered; only buff-detect ids are fetched
(Demo Shout never requested); Demo Shout never credited; None on no-spec /
unknown-spec / unresolvable-actor; the `_fetch_actor_id` fallback; the Buffs
fetch is cached (no second network call); and the shared renderer surfaces a
passed-in report (real `summarize_events_direct` summary under a Streamlit
runtime).
