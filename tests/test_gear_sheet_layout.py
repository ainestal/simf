"""Layout-regression tests for the card-paperdoll gear surface.

2026-06-20 redesign: the gear tab became the universal WoW / Raidbots /
Blizzard character-sheet — a grid of gear CARDS (quality-coloured name
leading, "Slot · Track · iLvl" subtitle, an inline steel "↑ +X eHP" on slots
with a better option), two columns with the right column mirrored, weapons
centred. DISPLAY ONLY: no buttons on the sheet (the reference tools have
none); browse / trial-swap lives off the sheet (a slot picker below + the
upgrade panel). The earlier ghost-button / center-gutter version was rejected
by the user ("buttons look bad, no paperdoll — follow raidbots/blizzard").

`_slot_card_html` is a pure string builder, so most of this is fast unit
tests; a couple of AppTest checks pin the rendered sheet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec
from simf.optimizer.tier_sets import SetStatus, TierSet
from simf.ui.app import (
    _PAPERDOLL_LEFT,
    _PAPERDOLL_RIGHT,
    _card_for,
    _slot_card_html,
    _SlotPick,
)
from simf.ui.helpers.enchant_panel import EnchantRow
from simf.ui.helpers.gear_list import SlotRow
from simf.ui.helpers.gem_panel import GemRow
from simf.ui.item_html import _humanize_source_label

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"
APP_SOURCE = APP_PATH.read_text()
# `_render_paperdoll_grid` moved into `simf.ui.recommend` (L3 render-panel
# split); `_render_gear_list` + `_render_slot_browse_control` moved into
# `simf.ui.gear_surface` (L4); the gear-card CSS stays in app.py.
RECOMMEND_SOURCE = (
    Path(__file__).parent.parent / "src" / "simf" / "ui" / "recommend.py"
).read_text()
GEAR_SURFACE_SOURCE = (
    Path(__file__).parent.parent / "src" / "simf" / "ui" / "gear_surface.py"
).read_text()


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _row(slot="head", item_id=100, name="Test Helm", ilvl=289) -> SlotRow:
    item = ItemSpec(slot=slot, item_id=item_id, name=name, ilvl=ilvl)
    return SlotRow(slot=slot, slot_label="Helm", item=item, name=name, ilvl=ilvl)


def _empty_row(slot="head", label="Helm") -> SlotRow:
    return SlotRow(slot=slot, slot_label=label, item=None, name=None, ilvl=None)


def _pick(
    slot="head",
    *,
    is_swap,
    delta_ehp=0.0,
    ehp_modeled=True,
    item=None,
    n_alternatives=3,
    source=None,
) -> _SlotPick:
    return _SlotPick(
        slot=slot,
        item=item,
        is_swap=is_swap,
        delta_ehp=delta_ehp,
        delta_dps=0.0,
        composite=0.0,
        has_warning=False,
        n_alternatives=n_alternatives,
        ehp_modeled=ehp_modeled,
        source=source,
    )


def _gem_row(
    *,
    is_optimal,
    is_identity_optimal=None,
    delta_ehp=0.0,
    current_label="Current Gem",
    best_label=None,
    current_gem_id=111,
    best_gem_id=222,
    slot="head",
    index=0,
) -> GemRow:
    # `is_identity_optimal` defaults to mirroring `is_optimal` — every test
    # written before the 2026-07-05 "optimal" label split treated the two as
    # the same thing, so this preserves their exact prior behavior. Pass
    # `is_identity_optimal=False` explicitly to exercise the new "kept, below
    # the swap bar" state (real gap, not the model's genuine best pick).
    if is_identity_optimal is None:
        is_identity_optimal = is_optimal
    return GemRow(
        slot=slot,
        index=index,
        current_label=current_label,
        is_optimal=is_optimal,
        is_identity_optimal=is_identity_optimal,
        best_label=best_label,
        best_stats_label="",
        delta_ehp=delta_ehp,
        delta_label=f"+{delta_ehp:,.0f} eHP",
        current_gem_id=current_gem_id,
        best_gem_id=best_gem_id,
    )


def _enchant_row(
    *,
    modeled=True,
    is_optimal,
    is_identity_optimal=None,
    delta_ehp=0.0,
    current_label="Current Enchant",
    best_label=None,
    current_enchant_id=111,
    current_wowhead_spell_id=111,
    best_wowhead_spell_id=222,
    slot="chest",
) -> EnchantRow:
    # Mirrors `_gem_row`'s exact convention: `is_identity_optimal` defaults to
    # mirroring `is_optimal` so every test written before the below-bar
    # renderer fix keeps its prior behavior unchanged. Pass
    # `is_identity_optimal=False` explicitly to exercise the "kept, below the
    # swap bar" state (real gap, not the model's genuine best pick) — the
    # enchant sibling of `_gem_row`'s `optimal_grace` case.
    if is_identity_optimal is None:
        is_identity_optimal = is_optimal
    return EnchantRow(
        slot=slot,
        current_label=current_label,
        modeled=modeled,
        is_optimal=is_optimal,
        is_identity_optimal=is_identity_optimal,
        best_label=best_label,
        best_stats_label="",
        delta_ehp=delta_ehp,
        delta_label="not modeled" if not modeled else f"+{delta_ehp:,.0f} eHP",
        current_enchant_id=current_enchant_id,
        current_wowhead_spell_id=current_wowhead_spell_id,
        best_wowhead_spell_id=best_wowhead_spell_id,
    )


# ── _slot_card_html unit tests (the card builder) ──────────────────────────


def test_card_is_display_html_with_no_button() -> None:
    """A card is pure display HTML — never a <button>."""
    item = ItemSpec(slot="head", item_id=100, name="Test Helm", ilvl=289)
    html = _slot_card_html(_row(), _pick(is_swap=False, item=item))
    assert html.startswith('<div class="gear-card')
    assert "<button" not in html
    assert "gear-card-name" in html and "gear-card-meta" in html


def test_card_name_is_quality_colored() -> None:
    """The item name carries the quality class (q-epic by default) so it renders
    in the WoW quality colour — the signal 'everyone knows'."""
    html = _slot_card_html(_row(), _pick(is_swap=False))
    assert 'class="gear-card-name q-epic"' in html


def test_swap_card_shows_ehp_and_swap_class() -> None:
    """A slot with a modeled better option shows the gold '↑ +X eHP' line (the
    moat — a real number, never a glyph), the .swap edge class, AND NAMES the
    recommended replacement so the user can see what to swap to."""
    item = ItemSpec(slot="head", item_id=200, name="Better Helm", ilvl=295)
    html = _slot_card_html(_row(), _pick(is_swap=True, delta_ehp=3889.0, item=item))
    assert "gear-card swap" in html
    assert "gear-card-swap" in html
    assert "eHP" in html and "3,889" in html
    # The recommended item is named on the card (the answer to "swap to what?").
    assert "gear-card-swap-to" in html and "Better Helm" in html


def test_mplus_drop_swap_names_its_own_provenance() -> None:
    """An unowned M+ loot-table drop scored as a swap must say so on the card
    — it used to render identically to a real owned-item swap (round-1
    review, 2026-07-05): a player would read "N slots recommend a swap,"
    click Trial, and see nothing change because they don't own the item."""
    item = ItemSpec(slot="head", item_id=200, name="Chase Helm", ilvl=298)
    html = _slot_card_html(
        _row(), _pick(is_swap=True, delta_ehp=32548.0, item=item, source="m+ Sky")
    )
    assert "gear-card-source" in html
    assert "M+ drop" in html and "Sky" in html
    assert "not yet owned" in html


def test_owned_swap_has_no_mplus_provenance_line() -> None:
    """A bag/vault-sourced swap (or the pre-existing-tests' default
    ``source=None``) must not pick up the M+-drop badge."""
    item = ItemSpec(slot="head", item_id=200, name="Better Helm", ilvl=295)
    html = _slot_card_html(_row(), _pick(is_swap=True, delta_ehp=3889.0, item=item, source="bag"))
    assert "gear-card-source" not in html


def test_humanize_source_label_expands_mplus_drop() -> None:
    """Shared by the paperdoll swap card and the slot dialog's alternative
    rows — the dialog still showed the raw "m+ Sky" tag after the card's
    own fix, a same-fact-two-surfaces mismatch (round-2 review,
    2026-07-05)."""
    assert _humanize_source_label("m+ Sky") == "M+ drop (Sky) · not yet owned"
    assert _humanize_source_label("m+ MGT") == "M+ drop (MGT) · not yet owned"


def test_humanize_source_label_passes_through_owned_sources() -> None:
    assert _humanize_source_label("bag") == "bag"
    assert _humanize_source_label("vault") == "vault"


def test_slot_dialog_wires_the_shared_source_humanizer() -> None:
    """Mutation-verified pin: drop the `_humanize_source_label(` reference
    at the slot-dialog call site and this fails — that's exactly the
    regression round 2 found (the dialog's alt rows kept showing the raw
    `"m+ Sky"` tag after the card's own fix)."""
    import inspect

    from simf.ui import slot_dialog

    assert "_humanize_source_label(" in inspect.getsource(slot_dialog)


def test_unmodeled_proc_swap_shows_no_fake_number() -> None:
    """An unmodeled proc/use swap must NOT print a passive-stat ΔeHP figure —
    it says 'run a sim' instead (false-precision honesty guard)."""
    item = ItemSpec(slot="trinket1", item_id=200, name="Proc Trinket", ilvl=295)
    html = _slot_card_html(
        _row(slot="trinket1"),
        _pick(slot="trinket1", is_swap=True, delta_ehp=41897.0, ehp_modeled=False, item=item),
    )
    assert "run a sim" in html
    assert "eHP" not in html
    assert "41,897" not in html
    # …but it STILL names the recommended item — only the fake number is held.
    assert "Proc Trinket" in html


def test_optimal_card_is_quiet_best_in_slot() -> None:
    """A slot with no better option reads a quiet 'Best you own', no swap edge."""
    html = _slot_card_html(_row(), _pick(is_swap=False))
    assert "Best you own" in html
    assert "gear-card swap" not in html
    assert "gear-card-swap" not in html


def test_optimal_card_with_alternatives_names_the_count() -> None:
    """A gated-out slot (no swap clears the meaningful-upgrade bar) still tells
    the user alternatives exist to compare — e.g. two owned trinkets that only
    differ in haste vs. versatility shouldn't read as 'nothing to compare',
    which was indistinguishable from a genuinely empty bag/vault (user-flagged
    2026-07-04). The count nudges toward the off-sheet Browse control rather
    than naming the alternative directly, so this stays quiet like the plain
    'Best you own' case — no swap edge, no ΔeHP figure."""
    html = _slot_card_html(_row(), _pick(is_swap=False, n_alternatives=2))
    assert "Best you own · 2 options to compare" in html
    assert "gear-card swap" not in html
    assert "gear-card-swap" not in html


def test_optimal_card_without_alternatives_stays_plain() -> None:
    """No bag/vault candidates at all for this slot: the original plain 'Best
    you own' text, no 'to compare' tacked on (nothing to browse to)."""
    html = _slot_card_html(_row(), _pick(is_swap=False, n_alternatives=0))
    assert "Best you own</span>" in html
    assert "to compare" not in html


def test_card_gem_line_absent_for_socketless_item() -> None:
    """gem_rows=None (the default — no sockets on this item) renders no gem line."""
    html = _slot_card_html(_row(), _pick(is_swap=False))
    assert "gear-card-gems" not in html


def test_card_gem_line_optimal_names_the_current_gem() -> None:
    """Already-optimal socket: quiet confirmation (not the aquamarine accent)
    that NAMES the current gem — a bare 'optimal' doesn't say what's socketed."""
    row = _gem_row(is_optimal=True, current_label="Flawless Versatile Lapis", current_gem_id=240912)
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert 'class="gear-card-gems optimal"' in html
    assert "Flawless Versatile Lapis" in html and "optimal" in html


def test_card_gem_line_optimal_gem_name_is_wowhead_linked() -> None:
    """The gem name is a Wowhead link — hovering shows the native stats
    tooltip via the page's Wowhead Power script (the actual 'mouse over the
    gem' mechanism; we can only pin the anchor href here, not real hover)."""
    row = _gem_row(is_optimal=True, current_label="Flawless Versatile Lapis", current_gem_id=240912)
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert 'href="https://www.wowhead.com/item=240912"' in html
    assert "item-link" in html  # shares the equipped-item focus-ring class


def test_card_gem_line_shows_recommended_name_and_delta() -> None:
    """A socket with a better survival gem NAMES it (the answer to 'what do I
    put in this socket') and shows the real ΔeHP (the moat — never a bare
    glyph), Wowhead-linked to the recommended gem's id, not the current one."""
    row = _gem_row(
        is_optimal=False, delta_ehp=2650.0, best_label="Flawless Masterful Lapis", best_gem_id=333
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert 'class="gear-card-gems"' in html
    assert "gear-card-gems optimal" not in html
    assert "Flawless Masterful Lapis" in html
    assert 'href="https://www.wowhead.com/item=333"' in html
    assert "eHP" in html and "2,650" in html


def test_card_gem_line_independent_of_swap_line() -> None:
    """A slot can be BOTH an item swap AND carry a gem call-out — the two
    signals are orthogonal (re-gemming the current item vs. replacing it)."""
    item = ItemSpec(slot="head", item_id=200, name="Better Helm", ilvl=295)
    row = _gem_row(is_optimal=False, delta_ehp=1200.0, best_label="Flawless Masterful Lapis")
    html = _slot_card_html(
        _row(),
        _pick(is_swap=True, delta_ehp=3889.0, item=item),
        gem_rows=[row],
    )
    assert "gear-card-swap" in html and "3,889" in html
    assert "gear-card-gems" in html and "1,200" in html and "Flawless Masterful Lapis" in html


def test_card_gem_line_below_bar_is_neutral_not_optimal() -> None:
    """A real, computed gap exists but is smaller than the swap-worthiness
    bar (`is_optimal` True, `is_identity_optimal` False): the card must NOT
    claim 'optimal' — that reads as a lie once the honesty caveats below the
    card say the same stat may be undervalued (2026-07-05 gem-trust review,
    found live on AnonGuardian1's rings: both scored thousands of eHP below the
    model's real best pick and both still said 'optimal'). Nor may it say
    'kept' (2026-07-30 user direction) — just the current gem, an arrow, the
    real best candidate, and the delta, so the reader decides for themself."""
    row = _gem_row(
        is_optimal=True,
        is_identity_optimal=False,
        delta_ehp=5470.0,
        current_label="Flawless Versatile Peridot",
        best_label="Flawless Masterful Lapis",
        best_gem_id=333,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert "gear-card-gems optimal" not in html
    assert 'class="gear-card-gems below-bar"' in html
    assert "kept" not in html
    assert "Flawless Versatile Peridot" in html  # still names what's actually socketed
    assert "Flawless Masterful Lapis" in html  # AND the real best candidate
    assert "→" in html  # neutral arrow, no verdict word
    assert "5,470" in html or "0.2" in html or "0.3" in html  # some form of the real delta
    # Both gems are Wowhead-linked, not just the currently-socketed one — a
    # 2026-07-07 ask: the below-bar state named the real best candidate as
    # plain text, so there was no way to check its own stats.
    assert 'href="https://www.wowhead.com/item=111"' in html  # current
    assert 'href="https://www.wowhead.com/item=333"' in html  # best candidate


def test_card_gem_line_empty_socket_below_bar_never_says_optimal() -> None:
    """The most damaging surfaced instance: an EMPTY, ungemmed socket must
    never claim 'optimal' just because the fill gain is below the swap bar."""
    row = _gem_row(
        is_optimal=True,
        is_identity_optimal=False,
        delta_ehp=500.0,
        current_label="empty socket",
        current_gem_id=None,
        best_label="Flawless Masterful Lapis",
        best_gem_id=333,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert "optimal" not in html
    assert "empty socket" in html
    assert "Flawless Masterful Lapis" in html
    # No current gem to link (empty socket), but the real best candidate
    # still gets its Wowhead link so its stats are checkable.
    assert 'href="https://www.wowhead.com/item=333"' in html


def test_card_gem_link_aria_label_includes_the_trailing_ehp_text() -> None:
    """The gem name link's ``aria-label`` restates the "· +X eHP" clause that
    sits AFTER the </a> as a plain-text sibling. Without this, a screen-reader
    user tabbing link-to-link (or using a links-list/rotor) hears only "link,
    Flawless Masterful Lapis" with no indication a number — or even that this
    is a gem — sits next to it (2026-07-01 a11y audit of PR #245/#246; unlike
    the equipped-item swap-to line, the gem's ΔeHP has no preceding sibling
    line a linear read would hit first, so the link's own accessible name is
    the only place that context can live)."""
    row = _gem_row(
        is_optimal=False, delta_ehp=2653.0, best_label="Flawless Masterful Lapis", best_gem_id=333
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert 'aria-label="Flawless Masterful Lapis gem, +2,653 eHP' in html
    # WCAG 2.5.3 Label in Name: the accessible name must still literally
    # contain the visible link text, or a speech-input user saying "click
    # Flawless Masterful Lapis" won't match the control.
    assert 'aria-label="Flawless Masterful Lapis' in html
    # The visible text itself is untouched by the aria-label addition — the
    # eHP clause stays OUTSIDE the <a>, exactly as before.
    assert ">Flawless Masterful Lapis</a> · +2,653 eHP" in html


def test_card_gem_link_aria_label_on_optimal_state_too() -> None:
    """The quiet 'optimal' gem line gets the same aria-label treatment — a
    screen-reader user tabbing to it should hear that the socket is already
    optimal, not just the bare gem name (matching the aquamarine-state fix
    above for consistency across both gem-line states)."""
    row = _gem_row(is_optimal=True, current_label="Flawless Versatile Lapis", current_gem_id=240912)
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert 'aria-label="Flawless Versatile Lapis gem, optimal"' in html


def test_card_gem_link_best_candidate_linked_and_labeled_in_below_bar_state() -> None:
    """The below-bar state links BOTH names — the currently-socketed
    gem (pre-existing) and the real best candidate (2026-07-07 ask: only the
    current gem was clickable, so there was no way to check the proposed
    gem's own stats without leaving the card and searching by name)."""
    row = _gem_row(
        is_optimal=True,
        is_identity_optimal=False,
        delta_ehp=5470.0,
        current_label="Flawless Versatile Peridot",
        best_label="Flawless Masterful Lapis",
        current_gem_id=111,
        best_gem_id=333,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=[row])
    assert 'href="https://www.wowhead.com/item=333"' in html
    assert ">Flawless Masterful Lapis</a>" in html
    assert 'aria-label="Flawless Masterful Lapis gem, +5,470 eHP if socketed"' in html


def test_card_enchant_line_optimal_name_is_wowhead_linked() -> None:
    """Same treatment as a gem: the enchant name is a Wowhead link (Wowhead's
    "on-use" spell id, not the enchant_id used for matching — see
    data/enchants.yaml's header) so hovering shows the native tooltip."""
    row = _enchant_row(
        is_optimal=True, current_label="Mark of the Worldsoul", current_wowhead_spell_id=1236069
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), enchant_row=row)
    assert 'href="https://www.wowhead.com/spell=1236069"' in html
    assert "item-link" in html  # shares the gem/equipped-item focus-ring class


def test_card_enchant_line_recommended_name_is_wowhead_linked_to_the_best_id() -> None:
    """A slot with a better survival enchant links to the RECOMMENDED
    enchant's spell id, not the currently-equipped one's."""
    row = _enchant_row(
        is_optimal=False,
        delta_ehp=1600.0,
        best_label="Mark of Nalorakk",
        best_wowhead_spell_id=1236054,
        current_wowhead_spell_id=1236069,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), enchant_row=row)
    assert 'href="https://www.wowhead.com/spell=1236054"' in html
    assert "https://www.wowhead.com/spell=1236069" not in html
    assert "Mark of Nalorakk" in html


def test_card_enchant_line_not_modeled_still_links_current_name() -> None:
    """An unmodeled enchant (head/shoulder/weapon) still names what's
    equipped — that name should be just as Wowhead-linked as a modeled one,
    the "not modeled" caveat is about the ΔeHP number, not the hover tooltip.
    Leads with "equipped" (2026-07-28 copy fix) so the card can't be misread
    as "no enchant is possible here" — the separate `no_enchant_exists` state
    below already owns that claim."""
    row = _enchant_row(
        modeled=False,
        is_optimal=True,
        current_label="Empowered Hex of Leeching",
        current_enchant_id=987,
        current_wowhead_spell_id=1236056,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), enchant_row=row)
    assert 'href="https://www.wowhead.com/spell=1236056"' in html
    assert "equipped, not modeled" in html
    # Base (vivid aquamarine) color, NOT the muted `.unmodeled` class — a
    # real Wowhead-linked name sits on this line (2026-07-31 user ask: it
    # read as the same dim tone as the two sibling captions below, which
    # have no link at all).
    assert 'class="gear-card-enchant"' in html
    assert "gear-card-enchant unmodeled" not in html


def test_card_enchant_line_not_modeled_empty_slot_never_claims_equipped() -> None:
    """The narrower sibling of the test above: a head/shoulder/weapon slot
    that's unmodeled AND genuinely never enchanted (`current_enchant_id`
    None, `current_label` == "no enchant") must NOT say "equipped" — a
    pre-merge review caught the first draft of the "equipped, not modeled"
    copy fix always saying "equipped" regardless, which read as the
    self-contradictory "no enchant · equipped, not modeled" for exactly this
    case."""
    row = _enchant_row(
        modeled=False,
        is_optimal=True,
        current_label="no enchant",
        current_enchant_id=None,
        current_wowhead_spell_id=None,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), enchant_row=row)
    assert "no enchant · not modeled" in html
    assert "equipped" not in html
    # Stays muted — genuinely nothing here to click, unlike the equipped
    # sibling above.
    assert "gear-card-enchant unmodeled" in html


def test_card_enchant_line_falls_back_to_plain_text_without_a_spell_id() -> None:
    """An unrecognized current enchant (or a catalog entry with no researched
    wowhead_spell_id yet) degrades to plain text — never a broken/empty href."""
    row = _enchant_row(
        is_optimal=True, current_label="unrecognized enchant", current_wowhead_spell_id=None
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), enchant_row=row)
    assert "unrecognized enchant" in html
    assert "wowhead.com/spell=" not in html


def test_card_enchant_line_below_bar_is_neutral_not_optimal() -> None:
    """The enchant sibling of `test_card_gem_line_below_bar_is_neutral_not_optimal`:
    a real, computed gap exists but is smaller than the swap-worthiness bar
    (`is_optimal` True via `EnchantRow`'s `optimal_grace` case, `is_identity_
    optimal` False) — the card must NOT claim 'optimal'. Before this fix the
    renderer only branched on `is_optimal`, so a recognized-but-suboptimal
    enchant collapsed into the same flat "· optimal" badge a genuinely-best
    pick gets — the identical class of bug PR #276 fixed for gems. Nor may
    it say 'kept' (2026-07-30 user direction) — just current → best + delta."""
    row = _enchant_row(
        is_optimal=True,
        is_identity_optimal=False,
        delta_ehp=650.0,
        current_label="Mark of the Worldsoul",
        best_label="Mark of Nalorakk",
        current_wowhead_spell_id=1236069,
        best_wowhead_spell_id=1236054,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), enchant_row=row)
    assert "gear-card-enchant optimal" not in html
    assert 'class="gear-card-enchant below-bar"' in html
    assert "kept" not in html
    assert "Mark of the Worldsoul" in html  # still names what's actually applied
    assert "Mark of Nalorakk" in html  # AND the real best candidate
    assert "→" in html  # neutral arrow, no verdict word
    assert "650" in html


def test_card_enchant_line_below_bar_links_both_names() -> None:
    """The below-bar state links BOTH names — the currently-applied
    enchant (pre-existing) and the real best candidate — mirroring
    `test_card_gem_link_best_candidate_linked_and_labeled_in_below_bar_state`."""
    row = _enchant_row(
        is_optimal=True,
        is_identity_optimal=False,
        delta_ehp=650.0,
        current_label="Mark of the Worldsoul",
        best_label="Mark of Nalorakk",
        current_wowhead_spell_id=1236069,
        best_wowhead_spell_id=1236054,
    )
    html = _slot_card_html(_row(), _pick(is_swap=False), enchant_row=row)
    assert 'href="https://www.wowhead.com/spell=1236069"' in html  # current
    assert 'href="https://www.wowhead.com/spell=1236054"' in html  # best candidate
    assert ">Mark of Nalorakk</a>" in html


def _set_status(*, pieces, active_threshold, name="Test Vesture") -> SetStatus:
    tier = TierSet(
        id="test_set",
        name=name,
        spec="protection_warrior",
        item_ids=frozenset({100}),
        bonus_2pc="2pc test bonus",
        bonus_4pc="4pc test bonus",
    )
    return SetStatus(set=tier, pieces=pieces, active_threshold=active_threshold)


def test_card_tags_active_tier_set_threshold() -> None:
    """A card whose item is a tier-set member at 2pc/4pc gets an 'Npc {set
    name}' tag on the meta line — closes a real gap (Brutoh, 2026-07-28):
    only the header strip's once-per-page aggregate badge named the set/
    count; a single card gave no hint it was even a member, forcing a
    slot-by-slot name comparison against that header."""
    status = _set_status(pieces=4, active_threshold=4)
    html = _slot_card_html(
        _row(item_id=100),
        _pick(is_swap=False),
        tier_status_by_item_id={100: status},
    )
    assert "4pc Test Vesture" in html


def test_card_tags_partial_tier_set_progress_below_threshold() -> None:
    """Below the 2pc bonus, the tag reads 'N/5 {set name}' — mirrors the
    header badge's own muted partial-progress state (`load._tier_sets_badge_html`)."""
    status = _set_status(pieces=1, active_threshold=0)
    html = _slot_card_html(
        _row(item_id=100),
        _pick(is_swap=False),
        tier_status_by_item_id={100: status},
    )
    assert "1/5 Test Vesture" in html


def test_card_omits_tier_tag_for_non_member_item() -> None:
    """An item id absent from the map (not a tracked set's member) gets no
    tag — most cards, most of the time."""
    status = _set_status(pieces=4, active_threshold=4)
    html = _slot_card_html(
        _row(item_id=999),
        _pick(is_swap=False),
        tier_status_by_item_id={100: status},
    )
    assert "Test Vesture" not in html


def test_card_omits_tier_tag_when_no_status_map_passed() -> None:
    """`tier_status_by_item_id=None` (the default) never crashes and never
    tags — every pre-existing call site that doesn't know about tier sets
    keeps rendering exactly as before."""
    html = _slot_card_html(_row(item_id=100), _pick(is_swap=False))
    assert ">Helm · i289<" in html


def test_card_multi_socket_item_stacks_one_gem_line_per_socket() -> None:
    """A rare 2-socket item gets two stacked gem lines, not a lossy aggregate —
    each socket names its own gem so the card never hides which one to swap."""
    rows = [
        _gem_row(is_optimal=True, current_label="Flawless Versatile Lapis", index=0),
        _gem_row(is_optimal=False, delta_ehp=900.0, best_label="Flawless Deadly Garnet", index=1),
    ]
    html = _slot_card_html(_row(), _pick(is_swap=False), gem_rows=rows)
    assert html.count("gear-card-gems") == 2  # one <span class="gear-card-gems..."> per socket
    assert "Flawless Versatile Lapis" in html and "optimal" in html
    assert "Flawless Deadly Garnet" in html and "900" in html


def test_paperdoll_rails_are_balanced_8_8() -> None:
    """Both rails are 8 tall (the in-game / ui.png layout) — the left rail
    carries the cosmetic shirt + tabard so it doesn't end short and leave dead
    panel space before the centred weapons."""
    assert len(_PAPERDOLL_LEFT) == 8
    assert len(_PAPERDOLL_RIGHT) == 8
    assert "shirt" in _PAPERDOLL_LEFT and "tabard" in _PAPERDOLL_LEFT


def test_card_for_synthesizes_empty_cosmetic_slot() -> None:
    """A cosmetic slot the data layer never produces (shirt/tabard) resolves to
    an empty, non-swap placeholder card instead of raising KeyError."""
    row, pick = _card_for("shirt", rows_by_slot={}, picks={})
    assert row.item is None and row.slot == "shirt" and row.slot_label == "Shirt"
    assert pick.is_swap is False
    html = _slot_card_html(row, pick)
    assert "gear-card empty" in html and "Shirt" in html


def test_mirror_flag_adds_mirror_class() -> None:
    """The right column flips via the .mirror class (icons hug the outer edge)."""
    plain = _slot_card_html(_row(), _pick(is_swap=False))
    mirrored = _slot_card_html(_row(), _pick(is_swap=False), mirror=True)
    assert "gear-card mirror" not in plain
    assert "gear-card mirror" in mirrored


def test_empty_slot_card_renders_placeholder() -> None:
    """An empty slot renders a quiet placeholder card showing the slot label
    (the in-game look — 'Helm', not 'Empty'), no swap edge, no signal line."""
    html = _slot_card_html(_empty_row(slot="head", label="Helm"), _pick(is_swap=False))
    assert "gear-card empty" in html
    assert "Helm" in html  # slot label IS the card text now
    assert "Best you own" not in html  # no signal line on an empty slot


# ── source-level structure / CSS pins (mutation guards) ────────────────────


def test_sheet_has_no_per_slot_buttons() -> None:
    """The card grid + gear list must not emit per-slot buttons (the sheet is
    display only). The old ghost-button path (slot_btn_ keys) is gone."""
    grid_start = RECOMMEND_SOURCE.find("def _render_paperdoll_grid(")
    grid_body = RECOMMEND_SOURCE[grid_start : RECOMMEND_SOURCE.find("\ndef ", grid_start + 1)]
    assert "_slot_card_html(" in grid_body, "grid must build cards via _slot_card_html"
    assert "st.button" not in grid_body, "the gear sheet must have no buttons"
    assert "slot_btn_" not in APP_SOURCE, (
        "per-slot 'slot_btn_' buttons were removed — the sheet is display only."
    )


def test_gear_list_full_sheet_no_filter_no_expander() -> None:
    """`_render_gear_list` shows the whole sheet — no actionable filter, no
    'show all' expander."""
    start = GEAR_SURFACE_SOURCE.find("def _render_gear_list(")
    body = GEAR_SURFACE_SOURCE[start : GEAR_SURFACE_SOURCE.find("\ndef ", start + 1)]
    assert "st.expander(" not in body
    assert "slot_filter=" not in body and "optimal_count" not in body
    assert "_render_paperdoll_grid(" in body


def test_card_css_present() -> None:
    for needle in (
        ".gear-card {",
        ".gear-card.mirror",
        ".gear-card.swap",
        ".gear-card-name.q-epic",
        "--q-epic-text:",
    ):
        assert needle in APP_SOURCE, f"missing gear-card CSS: {needle}"


def test_swap_recommendation_headline_has_visual_weight() -> None:
    """Live-UI review, 2026-07-18: "N slots recommend a swap · total +X%"
    is the single most decision-relevant number on the Gear tab, but used to
    render as plain markdown bold — body-text size, quieter than the section
    headers around it. Must render through a dedicated, larger-than-body
    CSS class, not bare `**bold**` markdown."""
    import simf.ui.recommend as recommend_module

    src = Path(recommend_module.__file__).read_text()
    assert 'class="gear-swap-headline"' in src
    assert "**" not in src.split("recommend a swap")[0][-40:], (
        "headline reverted to plain markdown bold instead of the styled <p>"
    )
    assert ".gear-swap-headline {" in APP_SOURCE
    assert "font-size: 19px" in APP_SOURCE


def test_card_icon_is_52px() -> None:
    """P4 polish (2026-07-02): the paperdoll icon grew 44px -> 52px for
    legibility. Pinned so a future edit can't silently shrink it back."""
    start = APP_SOURCE.find(".gear-sheet-dark .gear-card-icon {")
    assert start != -1, "gear-card-icon rule not found"
    block = APP_SOURCE[start : APP_SOURCE.find("}", start)]
    assert "width: 52px; height: 52px;" in block
    assert "44px" not in block


def test_gear_col_has_min_width_zero() -> None:
    """Regression (2026-07-05): `.gear-col` is a CSS Grid item under
    `.gear-sheet-grid`'s `1fr 1fr` columns. A grid item's automatic min-width
    defaults to its content's min-content size regardless of `min-width: 0`
    set further down on `.gear-card`/`.gear-card-text` — without this rule, a
    single long nowrap gem/enchant line (e.g. "Flawless Versatile Peridot →
    Flawless Masterful Lapis +0.29% (+5,535 eHP)") forces `.gear-col` past its
    column's width, overflowing `.gear-sheet-grid`/`.gear-sheet-dark`
    horizontally. Confirmed live in headless Chromium at a 1072px viewport
    (clipped the right column's trinket cards at the page edge, and the
    resulting reflow desynced the weapon row from the dark sheet's bottom
    edge — reads as a card rendering outside its container). Firefox's
    slightly different text-metric rounding didn't trip the same threshold,
    which is why an earlier headless-Firefox-only investigation couldn't
    reproduce the original bug report."""
    start = APP_SOURCE.find(".gear-col {")
    assert start != -1, "gear-col rule not found"
    block = APP_SOURCE[start : APP_SOURCE.find("}", start)]
    assert "min-width: 0" in block, ".gear-col must set min-width: 0 to prevent grid overflow"


def test_browse_control_present() -> None:
    """Off-sheet trial-swap access — a slot picker that opens the slot dialog."""
    assert "def _render_slot_browse_control(" in GEAR_SURFACE_SOURCE
    start = GEAR_SURFACE_SOURCE.find("def _render_slot_browse_control(")
    body = GEAR_SURFACE_SOURCE[start : GEAR_SURFACE_SOURCE.find("\ndef ", start + 1)]
    assert "on_click=_slot_dialog" in body, "browse control must open the slot dialog"


# ── rendered-app behavior ──────────────────────────────────────────────────


def _char_state(app: AppTest) -> None:
    app.session_state["char_data"] = {
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
    app.session_state["simc_equipped"] = {
        "head": ItemSpec(slot="head", item_id=100, name="Helm", ilvl=658),
    }
    app.session_state["view"] = "gear"


def test_rendered_sheet_is_cards_not_buttons(app: AppTest) -> None:
    """End-to-end: the gear sheet renders as cards (gear-card HTML) with no
    per-slot buttons, and the off-sheet browse picker is present."""
    _char_state(app)
    app.run()
    assert not app.exception, f"app raised: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "gear-card" in body, "card paperdoll HTML not found in rendered app"
    slot_buttons = [b for b in app.button if b.key and b.key.startswith("slot_btn_")]
    assert not slot_buttons, f"sheet must have no per-slot buttons, found: {slot_buttons}"
    pickers = [s for s in app.selectbox if s.key == "_slot_browse_select"]
    assert pickers, "off-sheet slot browse picker missing"


def test_rendered_sheet_shows_gem_line_for_socketed_item(app: AppTest) -> None:
    """End-to-end wiring check: a socketed item's card carries a NAMED,
    Wowhead-linked gem line — catches a dropped gem_rows_by_slot= wire between
    gear_surface/recommend/item_html that per-module unit tests can't see."""
    _char_state(app)
    app.session_state["simc_equipped"]["head"] = ItemSpec(
        slot="head",
        item_id=100,
        name="Helm",
        ilvl=658,
        gem_ids=[240912],  # Versatile Lapis
    )
    app.run()
    assert not app.exception, f"app raised: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    # class="gear-card-gems (not the bare substring — the static CSS block's
    # ".gear-card-gems { ... }" selector always matches regardless of whether
    # any card actually renders the class).
    assert 'class="gear-card-gems' in body, "socketed item's card is missing its gem line"
    assert "https://www.wowhead.com/item=" in body, "gem name isn't Wowhead-linked for hover stats"
    # The old consolidated section is gone from the main surface (user ask
    # 2026-07-01) — its per-slot dialog sibling is untouched but not rendered here.
    assert "#### 💎 Gems" not in body
