# WCL source-debuff events probe — 2026-06-29

## Context

The defensive-coverage coaching surface (`core/coaching.py`) credits a
tank-applied enemy debuff — Prot Warrior's **Demoralizing Shout** (1160, a
minor-tier −20% enemy debuff) — when the *exact mob that dealt a big hit*
carried it. The local-log path does this via `combat_log.parse_source_debuff_windows`
(keyed by creature GUID). The WCL fast-follow (PR #222) shipped with
`debuff_windows_by_source={}` — a deliberate under-credit — because no WCL
source-debuff fetch existed and the WCL event shape hadn't been pinned.

This document pins, empirically, the WCL v2 GraphQL shape needed to fetch that
debuff spawn-precisely. Same discipline as
[`wcl_events_probe_2026_05_26.md`](wcl_events_probe_2026_05_26.md): don't guess
the API.

## Method

Throwaway probes (`scratchpad/wcl_debuff_probe*.py`) against the real report
`ExampleCodeCCCCCCC` fight 1 (Brutoh, Prot Warrior, Windrunner Spire), using
simf's `_get_token` / `_gql`.

## Findings (verified, not from docs)

### 1. The debuff is on the ENEMY side — `hostilityType: Enemies`

`dataType: Debuffs` defaults to debuffs ON friendlies (auras on the tank).
Demoralizing Shout is a debuff the tank puts ON enemies, so the default returns
0 Demo Shout events. `hostilityType: Enemies` returns debuffs on enemies.

### 2. `sourceID` is BROKEN for aura dataTypes — filter client-side

`dataType: Debuffs, hostilityType: Enemies, sourceID: <tank>` returned **0
events** (same class of quirk the damage path documents for `targetID`). The
working filter is `abilityID: <spell>` server-side + `source.id == <tank>`
**client-side** (so a non-tank warrior's Demo Shout isn't credited).

- `Debuffs + Enemies` (no ability filter): 3002 events, 40 Demo Shout.
- `Debuffs + Enemies + abilityID: 1160`: **306 events, all Demo Shout.** ✓

### 3. `abilityID` is typed **Float**, not Int

A `$aid: Int!` variable is rejected: *"Variable \"$aid\" of type \"Int!\" used
in position expecting type Float."* An inline literal (`abilityID: 1160`) is
coerced and hides this — a variable must be declared `Float!` and passed
`float(spell_id)`. (Caught only at live run, after the probe used a literal.)

### 4. Spawn-unique identity exists on BOTH event types — `(actorID, instance)`

A single NPC *type* shares one WCL actor `id`; spawned copies are distinguished
by a per-event instance. Both event types carry it (useActorIDs false or true):

- DamageTaken: `source: {id}` + sibling `sourceInstance`.
- Debuffs (Enemies): `target: {id}` + sibling `targetInstance`.

This is the WCL analog of a creature GUID. The probe proves precision is
*needed*: at one timestamp Demo Shout landed on `Dutiful Groundskeeper
instance 3`, while a *different* `Groundskeeper instance 1` dealt melee earlier
— actor-id-only matching would falsely cross-credit; `(id, instance)` does not.
simf stamps `f"{id}:{instance}"` (missing instance → `1`, consistently on both
sides) onto `DamageTakenEvent.source_guid` (`_map_event`) and onto the debuff
window keys (`fetch_source_debuff_windows`).

## Production query

```graphql
events(
  fightIDs: [<fight.id>]
  dataType: Debuffs
  hostilityType: Enemies
  abilityID: <spell_id>      # Float!
  startTime: <start>
  endTime: <end>
  useActorIDs: false
  useAbilityIDs: false
  limit: 10000
)
```
filter `source.id == tank_actor_id` client-side; key windows by
`(target.id, targetInstance)`. A unit-test regression pin
(`test_fetch_source_debuff_windows_query_pins`) asserts the filter combo and the
absence of `sourceID`.

## End-to-end validation (same report, fight 1)

`analyze_wcl_fight` → `build_wcl_coverage_report`:

- `source_guid` populated on **2022/2022** events as `"actorid:instance"`. ✓
- **98** distinct Demo-Shouted spawns; **all 98** also appear as damage sources
  (perfect key overlap → instance numbering is consistent across event types). ✓
- **516 / 2022** hits landed while their *own* spawn was Demo-Shouted at hit
  time — the join finds real matches, spawn-precisely. ✓
- Top-8 biggest hits: 0 partials (a 45s-CD / 8s minor simply didn't overlap the
  single biggest spikes in this run) — correct, honest output, not a miss. The
  over-credit guard is what keeps the 516 from inflating to "any same-type mob."

## Outcome

`fetch_source_debuff_windows` + the `source_guid` spawn-key stamp close the
documented WCL parity gap: Demo Shout is now credited on the WCL path exactly as
on the local-log path, spawn-precisely (never over-credited). `analysis_version`
4 → 5 (WCL events carry the new `source_guid` field).
