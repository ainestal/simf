# WCL events API probe — 2026-05-26

## Context

After PRs #84 + #85 + the first two commits of PR #86 (picker, `?source=N`,
actor-ID pass-through), Brutoh tested a real WCL URL and got 3 events for a
14:57 fight, with sources rendered as numeric IDs ("Physical auto-attack from
2"). Local-log analysis of the same dungeon produced 1,361 events with named
sources. Three speculative commits had each chased the wrong symptom; the
underlying assumption about WCL's `events` semantics was the root issue.

This document pins what the WCL v2 GraphQL `events` field actually does, so
future-me doesn't re-guess.

## Method

Throwaway `wcl_probe.py` ran against the real report
`ExampleCodeCCCCCCC` (Brutoh's run that exposed the bug), using simf's existing
`_get_token` / `_gql` helpers. Tried multiple flag combinations and recorded
event count + first-event shape for each.

## Findings (verified empirically, not from docs)

### 1. `targetID` doesn't restrict to that target for `dataType: DamageTaken`

With `dataType: DamageTaken` + `targetID: <tank_actor_id>`:

- `hostilityType: Friendlies` (default): **3 events** — and they're self-cast
  buff events (Shadow Mend on self), not damage taken.
- `hostilityType: Enemies`: 5000 events — but they're damage the **tank
  dealt** (Moonfire / Shield Slam etc.), where `sourceID == tank_actor_id`.

Neither value of `hostilityType` makes `targetID` filter to the target.

### 2. The correct query: no `targetID`, default Friendlies, filter client-side

With `dataType: DamageTaken` + `hostilityType: Friendlies` + no `targetID`:

- 3122 events for the whole 5-player party in fight=1 (Windrunner Spire +15,
  14:57).
- Each event has `sourceID: <enemy>`, `targetID: <friendly>`, plus
  `unmitigatedAmount`, `blocked`, `absorbed`, `mitigated`.

Filtering client-side by `event.target.id == tank_actor_id` returns the **2022
events** Brutoh actually took — matching local-log analysis on the same key.

### 3. `useActorIDs: false` changes the response shape

With the flag set, events have:

```json
{
  "source": {"name": "Fervent Apothecary", "id": 5, "type": "NPC", ...},
  "target": {"name": "Brutoh", "id": 2, "type": "Warrior", ...},
  "ability": {"name": "Phial Toss", "guid": ..., "type": 8, ...}
}
```

Without it, you get flat numeric `sourceID` / `targetID` / `abilityGameID`
with no names — what produced "Physical auto-attack from 2" in the UI.

`useAbilityIDs: false` similarly switches ability from a numeric `abilityGameID`
to a named `ability {name, type, ...}` object.

### 4. `viewBy` doesn't exist on `events`

WCL rejected the query with:
> Unknown argument "viewBy" on field "events" of type "Report"

It's documented on the `table()` field but not `events()`. Don't add it.

## Decision

Production query becomes:

```graphql
events(
  fightIDs: [<fight.id>]
  dataType: DamageTaken
  hostilityType: Friendlies
  startTime: <fight.start>
  endTime: <fight.end>
  translate: true
  useActorIDs: false
  useAbilityIDs: false
  limit: 10000
)
```

`fetch_damage_events` then filters each returned event by
`event.target.id == target_actor_id`. The unit-test regression pin asserts
these flags and the absence of `targetID:` in the query body, so a future
edit that drops them fails CI rather than silently breaking live analysis.

## Outcome

Verified end-to-end via `fetch_damage_events` against
`ExampleCodeCCCCCCC` fight=1: **2022 events**, top sources
`Dutiful Groundskeeper (528)`, `Ardent Cutthroat (319)`, `Fervent Apothecary
(93)` — matches the same dungeon read from a local log.
