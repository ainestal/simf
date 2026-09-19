"""Slot-dialog trinket scoring-frame caveat (2026-06-13).

The paperdoll chip and the slot dialog it opens score trinkets in *different*
frames on purpose — the chip uses the proc/use registry at as-dropped ilvl,
the dialog is stats-only at match-my-gear ilvl — so the same trinket shows two
different ΔeHP numbers. ROADMAP "Paperdoll chip vs slot-dialog number
mismatch" asks us to label that so it reads as a frame difference, not a bug.
"""

from __future__ import annotations

from pathlib import Path

from simf.ui.app import _trinket_scoring_caveat

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PY = REPO_ROOT / "src" / "simf" / "ui" / "app.py"
SLOT_DIALOG_PY = REPO_ROOT / "src" / "simf" / "ui" / "slot_dialog.py"


def test_caveat_names_both_frames_and_disclaims_bug():
    c = _trinket_scoring_caveat().lower()
    # The dialog's own frame.
    assert "stats only" in c
    assert "match-my-gear" in c
    # The paperdoll chip's contrasting frame — the number the user actually saw.
    assert "paperdoll" in c
    assert "proc" in c
    assert "as-dropped" in c
    # The reassurance that resolves the "is this broken?" confusion.
    assert "not a bug" in c


def test_caveat_still_carries_the_proc_modeling_warning():
    """It must not lose the original honest warning that procs aren't modeled."""
    c = _trinket_scoring_caveat().lower()
    assert "on-use" in c or "procs" in c
    assert "⚠️" in _trinket_scoring_caveat()


def test_slot_dialog_uses_the_caveat_helper():
    """Tripwire: the trinket branch of _slot_dialog must render the helper, so
    a refactor that drops the reconciliation fails loudly. (st.dialog functions
    don't render cleanly in AppTest, so guard the wiring at the source.)"""
    src = SLOT_DIALOG_PY.read_text()
    body = src.split("def _slot_dialog(")[1].split("\ndef ")[0]
    assert "_trinket_scoring_caveat()" in body
