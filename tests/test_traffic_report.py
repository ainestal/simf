"""Unit tests for ``scripts/traffic_report.py``.

The script lives outside the ``src/`` package (same convention as
``prune_merged_branches.py``/``screenshot.py``), so it's imported via a
path-based loader rather than an install step. Its internal ``simf.core``
import still resolves normally since the package itself IS installed.

Every test that touches "now" passes an explicit ``now`` so the suite is
deterministic — never real wall-clock time.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "traffic_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("traffic_report", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, "spec_from_file_location failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["traffic_report"] = mod
    spec.loader.exec_module(mod)
    return mod


tr = _load_module()

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ─── missing / empty file ────────────────────────────────────────────────────


def test_missing_file_prints_no_data_message(tmp_path):
    missing = tmp_path / "nope.jsonl"
    out = tr.generate_report(missing, days=7, now=NOW)
    assert "no data yet" in out.lower()
    assert "nope.jsonl" in out


def test_empty_file_prints_no_data_message(tmp_path):
    p = tmp_path / "hits.jsonl"
    p.write_text("")
    out = tr.generate_report(p, days=7, now=NOW)
    assert "no data yet" in out.lower()


def test_main_exits_zero_on_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(tmp_path / "nope.jsonl"))
    rc = tr.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no data yet" in out.lower()


# ─── view_reached vs legacy cold-share tallies ───────────────────────────────


def test_mix_of_view_reached_and_legacy_cold_share_lines(tmp_path):
    p = tmp_path / "hits.jsonl"
    recent = NOW - timedelta(hours=1)
    _write_lines(
        p,
        [
            json.dumps({"t": _iso(recent), "event": "view_reached", "view": "gear"}),
            json.dumps({"t": _iso(recent), "event": "view_reached", "view": "gear"}),
            json.dumps({"t": _iso(recent), "event": "view_reached", "view": "log"}),
            # Legacy cold-share hits — no "event" field at all.
            json.dumps({"t": _iso(recent), "demo": "brutoh", "view": "gear"}),
            json.dumps({"t": _iso(recent), "demo": "brutoh"}),
        ],
    )
    entries = tr._read_entries(p)
    report = tr.build_report(entries, now=NOW, days=7)
    assert report.total == 5
    assert report.by_event["view_reached"] == 3
    assert report.by_event[tr.COLD_SHARE_LABEL] == 2
    # The view breakdown is scoped to view_reached events specifically — the
    # legacy hits' own "view" field (share-URL params, a different meaning)
    # must not leak into this tally.
    assert report.views_by_view == {"gear": 2, "log": 1}

    text = tr.format_report(report, days=7)
    assert "view_reached" in text
    assert tr.COLD_SHARE_LABEL in text
    assert "gear" in text and "log" in text


def test_malformed_line_is_skipped_not_crashing(tmp_path):
    p = tmp_path / "hits.jsonl"
    recent = NOW - timedelta(hours=1)
    _write_lines(
        p,
        [
            json.dumps({"t": _iso(recent), "event": "view_reached", "view": "gear"}),
            "{not valid json,,,",  # torn/partial write
            json.dumps({"t": _iso(recent), "event": "view_reached", "view": "log"}),
        ],
    )
    entries = tr._read_entries(p)
    assert len(entries) == 2  # the malformed line is skipped, not raised
    report = tr.build_report(entries, now=NOW, days=7)
    assert report.total == 2
    assert report.views_by_view == {"gear": 1, "log": 1}


def test_non_dict_json_line_is_skipped(tmp_path):
    """A well-formed JSON line that isn't an object (e.g. a bare list or
    number) is not a valid hit record — skip it like a malformed line."""
    p = tmp_path / "hits.jsonl"
    recent = NOW - timedelta(hours=1)
    _write_lines(
        p,
        [
            json.dumps([1, 2, 3]),
            json.dumps({"t": _iso(recent), "event": "view_reached", "view": "gear"}),
        ],
    )
    entries = tr._read_entries(p)
    assert len(entries) == 1


# ─── --days cutoff ────────────────────────────────────────────────────────────


def test_days_cutoff_excludes_an_old_entry(tmp_path):
    p = tmp_path / "hits.jsonl"
    old = NOW - timedelta(days=30)
    recent = NOW - timedelta(hours=2)
    _write_lines(
        p,
        [
            json.dumps({"t": _iso(old), "event": "view_reached", "view": "gear"}),
            json.dumps({"t": _iso(recent), "event": "view_reached", "view": "log"}),
        ],
    )
    entries = tr._read_entries(p)
    report = tr.build_report(entries, now=NOW, days=7)
    assert report.total == 1
    assert report.views_by_view == {"log": 1}


def test_days_flag_widens_the_window(tmp_path):
    p = tmp_path / "hits.jsonl"
    old = NOW - timedelta(days=10)
    _write_lines(p, [json.dumps({"t": _iso(old), "event": "view_reached", "view": "gear"})])
    entries = tr._read_entries(p)
    # Excluded at the default 7-day window...
    assert tr.build_report(entries, now=NOW, days=7).total == 0
    # ...but included once the window widens past it.
    assert tr.build_report(entries, now=NOW, days=14).total == 1


def test_entry_missing_timestamp_is_excluded(tmp_path):
    p = tmp_path / "hits.jsonl"
    _write_lines(p, [json.dumps({"event": "view_reached", "view": "gear"})])
    entries = tr._read_entries(p)
    report = tr.build_report(entries, now=NOW, days=7)
    assert report.total == 0
