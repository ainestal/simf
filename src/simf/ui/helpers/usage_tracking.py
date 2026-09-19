"""Session-correlated public-usage analytics (thin layer over core.share_hits).

simf's early instrumentation (`core.share_hits`) answered one narrow question
-- does a shared link convert a stranger? -- and was deliberately built to
record almost nothing, framed as a privacy promise to visitors. Nobody asked
for that promise, and the maintainer (also simf's main user) doesn't want it
traded against actually understanding how the tool gets used. This module
answers the more useful question instead: what do real visitors do, so simf
can be improved on evidence instead of guesses. See `core.usage_report` for
turning the log into a summary.

The fixed-allowlist discipline in `share_hits.record` stays -- not as a
privacy commitment, but because an enumerable set of fields keeps the log
small, keeps the report tool simple, and makes it structurally impossible for
a stray field (a raw exception message, a character name) to leak into an
aggregate analytics log by accident.

Unlike the original cold-share-load signal (still gated to `_is_public_mode()`
at its own call sites in `ui.load`/`ui.app`, since its purpose is specifically
"does a shared link convert a stranger"), events recorded here are NOT
gated on public mode: the maintainer is simf's own primary user, and their
usage is real signal for "how does simf get used," not noise to exclude.
"""

from __future__ import annotations

import secrets

from simf.core import share_hits
from simf.ui.state import _ss

_SID_KEY = "_usage_sid"


def usage_session_id() -> str:
    """Ephemeral per-browser-session id (8 hex chars): lets events emitted
    during one visit be joined into a funnel by `core.usage_report`.
    Generated once per Streamlit session and discarded with it -- never
    derived from IP or identity, never linked across sessions."""
    return _ss().setdefault(_SID_KEY, secrets.token_hex(4))


def record_event(event: str, **fields: str) -> None:
    """Record one usage-analytics event tagged with the session id.
    `fields` must only use keys `core.share_hits` allowlists -- anything
    else is silently dropped by `share_hits.record`, by design."""
    share_hits.record({"event": event, "sid": usage_session_id(), **fields})
