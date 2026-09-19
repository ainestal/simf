"""Persistent Gear-tab caveat when the loaded character's stats came from
the `/simc`-paste item-lookup resolver, not an exact export/log.

Found 2026-07-10: a user flagged a ring swap's composition line ("+49,479
Versatility" for an item carrying 153 versatility rating) that read as
absurd. The raw stat delta was confirmed correct (+153); the sim-derived
eHP-per-point marginal for the same resolver-estimated character swung
>5x — even flipped the swap's overall sign — between two independent fresh
resolutions, because the resolver's per-item Wowhead lookup can silently
under-resolve one or more equipped pieces (a proc trinket returning only
its primary stat, for instance), shifting the character's aggregate
stats and therefore every sim-derived marginal computed from them.

`SimcLoadOk.stats_estimated` already existed and was tested at construction
(test_simc_load_helper.py), but the ONLY place it reached the user was a
one-time `st.toast` (`_surface_load_summary_toast`) — long gone by the time
a reader is looking at a swap card. `ui/state.py::_gear_stats_estimated()`
persists it in session state (wired by ui/load.py's three load paths, see
test_load.py) for THIS caption, rendered every time the Gear tab renders.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from simf.core.character import Character
from simf.core.constants import DATA_DIR

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"

_CAVEAT_SNIPPET = "Your stats were estimated from item lookup"


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _brutoh_char_data() -> dict:
    with open(DATA_DIR / "characters" / "brutoh.yaml") as f:
        d = yaml.safe_load(f)
    return {k: v for k, v in d.items() if k in Character.__dataclass_fields__}


def test_caption_renders_on_gear_tab_when_stats_estimated(app):
    app.session_state["char_data"] = _brutoh_char_data()
    app.session_state["_gear_stats_estimated"] = True
    app.session_state["view"] = "gear"
    app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    captions = [getattr(c, "value", "") or "" for c in app.caption]
    assert any(_CAVEAT_SNIPPET in c for c in captions), (
        "expected the stats-estimated caveat caption on the Gear tab"
    )


def test_caption_absent_when_stats_not_estimated(app):
    app.session_state["char_data"] = _brutoh_char_data()
    app.session_state["_gear_stats_estimated"] = False
    app.session_state["view"] = "gear"
    app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    captions = [getattr(c, "value", "") or "" for c in app.caption]
    assert not any(_CAVEAT_SNIPPET in c for c in captions)


def test_caption_absent_when_flag_never_set(app):
    """Default-false: a character loaded without the flag ever being touched
    (e.g. an older session-state shape) must never show a false caveat."""
    app.session_state["char_data"] = _brutoh_char_data()
    app.session_state["view"] = "gear"
    app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    captions = [getattr(c, "value", "") or "" for c in app.caption]
    assert not any(_CAVEAT_SNIPPET in c for c in captions)
