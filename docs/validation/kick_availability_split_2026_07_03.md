# Cooldown-aware kick-availability split (shipped 2026-07-03)

The interruptible-cast coverage lever (`docs/validation/interruptible_cast_coverage_lever_2026_07_03.md`)
shipped evidence-only: a hit is flagged `interruptible` when its exact
spell_id was proven interruptible elsewhere in the same log/fight. Its own
"Deliberately out of scope" section named the natural follow-up: the feature
only proved "this cast CAN be kicked," never "you specifically had a kick
ready right then" — the same distinction `had_cd_available` / `kit_spent`
already draws for defensive cooldowns, applied to the tank's own interrupt
instead.

## What shipped

`data/constants.yaml` gains a `coaching.interrupts` registry — one entry per
tank spec, each a `{spell_id, name, cooldown_s}` row:

| Spec | Ability | Spell ID | Cooldown |
|---|---|---|---|
| Protection Warrior | Pummel | 6552 | 15s |
| Protection Paladin | Rebuke | 96231 | 15s |
| Blood Death Knight | Mind Freeze | 47528 | 15s |
| Vengeance Demon Hunter | Disrupt | 183752 | 15s |
| Brewmaster Monk | Spear Hand Strike | 116705 | 15s |
| Guardian Druid | Skull Bash | 106839 | 15s |

Every value confirmed live against Wowhead (2026-07-03) — see "Verification"
below. Unlike boss-ability interruptibility, this table IS safe to
hand-curate: every spec's baseline interrupt is a long-stable, non-talented
ability (the same reasoning the original feature's deferred-work note already
made), not a patch-fragile mechanic.

`core/coaching.py`:
- `InterruptAbility` (spell_id, name, cooldown_s) + `interrupt_ability_for_spec(spec, constants)`.
- `HitCoverage.kick_was_ready: bool | None` — `True`/`False` only for hits
  already `interruptible`; `None` when not assessable (spec unregistered, or
  the hit itself isn't interruptible). Plus `kick_missed` / `kick_on_cooldown`
  convenience properties, mirroring `had_cd_available` / `kit_spent`.
- `CoverageReport.interrupt_ability_name` + `n_kick_ready` / `n_kick_on_cooldown`.
- `build_coverage_report` gains `interrupt_ability` + `own_interrupt_cast_times`
  params (both default empty/None — bit-identical for every existing caller).
  Availability reuses the existing `_lever_available_at` helper unchanged —
  cast instants are represented as zero-width `(t, t)` windows, so "was the
  tank's kick off cooldown at hit time" is the same math as "was a defensive
  lever off cooldown," just applied to a different input.

Wiring, both paths (mirroring how the parent feature shipped local+WCL the
same day):
- **Local-log** (`ui/log_data.py`): `parse_cast_events` (already existed,
  previously only used for the rage-flow feature) filtered to the tank's own
  name + their spec's interrupt ability.
- **WCL** (`io/wcl_api.fetch_own_cast_times`, new): paginated `dataType: Casts`
  query filtered server-side by `sourceID` + `abilityID`. UNLIKE the parent
  feature's `fetch_interrupted_spell_ids` (party-wide, cached per-fight,
  shared across every spec viewing the same fight), this is personal —
  cached per `(target, spec)` since it's asking what *this specific tank*
  cast, not what anyone in the party did.

UI (`ui/log_coaching.py`): each interruptible hit's row gains an additional
clause — "🟢 your Pummel was ready" or "⚪ your Pummel was on cooldown" —
additive to the existing `⚡ interruptible` badge. A new summary caption
("Of your N interruptible hit(s), your own Pummel was off cooldown for X and
on cooldown for Y") sits alongside the existing continuous-uptime / honesty
captions.

`analysis_version` 6 → 7 (same hazard as the parent feature's 5→6 bump —
`_cached_coverage_report`'s disk-cached tuple grew a 4th element).

## Verification

**Spell IDs + cooldowns**: confirmed live against Wowhead (2026-07-03), not
assumed from training data — a WebSearch initially returned two conflicting
candidate IDs for Skull Bash (106839 vs 93985); fetching both spell pages
directly resolved it (106839 is the live Druid Cat/Bear-Form interrupt,
15s CD, 13-yard range; 93985 has no listed class/cooldown and a 100-yard
range — clearly a different, unrelated spell that happens to share the
name). Mind Freeze (47528) was independently cross-validated the same day
against a real WCL `Interrupts`-dataType payload (see the parent feature's
validation doc) — the ability doing the interrupting in that real event was
exactly spell 47528.

**Real local-log data**: `tests/test_coaching_integration.py`'s existing
real-log smoke test (runs against Brutoh's actual combat logs when present)
was extended to fetch his real Pummel casts and assert `kick_was_ready` is
correctly assessable. Live-verified through the actual running app against
`examples/WoWCombatLog-051926_132910.txt` (the same Algeth'ar Academy +10 run
the parent feature's doc cites): of his 3 interruptible Arcane Bolt hits,
the 172,118 hit at 16:42 shows **🟢 your Pummel was ready** (he'd last cast
it long before), while the 128,376 and 125,823 hits at 16:35/16:39 show
**⚪ your Pummel was on cooldown** — a real, differentiated, correct signal.

**Real WCL data**: `fetch_own_cast_times` was tested live (2026-07-03,
reusing the public-rankings-report technique from the parent feature) against
report `ExampleCode6666666` fight 8 — the real tank (actor 75) had cast Pummel
8 times; the raw `dataType: Casts` event shape (`sourceID`, `abilityGameID`,
`timestamp`) matched expectations exactly, confirmed server-side filtering
by both `sourceID` and `abilityID` works (no client-side filtering needed,
unlike the Debuffs dataType).

**Honest finding, not a bug**: checked 7 different real WCL reports
(Protection Warriors, Algeth'ar Academy + Magisters' Terrace, various key
levels) for a case where the top-8-biggest-hits list overlaps with the
fight's interrupted-spell set — zero overlap in all 7. This looks like a
real structural property, not bad luck: boss/elite mechanic damage dominates
"biggest hits" and is essentially never interruptible by design, while
interruptible casts (mostly trash spellcasters) rarely crack the top-8
damage threshold. This means the kick-availability badge (like its parent
`interruptible` flag) will visibly fire more often on local-log analysis of
a rough/dangerous pull than on a report's single scariest hits — expected,
not a defect. Full end-to-end WCL-path confirmation (badge visibly rendering
from live WCL data, not just the fetcher returning correct data) is still
gated on a real report that happens to have both; the local-log path already
has that confirmation (above).

## Deliberately out of scope

- **Charges/haste-aware cooldown.** Like every other availability check in
  this module, `cooldown_s` is the ability's base value with no
  charges/haste/talent modeling — this over-states recharge time, so it
  under-reports availability (errs toward "on cooldown," never wrongly
  claims "ready"), the same bias `_lever_available_at`'s docstring already
  states for defensive levers.
- **Cross-log/cross-fight cast history.** Only casts within the same
  run/fight count — a kick cast in a previous pull doesn't inform
  availability in the current one (cooldowns reset at pull start is the
  implicit assumption, same as the existing defensive-CD split).
- **Party-wide kick availability.** Deliberately personal (the tank's own
  ability only) — see "What shipped" above for why.

## Tests

`tests/test_coaching.py` (+12 — registry parsing for all 6 specs, the
ready/on-cooldown/never-cast/not-assessable matrix, backward compatibility),
`tests/test_wcl_own_casts.py` (new, 4 — the fetcher pinned to the real
payload shape, pagination, query-shape regression pin), `tests/test_wcl_coverage.py`
(+5 — WCL wire-up, personal vs party-wide caching distinction),
`tests/test_coaching_render.py` (+4 — both badge variants, the summary
caption, the not-assessable no-op case), `tests/test_coaching_integration.py`
(extended — real Brutoh log assertions). Full suite: 1925 passed, 7
pre-existing skips. `make lint` / `make typecheck` clean.
