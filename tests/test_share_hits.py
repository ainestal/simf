"""Public usage-analytics event log (Phase 5, 2026-06-13; broadened 2026-08-16).

Started as a one-arrow test (does Brutoh's shared link convert?) with no other
signal — Streamlit tracks nothing on its own. Broadened 2026-08-16 into simf's
general usage-analytics log (see the module docstring in `core/share_hits.py`
for why). These pin that a hit records exactly the allowlisted fields plus a
timestamp, nothing outside that allowlist, that it counts, and that it's
best-effort.
"""

from __future__ import annotations

import json
from pathlib import Path

from simf.core import share_hits

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_record_then_count_round_trip(tmp_path):
    p = tmp_path / "hits.jsonl"
    assert share_hits.count(override_path=p) == 0
    share_hits.record({"demo": "brutoh", "view": "gear"}, override_path=p)
    share_hits.record({"demo": "brutoh"}, override_path=p)
    assert share_hits.count(override_path=p) == 2


def test_record_writes_timestamp_and_only_allowlisted_truthy_fields(tmp_path):
    p = tmp_path / "hits.jsonl"
    share_hits.record(
        {"demo": "brutoh", "view": ""},
        override_path=p,
    )
    entry = json.loads(p.read_text().splitlines()[0])
    assert "t" in entry and entry["t"].endswith("+00:00")  # UTC ISO
    assert entry["demo"] == "brutoh"
    # Falsy fields dropped.
    assert "view" not in entry


def test_record_never_writes_pii(tmp_path):
    """Even if a caller passes IP/UA/session, only the allowlist is written."""
    p = tmp_path / "hits.jsonl"
    share_hits.record(
        {"demo": "brutoh", "ip": "1.2.3.4", "user_agent": "Mozilla", "session_id": "abc"},
        override_path=p,
    )
    entry = json.loads(p.read_text().splitlines()[0])
    # This call never passes "event", so its absence from the written entry
    # doesn't change even though "event" joined the allowlist below.
    assert set(entry) <= {"t", "demo", "view"}
    assert "1.2.3.4" not in p.read_text()


def test_event_field_is_allowlisted(tmp_path):
    """`"event"` joined the allowlist (2026-07-31 view-reached signal) — a
    "view_reached" event with its `view` land in the written entry, same
    style as the existing allowlisted-fields pin above."""
    p = tmp_path / "hits.jsonl"
    share_hits.record({"event": "view_reached", "view": "gear"}, override_path=p)
    entry = json.loads(p.read_text().splitlines()[0])
    assert entry["event"] == "view_reached"
    assert entry["view"] == "gear"


def test_broadened_usage_analytics_fields_are_allowlisted(tmp_path):
    """2026-08-16 broadening: `sid`/`load_method`/`spec`/`comfortable_max`/
    `prog_ceiling`/`wcl_status` all join the allowlist — pinned individually
    so a future accidental narrowing shows up as a specific failing field."""
    p = tmp_path / "hits.jsonl"
    share_hits.record(
        {
            "event": "verdict_computed",
            "sid": "abcd1234",
            "load_method": "demo",
            "spec": "protection_warrior",
            "comfortable_max": "15",
            "prog_ceiling": "19",
            "wcl_status": "success",
        },
        override_path=p,
    )
    entry = json.loads(p.read_text().splitlines()[0])
    assert entry["sid"] == "abcd1234"
    assert entry["load_method"] == "demo"
    assert entry["spec"] == "protection_warrior"
    assert entry["comfortable_max"] == "15"
    assert entry["prog_ceiling"] == "19"
    assert entry["wcl_status"] == "success"


def test_record_is_best_effort_on_unwritable_path(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("not a dir")
    # path under a regular file → mkdir/open raise internally; must be swallowed.
    share_hits.record({"demo": "brutoh"}, override_path=blocker / "sub" / "hits.jsonl")
    assert share_hits.count(override_path=blocker / "sub" / "hits.jsonl") == 0


def test_count_zero_for_missing_file(tmp_path):
    assert share_hits.count(override_path=tmp_path / "nope.jsonl") == 0


def test_env_var_overrides_default_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(tmp_path / "env.jsonl"))
    assert share_hits.hits_path() == tmp_path / "env.jsonl"
    share_hits.record({"demo": "brutoh"})
    assert share_hits.count() == 1


def test_apply_share_url_records_hits_only_in_public_mode():
    """Tripwire: the funnel signal must be recorded, and gated to public mode
    (so the owner's own loads don't pollute it). Source-level guard — exercising
    the wiring end-to-end would need a full AppTest cold-load with a tmp path."""
    # `_apply_share_url` moved out of app.py into `simf.ui.load` (panel split,
    # PR 2); read the wiring from its new home.
    src = (REPO_ROOT / "src" / "simf" / "ui" / "load.py").read_text()
    body = src.split("def _apply_share_url(")[1].split("\ndef ")[0]
    assert "share_hits.record(" in body
    # The record call sits under the public-mode gate.
    pre = body.split("share_hits.record(")[0]
    assert "_is_public_mode()" in pre


def test_view_reached_wiring_gated_on_public_mode_in_app():
    """Tripwire (2026-07-31 visitor-signal follow-through): app.py's per-view
    "view_reached" wiring must (a) sit under the public-mode gate in `main()`,
    same convention as `_apply_share_url` above, and (b) actually call
    `share_hits.record(...)` with the `event`/`view_reached` fields somewhere
    reachable from that call site. Source-level guard — same rationale as
    `test_apply_share_url_records_hits_only_in_public_mode`: exercising this
    end-to-end would need a full AppTest cold-load with a tmp share-hits path."""
    src = (REPO_ROOT / "src" / "simf" / "ui" / "app.py").read_text()

    # (a) the call site in main() sits under `_is_public_mode()`.
    main_body = src.split("\ndef main(")[1]
    call_site = main_body.split('view = _ss().get("view", "gear")')[1]
    # Only look at the few lines right after the view is resolved, not the
    # rest of main() (which legitimately calls _is_public_mode() elsewhere).
    call_site_head = "\n".join(call_site.splitlines()[:4])
    assert "_is_public_mode()" in call_site_head, call_site_head
    assert "_record_view_reached(view)" in call_site_head, call_site_head

    # (b) the helper itself fires share_hits.record(...) with the fixed
    # event literal, never anything derived from raw user/URL input.
    helper_body = src.split("def _record_view_reached(")[1].split("\ndef ")[0]
    assert "share_hits.record(" in helper_body
    assert '"event": "view_reached"' in helper_body
    # Lazily imported, matching load.py's existing convention for this module.
    assert "from simf.core import share_hits" in helper_body


def test_record_stops_at_size_cap(tmp_path, monkeypatch):
    """Bounded growth: a bot looping the public share URL can't grow the funnel
    log without limit (SD-card hygiene). Past the byte cap, record() no-ops."""
    p = tmp_path / "hits.jsonl"
    monkeypatch.setattr(share_hits, "_MAX_BYTES", 200)  # tiny cap for the test
    for _ in range(100):
        share_hits.record({"demo": "brutoh"}, override_path=p)
    # Growth stopped near the cap (one line may straddle it), not 100 records.
    assert p.stat().st_size < 200 + 200
    assert 0 < share_hits.count(override_path=p) < 100
