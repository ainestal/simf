"""Tests for `core.usage_report` — aggregating the usage-analytics JSONL
(`core.share_hits`) into a human-readable summary (2026-08-16, `simf
usage-report`)."""

from __future__ import annotations

import json

from simf.core.usage_report import build_usage_report


def _write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_missing_file_yields_all_zero_report(tmp_path):
    report = build_usage_report(override_path=tmp_path / "nope.jsonl")
    assert report.total_events == 0
    assert report.unique_sessions == 0
    assert report.first_seen is None
    assert report.last_seen is None
    assert "no events recorded yet" in report.render()


def test_counts_events_and_unique_sessions(tmp_path):
    p = tmp_path / "hits.jsonl"
    _write(
        p,
        [
            {
                "t": "2026-08-01T00:00:00+00:00",
                "sid": "aaaa",
                "event": "view_reached",
                "view": "gear",
            },
            {
                "t": "2026-08-01T00:00:05+00:00",
                "sid": "aaaa",
                "event": "view_reached",
                "view": "log",
            },
            {
                "t": "2026-08-02T00:00:00+00:00",
                "sid": "bbbb",
                "event": "view_reached",
                "view": "gear",
            },
        ],
    )
    report = build_usage_report(override_path=p)
    assert report.total_events == 3
    assert report.unique_sessions == 2
    assert report.views_reached["gear"] == 2
    assert report.views_reached["log"] == 1
    assert report.first_seen == "2026-08-01T00:00:00+00:00"
    assert report.last_seen == "2026-08-02T00:00:00+00:00"


def test_aggregates_load_methods_specs_and_wcl_outcomes(tmp_path):
    p = tmp_path / "hits.jsonl"
    _write(
        p,
        [
            {
                "t": "2026-08-01T00:00:00+00:00",
                "sid": "a",
                "event": "character_loaded",
                "load_method": "demo",
            },
            {
                "t": "2026-08-01T00:00:01+00:00",
                "sid": "a",
                "event": "character_loaded",
                "load_method": "simc",
            },
            {
                "t": "2026-08-01T00:00:02+00:00",
                "sid": "a",
                "event": "character_loaded",
                "load_method": "simc",
            },
            {
                "t": "2026-08-01T00:00:03+00:00",
                "sid": "a",
                "event": "verdict_computed",
                "spec": "protection_warrior",
                "comfortable_max": "16",
                "prog_ceiling": "19",
            },
            {
                "t": "2026-08-01T00:00:04+00:00",
                "sid": "a",
                "event": "verdict_computed",
                "spec": "protection_warrior",
                "comfortable_max": "14",
                "prog_ceiling": "None",
            },
            {
                "t": "2026-08-01T00:00:05+00:00",
                "sid": "a",
                "event": "wcl_flow",
                "wcl_status": "success",
            },
            {
                "t": "2026-08-01T00:00:06+00:00",
                "sid": "a",
                "event": "wcl_flow",
                "wcl_status": "failed_other",
            },
        ],
    )
    report = build_usage_report(override_path=p)
    assert report.load_methods == {"demo": 1, "simc": 2}
    assert report.specs_simmed == {"protection_warrior": 2}
    assert report.wcl_outcomes == {"success": 1, "failed_other": 1}
    assert report.comfortable_max_values == [16, 14]
    assert report.prog_ceiling_values == [19]  # "None" is skipped, not parsed as 0


def test_render_includes_key_level_stats_and_sections(tmp_path):
    p = tmp_path / "hits.jsonl"
    _write(
        p,
        [
            {
                "t": "2026-08-01T00:00:00+00:00",
                "sid": "a",
                "event": "verdict_computed",
                "spec": "protection_warrior",
                "comfortable_max": "14",
            },
            {
                "t": "2026-08-01T00:00:01+00:00",
                "sid": "b",
                "event": "verdict_computed",
                "spec": "protection_warrior",
                "comfortable_max": "16",
            },
        ],
    )
    report = build_usage_report(override_path=p)
    text = report.render()
    assert "Total events: 2" in text
    assert "Unique sessions: 2" in text
    assert "Specs simmed for a verdict:" in text
    assert "protection_warrior: 2" in text
    assert "Comfortable key-level ceiling — n=2, median=15, min=14, max=16" in text


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    p = tmp_path / "hits.jsonl"
    p.write_text(
        '{"t": "2026-08-01T00:00:00+00:00", "sid": "a", "event": "view_reached", "view": "gear"}\nnot json\n\n'
    )
    report = build_usage_report(override_path=p)
    assert report.total_events == 1
    assert report.views_reached["gear"] == 1
