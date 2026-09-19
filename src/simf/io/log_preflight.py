"""Cheap log-health preflight — a structural sanity check before the heavy
per-run analysis renders.

Batch D (2026-07-07 Active Triage Queue, see ROADMAP.md). Combat logs can be
ACL-on (rich ``COMBATANT_INFO``, see ``combat_log_roles.py``) or ACL-off,
which degrades many downstream surfaces silently and separately (grep "ACL"
across ``ui/*.py`` / ``io/*.py`` — ``log_coaching.py``, ``log_formatters.py``,
``log_surface.py``, ``log_data.py``, ``log_segment_risk.py`` each have their
own quiet fallback). There was no single upfront check that told the user
*why* some detail might be missing, or that the file they picked is even a
real combat log. This module is that check — read the first handful of
lines, don't duplicate the full parse the rest of the pipeline already does.

WoW writes the log-format header as the very first line of a
``WoWCombatLog-*.txt`` file:

    ``5/17/2026 13:48:19.1681  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,0,BUILD_VERSION,12.0.5,PROJECT_ID,1``

confirmed empirically against real logs in ``examples/`` (both
``ADVANCED_LOG_ENABLED,0`` and ``,1`` corpora exist). This module scans only
the first ``_HEADER_SCAN_LINE_CAP`` lines for it — bounded, so a
multi-hundred-MB log costs nothing extra to preflight.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from simf.io.combat_log_core import parse_combat_log_line

# This codebase targets COMBAT_LOG_VERSION 22 (Midnight 12.0.5) — see
# combat_log_core.py's own module docstring. Centralized here as the one
# place that actually checks it (grep confirmed zero enforcement existed
# anywhere before this module).
TARGET_COMBAT_LOG_VERSION = 22

# The header line is always at or near the very top of the file. Capping the
# scan keeps this "cheap" even on the largest logs in the corpus (250+ MB) —
# WoW has been observed re-emitting COMBAT_LOG_VERSION later in a long
# session log too, but the first occurrence is what describes how the log
# started recording, which is what a preflight check should report.
_HEADER_SCAN_LINE_CAP = 200


@dataclass(frozen=True)
class LogHealthReport:
    """Result of a cheap structural scan over a combat log file.

    ``None`` fields mean "couldn't determine" (no header line found in the
    scanned window), not "false" — an empty/corrupt file reports ``None``
    for both ACL and version, plus ``is_empty``/``has_recognizable_line``
    flagged so the caller can tell the two failure modes apart.
    """

    acl_enabled: bool | None
    found_log_version: int | None
    is_empty: bool
    has_recognizable_line: bool

    @property
    def version_matches(self) -> bool | None:
        """None when no version was found at all (nothing to compare)."""
        if self.found_log_version is None:
            return None
        return self.found_log_version == TARGET_COMBAT_LOG_VERSION


def log_health_preflight(log_path: Path) -> LogHealthReport:
    """Scan the first ``_HEADER_SCAN_LINE_CAP`` lines of ``log_path``.

    Does NOT do a full parse — that's ``_cached_run_events`` /
    ``_cached_log_summary``'s job downstream. Reports:

    - ``acl_enabled``: from the header's ``ADVANCED_LOG_ENABLED`` flag.
    - ``found_log_version`` / ``version_matches``: from the header's
      ``COMBAT_LOG_VERSION`` field, against ``TARGET_COMBAT_LOG_VERSION``.
    - ``is_empty``: the file doesn't exist or is zero bytes.
    - ``has_recognizable_line``: at least one line in the scanned window
      parses as a combat-log line at all (catches a truncated/corrupted/
      wrong-file upload — e.g. someone uploads a `/simc` export by mistake).
    """
    if not log_path.exists() or log_path.stat().st_size == 0:
        return LogHealthReport(
            acl_enabled=None,
            found_log_version=None,
            is_empty=True,
            has_recognizable_line=False,
        )

    acl_enabled: bool | None = None
    found_log_version: int | None = None
    has_recognizable_line = False

    with log_path.open(errors="replace") as f:
        for i, line in enumerate(f):
            if i >= _HEADER_SCAN_LINE_CAP:
                break
            parsed = parse_combat_log_line(line)
            if parsed is None:
                continue
            has_recognizable_line = True
            _time_s, event_type, payload = parsed
            if event_type == "COMBAT_LOG_VERSION" and found_log_version is None:
                # payload: ["22", "ADVANCED_LOG_ENABLED", "1", "BUILD_VERSION", ...]
                with contextlib.suppress(IndexError, ValueError):
                    found_log_version = int(payload[0])
                if len(payload) >= 3 and payload[1] == "ADVANCED_LOG_ENABLED":
                    acl_enabled = payload[2] == "1"
            if found_log_version is not None and has_recognizable_line:
                break

    return LogHealthReport(
        acl_enabled=acl_enabled,
        found_log_version=found_log_version,
        is_empty=False,
        has_recognizable_line=has_recognizable_line,
    )
