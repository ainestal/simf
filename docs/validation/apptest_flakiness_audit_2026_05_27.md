# AppTest flakiness audit — 2026-05-27

**Status:** documentation only — no code fix this iteration.
**Author:** autonomous director loop (iteration 18).
**Scope:** trail of evidence + hypothesis + suggested fixes, so the next iteration can pick this up with full context. No `src/simf/` code touched.

## 1. Symptoms

Two Streamlit `AppTest` tests fail occasionally under `pytest-xdist` parallelism, but pass on isolated retry:

- `tests/test_share_url.py::test_cold_load_view_log_routes_to_log_surface`
- `tests/test_app_real_flows.py::test_log_surface_link_navigates_from_gear`

Manifests in the wild as: the local pre-push hook (`scripts/hooks/pre-push`, which runs `pytest -q`) red-lights the first push, the user re-pushes seconds later with no code change, and the second push goes green. This was first noted in iteration 7 (PR #97 — worktree-aware Makefile) and has reappeared a handful of times across iterations 8-17.

The CI on master is currently 1058 passed / 16 skipped / 0 failed. The flakiness has never blocked a merge — it only adds ~5 minutes of latency to the push step when it fires.

Both tests share the same shape:

- Use the `app` fixture (function-scoped) → `AppTest.from_file(str(APP_PATH), default_timeout=30)`.
- Pre-populate `app.session_state["char_data"]` with a hand-crafted dict before `app.run()`.
- Assert `app.session_state["view"] == "log"` after a routing decision in `src/simf/ui/app.py`.

Neither test touches the disk, network, or any process-global state explicitly. The flake is _inside_ the AppTest runtime.

## 2. Reproduction attempts

`pyproject.toml` runs xdist with `addopts = "-n auto"` (4 workers on this Pi). Repro attempts from this iteration:

| Attempt                                                                                   | Result                                |
| ----------------------------------------------------------------------------------------- | ------------------------------------- |
| `make test` (full suite, `-n auto`)                                                       | 1058 passed (green)                   |
| 3× targeted: `pytest tests/test_share_url.py tests/test_app_real_flows.py -n 4`           | 22 passed each run (green)            |
| 5× stress: `pytest test_share_url.py test_app_real_flows.py test_app_smoke.py -n 4`       | 34 passed each run (green)            |

**Could not reproduce deterministically this iteration.** That's consistent with the observed pattern: the flake fires under specific xdist scheduling that occurs in the full suite but not in narrow runs. Implications:

- The flake is **not** a function of those tests in isolation — it needs the broader suite running in parallel to surface.
- It is **not** seed-controlled in any obvious way (xdist worker assignment is not seeded for our setup).
- Re-running locally with `pytest --lf` would not deterministically reproduce, so the existing pre-push retry pattern (re-push without changes) is effectively the only "fix" in place.

A fuller repro would likely require ~10-20 sequential `make test` runs in a tight loop on the same host, ideally captured under `--capture=no -v` so any worker-id correlation can be eyeballed. That's larger than fits one iteration.

## 3. Hypothesis

The most likely root cause is **`AppTest` instance state bleed across xdist workers**, specifically:

1. **Streamlit module-level state.** `AppTest.from_file()` imports the target script. Streamlit caches script ASTs, widget registries, and runtime state at module level. xdist's `--forkserver`-ish default (`loadscope` distribution + multiple worker processes) means several `AppTest` instances are alive at the same moment, each loading the same `src/simf/ui/app.py` module. If the module retains process-global state (e.g., a `@st.cache_data` decorator, a `_CACHE` dict, a `st.session_state` simulator that's not perfectly hermetic), two workers can stomp on each other.
2. **Function-scoped fixture, but module-scoped imports.** The `app` fixture is `scope="function"`, but the import chain it triggers (`AppTest.from_file(APP_PATH)`) re-evaluates `src/simf/ui/app.py` once per worker process — and then again per test through the AppTest machinery. If any module-level code in `app.py` mutates a singleton (e.g., a logging handler, a global Streamlit `session_state` proxy), the second test in the same worker can observe leakage from the first.
3. **`SafeSessionState` non-hermeticity.** The tests bracket-access `app.session_state` and use `"key" in app.session_state` instead of `.get()` — a comment in `test_app_real_flows.py:48` flags that `SafeSessionState` (AppTest's wrapper) does not implement `.get()`. This shape is brittle: a stale key from a prior test on the same worker could trigger a "char_data not set" assertion failure (the failure mode iteration 7 noted matches this).
4. **Timeout race.** Both tests use `default_timeout=30`. Under heavy 4-worker xdist load on a Pi 5, the Streamlit run loop can occasionally exceed 30s for first-paint cold loads — and the assertion `assert app.session_state["view"] == "log"` would fail because the routing decision happens late in the run. This is a less-likely cause (would manifest as an explicit timeout, not a flaky assertion), but worth noting.

The leading candidate is **(1) + (2): module-level state retained across AppTest instances inside a single xdist worker process.** This matches the "first push fails, second push passes" pattern: the failing run is the one where xdist happens to schedule both flaky tests on the same worker; the passing run schedules them onto different workers.

## 4. Suggested fixes (do not commit any without follow-up investigation)

Ranked rough-to-clean:

1. **Force serial execution for the AppTest module.** Add a pytest marker (e.g., `pytestmark = pytest.mark.serial`) and configure `pytest-xdist` to honour it. Requires either `pytest-xdist >= 3.0` `--dist loadscope` + a custom hook, or a small `conftest.py` shim that skips xdist scheduling for marked tests. Lowest-risk fix; cost is ~30s of wall clock per `make test` (those tests run sequentially instead of parallel).
2. **`@pytest.mark.no_xdist` style escape hatch.** Same idea, different ergonomics — add a `no_xdist` marker and a conftest hook that dispatches marked tests to a single worker. Cleaner if more AppTest tests get added later.
3. **`pytest-forked` / `pytest-isolate` for the two named tests.** Run each in its own subprocess. Heaviest hammer; guarantees no shared module state. Likely overkill but worth keeping in the toolkit if (1) doesn't hold.
4. **Restructure fixtures with explicit AppTest teardown.** Change `app` from `scope="function"` (already function-scoped, but the import isn't) to a per-test fresh-process pattern — e.g., use `multiprocessing.Process` to launch `AppTest.from_file` in a clean interpreter for each test. Heavy; only reach for it if (1)-(3) all fail.
5. **Pin the suite to `-p no:xdist` and accept the slower local run.** Wall-clock cost: `make test` goes from ~258s to ~13min on this Pi (rough estimate from `-n 1` baseline). Bad trade for everyday use, but a sane fallback for CI if local xdist keeps misbehaving.

The first fix that should be tried is (1) — it's narrow, reversible, and directly targets the hypothesis. If it eliminates the flake across 10+ full-suite runs, it's the answer. If the flake survives serial execution of just those two tests, the hypothesis is wrong and (4) becomes the next investigation.

## 5. Disclaimers

- This audit could not reproduce the flake. Conclusions are inferential, drawn from the observed pattern + the AppTest fixture shape + Streamlit's known module-level caching behaviour. A real fix needs deterministic repro first.
- The flake has **never** failed CI on a final push — it only causes one-off retry friction during local push. So the cost of not-fixing is bounded.
- Iteration 7 wrote the pre-push hook (PR #97); the hook is doing the right thing (catching the flake locally). The fix lives in the test layer, not the hook.

## 6. Next iteration suggestions

If picking this up:

1. First, get a reliable repro: loop `make test` 20× and look for failures correlated to xdist worker id. Add `-v --tb=long` so the failure mode is visible.
2. If reproduced, try suggested-fix (1) and verify the flake disappears across another 20 loops.
3. Land that as a small PR — `tests/conftest.py` or `pyproject.toml` config only, no `src/simf/` touched.
4. If the flake survives (1), the hypothesis was wrong — escalate to the AppTest internals, possibly file an upstream bug on Streamlit.
