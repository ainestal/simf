"""Read ``~/.simf/share_hits.jsonl`` and print a small traffic report.

Follow-through from the "who visits simf.cc and do they find it useful"
workshop (2026-07-31). ``share_hits.py`` (see its own module docstring) is
the ONLY traffic signal simf records — a privacy-safe, append-only JSON-lines
log with no IP, user-agent, session id, or any PII. This script is the other
half: something that actually reads the log and answers the concrete
question the owner cares about — "which top-level view do visitors ever
reach" (Gear/Vault vs "Why did I die?") — rather than the data sitting
unread on disk.

Two kinds of line can appear in the log, both written by the same
``share_hits.record()``:

  * legacy cold-share hits (Phase 5, pre-2026-07-31) — no ``"event"`` field
    at all, just whichever of ``demo``/``view`` the share URL carried. This
    script buckets those as ``cold_share`` for display only; it never
    writes a new literal value back into the log file for old entries.
  * ``"view_reached"`` events (2026-07-31 on) — one per session per
    distinct view, the first time that session's render reaches it.

Usage::

    .venv/bin/python scripts/traffic_report.py
    .venv/bin/python scripts/traffic_report.py --days 14

Resilient by design, matching ``share_hits.py``'s own best-effort
philosophy: a missing log file prints a plain "no data yet" and exits 0
(not an error — a fresh install or a quiet week are both normal); a
malformed or partial line (e.g. a write torn by a concurrent process) is
skipped rather than crashing the whole report.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from simf.core.share_hits import hits_path

# Display-only bucket label for a legacy hit with no "event" field — never
# written back into the log file itself, just how this report presents it.
COLD_SHARE_LABEL = "cold_share"


@dataclass(frozen=True)
class TrafficReport:
    """Tallies for one report window. ``by_event`` covers every entry in the
    window (``cold_share`` substituting for a missing ``"event"``);
    ``views_by_view`` only covers ``view_reached`` entries specifically —
    the "which surface do visitors reach" answer."""

    total: int
    by_event: Counter[str]
    views_by_view: Counter[str]


def _read_entries(path: Path) -> list[dict]:
    """Read every well-formed JSON *object* line from ``path``.

    Best-effort: a missing file, an unreadable file, or any individual
    malformed/partial line is skipped rather than raising — one bad line
    (e.g. a write torn by a concurrent process) must not sink the whole
    report.
    """
    try:
        raw = path.read_text()
    except OSError:
        return []
    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _parse_timestamp(entry: dict) -> datetime | None:
    t = entry.get("t")
    if not isinstance(t, str):
        return None
    try:
        ts = datetime.fromisoformat(t)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def build_report(entries: list[dict], *, now: datetime, days: int) -> TrafficReport:
    """Pure, deterministic core: filter ``entries`` to the trailing
    ``days``-day window ending at ``now``, then tally by event and (within
    ``view_reached`` specifically) by view. Takes ``now`` explicitly — never
    reads the real clock — so it's trivially testable; callers pick the
    cutoff, not this function.
    """
    cutoff = now - timedelta(days=days)
    by_event: Counter[str] = Counter()
    views_by_view: Counter[str] = Counter()
    total = 0
    for entry in entries:
        ts = _parse_timestamp(entry)
        if ts is None or ts < cutoff:
            continue
        total += 1
        event = entry.get("event") or COLD_SHARE_LABEL
        by_event[event] += 1
        if event == "view_reached":
            views_by_view[entry.get("view") or "unknown"] += 1
    return TrafficReport(total=total, by_event=by_event, views_by_view=views_by_view)


def format_report(report: TrafficReport, *, days: int) -> str:
    lines = [f"simf traffic report — last {days}d", f"total hits: {report.total}"]
    if report.total == 0:
        return "\n".join(lines)
    lines.append("")
    lines.append("by event:")
    for event, count in report.by_event.most_common():
        lines.append(f"  {event:<14} {count}")
    if report.views_by_view:
        lines.append("")
        lines.append("view_reached by view:")
        for view, count in report.views_by_view.most_common():
            lines.append(f"  {view:<14} {count}")
    return "\n".join(lines)


def generate_report(path: Path, *, days: int = 7, now: datetime | None = None) -> str:
    """Read ``path``, window it to the trailing ``days`` days, and return the
    formatted report text. ``now`` defaults to the real UTC clock — but that
    default resolves ONLY here, at this one call boundary; the testable
    logic above (``build_report``/``_read_entries``/``_parse_timestamp``)
    never touches the real clock. Tests should always pass an explicit
    ``now`` for determinism.
    """
    if now is None:
        now = datetime.now(UTC)
    if not path.exists():
        return f"No data yet — {path} doesn't exist."
    entries = _read_entries(path)
    if not entries:
        return f"No data yet — {path} has no recorded hits."
    report = build_report(entries, now=now, days=days)
    return format_report(report, days=days)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--days",
        type=int,
        default=7,
        help="trailing window size in days, ending now (default: 7)",
    )
    args = ap.parse_args(argv)
    print(generate_report(hits_path(), days=args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
