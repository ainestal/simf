from pathlib import Path

from simf.io.simc_import import load_simc_file, map_race, parse_simc_string, simc_to_character_yaml

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples"
# The demo character's gear now ships under the package data dir (so it's in the
# wheel / public container), not examples/.
DEMO_GEAR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "simf"
    / "data"
    / "characters"
    / "brutoh-vault-2026-06-10.simc"
)


def test_parse_basic_fields():
    text = """warrior="Brutoh"
level=90
race=earthen_dwarf
region=eu
server=uldum
role=tank
spec=protection
talents=ABC123
"""
    sim = parse_simc_string(text)
    assert sim.name == "Brutoh"
    assert sim.class_name == "warrior"
    assert sim.level == 90
    assert sim.race == "earthen_dwarf"
    assert sim.spec == "protection"
    assert sim.role == "tank"
    assert sim.region == "eu"
    assert sim.server == "uldum"
    assert sim.talents == "ABC123"


def test_parse_saved_loadouts():
    text = """warrior="Test"
talents=ACTIVE_LOADOUT

# Saved Loadout: raid
# talents=RAID_LOADOUT
# Saved Loadout: Kirawarrior-m+
# talents=KIRA_LOADOUT
"""
    sim = parse_simc_string(text)
    assert sim.talents == "ACTIVE_LOADOUT"
    assert sim.saved_loadouts == {"raid": "RAID_LOADOUT", "Kirawarrior-m+": "KIRA_LOADOUT"}


def test_parse_item_with_name_comment():
    text = """warrior="Test"
# Crown of the Dark Envoy (289)
head=,id=151333,enchant_id=7961,bonus_id=13440/6652/12667
"""
    sim = parse_simc_string(text)
    item = sim.items["head"]
    assert item.item_id == 151333
    assert item.enchant_id == 7961
    assert item.bonus_ids == [13440, 6652, 12667]
    assert item.name == "Crown of the Dark Envoy"
    assert item.ilvl == 289


def test_parse_item_with_gem_and_crafted_stats():
    text = """warrior="Test"
# Spellbreaker's Bracers (285)
wrist=,id=237834,bonus_id=12214/12497,crafted_stats=32/36,crafting_quality=5
"""
    sim = parse_simc_string(text)
    item = sim.items["wrist"]
    assert item.item_id == 237834
    assert item.bonus_ids == [12214, 12497]
    assert item.crafted_stats == [32, 36]
    assert item.crafting_quality == 5
    assert item.ilvl == 285


def test_parse_class_alias_demonhunter():
    """SimC addon emits `demonhunter=` (no underscore) for the class line."""
    text = """demonhunter="Lyney"
level=90
race=blood_elf
spec=vengeance
"""
    sim = parse_simc_string(text)
    assert sim.name == "Lyney"
    assert sim.class_name == "demon_hunter"
    assert sim.spec == "vengeance"


def test_parse_class_alias_deathknight():
    """SimC addon emits `deathknight=` (no underscore) for the class line."""
    text = """deathknight="Bonefiddler"
level=90
race=dwarf
spec=blood
"""
    sim = parse_simc_string(text)
    assert sim.name == "Bonefiddler"
    assert sim.class_name == "death_knight"


def test_race_mapping_earthen_variants():
    assert map_race("earthen_dwarf") == "earthen"
    assert map_race("earthen") == "earthen"
    assert map_race("earthen_council") == "earthen"
    assert map_race("tauren") == "tauren"
    assert map_race("unknown_race") == "human"


def test_parse_full_brutoh_fixture():
    """End-to-end: parse the real exported /simc string and verify key fields."""
    sim = load_simc_file(EXAMPLE_DIR / "brutoh.simc")
    assert sim.name == "Brutoh"
    assert sim.level == 90
    assert sim.race == "earthen_dwarf"
    assert sim.class_name == "warrior"
    assert sim.spec == "protection"
    assert sim.role == "tank"

    # Active talent loadout (not the saved ones)
    assert sim.talents.startswith("CkEAjLzRlq54bI5v+r8Sr9Xw")
    assert "Kirawarrior-m+" in sim.saved_loadouts

    # Spot-check parsed gear
    assert sim.items["head"].item_id == 151333
    assert sim.items["head"].name == "Crown of the Dark Envoy"
    assert sim.items["head"].ilvl == 289
    assert sim.items["trinket1"].name == "Solarflare Prism"
    assert sim.items["off_hand"].crafted_stats == [32, 40]


# ---------------------------------------------------------------------------
# Upgrade economy: crest/valorstone balances + per-slot ilvl watermarks parsed
# from the "### Additional Character Info" block of a real export. Powers the
# upgrade-normalized vault comparison (reachable ceilings + crest costs).
# ---------------------------------------------------------------------------


def test_parse_upgrade_currencies_and_items():
    sim = load_simc_file(EXAMPLE_DIR / "brutoh-288-vault.simc")
    # c:<id>:<amt> entries → spendable currencies
    assert sim.upgrade_currencies[3347] == 11
    assert sim.upgrade_currencies[3383] == 84
    assert sim.upgrade_currencies[3341] == 50
    assert sim.upgrade_currencies[1792] == 2789
    # i:<id>:<amt> entries → upgrade items (Voidcore et al.), not currencies
    assert 3347 not in sim.upgrade_items
    assert sim.upgrade_items[256608] == 9
    assert sim.upgrade_items[228338] == 1


def test_parse_catalyst_currencies():
    sim = load_simc_file(EXAMPLE_DIR / "brutoh-288-vault.simc")
    # 3269:8/3378:5/2813:8/3116:8  (id:amount form, no c:/i: prefix)
    assert sim.catalyst_currencies[3269] == 8
    assert sim.catalyst_currencies[3378] == 5
    assert sim.catalyst_currencies[3116] == 8


def test_parse_slot_high_watermarks_and_ceiling():
    sim = load_simc_file(EXAMPLE_DIR / "brutoh-288-vault.simc")
    # indices 0..16 → 17 entries
    assert len(sim.slot_high_watermarks) == 17
    # (slot_index, current, max) triples preserved verbatim
    assert (2, 276, 289) in sim.slot_high_watermarks
    assert (16, 295, 295) in sim.slot_high_watermarks
    # reachable ceiling = highest max field across slots (trinkets/weapon 298)
    assert sim.account_ilvl_ceiling == 298


def test_account_ceiling_none_without_watermarks():
    """A plain export with no watermark line → None, not a crash."""
    sim = parse_simc_string('warrior="X"\nlevel=90\nspec=protection\n')
    assert sim.slot_high_watermarks == []
    assert sim.account_ilvl_ceiling is None


def test_parse_vault_choices_from_288_fixture():
    sim = load_simc_file(EXAMPLE_DIR / "brutoh-288-vault.simc")
    vault_ids = {it.item_id for items in sim.vault_items.values() for it in items}
    assert 249653 in vault_ids  # Rampant Brambleplate
    # ilvl captured from the "(259)" name comment, well below equipped chest 289
    chest_vault = sim.vault_items["chest"][0]
    assert chest_vault.ilvl == 259
    assert sim.items["chest"].ilvl == 289


# ---------------------------------------------------------------------------
# 2026-06-10 vault-week fixtures. Two paired exports:
#   brutoh-vault-2026-06-10.simc — SYNTHETIC pre-pickup reconstruction: the
#     ilvl-298 Mark of Light still equipped in trinket2, Heart of Wind (272)
#     offered as the week's vault choice. This is the bundled-demo file
#     brutoh.yaml points at — it encodes the real Tuesday question ("claim
#     the 272 haste stat stick over an equipped 298?").
#   brutoh-2026-06-10.simc — the real post-pickup addon export (ground
#     truth): Heart of Wind claimed + equipped, Mark of Light in the bag.
# ---------------------------------------------------------------------------

MARK_OF_LIGHT_ID = 250241
HEART_OF_WIND_ID = 250256


def test_vault_2026_06_10_synthetic_equipped_trinket2_is_mark_of_light():
    sim = load_simc_file(DEMO_GEAR)
    t2 = sim.items["trinket2"]
    assert t2.item_id == MARK_OF_LIGHT_ID
    # Exact bonus string from the 2026-05-29 export (brutoh-288-vault.simc)
    # the synthetic reverted to.
    assert t2.bonus_ids == [13440, 6652, 12699, 13654]
    assert t2.name == "Mark of Light"
    assert t2.ilvl == 298
    # Identity sanity for the demo character
    assert sim.region == "eu"
    assert sim.server == "uldum"


def test_vault_2026_06_10_synthetic_offers_heart_of_wind_plus_dead_duplicates():
    sim = load_simc_file(DEMO_GEAR)
    flat = [(slot, it) for slot, items in sim.vault_items.items() for it in items]
    # 3 offers per Brutoh's account: the claimed Heart of Wind plus two dead
    # duplicates of items he owned at mythic (representative byte-real lines
    # from earlier exports — see the file's provenance header).
    assert len(flat) == 3
    by_id = {it.item_id: (slot, it) for slot, it in flat}
    assert set(by_id) == {250256, 49819, 258525}
    # Dead duplicates sit at myth rank 1 (272) while he owns them higher.
    assert by_id[49819][1].ilvl == 272 and by_id[49819][0] == "head"
    assert by_id[258525][1].ilvl == 272 and by_id[258525][0] == "main_hand"
    slot, how = by_id[250256]
    assert slot.startswith("trinket")
    assert how.item_id == HEART_OF_WIND_ID
    assert how.name == "Heart of Wind"
    assert how.ilvl == 272
    assert 12801 in how.bonus_ids  # myth-track rank-1 (272) id, verbatim from the claimed line


def test_vault_2026_06_10_synthetic_mark_of_light_not_in_bags():
    """Pre-pickup, Mark of Light was *equipped* — listing it in the bag too
    would let the trial-swap UI offer a duplicate of the worn trinket."""
    sim = load_simc_file(DEMO_GEAR)
    bag_ids = {it.item_id for items in sim.bag_items.values() for it in items}
    assert MARK_OF_LIGHT_ID not in bag_ids


def test_vault_2026_06_10_real_export_post_pickup_state():
    """Ground-truth export taken after the claim: Heart of Wind worn,
    Mark of Light displaced to the bag, no vault block left."""
    sim = load_simc_file(EXAMPLE_DIR / "brutoh-2026-06-10.simc")
    assert sim.items["trinket2"].item_id == HEART_OF_WIND_ID
    assert sim.items["trinket2"].ilvl == 272
    bag_ids = {it.item_id for items in sim.bag_items.values() for it in items}
    assert MARK_OF_LIGHT_ID in bag_ids
    assert sim.vault_items == {}


def test_demo_character_simc_path_contract():
    """`_load_demo_character` (ui/app.py) resolves brutoh.yaml's `simc_path`
    under the packaged data dir (so the demo's gear ships in the wheel /
    public container), repo-root as a dev fallback, and parses it to populate
    equipped/bag/vault state. Keep that contract honest at the parse level: the
    file exists, parses, and carries at least one vault choice (the demo's whole
    point), which differs from the trinket currently worn in the same slot."""
    import yaml

    from simf.core.constants import DATA_DIR

    repo_root = Path(__file__).resolve().parent.parent
    yaml_path = DATA_DIR / "characters" / "brutoh.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    simc_path = data.get("simc_path")
    assert simc_path, "brutoh.yaml must carry a simc_path for the bundled demo"
    # Resolve the SAME way _load_demo_character does: packaged data dir first,
    # repo root fallback. It must ship in the package, not examples/.
    full = next(
        (c for c in (DATA_DIR / simc_path, repo_root / simc_path) if c.exists()),
        DATA_DIR / simc_path,
    )
    assert full.exists(), f"simc_path points at a missing file: {simc_path}"

    sim = load_simc_file(full)
    assert sim.name == "Brutoh"
    vault_flat = [it for items in sim.vault_items.values() for it in items]
    assert vault_flat, "demo simc file must offer at least one vault item"
    # The demo encodes a real decision: the offered trinket must not be the
    # one already equipped (otherwise the comparison is a no-op).
    equipped_trinkets = {
        sim.items[slot].item_id for slot in ("trinket1", "trinket2") if slot in sim.items
    }
    for it in vault_flat:
        assert it.item_id not in equipped_trinkets


# AnonGuardian1's real Guardian Druid talents= string (simf's Guardian calibration
# reference profile) — self-contained here rather than reading it off an
# example file, so this test doesn't depend on any untracked fixture being
# present in the repo. Decodes to the Elune's Chosen hero tree — confirmed
# directly (test_talent_decoder.py), not assumed from prior docs.
_ANONGUARDIAN1_ELUNES_CHOSEN_TALENTS = (
    "CgGADBD3hSPCL9Y9gz68WcKvMAAAAAAAAAAAAgZmZmFzMjZWmZxMPwMLLDMbGGNR"
    "mZWGzMzsMm5BAAAAAAYsZGYZbmBjZZAMFAAAYzYmBYxYYgZxCAzMAA"
)


def test_simc_import_guardian_elunes_chosen_sets_ironfur_haste_gate_buff():
    """A Guardian Druid SimC paste with an Elune's Chosen talents= string
    must set active_buff_spell_ids to the Ironfur haste model's gate buff
    (Fury of Elune, 202770) — the same signal the WCL log-hydrate path
    already sets from a real COMBATANT_INFO buff observation. Closes the
    "SimC-paste path... discards the talent hash... deferred" gap named in
    docs/validation/phase4_guardian_haste_model_2026_06_24.md."""
    text = f"""druid="AnonGuardian1"
level=90
race=tauren
region=eu
server=anonrealm1
role=tank
spec=guardian
talents={_ANONGUARDIAN1_ELUNES_CHOSEN_TALENTS}
"""
    sim = parse_simc_string(text)
    yaml_dict = simc_to_character_yaml(sim)
    assert yaml_dict["class_spec"] == "guardian_druid"
    assert yaml_dict["active_buff_spell_ids"] == frozenset({202770})


def test_simc_import_guardian_no_talents_leaves_buff_gate_unset():
    text = """druid="NoTalentsYet"
level=10
region=eu
server=anonrealm1
role=tank
spec=guardian
"""
    sim = parse_simc_string(text)
    yaml_dict = simc_to_character_yaml(sim)
    assert yaml_dict.get("active_buff_spell_ids") is None
