"""Batch D — log-health preflight tests.

Anchored to real fixture logs already in ``examples/`` (same convention as
``test_run_segmentation.py``'s ``MGT_LOG``) — one ACL-on, one ACL-off,
confirmed via a direct header-line read before writing these tests. Only the
first ~200 lines are ever scanned regardless of file size, so pointing
directly at the (100+ MB) real corpus files is cheap.

``examples/WoWCombatLog-*.txt`` is gitignored (real combat logs are the
user's personal data — see CONTRIBUTING.md's browser-side-log-parsing decision),
so these files exist on this Pi but never reach a fresh CI checkout.
``skipif`` (not a hard failure) on the two tests below mirrors
``test_run_segmentation.py``'s own guard — this module's docstring claimed
that precedent from the start but never actually wired it, which is exactly
what turned "file missing" into a CI failure instead of a skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simf.io.log_preflight import (
    TARGET_COMBAT_LOG_VERSION,
    LogHealthReport,
    log_health_preflight,
)

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
ACL_ON_LOG = _EXAMPLES / "WoWCombatLog-050626_153703.txt"
ACL_OFF_LOG = _EXAMPLES / "WoWCombatLog-051726_134819.txt"

_has_real_logs = pytest.mark.skipif(
    not (ACL_ON_LOG.exists() and ACL_OFF_LOG.exists()),
    reason="real example combat logs not present (gitignored, local-only)",
)


@_has_real_logs
def test_acl_on_log_detected():
    report = log_health_preflight(ACL_ON_LOG)
    assert report.acl_enabled is True
    assert report.found_log_version == 22
    assert report.version_matches is True
    assert report.is_empty is False
    assert report.has_recognizable_line is True


@_has_real_logs
def test_acl_off_log_detected():
    report = log_health_preflight(ACL_OFF_LOG)
    assert report.acl_enabled is False
    assert report.found_log_version == 22
    assert report.version_matches is True
    assert report.has_recognizable_line is True


def test_version_mismatch_flagged_not_hard_failed(tmp_path):
    fake_log = tmp_path / "WoWCombatLog-fake.txt"
    fake_log.write_text(
        "5/6/2026 15:37:03.5941  COMBAT_LOG_VERSION,21,ADVANCED_LOG_ENABLED,1,"
        "BUILD_VERSION,11.0.0,PROJECT_ID,1\n"
        '5/6/2026 15:37:03.5961  ZONE_CHANGE,0,"Silvermoon City",0\n'
    )
    report = log_health_preflight(fake_log)
    assert report.found_log_version == 21
    assert report.found_log_version != TARGET_COMBAT_LOG_VERSION
    assert report.version_matches is False
    # A version mismatch is a flag, not a hard failure — the file is still
    # a real, non-empty, recognizable combat log.
    assert report.is_empty is False
    assert report.has_recognizable_line is True


def test_empty_file_reports_is_empty(tmp_path):
    empty_log = tmp_path / "WoWCombatLog-empty.txt"
    empty_log.write_text("")
    report = log_health_preflight(empty_log)
    assert report.is_empty is True
    assert report.has_recognizable_line is False
    assert report.acl_enabled is None
    assert report.found_log_version is None
    assert report.version_matches is None


def test_missing_file_reports_is_empty(tmp_path):
    report = log_health_preflight(tmp_path / "does-not-exist.txt")
    assert report.is_empty is True
    assert report.has_recognizable_line is False


def test_garbage_content_has_no_recognizable_line(tmp_path):
    """A wrong-file upload (e.g. a /simc paste saved as .txt) has no
    combat-log-shaped lines at all — must be flagged, not silently passed
    through to the heavy analysis downstream."""
    garbage = tmp_path / "not-a-log.txt"
    garbage.write_text("this is not a combat log\njust some random text\n")
    report = log_health_preflight(garbage)
    assert report.is_empty is False
    assert report.has_recognizable_line is False
    assert report.acl_enabled is None
    assert report.found_log_version is None


def test_header_missing_but_lines_recognizable(tmp_path):
    """A truncated log that starts mid-stream (no header line survived, but
    real combat-log lines are present) — ACL/version unknown, but the file
    is not flagged as garbage."""
    truncated = tmp_path / "WoWCombatLog-truncated.txt"
    truncated.write_text(
        "5/6/2026 15:37:05.1234  SWING_DAMAGE,Creature-0-0-0-0-123-00001,"
        '"Mob",0x10a48,Player-1-00000001,"Brutoh-Uldum-EU",0x511,0x0,'
        "1000,1000,1,-1,1,0,0,0,nil,nil,nil\n"
    )
    report = log_health_preflight(truncated)
    assert report.has_recognizable_line is True
    assert report.acl_enabled is None
    assert report.found_log_version is None
    assert report.version_matches is None
    assert report.is_empty is False


@pytest.mark.parametrize("log_path", [ACL_ON_LOG, ACL_OFF_LOG])
def test_report_is_frozen_dataclass(log_path):
    assert isinstance(log_health_preflight(log_path), LogHealthReport)


# ─── Banner rendering (log_surface._render_log_health_banner) ─────────────
#
# Thin UI glue over an already-tested report — a small stub `st` capture is
# enough here (mirrors the `_StubStreamlit` pattern in
# `test_per_pull_surfacing_render.py`); full-page coverage stays with the
# AppTest smoke suite.


class _StubStreamlit:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.captions: list[str] = []

    def error(self, msg, *args, **kwargs):
        self.errors.append(msg)

    def caption(self, msg, *args, **kwargs):
        self.captions.append(msg)


def _render_banner(report: LogHealthReport):
    from simf.ui import log_surface

    stub = _StubStreamlit()
    real_st = log_surface.st
    log_surface.st = stub
    try:
        result = log_surface._render_log_health_banner(report)
    finally:
        log_surface.st = real_st
    return result, stub


def test_banner_hard_fails_on_empty_file():
    report = LogHealthReport(
        acl_enabled=None, found_log_version=None, is_empty=True, has_recognizable_line=False
    )
    ok, stub = _render_banner(report)
    assert ok is False
    assert stub.errors
    assert not stub.captions


def test_banner_hard_fails_on_unrecognizable_content():
    report = LogHealthReport(
        acl_enabled=None, found_log_version=None, is_empty=False, has_recognizable_line=False
    )
    ok, stub = _render_banner(report)
    assert ok is False
    assert stub.errors


def test_banner_flags_acl_off_without_blocking():
    report = LogHealthReport(
        acl_enabled=False, found_log_version=22, is_empty=False, has_recognizable_line=True
    )
    ok, stub = _render_banner(report)
    assert ok is True
    assert not stub.errors
    assert any("Advanced Combat Logging" in c for c in stub.captions)


def test_banner_flags_version_mismatch_without_blocking():
    report = LogHealthReport(
        acl_enabled=True, found_log_version=21, is_empty=False, has_recognizable_line=True
    )
    ok, stub = _render_banner(report)
    assert ok is True
    assert not stub.errors
    assert any("21" in c for c in stub.captions)


def test_banner_silent_on_clean_report():
    """Bias toward silence on the common case — ACL on, version matches,
    a real file. No caption or error, per this project's caption-earns-its-
    place discipline."""
    report = LogHealthReport(
        acl_enabled=True, found_log_version=22, is_empty=False, has_recognizable_line=True
    )
    ok, stub = _render_banner(report)
    assert ok is True
    assert not stub.errors
    assert not stub.captions
