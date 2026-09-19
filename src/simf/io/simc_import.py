import re
from dataclasses import dataclass, field
from pathlib import Path

from simf.core.constants import load_constants
from simf.io.talent_decoder import decode_hero_tree_name, decode_simc_talent_string

_GUARDIAN_DRUID_SPEC_ID = 104

KNOWN_CLASSES = {
    "warrior",
    "paladin",
    "death_knight",
    "monk",
    "druid",
    "demon_hunter",
    "mage",
    "priest",
    "shaman",
    "warlock",
    "hunter",
    "rogue",
    "evoker",
}

# SimC addon emits multi-word classes without an underscore in the class
# declaration line (e.g. `demonhunter="Lyney"`, `deathknight="Foo"`) even
# though the rest of the export uses the underscored form. Map back to the
# canonical name so downstream `class_spec` and dispatch work.
_CLASS_ALIASES = {
    "demonhunter": "demon_hunter",
    "deathknight": "death_knight",
}

ITEM_SLOTS = {
    "head",
    "neck",
    "shoulder",
    "back",
    "chest",
    "wrist",
    "hands",
    "waist",
    "legs",
    "feet",
    "finger1",
    "finger2",
    "trinket1",
    "trinket2",
    "main_hand",
    "off_hand",
    "tabard",
    "shirt",
    "ranged",
}

# Map SimC race strings to our racial model. Earthen visual variants share racials.
SIMC_RACE_MAP = {
    "earthen": "earthen",
    "earthen_dwarf": "earthen",
    "earthen_council": "earthen",
    "tauren": "tauren",
    "highmountain_tauren": "highmountain_tauren",
    "dwarf": "dwarf",
    "dark_iron_dwarf": "dwarf",
    "kul_tiran": "kul_tiran",
    # Default: pass through; characters with unmodeled races are treated as "human" baseline
}


@dataclass
class ItemSpec:
    slot: str
    item_id: int = 0
    enchant_id: int | None = None
    # Human-readable enchant name, set ONLY by the Blizzard online-lookup path
    # (armory.py — the API resolves it server-side as `display_string`).
    # `.simc`-paste, Raider.IO, and log-hydrate imports never set this; the
    # enchant panel falls back to matching `enchant_id` against the static
    # data/enchants.yaml catalog for those (see ui/helpers/enchant_panel.py).
    enchant_name: str | None = None
    gem_ids: list[int] = field(default_factory=list)
    bonus_ids: list[int] = field(default_factory=list)
    crafted_stats: list[int] = field(default_factory=list)
    crafting_quality: int | None = None
    name: str | None = None
    ilvl: int | None = None


@dataclass
class SimcImport:
    name: str = ""
    level: int = 0
    race: str = ""
    class_name: str = ""
    spec: str = ""
    role: str = ""
    region: str = ""
    server: str = ""
    professions: str = ""
    talents: str = ""
    saved_loadouts: dict[str, str] = field(default_factory=dict)
    items: dict[str, ItemSpec] = field(default_factory=dict)
    # Items in bags — each slot can have multiple bag items (vault picks, spares, etc.)
    bag_items: dict[str, list[ItemSpec]] = field(default_factory=dict)
    # Weekly vault reward choices
    vault_items: dict[str, list[ItemSpec]] = field(default_factory=dict)
    # Raw stat lines if present in the SimC export (newer addon versions emit
    # `gear_haste_rating=`, `gear_stamina=` etc.). Maps Character field names to
    # integer values. Empty dict if the export didn't include these lines.
    gear_stats: dict[str, int] = field(default_factory=dict)
    # Crest / valorstone balances from the "### Additional Character Info" block.
    # ``upgrade_currencies`` holds the ``c:<id>:<amount>`` entries (spendable
    # crests + valorstones); ``upgrade_items`` holds ``i:<id>:<amount>`` entries
    # (upgrade *items* like Voidcore). ``catalyst_currencies`` holds the
    # ``<id>:<amount>`` catalyst charges. Currency-id → human name is resolved
    # downstream from a (drift-prone) data table, kept out of the parser.
    upgrade_currencies: dict[int, int] = field(default_factory=dict)
    upgrade_items: dict[int, int] = field(default_factory=dict)
    catalyst_currencies: dict[int, int] = field(default_factory=dict)
    # Per-slot upgrade watermarks: ``(slot_index, current_ilvl, max_reachable_ilvl)``.
    # The slot-index → slot-name map is intentionally NOT resolved here: a single
    # export can't pin it unambiguously (many slots share an ilvl), and the
    # upgrade-comparison feature only needs the reachable *ceilings*, read off
    # the ``max`` field via ``account_ilvl_ceiling``.
    slot_high_watermarks: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def account_ilvl_ceiling(self) -> int | None:
        """Highest reachable ilvl across all watermarked slots, or None when
        the export carried no watermark line. The natural upper bound for an
        "upgrade to" slider — derived from the player's own account, so it
        needs no hardcoded season table to stay honest."""
        if not self.slot_high_watermarks:
            return None
        return max(m for _, _, m in self.slot_high_watermarks)


_ITEM_NAME_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")

# Map SimC `gear_*` stat lines (newer addon versions emit these) to Character
# field names. We pick the max across attack/spell variants — they always
# match for tanks in practice.
_GEAR_STAT_KEYS = {
    "gear_strength": "strength",
    "gear_agility": "agility",
    "gear_stamina": "stamina",
    "gear_armor": "armor_from_gear",
    "gear_haste_rating": "haste_rating",
    "gear_crit_rating": "crit_rating",
    "gear_mastery_rating": "mastery_rating",
    "gear_versatility_rating": "versatility_rating",
    "gear_parry_rating": "parry_rating",
}


def parse_simc_string(text: str) -> SimcImport:
    result = SimcImport()
    pending_loadout_name: str | None = None
    last_item_comment: str | None = None
    in_bags_section = False
    in_vault_section = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            comment = stripped.lstrip("#").strip()

            # Section header: "Gear from Bags" (and similar)
            if comment.startswith("Gear from Bag"):
                in_bags_section = True
                in_vault_section = False
                continue

            # Weekly vault reward choices section
            if comment.startswith("Weekly Reward Choices"):
                in_vault_section = True
                in_bags_section = False
                continue

            if comment.startswith("End of Weekly Reward Choices"):
                in_vault_section = False
                continue

            # Saved Loadout marker
            if comment.startswith("Saved Loadout:"):
                pending_loadout_name = comment[len("Saved Loadout:") :].strip()
                continue

            # Commented-out talents inside a saved-loadout block
            if pending_loadout_name and comment.startswith("talents="):
                result.saved_loadouts[pending_loadout_name] = comment[len("talents=") :].strip()
                pending_loadout_name = None
                continue

            if "=" in comment:
                item_key, _, item_val = comment.partition("=")
                item_key = item_key.strip()
                if item_key in ITEM_SLOTS:
                    if in_vault_section:
                        item = parse_item_line(item_key, item_val, last_item_comment)
                        result.vault_items.setdefault(item_key, []).append(item)
                        last_item_comment = None
                        continue
                    elif in_bags_section:
                        item = parse_item_line(item_key, item_val, last_item_comment)
                        result.bag_items.setdefault(item_key, []).append(item)
                        last_item_comment = None
                        continue
                elif item_key == "upgrade_currencies":
                    _parse_upgrade_currencies(item_val, result)
                    continue
                elif item_key == "catalyst_currencies":
                    result.catalyst_currencies.update(_parse_currency_pairs(item_val))
                    continue
                elif item_key == "slot_high_watermarks":
                    result.slot_high_watermarks = _parse_watermarks(item_val)
                    continue

            # Item name + ilvl preceding an item or bag-item line
            if _ITEM_NAME_RE.match(comment):
                last_item_comment = comment
            continue

        # Non-comment lines reset section flags (sections only span comment blocks)
        in_bags_section = False
        in_vault_section = False

        if "=" not in stripped:
            continue

        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()

        normalized_key = _CLASS_ALIASES.get(key, key)
        if normalized_key in KNOWN_CLASSES:
            result.class_name = normalized_key
            result.name = value.strip('"')
        elif key == "level":
            result.level = int(value)
        elif key == "race":
            result.race = value
        elif key == "region":
            result.region = value
        elif key == "server":
            result.server = value
        elif key == "role":
            result.role = value
        elif key == "spec":
            result.spec = value
        elif key == "professions":
            result.professions = value
        elif key == "talents":
            result.talents = value
        elif key in ITEM_SLOTS:
            result.items[key] = parse_item_line(key, value, last_item_comment)
            last_item_comment = None
        elif key in _GEAR_STAT_KEYS:
            try:
                val = int(value)
            except ValueError:
                continue
            char_key = _GEAR_STAT_KEYS[key]
            result.gear_stats[char_key] = max(result.gear_stats.get(char_key, 0), val)

    return result


def parse_item_line(slot: str, raw: str, name_comment: str | None) -> ItemSpec:
    item = ItemSpec(slot=slot)
    raw = raw.lstrip(",")
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if k == "id":
            item.item_id = int(v)
        elif k == "enchant_id":
            item.enchant_id = int(v)
        elif k in ("gem_id", "gem_ids"):
            item.gem_ids = [int(x) for x in v.split("/") if x]
        elif k == "bonus_id":
            item.bonus_ids = [int(x) for x in v.split("/") if x]
        elif k == "crafted_stats":
            item.crafted_stats = [int(x) for x in v.split("/") if x]
        elif k == "crafting_quality":
            item.crafting_quality = int(v)

    if name_comment:
        m = _ITEM_NAME_RE.match(name_comment)
        if m:
            item.name = m.group(1).strip()
            item.ilvl = int(m.group(2))

    return item


def _parse_currency_pairs(raw: str) -> dict[int, int]:
    """Parse the ``catalyst_currencies`` form: ``id:amount/id:amount/...``.

    Skips malformed entries rather than raising — the export is pasted by
    hand and addon versions vary.
    """
    out: dict[int, int] = {}
    for entry in raw.split("/"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 2:
            continue
        try:
            out[int(parts[0])] = int(parts[1])
        except ValueError:
            continue
    return out


def _parse_upgrade_currencies(raw: str, result: "SimcImport") -> None:
    """Parse the ``upgrade_currencies`` form: ``<kind>:<id>:<amount>/...``.

    ``c:`` entries are spendable currencies (crests, valorstones) → stored
    in ``result.upgrade_currencies``. ``i:`` entries are upgrade *items*
    (Voidcore et al.) → stored in ``result.upgrade_items``. Malformed or
    unknown-kind entries are skipped.
    """
    for entry in raw.split("/"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            continue
        kind, cid, amt = parts
        try:
            cid_i, amt_i = int(cid), int(amt)
        except ValueError:
            continue
        if kind == "c":
            result.upgrade_currencies[cid_i] = amt_i
        elif kind == "i":
            result.upgrade_items[cid_i] = amt_i


def _parse_watermarks(raw: str) -> list[tuple[int, int, int]]:
    """Parse ``slot_high_watermarks``: ``slot:current:max/slot:current:max/...``."""
    out: list[tuple[int, int, int]] = []
    for entry in raw.split("/"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            continue
        try:
            out.append((int(parts[0]), int(parts[1]), int(parts[2])))
        except ValueError:
            continue
    return out


def map_race(simc_race: str) -> str:
    return SIMC_RACE_MAP.get(simc_race, "human")


def simc_to_character_yaml(
    sim: SimcImport,
    stats: dict | None = None,
    talents_loadout: str | None = None,
) -> dict:
    """Build a Character YAML dict from a parsed SimC import + supplied stats.

    When `talents_loadout` is None (the default), pick a sensible loadout
    per spec instead of hardcoding `kiratank-defensive` (a Warrior set) on
    every class. The fallback table is keyed by `class_spec`:

        protection_warrior        → brutoh-actual  (Phalanx + FbV)
        protection_paladin        → default-paladin
        blood_death_knight        → default-dk  (or empty if missing)
        vengeance_demon_hunter    → default-dh
        brewmaster_monk           → anonbrewmaster1-brewmaster
        guardian_druid            → anonguardian2-guardian

    engaged_tank Phase 4 review (2026-05-16): SimC paste previously
    slapped Warrior talents (Indomitable, Battle Scarred, etc) onto
    every spec, silently giving non-Warriors Warrior survivability
    talents they don't actually have.
    """
    race = map_race(sim.race)
    class_spec = (
        f"{sim.spec}_{sim.class_name}" if sim.spec and sim.class_name else "protection_warrior"
    )

    if talents_loadout is None:
        defaults = {
            "protection_warrior": "brutoh-actual",
            "protection_paladin": "default-paladin",
            "brewmaster_monk": "anonbrewmaster1-brewmaster",
            "guardian_druid": "anonguardian2-guardian",
            "blood_death_knight": "default-dk",
            "vengeance_demon_hunter": "default-dh",
        }
        talents_loadout = defaults.get(class_spec, "")

    char_yaml: dict = {
        "name": sim.name or "imported_character",
        "race": race,
        "class_spec": class_spec,
        "talents": talents_loadout,
    }
    # Carry region/server through so the Why-died surface can pre-fill the
    # target field as `Name-Server-Region` (the literal combat-log uses).
    # Without these, the field defaults to bare "Brutoh" which matches
    # zero events in any log (3.11 in ROADMAP).
    if sim.server:
        char_yaml["server"] = sim.server
    if sim.region:
        char_yaml["region"] = sim.region

    if stats:
        char_yaml.update(stats)
    else:
        char_yaml.update(
            {
                "strength": 0,
                "agility": 0,
                "stamina": 0,
                "armor_from_gear": 0,
                "haste_rating": 0,
                "crit_rating": 0,
                "mastery_rating": 0,
                "versatility_rating": 0,
                "max_hp_override": 0,
            }
        )

    # A SimC export's gear stamina is the form-independent (caster) value, so a
    # Guardian needs Bear Form's +40% HP applied in max_hp() — opt in here. The
    # log/WCL hydrate paths store in-form stamina and leave this False.
    if class_spec == "guardian_druid":
        char_yaml["stamina_in_caster_form"] = True

    # Decode the pasted talents= string into a real build (fixed 2026-08-31 —
    # see talent_string_codec.py's module docstring). A no-op for a
    # placeholder value like "ACTIVE_LOADOUT" (not a real base64 string,
    # fails the header check) or any string that doesn't decode cleanly.
    if sim.talents:
        decoded = decode_simc_talent_string(sim.talents)
        if decoded.ok and decoded.modeled:
            char_yaml["decoded_talents"] = decoded.modeled

        # Guardian Druid has no modeled-talent catalog (decode_simc_talent_string
        # only covers Protection Warrior) — its only talent-gated survivability
        # lever is the Ironfur haste model, gated on the Elune's Chosen hero
        # tree via a replay-detected buff id (character.py:
        # _guardian_ironfur_avg_stacks). A SimC paste carries no buff signal,
        # so mirror what the buff WOULD be if this were a hydrated log —
        # closing the gap named in docs/validation/
        # phase4_guardian_haste_model_2026_06_24.md's "still pending" note.
        if class_spec == "guardian_druid":
            hero_tree = decode_hero_tree_name(sim.talents, _GUARDIAN_DRUID_SPEC_ID)
            haste_model = (
                load_constants()
                .get("specs", {})
                .get("guardian_druid", {})
                .get("ironfur_haste_model")
            )
            gate_buff_id = (haste_model or {}).get("detect_buff_spell_id")
            if hero_tree == "Elune's Chosen" and gate_buff_id:
                char_yaml["active_buff_spell_ids"] = frozenset({int(gate_buff_id)})

    return char_yaml


def load_simc_file(path: str | Path) -> SimcImport:
    return parse_simc_string(Path(path).read_text())
