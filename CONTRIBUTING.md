# Contributing to simf

WoW Mythic+ tank survivability simulator. Monte Carlo engine, Prot Warrior primary spec, Midnight 12.1.0.
**The only free tank survivability sim in existence** (Raidbots dropped it, SimC unsupported, QE Live healer-only, AMR paywalled).

This doc is the orientation + ground rules for anyone working on the codebase — where things live, how to run it, current calibration status, and the conventions PRs are expected to follow.

**A note on PR numbers:** this repo's visible git history starts from a
single squashed commit — the day-to-day development history before the
public release lived in a private repo. PR numbers cited throughout this
doc, `ROADMAP.md`, `docs/CHANGELOG.md`, and `docs/validation/*.md` refer to
that private history and won't resolve to a browsable PR here. They're kept
as-is anyway: they're real provenance for specific engineering decisions —
what shipped, what was tried and refuted — even though the PR itself isn't
public.

**A note on review-cycle names:** comments and docs (including
`docs/ui_copy_voice.md`) sometimes cite names like `ui-craft-critic`,
`elite_tank`/`novice_tank`, `calibration-scientist`, or "the autonomous
loop." These refer to AI-assisted review personas and tooling used during
solo development, not real contributors — kept for the same provenance
reason as the PR numbers above.

**A note on memory-note citations:** backtick-quoted snake_case names and
occasional `[[double-bracket]]` links scattered through `docs/CHANGELOG.md`,
`ROADMAP.md`, and `docs/validation/*.md` (e.g.
`mastery_rating_per_pct_warrior_paladin_real_value`) refer to the
maintainer's own private, per-machine AI-agent memory notes — not files in
this repo, and not resolvable by anyone else. They're kept inline for the
same provenance reason as the PR numbers and review-cycle names above: real
evidence for how a specific number or decision was reached, even though the
note itself isn't public.

World of Warcraft, Warcraft, and Blizzard Entertainment are trademarks or
registered trademarks of Blizzard Entertainment, Inc. in the U.S. and/or
other countries. simf is not affiliated with, endorsed, or sponsored by
Blizzard Entertainment, SimulationCraft, or Warcraft Logs.

## Quick orientation

```
src/simf/
  core/         — runner.py (Monte Carlo loop), mitigation.py, policy.py, character.py,
                  metrics.py, timeline.py, key_level_verdict.py, skill_ladder.py,
                  skill_inference.py, cooldown_planner.py, normalized_score.py,
                  coaching.py (model-independent hit-vs-coverage join)
  io/           — simc_import.py, item_db.py, log_replay.py, death_analysis.py,
                  mitigation_audit.py, armory.py, raider_io.py + blizzard /equipment
                  (online gear lookup by name), wcl_api.py, wcl_replay.py,
                  wcl_bridge.py (WCL import + coverage).
                  combat_log.py: thin facade re-exporting every public/test symbol
                  from combat_log_core (line parsing) → combat_log_damage,
                  combat_log_runs, combat_log_encounters, combat_log_roles
                  (party/role/race detection), combat_log_buffs, combat_log_casts
                  (incl. parse_cast_events), combat_log_gear (COMBATANT_INFO),
                  combat_log_summary
  classes/      — protection_paladin.py, blood_death_knight.py, vengeance_dh.py,
                  brewmaster_monk.py, guardian_druid.py (per-spec policy modules)
  optimizer/    — stat_weights.py, talent_search.py, trinket_db.py, per_dungeon.py,
                  gem_suggester.py, vault_ranking.py, build_compare.py, item_upgrade.py
  ui/           — Streamlit UI, split into focused modules (Advanced toggle gate).
                  Gear surface: app.py (~1986-line thin entry/router) + gear_surface,
                  verdict, vault_panel, slot_dialog, recommend, upgrade_panel,
                  build_diff, load, state, models, marginals, widgets, item_html,
                  format_html, css, trial_banner.
                  Why-died surface: log_view.py (thin facade re-exporting every
                  public/test symbol) → log_surface (render_surface_log + local-log
                  flow) → log_wcl (WCL-URL flow) → log_analysis (orchestrator) →
                  panels (log_cd_plan, log_coaching, log_segment_risk, log_death) →
                  foundation (log_data caches, log_formatters)
  ui/helpers/   — trial_swap.py (item + enchant + gem trial channels), plus
                  aria_button, damage_school_badge, gem_panel, share_url, simc_load,
                  run_config, plotly_codex, gear_list, pareto_scatter, and the
                  upgrade-compare/curve chart helpers
  data/         — constants.yaml, dungeons.yaml, trinkets.yaml, key_level_scaling.yaml,
                  profiles/, characters/
  api/          — FastAPI scaffold (Phase 5.1, not in production)
```

## Running

```bash
make install        # create .venv, install deps (Python 3.13)
make dev            # launch the dev app from THIS checkout at localhost:8502 (on-demand; Ctrl-C to stop)
make test           # run pytest (2986 tests collected via pytest --collect-only)
simf calibrate-k                                              # ratified 16-log corpus (default, see docs/calibration.md)
simf calibrate-k --full-scan --logs-dir examples --k-min 3300 --k-max 3500 --k-step 25   # explore ALL of examples/ (opt-in)
simf cd-plan <log_path>
```

**The dev/verification target is port 8502**, launched on-demand from your checkout — **no systemd, no auto-reconcile** (that machinery was deliberately removed as needless complexity). `make dev` runs `simf ui --port 8502` straight from the current checkout, so it always reflects what's checked out with no rebuild/redeploy step. To pick up a code change, just relaunch it. To run it as a background process for scripted verification: `.venv/bin/streamlit run src/simf/ui/app.py --server.port 8502 --server.headless true` (same thing `make dev` runs), then kill the PID (`ss -tlnp | grep 8502`) when done — nothing needs reconciling afterward. `make ui` (no port arg) also launches from the checkout but defaults to :8501 — use `make dev`/:8502 for the standard dev loop.

**The live public simf.cc instance runs on separate, privately-managed infrastructure outside this repo.** Don't treat a local `/opt/simf` or `simf-public.service` (if you've set one up) as the production deploy, and don't treat simf.cc's liveness/staleness as part of local dev work — this repo is for running simf yourself locally (see Install above).

## Current state (v0.13.0, 2026-08-07, ~2986 tests)

**Phases done** (full history: `docs/CHANGELOG.md` Part 1; per-topic detail: `docs/validation/*.md`):
- **Phase 1** (entry): SimC import (equipped + bag + vault), online gear lookup by name (Raider.IO / Blizzard), COMBATANT_INFO hydrate, shareable URLs, Brutoh demo.
- **Phase 2** (core questions): key-level suitability verdict, sim-derived survivability stat weights, trinket registry, dungeon catalog, Skill-Adjusted Verdict (ladder + log-inferred tier + anchored callouts).
- **Phase 3** (Why Did I Die?): death reconstruction, mitigation audit, danger ranking, WCL API import, per-segment risk, trash-pull inference, model-independent defensive-coverage coaching (works on any tank, calibrated or not).
- **Phase 4** (all specs): 6 tank specs modeled; see Calibration below for status per spec.
- **Phase 5/6** (scale + advanced): FastAPI scaffold + Docker + CI (not in production — see Deploy below); Cooldown Planner; Normalized Tank Score. HRPS metric shipped then **removed** from user-facing surfaces 2026-07-08 (PR #314 — the metric didn't measure what it claimed).
- **UI**: single Gear/Vault surface + Why-died surface, dark-mode, per-dungeon breakdowns on both Vault and Gear cards, an eHP/eHPS mechanism-transparency ledger (composition line + mechanism gloss on every swap card), a single-iteration HP-over-time trace chart (2026-07-28). **All talent-facing UI was deliberately removed** (2026-07-19) — talent comparisons were Warrior-only and, per maintainer judgment after a build-then-cut cycle, "aren't really accurate or helpful in a meaningful way." Every talent-*gated engine calculation* that feeds a real eHP/DR number (armor/HP/DR math, COMBATANT_INFO talent decode) stays; only the UI surfaces were cut.
- **Deploy**: simf.cc runs on its own infrastructure, out of scope for local dev checkouts — see the dev/verification note above.

## Calibration (K=3430, RMSE=0.080, 16 logs — `characterized`, KYFOTG merged 2026-07-25)

**New to K / RMSE / `calibrate-k` / `calibration_tier`?** See [`docs/calibration.md`](docs/calibration.md) for the glossary, [`docs/CHANGELOG.md`](docs/CHANGELOG.md) Part 2 for the full ratification saga (Demo Shout double-count fix → Shield Block bugs → Vanguard armor passive → LOO-CV pass → cross-player downgrade → KYFOTG).

- **Prot Warrior**: `characterized`, not `calibrated`. Same-corpus fit is excellent (RMSE 0.080, LOO-CV passes) but a 2026-07-25 cross-player check (15 independent WCL players) misses by +11% mean bias — a real generalization gap, cause still unknown. `global_rmse` and every mitigation constant are unaffected by this; only the tier is downgraded.
- **Guardian Druid**: `characterized`, not `calibrated`. Passes its own single-player corpus (RMSE 0.119) but fails leave-one-out cross-validation (2026-07-17, re-confirmed on the F-consistent subset 2026-07-08).
- **Prot Paladin / Brewmaster / Blood DK / Vengeance DH**: modeled and characterized, `calibrated: false`. Each over/under-predicts by a known, documented amount with named leads (see `docs/CHANGELOG.md` Part 2 for the specific numbers per spec). **Blood DK remains closest** and has strengthened further: it's the only spec to pass the cross-player gate at all, and has now passed it TWICE on two fully independent Season 2 WCL corpora one day apart (2026-08-30: +3.2%/RMSE 0.125/83%; 2026-08-31: -1.6%/RMSE 0.087/92%, this time deliberately hero-talent-diverse — see `docs/validation/s2_cross_spec_keylevel_check_round2_2026_08_31.md`). Still blocked from `calibrated` by a structural gap, not a data gap: F-consistency/LOO-CV require local ACL-on combat logs, which don't exist in-repo for either hero talent — a WCL-only corpus can't unlock them regardless of size or how many times it passes cross-player. The earlier credit asymmetry (Deathbringer got Rune Carved Plates, San'layn got nothing) was closed 2026-07-31 (PR #453 — Blood-Soaked Ground, talent 434033/buff 434034, flat DR while standing in your own Death and Decay, replay-gated same shape as RCP; scalar bumped 5%→8% 2026-08-11, PR #472).
- **A new promotion criterion (cross-player validation) now applies to every spec going forward** — same-corpus fit alone is no longer sufficient for `calibrated: true`.

Calibration work — running `simf calibrate-k` against a bigger/more diverse log corpus, chasing one of the open leads below, or extending an existing WCL cross-player check to a new spec — is the highest-leverage way to contribute. It doesn't require Python expertise, mostly research + log analysis + interpreting a regression fit; see `docs/validation/*.md` for the format existing writeups use.

## Next priorities (read ROADMAP.md for full detail)

**From the 2026-07-28 Patch 12.1.0 research pass (run while 12.1.0 was still on PTR), which surfaced real, pre-existing survivability-model gaps independent of the patch.** Full ranked list + rationale in ROADMAP.md's "Patch 12.1.0 Coverage Gaps" section. Item 1 (Blood DK's San'layn hero-talent tanks got no credit for their always-on defensive passive while Deathbringer tanks did) **shipped 2026-07-31, PR #453** — same-shape fix mirroring the existing RCP ledger pattern; note it's live-only physical DR while standing in your own Death and Decay, not a literally unconditional passive as first assumed (see PR #453 for the corrected mechanic). Remaining: (2) Prot Warrior's KYFOTG (−8% Mountain Thane debuff) is calibrated on log-replay but never reaches forward-sim (verdict/ladder/gear recs); (3) Prot Paladin has three entirely unmodeled defensive levers (Bulwark of Order, Solace, Avenging Wrath's heal-inflation), now confirmed live and unchanged from the PTR reading (see `docs/validation/patch_1210_live_verification_2026_08_13.md`). 12.1.0 shipped 2026-08-11 — the scalar-bump PR (#472) is merged; see ROADMAP.md's "PTR scalar-bump status" for what's confirmed vs. still needs a follow-up correction.

**Carried forward, still genuinely open:**
- **VDH magic-DR residual** — unexplained after 2 of 3 leads shipped (Infernal Armor, Void Reaver's Frailty); Fel Flame Fortification held (non-universal talent, no per-player detection). Shape re-characterized 2026-08-30 (`docs/validation/s2_calibration_followups_2026_08_30.md`): near-zero at key 2, ramps sharply by key 4-6, plateaus +30-45% through at least key 16 — a key-level/damage-intensity-scaling shape, not the previously-documented "roughly constant 17-43pp" or a dungeon-specific artifact (confirmed via 4 independent same-dungeon cross-key-level comparisons, all moving the same direction). Held pending corpus data; this is a characterization update, not new corpus data unlocking the fix.
- **Brewmaster ledger remaining strands** — COMBATANT_INFO entry-id gating for the non-replay path, Stagger-absorb rescaling, synthetic-stagger over-ticking (only ~30-41% of credited Stagger ever ticks in Midnight). The physical-armor-magnitude and Master-of-Harmony leads are resolved (see `docs/CHANGELOG.md` Part 5).
- **ProtPal's remaining calibration leads** — Divine Bulwark spell-block chance, blocked-DoT absorb, the cross-spec ~5-7% run-scoped wedge (also touches Brewmaster). Distinct from today's new PTR-surfaced coverage gaps (Bulwark of Order/Solace/AW) — both are real, on the same spec.
- **Adaptive-lookahead Phase B** — `demo_shout_precast.enabled = true` promotion gated on a coupled `skill_tiers` recalibration.
- Any further VDH/Blood DK/ProtPal *calibration* chase (not coverage gaps) is blocked on corpus data that doesn't exist in-repo yet — if you have logs, see the Calibration section above.

## Season 2 / Patch 12.1.0 readiness

The full prep punch list (buckets A and B: dungeon-pool catalog staging, danger-pull cheat-sheet shell, the +25%/+25% rescale no-op check, mobile reflow, trust-strip honesty fixes, trinket VERIFY sweep, community log-corpus MVP) **closed 2026-07-27** — see `docs/CHANGELOG.md` Part 3 for the full writeup. **Patch 12.1.0 "Curse of Ula'tek" has shipped** — Aug 11 2026 (NA) / Aug 12 (EU), confirmed live, not PTR. Mythic+ Season 2 keystones start a week later, **Aug 18 2026**; until then only Mythic 0 (no-keystone) exists for the new dungeon pool. A live-patch-notes verification pass on 2026-08-13 confirmed/corrected the specific scalar values named in ROADMAP.md's coverage-gap section — see `docs/validation/patch_1210_live_verification_2026_08_13.md`. **Zone 55 is now confirmed live** (re-checked 2026-08-30, 12 days into S2 keystones) — the 2026-08-13 "no usable data yet" reading above is stale; `reportData.reports(zoneID: 55)` returns real, current-dated reports from real players. A 6-spec × 4-key-level cross-player accuracy check ran against it 2026-08-30 — see `docs/validation/s2_cross_spec_keylevel_check_2026_08_30.md`, with same-day calibration follow-ups (Blood DK corpus growth, VDH/Warrior key-level sweeps) in `docs/validation/s2_calibration_followups_2026_08_30.md`. **A full independent round-2 replication ran 2026-08-31** — see `docs/validation/s2_cross_spec_keylevel_check_round2_2026_08_31.md`: Blood DK passed the cross-player gate a second time (now the only spec to ever pass it, twice); every other spec's bias direction/magnitude reproduced closely, evidence these are real generalization gaps, not round-1 noise (Guardian Druid's magnitude roughly doubled, worth a closer look). **`dungeons.yaml`'s `season_2_catalog:` promotion shipped 2026-08-30 (PR #489)** — the live `dungeons:` catalog now serves the real, WCL-measured Season 2 pool (Season 1 archived to `season_1_catalog:`); see ROADMAP.md item 8.

## Ground rules for contributions

- No user accounts. Session state only (except the WCL-URL "Why did I die?" flow, which caches the fetched analysis on the server, keyed by report/fight — not by session — since WCL report data is itself already public). Shareable URLs for persistence.
- Usage analytics (2026-08-16, PR #482): `~/.simf/share_hits.jsonl` records `view_reached` / `character_loaded` / `verdict_computed` / `wcl_flow` events, tagged with an ephemeral per-session id — read via `simf usage-report`. These events are **not** public-mode-gated, unlike the older cold-share-link signal, which stays gated since it specifically measures stranger conversion. Full detail in README.md's "Usage analytics" section — read that before touching `core/share_hits.py` or `ui/helpers/usage_tracking.py`.
- Every new feature needs a test in `tests/`.
- Every change lands via branch + PR — never direct to master, even tiny one-file fixes.
- Routine changes can go straight into a PR; for anything major or risky (calibration constant changes, architecture shifts), open an issue or discuss first.
- All constants in `data/constants.yaml` — never hardcode numbers in Python.
- `dps_stat_weights` in constants.yaml: haste 1.40, crit 1.10, vers 1.05, mastery 0.80 (refit 2026-05-23 to Method's "Haste > Crit = Vers > Mastery" qualitative ordering; magnitudes still placeholder until Bloodmallet JSON or a Raidbots sim lands).
- Screenshot any UI change via `make screenshot` (or `scripts/screenshot.py`) and look at the PNG before calling it done — passing tests do not prove the UI works. For any CSS/layout-affecting change, also take a second screenshot at `--zoom 200` — and if a suspected layout bug doesn't reproduce at 100% in the default engine, retry with `--browser chromium` before calling it a false alarm or "known flaky" (a real `.gear-col` overflow bug, PR #279, was called unreproducible after 4 Firefox-only attempts at 100% zoom before a Chromium retest caught it).
- New/edited user-facing copy should match the "calm expert" voice and the trust-voice four-beat formula (Number → Confidence → Cause → Action) for any caveat about a shaky number — see [`docs/ui_copy_voice.md`](docs/ui_copy_voice.md).

## Blizzard API (optional)

```bash
mkdir -p ~/.simf
cat > ~/.simf/blizzard.yaml <<'EOF'
client_id: your-client-id
client_secret: your-client-secret
EOF
# get client_id/client_secret from develop.battle.net (free)
```

simf falls back to Wowhead-scraped stats when Blizzard creds aren't present — most flows work without them.

## Origin

simf started as a tool for Brutoh — a Prot Warrior on EU/Uldum pushing +14 to +18 keys — to answer questions about his own character and Tuesday vault choices. That origin still shapes the project's scope and priorities: Prot Warrior is the primary, most-scrutinized spec, and the example files in `examples/` (`brutoh.simc`, `brutoh-vault.simc`, and others) are real character exports used as the demo dataset and part of the calibration corpus.

### A note on player names in this repo

Two categories of character data appear in this repo, and only one of them
is named directly:

- **The maintainer's own alts** — Brutoh, Bruttah, Drapris, Lyney (all the
  same account/realm as Brutoh) — are self-disclosed and named directly in
  `examples/` and throughout `docs/validation/`. This is the maintainer's
  own data about their own characters, not third-party submissions.
- **Everyone else's data is anonymized**, whether it came from a local ACL
  log someone shared informally or from WCL's public API. Generic labels
  (`AnonPlayerX*`, `AnonGuardianN`, `AnonBrewmasterN`, etc.) stand in for
  real names, and no report codes, fight IDs, source/actor IDs, or
  Blizzard character GUIDs are committed — those are themselves a
  re-identification key, since anyone can open
  `warcraftlogs.com/reports/<code>` and see the real name and full raid
  roster, or look up a name+realm on the Armory.

A real third-party opt-in path exists for anyone who *wants* their name
credited: the GitHub issue template at
`.github/ISSUE_TEMPLATE/wcl-log-submission.yml` requires an explicit
consent checkbox before a submitted report is reviewed by hand and appended
to `src/simf/data/community_corpus.yaml`. That log is currently empty — no
submissions have come in through it yet.

**History:** an earlier version of this note claimed the anonymization
above was already fully enforced; a pre-public audit (2026-09) found that
untrue — several real third-party names and two real Blizzard character
GUIDs had leaked into `docs/validation/*.md`, source comments, test
fixtures, and example filenames despite the stated policy, sourced from
informally-shared local logs rather than the opt-in process. Those were
scrubbed and replaced with the generic labels above before this repo went
public. If you find a real, non-consenting player's name, GUID, or a live
report code anywhere in this repo going forward, please open an issue —
it's a bug in this policy's enforcement, not the intended state.
