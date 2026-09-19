"""Tests for optimizer/stat_mechanisms.py + data/stat_mechanisms.yaml parity.

Covers:
- every (class_spec, stat) pair whose yaml entry describes a REAL mechanism
  yields a non-empty tag when the live marginals dict shows a nonzero value
  for that stat;
- the SAME entry yields None when the marginal is zero/absent for that
  build — the exact gating bug class PR #276 fixed for the stale Guardian-
  mastery gem caveat (never assert a mechanism claim for a stat that isn't
  actually priced for this character);
- `leech_rating` — a stat this engine's survival model never wires up for
  any spec — yields the "not modeled yet" sentinel verbatim, regardless of
  what the (structurally impossible) marginal says;
- every `source_ref` in the yaml still resolves to a real file:symbol in the
  engine — a grep-based staleness guard so a future refactor that renames or
  deletes the cited function/attribute fails this test instead of leaving a
  false claim sitting in the data file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from simf.optimizer.stat_mechanisms import (
    _STAT_MECHANISMS_FILE,
    NOT_MODELED_GLOSS,
    mechanism_tag,
)

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "simf"


def _load_raw() -> dict:
    with _STAT_MECHANISMS_FILE.open() as f:
        return yaml.safe_load(f)


def _all_pairs() -> list[tuple[str, str]]:
    data = _load_raw()
    return [(class_spec, stat) for class_spec, stats in data.items() for stat in stats]


ALL_PAIRS = _all_pairs()


def test_yaml_has_entries_and_loads():
    data = _load_raw()
    assert data
    # Every catalogued class_spec actually appears in the engine's class_spec
    # switches (character.py) — guards against a typo'd spec key going
    # silently unused (mechanism_tag would just always return None for it).
    known_specs = {
        "protection_warrior",
        "protection_paladin",
        "blood_death_knight",
        "vengeance_demon_hunter",
        "brewmaster_monk",
        "guardian_druid",
    }
    assert set(data.keys()) == known_specs
    # Every spec catalogues the same stat set, so mechanism_tag's caller
    # never gets an inconsistent "some specs have this stat, some don't"
    # surface for the tracked stats.
    stat_sets = {frozenset(stats.keys()) for stats in data.values()}
    assert len(stat_sets) == 1


@pytest.mark.parametrize("class_spec,stat", ALL_PAIRS)
def test_nonzero_marginal_yields_tag(class_spec, stat):
    """A nonzero fixture marginal for (class_spec, stat) always yields a
    non-None, non-empty tag — whether it's a real mechanism gloss or the
    "not modeled" sentinel (which is unconditional)."""
    entry = _load_raw()[class_spec][stat]
    marginals = {stat: {"p": 321.0, "m": 65.0}}
    tag = mechanism_tag(class_spec, stat, marginals)
    assert tag
    assert tag == entry["gloss"]


@pytest.mark.parametrize("class_spec,stat", ALL_PAIRS)
def test_zero_marginal_gates_real_mechanisms_to_none(class_spec, stat):
    """A zero marginal for (class_spec, stat) yields None for a REAL
    mechanism entry (the gating actually works) but still yields the "not
    modeled" sentinel verbatim for a genuinely-unmodeled pair — that
    statement doesn't depend on the marginal."""
    entry = _load_raw()[class_spec][stat]
    marginals = {stat: {"p": 0.0, "m": 0.0}}
    tag = mechanism_tag(class_spec, stat, marginals)
    if entry["gloss"] == NOT_MODELED_GLOSS:
        assert tag == NOT_MODELED_GLOSS
    else:
        assert tag is None


@pytest.mark.parametrize("class_spec,stat", ALL_PAIRS)
def test_absent_marginal_key_gates_real_mechanisms_to_none(class_spec, stat):
    """Same as the zero-marginal case, but the stat key is entirely absent
    from the marginals dict (e.g. a caller that only passes a subset) —
    must be treated identically to an explicit zero, not KeyError."""
    entry = _load_raw()[class_spec][stat]
    tag = mechanism_tag(class_spec, stat, {})
    if entry["gloss"] == NOT_MODELED_GLOSS:
        assert tag == NOT_MODELED_GLOSS
    else:
        assert tag is None


def test_leech_yields_not_modeled_verbatim():
    # Even with an absurdly large marginal, leech is a genuinely zero
    # mechanism in this engine (no Character field, no consumer anywhere) —
    # the sentinel is returned unconditionally, not gated on the marginal.
    tag = mechanism_tag(
        "protection_warrior", "leech_rating", {"leech_rating": {"p": 99999.0, "m": 99999.0}}
    )
    assert tag == "not modeled yet — no survival credit"
    tag_absent = mechanism_tag("guardian_druid", "leech_rating", {})
    assert tag_absent == "not modeled yet — no survival credit"


def test_unknown_pair_returns_none():
    assert mechanism_tag("protection_warrior", "made_up_stat", {"made_up_stat": {"p": 1.0}}) is None
    assert mechanism_tag("made_up_spec", "stamina", {"stamina": {"p": 1.0, "m": 1.0}}) is None


def test_gloss_avoids_internal_jargon():
    """New/edited user-facing copy should read in plain language — no
    "marginal"/"regression"/"K constant"/"ETMI"/"perturbation" leaking from
    the engine's internal vocabulary into a player-facing sentence."""
    banned = ("marginal", "regression", "k constant", "etmi", "perturbation")
    for class_spec, stats in _load_raw().items():
        for stat, entry in stats.items():
            gloss_lower = entry["gloss"].lower()
            for word in banned:
                assert word not in gloss_lower, (
                    f"{class_spec}/{stat} gloss uses jargon word {word!r}: {entry['gloss']!r}"
                )


# --- source_ref parity: guards against the gloss going stale after a future
# refactor renames/removes the cited symbol. ---

_SOURCE_REF_RE = re.compile(r"^(?P<file>[\w]+\.py):(?P<symbol>[\w.]+)$")


def _find_file(basename: str) -> Path:
    matches = list(_SRC_ROOT.rglob(basename))
    assert matches, f"source_ref names {basename!r}, which doesn't exist anywhere under src/simf"
    return matches[0]


@pytest.mark.parametrize("class_spec,stat", ALL_PAIRS)
def test_source_ref_resolves_to_a_real_symbol(class_spec, stat):
    entry = _load_raw()[class_spec][stat]
    if entry["gloss"] == NOT_MODELED_GLOSS:
        assert entry.get("source_ref") is None
        return
    ref = entry["source_ref"]
    assert ref, f"{class_spec}/{stat} has a real-mechanism gloss but no source_ref"
    m = _SOURCE_REF_RE.match(ref)
    assert m, f"{class_spec}/{stat} source_ref {ref!r} doesn't match the 'file.py:symbol' shape"
    path = _find_file(m.group("file"))
    content = path.read_text()
    # "ClassName.method_name" and bare "attribute_or_function_name" both
    # resolve by requiring every dot-separated part to appear as a whole
    # word somewhere in the cited file — a simple grep-based check (per the
    # task spec) rather than a full AST walk, but still fails loudly the
    # moment a refactor deletes/renames the symbol out of the file.
    for part in m.group("symbol").split("."):
        assert re.search(rf"\b{re.escape(part)}\b", content), (
            f"{class_spec}/{stat} source_ref {ref!r}: {part!r} not found in {path}"
        )


def test_not_modeled_entries_have_no_source_ref():
    for class_spec, stats in _load_raw().items():
        for stat, entry in stats.items():
            if entry["gloss"] == NOT_MODELED_GLOSS:
                assert entry.get("source_ref") is None, (
                    f"{class_spec}/{stat} is 'not modeled' but carries a source_ref"
                )
