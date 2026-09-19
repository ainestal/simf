"""Tests for `simf usage-report` — summarizes the usage-analytics JSONL via
`core.usage_report.build_usage_report` (2026-08-16)."""

from __future__ import annotations

import json

from typer.testing import CliRunner


def _write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_usage_report_prints_summary_for_given_path(tmp_path):
    from simf.cli import app as cli_app

    p = tmp_path / "hits.jsonl"
    _write(
        p,
        [
            {"t": "2026-08-01T00:00:00+00:00", "sid": "a", "event": "view_reached", "view": "gear"},
            {
                "t": "2026-08-01T00:00:01+00:00",
                "sid": "a",
                "event": "character_loaded",
                "load_method": "demo",
            },
        ],
    )
    runner = CliRunner()
    result = runner.invoke(cli_app, ["usage-report", "--path", str(p)])
    assert result.exit_code == 0
    assert "Total events: 2" in result.output
    assert "Unique sessions: 1" in result.output
    assert "gear: 1" in result.output
    assert "demo: 1" in result.output


def test_usage_report_missing_file_prints_zero_report(tmp_path):
    from simf.cli import app as cli_app

    runner = CliRunner()
    result = runner.invoke(cli_app, ["usage-report", "--path", str(tmp_path / "nope.jsonl")])
    assert result.exit_code == 0
    assert "Total events: 0" in result.output
    assert "no events recorded yet" in result.output
