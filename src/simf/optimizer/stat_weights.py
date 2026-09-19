import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace

import numpy as np

from ..core.character import Character
from ..core.profiles import DamageProfile, HealingProfile
from ..core.runner import run_simulation

SECONDARY_STATS = ("haste_rating", "crit_rating", "mastery_rating", "versatility_rating")

# Lower-is-better metrics on SimResult. Stat-weight sign convention:
# weight < 0 => increasing the stat reduces (improves) the metric => good for survivability.
SURVIVABILITY_METRICS = (
    "m_plus_tmi",
    "death_rate",
    "mean_dtps",
    "p99_dtps",
    "mean_5s_window",
    "p95_5s_window",
    "p99_5s_window",
    "p99_10s_window",
    "p99_15s_window",
)


@dataclass
class StatWeight:
    stat: str
    delta_rating: int
    metric: str
    base_value: float
    high_value: float
    low_value: float
    weight_per_1000_rating: float
    weight_stderr: float = 0.0
    weight_ci_low: float = 0.0
    weight_ci_high: float = 0.0


def _stat_sim_worker(payload: tuple) -> float:
    """Module-level worker for ProcessPoolExecutor — must not be nested."""
    char, dmg, heal, iters, seed, metric = payload
    result = run_simulation(char, dmg, heal, iterations=iters, seed=seed)
    return float(getattr(result, metric))


def compute_stat_weights(
    character: Character,
    damage_profile: DamageProfile,
    healing_profile: HealingProfile,
    delta_rating: int = 1000,
    iterations: int = 1000,
    seed: int = 42,
    metric: str = "p99_10s_window",
    bootstrap_seeds: int = 5,
    n_workers: int = 0,
) -> dict[str, StatWeight]:
    """Compute survivability stat weights with 95% CI from multi-seed bootstrap.

    Total work: ``4 × 2 × bootstrap_seeds`` simulations. Runs in parallel across
    CPU cores when ``n_workers > 1`` (default: auto-detect, up to 8).
    """
    if metric not in SURVIVABILITY_METRICS:
        raise ValueError(f"Unknown metric '{metric}'. Available: {SURVIVABILITY_METRICS}")

    if n_workers == 0:
        n_workers = min(os.cpu_count() or 1, 8)

    base_result = run_simulation(
        character, damage_profile, healing_profile, iterations=iterations, seed=seed
    )
    base_value = float(getattr(base_result, metric))

    sub_iter = max(50, iterations // bootstrap_seeds)

    # Build all simulation tasks upfront
    task_keys: list[tuple[str, str, int]] = []  # (stat, "high"/"low", seed_idx)
    payloads: list[tuple] = []
    for stat in SECONDARY_STATS:
        current = getattr(character, stat)
        char_high = replace(character, **{stat: current + delta_rating})
        char_low = replace(character, **{stat: max(0, current - delta_rating)})
        for s in range(bootstrap_seeds):
            sub_seed = seed + s * 10_000
            task_keys.append((stat, "high", s))
            payloads.append(
                (char_high, damage_profile, healing_profile, sub_iter, sub_seed, metric)
            )
            task_keys.append((stat, "low", s))
            payloads.append((char_low, damage_profile, healing_profile, sub_iter, sub_seed, metric))

    # Run parallel when worthwhile; fall back to sequential on any error
    raw: dict[tuple, float] = {}
    if n_workers > 1 and len(payloads) >= 4:
        try:
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                futures = {
                    pool.submit(_stat_sim_worker, p): k
                    for p, k in zip(payloads, task_keys, strict=False)
                }
                for fut, key in futures.items():
                    raw[key] = fut.result()
        except Exception:
            raw = {}  # fall through to sequential
    if not raw:
        for key, payload in zip(task_keys, payloads, strict=False):
            raw[key] = _stat_sim_worker(payload)

    # Assemble StatWeight objects
    weights: dict[str, StatWeight] = {}
    for stat in SECONDARY_STATS:
        weight_samples, high_samples, low_samples = [], [], []
        for s in range(bootstrap_seeds):
            h = raw[(stat, "high", s)]
            lo = raw[(stat, "low", s)]
            high_samples.append(h)
            low_samples.append(lo)
            weight_samples.append((h - lo) / (2 * delta_rating) * 1000)

        ws = np.array(weight_samples)
        weight_mean = float(ws.mean())
        weight_stderr = (
            float(ws.std(ddof=1)) / np.sqrt(bootstrap_seeds) if bootstrap_seeds > 1 else 0.0
        )

        weights[stat] = StatWeight(
            stat=stat,
            delta_rating=delta_rating,
            metric=metric,
            base_value=base_value,
            high_value=float(np.mean(high_samples)),
            low_value=float(np.mean(low_samples)),
            weight_per_1000_rating=weight_mean,
            weight_stderr=weight_stderr,
            weight_ci_low=weight_mean - 1.96 * weight_stderr,
            weight_ci_high=weight_mean + 1.96 * weight_stderr,
        )

    return weights
