# simf — Product and Engineering Roadmap
# WoW Mythic+ Tank Survivability Simulator · Midnight 12.0.5
# Written: May 2026 · Last updated: 2026-07-28 (v0.13.0)
# CONTRIBUTING.md is the current source of truth for calibration status, test counts,
# and anything actively in flight this week. This file holds the longer-lived
# plan: the North Star, the current top-priority backlog, the original Phase
# 1-6 design, and what we're deliberately not building. Full session-by-session
# history (what shipped, when, and why — including this file's own former
# "Active Triage Queue"/"Season 2 Readiness"/"Release log" sections) moved to
# docs/CHANGELOG.md on 2026-07-28, so this file and CONTRIBUTING.md stop duplicating
# and drifting on the same "current state" claims. If a specific number below
# (test count, RMSE, calibration tier) looks stale, trust CONTRIBUTING.md over this
# file.

---

## North Star

simf exists to be the only free, honest answer to the question every tank asks after a wipe: *"What should I have done differently, and what gear would have kept me alive?"* Raidbots deprecated tank survivability metrics. SimulationCraft's own wiki says tank performance is unsupported. QE Live is healer-only. AskMrRobot is paywalled. The community has been left with nothing — forced to guess vault picks, wing it on crest upgrades, and read tea leaves from DPS parses that don't model tank survival at all. simf's North Star is to give every tank — from a 10-key casual to a 20+ progression player — a free, plain-English answer to the questions that actually matter: which item to take, whether they are tanky enough for the next key level, and why they died last pull.

---

## Patch 12.1.0 Coverage Gaps (found 2026-07-28 on PTR; patch shipped 2026-08-11)

**Update 2026-08-13: 12.1.0 "Curse of Ula'tek" has shipped** (Aug 11 2026 NA / Aug 12 EU, confirmed live — see CONTRIBUTING.md's Season 2 note). Mythic+ Season 2 keystones start a week later, Aug 18 2026; until then only Mythic 0 exists for the new dungeon pool. This section's title originally said "PTR" because the research pass below ran while 12.1.0 was still on the test realm — that distinction has now resolved, so the heading has been updated, but the section's substance (the per-spec findings below) predates ship and is independent of it; see `docs/validation/patch_1210_live_verification_2026_08_13.md` for the live-patch-notes verification pass that confirmed/corrected the specific scalars named in the "PTR scalar-bump status" subsection below.

A dedicated research + codebase cross-reference pass (14 agents: 1 fetching the then-current PTR dev notes, 6 pairs of per-spec research+mapping across all 6 tank specs, 1 synthesis) read every tank-relevant PTR change and checked whether simf's engine already models the *live* version of each ability. The PTR numbers themselves were explicitly **not** encoded anywhere at the time — the project's standing rule was to wait until ship, and that's exactly what happened (see the scalar-bump subsection below for what shipped once 12.1.0 actually went live). What the pass actually surfaced is more valuable and stays true post-ship: **real, pre-existing survivability gaps in the live model**, independent of the patch, that would otherwise get silently re-discovered later. Full per-spec detail (including several "unconfirmed, verify on ship" soft flags) is in the session record; this is the actionable extract.

**Ranked by the synthesis, highest-value first:**

1. **Blood DK — San'layn hero-talent asymmetry — SHIPPED 2026-07-31 (PR #453).** Deathbringer tanks get credited Rune Carved Plates' bonus armor; San'layn tanks got *nothing* for their own physical-DR passive, Blood-Soaked Ground (talent 434033 / buff 434034, live flat 5%, PTR→8%). Fixed with the same-shape replay-gated ledger entry RCP already uses (`event.active_buffs` buff-id gating, physical-only, no stacks). Correction from the original framing found during implementation research: Blood-Soaked Ground is NOT a true always-on passive — the live tooltip and SimC's `midnight`-branch source both confirm the 5% only applies while the DK is standing within their own Death and Decay, not for the whole fight. The ledger entry credits it the same unconditional-while-buffed way RCP already credits its own window, which is the correct match to the existing convention, but the *sim doesn't separately model DnD-uptime* — a San'layn tank who never plants DnD would be over-credited relative to reality (this uptime-fidelity nuance is a smaller, separate residual from the credit-parity bug that's now fixed; not reopening as a new finding, just flagging for anyone revisiting Blood DK calibration). See `src/simf/classes/blood_death_knight.py`.
2. **Prot Warrior — Keep Your Feet on the Ground (KYFOTG) doesn't reach forward-sim.** The −8% Mountain Thane debuff (spell 438591) is modeled and calibrated on the log-replay path, but the verdict sweep / skill ladder / gear & vault recommendations — everything a live Mountain Thane player actually looks at — silently omit it, because there's no Thunder Blast press-cadence model to gate it on forward-sim. This affects the primary user's own build today. See `src/simf/core/mitigation.py` (KYFOTG lines, log-replay-gated only).
3. **Prot Paladin — three invisible defensive levers.** Bulwark of Order (self-shield, absorbs 60%→75% of Avenger's Shield damage on PTR), Solace (Consecration self-heal, 300%→375%), and Avenging Wrath (no buff/CD tracking at all, despite directly inflating Word of Glory's heal size via `wog_heal_pct_of_max_hp`'s flat corpus-average approximation). None modeled. Natural inclusions in ProtPal's next characterization pass — it's already `characterized`, not `calibrated`, and known to under-mitigate. See `src/simf/classes/protection_paladin.py`.
4. **Cross-spec structural gap: no spec models Leech, or an absorb-shield layered on top of an already-modeled DR ability.** `leech_rating` is parsed as a gear stat but never converted into any heal effect anywhere (affects Blood DK and Guardian in particular). Guardian's Matted Fur/Ursoc's Fury/Brambles all fall in the same hole — Barkskin and Survival Instincts are modeled as flat DR%, not as the stackable absorb shields they actually grant. Lower urgency, structural, worth naming once so it isn't repeatedly re-found.
5. **Correctness notes, not gaps (cheap, independent of PTR) — SHIPPED 2026-08-07.** Brewmaster's `BrewmasterPolicy.decide()` silently mis-simulated the live choice-node talent Celestial Infusion as if it were Celestial Brew; Celestial Brew's own modeled cooldown (60s → 45s, matching the live tooltip) — both fixed, see `docs/validation/phase4_brewmaster_celestial_brew_cooldown_fix_2026_08_07.md`.
6. **Prot Warrior — Brutal Vitality has no self-cap (found 2026-08-07, PTR-independent).** The live tooltip caps Brutal Vitality's own Ignore-Pain-absorb contribution at *"up to 15% of max health"* — a separate sub-cap from `ignore_pain.cap_pct_of_max_hp` (the shared 30% cap on the whole Ignore Pain shield). `policy.py:143-144` only enforces the shared 30% cap; there's no 15% Brutal-Vitality-specific ceiling at all. A real correctness gap independent of the PTR 8%→10% scalar change — worth a dedicated fix, and plausibly *part of* why `brutal_vitality`'s coded `0.05` conversion rate needed to be fit downward in the first place (see item 7 below).
7. **Prot Paladin — Improved Ardent Defender talent, entirely unmodeled (corrects a stale claim below).** The PTR "no longer cancels remaining duration on a fatal-damage save" note is about the *optional* talent Improved Ardent Defender (spell 393114, live: +10% additional DR while AD is active; PTR redesign: +20% max HP while active, doesn't end early on a fatal save) — not a bug in `runner.py`'s cheat-death logic, which was already fixed by PR #288 (2026-07-07, three weeks before this section was first written) and is confirmed correct today (`runner.py:394-406`, gates on `now < state.ardent_defender_until`, leaves `cd_until` untouched on save). ProtPal has **zero talent-detection plumbing** today (no `state.talents` references anywhere in `protection_paladin.py`, no `known_unmodeled_talents.protection_paladin` entry either) — modeling this talent means building that plumbing from scratch, then a temporary max-HP buff scoped to the existing `ardent_defender_until` window, mirroring the already-working Last Stand pattern (`state.last_stand_base_max_hp`/`state.last_stand_until`, `runner.py:234-237`). Net-new feature work, not a scalar bump — bigger than a same-day PR. Separately (found in the same pass, unrelated to PTR): `ardent_defender_cheat_death_threshold: 0.20` (`constants.yaml:2361`) is dead config with zero code references — the real cheat-death check is `state.hp <= 0` (fully lethal), not "would drop below 20%" per the tooltip; worth a validator look independent of this item.

**Season 2 live-data follow-ups (added 2026-08-30, first cross-spec check run against zone 55 — see `docs/validation/s2_cross_spec_keylevel_check_2026_08_30.md`; items 9-11 followed up the same day — see `docs/validation/s2_calibration_followups_2026_08_30.md`):**

8. **Season 2 dungeon-catalog promotion — SHIPPED 2026-08-30 (PR #489).** Replayed 3 real WCL fights per dungeon (24 total) through new `scripts/compute_season2_school_mix.py`, filled `school_mix`/`recommended_profile` for all 8 Season 2 dungeons, and ran the pre-staged `scripts/promote_season2_catalog.py --apply` to cut `dungeons.yaml`'s live `dungeons:` key over from Season 1 to Season 2 (Season 1 archived to `season_1_catalog:`). `map_id`/`par_time_ms` deliberately left unset (optional per the promotion script's own design). No per-dungeon calibration residual data exists yet for the new catalog — every dungeon reads as "unverified" in the Calibration-details popover until a future pass measures that.
9. **Blood DK — grew the in-repo corpus; found the "first PASS" wasn't hero-talent-diverse.** Following up on the 2026-08-30 cross-player PASS (n=12, mean bias +3.2%, RMSE 0.125): buff-window classification found ALL 12 of those fights are San'layn, zero Deathbringer — the "first-ever PASS any spec has posted" was a San'layn-only result, not the diverse sample the framing implied. Re-ran the original 5 Deathbringer fights at today's constants (mean bias +3.5%, RMSE 0.114) for a fresh comparison: hero-talent choice explains essentially none of Blood DK's residual (San'layn and Deathbringer within 0.3pp of each other). Also found a 3rd confirmed instance of the known armor-snapshot-pollution bug (Bone Shield up at COMBATANT_INFO capture). F-consistency/LOO-CV remain infeasible on a WCL-only corpus regardless of size (structural — needs local ACL-on logs). `calibration_tier` unchanged (`characterized`). See `docs/validation/s2_calibration_followups_2026_08_30.md` and the new `blood_death_knight_sanlayn_2026_08_30.yaml` corpus manifest.
10. **Vengeance DH — the "cliff" doesn't survive an unconfounded sweep.** A deliberate key 4/6/8 sweep (9 fights, 6 distinct dungeons, zero shared reports) found the residual is a fast ramp from near-zero at key 2 to ~+30% by key 4-6, then a plateau through at least key 16 — not a late cliff. Directly falsified the dungeon-artifact hypothesis: 4 dungeons each appearing at two different key levels all show higher bias at the higher key level (e.g. Altar of Fangs: -6.6% at kl2 → +13.7% at kl8). Recommended framing update: "near-zero at key 2, rises sharply by key 4-6, plateaus +30-45% through key 16" — a key-level/damage-intensity-scaling explanation, not a dungeon-specific one. No constant/tier change — the gap stays held per 2026-07-26 direction. See `docs/validation/s2_calibration_followups_2026_08_30.md` and `vengeance_demon_hunter_keylevel_4_6_8_sweep_2026_08_30.yaml`.
11. **Prot Warrior — the −27.9% key-2 bias mostly doesn't replicate, but a new question opened.** Expanding key-2 from n=3 (67% one dungeon) to n=6 (4 distinct dungeons) roughly halved the bias (-27.9% → -12.8%), consistent with the original reading being a small-n/dungeon-concentration artifact — the larger kl2 mean now tracks close to the already-known kl8/kl12 bias. But kl3/kl4 (n=1 each, from a search that hit WCL's newly-discovered page-25 cap before reaching kl5) read MORE negative (-34.7%/-40.5%) than the revised kl2 mean, an unresolved, not-yet-distinguished-from-noise wrinkle. Needs a second, larger (time-windowed) discovery pass targeting kl3/4/5 across more dungeons before treating either reading as settled. See `docs/validation/s2_calibration_followups_2026_08_30.md` and `protection_warrior_lowkey_2to4_2026_08_30.yaml`.
12. **Round-2 replication (2026-08-31) — Blood DK passes twice, Guardian Druid's bias roughly doubled.** A full independent re-run of the 6-spec × 4-key-level check (fresh WCL discovery, zero fight overlap with round 1 except VDH's kl2/kl16 buckets) found: Blood DK PASSES the cross-player gate a second time, now hero-talent-diverse (-1.6%/RMSE 0.087/92%, vs round 1's +3.2%/0.125/83%) — still capped at `characterized` (F-consistency/LOO-CV need local logs, not more WCL fights). Prot Warrior/Paladin/Brewmaster/VDH all reproduced closely (real, stable gaps, not round-1 noise). **Guardian Druid's bias roughly doubled** (-9.9% → -21.2%) with sign consistency newly strong (12/12 negative vs round 1's mixed spread) — worth a closer look, not yet a `calibration_tier` action. A real corpus-hygiene bug was found and fixed: WCL's `completeRaid` field is useless for M+ (uniformly `false`); `kill` is the field that actually distinguishes a finish from a wipe, and round 1's own Prot Paladin manifest had 2 undisclosed `kill: false` fights (now disclosed, not re-scored). Also fixed two real IO-layer caching bugs (`fetch_buff_windows`/`fetch_player_details` never threaded `cache_dir` through) found under 6-way concurrent WCL load. See `docs/validation/s2_cross_spec_keylevel_check_round2_2026_08_31.md`.

13. **Talent-string decoder — fixed (2026-08-31), Guardian Druid haste model now reaches the SimC-paste path.** Reopened and closed the shelved `docs/validation/talent_string_decoder_2026_07_13.md` investigation: the per-node bit format was missing an "Is Node Purchased" bit (confirmed against Blizzard's own shipped Lua source), which is what actually blocked that investigation, not the node list or SubTreeSelection width. Fixed and verified against real ground truth (Brutoh: 78/79 exact match vs. his real COMBATANT_INFO; AnonGuardian1 + 2 more real exports + AnonGuardian3: all independently confirm Elune's Chosen). A Guardian Druid SimC paste that decodes to Elune's Chosen now sets the Ironfur haste model's gate buff automatically — the "SimC-paste path... discards the talent hash... deferred" gap named in `docs/validation/phase4_guardian_haste_model_2026_06_24.md` is closed. Also flipped on `decode_simc_talent_string` for Protection Warrior (previously always `ok=False`) — real accuracy improvement for any Warrior gear-panel user, and new corroborating evidence for the still-open `protwarrior_decoded_talents_ignored_by_runner` memory. See `docs/validation/talent_string_decoder_2026_08_31.md` for the full writeup, including known narrow limitations (non-chosen-hero-tree body-node noise, tiered/Apex nodes, older pre-patch strings) — all handled by failing closed, not guessing.

**Explicitly holding, not chasing:** everything gated on player-damage-output plumbing (VDH's Fel Devastation/Feast of Souls/Frailty-self-heal/Charred Warblades/Revel in Pain, Guardian's Elune's Favored, Warrior's Fueled by Violence's real trigger) — these need the Warrior `tank_dps` pattern ported to a policy that currently has zero damage-dealt hooks (`make_policy` routes VDH with no `tank_dps` param at all), which is a real structural prerequisite, not a same-day fix. Per the 2026-07-26 direction, further VDH/Blood DK/ProtPal *calibration* work stays off the table pending corpus data the user doesn't have — the coverage-gap items above don't conflict with that hold.

**PTR scalar-bump status (2026-08-07 research pass — see the `patch_1210_ptr_scalar_prep_2026_08_07` memory for the full writeup; ship-day status updated 2026-08-13, see `docs/validation/patch_1210_live_verification_2026_08_13.md`):**
- **Merged — `patch/12.1.0-scalars` landed as PR #472 (commit `a90bc577`, 2026-08-11):** Blood-Soaked Ground 5%→8% (simple flat replace, confirmed by Warcraft Wiki's Patch 12.1.0 page, never corpus-fit so no recalibration needed) and VDH Soul Cleave heal ×1.25 (240%→300% AP coefficient, confirmed relative +25%) — both confirmed correct against the live patch notes. **Brewmaster Celestial Brew absorb needs a follow-up correction, tracked separately**: a live-patch-notes review found the shipped ×1.25 doesn't match Blizzard's actual Curse of Ula'tek notes for this ability (which describe a materially different change from the +25%-absorb-only assumption this PR was built on) — see the validation doc above for what the notes actually say; the fix itself is not done in this pass.
- **Resolved on ship day — Ignore Pain absorb (`0.216`) and Brutal Vitality (`0.05`):** these were deliberately NOT prepared pre-ship (both were undocumented day-one placeholders with no prior fit to scale from, and there was an unresolved discrepancy between Blizzard's dev notes (+25%) and a raw PTR spell-tooltip scrape (+35%) on Ignore Pain specifically). A live-tooltip check against Blizzard's own shipped patch notes now resolves both: Ignore Pain's absorb amount is **+25%** (the dev-notes reading was right, the +35% PTR scrape was not), and Brutal Vitality's conversion rate is **8%→10%** of damage dealt added to Ignore Pain. **However, this does NOT feed a `calibrate-k` recalibration as originally planned in this section** — a calibration-scientist pass proved both constants are structurally invisible to `calibrate-k`'s RMSE at ANY value, because `mitigation.py`'s log-replay path substitutes the log's own `event.log_absorbed` and never reaches the modeled Ignore Pain/Brutal Vitality chain at all. The eventual constant update (in flight separately) is correctly framed as a correctness/tooltip fix, not a recalibration — `calibration_tier` does not move either way. See the validation doc above for the full writeup, including the separate, still-open Brutal Vitality self-cap correctness bug (item 6 above) and Wowhead-vs-Blizzard-notes sourcing caveats.
- **ProtPal's Ardent Defender rework is NOT a scalar bump at all** — see item 7 above; it's net-new talent coverage, tracked separately from this scalar-prep effort. (Its live values — +20% max HP while active, no early-cancel-on-fatal-save, plus Bulwark of Order 60%→75%, Solace 300%→375%, and Avenging Wrath's new +10%/+10% buff — are now confirmed live and unchanged from what's described in item 3 and item 7 above.)

---

## Ideas Backlog

Open-ended ideas worth keeping in mind. Not committed to a phase or release — promote into a phase deliverable when an idea becomes the right next move. Add freely; prune when superseded or shipped (shipped entries move to `docs/CHANGELOG.md`, not deleted).

- **QOL pass R4 deferred findings (2026-07-28)** — real findings from a triaged review round, out of scope for a same-day fix, not forgotten (findings F-009 through F-016):
  - A real correctness bug for `validator` to pick up: a +79,344 eHP trial leaves the key-level verdict byte-identical and the reproduction hash unchanged — either the sweep doesn't consume trial-swapped gear, or the cache key omits the trial channel. Trust bug, not a QOL one.
  - Item-id-to-name resolution on the log-hydrate path ("Item #251098" instead of a real name) — needs a networked item-name resolver + caching. The disclosure half (naming that gear came from a log) already shipped.
  - Standardise the 5 trial-swap verbs ("Try" / "Trial anyway" / "Trial all" / "Equipped (trial)" / "Browse alternatives / try a swap") — real consistency gap, do as one deliberate copy pass.
  - Collapse the key-level verdict's ~12 near-identical "+2…+13 · death 0.0% · comfortable" rows — reshapes the panel's core output and its tests; own pass.
  - Promote logged Shield Block uptime out of caption type on the Why-died surface — the most actionable number on that page, needs a content-hierarchy pass.
  - Add the eHP gloss to the Gear tab's scope caption (only Vault has it today) — real gap, but re-adds vertical chrome right after 2 measured mobile-reflow rounds; wants its own 390px bounding-rect check first.
  - CSV/`st.dataframe` export for the stat price sheet and key-level ladder — feature work, touches an audited visual surface.
  - Iterations/seed/"sim this key only" controls next to Compute verdict, item-name search, side-by-side A/B build compare — feature requests, not friction removal.
- **Track-aware vault verdict — crest-affordable reachable ceiling (still open).** Dead-choice detection + ceiling-upgrade callout + paired-slot baseline shipped 2026-06-10 (PR #159). The crest-affordable half stays open: `upgrade_currencies` are parsed from the SimC export but consumed nowhere — the vault grid should default to comparing at the ceiling the player can *afford today* ("1/6 → 6/6 myth: you hold the crests" vs "reachable in ~N weeks"), gated on a verified crest-id→track→cost table that doesn't exist yet (do not guess it).
- **Deploy target — self-hosted first, paid-service later, gated on Phase 5.5.** *(Decision 2026-05-27.)* Hosting a Python sim engine for many concurrent users costs real money on every hosted option (Fly.io / Railway / Hetzner / DO all $5-10/mo minimum). Service migration trigger stays: Phase 5.5 ships (moves the heaviest per-user cost, log parsing, client-side) AND user count crosses the current host's ceiling. Picking a hosted platform before 5.5 means paying for compute 5.5 will obsolete.
- **Client-side log parsing — Web Worker, summarize-then-upload (Phase 5.5).** Pi-hosted parsing of 100-230 MB combat logs is the heaviest server compute today. Move tokenization + event extraction into a browser Web Worker emitting a compact JSON summary; once user count > 1 this is essential to keep hosting cost flat, since compute scales with users automatically. Tradeoffs: two parsers to maintain (JS + Python), the "no log ever leaves your machine unless you opt in" privacy story is a real upside.
- **Non-plate M+ loot catalog expansion.** The slot-dialog M+ loot section covers 9 trinkets + 29 plate items across all 8 dungeons; mail/leather/cloth/weapons/jewelry remain TODO. Matters because Brewmaster, Guardian, and Vengeance DH (non-plate specs) don't get the same "chase this drop" surface plate tanks (Warrior, Paladin) already have.
- **Empty-socket gem "kept, below swap bar" is a live but pre-existing design flaw (found 2026-07-28, gear-card QoL review).** `gem_panel.py`'s epsilon grace uses the paperdoll's baseline-relative "meaningful upgrade" threshold (~0.5% of total eHP — hundreds to thousands of eHP even on a modest character), but a gem's own value is always small ("tens to a few thousand eHP" per the module's own docstring). A `validator` review of the sibling "unrecognized gem" fix (below) computed this against Brutoh's real stat block: his single best possible gem (Flawless Versatile Lapis, ~3,629 eHP) sits *under* his own ~9,857 eHP swap-bar threshold — meaning **every one of his empty sockets renders "kept — below swap bar" and never as an actionable swap**, even though socketing an empty hole has no opportunity cost (unlike a real item-vs-item swap, where the bar exists to suppress noise on a real tradeoff). This silently defeats the "recommend gemming your empty sockets" feature for realistic characters. Not a regression — the pre-fix code granted this same grace unconditionally, so it predates and is independent of the `current_known` gating fix. Needs a design decision (skip the baseline-relative epsilon entirely for a genuinely-empty socket, since there's no downside to filling it) before a fix, not just a bigger number.

---

## Phases 1-6 — Original Design (written May 2026)

The six phases below are the original long-term product design, kept as
reference for *how* something was meant to work — most of Phases 1-4 have
since shipped (see CONTRIBUTING.md's current-state summary and `docs/CHANGELOG.md`
for what and when). Treat this as an architecture/design record, not a live
status tracker: a deliverable not marked "SHIPPED" inline isn't necessarily
still open, and one item (6.2 HRPS) was shipped and later actively *removed*
— check CONTRIBUTING.md before assuming anything below is still todo.

---

## Phase 1 — Zero-Friction Entry
**Theme**: Remove every barrier between "I'm a tank" and "I have an answer."
**Milestone**: A player can load their character in under 30 seconds without reading any instructions.

### Deliverables

**1.1 WoW API Character Import (Blizzard Battle.net)**
- New module `io/armory.py`: OAuth2 client-credentials flow against `https://us.api.blizzard.com/profile/wow/character/{realm}/{name}` + `/character-equipment` endpoints.
- Populate `Character` fields: stamina, strength, armor, haste/crit/mastery/vers ratings, race, spec, max HP override.
- Landing page button: "Import from Battle.net" — triggers OAuth redirect or client-token lookup, stores token in `st.session_state`.
- Fallback: if API fails, degrade gracefully to the existing manual form with pre-filled fields from whatever partial data arrived.

**1.2 WoW API Item Stat Lookup**
- New module `io/item_db.py`: cache layer over `https://us.api.blizzard.com/data/wow/item/{itemId}/item-stats`. Cache to `~/.simf/item_cache.json` (disk) with 30-day TTL.
- Vault pick tab: when user has equipped SimC items (already parsed via `simc_import.py`'s `ItemSpec.item_id`), auto-fetch stat differences instead of requiring manual Δ entry.
- Crest upgrade tab: same lookup, auto-populate Δ columns from ilvl difference via item-statistics API.
- Fallback: if `item_id` is 0 or API returns nothing, keep current manual entry columns visible.

**1.3 Shareable URLs**
- Use Streamlit's `st.query_params` to serialize character state: `?name=Brutoh&race=earthen&spec=protection_warrior&stam=12000&armor=5517&…`.
- `_char_entry_form()` reads query params on load; if valid, pre-fills and auto-submits.
- "Copy link" button in sidebar below character name. Also encodes `dmg_name`, `heal_name`, `race`, `talents` overrides.

**1.4 SimC String → Stats Auto-Population**
- Extend `parse_simc_string()` to extract equipped stat totals if the SimC export includes them (newer SimC versions emit `# stats=...` comment blocks).
- If stat totals present, skip manual entry entirely and go straight to the simulator.

**Why it matters**: *"Is there no way to accurately just sim tanking? How am I supposed to know what shoulders to wear?"* — The answer is currently: "Yes, but you have to manually look up every stat difference." That friction kills adoption.

**Effort**: M (Battle.net OAuth registration is free; API is well-documented; item cache is straightforward).

---

## Phase 2 — Answer the Core Questions
**Theme**: Give each key question a single, unambiguous answer.
**Milestone**: A player gets a "yes/no, you are tankable enough for +15" verdict in under 10 seconds.

### Deliverables

**2.1 Key-Level Suitability Output**
- New `data/key_level_scaling.yaml`: damage multiplier per key level (+10 through +20), Fortified and Tyrannical variants.
- Sim runs the damage profile calibrated to the selected key level.
- Output: `verdict_card()` — "Survivable at +15 Fortified (death rate 2.3%) — not yet at +16 (death rate 18.7%)."
- New `SimResult` field `effective_key_level_range`: lowest key where death rate < 5% (comfortable), highest where death rate < 25% (progression ceiling).

**2.2 Per-Dungeon Damage School Profiles**
- New `data/dungeon_profiles/` — 14 current M+ dungeons, each with physical/magic/fire/shadow split sourced from WCL aggregates.
- Extend `DamageProfile` with `dungeon_id: str | None` and `school_mix: dict[str, float]`.
- Sidebar dungeon selector auto-loads the school mix. "Generic M+" remains available.
- This directly improves armor vs. versatility tradeoff answers: Stonevault (magic-heavy) weights vers more; Ara-Kara (physical-heavy) weights armor more.

**2.3 Trinket Registry**
- New `data/trinkets.yaml`: 15+ current-season defensive trinkets encoded as `HealingExternal` entries (absorb, dr_cooldown, or heal) or flat stat bonuses.
- New module `optimizer/trinket_db.py` with A/B compare integration: sim runs with trinket effects baked into `HealingProfile.externals` vs. a stat-bonus delta on `Character`.
- *"Trinket evaluation… currently impossible, comes down to what you like."* — This is the only tool that will answer this quantitatively.

**2.4 Parallelize the Sim Engine**
- Replace the inner `run_simulation()` loop with `concurrent.futures.ProcessPoolExecutor`, chunk size `iterations // n_workers`. Each worker gets a sub-seed range, returns `list[IterationResult]`.
- `_aggregate()` stays unchanged — it already operates on a flat list.
- Target: 4× speedup on 4 cores, reducing stat-weight sweeps from ~40s to ~10s.
- Benchmark NumPy vectorization of `generate_events()` as an alternative/complementary path.

**2.5 Additional Damage Profiles**
- `m+_council_boss.yaml`: multi-target tank-buster with overlapping damage windows.
- `m+_dungeon_composite.yaml`: probability-weighted mix of existing profiles — the default "full dungeon" profile.

**2.7 Sim-Derived Survivability Stat Weights**
- *Problem.* `core/marginals.py:ehp_marginals()` is a closed-form derivative of `effective_hp = max_hp / ((1 − armor_dr)(1 − vers_dr))`. By construction it returns zero for haste / crit / mastery / strength and ignores self-sustain (Death Strike, Soul Cleave, Word of Glory, Brutal Vitality, Frenzied Regen). Every gear surface, every vault verdict, every slot trial uses these marginals as the source of truth for "+X eHP." Vers is structurally over-credited; self-heal specs are structurally under-credited. The product's central claim ("the only honest answer to *what gear would have kept me alive*") fails on its highest-trust number.
- *Fix.* Replace `ehp_marginals(char)` with `survivability_marginals(char)` — sim-derived per-stat eHP equivalents.
  - **Metric:** ETMI-12 (TMI with externals, `include_externals=True`, 12s window). Subtracts `heal_timeline` from `damage_timeline` at bin level, so self-sustain registers. Already implemented in `core/metrics.py:compute_tmi`.
  - **Calibration.** Stamina has an exact closed-form `dEHP/dstam = hp_per_stam / ((1 − armor_dr)(1 − vers_dr))`. After running the perturbation sim, anchor the conversion: for every stat `x`, `eHP_per_unit_x = (sim_weight_x / sim_weight_stam) × closed_form_dEHP_per_stam`. Units stay in eHP. By construction the stamina marginal equals the paper-math answer; armor and versatility are independent sanity checks.
  - **Shape preserved.** `{stat: {"p": ..., "m": ...}}` so `optimizer/per_dungeon.py:_score_for_school_mix` and all downstream consumers keep working bit-identically. Compute against an all-physical and an all-magic profile.
  - **Cache.** Keyed on `(class_spec, talents, stats_signature, K, constants_version)`. Background-compute on character load. Picker can fall back to closed-form marginals if cache is cold, with a `recalibrating…` hint.
- *Defense for the "+820 eHP" label.* "The sim absorbs 820 more units of raw damage before your HP would crash, calibrated against the one stat whose paper math is exact (stamina)." Closed-form vs sim agreement on armor + vers is asserted as an automated test (±20%).
- *Scope discipline.* No per-dungeon split for this phase — keeps the `{p, m}` API surface. `marginals[stat][dungeon_id]` is a future refactor when per-dungeon school weights matter more than self-heal does.
- *Why not stat_weights.py.* `optimizer/stat_weights.py` exists but defaults to `p99_10s_window` (post-mit, pre-heal) and only covers the 4 secondaries. Also blind to self-heal. Fix it in the same change.

**2.10 Skill-Adjusted Verdict** *(promoted from IDEAS.md 2026-05-19; SHIPPED 2026-05-20/21 via PRs #9 / #10 / #11 / #16 (v3 DS) / #17 (SB ceiling) — see v0.11.5 / v0.11.6 / v0.11.7 ship notes above. Engine modifier, log inference (SB + Demo Shout), anchored coaching callouts, talent-aware ladder matching, and build-floor bottleneck attribution all landed.)*
- *Problem.* The verdict today says "+17 survivable" as if play quality is fixed. Same gear at the same key has wildly different death rates depending on whether the player presses their buttons. The current trust strip is honest about model uncertainty but silent on player-side assumptions, so the verdict implicitly promises optimal play.
- *Fix.* Output a verdict per skill tier instead of one number. 3–4 tiers — "missed half your buttons / use defensives reactively / consistent CD discipline / optimal play." Each tier maps to a multiplier on the existing levers already measured by the engine: SB uptime %, `sb_rage_starved_s`, `sb_charge_limited_s`, planned-vs-heuristic CD usage. Bin those and surface the band along with the verdict — *"At +14 you survive 97% with optimal play; 78% if you forget defensives; 45% if you panic-press."*
- *Engine.* No new math — `IterationResult.shield_block_uptime_pct` + `sb_rage_starved_s` + `sb_charge_limited_s` already exist. Add a skill-modifier knob to the policy that scales SB/IP press-rate downward in 3 tiers. Run the existing Monte Carlo loop 3× per verdict (once per tier).
- *UI.* Verdict card grows a tier toggle ("If I play …") or shows a small ladder beneath the headline.
- *Why it matters.* Honesty. A tank looking at "+17 survivable" today doesn't know which version of themselves the model assumed. Skill-tier verdict turns the question from "can I do this?" into "what level of play do I need to reach to do this?" — actionable.
- *Effort.* M.

**Why it matters**: *"I just don't know what to do!"* (vault pick) and *"They really should add M+ trash pull sim somehow."* — The key-level threshold turns a number (ETMI-12 = 23 000) into a decision (+14 is fine, step up after next Gilded upgrade).

**Effort**: L (key scaling research + trinket registry data collection is the bulk; parallelization is a clean internal change).

---

## Phase 3 — "Why Did I Die?"
**Theme**: Make the log analysis tab answer the actual question tanks ask after a death.
**Milestone**: A player uploads a log and gets a ranked list of "this killed you" with concrete mitigation suggestions.

### Deliverables

**3.1 Death Reconstruction**
- Extend `io/log_replay.py:load_replay()` to parse UNIT_DIED events. `ReplayData` gains `deaths: list[DeathEvent]`, where `DeathEvent` has `time_s`, `last_ability`, `hp_before`, `sequence: list[DamageTakenEvent]` (5s window preceding death).
- New `io/death_analysis.py:reconstruct_death()`: for each death, extract the contributing hit sequence, compute cumulative damage in 3/5/10s windows, identify the fatal ability.
- UI: "Death timeline" expander in Log analysis tab — mini HP trace for the 30s leading up to each death with individual hits annotated.

**3.2 Mitigation Uptime Diagnosis**
- New `io/mitigation_audit.py:audit_replay()`: re-run replay with `MitigationState` instrumentation. For each event: record `sb_covered`, `ip_absorbed`, `demo_shout_active`.
- Output panel: "Mitigation uptime audit" — `st.dataframe()` with columns `Ability | Hits | SB-covered | IP-absorbed | Demo-shout | Unmitigated`. Color gradient on Unmitigated column.
- *"Shield Block / mitigation uptime diagnosis"* — ranked #5 user need; tanks see exactly where SB coverage lapsed.

**2.6 UI Simplification (Progressive Disclosure)**
- The current UI has 8 tabs with many independent knobs visible simultaneously. New users face decision paralysis before running a single sim.
- Collapse advanced options behind `st.expander("Advanced")` in each tab: damage profile, healing profile, iterations, seed, metric selector. Defaults should be sensible enough that most users never open it.
- Sidebar: group into clear sections — "Character" (stats/import), "Encounter" (key level, dungeon, affixes), "Simulation" (iterations, seed). Hide "Simulation" behind an expander by default.
- Audit each tab for redundant or rarely-used widgets; remove or demote anything that doesn't serve the primary question the tab answers.
- Consider merging low-traffic tabs (e.g. Race optimizer → collapse into Run tab as a comparison option).

**3.3 Per-Ability Danger Ranking**
- Extend `LogSummary` with `ability_spike_score: dict[str, float]`: `max_single_hit / mean_hit × total_damage_share`. Higher = more dangerous.
- Danger ranking table sorted by spike score, annotated with blockable/unblockable, physical/magic, avoidable/unavoidable.

**3.4 Shield Block Uptime Root Cause**
- New `sb_starved_time_s` on `IterationResult`: time in SB gaps where a charge was ready but rage < 30.
- UI: "SB diagnosis" — "X% of your SB gaps were rage-starved (more physical pulls for rage) vs Y% charge-limited (consider Anger Management)."

**3.5 WCL API Integration — SHIPPED 2026-05-26 (URL-flow cluster PRs #84-#87)**
- New `io/wcl_api.py`: client-credentials OAuth2 against WCL GraphQL API. Query `report.fight.damageTaken` events.
- "Import from WCL" button in Log analysis tab. User pastes report URL + fight ID; simf fetches and converts to `list[DamageEvent]`.
- Credentials stored in `~/.simf/wcl_config.yaml` or env vars `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`.
- URL-flow refinements landed 2026-05-26 across 4 PRs: **#84** tank-first character dropdown (the report's tanks float to the top of the actor picker); **#85** honor `?source=N` in share URLs + clearer "no events" empty-state copy; **#86** pass actor ID through `analyze()` + fix realm-suffix matching so `Brutoh-Uldum` resolves; **#87** plumb `spell_id` + `npc_id` through the damage-taken pipeline so Wowhead Power tooltips fire on abilities AND NPCs (not just URLs).

**3.6 Per-boss + per-trash-gap risk surfacing (Phase A) — SHIPPED 2026-05-15 on `feature/per-boss-risk` (commit `05cb82d`, unmerged)**
- `combat_log.py`: `parse_encounters()` pairs `ENCOUNTER_START/END` events into boss windows; `segment_run()` slices a run into ordered boss + trash segments; multi-pull bosses get `(try N)` / `(kill)` suffixes.
- `log_view.py`: `render_per_segment_risk()` walks segments in time order and auto-expands the ones containing deaths. Each death surfaces top-3 fatal-window abilities with school + source mob/boss + % share of the 5s killer window. Run-selector for logs with multiple back-to-back keys.
- Validated against Brutoh's MGT +12 log: 3 deaths bucket as 2 trash + 1 boss (Degentrius try 1); top-3 killers populated per death.
- Scope: descriptive path only — anchored to the user's uploaded log. No engine math touched, no schema changes.
- Tests: `tests/test_run_segmentation.py` — 9 cases (synthetic + integration against the real MGT log).

**3.7 Trash-pull inference (Phase B) — SHIPPED 2026-05-16 session 7 (commit `05da50f`)**
- `io/pull_segmentation.cluster_trash_pulls()` slices a trash `RunSegment` into `TrashPull` objects using two rules: combat gap ≥ 8s closes one pull and opens the next; a pull needs ≥ 3 distinct hostile source names to be named (solo mob taps dropped).
- `TrashPull` is frozen + carries `sources: frozenset` so identical packs across runs fingerprint to the same hash — useful for the future community-corpus aggregation in 6.5.
- UI: `render_per_segment_risk` lists the inferred pulls inside expanded trash segments.
- Tests: 9 in `test_pull_segmentation.py` — 8 synthetic unit cases + one integration against the MGT log that pins the Gemellus run-up shape.
- Unblocks 3.8 (predictive per-pull risk) once the dungeons.yaml per-pull schema and source-fingerprint mapping land.

**3.8 Predictive per-pull risk (Phase C) — still blocked, blocker now re-scoped**
- The user-facing payoff: "at +14 MGT, the Voidling pack before Selin will kill you 18% of the time." Distinct from 3.6 — 3.6 is descriptive (your log → what killed you); 3.8 is predictive (your character → where you'll likely die).
- **Remeasurement 2026-05-23** (`docs/validation/magic_mit_gap_remeasurement_2026_05_23.md`): director asked the validator to remeasure the magic-mit blocker against the post-F14 / post-mastery-chain / post-Phase-A-B engine, since the blocker was first measured pre-F14. Verdict: **DEFER, blocker not closed.** Magic gaps remain 10-13pp under-mit on every non-frost school (p90 12-25pp). NEW finding: physical mitigation under-mit by **−12.79pp with σ≈1pp across 18/18 runs** — same direction as magic, points to ONE structural missing layer. K=3430 / RMSE 0.065 global calibration is not contradicted because per-event gaps aggregate against post-mit DTPS where healer absorbs + party DR + Phalanx + BfI compensate at the run level; per-segment they DON'T, which is why Phase C needs them modelled.
- Blocked on (rescoped):
  1. **Engine fixes 3.9.1–3.9.5** (below) — close the per-segment gap from ~−13pp to <5pp.
  2. **`dungeons.yaml` per-pull schema.** Today it's one `school_mix` per dungeon. Predictive per-pull risk needs `pulls: [{id, label, school_mix, expected_duration_s, danger_score}]` — hand-labelled or auto-mined from collected logs.
- Effort: M (after blockers clear).

**3.9 Per-school mitigation gap — investigation (remeasured 2026-05-23)**
- **Latest measurement (2026-05-23, `docs/validation/magic_mit_gap_remeasurement_2026_05_23.md`):** decomposed per-event mitigation residuals by school across 18 Brutoh M+ runs using `scripts/per_school_mitigation_gap.py` against the post-F14 engine. Verified by independent reproduction (validator + author).
- **Per-school p90 absolute gaps:**
  - physical: −12.79pp weighted, σ≈1pp, 18/18 runs under-mit
  - shadow / fire / arcane / nature / holy: −10 to −13pp weighted, p90 12-25pp
  - frost: +1.82pp weighted (mixed direction)
  - bleed: +37pp over-mit on the 117 events sampled (engine treats bleeds as armor-mitigated; they bypass armor in-game)
- **Pre-F14 magic-mit blocker (the original 3.9):** the May 15 finding measured magic at 10-30pp under-mit. Today's remeasurement shows magic at 10-13pp p90, similar magnitude — but the physical floor of −12.79pp with σ≈1pp is the NEW signature, pointing to one structural missing layer rather than per-ability spell-data drift. Original 3.9 SUPERSEDED by this entry.

**3.9.1 Brace for Impact stack-up in replay** *(engine fix, expected −4 to −6pp on physical)*
- `talents.brace_for_impact` is modelled (`shield_slam_stack_dr: 0.01` × 4 stacks = 4% phys DR at max stack), but the replay-mode path doesn't accumulate stacks from log Shield Slam casts. Today's audit script approximates by pre-warming to `bfi_stacks_max`; the runner replay path needs the same fix to credit BfI properly per event. Brutoh runs `brace_for_impact` in his M+ loadout — leverage is universal.

**3.9.2 Party DR auras generalised across schools** *(engine fix, expected −3 to −5pp phys + −5 to −8pp magic)*
- `mitigation.py:246` currently models `party_magic_dr` (magic-only, default 0.05 opt-in via `state.party_magic_dr_active`). Real M+ groups carry: Shaman Earth Shield + Ancestral Vigor (all-school), Druid Symbol of Hope (all-school), Holy Priest Power Word: Barrier (magic), Devotion Aura (magic), etc. Generalise the knob to `party_dr_by_school: dict[school, float]` keyed by aura name; populate when the log shows the aura's `SPELL_AURA_APPLIED` on the tank. Phase C will then see real per-segment DR, not a flat magic-only multiplier.

**3.9.3 Bleed armor-bypass** — *SHIPPED 2026-05-23 (PR #49, commit `f71b471`); see entry above in the recently-shipped block. WoW bleeds bypass armor; the engine now skips armor DR when `event.is_bleed` (new `core/bleed_detection.py` + flag on `DamageEvent`, wired through `log_replay.py`). Closed the +37pp bleed bucket entirely. K calibration sweep: best K 3400→3375, canonical K=3430 RMSE 0.064→0.067, K stays at 3430.*

**3.9.4 Bonus armor inference from COMBATANT_INFO** *(engine fix, ~2pp phys)*
- `iter_combatant_info` exposes the player's actual in-game armor stat at run start. Today simf uses `armor_from_gear` from the SimC YAML, which can drift from the log-era value (Brutoh's 989-vs-931 shield-armor drift is the documented case). Adding a `bonus_armor` knob on Character + populating it from COMBATANT_INFO closes the ~2pp unmodeled-armor floor.

**3.9.5 Per-source arcane immunity (Algeth'ar)** *(engine fix, dungeon-local)*
- Per-dungeon table shows Algeth'ar arcane is the worst residual (−14.0pp across 3 runs). The Echo-of-Doragosa-style debuff-immunity list already exists for Demoralizing Shout (`active_mitigation.demoralizing_shout.immune_sources`); the same mechanism needs an arcane-debuff analogue (Crystal Constructs apply an arcane DoT immune to certain DR). Per-dungeon hand-labelling — cheap.

- Blocks 3.8 (predictive per-pull). Surface in the UI inline on magic-heavy segments — engaged-tank panel feedback: *"flag it inline on magic-heavy pulls, hiding it is worse than naming it."*

**3.10 Dungeon catalog completion — SHIPPED 2026-05-16 session 7 (commit `09f622b`)**
- `dungeons.yaml` now lists 8 entries — added `magisters_terrace` (map_id 2811) with `school_mix` derived from the +12 validation log (55% physical / 30% shadow / 10% arcane / 5% fire) and `calibration_gap_pct: +5.2` post-Phalanx-loadout-fix.
- Remaining S2 dungeon audit against logs in `examples/` is still pending — non-blocking.

**3.11 Log target-name resolution — SHIPPED 2026-05-16 session 7 (commit `09f622b`)**
- `SimcImport` now threads `server` and `region` through into `char_data` on both demo-load and SimC-paste paths. `_render_surface_log` assembles `Name-Server-Region` (e.g. `Brutoh-Uldum-EU`) and passes it as the default target-name field — bare `Brutoh` previously matched zero combat-log events.

**Why it matters**: *"DPS have the luxury of just looking at parses. There's no great metric to determine how well you're doing as a tank."* — This phase gives tanks exactly that metric, grounded in their actual log data.

**Effort**: L (WCL API is GraphQL; death reconstruction requires careful event-window handling; mitigation audit is a new replay pass).

---

## Phase 4 — All Tanks, All Specs
**Theme**: Every tank spec gets a first-class model.
**Milestone**: Blood DK, Vengeance DH, and Protection Paladin each calibrate within ±15% RMSE on at least 2 real logs.
**Status (2026-07-04, updated)**: **Guardian Druid reached `calibrated: true`** (#219, 2026-06-29) — ahead of the originally-named three, on the strength of a 16-log single-player corpus. 2 of 6 specs now calibrated (Prot Warrior + Guardian). The three named in the milestone remain open, all with real progress the same day: VDH's physical gap was closed in PR #143 (Metamorphosis armor + Demon Spikes + Demonic Wards −12%, over-prediction +102-149% → +43-73%) and a 2026-07-04 follow-up shipped Painbringer + `party_dr_by_school`, narrowing to +37.5/+62.0/+36.5% (RMSE 0.468), but a large unidentified magic layer still blocks `calibrated: true` (`docs/validation/phase4_vdh_painbringer_2026_07_03.md`). **Blood DK got its first-ever characterization** (PR #264): real Bone Shield mechanic + Rune Carved Plates + IBF CD fix, canonical-K RMSE 0.483→0.152, **4/5 runs within ±15%** — the closest of the three to `calibrated: true`, blocked mainly by having only one hero-talent build observed (`docs/validation/phase4_blood_dk_characterization_2026_07_03.md`). **Prot Paladin's Ardent Defender parameters were corrected 2026-07-03** (gap grew with key level, +19.8/+1.6/+29.7/+47.5% RMSE 0.297) and its named holy-power-economy lead **resolved the same day as Blood DK's characterization** (PR #265): WoG is mana-funded not Holy-Power-funded, closing the fake key-level gradient to +14.7/-5.3/+28.2/+9.3% (RMSE 0.168) — still `calibrated: false` (`docs/validation/phase4_protpal_holy_power_economy_2026_07_04.md`).

### Deliverables

**4.1 Blood Death Knight**
- New `classes/blood_death_knight.py`: `apply_blood_dk_mitigation()`.
- Bone Shield stacks: physical DR per stack (`MitigationState.bone_shield_stacks`); each melee hit consumes one stack.
- Death Strike: heals % of damage taken in the last 5s (`recent_damage_total()` already tracked in `MitigationState`).
- Vampiric Blood: emergency CD slot (reuses `shield_wall_until` pattern). +30% max HP + self-healing increase.
- `constants.yaml`: new `specs.blood_death_knight` block.

**4.2 Vengeance Demon Hunter**
- New `classes/vengeance_dh.py`: Demon Spikes (physical DR + parry window, like Shield Block with `demo_spikes_until`), Metamorphosis emergency CD, Frailty self-heal (Soul Cleave → `HealingExternal` scheduled heal).
- `parry_rating` scaling in `character.py` gated behind `class_spec`.

**4.3 Protection Paladin**
- New `classes/protection_paladin.py`: Shield of the Righteous (physical DR window consuming Holy Power — add `holy_power` resource to `MitigationState`), Ardent Defender (20% constant DR + cheat death once per 2 min), Sentinel stacking absorbs, Consecration average DR uptime.
- Highest-played M+ tank spec; highest adoption impact.

**4.4 Brewmaster and Guardian Improvements**
- Brewmaster: proper Stagger DoT tracking. `MitigationState` gains `stagger_pool: float` draining at `stagger_drain_rate` per second. `PurifyBrewPolicy` purifies when pool > 40% of max HP.
- Guardian: Rage of the Sleeper, Tooth and Claw absorbs, Incarnation emergency CD, dodge from Agility scaling.
- Both: separate `talent_loadouts` entries in `constants.yaml`.

**4.5 Spec Gating Lift in UI**
- `_SPEC_LABELS` and `_SPEC_OPTIONS` in `app.py`: add remaining specs.

**Why it matters**: *"Tank survivability rankings… I miss them."* — Rankings require all specs. A tank considering spec-swapping has no tool to compare "is Blood DK or Prot Warrior tankier for this key level?"

**Effort**: XL (each spec requires careful mechanical research, new `MitigationState` fields, policy logic, and ≥2 calibration logs per spec).

---

## Phase 5 — Scale
**Theme**: Make simf deployable, fast, and maintainable at production quality.
**Milestone**: A public URL exists. Stat-weight sweep completes in < 5 seconds. Zero manual deployment steps on push.

### ⭐ Re-planned 2026-06-13 (user-ratified) — first public URL = Cloudflare Tunnel + read-only mode

A 6-agent brainstorm (director · external/competitor UX · Pi-security · PM · architecture audit) + the user re-defined "Phase 5 working" as the **smallest reliable path** to: *a stranger clicks Brutoh's `?build=`/`?compare=` diff-card share link on a free public HTTPS URL and gets a correct diff card* — so the share wedge (#162–#167) pays off. Not a hosted, horizontally-scalable deploy. The original 5.1–5.5 deliverables below were scoped for that larger goal; most are **cut from the first URL**.

**Shipped — the code-side launch set:**
- **#169** `SIMF_PUBLIC=1` read-only mode — forces read-only, hides the log-upload surface + online gear lookup (removes the OOM + SSRF surfaces), title-only header.
- **#170** single-flight sim guard (one stranger's sim can't GIL-wedge the Pi) + Blizzard `region` SSRF allowlist (fails closed before host interpolation).
- **#171** `simf ui --public` tunnel flags (WebSocket/CORS/XSRF off — required behind a reverse proxy or the app renders blank — + 25 MB cap, hidden toolbar) + a Makefile target and deploy runbook (since moved to privately-managed deploy tooling, not part of this repo).
- **#172** privacy-safe cold-share-load hit counter (`core/share_hits.py`, `~/.simf/share_hits.jsonl`, no PII) — the one-arrow signal.

**CUT from the first URL** (not the unlock; revisit only when traffic/demand forces it): **5.1** FastAPI/httpx cutover (same in-process `run_simulation` under one GIL — moves the problem), **5.2** vectorization / "<5s sweep" (the marginals disk cache #164 already makes the 2nd+ load instant), **5.5** client-side log parsing (only needed if the log surface is exposed — it isn't), paid hosting / `fly.toml`, user accounts.

**Operator go-live steps (historical — executed via privately-managed deploy tooling, not part of this repo):** rotate the GitHub PAT out of the git remote URL · buy a domain · run `cloudflared` · launch the locked-down public UI (with `SIMF_PUBLIC_HOST`) · fire one distribution arrow. **Fast-follows gated on the hit counter showing real clicks:** a `ProcessPoolExecutor` to kill GIL head-of-line blocking, and static pre-render of Brutoh's share-card so the viral path costs zero compute (the Raidbots/Bloodmallet pattern).

### Deliverables (original hosted-deploy plan — see the re-plan above for what actually shipped)

**5.1 FastAPI Backend**
- New `api/app.py`: endpoints `POST /simulate`, `POST /stat-weights`, `GET /profiles/damage`, `GET /profiles/healing`, `GET /item/{item_id}/stats`.
- Pydantic v2 request/response schemas. `CharacterRequest` mirrors `Character`; `SimResultResponse` mirrors `SimResult`.
- Streamlit switches from direct `run_simulation()` calls to `httpx.post("/simulate")`. UI becomes stateless and horizontally scalable.

**5.2 NumPy Vectorization of the Core Loop**
- `timeline.py:generate_events()`: structured NumPy array instead of Python list of dataclasses. dtype `[("time_s", float64), ("raw_amount", float64), ("school", "U16"), ...]`.
- `runner.run_simulation()`: vectorized batched operations over the event array. Per-event mitigation branching profiled first; apply array operations where branches are uniform.
- Target: single-iteration 0.5 ms → 0.1 ms; 2 000-iteration run 1s → 0.2s.

**5.3 Docker + CI/CD**
- `Dockerfile`: `python:3.13-slim`, install `.[ui]`, Streamlit entry point.
- `docker-compose.yml`: `simf-ui` (port 8501) + `simf-api` (port 8000).
- GitHub Actions `.github/workflows/ci.yml`: `pytest`, `ruff check`, `mypy --strict` on push. Docker build on tag.
- Deploy target: Fly.io or Railway. `fly.toml` in repo root. `make deploy` target in `Makefile`.

**5.4 Constants Version Management**
- `constants_version: int` in `constants.yaml`, bumped on every mechanical change.
- `SimResult` embeds `constants_version`. Streamlit cache key extended to include it so stale results auto-expire on patch updates.

**5.5 Client-Side Log Parsing**
- Web Worker module (`web/log-parser.js` or similar) parses raw `WoWCombatLog-*.txt` line-by-line in the browser. Emits the same JSON schema `death_analysis.py` and `log_view.py` already consume (events + COMBATANT_INFO + segment boundaries + per-segment damage-taken digest).
- Pi receives compact JSON (kB–MB) instead of raw text (10s–100s of MB). `combat_log.py` retained server-side as the canonical parser and for validation. `log_view.py` switches its hot path to consume the uploaded summary directly.
- Opt-in toggle "Save this analysis / contribute to calibration" uploads the parsed JSON. Without the opt-in, nothing leaves the user's machine. With it, the Pi caches the JSON for share-URL cold-load and adds to the K-calibration corpus (Phase 6.5).
- Streamlit integration via a custom component or `components.html` shim that hooks the Worker and pushes the resulting JSON into `st.session_state`. Falls back to server-side parse on browsers without Worker support or for users who explicitly disable client-side compute.
- Parity test: a fixture-driven test runs both parsers (JS via `node`, Python via `combat_log.py`) on the same log and asserts identical JSON output. Required to prevent format-drift between the two implementations.

**Why it matters**: Streamlit's `st.cache_data` is per-session. A deployed simf with concurrent users runs all sims in-process, blocking. FastAPI + worker pool is the correct architecture. Vectorization makes stat-weight sweeps instant enough to run on every page load. Client-side parsing (5.5) moves the heaviest per-user cost off the Pi entirely so hosting cost stays flat as users grow.

**Effort**: L (FastAPI port is mechanical; Docker straightforward; vectorization needs profiling first).

---

## Phase 6 — Advanced
**Theme**: Features that no other application on earth provides for tank theorycrafting.
**Milestone**: simf has features that even AskMrRobot does not offer for tank survivability.

### Deliverables

**6.1 Cooldown Planner**
- New "CD Planner" tab: user specifies a damage timeline (log upload or dungeon profile), simf finds optimal CD placement.
- Engine: extend `ActiveMitigationPolicy` with `CooldownPlanOverride` — a list of `(time_s, ability)` tuples that fire at exact times.
- Optimizer: brute-force over CD placements (Shield Wall: 1 per 240s; Last Stand: 1 per 180s). 5–10 candidate placements × iterations = 25–50 sim calls. Output: recommended CD timeline with "expected HP at each CD fire."

**6.2 HRPS Metric (Healing Required Per Second) — REMOVED from user-facing surfaces, 2026-07-08 (PR #314).** Shipped as originally spec'd below, then found broken in practice: `BASELINE_HRPS` was a stale constant that saturated Tank Score's HRPS component near-constant, and an independent check found `mean_hrps` nets out not just self-sustain but the sim's own modeled healer output too — running ~12x smaller than a real healer's throughput and *decreasing* at higher key levels, unfit for any "which build is easier to heal" claim. Original spec follows for reference; do not re-ship without fixing that measurement problem first.
- `hrps = total_damage_taken_after_self_sustain / duration_s` — how much healing per second the healer must provide.
- Computed per-iteration in `runner.run_simulation()`, aggregated as `mean_hrps` on `SimResult`. Exposed in all output tabs.
- Answers "which build is easier to heal?" — a question no current tool answers. Lower HRPS = healer does more DPS = faster key.

**6.3 Normalized Tank Score**
- `normalized_tank_score(result, key_level) -> float`: composite of `(1 − death_rate) × 0.40 + (1 − hrps_normalized) × 0.30 + (1 − mean_dtps_normalized) × 0.30`. Normalize against level-appropriate baseline from `data/key_level_scaling.yaml`.
- The tank equivalent of a DPS parse — a single number that lets tanks compare specs, builds, and gear without understanding ETMI-12.

**6.4 Interactive DPS/Survivability Pareto**
- Extend the existing Pareto scatter (`ui/helpers/pareto_scatter.py`, rendered from `ui/slot_dialog.py`) to a full interactive frontier: X = survivability value, Y = DPS value, both per 1 000 rating.
- Add items (trinkets, gems, enchants) as additional scatter points. Pareto-frontier highlighting; items below the line are dominated choices.
- Answers: "Survivability/DPS tradeoff visualization" (ranked #7 user need).

**6.5 Community Log Corpus**
- Opt-in anonymous log sharing (WCL link + character stats) for calibration.
- `calibrate-k` CLI (`cli.py`) runs against the growing corpus.
- Enables per-dungeon K refinement as the meta shifts with each patch.
- **Decision 2026-07-06 (Top-5 #5, Active Triage Queue):** build on WCL
  report links, not raw log uploads — sidesteps Phase 5.5's browser-parser
  privacy problem entirely (a WCL link never exposes the raw log's party
  roster) and reuses the `--wcl-url` / `calibrate_spec_from_wcl.py` path
  already shipped 2026-05-27. **No longer gated on Phase 5.5** — the two
  phases can ship independently. MVP scope (not yet built): a
  hand-reviewed registry file + a GitHub issue template for submissions,
  explicit consent language distinct from "the WCL link happens to be
  public," manual (not scheduled) recalibration runs using Top-5 #4's new
  LOO-CV + F-consistency tooling. Full rationale + prerequisite (log
  backup — unresolved, needs the owner's call on a destination) in
  `docs/validation/phase6_5_community_log_corpus_decision_2026_07_06.md`.

**6.6 Playstyle Coach** *(promoted from IDEAS.md 2026-05-19)*
- *Problem.* Today's death analysis is descriptive — "this hit you" — not prescriptive. The mitigation-uptime audit (3.2) and SB root-cause diagnosis (3.4) measure what happened but don't translate to "do this differently." Brutoh's ask: a coach layer that tells the player, in plain language, what to change.
- *Fix.* Rule library that maps measured patterns to suggestions:
  - *Was a defensive up?* Cross-reference each death sequence (`death_analysis.py`) against `MitigationState` history at fatal-event time. Surface: "Shield Wall was off CD but unused" / "IP absorb was empty for 4s before the killing blow."
  - *What should they have done?* For each unmitigated fatal sequence, run a counterfactual: would SB / Demo Shout / Shield Wall fired 2s earlier have survived? Use the existing cooldown-planner search restricted to a ±5s window around the death.
  - *Group context.* Limited: replay surfaces healer-DR-cooldown gaps (no Ironbark within 5s of the death) via the existing externals timeline. Not a full healer model — just "no external was active."
- *UI.* Per-death "What could have changed this?" expander beneath the existing death timeline. 1–3 ranked suggestions per death, each with the counterfactual delta.
- *Spec correctness.* Every suggestion must be filtered by `class_spec` — never suggest Shield Wall to a Vengeance DH. (See also the v0.11 queue CD-plan spec-correctness item.)
- *Engine prerequisites.* 3.2 (mitigation audit) + 3.4 (SB root cause) shipped. Needs the Phase 2.8 / 4.6 tank-self-sustain slot for accurate counterfactuals on self-heal specs.
- *Why it matters.* "People don't see you playing." Nobody coaches tanks. Currently the only feedback loop is replays after the wipe; simf can offer it before the next pull.
- *Effort.* L (rule library + counterfactual sim + suggestion UX).

**Why it matters**: Phase 6 is the moat. DPS tools don't have HRPS. DPS tools don't have a cooldown planner. DPS tools don't have a normalized tank score. These features can only exist in a tool built from the ground up around tank survivability.

**Effort**: XL (cooldown planner is combinatorially complex; HRPS requires careful definition; normalized score needs community calibration data).

---

## Success Metrics

| Phase | How We Know It Worked |
|---|---|
| **Phase 1** | Median time from landing to first sim result < 60 seconds. Armory import success rate > 85% for English-region characters. |
| **Phase 2** | Trinket registry covers 15+ current-season trinkets. Key-level verdict matches community-tested thresholds within ±2 key levels. Stat-weight sweep completes in < 10 seconds at 1 000 iterations. |
| **Phase 3** | Death reconstruction correctly identifies the fatal ability in > 90% of test logs (validated against 6 calibration logs). WCL import works for any public report without configuration. |
| **Phase 4** | Blood DK, Vengeance DH, and Prot Paladin each calibrate within ±15% RMSE on 2+ real logs. All three appear in spec selector without the existing protection_warrior-only gate. |
| **Phase 5** | Public URL is live. Stat-weight sweep latency < 5 seconds. CI passes on every push. Docker image builds in < 3 minutes. |
| **Phase 6** | Normalized Tank Score correlation with actual key-level success rate > 0.7 (measured against WCL data). Cooldown planner reduces death rate > 20% relative to policy-driven CD usage on high-key profiles. |

---

## What We Are NOT Building

- **DPS rotation optimization.** SimulationCraft owns this. APL modeling multiplies complexity with zero tank-survival value.
- **Raid survivability.** Tank-buster profiles exist, but full raid encounter scripting (positioning, movement, raid-wide mechanics) is out of scope. M+ is the domain.
- **Full healer engine.** The current healer model (baseline HPS + externals + reactive burst) is intentionally simple. Modeling a healer's full decision tree and mana constraints requires a second simulation engine.
- **PvP.** Entirely different damage model, no M+ overlap.
- **Pixel-perfect dungeon scripting.** Archetype profiles (caster pull, melee pull, boss tank-buster) are the correct abstraction. Log replay is the right tool for "simulate exactly what happened."
- **User accounts and cloud save.** Shareable URLs (Phase 1.3) covers the use case. A database adds ops burden with minimal benefit until DAU exceeds ~500.
- **Mobile-native app.** Streamlit is responsive enough for tablet use. A native iOS/Android app adds a platform to maintain for a niche audience.
- **SimulationCraft APL import.** The `/simc` string for character stats is in scope; the APL rotation itself is not.

---

## Architecture Reference — Key Files per Phase

| File | Phase(s) | Role |
|---|---|---|
| `src/simf/core/runner.py` | 2, 5 | Inner simulation loop; parallelization and vectorization target |
| `src/simf/core/mitigation.py` | 4, 6 | Dispatch by `class_spec`; new specs extend the if-chain |
| `src/simf/core/policy.py` | 3, 4, 6 | `ActiveMitigationPolicy.decide()`; CD planner overrides here |
| `src/simf/core/metrics.py` | 2, 6 | `SimResult` gains `effective_key_level_range`, `mean_hrps`, `normalized_score` |
| `src/simf/ui/app.py` (+ `ui/*.py` gear/vault modules, `ui/log_*.py` log surface) | 1, 2, 3 | `app.py` is a thin entry/router; Phase 1 landing + Phase 2 vault/crest/trinket surfaces split across `ui/` modules (gear_surface, vault_panel, slot_dialog, recommend, …); Phase 3 log analysis lives in the `ui/log_*.py` family behind the `log_view.py` facade |
| `src/simf/io/simc_import.py` | 1 | `ItemSpec.item_id` already available for Phase 1.2 item lookups |
| `src/simf/io/combat_log.py` | 3 | Extend with UNIT_DIED parsing for Phase 3.1 |
| `src/simf/io/log_replay.py` | 3, 5 | `ReplayData` gains `deaths` field in Phase 3.1 |
| `src/simf/data/constants.yaml` | 4 | New `specs.blood_death_knight`, `specs.vengeance_dh`, `specs.protection_paladin` blocks |
| `src/simf/optimizer/stat_weights.py` | 2 | Bootstrap parallelization target for Phase 2.4 |
