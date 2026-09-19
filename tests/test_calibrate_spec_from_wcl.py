"""Unit tests for ``scripts/calibrate_spec_from_wcl.py`` — pure parsing only.

The WCL network path (``cmd_calibrate``) is not exercised here; we pin the
``--fight CODE:FIGHT:SOURCE:NAME`` parser because a mis-parse points the
characterization at the wrong fight or the wrong tank. The script lives
outside ``src/`` so we import it via a path-based loader (no install step),
matching ``tests/test_prune_merged_branches.py``.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "calibrate_spec_from_wcl.py"


def _load():
    spec = importlib.util.spec_from_file_location("calibrate_spec_from_wcl", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_spec_from_wcl"] = mod
    spec.loader.exec_module(mod)
    return mod


cal = _load()


def test_parse_fight_full():
    assert cal._parse_fight("ExampleCode1111111:1:2:AnonPlayerX1") == (
        "ExampleCode1111111",
        1,
        2,
        "AnonPlayerX1",
    )


def test_parse_fight_empty_source_is_none():
    # SOURCE may be blank → fall back to the masterData name lookup.
    assert cal._parse_fight("ABC123:5::AnonPlayerX2") == ("ABC123", 5, None, "AnonPlayerX2")


def test_parse_fight_name_keeps_spaces_via_maxsplit():
    # maxsplit=3 keeps everything after the 3rd colon as the name (names can
    # contain spaces; they never contain colons).
    assert cal._parse_fight("ABC:2:7:Brutoh of Uldum") == ("ABC", 2, 7, "Brutoh of Uldum")


@pytest.mark.parametrize("bad", ["ABC:1", "ABC", "ABC:1:2", ""])
def test_parse_fight_too_few_parts_raises(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        cal._parse_fight(bad)


def test_parse_fight_non_int_fight_raises():
    with pytest.raises(argparse.ArgumentTypeError):
        cal._parse_fight("ABC:notanint:2:Name")


# ── Symmetric coverage for the decomposition tool's identical --fight format ──
# `per_school_gap_from_wcl.py` parses the same CODE:FIGHT:SOURCE:NAME shape; pin
# it so the two tools can't silently drift apart on the load-bearing parse.
_DECOMP = _SCRIPT.parent / "per_school_gap_from_wcl.py"


def _load_decomp():
    spec = importlib.util.spec_from_file_location("per_school_gap_from_wcl", _DECOMP)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["per_school_gap_from_wcl"] = mod
    spec.loader.exec_module(mod)
    return mod


decomp = _load_decomp()


def test_decomp_parse_fight_full():
    assert decomp._parse_fight("ExampleCode1111111:1:2:AnonPlayerX1") == (
        "ExampleCode1111111",
        1,
        2,
        "AnonPlayerX1",
    )


def test_decomp_parse_fight_empty_source_is_none():
    assert decomp._parse_fight("ABC123:5::AnonPlayerX2") == ("ABC123", 5, None, "AnonPlayerX2")


@pytest.mark.parametrize("bad", ["ABC:1", "ABC", "ABC:1:2"])
def test_decomp_parse_fight_too_few_parts_raises(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        decomp._parse_fight(bad)
