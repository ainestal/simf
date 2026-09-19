"""Regression: log_data's cached helpers must NOT re-parse the
CHALLENGE_MODE list directly. They share `_cached_runs(log_name)` so
that cold-cache cost is one file scan, not four.

A direct `parse_challenge_modes(...)` call inside the four downstream
helpers re-scans the ~80MB log on every cold helper call. The single
allowed call lives inside `_cached_runs` itself.

(The cached-data layer moved from log_view.py to log_data.py in the
log_view split; this test follows it.)
"""

from __future__ import annotations

import inspect

from simf.ui import log_data


def test_only_one_direct_parse_challenge_modes_call_in_cached_helpers():
    """Exactly ONE `parse_challenge_modes(` call lives in log_data.py —
    inside `_cached_runs`. The import uses `parse_challenge_modes,` (no
    parenthesis) and doesn't count. Every other cached helper must go
    through `_cached_runs(log_name)` so cold-cache cost stays at one
    file scan rather than four.
    """
    src = inspect.getsource(log_data)
    count = src.count("parse_challenge_modes(")
    assert count == 1, (
        f"Found {count} `parse_challenge_modes(` calls in log_data.py — "
        "expected exactly 1 (the call inside _cached_runs). "
        "A cached helper has likely reintroduced a direct log scan; "
        "route it through _cached_runs(log_name) instead."
    )
