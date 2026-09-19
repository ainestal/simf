"""The survivability-slider `help=` text must never leak a literal `%%`.

Streamlit %-formats a slider's `format=` param but NOT its `help=` string —
`help=` is rendered as plain markdown. A `%%` written there (as if it needed
escaping like `format=`) rendered to the user as "100%%... 0%%... 70%%"
instead of "100%... 0%... 70%%" (round-1 copy audit, 2026-07-05).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from simf.ui import recommend


def test_surv_slider_help_has_no_double_percent(monkeypatch):
    captured = {}

    def fake_slider(*args, **kwargs):
        captured.update(kwargs)
        return kwargs.get("value")

    monkeypatch.setattr(recommend.st, "slider", fake_slider)
    monkeypatch.setattr(recommend.st, "caption", MagicMock())
    monkeypatch.setattr(recommend, "_surv_weight", lambda: 70)
    monkeypatch.setattr(recommend, "_surv_axis_label", lambda _pct: "Pure survival")

    recommend._render_surv_slider()

    assert "%%" not in captured["help"], captured["help"]
    assert "100%" in captured["help"]
    assert "0%" in captured["help"]
