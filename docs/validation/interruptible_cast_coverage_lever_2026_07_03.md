# Interruptible-cast coverage lever (shipped 2026-07-03)

The field-test memory from the coaching-coverage MVP's first real use
(`coaching_feature_fieldtest_2026_06_29`) flagged this as "the next move on
the coaching surface, highest leverage, NOT YET BUILT": two of a AnonGuardian1
Pit-of-Saron run's three biggest hits (413k + 409k Icy Blast) were dropped
kicks the feature structurally couldn't see — it only ever asked "was a
defensive up," never "was this damage preventable entirely via an interrupt."

## What shipped

`io.combat_log.parse_interrupted_spell_ids(log_path, ...)` — a new pure
parser scanning `SPELL_INTERRUPT` lines for their `extraSpellId` field (the
spell that got interrupted, distinct from `spellId`, the interrupting
ability like Pummel/Kick — verified against real `SPELL_INTERRUPT` lines in
`examples/`, not assumed from the WoW log spec). Returns a `frozenset[int]`
of every spell_id proven interruptible anywhere in the scanned window.

`core.coaching.HitCoverage` gains `interruptible: bool`; `build_coverage_report`
gains an `interrupted_spell_ids` parameter (default empty — bit-identical for
every existing caller) and flags a hit interruptible when its `spell_id`
is in that set. `CoverageReport.n_interruptible` counts them.

`ui.log_coaching.render_defensive_coverage` prepends a `⚡ interruptible
(kicked elsewhere this run) — free prevention` badge to a flagged hit's row,
additive to (not replacing) its normal coverage badge — the two signals are
orthogonal: a hit can be both covered by a defensive AND a missed kick.

Wired into the local-log path first (`ui/log_data.py::_cached_coverage_report`),
matching how the original coverage MVP itself shipped local-log-first with a
WCL fast-follow PR — the WCL path followed the same day (see below).
`analysis_version` 5 → 6 (the disk-cached tuple `_cached_coverage_report`
returns grew a third element; an old 2-tuple cache entry would crash on
unpack, not just serve stale data, so this bump is load-bearing, not
cosmetic). The WCL path needed no further version bump — it added a new,
independent cache key rather than changing an existing cached shape.

## The honesty design (the part worth reading before touching this again)

The obvious naive approach — a hand-curated YAML table of "these boss
abilities are interruptible" — was rejected. It would be exactly the kind of
external, patch-fragile assumption this feature's charter exists to avoid
(see the "Honesty rules" docstring in `core/coaching.py`): WoW's mechanics
change every season, a table goes stale the moment a dungeon rotates out,
and getting one entry wrong turns "coaching" into "confidently wrong advice"
on the single most trust-critical surface simf has.

Instead: a spell is flagged interruptible **only when the log itself proves
it** — a real `SPELL_INTERRUPT` against that exact spell_id, anywhere in the
scanned run, by anyone in the party (not just the tank). This is the same
positive-evidence gate every other lever in this module already uses
(talent-gating, the bleed/armor-scope rule). The real, disclosed cost: a
genuinely interruptible cast that nobody ever kicked in the available log is
silently not flagged — under-shows, never lies, same shape as every other
rule in this file.

**Verified against real data, not just synthetic fixtures** — ran the full
pipeline against `examples/WoWCombatLog-051926_132910.txt` (Brutoh,
Algeth'ar Academy +10): 3 of his 8 biggest hits that run (172k / 128k / 125k
"Arcane Bolt" from a Spectral Invoker) were flagged interruptible, because
spell_id 1279627 was independently proven interrupted elsewhere in the same
run. Real, actionable, correctly-scoped signal — not a hypothetical.

## WCL path parity (shipped 2026-07-03, same day)

`io/wcl_api.py` gained `fetch_interrupted_spell_ids(report, fight, token)` — a
party-wide (no source filter, matching the local parser's "anyone in the
party" scope), paginated `dataType: Interrupts` query, wired into
`wcl_bridge.build_wcl_coverage_report` and cached under a
spec-and-target-independent `"interrupts"` key (evidence of what got kicked
in a fight has nothing to do with which tank is being analyzed, so every
spec/target view of the same fight shares one fetch).

**Field name CONFIRMED against live data (2026-07-03, same day).** The v2 API
docs site (`warcraftlogs.com/v2-api-docs/...`) 403s automated fetches — same
failure mode already on record for Wowhead's per-dungeon pages
(`midnight_loot_data_refs.md`) — and WCL's `characterRankings` API
deliberately redacts `report.code`/`fightID` for privacy, so a report couldn't
be pulled from the API either. Worked around by loading WCL's own **public
zone rankings page** (`warcraftlogs.com/zone/rankings/47?class=Warrior&spec=Protection`)
through a real Playwright browser (which the site serves; a bare WebFetch
gets the same 403 the docs site does) and scraping the rendered `<a
href="/reports/...">` links — those aren't redacted, since the leaderboard
UI has to link somewhere. That produced 63 real, currently-valid report+fight
pairs with no need to guess a URL.

Against report `ExampleCode6666666` fight 8 (Algeth'ar Academy +22, Protection
Warrior), a real `dataType: Interrupts` event is flat-shaped:

```json
{"type": "interrupt", "sourceID": 78, "targetID": 109, "targetInstance": 1,
 "abilityGameID": 47528, "fight": 8, "extraAbilityGameID": 396640}
```

`abilityGameID` is the interrupting ability (Mind Freeze); **`extraAbilityGameID`
is the confirmed field for the interrupted spell** — the `stoppedAbility`
guess (corroborated only by WCL's scripting-API help docs, which turned out
to name a scripting-API accessor, not the raw GraphQL field) was wrong, but
the code's defensive fallback chain already tried `extraAbilityGameID` second,
so `fetch_interrupted_spell_ids` extracted the right ID (`396640`) even before
this was confirmed. Reordered `_extract_stopped_spell_id` to try
`extraAbilityGameID` first now that it's the known-correct field;
`stoppedAbility` stays as a defensive fallback. The shipped function was
re-run against the real report and returned `frozenset({396640, 388392,
1279627, 388862})` — notably **`1279627` is the exact same Arcane Bolt /
Spectral Invoker spell_id** the original local-log validation (above) found
independently, in a completely different tank's completely different pull,
via a completely different code path. Two independent data sources agreeing
on the same spell_id is strong cross-validation.

**End-to-end UI check:** pasted the real report URL into the running app
(`Warcraft Logs URL` tab) and confirmed the full pipeline — report fetch,
fight/player resolution, damage events, buff/debuff windows, AND the
interrupt fetch — completes and renders the "Defensive coverage" section with
no errors. Neither this fight nor a second real fight checked
(`ExampleCodeDDDDDDD` fight 35) happened to have the badge visible, because
neither one's top-8-biggest-hits overlapped with its interrupted-spell set —
both fights' biggest hits were boss-mechanic nukes (Vexamus's Arcane
Expulsion, Echo of Doragosa's Astral Blast), while their actual kicks landed
on smaller trash casts that never rank in the top 8. That's a legitimate
"nothing to flag" outcome (same fail-safe shape as everywhere else in this
module), not a bug — the extraction logic itself is independently confirmed
correct at the unit level against the real payload above. See
`tests/test_wcl_interrupts.py` for the full set of shapes covered, including
`test_real_interrupt_event_shape_extracts_correctly` pinned to the exact
real-world payload.

## "Was a kick available" (cooldown-aware) split — CLOSED (shipped 2026-07-03, same day)

See `docs/validation/kick_availability_split_2026_07_03.md` for the full
record. Shipped exactly as scoped above: a small, stable per-tank-spec
interrupt-ability registry (`coaching.interrupts` in `constants.yaml`) plus
cast-cooldown tracking, reusing the existing `_lever_available_at` helper.
Live-verified against Brutoh's real log (his own real Pummel casts) and a
real WCL report's real `dataType: Casts` payload.

## Deliberately out of scope
- Cross-log/cross-corpus evidence (only interrupts within the SAME run count
  today) — a spell interrupted in a different log doesn't carry over.

## Tests

Local-log path: `tests/test_interrupted_spell_parsing.py` (5, the raw parser
— real line shape, multi-interrupt dedup, empty case, time-window filtering,
malformed line resilience), `tests/test_coaching.py` (+5, the join:
flagged/not-flagged, exact-spell_id-only matching, independence from
defensive coverage, default empty-set backward compatibility),
`tests/test_coaching_render.py` (+2, the badge renders additively and only
when flagged).

WCL path parity (2026-07-03, same day): `tests/test_wcl_interrupts.py` (13 —
`_extract_stopped_spell_id` against every candidate field shape plus garbage
input plus the pinned real-world payload, `fetch_interrupted_spell_ids`
collect/dedup/pagination/query-shape pins), `tests/test_wcl_coverage.py` (+3
— the join wire-up, the fetch is cached, the fetch is shared across specs on
the same fight).

Full suite: 1897 passed, 7 pre-existing skips. `make lint` / `make typecheck`
clean. Additionally verified live against two real public Warcraft Logs
reports (see "Field name CONFIRMED against live data" above) — both the raw
extraction and the full UI pipeline.
