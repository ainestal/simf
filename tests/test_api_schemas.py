"""Pydantic schema tests — Phase 5.1.

FastAPI itself is an optional extra (`pip install .[api]`); the engine
must import cleanly without it. These tests cover the schema mapping
between the internal dataclasses and the wire format, plus the
graceful-degradation when fastapi is missing.
"""

from __future__ import annotations

import pytest


def test_simulate_request_round_trip():
    """`SimulateRequest` JSON encodes and decodes losslessly."""
    from simf.api.schemas import SimulateRequest

    payload = {
        "character": {
            "name": "Brutoh",
            "race": "earthen",
            "class_spec": "protection_warrior",
            "talents": "brutoh-actual",
            "strength": 2000,
            "stamina": 50000,
            "armor_from_gear": 5517,
        },
        "damage_profile": "m+_pull_caster",
        "iterations": 200,
        "seed": 42,
    }
    req = SimulateRequest.model_validate(payload)
    assert req.character.name == "Brutoh"
    assert req.iterations == 200
    # Round-trip
    redumped = req.model_dump(exclude_none=True)
    assert redumped["character"]["stamina"] == 50000


def test_simulate_request_iter_bounds_enforced():
    """The schema must reject `iterations` outside the sane range."""
    from pydantic import ValidationError

    from simf.api.schemas import CharacterRequest, SimulateRequest

    char = CharacterRequest(
        name="x", race="orc", class_spec="protection_warrior", talents="brutoh-actual"
    )
    with pytest.raises(ValidationError):
        SimulateRequest(character=char, iterations=5)  # below ge=10
    with pytest.raises(ValidationError):
        SimulateRequest(character=char, iterations=99999)  # above le=10000


def test_sim_result_response_from_internal_dataclass():
    """`SimResultResponse.from_sim_result` builds from the internal
    `SimResult` shape without leaking numpy/dataclass internals."""
    from simf.api.schemas import SimResultResponse
    from simf.core.metrics import SimResult

    sim = SimResult(
        iterations=200,
        deaths=4,
        death_rate=0.02,
        death_times=[10.0, 20.0, 30.0, 40.0],
        mean_dtps=80000.0,
        p50_dtps=75000.0,
        p99_dtps=120000.0,
        mean_5s_window=400000.0,
        p95_5s_window=600000.0,
        p99_5s_window=750000.0,
        p99_10s_window=950000.0,
        p99_15s_window=1100000.0,
        tmi_6=0.5,
        etmi_6=0.4,
        tmi_12=1.2,
        etmi_12=0.9,
        mean_sb_uptime=0.55,
        constants_version=4,
    )
    resp = SimResultResponse.from_sim_result(sim)
    assert resp.iterations == 200
    assert resp.deaths == 4
    assert resp.death_rate == pytest.approx(0.02)
    assert resp.constants_version == 4
    # JSON-encodes cleanly
    js = resp.model_dump_json()
    assert "constants_version" in js
    assert "etmi_12" in js


def test_app_import_does_not_crash_when_fastapi_missing():
    """`simf.api.app` import must not raise — fastapi is optional. The
    factory `create_app()` returns None or raises a friendly error if
    fastapi isn't installed; the module import itself succeeds."""
    import simf.api.app as api_app

    # If FastAPI isn't installed, app should be None at module level.
    # If it IS installed, app is a FastAPI instance — either way no crash.
    assert hasattr(api_app, "app")
