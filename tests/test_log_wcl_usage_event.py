"""`_run_wcl_analysis` must record a `wcl_flow` usage-analytics event with
the outcome (success / failed_value_error / failed_other) — 2026-08-16
usage-analytics broadening. Mirrors `tests/test_log_wcl_error_copy.py`'s
direct-call pattern for exercising each branch without a real WCL fetch.

`AppTest.from_function` only executes the function's OWN source text in an
isolated script context — no enclosing-module closures — so every import
and helper must be defined inside each `_script()` body, same as
`test_log_wcl_error_copy.py`'s existing pattern.
"""

from __future__ import annotations

import json

from streamlit.testing.v1 import AppTest


def test_success_records_wcl_status_success(monkeypatch, tmp_path):
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))

    def _script() -> None:
        from simf.ui.log_wcl import _run_wcl_analysis

        _run_wcl_analysis(
            lambda *a, **k: {"ok": True},
            report=None,
            fight=None,
            target="Brutoh-Uldum-EU",
            token="fake-token",
            target_actor_id=None,
        )

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception

    entries = [json.loads(line) for line in hits_path.read_text().splitlines()]
    matches = [e for e in entries if e.get("event") == "wcl_flow"]
    assert len(matches) == 1
    assert matches[0]["wcl_status"] == "success"


def test_value_error_records_wcl_status_failed_value_error(monkeypatch, tmp_path):
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))

    def _script() -> None:
        from simf.ui.log_wcl import _run_wcl_analysis

        def _boom(*_a, **_k):
            raise ValueError("Actor 'Bob' was not found in this report.")

        _run_wcl_analysis(
            _boom,
            report=None,
            fight=None,
            target="Brutoh-Uldum-EU",
            token="fake-token",
            target_actor_id=None,
        )

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception

    entries = [json.loads(line) for line in hits_path.read_text().splitlines()]
    matches = [e for e in entries if e.get("event") == "wcl_flow"]
    assert len(matches) == 1
    assert matches[0]["wcl_status"] == "failed_value_error"


def test_generic_exception_records_wcl_status_failed_other(monkeypatch, tmp_path):
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))

    def _script() -> None:
        from simf.ui.log_wcl import _run_wcl_analysis

        def _boom(*_a, **_k):
            raise RuntimeError("Connection reset by peer")

        _run_wcl_analysis(
            _boom,
            report=None,
            fight=None,
            target="Brutoh-Uldum-EU",
            token="fake-token",
            target_actor_id=None,
        )

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception

    entries = [json.loads(line) for line in hits_path.read_text().splitlines()]
    matches = [e for e in entries if e.get("event") == "wcl_flow"]
    assert len(matches) == 1
    assert matches[0]["wcl_status"] == "failed_other"
