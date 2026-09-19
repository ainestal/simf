"""Online gear-source dispatcher.

Picks the best available source for a character's CURRENT equipped gear and
returns a ``HydrateResult`` (the same shape the SimC-paste / COMBATANT_INFO
hydrate paths produce) plus the source actually used.

Sources, in fidelity order:
  * ``blizzard`` — Blizzard Profile API. Exact in-game aggregate stats
    (`/statistics`) + per-slot items (`/equipment`). Needs free client-
    credentials (``~/.simf/blizzard.yaml`` or env). Highest fidelity.
  * ``raiderio`` — Raider.IO public API. Zero-auth. Items only; stats are
    re-derived from item data via Wowhead. The always-available fallback.

Both expose EQUIPPED gear only (no Great Vault / bags — player-OAuth-gated),
and both return the character's CURRENT gear, not gear-at-fight-time.
"""

from __future__ import annotations

__all__ = ["fetch_online_gear"]


def fetch_online_gear(name: str, realm: str, region: str = "eu", source: str = "auto"):
    """Return ``(HydrateResult | None, source_used)``.

    ``source``:
      * ``"auto"`` — Blizzard when configured (falling back to Raider.IO on a
        miss), else Raider.IO.
      * ``"blizzard"`` / ``"raiderio"`` — force that source (no fallback).
    """
    from . import armory
    from .raider_io import fetch_character_gear as _raiderio_fetch

    if source == "blizzard":
        return armory.fetch_character_gear(name, realm, region), "blizzard"
    if source == "raiderio":
        return _raiderio_fetch(name, realm, region), "raiderio"

    # auto: prefer Blizzard when creds are present, fall back to Raider.IO.
    if armory.is_configured():
        res = armory.fetch_character_gear(name, realm, region)
        if res is not None:
            return res, "blizzard"
    res = _raiderio_fetch(name, realm, region)
    return (res, "raiderio") if res is not None else (None, "auto")
