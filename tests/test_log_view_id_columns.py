"""Regression: the per-death damage table must NOT render NPC ID /
Spell ID with a trailing ``.0``.

When the column carries a mix of ``int`` and ``None`` (some events have
no parseable ID), pandas's default behaviour is to upcast to ``float64``
with ``NaN`` for the Nones — so an ID of ``12345`` renders as
``12345.0``. The fix is an explicit cast to pandas's nullable ``Int64``
dtype, which renders the value bare AND keeps NaN/<NA> for missing IDs.

This test pins both halves of the contract:
1. The ``astype({"NPC ID": "Int64", "Spell ID": "Int64"})`` call is
   present in ``log_view.py`` (mutation-verified: remove the line, this
   fails).
2. A dataframe constructed the same way with mixed int/None values
   round-trips through ``Int64`` and renders without the ``.0`` suffix.
"""

from __future__ import annotations

import inspect

import pandas as pd

from simf.ui import log_analysis


def test_id_columns_cast_to_nullable_int64():
    # The per-death damage table lives in render_log_analysis, which moved to
    # log_analysis.py in the log_view split.
    src = inspect.getsource(log_analysis)
    assert 'astype({"NPC ID": "Int64", "Spell ID": "Int64"})' in src, (
        "The per-death damage table must cast NPC ID / Spell ID to "
        "nullable Int64 — otherwise pandas promotes mixed int/None to "
        "float64 and IDs render as '12345.0'."
    )


def test_mixed_int_none_renders_without_decimal_after_int64_cast():
    df = pd.DataFrame(
        [
            {"NPC ID": 12345, "Spell ID": 67890},
            {"NPC ID": None, "Spell ID": 67890},
            {"NPC ID": 22222, "Spell ID": None},
        ]
    )
    df = df.astype({"NPC ID": "Int64", "Spell ID": "Int64"})
    rendered = df.to_string(index=False)
    for token in ("12345.0", "67890.0", "22222.0"):
        assert token not in rendered, (
            f"After Int64 cast the rendered table still contains "
            f"'{token}'. Expected bare integer rendering."
        )
    assert "12345" in rendered and "67890" in rendered and "22222" in rendered
