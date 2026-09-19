# Phase 6.5 Community Log Corpus — the decision (2026-07-06)

Top-5 #5 from the 2026-07-06 retrospective (`ROADMAP.md`'s Active Triage
Queue): "the one structural bet — simf is 2-of-6 calibrated because of
data scarcity (VDH/Blood DK stuck on single unrepresentative corpora), not
modeling skill." Scored Effort XL, explicitly "decision now, build-out
later" — this doc is that decision. No MVP code shipped in this pass.

## The decision: build on WCL links, not raw log uploads

`ROADMAP.md`'s existing Phase 6.5 sketch ("Opt-in anonymous log sharing
(WCL link + character stats) for calibration") and Phase 5.5 sketch
("Web Worker... K-calibration corpus depends on opt-in upload of the
[parsed raw-log] summary") describe **two different submission
mechanisms** without picking between them, and Phase 5.5's framing
implicitly gates 6.5 behind 5.5 shipping first (a full browser-side
combat-log parser, Effort L, not yet started).

**Decision: skip the raw-log-upload path entirely for the community
corpus. Build on WCL report links.** Rationale:

1. **It already works today.** `simf calibrate-k --wcl-url <report> --wcl-target <name>`
   and `scripts/calibrate_spec_from_wcl.py` already pull a real fight
   through `io/wcl_replay.wcl_to_replay_data` and feed it to the K sweep —
   shipped 2026-05-27, used all session for the VDH/Blood DK/ProtPal
   characterization work this project already did. A community corpus
   built on WCL links is mostly *registry + scheduling* work on top of a
   path that's been in production for six weeks, not new parsing
   infrastructure.
2. **It sidesteps the privacy problem Phase 5.5 exists to solve.** Phase
   5.5's whole design ("no log ever leaves the user's machine unless they
   tick a box") is defensive engineering against exactly one risk: a raw
   `WoWCombatLog-*.txt` contains everyone in the group's name, gear, and
   sometimes guild — uploading it to a Pi you don't own is a real ask. A
   WCL *link* has none of that exposure: the submitter already chose to
   upload their log to Warcraft Logs (a third party they picked) and is
   sharing a URL to something already public (or unlisted-but-linkable)
   on that service. simf never touches the raw log file at all. This
   isn't "5.5 lite" — it's a different risk profile that doesn't need
   5.5's browser-worker machinery to be safe.
3. **The supply already exists.** M+ guilds and pushing groups routinely
   upload to Warcraft Logs and drop the link in Discord — that's existing
   raiding culture, not a new behavior simf needs to bootstrap. Recruiting
   "paste your WCL link" is a much smaller ask than "paste your raw combat
   log," which most players don't keep or know how to find.
4. **This re-orders the phase dependency, not just the mechanism.** Phase
   6.5 no longer needs to wait on Phase 5.5. They can ship independently;
   5.5 remains valuable for its OWN stated reason (moving Pi-hosted
   parsing compute to the browser as user count grows), but it is no
   longer a blocker for breaking the 2-of-6-calibrated ceiling.

## What "opt-in" and consent mean here

A WCL link being technically public (or reachable via an unlisted URL)
is not the same as consent to have your fight used in simf's calibration
corpus and cited in a `docs/validation/` doc with a character label
attached that lets someone re-derive findings against the source log
later — real, for the maintainer's own self-disclosed alts (Brutoh), or
generic otherwise (AnonBrewmaster1/2, AnonGuardian1), per the
anonymization policy CONTRIBUTING.md documents. The submission
step must be an explicit, separate consent action, not an inference from
"the link is public." Concretely: a submission form/flow states plainly
what happens to a submitted link (used to compute K-sweep deltas and
F-factor, findings may name the character and be published in-repo docs,
the link itself is stored in a small registry file) and requires an
explicit checkbox, not just a paste box.

## MVP scope (build-out later, sketched now so it's actionable)

Deliberately small — the goal is unblocking VDH/Blood DK/Brewmaster with
*a few* multi-player data points, not building a full pipeline:

1. **A registry file** (`data/community_corpus.yaml` or similar): one
   entry per submission — WCL report code, target character name, spec,
   submitter-consented timestamp, and which `docs/validation/` finding (if
   any) it contributed to. Append-only, hand-reviewed before merge (no
   auto-ingestion of arbitrary URLs into a script that runs unattended —
   a malicious or malformed WCL code is a resource-exhaustion vector
   against the Pi if it's ever pulled without a human looking at it first).
2. **The submission surface**, cheapest-first: start with a GitHub issue
   template ("Submit a WCL log for calibration") rather than an in-app
   form — zero new UI code, the owner reviews each submission before it
   touches the registry, and it's already how this kind of "please help
   with data" ask works in other small open projects. An in-app "Submit
   your WCL link" panel is a natural fast-follow once the registry +
   review flow is proven, not a prerequisite for starting.
3. **Recalibration trigger**: manual, for now — `scripts/calibrate_spec_from_wcl.py`
   already takes a `--fight` argument per report; a short wrapper script
   iterates the registry and re-runs Top-5 #4's LOO-CV + F-consistency
   checks (this session's new tooling) against the growing per-spec pool.
   Automatic/scheduled recalibration is explicitly NOT in scope for the
   MVP — every tier promotion in this project has been human-ratified
   with a validation doc, and a community corpus is exactly the place to
   keep that discipline, not the place to relax it.
4. **Where this actually unblocks work**: VDH and Blood DK are each
   currently `characterized` on a single build/player (Top-5 #4's tier
   doc). 3-5 WCL links from *different* players/builds per spec would let
   the promotion criteria's ">=8 F-consistent runs, multiple builds" bar
   (see `core.constants.spec_is_calibrated`'s docstring) become reachable
   for the first time without the owner personally leveling and logging
   five more tank specs.

## Explicitly NOT decided here (left for the build-out pass)

- Exact registry schema / file format (sketched above, not finalized).
- Whether to build the in-app submission panel at all, or stay
  issue-template-only indefinitely (defensible if volume stays low).
- Any anonymization beyond "the submitter chose to share a WCL link" —
  if this ever needs to scale past a handful of trusted submissions, a
  real privacy review is due before that, not now.

## Prerequisite: back up the existing personal log corpus

The retrospective named this as a hard prerequisite for Top-5 #5: the
current Warrior + Guardian calibration corpus (`examples/*.txt`,
`examples/anonguardian1-guardian/*.txt`, `examples/Logs/`, `examples/mai/` — 9 GB
total) is real, personal combat-log data, confirmed untracked by git
(deliberately — these are far too large for a git repo and contain full
party rosters). The retrospective flagged this as having zero backup on
this Pi specifically — see the resolution below for why that turned out
not to be the whole picture.

No cloud backup tool (rclone, a configured cloud CLI) is present on this
machine, and the only local alternative found — an unmounted second-disk
partition (`mmcblk0p2`, an old SD card, 59 GB, likely the Pi's
pre-SSD-migration boot media) — is of unknown provenance, so
unmounting/formatting it wasn't a call to make without asking. Surfaced
directly to the user (2026-07-06) rather than guessed at or worked around.

**Resolved same day — no action needed.** The owner confirmed the log
corpus already exists on a second machine (their gaming PC), independent
of this Pi. The single-copy exposure the retrospective flagged doesn't
apply; the "recovered by luck, not by plan" framing above was based on an
incomplete picture of where copies actually live.
