"""Wiring tests for the key-level verdict panel's disk cache
(`core.key_verdict_cache`), driven through the real `ui/verdict.py` panel
via `AppTest` — see `test_key_verdict_cache.py` for the pure-stdlib unit
tests of the cache module itself.

Two things this file proves that the unit tests can't:

1. A demo-character compute writes the disk cache; a real-character compute
   never does — the deliberate privacy-conscious write scope described in
   `core.key_verdict_cache`'s module docstring.
2. A SECOND cold session (fresh `AppTest` instance, no session-state
   carried over) that loads the same demo signature renders the cached
   verdict WITHOUT ever calling `compute_key_level_verdict` — the actual
   "cold-load path costs zero compute" point of this feature.

The `tmp_path`-per-test disk-cache redirect comes from the autouse
`_isolate_key_verdict_cache_dir` fixture in `conftest.py`; `cache_dir` below
is just that same directory, named for readability at the assertion sites.
"""

from __future__ import annotations

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
        prog_ceiling=None,
        affix="fortified",
    )


@pytest.fixture
def cache_dir(tmp_path) -> Path:
    """Same directory the autouse `_isolate_key_verdict_cache_dir` conftest
    fixture already pointed `SIMF_KEY_VERDICT_CACHE_DIR` at for this test —
    `tmp_path` resolves to the identical path for both fixtures within one
    test function, so this is just a readable name for assertions below."""
    return tmp_path / "key_verdict_cache"


@pytest.fixture
def counting_compute(monkeypatch):
    """Mocks the slow sweep (same reasoning as
    `test_key_level_verdict_expander_stays_open.py`'s `app` fixture) AND
    counts real invocations, so a test can assert "the disk cache was
    actually consulted instead of recomputing" rather than just "the right
    number showed up on screen.\""""
    import simf.core.key_level_verdict as klv_mod

    calls = {"n": 0}

    def _compute(**kwargs):
        calls["n"] += 1
        return _fake_verdict()

    monkeypatch.setattr(klv_mod, "compute_key_level_verdict", _compute)
    return calls


def _new_app(demo_slug: str | None) -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.session_state["char_data"] = _prot_warrior_char_data()
    at.session_state["view"] = "gear"
    if demo_slug is not None:
        at.session_state["_loaded_demo_slug"] = demo_slug
    return at


def test_demo_load_populates_disk_cache(cache_dir: Path, counting_compute) -> None:
    at = _new_app(demo_slug="brutoh")
    at.run()
    compute_btn = next(b for b in at.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not at.exception, f"Unhandled exception: {at.exception}"

    assert list(cache_dir.glob("*.json")), "a demo-flagged compute must persist its verdict to disk"
    assert counting_compute["n"] == 1


def test_non_demo_load_does_not_populate_disk_cache(cache_dir: Path, counting_compute) -> None:
    # No `_loaded_demo_slug` — a real SimC/Raider.IO/Armory load never sets
    # it (see `ui/load.py`'s `_do_simc_load`/`_do_raider_io_load`, which
    # both now also explicitly pop any stale flag left by an earlier demo
    # load in the same session).
    at = _new_app(demo_slug=None)
    at.run()
    compute_btn = next(b for b in at.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not at.exception, f"Unhandled exception: {at.exception}"

    assert not list(cache_dir.glob("*.json")), (
        "a non-demo compute must NEVER write a real character's gear "
        "signature to the server-side disk cache"
    )
    assert counting_compute["n"] == 1


def test_second_cold_session_reads_the_demo_entry_without_recomputing(
    cache_dir: Path, counting_compute
) -> None:
    """The actual point of this feature: a second cold browser session
    (fresh `AppTest`, no session-state carried over) loading the SAME demo
    signature renders the cached verdict WITHOUT ever calling
    `compute_key_level_verdict` again."""
    at1 = _new_app(demo_slug="brutoh")
    at1.run()
    compute_btn = next(b for b in at1.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not at1.exception
    assert counting_compute["n"] == 1

    at2 = _new_app(demo_slug="brutoh")
    at2.run()
    assert not at2.exception, f"Unhandled exception: {at2.exception}"

    assert counting_compute["n"] == 1, "the second cold session must hit disk, not recompute"
    assert any(b.label == "Re-compute verdict" for b in at2.button), (
        "a disk-cache hit should render as already-fresh (Re-compute label), "
        "with zero clicks required"
    )
    body = "\n".join(str(m.value) for m in at2.markdown)
    assert "+15" in body


def test_second_cold_session_for_a_real_character_gets_no_disk_hit(
    cache_dir: Path, counting_compute
) -> None:
    """Mirror of the above for the non-demo path: even after a real
    character's compute has run once (uncached to disk by design), a second
    cold session for the SAME real character must recompute — there is
    nothing on disk to hit."""
    at1 = _new_app(demo_slug=None)
    at1.run()
    compute_btn = next(b for b in at1.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not at1.exception
    assert counting_compute["n"] == 1

    at2 = _new_app(demo_slug=None)
    at2.run()
    assert not at2.exception, f"Unhandled exception: {at2.exception}"

    # No disk entry exists, so the panel offers the "Compute verdict"
    # (not "Re-compute") label on this fresh session's very first render.
    assert any(b.label == "Compute verdict" for b in at2.button)
    assert not list(cache_dir.glob("*.json"))
