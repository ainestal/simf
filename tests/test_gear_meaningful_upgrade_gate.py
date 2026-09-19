"""The meaningful-upgrade gate on the gear paperdoll (2026-06-30).

A slot only flags a swap when the best candidate's ΔeHP clears a fraction of
baseline eHP — so marginal vers re-shuffles (any tiny +ΔeHP) stop lighting up
~14/16 slots. The full max-vers sweep stays reachable via Browse / the upgrade
panel (those read ``ranked_by_slot``, which the gate does NOT touch). These
tests pin the pure gate predicate + the baseline-eHP denominator + the constant.
"""

from __future__ import annotations

from simf.core.constants import load_constants
from simf.ui import app


def _pick(*, composite: float, delta_ehp: float, is_swap: bool = True) -> app._SlotPick:
    return app._SlotPick(
        slot="head",
        item=object(),
        is_swap=is_swap,
        delta_ehp=delta_ehp,
        delta_dps=0.0,
        composite=composite,
        has_warning=False,
    )


def _baseline() -> app._SlotPick:
    return app._SlotPick(
        slot="head",
        item=None,
        is_swap=False,
        delta_ehp=0.0,
        delta_dps=0.0,
        composite=0.0,
        has_warning=False,
    )


# ── the constant ───────────────────────────────────────────────────────────────


def test_meaningful_upgrade_pct_constant_present_and_sane() -> None:
    pct = load_constants()["survivability_recommender"]["meaningful_upgrade_ehp_pct"]
    assert isinstance(pct, (int, float))
    assert 0.0 <= pct < 0.5, "a fraction of eHP, not a percent or a silly bar"


# ── baseline eHP denominator (shared with the ΔeHP % framing) ───────────────────


class _StubChar:
    def __init__(self, phys: float, magic: float) -> None:
        self._p, self._m = phys, magic

    def effective_hp_physical(self) -> float:
        return self._p

    def effective_hp_magic(self) -> float:
        return self._m


def test_char_baseline_ehp_is_mean_of_physical_and_magic() -> None:
    assert app._char_baseline_ehp(_StubChar(1_000_000, 600_000)) == 800_000


# ── the gate predicate ──────────────────────────────────────────────────────────


def test_below_threshold_ehp_gain_is_not_a_swap() -> None:
    """A composite WIN with a sub-bar survival gain must NOT flag (the noise)."""
    best = _pick(composite=0.7, delta_ehp=500.0)  # +500 eHP, wins composite
    assert not app._is_meaningful_swap(best, _baseline(), ehp_threshold=5_000.0)


def test_at_or_above_threshold_ehp_gain_is_a_swap() -> None:
    best = _pick(composite=0.7, delta_ehp=5_000.0)
    assert app._is_meaningful_swap(best, _baseline(), ehp_threshold=5_000.0)
    bigger = _pick(composite=0.9, delta_ehp=42_000.0)
    assert app._is_meaningful_swap(bigger, _baseline(), ehp_threshold=5_000.0)


def test_composite_loss_is_never_a_swap_even_with_big_ehp() -> None:
    """If the candidate doesn't beat the equipped composite, it's not picked —
    the eHP gate only *tightens*, never overrides the slider's verdict."""
    best = _pick(composite=0.0, delta_ehp=99_999.0)  # ties baseline composite (0.0)
    assert not app._is_meaningful_swap(best, _baseline(), ehp_threshold=5_000.0)


def test_no_candidate_is_not_a_swap() -> None:
    assert not app._is_meaningful_swap(None, _baseline(), ehp_threshold=5_000.0)


def test_zero_threshold_disables_the_gate() -> None:
    """threshold=0 → any composite win flags (the pre-2026-06-30 behavior),
    so setting the constant to 0 is a clean kill-switch."""
    best = _pick(composite=0.001, delta_ehp=1.0)
    assert app._is_meaningful_swap(best, _baseline(), ehp_threshold=0.0)
