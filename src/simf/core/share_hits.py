"""Public-usage analytics event log (Phase 5, 2026-06-13; broadened 2026-08-16).

Started as a narrow "one-arrow test" (does a shared link convert a
stranger?) and was deliberately built to record almost nothing, framed as a
privacy promise to visitors. That framing was dropped 2026-08-16 -- the
maintainer (also simf's main user) wants real usage data to improve the
tool, not a self-imposed tracking restriction nobody asked for. This module
is now simf's general public-usage event log: `simf.ui.helpers.usage_tracking`
is the preferred way to write to it (adds the session-correlation `sid`
automatically); `core.usage_report` turns the log into a summary.

The fixed allowlist below is still deliberate, for a different reason than
before: an enumerable field set keeps the log small, keeps `usage_report`'s
job simple, and makes it structurally impossible for a stray field (a raw
exception message, a character name) to leak into an aggregate log by
accident. It is a data-quality discipline, not a privacy commitment -- raw
IP/user-agent/exact-identity fields are excluded because they add no
product-analytics value here, not because they're forbidden in principle.

Best-effort: any disk error degrades to "no record" rather than breaking a
visitor's page. Read it with ``wc -l ~/.simf/share_hits.jsonl``, ``count()``,
or ``simf usage-report`` for an aggregated summary.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_PATH = Path.home() / ".simf" / "share_hits.jsonl"

# Explicit allowlist of the only fields ever written — so a future field
# can't silently add unbounded-cardinality or accidentally-identifying data
# (a raw exception message, a character name) to the analytics log.
_RECORDED_FIELDS = (
    "demo",
    "view",
    "event",
    "sid",  # ephemeral per-session correlation id, see ui.helpers.usage_tracking
    "load_method",  # "demo" | "simc" | "online_blizzard" | "online_raiderio" | "online_auto"
    "spec",  # class_spec of the character a verdict was computed for
    "comfortable_max",  # KeyLevelVerdict.comfortable_max, as a string
    "prog_ceiling",  # KeyLevelVerdict.prog_ceiling, as a string
    "wcl_status",  # "success" | "failed_value_error" | "failed_other"
)

# Bound the append-only funnel log so a bot looping the public share URL can't
# grow it without limit (SD-card hygiene). ~80 bytes/line → ~65k records, far
# more than the one-arrow test needs; past this we stop recording (the signal
# "did anyone click" is long since answered). O(1) stat per write.
_MAX_BYTES = 5 * 1024 * 1024


def hits_path(override: Path | None = None) -> Path:
    """Explicit arg > ``SIMF_SHARE_HITS_PATH`` env (tests) > the default."""
    if override is not None:
        return override
    env = os.environ.get("SIMF_SHARE_HITS_PATH")
    return Path(env) if env else _DEFAULT_PATH


def record(fields: dict, *, override_path: Path | None = None) -> None:
    """Append one privacy-safe cold-share-load hit (timestamp + allowlisted
    share params only). Best-effort — disk errors are swallowed."""
    with contextlib.suppress(OSError):
        p = hits_path(override_path)
        # Stop appending past the cap — the funnel signal is long since captured
        # and an unbounded file is an SD-card-fill vector on a public box.
        if p.exists() and p.stat().st_size > _MAX_BYTES:
            return
        entry: dict[str, str] = {"t": datetime.now(UTC).isoformat(timespec="seconds")}
        for k in _RECORDED_FIELDS:
            v = fields.get(k)
            if v:
                entry[k] = str(v)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def count(*, override_path: Path | None = None) -> int:
    """Number of recorded cold-share loads (0 when the file doesn't exist)."""
    p = hits_path(override_path)
    try:
        with p.open() as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0
