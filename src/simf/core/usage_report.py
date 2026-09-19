"""Aggregate simf's usage-analytics event log (`core.share_hits`) into a
human-readable summary — answers "how is simf actually being used?" instead
of leaving the raw JSONL to be grepped by hand.

Pure function: no Streamlit dependency, no I/O beyond reading the one JSONL
file, so it's testable and usable from both `simf usage-report` and tests.
See `ui.helpers.usage_tracking` for what gets written and why.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from simf.core import share_hits


@dataclass
class UsageReport:
    total_events: int
    unique_sessions: int
    first_seen: str | None
    last_seen: str | None
    views_reached: Counter
    load_methods: Counter
    specs_simmed: Counter
    wcl_outcomes: Counter
    comfortable_max_values: list[int]
    prog_ceiling_values: list[int]

    def render(self) -> str:
        lines = [
            f"Total events: {self.total_events}",
            f"Unique sessions: {self.unique_sessions}",
        ]
        if self.first_seen and self.last_seen:
            lines.append(f"Date range: {self.first_seen} .. {self.last_seen}")

        def _section(title: str, counter: Counter) -> None:
            if not counter:
                return
            lines.append("")
            lines.append(title)
            for key, n in counter.most_common():
                lines.append(f"  {key}: {n}")

        _section("Views reached:", self.views_reached)
        _section("Character load method:", self.load_methods)
        _section("Specs simmed for a verdict:", self.specs_simmed)
        _section("Why-did-I-die (WCL) outcomes:", self.wcl_outcomes)

        def _key_level_stats(title: str, values: list[int]) -> None:
            if not values:
                return
            vals = sorted(values)
            n = len(vals)
            median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
            lines.append("")
            lines.append(f"{title} — n={n}, median={median:g}, min={vals[0]}, max={vals[-1]}")

        _key_level_stats("Comfortable key-level ceiling", self.comfortable_max_values)
        _key_level_stats("Progression key-level ceiling", self.prog_ceiling_values)

        if self.total_events == 0:
            lines.append("(no events recorded yet)")
        return "\n".join(lines)


def _parse_int(value: str | None) -> int | None:
    if not value or value == "None":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def build_usage_report(*, override_path: Path | None = None) -> UsageReport:
    """Read the usage-analytics event log and aggregate it. A missing or
    empty file yields an all-zero report, matching `share_hits.count()`'s
    missing-file behavior."""
    path = share_hits.hits_path(override_path)
    try:
        raw_lines = path.read_text().splitlines()
    except OSError:
        raw_lines = []

    sessions: set[str] = set()
    views_reached: Counter = Counter()
    load_methods: Counter = Counter()
    specs_simmed: Counter = Counter()
    wcl_outcomes: Counter = Counter()
    comfortable_max_values: list[int] = []
    prog_ceiling_values: list[int] = []
    timestamps: list[str] = []
    total = 0

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        total += 1
        if entry.get("t"):
            timestamps.append(entry["t"])
        if entry.get("sid"):
            sessions.add(entry["sid"])

        event = entry.get("event")
        if event == "view_reached" and entry.get("view"):
            views_reached[entry["view"]] += 1
        elif event == "character_loaded" and entry.get("load_method"):
            load_methods[entry["load_method"]] += 1
        elif event == "verdict_computed":
            if entry.get("spec"):
                specs_simmed[entry["spec"]] += 1
            cm = _parse_int(entry.get("comfortable_max"))
            if cm is not None:
                comfortable_max_values.append(cm)
            pc = _parse_int(entry.get("prog_ceiling"))
            if pc is not None:
                prog_ceiling_values.append(pc)
        elif event == "wcl_flow" and entry.get("wcl_status"):
            wcl_outcomes[entry["wcl_status"]] += 1

    timestamps.sort()
    return UsageReport(
        total_events=total,
        unique_sessions=len(sessions),
        first_seen=timestamps[0] if timestamps else None,
        last_seen=timestamps[-1] if timestamps else None,
        views_reached=views_reached,
        load_methods=load_methods,
        specs_simmed=specs_simmed,
        wcl_outcomes=wcl_outcomes,
        comfortable_max_values=comfortable_max_values,
        prog_ceiling_values=prog_ceiling_values,
    )
