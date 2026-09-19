"""FastAPI surface — Phase 5.1 scaffold.

Endpoints:
    POST /simulate                 — wrap `runner.run_simulation`
    POST /verdict/key-level        — wrap `compute_key_level_verdict`
    GET  /profiles/damage          — list available damage profiles
    GET  /profiles/healing         — list available healing profiles
    GET  /healthz                  — liveness probe

The Streamlit UI continues to call `run_simulation` in-process. Cutting
the UI over to `httpx.post('/simulate')` is the follow-up commit; the
scaffold here proves the wire format and lets a Docker/Fly.io deploy
expose the engine over HTTP for third-party tools.

Run locally:
    uvicorn simf.api.app:app --host 0.0.0.0 --port 8000

Run inside Docker:
    docker run -p 8000:8000 simf:dev uvicorn simf.api.app:app \\
        --host 0.0.0.0 --port 8000

`fastapi` and `uvicorn` are NOT in the default install — they live in
the `[api]` extra in pyproject.toml. Streamlit users don't need them.
"""

from __future__ import annotations

from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
except ModuleNotFoundError:  # pragma: no cover - optional extra
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]

from simf.core.character import Character
from simf.core.constants import DATA_DIR
from simf.core.key_level_verdict import compute_key_level_verdict
from simf.core.profiles import load_damage_profile, load_healing_profile
from simf.core.runner import run_simulation

from .schemas import (
    KeyLevelPointResponse,
    KeyLevelVerdictRequest,
    KeyLevelVerdictResponse,
    SimResultResponse,
    SimulateRequest,
)


def _list_profile_slugs(kind: str) -> list[str]:
    base = Path(DATA_DIR) / "profiles" / kind
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))


def create_app() -> FastAPI:  # pragma: no cover - skipped when fastapi missing
    """Build the FastAPI app. Factory pattern so tests can spin up an
    instance with overrides, and module import doesn't blow up when
    `fastapi` isn't installed (default dev install)."""
    if FastAPI is None:
        raise RuntimeError(
            "FastAPI not installed. Run `pip install .[api]` to enable the HTTP surface."
        )

    app = FastAPI(
        title="simf API",
        description="WoW M+ tank survivability simulator HTTP surface.",
        version="0.1.0",
    )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/profiles/damage")
    def list_damage_profiles() -> list[str]:
        return _list_profile_slugs("damage")

    @app.get("/profiles/healing")
    def list_healing_profiles() -> list[str]:
        return _list_profile_slugs("healing")

    @app.post("/simulate", response_model=SimResultResponse)
    def simulate(req: SimulateRequest) -> SimResultResponse:
        # damage_profile/healing_profile are caller-supplied strings — resolve
        # only against the packaged profile slugs, never as a filesystem path.
        # (load_damage_profile/load_healing_profile fall back to treating an
        # existing path as-is, which is fine for trusted local CLI use but
        # would let an HTTP caller read arbitrary files off the server.)
        if req.damage_profile not in _list_profile_slugs("damage"):
            raise HTTPException(
                status_code=400, detail=f"Unknown damage_profile: {req.damage_profile!r}"
            )
        if req.healing_profile not in _list_profile_slugs("healing"):
            raise HTTPException(
                status_code=400, detail=f"Unknown healing_profile: {req.healing_profile!r}"
            )
        try:
            char = Character.from_dict(req.character.to_dict())
            damage = load_damage_profile(req.damage_profile)
            healing = load_healing_profile(req.healing_profile)
        except (FileNotFoundError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        result = run_simulation(
            character=char,
            damage_profile=damage,
            healing_profile=healing,
            iterations=req.iterations,
            seed=req.seed,
        )
        return SimResultResponse.from_sim_result(result)

    @app.post("/verdict/key-level", response_model=KeyLevelVerdictResponse)
    def key_level_verdict(req: KeyLevelVerdictRequest) -> KeyLevelVerdictResponse:
        try:
            char = Character.from_dict(req.character.to_dict())
            # m+_boss_tankbuster is the conservative default (matches the UI).
            damage = load_damage_profile("m+_boss_tankbuster")
            healing = load_healing_profile("m+_high_key_healer")
        except (FileNotFoundError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        verdict = compute_key_level_verdict(
            character=char,
            damage_profile=damage,
            healing_profile=healing,
            iterations=req.iterations,
            seed=req.seed,
            affix=req.affix,
        )
        return KeyLevelVerdictResponse(
            headline=verdict.headline(),
            detail=verdict.detail(),
            points=[
                KeyLevelPointResponse(
                    key_level=p.key_level,
                    damage_multiplier=p.damage_multiplier,
                    death_rate=p.death_rate,
                    mean_dtps=p.mean_dtps,
                    p99_5s_window=p.p99_5s_window,
                    band=p.band,
                )
                for p in verdict.points
            ],
            comfortable_max=verdict.comfortable_max,
            prog_ceiling=verdict.prog_ceiling,
            affix=verdict.affix,
        )

    return app


# Pre-build at import when fastapi is installed; raises a friendly error
# from `create_app` otherwise so the rest of simf imports cleanly.
app = create_app() if FastAPI is not None else None
