# Phase 5 (Scale) audit — 2026-05-16

Auditor: validator Mode A. Commit 88ea2b0. Methodology: read source, install `[api]` extra in `.venv`, instantiate `create_app()`, hit each endpoint via `fastapi.testclient.TestClient`, build a wheel and replay the Dockerfile copy-order.

## Per-area verdict

### Schemas — PASS (with one omission)

- `CharacterRequest` → `Character.from_dict` round-trip works empirically. `to_dict(exclude_none=True)` drops `max_hp_override` (None) and keeps zero-valued ints; `Character.from_dict` (`character.py:29-31`) filters by `__dataclass_fields__`, so it tolerates extras and accepts the minimal shape.
- `SimResultResponse.from_sim_result` (`schemas.py:99-126`) captures every scalar field on `SimResult` (`metrics.py:47-86`). Verified via `TestClient.post('/simulate')` — JSON has `iterations`, `etmi_12`, `constants_version=4`.
- **Omission**: `mean_sb_rage_starved_pct` and `mean_sb_charge_limited_pct` (`metrics.py:68-69`) — the Phase 3 SB-gap diagnosis fields — are NOT on `SimResultResponse`. UI consumers wanting parity over HTTP can't get the rage-starved/charge-limited split. Add them; they're cheap floats.
- Intentionally skipped: `sample_damage_timeline`, `sample_heal_timeline`, `sample_max_hp`, `sample_duration_s` — documented in the docstring; deferred to a future `/simulate/timeline` endpoint. Reasonable.

### FastAPI factory — PASS

- `pip install fastapi uvicorn[standard] httpx` succeeded against the existing `.venv`. `create_app()` returns a `FastAPI` with 5 simf routes + 4 OpenAPI/docs routes. `app = create_app() if FastAPI is not None else None` (`app.py:145`) means the module imports cleanly even without the `[api]` extra — confirmed by `test_app_import_does_not_crash_when_fastapi_missing`.
- All 4 schema tests pass (6.61s).

### Endpoint return-types — PASS

End-to-end via `TestClient`:
- `GET /healthz` → 200 `{"status":"ok"}`
- `GET /profiles/damage` → 200 list of 6 slugs
- `GET /profiles/healing` → 200 list of 1 slug
- `POST /simulate` (50 iter, real Brutoh shape) → 200; `iterations=50, death_rate=0.0, etmi_12=80255.5, constants_version=4`
- `POST /verdict/key-level` (20 iter) → 200; headline + 11 points

All JSON-serializable; no numpy or dataclass leakage. The verdict endpoint correctly invokes `headline()` / `detail()` as methods (`app.py:122-123`).

### Docker build — **FAILS at runtime**

The Dockerfile is logically broken. Lines 25-32:

```
COPY pyproject.toml ./
COPY src/simf/__init__.py ./src/simf/__init__.py
RUN pip install --upgrade pip && pip install .[ui]
```

Then line 35 `COPY src/ ./src/` puts the full source on the build image. The runtime stage copies `site-packages` and `/app/src` over.

**The problem**: `pip install .[ui]` at line 32 builds a wheel from a tree containing ONLY `src/simf/__init__.py`. Hatch happily packages that — the resulting wheel installs `site-packages/simf/__init__.py` and nothing else. I reproduced this by building the wheel with only `__init__.py` present: `site-packages/simf/` contains exactly one file.

At runtime, `streamlit run src/simf/ui/app.py` runs from `/app`. `/app/src` is NOT on `sys.path` automatically — Streamlit only adds the script's own directory. The empty installed `simf` package shadows the full source. Empirical:

```
>>> import simf; simf.__file__
'/site-packages/simf/__init__.py'
>>> from simf.core.runner import run_simulation
ModuleNotFoundError: No module named 'simf.core'
```

**Fix**: copy `src/` before `pip install`, OR use `pip install -e .[ui]` against the full tree. The current layer-cache micro-optimization is buying nothing because the install layer also invalidates when `pyproject.toml` or `__init__.py` change.

Additionally:
- The Dockerfile builds for the **Streamlit UI** but the docstring/Phase-5.3 framing implies a generic image; `[api]` is not installed and uvicorn isn't on the runtime PATH. The docker-compose comment promises a `simf-api` service "in Phase 5.1" but it's not there. One-or-other is fine, but right now the deploy story is UI-only.

### CI — PASS

- `ruff check src tests` → "All checks passed!"
- `ruff format --check src tests` → "93 files already formatted"
- `mypy src` → 7 errors in 4 files. `continue-on-error: true` (`ci.yml:40`) absorbs this — by design.
- `pip install .[dev,ui]` resolves cleanly. The full test suite is 354 passing, 4 skipped, ~60s; CI gate is sound.
- `docker-build` job has `needs: check` and runs only on `main`/`master` push — it will currently produce a broken image but `push: false` means nothing is shipped. Latent landmine.

### Version-management — PASS

- `constants.yaml:13 constants_version: 4` ✓
- `runner.py:360` writes it into every `SimResult` ✓
- `SimResultResponse` carries it through to the wire ✓
- Default of 0 in `SimResult` (`metrics.py:80`) and `getattr(..., 0)` in `from_sim_result` (`schemas.py:125`) means legacy cached results land as `constants_version=0` — clean expiry semantics for downstream cache layers.

## Top 3 things to fix before production-ready

1. **Fix Dockerfile copy order** (`Dockerfile:25-35`). Either drop the partial-copy optimization (`COPY src/ ./src/` before `pip install`), or use `pip install -e .[ui]`. As shipped, the image starts but every page that touches the engine will 500. Add a smoke test to CI: `docker run --rm simf:ci python -c "from simf.core.runner import run_simulation"`.
2. **Add an `api` service to docker-compose** and a Dockerfile target (or a separate `Dockerfile.api`) that installs `.[api]` and runs `uvicorn simf.api.app:app`. Otherwise Phase 5.1 ships a wire format with no way to actually serve it from Docker.
3. **Add SB-gap fields to `SimResultResponse`** (`mean_sb_rage_starved_pct`, `mean_sb_charge_limited_pct`). They're already on `SimResult`; HTTP consumers shouldn't have to fall back to a second endpoint to get them.

## What's broken vs imperfect

- **Broken**: Dockerfile (above). The scaffold cannot actually serve real requests *from the container*. Direct `uvicorn`-on-host works fine.
- **Imperfect**: missing SB-gap fields; no `/simulate/timeline`; mypy at 7 errors (lenient by design).
- **Trust verdict**: schemas + endpoints are sound for relative comparisons over HTTP — same engine, same constants, no math drift introduced. The wire format is honest. The deploy story is not yet honest.
