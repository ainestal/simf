"""Per-dungeon tractability benchmark for the v0.9 UI redesign.

This benchmark gated v0.9 UI work on knowing the actual cost on Pi-class
hardware. (Historical context: the v0.9 redesign plan that drove it lived
in `UI_REDESIGN_PLAN.md`, which has since been superseded by ROADMAP.md
and the per-version ship blocks therein.) Three questions:

  Q1. Marginals path (analytical ∂eHP/∂stat × school_mix) — used by the
      vault joint optimizer today. Should be sub-millisecond even fanned out
      across 9 vault rows × 7 dungeons.

  Q2. Full Monte Carlo sim at 2k iter (per-dungeon expander, lazy) — single
      dungeon baseline.

  Q3. Full sim × 7 dungeons sequentially at 2k iter — what the per-dungeon
      verdict card breakdown would cost if computed eagerly.

Thresholds from the plan:
  GREEN  Q3 < 5s   → full per-dungeon expander, eager fan-out is fine
  YELLOW Q3 < 10s  → lazy: only sim the expanded row
  RED    Q3 ≥ 10s  → verdict-sentence-only, no per-row dungeon breakdown

The Q1 test runs in normal `make test` (fast, important regression guard).
The Q2/Q3 tests are marked `slow` and skipped unless `--run-slow` is passed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from simf.core.character import Character
from simf.core.constants import DATA_DIR
from simf.core.profiles import load_damage_profile, load_healing_profile
from simf.core.runner import run_simulation
from simf.optimizer.vault_joint_optimizer import enumerate_joint_loadouts

BENCHMARK_ARTIFACT = Path(__file__).parent.parent / "benchmark_results.json"


def _record(key: str, payload: dict) -> None:
    """Append a benchmark measurement to a JSON artifact for human inspection.
    Survives pytest output-capture quirks under run_in_background."""
    data: dict = {}
    if BENCHMARK_ARTIFACT.exists():
        try:
            data = json.loads(BENCHMARK_ARTIFACT.read_text())
        except json.JSONDecodeError:
            data = {}
    data[key] = payload
    BENCHMARK_ARTIFACT.write_text(json.dumps(data, indent=2))


# ── Q1: Marginals path — cheap analytical fan-out ──────────────────────────


@dataclass
class _StubSpec:
    slot: str
    item_id: int


def _stub_stats(spec: _StubSpec) -> dict[str, int]:
    base = (spec.item_id % 7) * 100
    return {
        "stamina": 800 + base,
        "armor_from_gear": 150 + base // 2,
        "versatility_rating": 80 + base // 3,
    }


def _all_dungeons() -> list[dict]:
    with open(DATA_DIR / "dungeons.yaml") as f:
        catalog = yaml.safe_load(f)
    return [{"id": d["id"], "school_mix": d["school_mix"]} for d in catalog["dungeons"]]


def _marginals_brutoh_shape() -> dict:
    """Approximate Brutoh-tier marginals — matches order-of-magnitude of the
    real ehp_marginals() output without requiring the streamlit dep chain."""
    return {
        "stamina": {"p": 30.0, "m": 22.0},
        "armor_from_gear": {"p": 8.0, "m": 0.0},
        "versatility_rating": {"p": 5.0, "m": 4.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
    }


def test_marginals_per_dungeon_9_items_x_all_dungeons_under_50ms():
    """Analytical path: 9 vault items × every dungeon in the catalog via the
    joint optimizer. Should be far under 50ms — this is the path the verdict
    card walks per render. Test was originally hard-coded to 7 dungeons; now
    asserts against `len(_all_dungeons())` so MGT / Maisara / future S2
    additions don't require a code change."""
    vault = [_StubSpec(slot="chest", item_id=i) for i in range(9)]
    equipped = {"chest": _StubSpec(slot="chest", item_id=99)}
    dungeons = _all_dungeons()
    marginals = _marginals_brutoh_shape()
    n_dungeons = len(dungeons)
    assert n_dungeons >= 7  # smoke: catalog never shrinks below the Phase-2 set

    t0 = time.perf_counter()
    for _ in range(10):  # 10 iterations to average out timer jitter
        results = enumerate_joint_loadouts(
            vault_items=vault,
            bag_items_by_slot={},
            equipped=equipped,
            selected_dungeons=dungeons,
            marginals=marginals,
            item_stats_fn=_stub_stats,
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000 / 10

    assert len(results) == 9
    assert all(len(r.score_per_dungeon) == n_dungeons for r in results)
    assert elapsed_ms < 50, (
        f"Marginals fan-out took {elapsed_ms:.2f}ms — expected <50ms. "
        f"Verdict card would lag on every render."
    )
    _record("Q1_marginals_9x7", {"elapsed_ms": elapsed_ms, "threshold_ms": 50})
    print(f"\n[Q1] marginals 9×7 = {elapsed_ms:.2f}ms")


# ── Q2/Q3: Full sim fan-out — slow benchmark ───────────────────────────────


def _load_brutoh_character() -> Character:
    """Load Brutoh, stripping yaml keys Character doesn't accept."""
    with open(DATA_DIR / "characters" / "brutoh.yaml") as f:
        raw = yaml.safe_load(f)
    allowed = {f for f in Character.__dataclass_fields__}
    return Character(**{k: v for k, v in raw.items() if k in allowed})


@pytest.fixture(scope="module")
def brutoh() -> Character:
    return _load_brutoh_character()


@pytest.fixture(scope="module")
def healer():
    return load_healing_profile("m+_high_key_healer")


@pytest.mark.slow
def test_full_sim_2k_iter_single_dungeon_baseline(brutoh, healer, capsys):
    """Q2: One full sim at 2k iter on Brutoh-tier character. Establishes
    per-dungeon-expander unit cost."""
    dmg = load_damage_profile("m+_pull_caster")

    t0 = time.perf_counter()
    result = run_simulation(brutoh, dmg, healer, iterations=2000, seed=42)
    elapsed = time.perf_counter() - t0

    assert result.iterations == 2000
    _record(
        "Q2_full_sim_2k_iter",
        {
            "elapsed_s": elapsed,
            "death_rate": result.death_rate,
            "mean_dtps": result.mean_dtps,
        },
    )
    with capsys.disabled():
        print(
            f"\n[Q2] 1 sim × 2000 iter = {elapsed:.2f}s "
            f"(death_rate {result.death_rate:.1%}, mean_dtps {result.mean_dtps:,.0f})"
        )


@pytest.mark.slow
def test_full_sim_2k_iter_all_dungeons_sequential(brutoh, healer, capsys):
    """Q3: Sequentially sim Brutoh across all 7 dungeon damage profiles at 2k iter.
    This is the cost of the per-dungeon breakdown for ONE item if eagerly computed.

    Threshold gates (v0.9 redesign plan, now in ROADMAP.md history):
        GREEN  < 5s   → eager per-dungeon fan-out is fine
        YELLOW < 10s  → lazy: only sim the expanded row
        RED    ≥ 10s  → verdict-sentence-only, no per-row dungeon breakdown
    """
    with open(DATA_DIR / "dungeons.yaml") as f:
        catalog = yaml.safe_load(f)

    timings: list[tuple[str, float]] = []
    total_t0 = time.perf_counter()
    for d in catalog["dungeons"]:
        dmg = load_damage_profile(d["recommended_profile"])
        t0 = time.perf_counter()
        run_simulation(brutoh, dmg, healer, iterations=2000, seed=42)
        timings.append((d["abbrev"], time.perf_counter() - t0))
    total_elapsed = time.perf_counter() - total_t0

    with capsys.disabled():
        print(
            f"\n[Q3] {len(catalog['dungeons'])} dungeons × 2000 iter sequential = {total_elapsed:.2f}s"
        )
        for abbrev, t in timings:
            print(f"      {abbrev:>6s}: {t:.2f}s")

    if total_elapsed < 5.0:
        verdict = "GREEN — eager per-dungeon fan-out is tractable"
    elif total_elapsed < 10.0:
        verdict = "YELLOW — lazy per-row expansion required"
    else:
        verdict = "RED — downgrade to verdict-sentence-only"
    with capsys.disabled():
        print(f"      → {verdict}")

    _record(
        "Q3_seven_dungeons_2k_iter",
        {
            "total_elapsed_s": total_elapsed,
            "per_dungeon_s": dict(timings),
            "verdict": verdict,
        },
    )


@pytest.mark.slow
def test_full_sim_10k_iter_single_dungeon_verdict_card(brutoh, healer, capsys):
    """Q4: One full sim at 10k iter — the verdict card cost for the picked
    dungeon set. The plan assumes 10k iter is the headline number.
    Used to confirm the cold-start "<30s on phone" success criterion is plausible."""
    dmg = load_damage_profile("m+_pull_caster")

    t0 = time.perf_counter()
    result = run_simulation(brutoh, dmg, healer, iterations=10_000, seed=42)
    elapsed = time.perf_counter() - t0

    assert result.iterations == 10_000
    _record(
        "Q4_full_sim_10k_iter",
        {
            "elapsed_s": elapsed,
            "death_rate": result.death_rate,
            "mean_dtps": result.mean_dtps,
        },
    )
    with capsys.disabled():
        print(f"\n[Q4] 1 sim × 10_000 iter = {elapsed:.2f}s (verdict-card cost)")
