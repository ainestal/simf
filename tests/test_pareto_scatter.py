"""Phase 6.4 — Pareto scatter helper tests.

Pins the frontier math (``compute_pareto_frontier``) and the
``Alternative → ParetoPoint`` adapter. Render-layer tests are
deferred to AppTest smoke; the math is the load-bearing piece and
deserves its own coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.ui.helpers.pareto_scatter import (
    ParetoPoint,
    alternatives_to_points,
    compute_pareto_frontier,
)


def _p(label: str, ehp: float, dps: float, source: str = "bag") -> ParetoPoint:
    return ParetoPoint(label=label, source=source, delta_ehp=ehp, delta_dps=dps)


# ─── Frontier math ──────────────────────────────────────────────────────────


def test_empty_input_returns_empty_frontier() -> None:
    assert compute_pareto_frontier([]) == []


def test_single_point_is_its_own_frontier() -> None:
    pts = [_p("solo", 1000.0, 5.0)]
    assert compute_pareto_frontier(pts) == pts


def test_strict_dominator_kicks_dominated_off_frontier() -> None:
    """A clearly-better-on-both-axes point dominates a strictly-worse one."""
    dominator = _p("better", 1500.0, 7.0)
    dominated = _p("worse", 500.0, 3.0)
    out = compute_pareto_frontier([dominator, dominated])
    assert out == [dominator]


def test_trade_off_picks_both_stay_on_frontier() -> None:
    """Two points where one wins on eHP and the other on DPS are BOTH
    on the frontier — neither dominates the other on both axes."""
    ehp_pick = _p("eHP", 2000.0, 1.0)
    dps_pick = _p("DPS", 100.0, 10.0)
    out = compute_pareto_frontier([ehp_pick, dps_pick])
    assert set(out) == {ehp_pick, dps_pick}


def test_frontier_sorts_by_delta_ehp_descending() -> None:
    """Returned frontier reads top-eHP first so the UI rendering can
    walk it as an upper-right hull line."""
    top_ehp = _p("top-ehp", 3000.0, 1.0)
    mid = _p("mid", 2000.0, 5.0)
    top_dps = _p("top-dps", 500.0, 12.0)
    out = compute_pareto_frontier([mid, top_dps, top_ehp])
    assert [p.label for p in out] == ["top-ehp", "mid", "top-dps"]


def test_ties_both_stay_on_frontier() -> None:
    """Two points with identical (ΔeHP, ΔDPS) but different labels
    both stay on the frontier — bag and M+ drop with same delta profile
    are both legitimate picks; the strict-inequality clause in the
    dominator test prevents either from knocking the other off."""
    bag = _p("Item", 1000.0, 5.0, source="bag")
    loot = _p("Item", 1000.0, 5.0, source="m+ X")
    out = compute_pareto_frontier([bag, loot])
    assert len(out) == 2
    assert set(out) == {bag, loot}


def test_negative_deltas_handled_correctly() -> None:
    """A swap with negative ΔeHP AND negative ΔDPS is dominated by
    the (0, 0) baseline conceptually, but as a candidate it can still
    be on the frontier if no OTHER candidate dominates it (the
    baseline isn't a candidate in this list)."""
    a = _p("a", -100.0, -1.0)
    b = _p("b", -50.0, -5.0)  # better ehp, worse dps — trade-off vs a
    out = compute_pareto_frontier([a, b])
    assert set(out) == {a, b}  # neither dominates the other


def test_mixed_frontier_with_dominated_cloud() -> None:
    """Realistic case: 3 frontier picks + 3 dominated picks. Only the
    frontier members come back."""
    frontier_ehp = _p("F-ehp", 2000.0, 2.0)
    frontier_mid = _p("F-mid", 1500.0, 6.0)
    frontier_dps = _p("F-dps", 400.0, 12.0)
    dom1 = _p("d1", 1000.0, 1.0)  # dominated by F-mid (and F-ehp)
    dom2 = _p("d2", 1400.0, 4.0)  # dominated by F-mid
    dom3 = _p("d3", 300.0, 8.0)  # dominated by F-dps
    out = compute_pareto_frontier([dom1, frontier_ehp, dom2, frontier_dps, dom3, frontier_mid])
    assert set(out) == {frontier_ehp, frontier_mid, frontier_dps}


# ─── Adapter: Alternative → ParetoPoint ──────────────────────────────────────


@dataclass
class _FakeItem:
    name: str | None = None
    item_id: int = 0
    ilvl: int | None = None


@dataclass
class _FakeAlt:
    item: _FakeItem
    source: str
    avg_delta_ehp: float
    delta_dps: float


def test_adapter_carries_through_basic_fields() -> None:
    alt = _FakeAlt(
        item=_FakeItem(name="Helm of Test", item_id=12345, ilvl=289),
        source="bag",
        avg_delta_ehp=500.0,
        delta_dps=3.5,
    )
    points = alternatives_to_points([alt])
    assert len(points) == 1
    p = points[0]
    assert p.label == "Helm of Test (289)"
    assert p.source == "bag"
    assert p.delta_ehp == 500.0
    assert p.delta_dps == 3.5


def test_adapter_falls_back_to_item_id_when_name_missing() -> None:
    """SimC imports can produce ItemSpec rows without a ``name`` if
    Wowhead is unreachable. The chart label still needs SOMETHING
    readable — fall back to ``item:<id>``."""
    alt = _FakeAlt(
        item=_FakeItem(name=None, item_id=99999),
        source="vault",
        avg_delta_ehp=0.0,
        delta_dps=0.0,
    )
    points = alternatives_to_points([alt])
    assert points[0].label == "item:99999"


def test_adapter_handles_empty_input() -> None:
    assert alternatives_to_points([]) == []


def test_adapter_omits_ilvl_suffix_when_absent() -> None:
    """An ItemSpec without ilvl (legacy/test fixture) gets just the
    bare name — no "(None)" suffix."""
    alt = _FakeAlt(
        item=_FakeItem(name="Bare Item", item_id=1, ilvl=None),
        source="m+",
        avg_delta_ehp=10.0,
        delta_dps=1.0,
    )
    points = alternatives_to_points([alt])
    assert points[0].label == "Bare Item"
