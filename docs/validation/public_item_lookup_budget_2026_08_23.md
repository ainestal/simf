# The public instance could not resolve anyone's gear but the demo's

**2026-08-23.** Reported from the live site: a real `/simc` export (AnonGuardian1,
Guardian Druid, EU/AnonRealm1, SimC Addon 12.1.0-03) pasted into the "Check your
vault picks (and bags)" box came back with

> Couldn't fill in your stats. Your SimC export didn't include
> `gear_haste_rating=` lines, and the Blizzard item API isn't configured
> (`~/.simf/blizzard.yaml`). Update SimulationCraft addon, or configure Blizzard
> API credentials.

Both instructions in that error are dead ends, and neither names the real cause.

## Which instance produced it

The pasted page ended in `build unknown`. `format_build_footer` only degrades to
that when `git rev-parse` fails — i.e. a tarball install, not a checkout. This
dev checkout resolves a sha fine (`e6d7a5c` at the time), and `/opt/simf` is not
a git repo, so the report came from a deploy-style install running
`deploy/simf-public.service` (`SIMF_PUBLIC=1` — private deploy tooling not
included in this repo), not from the dev app on :8502.

## Root cause

`SIMF_PUBLIC=1` made `item_db._is_offline()` true, and every item-stat fetch
site returned `None` on a cache miss without going to the network. The intent
(2026-06-13) was sound: a stranger's paste fans out one request per item, so an
anonymous visitor shouldn't be able to drive unbounded egress from the Pi.

The problem is what "cache" means there. The committed seed
(`src/simf/data/item_cache_seed`, 118 entries) is keyed by item id **plus bonus
ids** — `wh_151308_6652-12699-12795-13440-13668.json`. A stranger's items miss
it by construction, and so does the *same* item at a different upgrade track.
So public mode wasn't mildly degraded for visitors; it was structurally
incapable of resolving any gear it hadn't already seen:

- `/simc` paste → `resolve_equipped_stats` returns `{}` → the error above.
- Raider.IO name lookup → same resolver → an all-zero, `is_degraded()` character.

Those are the two headline entry points. Both worked only for the seeded demo.

Reproduced exactly (byte-for-byte error text) with a cold cache:

```
HOME=<tmp> SIMF_ITEM_DB_OFFLINE=1 load_from_simc(anonguardian1.simc, resolve_stats_fn=…)
→ SimcLoadError("Couldn't fill in your stats. …")
```

The first attempt at that repro *passed* — a live run seconds earlier had warmed
`~/.simf/item_cache`. Redirecting `HOME` was what made it fail. Worth
remembering: any offline-behaviour test on this box is contaminated by the
owner's own warm cache unless the cache dir is redirected.

## Decision — budget the lookups instead of blocking them

Owner's call (asked, not assumed, since it loosens a deliberate safety gate on
an internet-facing box): allow the lookups, bounded.

- **Wowhead** (`fetch_item_stats_wowhead`, sockets, two-hand, icon) is zero-auth
  and cheap, so in public mode it now spends a rolling window —
  `wcl_budget.item_lookup_guard()`, 180/min and 1800/hour, its own event deque
  so it can't starve the WCL or Raider.IO windows. Every result is cached to
  disk, so a repeat visit on the same gear costs nothing.
- **Blizzard** (`fetch_item_stats`, `search_items`) stays hard-blocked in public
  mode. Those spend the *owner's* OAuth budget; a stranger must never be able
  to. `_is_offline()` keeps exactly its old meaning and now governs only these.
- **`SIMF_ITEM_DB_OFFLINE=1`** remains the kill switch — it restores the old
  block-everything behaviour for Wowhead too, and is documented as a commented
  directive in `deploy/simf-public.service` (private deploy tooling not
  included in this repo).

### Why the burst is reserved all-or-nothing

A character's stat total is only meaningful if *every* equipped slot resolved.
Metering per request could admit 12 of 17 lookups and hand back a character
whose stamina is silently ~30% short — a plausible-looking wrong number, which
is the failure mode this project treats as worse than an honest error.
`WCLRateGuard.check(cost)` therefore admits a whole burst or nothing, and
`resolve_equipped_stats` reserves one slot per *uncached* item up front
(`item_db.live_lookup_lease`) before any request goes out. A refused burst
returns `{}` — "couldn't read your gear stats" — and reserves nothing.

Hard-offline keeps its old partial-sum behaviour: that's how the seeded demo
loads, and turning it into an empty result would break it.

### Sizing

One cold visitor ≈ 18 reserved equipped lookups + ~25 vault/bag cards costing a
stat + icon lookup each ≈ 90 requests, then ~0 on their next visit. 180/min
absorbs a couple of concurrent cold loads without a burst denying itself;
1800/hour ≈ 20 cold visitors an hour, ~0.5 req/s average.

## Side finding — the icon fetch had no gate at all

`fetch_item_icon_wowhead` never consulted `_is_offline()`, so in public mode
every rendered gear card already made one uncapped outbound request. The
"offline" box was never actually offline. It's under the same window now.

## Copy fixes (docs/ui_copy_voice.md rule #2)

The stats-unresolved error told the reader to update the SimC addon and to edit
`~/.simf/blizzard.yaml`. The second is the owner's file — the exact leak rule #2
forbids, already fixed once in `vault_panel.py` (PR #282) and missed here. The
first is unactionable: **no** addon build emits `gear_*_rating=` lines — 0 of 6
real exports in `examples/`, spanning addon 12.0.5 and 12.1.0 — so the resolver
path is the normal path, not a misconfiguration. It now names the real cause and
two actions the visitor can take (retry; or load a combat log / WCL link).

Two sibling captions repeated the same impossible instruction ("Re-export with a
SimC addon that includes `gear_*_rating=` lines") and now point only at the
exact-stat paths that do exist.

## Reconciled with PRs #485-#487 (found in parallel, 2026-08-22)

This work was done on a branch cut from a stale `master`. While it was in
flight, three PRs landed on `master` diagnosing the same live symptom
independently:

- **#485** — rewrote the stats-unresolved error to drop the `~/.simf/blizzard.yaml`
  instruction. Same finding as here, and it also noted the copy fired verbatim
  even when credentials *are* configured. Its replacement kept a softened
  "re-export from SimulationCraft with those stat lines" — still a dead end, since
  no addon build emits them (0 of 6 real exports in `examples/`), so the merged
  copy drops that half too and points at the combat-log / WCL path instead.
- **#486** — `_with_retries` (3 attempts, short backoff) on every item_db request.
  Independent of the gate and **kept as-is**: a batch of ~16-20 lookups only has
  to lose all of them to fail the load, so a single transient blip shouldn't
  decide it.
- **#487** — narrowed the public gate by exempting `fetch_item_stats_wowhead`
  behind a fixed 40-req/minute counter. Correct diagnosis, and this keeps its
  intent; the counter is superseded by `_live_wowhead_allowed` /
  `live_lookup_lease` because that adds four things it couldn't:
  a rolling window **plus** an hourly cap (a fixed 60 s bucket admits 2x the cap
  across a boundary, and nothing bounded the day); an atomic per-load
  reservation, so a stat total is never silently short a few slots; coverage of
  the icon/socket/two-hand lookups, which fan out per rendered card and which
  the exemption left blocked — except the icon one, which was making *uncapped*
  public requests all along; and a working `SIMF_ITEM_DB_OFFLINE` kill switch,
  which #487's counter overrode (it checked `_is_offline()` and then ignored it),
  leaving the operator no way to stop live stat fetches on a misbehaving box.

Net effect of the reconciliation: #486 kept whole, #485's finding kept with a
more accurate action, #487's mechanism replaced by a superset. Its tests were
rewritten rather than deleted — including its `SIMF_ITEM_DB_OFFLINE=1`-as-
public-mode shortcut, which now says `SIMF_PUBLIC=1` so the kill-switch test can
mean what it says.

## Per-render cost, and the one thing that would double it

`ui/state.py` resolves baseline **and** trial stats on every gear-surface
render. In public mode trial swaps are read-only ("Read-only share — trial
swaps disabled"), so both calls describe the same gear and hit the disk cache
after the first load — the cache probe that sizes a reservation is skipped
entirely once nothing is uncached, and it never runs at all on the owner's box.
A future change that enables trial swaps publicly would make the second call a
genuinely different item set, i.e. a second reservation per render; size the
window for that before flipping it.

## Verification

`SIMF_PUBLIC=1`, `HOME` redirected to an empty dir (cold cache), driven in a
real headless browser — **both** public on-ramps, each of which returned
nothing before this change:

| on-ramp | result |
|---|---|
| `/simc` paste (AnonGuardian1's export) | Guardian Druid, 16 slots, real item names + icons, "2 slots recommend a swap · total +4.23% (+130,783 eHP)" |
| "Find your character by name" (AnonGuardian1-AnonRealm1, EU) | "Gear fetched via raiderio just now", same character, "+4.30% (+126,411 eHP)" — equipped-only, so it differs from the paste as expected |

Both show the "stats estimated from item lookup" caveat, which is the honest
label for a resolver-derived load, and neither shows an error or the
`is_degraded()` "couldn't read your gear" banner.

The name-lookup path crosses two independent windows in one action
(`gear_lookup_guard` for the Raider.IO GET, then `item_lookup_guard` for the
item burst); that ordering had never been exercised before and works.

Tests: 11 budget/lease tests, 6 burst-admission tests, 2 copy tests. The full
suite's only failures are the documented xdist Streamlit-AppTest flake — three
full runs failed 2, then 6, then 4 tests with **disjoint** sets, and every one
of them passes serially (`-n 0`).
