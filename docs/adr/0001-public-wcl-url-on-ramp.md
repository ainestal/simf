# ADR 0001 — Public WCL-URL on-ramp (owner key + rate limiting)

- **Status:** Accepted (2026-06-14)
- **Deciders:** Brutoh (owner)
- **Context tags:** public deploy, Warcraft Logs, "Why did I die?", rate limiting

## Context

simf's hosted instance is going public (`SIMF_PUBLIC=1`, fronted by a
Cloudflare tunnel at `simf.cc`) — unrelated to this repo's own GitHub
visibility. In public mode the entire "Why did I die?" surface is hidden: the
router gate `if view == "log" and not _is_public_mode()` (`app.py:4586`) is dead,
and the nav button isn't rendered (`app.py:4292`). A cold visitor therefore only
ever sees the SimC-paste / gear / vault surfaces.

That means **death reconstruction — simf's actual differentiator (the only free
tank survivability sim; Raidbots dropped it, QE is healer-only, AMR paywalls
it) — is invisible to the exact audience a public link is for.** The
retrospective surfaces (death reconstruction, mitigation audit, danger ranking,
per-segment risk) all require a *real fight*, not a static character snapshot.

Two research passes (2026-06-14, see the session findings) established the auth
reality for every input that could feed a real fight publicly:

| Input | Feeds why-died? | Needs user creds? | Public-safe today? |
|-------|-----------------|-------------------|--------------------|
| Combat-log upload | yes (richest) | no | **no** — OOM/abuse vector (100 MB uploads from strangers) |
| **WCL report URL** | **yes** | **no** — any client-credentials token reads a *public* report | gated off only because it lives inside the same hidden log surface |
| Armory / Raider.IO | no (static gear) | no | hidden (owner creds + SSRF region-interpolation) |
| Blizzard user-OAuth | no | yes | irrelevant — see below |

Key external findings (sources in the research transcript):

1. **WCL public reports are readable with an app client-credentials token — no
   user login.** simf already authenticates this way (`wcl_api.py:59-95`,
   `_get_token()` takes no args, client-credentials). User authorization-code
   login only unlocks *private/unlisted* reports.
2. **WCL rate limiting is billed per client application (~3,600 points/hour free
   tier), not per user token.** So "users bring their own login" (OAuth-on-behalf
   with our client_id) would **not** distribute the quota — it stays pooled on
   our one key. Only true bring-your-own-key (the user registers a separate WCL
   app) distributes it, and ~1% of users will ever do that.
3. **Blizzard exposes no Great Vault endpoint — public or protected — and never
   has** (feature request open since Jan 2021). Equipment is already public via
   client-credentials. So Blizzard user-OAuth buys simf nothing: the vault stays
   `/simc`-paste-only forever, and gear is already reachable without a user login.

Given (1)–(3), the moat does **not** require per-user authentication. It requires
exposing the existing WCL-URL flow publicly on the owner's app key, defended
against exhausting the shared rate-limit budget.

## Decision

**Expose the Warcraft Logs URL flow — and only that flow — in public mode,
backed by the owner's client-credentials WCL key, behind a layered rate-limit
guard.**

Specifically:

1. **Surface.** In public mode, `view == "log"` renders a WCL-URL-only variant
   of the "Why did I die?" surface (no local-file upload tab). The full two-tab
   surface (`render_surface_log`, `log_view.py:2362`) stays for owner mode. The
   public header regains a "Why did I die?" nav button.
2. **Auth.** Owner client-credentials key, injected via the private deploy
   tooling's systemd unit (not included in this repo).
   **No redirect URI / OAuth flow** — client-credentials reads public reports.
3. **Rate-limit defense in depth** (the substantive new work):
   - **Disk cache (exists):** `analyze_wcl_fight` is cached on
     `(report, fight, target)` (`wcl_bridge.py:119+`) — repeat analyses of a
     shared/viral log cost zero points.
   - **Pre-flight budget check (new):** query WCL `rateLimitData`; if remaining
     points fall below a reserve, refuse with a friendly "Warcraft Logs is busy —
     try again in N minutes" (from `pointsResetIn`) instead of a raw 429.
   - **Global rolling-window limiter (new):** process-wide cap on WCL fetches per
     minute / per hour (thread-safe, in-memory).
   - **Single-flight (new, mirrors `_sim_slot`):** a `_wcl_slot()` so concurrent
     strangers serialize rather than firing N paginated fetches at once.
   - **Per-session cooldown (new):** one analyze per session per X seconds.
   - **Fight-size cap (new, public only):** bound a single analysis's page count
     / fight length so one request can't burn the whole budget.
4. **Defer / drop:**
   - **OAuth-on-behalf (authorization-code):** deferred — only unlocks private
     logs, does not relieve the quota, and adds a token-in-session attack surface
     on a shared single-process box (and simf echoes query params into share
     URLs, so a `?code=` leak is a real hazard). Revisit only if private-log
     demand is real.
   - **Bring-your-own-key:** deferred to a later optional Advanced escape hatch
     ("paste your own WCL key for higher limits / private logs"); never the front
     door.
   - **Blizzard user-OAuth:** dropped permanently — buys nothing (no vault
     endpoint; equipment already public).
   - **Hydrate-from-WCL:** stays deferred (already noted at `log_view.py:2998`).
     A cold public visitor's analysis uses a default character, so stat-weight
     numbers are approximate; the death reconstruction / mitigation audit /
     verdict are fight-driven and correct. Surface the existing caveat.

## Consequences

**Positive**
- The differentiator becomes reachable by the public audience it's meant for: a
  Discord/Reddit visitor can paste a WCL URL and get a death reconstruction with
  no local files, no install, no login.
- No new accounts, no persistence — the "session-state only" design rule holds.
- WCL flow is read-only (no gear mutation; `cd_plan_context=None`), so it fits
  the public read-only posture (`_is_read_only()`) with no new mutation surface.
- Low SSRF risk: the flow only ever hits the fixed `warcraftlogs.com` API host;
  the report code is a GraphQL *variable*, not an interpolated request host
  (contrast the armory region-interpolation surface that keeps armory gated).

**Negative / accepted**
- The owner's single WCL key is a shared resource. Mitigated by the layered
  limiter + Cloudflare edge rate-limit, but a determined abuser could still spend
  the budget; the worst case is a temporary "WCL busy" for other visitors, not a
  data or money loss. Acceptable.
- Public visitors get *public-report* analysis only; private/unlisted logs are
  unsupported until (and unless) OAuth-on-behalf is built.
- Cold-visitor analyses run on a default character → approximate stat weights.
  Known, captioned, and orthogonal to the death-reconstruction value.

**Reversibility**
- Fully reversible: revert the surface gate (one-line router change) and remove
  the creds from the unit. No data migration, no schema, no user state.

## Alternatives considered

- **A. Keep public paste-only, add a signpost** that death analysis exists
  locally. Cheapest, but leaves the moat invisible to the public audience —
  rejected as the *primary* move (still worth a small signpost regardless).
- **B. OAuth-on-behalf so users pull their own data.** Rejected as the lead
  option: per the rate-limit finding it does not distribute quota, only unlocks
  private logs, and adds real attack surface for marginal benefit.
- **C. Bring-your-own-key as the front door.** Rejected: brutal UX (register a
  WCL app), and a secret pasted into a shared process. Kept as a deferred
  power-user escape hatch.
- **D. Blizzard user-OAuth for live gear/vault.** Rejected permanently: no vault
  endpoint exists, equipment is already public.

## Implementation seams (for the PR plan)

*Note: line numbers below are as of this proposal's date (2026-06-14); the
codebase has since been split into smaller modules (see CONTRIBUTING.md's
orientation section) — locate by function/symbol name (`main`,
`_render_surface_log`, `_render_header`, `render_surface_log`,
`_render_wcl_url_flow`, `_get_token`/`fetch_report`, `fetch_rate_limit`)
rather than by these stale file:line references.*

- Surface gate / router: `app.py:4586` (`main`), `app.py:4198` (`_render_surface_log`).
- Public header nav: `app.py:4288-4294` (`_render_header`).
- WCL-only render: `log_view.py:2362` (`render_surface_log`, add a public flag),
  `log_view.py:2985` (`_render_wcl_url_flow`, self-contained).
- WCL entry points to guard: `_get_token` / `fetch_report` / `analyze_wcl_fight`
  (`log_view.py:3063, 3161`).
- New budget/limiter module + `fetch_rate_limit(token)` in `wcl_api.py`.
- Creds: injected via the private deploy tooling's systemd unit (not included
  in this repo).
