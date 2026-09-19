"""Track-aware vault verdict — fixture ground truth + AppTest surfaces.

The bundled demo fixture (src/simf/data/characters/brutoh-vault-2026-06-10.simc,
ratified 2026-06-10) is the ground truth for dead-pick detection: Skeleton Lord's
Cranium is owned equipped at 289 (= armor ceiling) and Scepter of the
Endless Night at 298 (= weapon ceiling), so both 272 offers are dead;
Heart of Wind (250256) is not owned anywhere and must be the headline pick.

AppTests follow the session-seeding template from test_upgrade_compare_app
(stats pre-seeded into ``_item_stats_cache`` keyed ``(item_id, sorted
bonus_ids, sorted crafted_stats)`` so nothing hits the network); the
demo-button flow follows test_app_real_flows and is network-free via the
committed item-cache seed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec, parse_simc_string

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"
# The demo's gear now ships under the package data dir (so it's in the wheel /
# public container), not examples/.
FIXTURE = (
    Path(__file__).parent.parent
    / "src"
    / "simf"
    / "data"
    / "characters"
    / "brutoh-vault-2026-06-10.simc"
)


# ─── fixture-driven unit test (no streamlit) ──────────────────────────────────


def test_fixture_dead_picks_classified_from_real_export():
    """Parse the ratified fixture and classify its three offers with the
    same ceiling expression the vault panel uses."""
    from simf.core.constants import load_constants
    from simf.optimizer.vault_ranking import rank_vault_items
    from simf.ui.helpers.upgrade_compare import category_ceiling

    imp = parse_simc_string(FIXTURE.read_text())
    assert imp.account_ilvl_ceiling == 298  # fixture watermark ground truth
    ceilings_cfg = load_constants().get("gear", {}).get("ilvl_ceiling_by_category")
    assert ceilings_cfg, "gear.ilvl_ceiling_by_category missing from constants"

    vault = [it for items in imp.vault_items.values() for it in items]
    assert {it.item_id for it in vault} == {250256, 49819, 258525}

    marginals = {"stamina": {"p": 30.0, "m": 22.0}}
    dungeons = [{"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.9}}]
    rows = rank_vault_items(
        vault_items=vault,
        equipped=imp.items,
        marginals=marginals,
        dungeons=dungeons,
        # Stats are irrelevant to ownership classification — keep it offline.
        item_stats_fn=lambda spec: {"stamina": 100},
        bag=imp.bag_items,
        ceiling_fn=lambda slot: category_ceiling(
            slot, ceilings_cfg, account_ceiling=imp.account_ilvl_ceiling
        ),
    )
    by_id = {r.item.item_id: r for r in rows}

    cranium = by_id[49819]  # owned equipped at 289 = armor ceiling → dead
    assert cranium.dead is True
    assert cranium.owned_max_ilvl == 289
    assert cranium.offer_ceiling == 289

    scepter = by_id[258525]  # owned equipped at 298 = weapon ceiling → dead
    assert scepter.dead is True
    assert scepter.owned_max_ilvl == 298
    assert scepter.offer_ceiling == 298

    heart = by_id[250256]  # not owned anywhere → live, no callout
    assert heart.dead is False
    assert heart.ceiling_upgrade is False
    assert heart.owned_max_ilvl is None
    assert heart.offer_ceiling == 298

    # Dead rows are demoted: the live Heart of Wind leads the list.
    assert rows[0].item.item_id == 250256


# ─── AppTest surfaces ─────────────────────────────────────────────────────────


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=120)


def _char_state() -> dict:
    return {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }


def test_demo_vault_grid_demotes_dead_picks(app):
    """Demo flow on the real fixture: two dead badges, dead cells carry the
    demotion class, and the verdict card carries the dead-count sentence.

    The one live offer (Heart of Wind) is still a net loss vs equipped gear
    at this ilvl (-1,191 eHP) — so per the 2026-07-04 fix it must NOT carry
    the winner star even though it's the "best of the losers": starring a
    negative-delta pick directly under a "No vault upgrade this week."
    headline told the user two contradictory things in the same glance
    (ui-craft-critic review, 2026-07-04)."""
    app.run()
    demo_btn = next((b for b in app.button if "sample build" in (b.label or "").lower()), None)
    assert demo_btn is not None, "Demo button not found on first paint"
    demo_btn.click().run()
    assert not app.exception, f"Exception during demo load: {app.exception}"

    captions = "\n".join(str(c.value) for c in app.caption)
    # "eHP" is the load-bearing term in every verdict sentence on this tab
    # (and the Gear tab) but was never defined in plain English anywhere a
    # first-time visitor would see it before reading it (round-1 copy audit,
    # 2026-07-05) — glossed once on the scope subtitle, the earliest caption
    # on the tab that loads first whenever a vault exists.
    assert "eHP = effective HP" in captions
    assert captions.count("Dead pick.") == 2
    assert "You own this at i289 — already at this offer's ceiling. Dead pick." in captions
    assert "You own this at i298 — already at this offer's ceiling. Dead pick." in captions
    # Heart of Wind isn't owned — its cell must NOT carry an ownership badge.
    assert "Ceiling upgrade:" not in captions
    # Paired-slot baseline: the trinket offer names the equipped piece the
    # most favorable swap replaces (PR #131 voice).
    assert "↳ replaces your Trinket" in captions
    assert "· i298" in captions

    body = "\n".join(str(m.value) for m in app.markdown)
    # Both dead cells demoted visually.
    assert body.count('class="vault-cell vault-cell-dead"') == 2
    # No cell — dead or live — carries the winner star this week: every
    # offer nets negative, so the headline reads "No vault upgrade this
    # week." and nothing may contradict it. (Match the class attribute, not
    # the bare token — the page stylesheet also names these classes.)
    assert "vault-cell vault-cell-winner" not in body
    assert "No vault upgrade this week." in body
    # Dead offers are excluded from the headline: neither dead item may be
    # the "Take X." verdict.
    assert "Take Skeleton Lord's Cranium." not in body
    assert "Take Scepter of the Endless Night." not in body
    assert "Take Heart of Wind." not in body
    # The verdict card names how many offers were dead.
    assert "2 of 3 offers duplicate items you already own at their ceiling." in body


def test_ehp_gloss_caption_is_spec_aware_for_non_warrior_spec(app):
    """Regression test (2026-07-09): the Vault tab's eHP-gloss caption used
    to embed a hardcoded module-level `EHP_GLOSS` string that named
    Protection Warrior's own always-on passive ("Defensive Stance") and
    excluded cooldowns ("Shield Block/Shield Wall") no matter which spec's
    vault this rendered on. A Guardian Druid character must see Guardian's
    own examples instead, never the Warrior wording."""
    char_state = _char_state()
    char_state["class_spec"] = "guardian_druid"
    char_state["talents"] = "anonguardian2-guardian"
    char_state["agility"] = 2000
    app.session_state["char_data"] = char_state
    owned = ItemSpec(slot="head", item_id=42, name="OwnedHelm", ilvl=289, bonus_ids=[1])
    offer = ItemSpec(slot="head", item_id=42, name="OwnedHelm", ilvl=272, bonus_ids=[2])
    app.session_state["simc_equipped"] = {"head": owned}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [offer]}
    app.session_state["simc_account_ilvl_ceiling"] = 289
    app.session_state["_item_stats_cache"] = {
        (42, (1,), (), "guardian_druid"): {"stamina": 1000, "armor_from_gear": 300},
        (42, (2,), (), "guardian_druid"): {"stamina": 900, "armor_from_gear": 270},
    }
    app.run()
    assert not app.exception, f"render error: {app.exception}"

    captions = "\n".join(str(c.value) for c in app.caption)
    assert "eHP = effective HP" in captions
    assert "Thick Hide" in captions
    assert "Ironfur" in captions
    assert "Defensive Stance" not in captions
    assert "Shield Block" not in captions
    assert "Shield Wall" not in captions


def test_vault_panel_passes_class_spec_to_ehp_gloss_text(app, monkeypatch):
    """Confirms the actual wiring: `vault_panel._render_vault_panel` must
    call the shared `ehp_gloss_core_text` with THIS character's own
    `class_spec`, not a spec-blind constant. Spies on the real function
    (still delegates to it) so the caption itself stays correct too.

    Updated 2026-07-26 (mobile round 2): the eager caption now calls
    `ehp_gloss_core_text` instead of the full `ehp_gloss_text` — the
    exclusion caveat moved into a popover (see the sibling assertion
    below, which spies on that call too)."""
    import simf.ui.vault_panel as vault_panel

    real_core_text = vault_panel.ehp_gloss_core_text
    real_exclusion_text = vault_panel.ehp_gloss_exclusion_text
    seen_core_specs: list[str | None] = []
    seen_exclusion_specs: list[str | None] = []

    def _core_spy(class_spec: str | None = None) -> str:
        seen_core_specs.append(class_spec)
        return real_core_text(class_spec)

    def _exclusion_spy(class_spec: str | None = None) -> str:
        seen_exclusion_specs.append(class_spec)
        return real_exclusion_text(class_spec)

    monkeypatch.setattr(vault_panel, "ehp_gloss_core_text", _core_spy)
    monkeypatch.setattr(vault_panel, "ehp_gloss_exclusion_text", _exclusion_spy)

    app.run()
    demo_btn = next((b for b in app.button if "sample build" in (b.label or "").lower()), None)
    assert demo_btn is not None, "Demo button not found on first paint"
    demo_btn.click().run()
    assert not app.exception, f"Exception during demo load: {app.exception}"

    assert "protection_warrior" in seen_core_specs
    assert "protection_warrior" in seen_exclusion_specs


def test_vault_eager_caption_is_short_and_exclusion_caveat_sits_behind_a_popover(app):
    """Regression test for the mobile round-2 caption split (2026-07-26):
    the eager caption must carry the short core definition (not the full
    ~220-char combined text that used to cost 179px at 390px) and the
    "what eHP leaves out" caveat must render behind a popover trigger,
    not inline — both discoverable from the same AppTest run, not just
    claimed in a comment."""
    app.run()
    demo_btn = next((b for b in app.button if "sample build" in (b.label or "").lower()), None)
    assert demo_btn is not None, "Demo button not found on first paint"
    demo_btn.click().run()
    assert not app.exception, f"Exception during demo load: {app.exception}"

    captions = [c.value for c in app.caption]
    eager = next(c for c in captions if c.startswith("Scores this week's 3 offers"))
    # The eager caption stays short — no longer carries the exclusion
    # caveat's own wording inline.
    assert "excludes" not in eager
    assert "Shield Block/Shield Wall" not in eager
    assert "eHP = effective HP" in eager

    popover_labels = [p.proto.popover.label for p in app.get("popover")]
    assert any("What eHP leaves out" in label for label in popover_labels)

    # The exclusion text itself still renders (inside the popover body,
    # collapsed by default) — AppTest flattens captions across containers
    # (confirmed by the pre-existing `test_render_order_ehp_gloss_is_the_
    # first_caption` pattern in test_stat_price_sheet.py), so it must still
    # be discoverable in the flattened caption list even though a user
    # wouldn't see it without clicking.
    assert any("Shield Block/Shield Wall" in c for c in captions)
    assert any(c.startswith("It excludes per-hit dodge/parry/block rolls") for c in captions)


def test_all_dead_week_keeps_no_upgrade_headline_with_dead_count(app):
    """Every offer dead → no winner star, the 'No vault upgrade this week.'
    headline stays, and the dead-count sentence explains why."""
    app.session_state["char_data"] = _char_state()
    owned = ItemSpec(slot="head", item_id=42, name="OwnedHelm", ilvl=289, bonus_ids=[1])
    offer = ItemSpec(slot="head", item_id=42, name="OwnedHelm", ilvl=272, bonus_ids=[2])
    app.session_state["simc_equipped"] = {"head": owned}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [offer]}
    app.session_state["simc_account_ilvl_ceiling"] = 289
    app.session_state["_item_stats_cache"] = {
        (42, (1,), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 300},
        (42, (2,), (), "protection_warrior"): {"stamina": 900, "armor_from_gear": 270},
    }
    app.run()
    assert not app.exception, f"render error: {app.exception}"

    body = "\n".join(str(m.value) for m in app.markdown)
    assert "No vault upgrade this week." in body
    assert "1 of 1 offers duplicates an item you already own at its ceiling." in body
    # No live pick → nothing may wear the winner star. (Match the class
    # attribute — the page stylesheet also names this class.)
    assert "vault-cell vault-cell-winner" not in body
    assert 'class="vault-cell vault-cell-dead"' in body
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "Dead pick." in captions
    # Live-UI review, 2026-07-18: "no upgrade this week" is GOOD news (every
    # offer duplicates gear you already hold) — must NOT wear the bronze
    # `verdict-warn` bar, which reads as a problem/error on this trust-first
    # surface. `verdict-neutral` is the dedicated third state.
    assert 'class="verdict-neutral"' in body
    assert 'class="verdict-warn"' not in body


def test_gear_already_beats_every_vault_offer_uses_neutral_not_warn(app):
    """The OTHER 'No vault upgrade this week' path (a real, non-dead offer
    exists but the player's equipped gear beats it on average ΔeHP) must
    also render `verdict-neutral`, not `verdict-warn` — same reasoning as
    the all-dead-week case above, different code path in
    `_render_verdict_card`."""
    app.session_state["char_data"] = _char_state()
    owned = ItemSpec(slot="head", item_id=42, name="OwnedHelm", ilvl=289, bonus_ids=[1])
    offer = ItemSpec(slot="head", item_id=99, name="WorseHelm", ilvl=272, bonus_ids=[2])
    app.session_state["simc_equipped"] = {"head": owned}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [offer]}
    app.session_state["simc_account_ilvl_ceiling"] = 289
    app.session_state["_item_stats_cache"] = {
        (42, (1,), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 300},
        (99, (2,), (), "protection_warrior"): {"stamina": 500, "armor_from_gear": 150},
    }
    app.run()
    assert not app.exception, f"render error: {app.exception}"

    body = "\n".join(str(m.value) for m in app.markdown)
    assert "No vault upgrade this week." in body
    assert 'class="verdict-neutral"' in body
    assert 'class="verdict-warn"' not in body


def test_ceiling_upgrade_callout_for_owned_lower_copy(app):
    """Owned copy below the offer's reachable ceiling → the cell states
    what is known: 'you own this at iX — this offer reaches iY.'"""
    app.session_state["char_data"] = _char_state()
    equipped_trink = ItemSpec(
        slot="trinket1", item_id=900, name="WornTrink", ilvl=298, bonus_ids=[1]
    )
    owned_copy = ItemSpec(slot="trinket1", item_id=950, name="HeartCopy", ilvl=276, bonus_ids=[3])
    offer = ItemSpec(slot="trinket1", item_id=950, name="HeartCopy", ilvl=272, bonus_ids=[2])
    app.session_state["simc_equipped"] = {"trinket1": equipped_trink}
    app.session_state["simc_bag_items"] = {"trinket1": [owned_copy]}
    app.session_state["simc_vault_items"] = {"trinket1": [offer]}
    app.session_state["simc_account_ilvl_ceiling"] = 298
    app.session_state["_item_stats_cache"] = {
        (900, (1,), (), "protection_warrior"): {"stamina": 1000, "versatility_rating": 200},
        (950, (2,), (), "protection_warrior"): {"stamina": 950, "versatility_rating": 190},
        (950, (3,), (), "protection_warrior"): {"stamina": 970, "versatility_rating": 194},
    }
    app.run()
    assert not app.exception, f"render error: {app.exception}"

    captions = "\n".join(str(c.value) for c in app.caption)
    assert "Ceiling upgrade: you own this at i276 — this offer reaches i298." in captions
    assert "Dead pick." not in captions


def _render_vault_cell_script(delta_dps: float) -> None:
    from simf.io.simc_import import ItemSpec
    from simf.optimizer.vault_ranking import VaultRow
    from simf.ui.vault_panel import _render_vault_cell

    item = ItemSpec(slot="head", item_id=42, name="TestHelm", ilvl=289)
    row = VaultRow(
        item=item,
        slot="head",
        per_dungeon=[],
        avg_delta_ehp=1234.0,
        verdict_sentence="",
        delta_dps=delta_dps,
    )
    _render_vault_cell(row, is_winner=False, key_suffix="test")


def test_vault_card_suppresses_zero_dps_noise():
    """Regression test (2026-07-16): a vault card whose ΔDPS rounds to ~0
    must render NO 'ΔDPS' text and NO '≈0' text at all — the old code
    hardcoded a '· ΔDPS ≈0' clause on every such card, which is noise +
    fake precision on a survivability tool (the same rule recommend.py's
    per-row/headline chips already followed). This must fail against the
    pre-fix behaviour that always appended a ΔDPS clause."""
    at = AppTest.from_function(_render_vault_cell_script, kwargs={"delta_dps": 0.04})
    at.run()
    assert not at.exception, f"render error: {at.exception}"
    body = "\n".join(str(m.value) for m in at.markdown)
    assert "ΔDPS" not in body
    assert "≈0" not in body


def test_vault_card_shows_real_dps_delta_above_threshold():
    """A card with a meaningful ΔDPS (>= 0.1) still shows it, sign-prefixed
    and formatted like recommend.py's matching chip — only the noisy ≈0
    case is suppressed, not the whole clause."""
    at = AppTest.from_function(_render_vault_cell_script, kwargs={"delta_dps": 5.5})
    at.run()
    assert not at.exception, f"render error: {at.exception}"
    body = "\n".join(str(m.value) for m in at.markdown)
    assert "ΔDPS +5.50" in body
    assert "≈0" not in body


def test_paired_offer_replaces_weaker_trinket_and_try_swaps_that_slot(app):
    """A trinket1 offer that wins against the weaker trinket2 incumbent must
    say so — and its Try button must trial-swap into trinket2, not trinket1,
    so the label and the action agree."""
    app.session_state["char_data"] = _char_state()
    strong = ItemSpec(slot="trinket1", item_id=10, name="StrongTrink", ilvl=298, bonus_ids=[1])
    weak = ItemSpec(slot="trinket2", item_id=11, name="WeakTrink", ilvl=280, bonus_ids=[1])
    offer = ItemSpec(slot="trinket1", item_id=20, name="NewTrink", ilvl=272, bonus_ids=[2])
    app.session_state["simc_equipped"] = {"trinket1": strong, "trinket2": weak}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"trinket1": [offer]}
    app.session_state["simc_account_ilvl_ceiling"] = 298
    app.session_state["_item_stats_cache"] = {
        (10, (1,), (), "protection_warrior"): {"stamina": 1200, "versatility_rating": 300},
        (11, (1,), (), "protection_warrior"): {"stamina": 500, "versatility_rating": 100},
        (20, (2,), (), "protection_warrior"): {"stamina": 1000, "versatility_rating": 250},
    }
    app.run()
    assert not app.exception, f"render error: {app.exception}"

    captions = "\n".join(str(c.value) for c in app.caption)
    assert "↳ replaces your Trinket 2: WeakTrink · i280" in captions

    try_btn = next(
        (b for b in app.button if (b.key or "").startswith("vault_try_")),
        None,
    )
    assert try_btn is not None, "vault Try button not found"
    try_btn.click().run()
    assert not app.exception, f"trial swap error: {app.exception}"
    assert "_trial_swaps" in app.session_state
    swaps = app.session_state["_trial_swaps"]
    assert "trinket2" in swaps, f"trial landed in the wrong slot: {list(swaps)}"
    assert getattr(swaps["trinket2"], "item_id", None) == 20
