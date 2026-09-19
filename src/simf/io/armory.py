"""Blizzard Battle.net character profile import — client-credentials flow.

Public character data (equipment, stats, talents, M+ score) is accessible via
the Profile API with a server-side client-credentials token alone. No player
OAuth required — this is the same auth flow item_db.py uses, and the same
zero-login lookup pattern Wowhead's profiler uses.

User auth-code OAuth is only needed for PRIVATE data (vault contents,
collections, account-wide history) — deliberately not implemented here.
"""

from __future__ import annotations

import re

from .item_db import _get_token, is_configured  # reuse the cached client-credentials token

__all__ = ["fetch_character_gear", "fetch_character_public", "is_configured"]

# `region` is interpolated straight into the request host
# (`https://{region}.api.blizzard.com/...`), so an unvalidated value is an SSRF
# that could exfiltrate the Bearer token to an attacker host. The UI only ever
# passes a fixed selectbox value, but this function is the trust boundary —
# allowlist the real Blizzard regions and fail closed on anything else.
_VALID_REGIONS = frozenset({"us", "eu", "kr", "tw", "cn"})

# Tank class_specs we model — `fetch_character_public` already composes
# class_spec as `{spec}_{class}` (e.g. "blood_death_knight"), which matches
# these keys for all six tanks. Used to reject non-tank lookups.
_TANK_CLASS_SPECS = frozenset(
    {
        "protection_warrior",
        "protection_paladin",
        "blood_death_knight",
        "guardian_druid",
        "brewmaster_monk",
        "vengeance_demon_hunter",
    }
)

# Blizzard equipment `slot.type` → simf ITEM_SLOTS.
_BLIZZARD_SLOT_MAP = {
    "HEAD": "head",
    "NECK": "neck",
    "SHOULDER": "shoulder",
    "BACK": "back",
    "CHEST": "chest",
    "WAIST": "waist",
    "WRIST": "wrist",
    "HANDS": "hands",
    "LEGS": "legs",
    "FEET": "feet",
    "FINGER_1": "finger1",
    "FINGER_2": "finger2",
    "TRINKET_1": "trinket1",
    "TRINKET_2": "trinket2",
    "MAIN_HAND": "main_hand",
    "OFF_HAND": "off_hand",
    "SHIRT": "shirt",
    "TABARD": "tabard",
}


# Blizzard statistics endpoint → Character field. Stamina is derived from
# Profile API "health" field as health / hp_per_stamina elsewhere; we capture
# the raw value when the API exposes it.
_STAT_FIELDS = {
    "strength": "strength",
    "agility": "agility",
    "stamina": "stamina",
    "armor": "armor_from_gear",
}
# NOTE on the payload shape (verified against the live EU endpoint,
# 2026-06-10): rating entries carry NO `rating` key. They look like
#   {"rating_bonus": 24.09, "value": 29.10, "rating_normalized": 1060}
# where `rating_bonus` is the PERCENT contributed by rating and
# `rating_normalized` is the real post-squish rating value (1060 == 24.09091
# x haste_rating_per_pct=44, confirmed against Brutoh's live payload — see
# tests/test_armory_gear.py's _STATISTICS fixture). Prefer
# `rating_normalized`: `rating_bonus * 100` is the OLD self-fit convention
# PR #214 replaced project-wide on 2026-06-28, and armory.py was the one
# caller never updated (haste/crit/vers landed ~2.3x/2.2x/1.85x too high).
# `rating_bonus * 100` stays only as a defensive fallback for a payload
# variant that omits `rating_normalized`.
# MASTERY IS THE EXCEPTION: mastery_rating_per_pct_* is still a self-fit
# 100 "decode key" (unreviewed this session — see stat_conversion in
# constants.yaml), so mastery must keep encoding as `rating_bonus * 100`,
# NOT `rating_normalized`, or mastery_pct() downstream under-counts real
# mastery by ~3.3x. Do not "fix" mastery the same way as haste/crit.
# Versatility is NOT a dict — the percent lives in the separate
# `versatility_damage_done_bonus` top-level float, but the payload also
# carries the real rating directly as the bare `versatility` field.
# Parry is deliberately absent: a warrior's parry rating_bonus is
# Riposte-derived from crit, so mapping it would double-credit crit.
_RATING_FIELDS = {
    "melee_haste": "haste_rating",
    "ranged_haste": "haste_rating",
    "spell_haste": "haste_rating",
    "melee_crit": "crit_rating",
    "ranged_crit": "crit_rating",
    "spell_crit": "crit_rating",
    "mastery": "mastery_rating",
}


def _fetch_json(url: str, token: str, namespace: str, region: str) -> dict | None:
    try:
        import requests

        resp = requests.get(
            url,
            params={"namespace": namespace, "locale": "en_US"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def fetch_character_public(
    realm_slug: str,
    character_name: str,
    region: str = "eu",
) -> dict | None:
    """Fetch public character data from Blizzard. Returns a Character-shaped dict
    or None if unconfigured / character not found / API failure.

    Returns keys: name, race, class_spec, strength, agility, stamina,
    armor_from_gear, haste_rating, crit_rating, mastery_rating,
    versatility_rating, parry_rating, max_hp_override.

    `talents` is NOT included — caller chooses a sensible loadout default.
    """
    if not realm_slug or not character_name:
        return None
    region = (region or "").lower().strip()
    if region not in _VALID_REGIONS:
        return None  # fail closed — never interpolate an untrusted region into the host
    token = _get_token(region)
    if not token:
        return None

    realm = realm_slug.lower().strip().replace(" ", "-")
    name = character_name.lower().strip()
    base = f"https://{region}.api.blizzard.com/profile/wow/character/{realm}/{name}"
    # Namespace is `profile-{region}` — NOT `profile-{region}.battle.net`.
    # The `.battle.net` suffix returns 403 Forbidden (verified live 2026-06-09);
    # the plain form returns 200. This path was never exercised live before.
    namespace = f"profile-{region}"

    profile = _fetch_json(base, token, namespace, region)
    if not profile:
        return None

    stats = _fetch_json(f"{base}/statistics", token, namespace, region) or {}

    result: dict = {
        "name": profile.get("name", character_name.title()),
    }

    # Race + class/spec
    race_name = (profile.get("race") or {}).get("name", "").lower().replace(" ", "_")
    klass = (profile.get("character_class") or {}).get("name", "").lower().replace(" ", "_")
    spec = (profile.get("active_spec") or {}).get("name", "").lower().replace(" ", "_")
    result["race"] = _normalize_race(race_name)
    result["class_spec"] = f"{spec}_{klass}" if spec and klass else "protection_warrior"

    # Max HP override (health is the displayed total). The profile endpoint
    # sometimes omits it; /statistics carries the same number.
    health = profile.get("health") or stats.get("health")
    if isinstance(health, int) and health > 0:
        result["max_hp_override"] = health

    # Direct stats from /statistics endpoint
    for api_key, char_key in _STAT_FIELDS.items():
        val = stats.get(api_key)
        if isinstance(val, dict):
            val = val.get("effective") or val.get("base") or 0
        if isinstance(val, (int, float)) and val > 0:
            result[char_key] = int(val)

    # The sheet armor is post-racial (Earthen Titan-Wrought Frame bakes its
    # +10% into `effective`), while Character.armor_from_gear is pre-racial —
    # the engine re-applies the multiplier (core/character.py). Back it out
    # here or Earthen characters get armor credited twice (~+10%). Same
    # convention as data/characters/brutoh.yaml's hand-calibrated block.
    if result.get("armor_from_gear") and result.get("race") == "earthen":
        from simf.core.constants import load_constants

        mult = load_constants()["racials"]["earthen_titan_wrought_frame"]
        result["armor_from_gear"] = int(result["armor_from_gear"] / mult)

    # Secondary ratings, max across melee/ranged/spell variants. `rating`
    # kept as a fallback in case older API versions carried it.
    for api_key, char_key in _RATING_FIELDS.items():
        entry = stats.get(api_key)
        if not isinstance(entry, dict):
            continue
        if char_key == "mastery_rating":
            # Leave mastery on the old pct*100 convention — see the
            # _RATING_FIELDS comment above for why this is deliberate.
            pct = entry.get("rating_bonus")
            rating = pct * 100 if isinstance(pct, (int, float)) else entry.get("rating")
        else:
            normalized = entry.get("rating_normalized")
            if isinstance(normalized, (int, float)) and normalized > 0:
                rating = normalized
            else:
                pct = entry.get("rating_bonus")
                rating = pct * 100 if isinstance(pct, (int, float)) else entry.get("rating")
        if isinstance(rating, (int, float)) and rating > 0:
            result[char_key] = max(result.get(char_key, 0), round(rating))

    # Versatility arrives as a bare float, not a rating dict — and that
    # float is already the real rating (245 == 4.537037% x
    # versatility_rating_per_pct=54), so use it directly. Only fall back to
    # deriving it from the percent field (via the real constant, NOT a
    # hardcoded 100) if a payload variant omits the bare field.
    vers_rating = stats.get("versatility")
    if isinstance(vers_rating, (int, float)) and vers_rating > 0:
        result["versatility_rating"] = round(vers_rating)
    else:
        vers_pct = stats.get("versatility_damage_done_bonus")
        if isinstance(vers_pct, (int, float)) and vers_pct > 0:
            from simf.core.constants import load_constants

            rating_per_pct = load_constants()["stat_conversion"]["versatility_rating_per_pct"]
            result["versatility_rating"] = round(vers_pct * rating_per_pct)

    # Sensible defaults for any field the API didn't provide
    for f in (
        "strength",
        "agility",
        "stamina",
        "armor_from_gear",
        "haste_rating",
        "crit_rating",
        "mastery_rating",
        "versatility_rating",
        "parry_rating",
    ):
        result.setdefault(f, 0)

    return result


# Blizzard returns race names like "Earthen Dwarf", "Highmountain Tauren".
# Map them to simf's modeled race keys (mirrors simc_import.SIMC_RACE_MAP).
_RACE_MAP = {
    "earthen": "earthen",
    "earthen_dwarf": "earthen",
    "tauren": "tauren",
    "highmountain_tauren": "highmountain_tauren",
    "dwarf": "dwarf",
    "dark_iron_dwarf": "dwarf",
    "kul_tiran": "kul_tiran",
}


def _normalize_race(name: str) -> str:
    return _RACE_MAP.get(name, "human")


_ENCHANT_SLOT_PREFIX_RE = re.compile(r"^Enchant [A-Za-z]+ - ")


def _enchant_display_name(raw: str | None) -> str | None:
    """Blizzard's `display_string` is BLTE-flavored: e.g. "Enchanted:
    Enchant Chest - Mark of the Worldsoul |A:Professions-ChatIcon-Quality-12-
    Tier2:20:20|a" or, for an armor kit, "Enchanted: +41 Agility/Strength &
    +115 Stamina" (no "Enchant X - " prefix at all — kit-style enchants name
    themselves by their stats instead). Strip the "Enchanted: " lead-in, the
    trailing `|A:...|a` icon-markup tag, and — when present — the "Enchant
    <Slot> - " prefix, so the result matches data/enchants.yaml's bare-name
    convention (e.g. "Mark of the Worldsoul") regardless of import path."""
    if not raw:
        return None
    text = raw.removeprefix("Enchanted: ").strip()
    text = text.split("|A:")[0].strip()
    text = _ENCHANT_SLOT_PREFIX_RE.sub("", text)
    return text or None


def _equipped_from_blizzard(equipment: dict):
    """Map a Blizzard `/equipment` payload to a {slot: ItemSpec} dict.

    Defensive: every field is optional-accessed so a renamed/missing field
    degrades to a still-usable ItemSpec (item_id + slot are the only hard
    requirements) rather than raising. Documented shape:
        equipped_items[].item.id / .slot.type / .level.value /
        .bonus_list[] / .sockets[].item.id /
        .enchantments[].enchantment_id / .display_string
    """
    from .simc_import import ItemSpec

    out: dict[str, ItemSpec] = {}
    for entry in equipment.get("equipped_items") or []:
        if not isinstance(entry, dict):
            continue
        slot_type = (entry.get("slot") or {}).get("type")
        slot = _BLIZZARD_SLOT_MAP.get(slot_type) if isinstance(slot_type, str) else None
        if not slot:
            continue
        item_id = (entry.get("item") or {}).get("id")
        if not isinstance(item_id, int) or item_id <= 0:
            continue
        bonus_ids = [int(b) for b in (entry.get("bonus_list") or []) if isinstance(b, int)]
        gem_ids = [
            int(gid)
            for s in (entry.get("sockets") or [])
            if isinstance(s, dict) and isinstance((gid := (s.get("item") or {}).get("id")), int)
        ]
        enchant_id = None
        enchant_name = None
        for ench in entry.get("enchantments") or []:
            if not isinstance(ench, dict):
                continue
            slot_t = (ench.get("enchantment_slot") or {}).get("type")
            eid = ench.get("enchantment_id")
            if isinstance(eid, int) and slot_t in (None, "PERMANENT"):
                enchant_id = int(eid)
                enchant_name = _enchant_display_name(ench.get("display_string"))
                break
        ilvl = (entry.get("level") or {}).get("value")
        out[slot] = ItemSpec(
            slot=slot,
            item_id=int(item_id),
            enchant_id=enchant_id,
            enchant_name=enchant_name,
            gem_ids=gem_ids,
            bonus_ids=bonus_ids,
            name=entry.get("name"),
            ilvl=ilvl if isinstance(ilvl, int) else None,
        )
    return out


def fetch_character_gear(name: str, realm: str, region: str = "eu"):
    """Fetch a character's equipped gear from the Blizzard Profile API.

    Higher fidelity than the Raider.IO source: aggregate stats come from the
    in-game `/statistics` endpoint (exact, not Wowhead-re-derived) while the
    per-slot item list comes from `/equipment`. Returns a ``HydrateResult``
    (``source="blizzard"``) or ``None`` when unconfigured / not found / not a
    modeled tank spec.

    EQUIPPED gear only — the Great Vault and bags are player-OAuth-protected
    and deliberately not fetched (mirrors `fetch_character_public`).
    """
    if not name or not realm:
        return None
    if not is_configured():
        return None

    realm_slug = realm.lower().strip().replace(" ", "-")
    cname = name.strip()

    # Reuse the profile + /statistics parse: exact aggregate stats, race,
    # class_spec (composed as `{spec}_{class}`), and max_hp.
    public = fetch_character_public(realm_slug, cname, region)
    if public is None:
        return None
    class_spec = public.get("class_spec")
    if class_spec not in _TANK_CLASS_SPECS:
        return None  # not a tank we model (or degenerate profile)

    token = _get_token(region)
    if not token:
        return None
    base = f"https://{region}.api.blizzard.com/profile/wow/character/{realm_slug}/{cname.lower()}"
    namespace = f"profile-{region}"  # NOT `.battle.net` — see fetch_character_public
    equipment = _fetch_json(f"{base}/equipment", token, namespace, region)
    if not equipment:
        return None
    equipped = _equipped_from_blizzard(equipment)
    if not equipped:
        return None

    from .character_from_combatant_info import HydrateResult, _spec_to_talents_loadout

    char_data = dict(public)
    char_data["talents"] = _spec_to_talents_loadout(class_spec)
    char_data["region"] = region.upper()
    char_data["server"] = realm

    # shield_armor (off-hand shield's armor in isolation, for block value) isn't
    # in /statistics — resolve just the off-hand item via item_db. One lookup.
    if "off_hand" in equipped:
        from .item_db import resolve_equipped_stats

        oh = resolve_equipped_stats({"off_hand": equipped["off_hand"]}, region) or {}
        if oh.get("shield_armor"):
            char_data["shield_armor"] = int(oh["shield_armor"])

    ilvls = [it.ilvl for it in equipped.values() if it.ilvl]
    avg_ilvl = round(sum(ilvls) / len(ilvls)) if ilvls else 0
    ilvl_str = f", ilvl {avg_ilvl}" if avg_ilvl else ""
    summary = (
        f"Loaded {char_data.get('name', cname)} from Blizzard Armory — "
        f"{class_spec.replace('_', ' ')}{ilvl_str}, {len(equipped)} equipped slots. "
        f"Current gear (vault + bags need a /simc paste)."
    )

    return HydrateResult(
        char_data=char_data,
        equipped=equipped,
        summary=summary,
        source="blizzard",
    )
