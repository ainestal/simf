"""Plain-language "why does this stat help me survive" glosses, gated on a
character's own live survivability marginals.

`core/marginals.py:ehp_marginals` (closed-form) and
`core/survivability_weights.py:compute_survivability_marginals` (sim-derived)
already encode WHICH mitigation mechanism each stat's survivability value
flows through — but only as a number. This module answers "why" in one
sentence, sourced from `data/stat_mechanisms.yaml` (one entry per
(class_spec, stat) pair this engine tracks, each citing the actual
file:symbol that implements the mechanism).

The gating mirrors `ui/helpers/gem_panel.py:gem_section_notes` — a mechanism
claim is only ever shown when the character's own LIVE marginals dict shows
the stat is actually priced (nonzero) for their build. This is the same bug
class PR #276 fixed for the stale Guardian-mastery gem caveat: a caveat/tag
about a mechanism must be re-derived from the live marginal every call, not
asserted unconditionally, or it goes stale the moment a build makes the
marginal genuinely zero (e.g. a Druid of the Claw's haste, or a Warrior
without Brutal Vitality's crit).

A (class_spec, stat) pair with NO mechanism anywhere in the engine carries
the literal gloss "not modeled yet — no survival credit" instead of an
invented one — since that statement is true regardless of the marginal
(which is always zero for it anyway), `mechanism_tag` returns it
unconditionally rather than gating it on a marginal that structurally can
never be nonzero.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_STAT_MECHANISMS_FILE = _DATA_DIR / "stat_mechanisms.yaml"

# Verbatim sentinel for a (class_spec, stat) pair with no mechanism anywhere
# in this engine — must match the literal string in stat_mechanisms.yaml.
NOT_MODELED_GLOSS = "not modeled yet — no survival credit"


@lru_cache(maxsize=1)
def _load_stat_mechanisms() -> dict:
    """Load + cache `data/stat_mechanisms.yaml`. Loaded once per process,
    not on every `mechanism_tag` call."""
    with _STAT_MECHANISMS_FILE.open() as f:
        return yaml.safe_load(f) or {}


def _is_priced(marginals: dict, stat: str) -> bool:
    """True when the live marginals dict shows a nonzero value for `stat` on
    EITHER school. Same ">0.0" comparison `gem_panel.gem_section_notes` uses
    (no new epsilon invented) — generalized from its single-school ("p")
    check to both schools defensively, since a mechanism this module glosses
    could in principle be magic-only-nonzero even though none of today's
    catalogued ones are."""
    school_values = marginals.get(stat) or {}
    return any(float(school_values.get(school, 0.0) or 0.0) > 0.0 for school in ("p", "m"))


def mechanism_tag(class_spec: str, stat: str, marginals: dict) -> str | None:
    """The plain-language mechanism gloss for (class_spec, stat), or None.

    Returns None when:
      - there's no catalogued entry for this (class_spec, stat) pair, or
      - the entry describes a REAL mechanism but the character's own live
        `marginals` shows it isn't actually priced (zero/absent) for this
        build — never claim a mechanism that isn't contributing today.

    Returns the entry's gloss unconditionally when it's the
    `NOT_MODELED_GLOSS` sentinel — "this isn't modeled" is true regardless
    of the (always-zero) marginal, so there is nothing to gate.
    """
    data = _load_stat_mechanisms()
    entry = (data.get(class_spec) or {}).get(stat)
    if not entry:
        return None
    gloss = entry.get("gloss")
    if not gloss:
        return None
    if gloss == NOT_MODELED_GLOSS:
        return gloss
    if not _is_priced(marginals, stat):
        return None
    return gloss
