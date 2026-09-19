"""ui/widgets.py's gem/enchant trial buttons — the render-layer half of the
2026-08-07 enchant/gem trial fix (see test_trial_gem_enchant_mutators.py for
the state-layer half). Monkeypatches the already-imported `st` reference on
the widgets module itself (not sys.modules — widgets.py is imported by many
other test modules first, so patching sys.modules after the fact wouldn't
reach its bound `st` name).

Both buttons are called INLINE (`if st.button(...): _apply_trial_x(...)`),
not `on_click=` — the mutators end in `st.rerun()`, which only forces an
immediate rerun when called this way (as an `on_click` callback it's a
no-op warning, since Streamlit already reruns after those on its own; a
live click-through caught this the hard way — the button rendered and did
nothing)."""

from __future__ import annotations

from dataclasses import dataclass

import simf.ui.widgets as widgets


class _FakeButton:
    calls: list[dict]

    def __init__(self, *, clicked: bool = False):
        self.calls = []
        self._clicked = clicked

    def __call__(self, label, **kwargs):
        self.calls.append({"label": label, **kwargs})
        return self._clicked


class _FakeSt:
    def __init__(self, *, clicked: bool = False):
        self.button = _FakeButton(clicked=clicked)


@dataclass
class _FakeGemRow:
    slot: str = "neck"
    index: int = 0
    best_gem_id: int | None = 240894
    best_label: str | None = "Flawless Versatile Peridot"


@dataclass
class _FakeEnchantRow:
    best_enchant_id: int | None = 1236054
    best_label: str | None = "Mark of Nalorakk"


def test_gem_trial_button_renders(monkeypatch):
    fake_st = _FakeSt(clicked=False)
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: False)
    widgets._render_gem_trial_button(_FakeGemRow(), "neck")
    assert len(fake_st.button.calls) == 1
    assert "Flawless Versatile Peridot" in fake_st.button.calls[0]["label"]
    # on_click/args wiring is exactly the anti-pattern this fix moved away
    # from (see module docstring) — must never reappear on this control.
    assert "on_click" not in fake_st.button.calls[0]


def test_gem_trial_button_click_invokes_mutator_with_correct_args(monkeypatch):
    fake_st = _FakeSt(clicked=True)
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: False)
    calls = []
    monkeypatch.setattr(widgets, "_apply_trial_gem", lambda *a: calls.append(a))
    widgets._render_gem_trial_button(_FakeGemRow(), "neck")
    assert calls == [("neck", 0, 240894)]


def test_gem_trial_button_absent_in_consolidated_view(monkeypatch):
    """`slot=None` is the consolidated Gear-tab view spanning every socketed
    slot — no per-row action there, only in the focused single-slot dialog."""
    fake_st = _FakeSt()
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: False)
    widgets._render_gem_trial_button(_FakeGemRow(), None)
    assert fake_st.button.calls == []


def test_gem_trial_button_absent_when_identity_optimal(monkeypatch):
    """`best_gem_id=None` is how the identity-optimal / no-real-candidate
    case reads (callers only pass rows with a real best_label, but the
    button itself re-checks defensively)."""
    fake_st = _FakeSt()
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: False)
    widgets._render_gem_trial_button(_FakeGemRow(best_gem_id=None), "neck")
    assert fake_st.button.calls == []


def test_gem_trial_button_hidden_on_read_only_share(monkeypatch):
    fake_st = _FakeSt()
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: True)
    widgets._render_gem_trial_button(_FakeGemRow(), "neck")
    assert fake_st.button.calls == []


def test_enchant_trial_button_renders(monkeypatch):
    fake_st = _FakeSt(clicked=False)
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: False)
    widgets._render_enchant_trial_button(_FakeEnchantRow(), "chest")
    assert len(fake_st.button.calls) == 1
    assert "Mark of Nalorakk" in fake_st.button.calls[0]["label"]
    assert "on_click" not in fake_st.button.calls[0]


def test_enchant_trial_button_click_invokes_mutator_with_correct_args(monkeypatch):
    fake_st = _FakeSt(clicked=True)
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: False)
    calls = []
    monkeypatch.setattr(widgets, "_apply_trial_enchant", lambda *a: calls.append(a))
    widgets._render_enchant_trial_button(_FakeEnchantRow(), "chest")
    assert calls == [("chest", 1236054)]


def test_enchant_trial_button_absent_when_no_real_candidate(monkeypatch):
    fake_st = _FakeSt()
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: False)
    widgets._render_enchant_trial_button(_FakeEnchantRow(best_enchant_id=None), "chest")
    assert fake_st.button.calls == []


def test_enchant_trial_button_hidden_on_read_only_share(monkeypatch):
    fake_st = _FakeSt()
    monkeypatch.setattr(widgets, "st", fake_st)
    monkeypatch.setattr(widgets, "_is_read_only", lambda: True)
    widgets._render_enchant_trial_button(_FakeEnchantRow(), "chest")
    assert fake_st.button.calls == []
