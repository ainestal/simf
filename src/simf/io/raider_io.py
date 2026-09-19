"""Raider.IO character gear import — zero-auth public API.

Fetches a character's currently-equipped gear from Raider.IO and shapes it into
the same ``(char_data, equipped)`` pair the SimC-paste and COMBATANT_INFO
hydrate paths produce, so the Gear surface, trial-swap, and survivability sim
all consume it unchanged (``HydrateResult`` with ``source="raider_io"``).

It is "SimC paste, but the items come from the API": build the
``{slot: ItemSpec}`` map from the gear payload, then reuse
``item_db.resolve_equipped_stats`` (zero-auth, Wowhead-first, bonus-id-aware) to
aggregate the survivability stats — including ``shield_armor`` from the
off-hand.

Limitations (by design — surfaced to the user, not silent):
  * **EQUIPPED gear only.** Raider.IO does not expose the Great Vault or bags
    (those are player-OAuth-protected on the Blizzard API). The vault tab and
    bag-alternatives stay SimC-paste-only.
  * **CURRENT gear, not gear-at-fight-time.** Fine for "analyse my character
    now"; for replaying an *old* log it can drift from what was worn. Using it
    to calibrate the engine against a past fight would reintroduce the
    gear-mismatch the hydrate-calibration notes warn about — that benefit is
    gated on the deferred gear-snapshot storage.

Zero auth required (200 req/min unauthenticated). Returns ``None`` on
not-found / API error / network failure, mirroring ``armory.py``.
"""

from __future__ import annotations

from .character_from_combatant_info import HydrateResult, _spec_to_talents_loadout
from .item_db import resolve_equipped_stats
from .resolver_estimated_stats import (
    apply_guardian_caster_form_flag,
    apply_resolver_estimated_corrections,
)
from .simc_import import SIMC_RACE_MAP, ItemSpec

__all__ = ["character_from_raider_io", "fetch_character_gear"]

_API_BASE = "https://raider.io/api/v1/characters/profile"

# Raider.IO slot vocabulary → simf ITEM_SLOTS. Everything matches except the
# two weapon slots (Raider.IO has no underscore).
_SLOT_MAP = {"mainhand": "main_hand", "offhand": "off_hand"}

# (class, active_spec_name) → simf class_spec, both lower-cased with spaces→_.
_CLASS_SPEC_MAP = {
    ("warrior", "protection"): "protection_warrior",
    ("paladin", "protection"): "protection_paladin",
    ("death_knight", "blood"): "blood_death_knight",
    ("druid", "guardian"): "guardian_druid",
    ("monk", "brewmaster"): "brewmaster_monk",
    ("demon_hunter", "vengeance"): "vengeance_demon_hunter",
}

# Stat keys we copy out of the resolved totals onto char_data (mirrors the
# SimC-paste / hydrate field set).
_STAT_FIELDS = (
    "strength",
    "agility",
    "stamina",
    "armor_from_gear",
    "haste_rating",
    "crit_rating",
    "mastery_rating",
    "versatility_rating",
    "parry_rating",
    "shield_armor",
)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower().replace(" ", "_").replace("-", "_")


def _item_spec(slot: str, raw: dict) -> ItemSpec | None:
    """Map one Raider.IO gear entry to an ItemSpec, or None if it carries no
    item_id (empty slot)."""
    item_id = raw.get("item_id")
    if not isinstance(item_id, int) or item_id <= 0:
        return None
    enchants = raw.get("enchants") or ([] if raw.get("enchant") is None else [raw["enchant"]])
    enchant_id = int(enchants[0]) if enchants else None
    return ItemSpec(
        slot=slot,
        item_id=int(item_id),
        enchant_id=enchant_id,
        gem_ids=[int(g) for g in (raw.get("gems") or []) if isinstance(g, int)],
        bonus_ids=[int(b) for b in (raw.get("bonuses") or []) if isinstance(b, int)],
        name=raw.get("name"),
        ilvl=raw.get("item_level") if isinstance(raw.get("item_level"), int) else None,
    )


def character_from_raider_io(payload: dict, *, resolve_stats_fn=resolve_equipped_stats):
    """Pure transform: Raider.IO profile payload → HydrateResult, or None.

    Separated from the HTTP fetch so it is testable against a recorded payload
    with no network. ``resolve_stats_fn`` takes the ``{slot: ItemSpec}`` map and
    returns the aggregate stat dict (defaults to the real, Wowhead-backed
    resolver; inject a stub in tests).
    """
    if not isinstance(payload, dict) or payload.get("error") or not payload.get("name"):
        return None

    klass = _norm(payload.get("class"))
    spec = _norm(payload.get("active_spec_name"))
    class_spec = _CLASS_SPEC_MAP.get((klass, spec))
    if class_spec is None:
        return None  # not a tank spec we model

    race_key = SIMC_RACE_MAP.get(_norm(payload.get("race")), "human")
    name = payload.get("name", "")
    region = (payload.get("region") or "eu").upper()
    server = payload.get("realm", "")

    gear = payload.get("gear") or {}
    items_raw = gear.get("items") or {}
    equipped: dict[str, ItemSpec] = {}
    for rio_slot, raw in items_raw.items():
        if not isinstance(raw, dict):
            continue
        slot = _SLOT_MAP.get(rio_slot, rio_slot)
        spec_item = _item_spec(slot, raw)
        if spec_item is not None:
            equipped[slot] = spec_item

    if not equipped:
        return None

    stats = resolve_stats_fn(equipped) or {}

    char_data: dict = {
        "name": name,
        "race": race_key,
        "class_spec": class_spec,
        "talents": _spec_to_talents_loadout(class_spec),
        "region": region,
        "server": server,
    }
    for f in _STAT_FIELDS:
        val = stats.get(f)
        if isinstance(val, (int, float)) and val:
            char_data[f] = int(val)
    # The resolver returns {} / a partial dict when item_db is OFFLINE (the
    # public box never makes live item fetches — seed-cache hits only) or an
    # item simply isn't cached, and the loop above SKIPS zero/absent stats. That
    # can leave char_data missing a REQUIRED Character field (stamina,
    # armor_from_gear), so Character.from_dict() 500s the Gear surface. Backfill
    # the required fields to 0 — mirroring the Blizzard path's setdefault loop
    # (io/armory.py). A degraded (0-stat) import beats a crashed surface.
    for f in ("stamina", "armor_from_gear"):
        char_data.setdefault(f, 0)

    # `resolve_stats_fn` is ALWAYS a per-item resolver estimate here — Raider.IO
    # has no "exact export gear_stats" alternative the way a /simc paste does.
    # It is therefore GEAR-ONLY (missing socketed gems'/enchants' own stats and
    # the level-90 base stamina/primary every real character carries) and, for
    # Guardian, the form-independent (caster) stamina value — the exact same
    # gap class ui/helpers/simc_load.py's resolver-fallback branch has, and
    # fixed with the SAME shared correction (io/resolver_estimated_stats.py) so
    # the two paths can't drift out of sync again.
    # Gated on `stats` (the raw resolver output, pre-backfill) being non-empty:
    # when item_db is fully OFFLINE / every item lookup misses, `stats` is `{}`
    # and char_data must stay genuinely all-zero so `Character.is_degraded()`
    # still shows the "couldn't read your gear" banner instead of a fabricated
    # base-stat-only character that LOOKS resolved.
    if stats:
        apply_resolver_estimated_corrections(char_data, equipped, class_spec)
        apply_guardian_caster_form_flag(char_data, class_spec)

    ilvl = gear.get("item_level_equipped")
    ilvl_str = f", ilvl {int(ilvl)}" if isinstance(ilvl, (int, float)) and ilvl else ""
    summary = (
        f"Loaded {name} from Raider.IO — {spec.replace('_', ' ')} "
        f"{klass.replace('_', ' ')}{ilvl_str}, {len(equipped)} equipped slots. "
        f"Current gear (vault + bags need a /simc paste)."
    )

    return HydrateResult(
        char_data=char_data,
        equipped=equipped,
        summary=summary,
        source="raider_io",
    )


def _request_profile(name: str, realm: str, region: str) -> dict | None:
    """HTTP GET the Raider.IO profile (gear + talents). Returns parsed JSON or
    None on any failure. Isolated so the transform stays network-free."""
    try:
        import requests

        resp = requests.get(
            _API_BASE,
            params={
                "region": region.lower(),
                "realm": realm,
                "name": name,
                "fields": "gear",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def fetch_character_gear(
    name: str,
    realm: str,
    region: str = "eu",
    *,
    resolve_stats_fn=resolve_equipped_stats,
):
    """Fetch a character's current equipped gear from Raider.IO.

    Returns a ``HydrateResult`` (``source="raider_io"``) ready for the same
    apply path the COMBATANT_INFO hydrate uses, or ``None`` if the character
    isn't found / isn't a modeled tank spec / the API fails.
    """
    if not name or not realm:
        return None
    payload = _request_profile(name, realm, region)
    if payload is None:
        return None
    return character_from_raider_io(payload, resolve_stats_fn=resolve_stats_fn)
