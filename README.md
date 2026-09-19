# simf

WoW Mythic+ tank survivability simulator. Monte Carlo engine, Midnight patch 12.1.0.

**The only free tank survivability sim in existence** — Raidbots deprecated tank metrics, SimulationCraft's own wiki says tank performance is unsupported, QE Live is healer-only, AskMrRobot is paywalled. simf fills that gap.

See [ROADMAP.md](ROADMAP.md) for current state and direction, [SPEC.md](SPEC.md) for the full design.

## Install

```bash
make install        # creates .venv, installs deps (requires Python 3.13)
```

## Run

```bash
make ui             # launch Streamlit UI at http://localhost:8501
make test           # run the test suite
```

CLI entry points:

```bash
# Sim a character + profile
simf run --character src/simf/data/characters/brutoh.yaml \
         --damage-profile m+_boss_tankbuster \
         --healing-profile m+_high_key_healer \
         --iterations 2000

# Calibrate K against your real M+ logs
simf calibrate-k --logs-dir examples --k-min 3300 --k-max 3500 --k-step 25

# Cooldown planner — when to press your major defensives
simf cd-plan <log_path>

# Why did I die? — reconstruct deaths + mitigation audit from a combat log
simf analyze-log <log_path>

# Calibrate K straight from a public Warcraft Logs report (no .txt needed)
simf calibrate-k --wcl-url <report-url> --wcl-target <name>

# Summarize how simf is actually being used (see "Usage analytics" below)
simf usage-report
```

Run `simf --help` for the full command list (`run`, `calibrate-k`, `cd-plan`,
`analyze-log`, `replay-log`, `stat-weights`, `compare`, `import-character`,
`rankings-refresh`, `ui`, `usage-report`).

## Usage analytics

simf records a small, append-only usage-analytics log at
`~/.simf/share_hits.jsonl` (override with `$SIMF_SHARE_HITS_PATH`) — read it
with `simf usage-report` for a summary, or `wc -l` / `share_hits.count()` for
a raw count.

**What's tracked, per event, allowlisted fields only** (`core/share_hits.py`):
which top-level view was reached (`view_reached`), how a character got loaded
— demo / `/simc` paste / online lookup (`character_loaded`), the spec + the
comfortable/progression key-level ceiling a verdict came back with
(`verdict_computed`), and whether a "Why did I die?" Warcraft Logs fetch
succeeded (`wcl_flow`). Every event carries a random, ephemeral per-visit `sid`
(`ui/helpers/usage_tracking.py`) so a session's events can be joined into a
funnel — never an IP, user-agent, or account identity, not as a privacy
promise but because none of that is useful for the product questions this
log exists to answer.

**Why this exists (2026-08-16):** the log started as a narrow "does a shared
link convert a stranger?" counter, deliberately built to record almost
nothing and framed as a privacy commitment to visitors. That framing was
dropped at the maintainer's explicit direction — simf's own primary user
wants real usage data to decide what to improve, not a self-imposed
restriction nobody asked for.

**This covers feature-level usage only — not raw traffic.** The maintainer's
hosted instance (simf.cc) separately uses Cloudflare Web Analytics for real
visitor/page-view counts, to cover the "how many people, not just what do
they do" half — that's dashboard-managed infra outside this repo, not
something this codebase configures.

**Two different gates, on purpose:** the original cold-share-load signal
(`_apply_share_url`, `_record_view_reached` in `ui/app.py` / `ui/load.py`)
stays gated to `SIMF_PUBLIC=1` (public-instance mode) — its whole point is
measuring whether a shared link converts a *stranger*, so the owner's own
loads would just be noise. The newer events (`character_loaded`,
`verdict_computed`, `wcl_flow`, routed through `usage_tracking.record_event`)
are **not** gated on public mode — the maintainer is simf's own heaviest
user, and that usage is real signal, not something to exclude.

## Status — v0.13.0 (2026-07-28)

> **Note:** this Status section is a point-in-time snapshot and drifts between
> updates. If a specific number below (test count, RMSE, calibration tier)
> looks stale, trust [CONTRIBUTING.md](CONTRIBUTING.md)'s Calibration section
> over this file.

- **Engine**: K=3430 (SimC DBC level-90 anchor). Prot Warrior sits at RMSE=0.080 on the ratified 16-log corpus (`characterized` tier — see below). SimC audit chain closed (F1/F4/F5/F7/F8/F11/F12/F13/F14) plus a further Shield Block/Vanguard-armor correction chain in July. Secondary-stat conversions refit to the real 12.0.7 rating→percent values + per-spec mastery scaling. ~2671 tests (pytest's collected-test count runs higher, 3002, once parametrize expansion is counted).
- **Specs (0 of 6 currently flagged `calibrated: true`)**: this isn't a regression in the math — it's a harder, more honest bar. **Prot Warrior** and **Guardian Druid** each briefly held `calibrated: true` and were downgraded back to `characterized` after clearing new cross-validation gates that didn't exist when they first got the flag: Guardian fails a leave-one-out cross-validation check (2026-07-17); Warrior passes its own-corpus LOO-CV cleanly but fails a *cross-player* check — it fits its primary log donor well but misses 15 independent players by +11% mean bias (2026-07-25), a real generalization gap whose cause is still being investigated. Prot Paladin / Brewmaster / Blood DK / Vengeance DH are modeled and *characterized* but `calibrated: false` — each over- or under-predicts a known, documented amount and the UI surfaces honest caveats. **Blood DK** is closest to the bar — the only spec to pass the cross-player validation gate, now twice on two independent Season 2 corpora including both hero-talent builds — but still blocked from `calibrated` by a structural gap: F-consistency/LOO-CV require local ACL-on combat logs, which don't exist in-repo yet for either build. See `docs/calibration.md` for what these tiers mean and CONTRIBUTING.md's Calibration section for the full history.
- **All talent-facing UI removed (2026-07-19)** — talent comparisons were Warrior-only and, per user judgment after a build-and-cut cycle, "aren't really accurate or helpful in a meaningful way." Every talent-gated engine calculation that feeds a real eHP/DR number (armor/HP/DR math, COMBATANT_INFO talent decode) stays; only the UI surfaces were cut.
- **Try-any-item BiS**: Voidcore upgrade simulator (per-slot ΔeHP for a +N ilvl bump) + slot-dialog M+ loot section (plate + leather across all 8 dungeons), per-socket gem suggester (best survival gem by ΔeHP), track-aware vault verdict (dead-choice detection + ceiling-upgrade callout).
- **Inputs**: look up current gear by character name (Raider.IO, zero-auth; or Blizzard `/equipment` with creds), `/simc` paste (equipped + bag + vault), **Warcraft Logs URL import** (public report link — no `.simc` needed), Wowhead-fallback stats lookup, Brutoh demo character, cold-share URLs.
- **Why-died + coaching**: death reconstruction + danger ranking + SB-gap diagnosis from a combat log, per-segment risk, cooldown planner, and a **model-independent defensive-coverage section** ("you ate 2 of your 8 biggest hits with no major up — pre-press there"). Coverage joins your biggest hits against which defensive aura was active — read 100% from log/WCL events, so it works on any tank, calibrated or not, on both the local-log and WCL surfaces.
- **Skill-Adjusted Verdict (Phase 2.10, complete)**: 4-rung ladder under the key-level verdict, log-inferred `← you` indicator from real Shield Block + Demoralizing Shout casts, anchored coaching callout, and a talent-aware trust caption that names the build's SB ceiling, the rage/charge floor at perfect play, and the missed-pressable gap.
- **Log replay**: COMBAT_LOG_VERSION 22; per-event absorbs; trial swaps cover full-item + enchant + gem channels; **log analysis cache** (307× warm reload); **COMBATANT_INFO hydrate** (ACL-on logs skip the SimC paste step).
- **Recent (2026-07)**: a 4-PR Shield Block correctness chain + a previously-unmodelled Vanguard armor passive took Prot Warrior's same-corpus RMSE from 0.250 to 0.073, then a real Mountain Thane hero-talent proc (KYFOTG) widened it back to 0.080 on correctness grounds; a new cross-player validation gate (run against 15 independent players' public logs) is now a promotion criterion for every spec going forward; and the full talent-comparison UI was built out (interactive tree), reviewed, and then cut (see above) inside the same two-week window.
- **Season 2 readiness (patch 12.1.0 "Curse of Ula'tek", ~Aug 11 2026)**: the Season 2 M+ dungeon pool is already catalogued (`data/dungeons.yaml`'s `season_2_catalog` — Altar of Fangs + Murder Row/Den of Nalorakk/The Blinding Vale/Voidscar Arena + Ruby Life Pools/Temple of Sethraliss/King's Rest), pending real logs to fill in numbers once the season goes live. A PTR-notes research pass (2026-07-28) also cross-checked every tank ability changing in 12.1.0 against what simf already models — see ROADMAP.md's "Patch 12.1.0 PTR — Tank Survivability Coverage Gaps" section for the resulting punch list of real, pre-existing coverage gaps it surfaced.

Tank not in the calibrated list above? File an issue. Progress there is currently blocked less by engine work and more by corpus availability — either a local combat-log donor for an under-represented hero-talent build, or enough independent players' public Warcraft Logs reports to clear the new cross-player validation bar.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for codebase orientation, how to run it locally, current calibration status, and the ground rules PRs are expected to follow. Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[AGPL-3.0](LICENSE). If you run a modified version of simf as a network service, you're required to make your modified source available to its users.

---

World of Warcraft, Warcraft, and Blizzard Entertainment are trademarks or registered trademarks of Blizzard Entertainment, Inc. in the U.S. and/or other countries. simf is not affiliated with, endorsed, or sponsored by Blizzard Entertainment, SimulationCraft, or Warcraft Logs.
