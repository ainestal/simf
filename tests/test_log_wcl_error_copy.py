"""`_run_wcl_analysis`'s user-facing error copy.

A bare `f"WCL fetch failed: {exc}"` was the whole message for any
non-`ValueError` failure (network errors, GraphQL errors, timeouts) — a raw
exception repr with no cause or recovery step. Now shows a calm
cause-plus-recovery message with the raw text demoted to a `st.caption`
"Technical detail" line. The `ValueError` branch (actor-not-found and
friends, written to be end-user-safe) is untouched — pinned here as a
regression guard so a future edit doesn't accidentally fold it into the
generic-exception branch's copy.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_generic_exception_shows_calm_message_with_technical_detail_demoted():
    def _script() -> None:
        from simf.ui.log_wcl import _run_wcl_analysis

        def _boom(*_args, **_kwargs):
            raise RuntimeError("Connection reset by peer")

        _run_wcl_analysis(
            _boom,
            report=None,
            fight=None,
            target="Brutoh-Uldum-EU",
            token="fake-token",
            target_actor_id=None,
        )

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception

    errors = [str(e.value) for e in at.error]
    assert len(errors) == 1
    assert "Couldn't reach Warcraft Logs" in errors[0]
    assert "try again in a minute" in errors[0]
    assert "Local log" in errors[0]
    # The raw exception text must NOT be in the headline error anymore.
    assert "Connection reset by peer" not in errors[0]

    captions = [str(c.value) for c in at.caption]
    assert any("Technical detail: Connection reset by peer" in c for c in captions)


def test_value_error_still_shows_raw_message_verbatim():
    """Untouched regression guard — ValueError's message is written to be
    end-user-safe (actor-not-found and friends) and must render as-is,
    with no caption demotion and no generic-failure wrapper text."""

    def _script() -> None:
        from simf.ui.log_wcl import _run_wcl_analysis

        def _boom(*_args, **_kwargs):
            raise ValueError("Actor 'Bob' was not found in this report.")

        _run_wcl_analysis(
            _boom,
            report=None,
            fight=None,
            target="Brutoh-Uldum-EU",
            token="fake-token",
            target_actor_id=None,
        )

    at = AppTest.from_function(_script)
    at.run()
    assert not at.exception

    errors = [str(e.value) for e in at.error]
    assert errors == ["Actor 'Bob' was not found in this report."]
    assert not at.caption
