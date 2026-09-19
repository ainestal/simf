"""Unit tests for ``scripts/recalibrate_from_community_corpus.py``.

The WCL network path (``cmd_calibrate``, imported from the sibling
``calibrate_spec_from_wcl.py``) is not exercised here — same scope
discipline as ``tests/test_calibrate_spec_from_wcl.py``, which pins that
script's own pure-parsing logic and leaves the network path untested. This
file covers the registry-parsing logic (``load_consented_fights_by_spec``)
and confirms the dynamic sibling-script loader actually resolves a callable,
without invoking it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "recalibrate_from_community_corpus.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("recalibrate_from_community_corpus", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recalibrate_from_community_corpus"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _write_corpus(tmp_path: Path, submissions: list[dict]) -> Path:
    corpus_path = tmp_path / "community_corpus.yaml"
    with open(corpus_path, "w") as f:
        yaml.safe_dump({"submissions": submissions}, f)
    return corpus_path


def test_missing_file_returns_empty_dict(tmp_path):
    assert mod.load_consented_fights_by_spec(tmp_path / "does_not_exist.yaml") == {}


def test_empty_submissions_list_returns_empty_dict(tmp_path):
    corpus = _write_corpus(tmp_path, [])
    assert mod.load_consented_fights_by_spec(corpus) == {}


def test_consented_entry_parses_into_the_cmd_calibrate_tuple_shape(tmp_path):
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "spec": "vengeance_demon_hunter",
                "wcl_code": "ExampleCode1111111",
                "fight_id": 1,
                "source_id": 2,
                "target_name": "AnonPlayerX1",
                "consented": True,
                "submitted_via": "issue #431",
            }
        ],
    )
    result = mod.load_consented_fights_by_spec(corpus)
    assert result == {"vengeance_demon_hunter": [("ExampleCode1111111", 1, 2, "AnonPlayerX1")]}


def test_non_consented_entry_is_excluded(tmp_path):
    """Missing consent is the one safety net between a hand-reviewed
    registry and actually running a fetch — a real bug here (e.g. treating
    a missing/false `consented` key as opt-in) would silently process a
    submission nobody agreed to have used."""
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "spec": "blood_death_knight",
                "wcl_code": "ABC123",
                "fight_id": 1,
                "source_id": None,
                "target_name": "Somebody",
                "consented": False,
            },
            {
                "spec": "blood_death_knight",
                "wcl_code": "DEF456",
                "fight_id": 2,
                "source_id": None,
                "target_name": "SomebodyElse",
                # consented key entirely absent — must NOT default to opt-in
            },
        ],
    )
    assert mod.load_consented_fights_by_spec(corpus) == {}


def test_source_id_none_falls_back_to_name_lookup(tmp_path):
    """A submission without a WCL actor id must still parse — `source_id`
    stays `None`, matching `calibrate_spec_from_wcl._parse_fight`'s own
    "SOURCE may be empty" convention."""
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "spec": "brewmaster_monk",
                "wcl_code": "XYZ789",
                "fight_id": 3,
                "source_id": None,
                "target_name": "AnonPlayerX2",
                "consented": True,
            }
        ],
    )
    result = mod.load_consented_fights_by_spec(corpus)
    assert result["brewmaster_monk"] == [("XYZ789", 3, None, "AnonPlayerX2")]


def test_multiple_specs_grouped_separately(tmp_path):
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "spec": "vengeance_demon_hunter",
                "wcl_code": "AAA",
                "fight_id": 1,
                "source_id": 1,
                "target_name": "A",
                "consented": True,
            },
            {
                "spec": "blood_death_knight",
                "wcl_code": "BBB",
                "fight_id": 2,
                "source_id": 2,
                "target_name": "B",
                "consented": True,
            },
            {
                "spec": "vengeance_demon_hunter",
                "wcl_code": "CCC",
                "fight_id": 3,
                "source_id": 3,
                "target_name": "C",
                "consented": True,
            },
        ],
    )
    result = mod.load_consented_fights_by_spec(corpus)
    assert len(result["vengeance_demon_hunter"]) == 2
    assert len(result["blood_death_knight"]) == 1


def test_shipped_registry_file_parses_and_starts_empty():
    """The real, committed `src/simf/data/community_corpus.yaml` must parse
    cleanly and start with zero entries — this is a fresh registry, not a
    stub with placeholder data."""
    from simf.core.constants import DATA_DIR

    result = mod.load_consented_fights_by_spec(DATA_DIR / "community_corpus.yaml")
    assert result == {}


def test_load_cmd_calibrate_resolves_a_callable():
    """Confirms the dynamic sibling-script loader actually works end to
    end (module loads, attribute exists) without invoking the function
    itself (which needs live WCL network access)."""
    cmd_calibrate = mod._load_cmd_calibrate()
    assert callable(cmd_calibrate)
