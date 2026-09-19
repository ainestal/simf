"""Season 2 M+ pool honesty caveat.

Historical context: `season_2_catalog:` entries in dungeons.yaml were
pre-staged inert (no school_mix/recommended_profile —
scripts/promote_season2_catalog.py refuses to promote them as-is) until real
Season 2 logs let someone run the promotion for real. Season 2's rotation
went live 2026-08-18 — a separate, later date than patch 12.1.0's own
~2026-08-11 ship. Between those two dates and the real promotion (which
happened 2026-08-30, once zone 55 had enough live play to replay), the "Prog
dungeons" picker was silently showing Season 1's pool with no indication it
was stale. This pins `_season_pool_is_stale()` (the date+file-state gate) and
the app-level caption it drives — both the synthetic not-yet-promoted case
(via a fabricated tmp_path file, so it stays testable forever) and the real
file's now-promoted state.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import simf.ui.state as state_mod

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


class _FakeDatetime:
    """Stand-in for `datetime` with `.now(tz)` frozen — mirrors
    `test_run_config_strip.py`'s `_FakeDatetime` so both date-gated
    caveats in this codebase are tested the same way."""

    def __init__(self, fixed):
        self._fixed = fixed

    def now(self, tz=None):
        return self._fixed


def _freeze_state_now(monkeypatch, iso: str) -> None:
    monkeypatch.setattr(state_mod, "datetime", _FakeDatetime(datetime.fromisoformat(iso)))


def test_season_pool_not_stale_before_ship_date(monkeypatch):
    _freeze_state_now(monkeypatch, "2026-08-17T23:59:00+00:00")
    assert state_mod._season_pool_is_stale() is False


def test_season_pool_not_stale_on_ship_day_now_that_real_file_is_promoted(monkeypatch):
    """The real dungeons.yaml was promoted 2026-08-30 (see
    scripts/promote_season2_catalog.py's real-file regression tests) — it now
    carries a `season_1_catalog:` archive key, so the staleness gate must
    report clean even evaluated as of the ship date itself."""
    _freeze_state_now(monkeypatch, "2026-08-18T00:00:01+00:00")
    text = (state_mod.DATA_DIR / "dungeons.yaml").read_text()
    # Anchored, not a bare substring check — the file's own header prose
    # already NAMES `season_1_catalog:` in a sentence, so a substring check
    # would false-positive even before the key itself existed.
    assert re.search(r"^season_1_catalog:\s*$", text, re.MULTILINE) is not None
    assert state_mod._season_pool_is_stale() is False


def test_season_pool_self_clears_once_promoted(monkeypatch, tmp_path):
    """The moment `promote_season2_catalog.py --apply` actually runs, the
    real dungeons.yaml gains a `season_1_catalog:` archive key — the caveat
    must stop firing with no second manual step."""
    _freeze_state_now(monkeypatch, "2026-09-01T00:00:00+00:00")
    fake_yaml = tmp_path / "dungeons.yaml"
    fake_yaml.write_text("dungeons:\n  - id: wr\nseason_1_catalog:\n  - id: old\n")
    monkeypatch.setattr(state_mod, "DATA_DIR", tmp_path)
    assert state_mod._season_pool_is_stale() is False


def test_season_pool_stale_far_after_ship_date_if_never_promoted(monkeypatch, tmp_path):
    _freeze_state_now(monkeypatch, "2026-09-01T00:00:00+00:00")
    fake_yaml = tmp_path / "dungeons.yaml"
    fake_yaml.write_text("dungeons:\n  - id: wr\n")
    monkeypatch.setattr(state_mod, "DATA_DIR", tmp_path)
    assert state_mod._season_pool_is_stale() is True


def _baseline_char_state() -> dict:
    return {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def test_caption_absent_before_ship_date(app, monkeypatch):
    _freeze_state_now(monkeypatch, "2026-08-17T00:00:00+00:00")
    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "still Season 1's dungeons" not in captions


def test_caption_absent_after_ship_date_now_that_real_file_is_promoted(app, monkeypatch):
    """Mirrors `test_caption_absent_before_ship_date` — now that the real
    catalog is promoted, the caveat must stay silent on/after ship date too,
    not just before it."""
    _freeze_state_now(monkeypatch, "2026-08-18T00:00:01+00:00")
    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "still Season 1's dungeons" not in captions
