"""Cold-share URL handler — pure parsing + AppTest cold-load.

The `?demo=brutoh`, `?view=log`, `?adv=1` style query params let a Discord
link land a reader on a populated simf state without re-pasting SimC. The
parser stays pure (no streamlit imports) so we can unit-test the directive
without booting an app.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from streamlit.testing.v1 import AppTest

from simf.ui.helpers.share_url import (
    ShareDirective,
    build_share_url,
    parse_share_params,
)

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


# ─── pure parser ───────────────────────────────────────────────────────────────


def test_empty_params_yields_noop_directive():
    d = parse_share_params({})
    assert d == ShareDirective()
    assert d.is_empty()


def test_demo_param_sets_load_demo():
    d = parse_share_params({"demo": "brutoh"})
    assert d.load_demo == "brutoh"
    assert d.view is None
    assert d.advanced is False
    assert not d.is_empty()


def test_view_param_gear_or_log_accepted():
    assert parse_share_params({"view": "gear"}).view == "gear"
    assert parse_share_params({"view": "log"}).view == "log"


def test_view_param_unknown_value_ignored():
    """A bogus ?view=foo should be ignored, not crash — permissive cold-load."""
    assert parse_share_params({"view": "foo"}).view is None
    assert parse_share_params({"view": ""}).view is None


def test_advanced_param_truthy_strings():
    """Streamlit query_params are strings — accept the common truthy variants."""
    for truthy in ("1", "true", "True", "yes", "on"):
        assert parse_share_params({"adv": truthy}).advanced is True
    for falsy in ("0", "false", "no", "off", ""):
        assert parse_share_params({"adv": falsy}).advanced is False


def test_read_only_param_truthy_strings():
    """`?ro=1` flips the share into read-only mode for cold-share viewers."""
    for truthy in ("1", "true", "yes", "on"):
        assert parse_share_params({"ro": truthy}).read_only is True
    for falsy in ("0", "false", "no", "off", ""):
        assert parse_share_params({"ro": falsy}).read_only is False


def test_read_only_default_false():
    """No ?ro param → mutations allowed (host's own view)."""
    assert parse_share_params({}).read_only is False
    assert parse_share_params({"demo": "brutoh"}).read_only is False


def test_read_only_not_empty():
    """A directive with only ro=1 set is NOT empty — it must survive
    `is_empty()` gating in the cold-share dispatcher."""
    d = parse_share_params({"ro": "1"})
    assert not d.is_empty()


def test_unknown_keys_are_ignored():
    """Random query params (utm_*, tracking, etc.) must not crash the parser."""
    d = parse_share_params({"demo": "brutoh", "utm_source": "discord", "x": "y"})
    assert d.load_demo == "brutoh"


def test_demo_name_safelist():
    """Demo names go to a filesystem lookup — only allow simple slugs."""
    # Path traversal attempt
    assert parse_share_params({"demo": "../etc/passwd"}).load_demo is None
    # Absolute path attempt
    assert parse_share_params({"demo": "/etc/passwd"}).load_demo is None
    # Whitespace
    assert parse_share_params({"demo": " brutoh "}).load_demo == "brutoh"
    # Empty
    assert parse_share_params({"demo": ""}).load_demo is None


def test_directive_list_values_unwrapped():
    """st.query_params may produce list[str] for repeated keys — take the first."""
    d = parse_share_params({"demo": ["brutoh", "other"], "view": ["log"]})
    assert d.load_demo == "brutoh"
    assert d.view == "log"


# ─── build_share_url (inverse of the parser) ────────────────────────────────────


def test_build_share_url_round_trips_with_parser():
    """build_share_url → parse_share_params must recover the same directive —
    the generate/consume pair is the whole share story."""
    url = build_share_url(demo="brutoh", view="gear")
    d = parse_share_params(parse_qs(urlsplit(url).query))
    assert d.load_demo == "brutoh"
    assert d.view == "gear"


def test_build_share_url_emits_only_set_params_in_stable_order():
    assert build_share_url() == ""
    assert build_share_url(base="https://simf.app") == "https://simf.app"
    assert build_share_url(demo="brutoh", view="gear") == "?demo=brutoh&view=gear"


def test_build_share_url_flags_round_trip():
    url = build_share_url(advanced=True, read_only=True)
    d = parse_share_params(parse_qs(urlsplit(url).query))
    assert d.advanced is True
    assert d.read_only is True


# ─── AppTest integration ───────────────────────────────────────────────────────


@pytest.fixture
def app() -> AppTest:
    # The Gear-tab recommender (_per_slot_picks) touches ~30 items on a cold
    # share-load of the Brutoh demo; 10s was too tight once the composite
    # scorer landed. First-paint of a fresh session is the only path that
    # exercises this budget — production session-state caches keep reruns fast.
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def test_cold_load_demo_param_populates_character(app):
    """`?demo=brutoh` should cold-load the bundled Brutoh character."""
    app.query_params["demo"] = "brutoh"
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    assert "char_data" in app.session_state
    cd = app.session_state["char_data"]
    assert cd.get("name", "").lower() == "brutoh"


def test_cold_load_view_log_routes_to_log_surface(app):
    """`?view=log` should land directly on the Why-did-I-die surface."""
    app.query_params["view"] = "log"
    app.session_state["char_data"] = {
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
    app.run()
    assert not app.exception
    assert app.session_state["view"] == "log"


def test_cold_load_adv_param_opens_advanced(app):
    """`?adv=1` should flip the Advanced toggle on at first paint."""
    app.query_params["adv"] = "1"
    app.run()
    assert not app.exception
    assert app.session_state["advanced_mode"] is True


def test_cold_load_with_no_params_is_a_noop(app):
    """No params → app behaves identically to a fresh boot."""
    app.run()
    assert not app.exception
    # No character, no view override, no advanced toggle flipped.
    assert "char_data" not in app.session_state or not app.session_state["char_data"]
    # SafeSessionState (AppTest) has no `.get()` — fall back to membership check.
    if "advanced_mode" in app.session_state:  # noqa: SIM108
        advanced = app.session_state["advanced_mode"]
    else:
        advanced = False
    assert advanced is False


def test_cold_load_invalid_demo_does_not_crash(app):
    """A bad ?demo=xyz must produce an empty-state app, not an exception."""
    app.query_params["demo"] = "nonexistent_xyz"
    app.run()
    assert not app.exception
    assert "char_data" not in app.session_state or not app.session_state["char_data"]


def _warrior_with_vault(app) -> None:
    """Warrior char + a non-empty vault, so the Vault/Gear tab order is decided
    by `has_vault` (which would otherwise land a visitor on the Vault tab)."""
    from simf.io.simc_import import ItemSpec

    app.session_state["char_data"] = {
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
    app.session_state["simc_vault_items"] = {
        "trinket1": [ItemSpec(slot="trinket1", item_id=200, name="Vault Trinket", ilvl=272)],
    }


def test_vault_first_without_diff_share(app):
    """Regression guard: Vault stays the default sub-tab when a vault exists
    (the 2026-05-15 real-flow default)."""
    _warrior_with_vault(app)
    app.session_state["view"] = "gear"
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    assert app.session_state["_gear_active_subtab"] == "vault", (
        "Non-share load with a vault should default to the Vault sub-tab, got "
        f"{app.session_state.get('_gear_active_subtab')!r}"
    )
