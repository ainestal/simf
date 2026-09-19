"""Opt-in parity test: WCL-built ReplayData vs local .txt log for the same fight.

This test is **gated** on the ``SIMF_WCL_PARITY`` environment variable
because it requires:

1. WCL credentials (``WCL_CLIENT_ID`` / ``WCL_CLIENT_SECRET`` env vars or
   ``~/.simf/wcl_config.yaml``) — see ``wcl_api._load_credentials``.
2. A **public** WCL report code that matches one of the
   ``WoWCombatLog-*.txt`` files in ``examples/`` (same character, same
   pull). Brutoh's examples may not all have been uploaded; matching
   them up needs Brutoh's WCL account or a manual cross-reference.

Search log (2026-05-27): Brutoh's 5 most-recent public WCL reports via
``characterData.character(name:"Brutoh", serverSlug:"uldum",
serverRegion:"EU")`` span 2026-05-22 → 2026-05-25 (codes
ExampleCode5555555, ExampleCode8888888, ExampleCode9999999, ExampleCodeAAAAAAA,
ExampleCodeBBBBBBB). The latest ``WoWCombatLog-*.txt`` in ``examples/``
is 2026-05-15 — no overlap exists in the current corpus, so the parity
test stays skipped until a fresher Brutoh ``.txt`` lands or an older
Brutoh WCL report is identified.

CI does not have the credentials and the matched pair is not yet
identified, so by default this test is **skipped**. To unblock:

1. Identify a Brutoh log in ``examples/`` that was also uploaded to WCL.
2. Set env vars below and run::

       SIMF_WCL_PARITY=1 \\
       SIMF_WCL_REPORT=abc123XYZ \\
       SIMF_WCL_FIGHT=4 \\
       SIMF_WCL_LOG_PATH=examples/WoWCombatLog-….txt \\
       SIMF_WCL_TARGET=Brutoh-Uldum-EU \\
       pytest tests/test_wcl_calibrate_parity.py -v

The test asserts that the two ingest paths produce ReplayData lists that
agree within ±2% on event count and ±1% on ``actual_dealt``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.mark.skipif(
    not os.environ.get("SIMF_WCL_PARITY"),
    reason="opt-in WCL parity test (set SIMF_WCL_PARITY=1 plus the data env vars)",
)
def test_wcl_vs_local_log_parity():
    report_code = os.environ.get("SIMF_WCL_REPORT")
    fight_id_raw = os.environ.get("SIMF_WCL_FIGHT")
    log_path_raw = os.environ.get("SIMF_WCL_LOG_PATH")
    target = os.environ.get("SIMF_WCL_TARGET", "Brutoh-Uldum-EU")
    if not (report_code and fight_id_raw and log_path_raw):
        pytest.skip("Parity test gated on SIMF_WCL_REPORT / SIMF_WCL_FIGHT / SIMF_WCL_LOG_PATH")

    from simf.io.log_replay import load_replay
    from simf.io.wcl_replay import wcl_to_replay_data

    fight_id = int(fight_id_raw)
    log_path = Path(log_path_raw)
    assert log_path.exists(), f"Local log not found: {log_path}"

    wcl_replay = wcl_to_replay_data(report_code, fight_id, target)
    # Local-log side: assume last run in the file is the matching one;
    # override SIMF_WCL_RUN_INDEX if needed.
    run_index = int(os.environ.get("SIMF_WCL_RUN_INDEX", "-1"))
    local_replay = load_replay(log_path, target, run_index=run_index)

    # ±2% on event count
    ec_wcl = wcl_replay.event_count
    ec_local = local_replay.event_count
    assert abs(ec_wcl - ec_local) / max(ec_local, 1) <= 0.02, (
        f"Event-count parity broken: WCL={ec_wcl}, local={ec_local}"
    )

    # ±1% on actual_dealt
    d_wcl = wcl_replay.actual_dealt
    d_local = local_replay.actual_dealt
    assert abs(d_wcl - d_local) / max(d_local, 1) <= 0.01, (
        f"actual_dealt parity broken: WCL={d_wcl}, local={d_local}"
    )
