"""simf UI — sim-derived eHP marginals helpers (L2).

Wraps the closed-form ``ehp_marginals`` / sim-derived
``compute_survivability_marginals`` paths with the per-character cache
(session + disk), single-flight sim slot, and the silent-fallback caveat
surfacing. No ``st.*`` at module scope — ``st.spinner`` only runs inside
``_marginals_for`` (called from main()).

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1 foundation layer (``state``) and core. NEVER imports from ``app``.
"""

from __future__ import annotations

import streamlit as st

from simf.core.character import Character
from simf.core.character_fields import sim_affecting_signature
from simf.core.constants import load_constants
from simf.core.marginals import ehp_marginals
from simf.core.survivability_weights import compute_survivability_marginals
from simf.ui.state import _is_public_mode, _sim_slot, _ss

# Single source of truth for BOTH `_marginals_for`'s Monte Carlo compute below
# AND the noise-summary provenance label the Calibration-details popover
# renders (`run_config.py`'s `marginal_noise_basis` field, populated via
# `marginal_noise_basis_label()`). Passed explicitly to
# `compute_survivability_marginals` rather than relying on that function's
# own defaults for `ci_resamples`/`ci_seed`, so a future default change
# there can't silently desync the popover's label from what was actually
# computed. This is a DIFFERENT, smaller sim than the vault/gear sweep the
# popover's "iter=1,000" line describes elsewhere — conflating the two was
# the reproducibility bug this constant block fixes.
MARGINALS_SIM_ITERATIONS = 300
MARGINALS_SIM_SEED = 42
MARGINALS_CI_RESAMPLES = 1000
MARGINALS_CI_SEED = 42


def _marginals_for(char: Character) -> dict:
    """Sim-derived eHP marginals, cached per character signature.

    Replaces the closed-form `ehp_marginals(char)` path in every gear-surface
    caller. The compute is ~10–20 s on Pi so we hide it behind a one-time
    spinner per character load; subsequent reads come from session state.

    Falls back to the closed-form transparently if the sim path errors or
    the stamina anchor can't be established — the picker keeps working
    either way. Stale entries auto-expire on `constants_version` bumps so
    a yaml change forces a recompute.

    ``SIMF_FAST_MARGINALS=1`` env var skips the sim and uses closed-form
    directly — set by `tests/conftest.py` so AppTest UI smoke tests don't
    hit the 30 s timeout. Survivability-weight correctness has its own
    dedicated tests in ``tests/test_survivability_weights.py``.
    """
    import os

    if os.environ.get("SIMF_FAST_MARGINALS") == "1":
        return ehp_marginals(char)

    sig = _char_marginals_signature(char)
    cache = _ss().setdefault("_surv_marginals_cache", {})
    if sig in cache:
        return cache[sig]
    # Disk cache — survives a browser refresh, so the demo landing funnel pays
    # the 10-20s compute once per machine instead of on every cold load. Keyed
    # by the same signature (constants_version folded in), best-effort on errors.
    # Demo-only, same rationale as `core.key_verdict_cache`: a real visitor's
    # unique gear signature has near-zero cache-hit value (nobody else will
    # ever share it), and writing — or even looking up — it server-side would
    # be real-visitor data persisting past the session, contradicting the
    # privacy footer's "never written to disk" claim for non-demo characters.
    from simf.core import marginals_cache

    is_demo_char = bool(_ss().get("_loaded_demo_slug"))
    ci_cache = _ss().setdefault("_surv_marginals_ci_cache", {})
    disk = marginals_cache.load(sig) if is_demo_char else None
    if disk is not None:
        cache[sig] = disk
        _ss()[f"_surv_marginals_fallback_{sig}"] = None
        # CI is a separate on-disk read (may be None — an old cache entry
        # written before this field existed, or one stored with the CI
        # disabled). Real fresh computes always populate it below; a
        # disk-cache hit is the one path that can honestly have none.
        ci_cache[sig] = marginals_cache.load_ci(sig)
        return disk
    # Single-flight: if another sim already holds the slot, return the instant
    # closed-form for THIS render rather than thrash the GIL. Not cached, so a
    # later rerun retries and lands the real sim-derived marginals. Under
    # concurrent public load this is the COMMON case, not a rare one, so it
    # gets the same caveat-surfacing the exception/anchor-failed branches
    # below already have — a launch-readiness audit (2026-08-01) found this
    # branch was the one silent exception, showing a degraded number with no
    # on-screen indication (exactly the regression Phase 2.7 fixed elsewhere).
    with _sim_slot() as slot:
        if not slot:
            _ss()[f"_surv_marginals_fallback_{sig}"] = (
                "another visitor's stat-weight sim is running; using closed-form derivative"
            )
            return ehp_marginals(char)
        fallback_reason: str | None = None
        ci: dict | None = None
        with st.spinner("Calibrating stat weights for this character…"):
            try:
                # The public box runs under a tight cgroup (MemoryMax=900M ≈ 2
                # cores): 4 forked workers risk an OOM-kill of the WHOLE service.
                # Halve them in public mode (slower, but it survives).
                n_workers = 2 if _is_public_mode() else 4
                w = compute_survivability_marginals(
                    char,
                    iterations=MARGINALS_SIM_ITERATIONS,
                    seed=MARGINALS_SIM_SEED,
                    n_workers=n_workers,
                    ci_resamples=MARGINALS_CI_RESAMPLES,
                    ci_seed=MARGINALS_CI_SEED,
                )
                marginals = w.marginals
                ci = w.ci
                if w.meta.get("anchor_failed"):
                    fallback_reason = (
                        f"stat-weight anchor below noise floor "
                        f"({w.meta.get('reason', 'unknown')}); using closed-form derivative"
                    )
            except Exception as exc:
                marginals = ehp_marginals(char)
                fallback_reason = f"sim threw {type(exc).__name__}: {exc}"
        cache[sig] = marginals
        ci_cache[sig] = ci
        # Only persist a real sim result — a closed-form fallback is a transient
        # degraded path (anchor below noise / sim threw); don't freeze it to disk.
        if fallback_reason is None and is_demo_char:
            marginals_cache.store(sig, marginals, ci=ci)
        # Stash so the gear surface can render a caveat. Silent fallback to the
        # old closed-form is exactly the bug Phase 2.7 fixed — surfaced loudly
        # so a future regression doesn't reintroduce it without the user noticing.
        _ss()[f"_surv_marginals_fallback_{sig}"] = fallback_reason
        return marginals


def _surv_marginals_fallback_warning(char: Character) -> str | None:
    sig = _char_marginals_signature(char)
    return _ss().get(f"_surv_marginals_fallback_{sig}")


def _marginals_ci_for(char: Character) -> dict | None:
    """95% bootstrap CI for `char`'s marginals — session cache first, with a
    disk-cache fallback on a session miss.

    The disk fallback matters for render order: `_build_run_config`
    (`ui/load.py`) calls this near the TOP of every script pass, but the
    gear surface's `_marginals_for(char)` call — the thing that actually
    populates the session cache on a fresh compute — runs LATER in that
    same pass (`app.py::main`). Without a disk read here, a prewarmed disk
    cache (the common case for the bundled demo character, or any repeat
    visitor) would still make the Calibration-details popover's noise line
    render one full rerun late: absent on the very pass a user opens the
    popover, and only appearing after some unrelated widget interaction
    forces a second server rerun (a client-side popover toggle never
    triggers one) — caught in a 2026-07-08 review round (elite_tank +
    engine-math validator).

    ``None`` on: `SIMF_FAST_MARGINALS` (test short-circuit — never persists
    a CI to disk either), a disk-cache miss or a pre-CI/CI-disabled entry, a
    closed-form fallback (anchor failed / sim threw), or simply "not
    computed yet, and never cached" — a caller must treat every ``None`` as
    "no claim," never as zero-width.
    """
    sig = _char_marginals_signature(char)
    ci_cache = _ss().setdefault("_surv_marginals_ci_cache", {})
    if sig in ci_cache:
        return ci_cache[sig]
    from simf.core import marginals_cache

    disk_ci = marginals_cache.load_ci(sig)
    ci_cache[sig] = disk_ci
    return disk_ci


def marginal_noise_basis_label() -> str:
    """Provenance clause for the Calibration-details popover's stat-weight
    noise line: the physical-school scope plus the REAL iteration/resample/
    seed basis the CI was computed from.

    Exists so `run_config.py` never has to re-hardcode these numbers (which
    would silently drift from the real compute above) just to describe
    them, and so the line can't be misread against the popover's separate
    "iter=1,000" clause, which counts a different, larger sim (the vault/
    gear sweep) — the exact reproducibility gap a 2026-07-08 review flagged.
    """
    return (
        f"physical school · {MARGINALS_SIM_ITERATIONS} iters · "
        f"{MARGINALS_CI_RESAMPLES:,} bootstrap resamples · seed {MARGINALS_SIM_SEED}"
    )


_NOISE_SUMMARY_LABELS = {
    "versatility_rating": "versatility",
    "haste_rating": "haste",
    "crit_rating": "crit",
    "mastery_rating": "mastery",
    "strength": "strength",
    "agility": "agility",
    "armor_from_gear": "armor",
    "stamina": "stamina",
}


def summarize_marginal_noise(marginals: dict, ci: dict | None) -> str | None:
    """Human-readable Monte Carlo noise summary for the elite "Calibration
    details" popover: relative half-width (``±Y%``) per stat on the
    physical school — the one that drives most gear decisions — widest
    first, e.g. ``"crit ±64%, haste ±24%, mastery ±19%, versatility ±19%"``.

    This is Monte Carlo noise ONLY (the bootstrap CI on the marginal
    estimate itself) — not model error (the ``±X%`` calibration banner) and
    not finite-difference bias from the perturbation step size. Skips any
    stat with no CI (``None`` — physics-pinned, noise-floor-zeroed, or an
    unstable anchor school) or a zero point estimate (a relative width is
    undefined at zero). Returns ``None`` when nothing qualifies — including
    the common case of no CI available at all this session — so the caller
    omits the line entirely rather than showing an empty one.
    """
    if not ci:
        return None
    rows: list[tuple[float, str]] = []
    for stat, by_school in ci.items():
        bound = by_school.get("p")
        point = marginals.get(stat, {}).get("p", 0.0)
        if bound is None or point == 0.0:
            continue
        lo, hi = bound
        half_width_pct = ((hi - lo) / 2.0) / abs(point) * 100.0
        rows.append((half_width_pct, stat))
    if not rows:
        return None
    rows.sort(reverse=True)
    parts = [f"{_NOISE_SUMMARY_LABELS.get(stat, stat)} ±{pct:.0f}%" for pct, stat in rows]
    return ", ".join(parts)


def _char_marginals_signature(char: Character) -> tuple:
    """Stable signature for the marginals cache: every `Character` field that
    can change the derivative, plus the constants version so a yaml bump
    invalidates the cache.

    The field list itself lives in `core.character_fields.
    SIM_AFFECTING_CHARACTER_FIELDS` — the SAME tuple `ui.helpers.run_config`'s
    `compute_reproduction_hash` uses for its hash payload. The two used to be
    hand-maintained separately and drifted twice from the same root cause (a
    field added to `Character` updated one list but not the other): PR #275
    (`agility` — the largest Guardian/Brewmaster/VDH marginal — was missing
    here only) and a second gap the shared module's docstring names in full
    (`max_hp_override` / `detected_talent_spell_ids` were in the repro-hash
    list but not here). See `core.character_fields` for the complete
    per-field rationale and the `max_hp_override` closed-form-fallback
    asymmetry in particular.
    """
    return (
        *sim_affecting_signature(char),
        load_constants().get("constants_version", 0),
    )
