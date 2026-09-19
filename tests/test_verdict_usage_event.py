"""The "Key-level verdict" panel must record a `verdict_computed` usage-
analytics event (spec + comfortable_max + prog_ceiling) whenever a fresh
sweep actually runs — 2026-08-16 usage-analytics broadening.

Mirrors `tests/test_key_level_verdict_expander_stays_open.py`'s fixture
(mocks `compute_key_level_verdict` so the test doesn't pay for a real
Monte Carlo sweep).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.core.key_level_verdict import KeyLevelPoint, KeyLevelVerdict

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


def _prot_warrior_char_data() -> dict:
    return {
        "name": "Brutoh",
        "race": "earthen",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2182,
        "stamina": 34176,
        "armor_from_gear": 5015,
        "haste_rating": 2318,
        "crit_rating": 1391,
        "mastery_rating": 1608,
        "versatility_rating": 296,
        "max_hp_override": 751872,
    }


def _fake_verdict() -> KeyLevelVerdict:
    return KeyLevelVerdict(
        points=[
            KeyLevelPoint(
                key_level=15,
                damage_multiplier=1.0,
                death_rate=0.02,
                mean_dtps=40_000.0,
                p99_5s_window=1.0,
                band="comfortable",
                mean_hrps=1_000.0,
                normalized_tank_score=0.85,
            )
        ],
        comfortable_max=15,
        prog_ceiling=19,
        affix="fortified",
    )


@pytest.fixture
def app(monkeypatch, tmp_path) -> AppTest:
    import simf.core.key_level_verdict as klv_mod

    monkeypatch.setattr(klv_mod, "compute_key_level_verdict", lambda **kwargs: _fake_verdict())
    # Deliberately NOT public mode: the Compute-verdict button is disabled
    # entirely in public/read-only mode (CPU-abuse guard on the shared Pi),
    # so this event only ever fires for an interactive, non-read-only
    # session today — same as every owner/local use of this panel.
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(tmp_path / "hits.jsonl"))

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.session_state["char_data"] = _prot_warrior_char_data()
    at.session_state["view"] = "gear"
    return at


def test_compute_records_verdict_computed_event(app: AppTest, tmp_path) -> None:
    app.run()
    compute_btn = next(b for b in app.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    hits_path = tmp_path / "hits.jsonl"
    entries = [json.loads(line) for line in hits_path.read_text().splitlines()]
    matches = [e for e in entries if e.get("event") == "verdict_computed"]
    assert len(matches) == 1
    assert matches[0]["spec"] == "protection_warrior"
    assert matches[0]["comfortable_max"] == "15"
    assert matches[0]["prog_ceiling"] == "19"
    assert "sid" in matches[0]
