"""Blizzard-affiliation disclaimer + privacy notice — always-visible footer.

A 2026-08 readiness audit found simf had neither, despite using the
Blizzard Battle.net API, Raider.IO's API, and Warcraft Logs' API to serve
public visitors. Blizzard's Developer API Terms of Use make both an
explicit, named, contractual condition of using the API at all (not
etiquette): "You shall clearly and conspicuously identify Blizzard... as
the source of the Data... in such a way which makes it not appear that
Blizzard is endorsing or affiliated with Your Application", and "You must
post a privacy policy governing the use of the Data."

Updated 2026-08-16: the original copy described a deliberately minimal,
maximally-restrictive tracking posture, framed as a promise to visitors.
Nobody asked for that promise, and the maintainer (simf's own main user)
would rather have real usage data to improve the tool than hold to a
self-imposed restriction. The blurb below is a plain, honest description of
the current architecture, not a claim about how little is tracked:

  - Still no accounts. A character/gear lookup is proxied through simf's
    own server (Raider.IO zero-auth, or Blizzard client-credentials) and
    held only in Streamlit's server-side session state — gone when the tab
    closes, never written to disk. That part of the architecture hasn't
    changed.
  - The usage-analytics event log (`core.share_hits`, written via
    `ui.helpers.usage_tracking`) now records more than before: which views
    you reach, how you loaded a character (demo / SimC paste / online
    lookup), which spec and key-level range a verdict was computed for, and
    whether a Warcraft-Logs-URL fetch succeeded — tagged with a random,
    ephemeral per-session id so those events can be joined into a funnel.
    Still no raw IP, user-agent, or account-linked identity — not because
    of a policy promise, but because none of that is useful for the
    product questions this log exists to answer, and an enumerable field
    list keeps the log simple to read.
  - The public "Why did I die?" Warcraft-Logs-URL flow caches the fetched
    analysis bundle on disk (`io.log_analysis_cache`, `~/.simf/log_cache/`),
    keyed by (report, fight, target) rather than by session, and bounded by
    a 20-entry / 500 MB LRU cap. WCL report data is itself already public,
    so caching the parsed result is caching public data, not visitor data.
"""

from __future__ import annotations

import streamlit as st

DISCLAIMER_TITLE = "Privacy & disclaimers"

BLIZZARD_DISCLAIMER = (
    "simf is a fan-made tool. It is not affiliated with, endorsed, or "
    "sponsored by Blizzard Entertainment, SimulationCraft, or Warcraft Logs."
)

ATTRIBUTION = (
    "Character data via the Blizzard Battle.net API and Raider.IO. "
    "Combat-log analysis via Warcraft Logs."
)

PRIVACY_BLURB = (
    "No accounts. The character/gear data you look up is proxied through "
    "simf's server and held only in your browser session — gone when you "
    "close the tab, never written to disk. simf does record usage analytics "
    "to understand how the tool gets used and improve it — which pages you "
    "reach, how you loaded your character, and which spec/key level a "
    "verdict was for — tagged with a random per-visit id, never your IP, "
    'browser, or account identity. One exception: the public "Why did I '
    'die?" Warcraft Logs-URL flow caches the fetched analysis on the server '
    "(bounded by size, not tied to your session), since WCL report data is "
    "itself already public."
)


def render_privacy_footer() -> None:
    """Always-visible Blizzard disclaimer caption + a collapsed expander for
    the longer attribution/privacy blurb.

    Always rendered — every view, every mode (see `ui.app.main`'s call
    site next to `format_build_footer`) — because both disclosures are
    contractual conditions of the Blizzard API, not etiquette, so they
    can't be gated behind a mode check the way the trust-strip caveats
    are. The affiliation disclaimer specifically must "clearly and
    conspicuously identify Blizzard" per the API terms quoted in this
    module's docstring, so it renders as its own caption rather than
    behind a click-to-reveal expander; the longer attribution + privacy
    text stays collapsed for brevity.
    """
    st.caption(BLIZZARD_DISCLAIMER)
    with st.expander(DISCLAIMER_TITLE, expanded=False):
        st.caption(ATTRIBUTION)
        st.caption(PRIVACY_BLURB)
