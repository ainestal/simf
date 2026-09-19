"""simf UI — item HTML fragment builders (L2).

Pure string builders for the gear surfaces: Wowhead-linked item names,
standalone tooltip-anchored icons, the quality-bordered icon wrapper, and
the card-paperdoll gear card. Display only — no ``st.*`` at module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1 foundation layers (``models``/``format_html``/``state``) and the
gear-list helper. NEVER imports from ``app`` (strict L2 DAG).
"""

from __future__ import annotations

import html

from simf.io.upgrade_track import classify_upgrade_track
from simf.optimizer.tier_sets import SetStatus
from simf.ui.format_html import _item_quality_class
from simf.ui.helpers.enchant_panel import EnchantRow
from simf.ui.helpers.gear_list import (
    display_name,
    wowhead_icon_url,
    wowhead_url,
)
from simf.ui.helpers.gem_panel import GemRow
from simf.ui.helpers.stat_composition import composition_html, trinket_registry_html
from simf.ui.models import _SlotPick
from simf.ui.state import _format_ehp_delta, _icon_for_item


def _title_attr(text: str) -> str:
    """A ``title="…"`` fragment (leading space included) carrying the
    plain-text equivalent of a truncating span's rendered content, so the
    native browser tooltip recovers whatever ``white-space: nowrap;
    text-overflow: ellipsis`` (the five ``.gear-card-*`` CSS rules in
    app.py) cuts off — the honesty copy PRs #276/#433 shipped ("kept —
    best: X +Y%") is unrecoverable on a truncated card without this."""
    return f' title="{html.escape(text, quote=True)}"'


def _humanize_source_label(source: str) -> str:
    """`"m+ Sky"` -> `"M+ drop (Sky) · not yet owned"`; `"bag"`/`"vault"`
    pass through unchanged. Shared by the paperdoll swap card and the slot
    dialog's alternative rows so an M+ loot-table drop reads the same
    provenance on both surfaces — the dialog still showed the raw `"m+
    Sky"` tag after the card's own fix (round-2 review, 2026-07-05)."""
    if source.startswith("m+ "):
        return f"M+ drop ({source[3:]}) · not yet owned"
    return source


def _item_link_html(item: object, with_icon: bool = True) -> str:
    """Item name as a clickable Wowhead link (new tab). When ``with_icon`` is
    set and an icon is cached, the link is prefixed with an inline icon
    sharing the anchor. Icon dimensions come from CSS, not URL size."""
    name = html.escape(display_name(item), quote=False)
    url = wowhead_url(item)
    icon_url = wowhead_icon_url(_icon_for_item(item)) if with_icon else None
    icon_html = f'<img src="{icon_url}" alt="" class="item-icon" />' if icon_url else ""
    if not url:
        return f"{icon_html}{name}"
    # `item-link` class hooks a visible :focus-visible ring. After the
    # item-details popover was dropped from the caption-bearing surfaces
    # (vault cell, slot-dialog alt + current rows), this anchor is the
    # PRIMARY keyboard affordance there — the default browser ring is
    # near-invisible on the cream surface, so WCAG 2.4.7 needs an explicit one.
    return (
        f'<a class="item-link" href="{url}" target="_blank" '
        f'rel="noopener noreferrer">{icon_html}{name}</a>'
    )


def _icon_link_html(item: object, css_class: str, size: str = "medium") -> str:
    """Standalone Wowhead-linked icon — used by row layouts that separate the
    icon from the text. Power.js attaches the rich tooltip to *anchors* with
    a wowhead URL, so an unwrapped ``<img>`` won't show one. Wrapping the
    image in its own anchor keeps the row layout intact while making the
    icon hoverable. Empty-slot placeholders fall back to a bare ``<div>``.

    ``size`` selects the zamimg source resolution (see ``wowhead_icon_url``);
    callers displaying the icon larger than the 36px "medium" native size
    should pass ``"large"`` (56px) so the browser downsamples a sharp source
    instead of upscaling a soft one."""
    icon_url = wowhead_icon_url(_icon_for_item(item), size=size) if item is not None else None
    if not icon_url:
        return f'<div class="{css_class}"></div>'
    img = f'<img src="{icon_url}" alt="" class="{css_class}" />'
    url = wowhead_url(item)
    if not url:
        return img
    return (
        f'<a class="icon-tooltip-anchor" href="{url}" '
        f'target="_blank" rel="noopener noreferrer" '
        f'aria-label="{html.escape(display_name(item), quote=True)} on Wowhead">{img}</a>'
    )


def _icon_link_html_q(item: object, base_class: str, size: str = "medium") -> str:
    """``_icon_link_html`` with the item's quality-border class appended.

    Convenience wrapper for the paperdoll surfaces so the quality classifier
    and the icon markup stay co-located at the call sites that render an
    equipped/recommended piece. Falls back to the plain icon when the item
    has no resolvable quality (the fragment is empty)."""
    q = _item_quality_class(item)
    return _icon_link_html(item, f"{base_class} {q}".strip(), size=size)


def _gem_link_html(gem_id: int | None, name: str, *, a11y_suffix: str = "") -> str:
    """A gem's name as a focusable Wowhead link, sharing the ``item-link``
    class (focus ring) with equipped-item names. Gems have fixed stats (no
    ilvl/bonus_id scaling to worry about, unlike gear), so the URL is just
    the bare item id. Hovering shows the native rich tooltip — full stats,
    icon — via the page's Wowhead Power script (``_inject_wowhead_tooltips``,
    already loaded once for the whole app), no custom popover needed. Falls
    back to plain text when there's no id to link (unrecognized current gem).

    ``a11y_suffix`` (e.g. "gem, already optimal" / "gem, +2,653 eHP if
    socketed") is appended via ``aria-label`` only — it does NOT change the
    visible text. Unlike the equipped-item swap-to line (where the ΔeHP leads
    on its own preceding line, so a screen reader hits the number before ever
    reaching the link), the gem line's ΔeHP is a trailing plain-text sibling
    on the SAME line as the only link — a Tab-to-link or links-list read would
    otherwise announce just the bare gem name with no hint a number, or even
    that this is a gem, sits next to it (WCAG 1.3.1/2.4.6 — the visual
    relationship the eHP figure has to its gem isn't conveyed to non-sighted
    users without this)."""
    if not gem_id:
        return name
    label = f' aria-label="{name}{a11y_suffix}"' if a11y_suffix else ""
    return (
        f'<a class="item-link" href="https://www.wowhead.com/item={gem_id}"{label} '
        f'target="_blank" rel="noopener noreferrer">{name}</a>'
    )


def _gem_row_html(g: GemRow, class_spec: str = "", marginals: dict | None = None) -> str:
    """One socket's gem line — THREE distinct states, not two. A 2026-07-05
    gem-trust review (novice/engaged/elite tank + ui-craft-critic +
    calibration-scientist, independently) found a single "optimal" badge
    collapsing two different truths into one: "the model scored this and it
    genuinely won" and "a real, computed gap exists but is too small to act
    on." The most damaging surfaced instance was an EMPTY, ungemmed socket
    reading "💎 empty socket · optimal". Branch on ``is_identity_optimal``
    first, not ``is_optimal`` alone:

      - Genuinely the model's best pick → quiet "💎 {name} · optimal".
      - A real gap exists but sits below the swap bar → neutral
        "💎 {name} → {best name} {delta}", naming the real best candidate and
        its delta with NO verdict word — the reader sees the actual numbers
        (here and in the composition line below) and decides for themself
        whether it's worth the gold/time, rather than the card telling them
        "kept" (2026-07-30 user direction: the model shouldn't decide this
        for the reader, only show the difference).
      - An actionable swap → aquamarine "💎 {recommended name} · +X eHP".

    The name is a Wowhead link (see ``_gem_link_html``) so hovering answers
    "what IS that gem" without leaving the card — a bare eHP number can't.
    The link carries an ``aria-label`` restating the trailing text, since
    that text is a plain sibling node the link's own accessible name
    wouldn't otherwise include.

    ``class_spec``/``marginals`` (empty/``None`` — no composition rendered)
    drive the per-stat composition line appended below a real ΔeHP (the
    below-bar and "swap" states — ``g.terms`` sums to exactly the
    same ``delta_ehp`` shown next to it, see ``gem_panel._delta_terms``)."""
    if g.is_identity_optimal:
        link = _gem_link_html(g.current_gem_id, g.current_label, a11y_suffix=" gem, optimal")
        title = _title_attr(f"💎 {g.current_label} · optimal")
        return f'<span class="gear-card-gems optimal"{title}>💎 {link} · optimal</span>'
    comp = composition_html(g.terms, class_spec, marginals or {})
    if g.is_optimal:
        delta = _format_ehp_delta(g.delta_ehp)
        suffix = f" gem, current pick — {g.best_label} would be {delta}"
        if g.current_gem_id is None:
            current = "empty socket"
            current_label = "empty socket"
        else:
            current = _gem_link_html(g.current_gem_id, g.current_label, a11y_suffix=suffix)
            current_label = g.current_label
        best = _gem_link_html(g.best_gem_id, g.best_label, a11y_suffix=f" gem, {delta} if socketed")
        title = _title_attr(f"💎 {current_label} → {g.best_label} {delta}")
        return (
            f'<span class="gear-card-gems below-bar"{title}>💎 {current} → '
            f"{best} {delta}</span>{comp}"
        )
    name = g.best_label or g.current_label
    delta = _format_ehp_delta(g.delta_ehp)
    link = _gem_link_html(g.best_gem_id, name, a11y_suffix=f" gem, {delta} if socketed")
    title = _title_attr(f"💎 {name} · {delta}")
    return f'<span class="gear-card-gems"{title}>💎 {link} · {delta}</span>{comp}'


def _enchant_link_html(spell_id: int | None, name: str, *, a11y_suffix: str = "") -> str:
    """An enchant's name as a focusable Wowhead link, sharing the
    ``item-link`` class (focus ring) with gem/equipped-item names.

    ``spell_id`` is Wowhead's "on-use" spell id (``EnchantRow.
    current_wowhead_spell_id`` / ``best_wowhead_spell_id``) — a DIFFERENT
    namespace from the ``enchant_id`` simf matches a character's gear
    against (see ``data/enchants.yaml``'s header). It resolves to a real
    Wowhead page (confirmed live: ``wowhead.com/spell={id}`` 301-redirects to
    ``/spell={id}/enchant-{slot}-{name}``), so hovering shows the native rich
    tooltip via the page's Wowhead Power script — same UX as a gem's link.
    Falls back to plain text when there's no id yet (an unrecognized current
    enchant, or a catalog entry whose spell id hasn't been researched)."""
    if not spell_id:
        return name
    label = f' aria-label="{name}{a11y_suffix}"' if a11y_suffix else ""
    return (
        f'<a class="item-link" href="https://www.wowhead.com/spell={spell_id}"{label} '
        f'target="_blank" rel="noopener noreferrer">{name}</a>'
    )


def _enchant_row_html(e: EnchantRow, class_spec: str = "", marginals: dict | None = None) -> str:
    """One slot's enchant line: a quiet "✨ {current name} · optimal", an
    aquamarine "✨ {recommended name} · +X eHP" when a better enchant exists,
    or one of three honest "no pick here" reasons:
      - "✨ {current name} · equipped, not modeled" when a real enchant IS
        socketed but nothing in this slot's catalog carries a stat simf's
        survival model prices yet (``EnchantRow.modeled`` — see
        ``optimizer/enchant_suggester``'s module docstring: head, shoulder,
        and every weapon enchant fall in this bucket in Midnight 12.0.5).
        Leads with "equipped" deliberately — a 2026-07-28 user report read
        the old bare "· not modeled" as "no enchant is possible here,"
        confusing it with the state below, when actually an enchant exists
        and is active, simf just doesn't price its effect (Avoidance/Leech/
        Speed/procs — spelled out in the title/aria-label and in
        ``enchant_panel.NOT_MODELED_NOTE``). Base (NOT muted) color — a real
        Wowhead-linked name sits on this line, so it gets the same vivid
        aquamarine every other clickable enchant name uses (2026-07-31 user
        ask: the link read as the same dim tone as the two sibling captions
        below, which have nothing to click at all).
      - "✨ no enchant · not modeled" — a narrower, genuinely-EMPTY sub-case
        (``current_enchant_id is None`` — the player never enchanted it).
        Muted (``.unmodeled``): no link, nothing to distinguish visually. A
        pre-merge review caught an earlier draft always saying "equipped"
        regardless, which read as "no enchant · equipped, not modeled" for
        exactly this case.
      - "✨ No enchant exists for this slot in Midnight." for
        back/wrist/neck/waist/hands (``EnchantRow.no_enchant_exists`` — a
        confirmed, researched finding, not a data gap: there is no enchant
        to socket at all, unlike the two states above). Also muted — same
        reasoning, no link.
    Like a gem, the enchant name is a focusable Wowhead link when a
    ``wowhead_spell_id`` is known for it (``_enchant_link_html``) — falls
    back to plain text for an unrecognized current enchant or a catalog
    entry whose spell id hasn't been researched yet. The live Blizzard-
    resolved name (``ItemSpec.enchant_name``, when the character came from
    an online lookup) is still shown even unlinked, when its enchant_id
    wasn't recognized by the catalog.

    ``class_spec``/``marginals`` drive the per-stat composition appended
    below the recommended-swap line (``e.terms`` sums exactly to
    ``delta_ehp``) — the other branches show no numeric delta at all, so
    there's nothing to decompose."""
    if e.no_enchant_exists:
        text = "No enchant exists for this slot in Midnight."
        title = _title_attr(f"✨ {text}")
        return f'<span class="gear-card-enchant unmodeled"{title}>✨ {text}</span>'
    if not e.modeled:
        if e.current_enchant_id is None:
            # Genuinely nothing applied here (current_label == "no enchant")
            # AND the slot's whole catalog scores ~0 anyway — two honest
            # facts, neither of which is "equipped" (an earlier version of
            # this branch always said "equipped," which read as "no enchant
            # · equipped, not modeled" for exactly this case — a live,
            # confirmed bug caught in review before merge).
            text = f"{e.current_label} · not modeled"
            title = _title_attr(
                f"✨ {text} — Avoidance/Leech/Speed/proc effects aren't modeled here"
            )
            return f'<span class="gear-card-enchant unmodeled"{title}>✨ {text}</span>'
        suffix = (
            " enchant, equipped — its Avoidance/Leech/Speed/proc effect isn't "
            "modeled by the survival score"
        )
        link = _enchant_link_html(e.current_wowhead_spell_id, e.current_label, a11y_suffix=suffix)
        title = _title_attr(
            f"✨ {e.current_label} · equipped — Avoidance/Leech/Speed/proc effects aren't modeled"
        )
        # Base (unmodified) `.gear-card-enchant` color, NOT `.unmodeled` — a
        # real, clickable enchant name sits on this line (unlike the two
        # sibling branches above, which are plain unlinked text with nothing
        # to visually distinguish). 2026-07-31 user ask: this line's link
        # read as the same dim, easy-to-miss tone as "no enchant" captions
        # with no link at all, when every other clickable enchant name on
        # the sheet uses the vivid aquamarine tone — muting it bought no
        # honesty the "not modeled" text itself doesn't already say.
        return f'<span class="gear-card-enchant"{title}>✨ {link} · equipped, not modeled</span>'
    if e.is_identity_optimal:
        link = _enchant_link_html(
            e.current_wowhead_spell_id, e.current_label, a11y_suffix=" enchant, optimal"
        )
        title = _title_attr(f"✨ {e.current_label} · optimal")
        return f'<span class="gear-card-enchant optimal"{title}>✨ {link} · optimal</span>'
    if e.is_optimal:
        # `is_optimal` True but `is_identity_optimal` False: the "optimal_grace"
        # case (EnchantRow's own docstring) — a known, non-empty current
        # enchant that's genuinely close to the model's best but not an exact
        # identity match. Neutral "{current} → {best} {delta}" state, no
        # verdict word — mirrors `_gem_row_html`'s identical branch (PR #276,
        # reworded 2026-07-30 per user direction: show the numbers, let the
        # reader decide, don't have the card say "kept").
        delta = _format_ehp_delta(e.delta_ehp)
        suffix = f" enchant, current pick — {e.best_label} would be {delta}"
        current = _enchant_link_html(
            e.current_wowhead_spell_id, e.current_label, a11y_suffix=suffix
        )
        best = _enchant_link_html(
            e.best_wowhead_spell_id,
            e.best_label or "",
            a11y_suffix=f" enchant, {delta} if applied",
        )
        title = _title_attr(f"✨ {e.current_label} → {e.best_label or ''} {delta}")
        return (
            f'<span class="gear-card-enchant below-bar"{title}>✨ {current} → {best} {delta}</span>'
        )
    name = e.best_label or e.current_label
    spell_id = e.best_wowhead_spell_id if e.best_label else e.current_wowhead_spell_id
    delta = _format_ehp_delta(e.delta_ehp)
    link = _enchant_link_html(spell_id, name, a11y_suffix=f" enchant, {delta}")
    comp = composition_html(e.terms, class_spec, marginals or {})
    title = _title_attr(f"✨ {name} · {delta}")
    return f'<span class="gear-card-enchant"{title}>✨ {link} · {delta}</span>{comp}'


def _slot_card_html(
    r,
    pick: _SlotPick,
    *,
    mirror: bool = False,
    gem_rows: list[GemRow] | None = None,
    enchant_row: EnchantRow | None = None,
    class_spec: str = "",
    marginals: dict | None = None,
    tier_status_by_item_id: dict[int, SetStatus] | None = None,
) -> str:
    """Build one gear card (HTML) for the card paperdoll — the universal WoW /
    Raidbots / Blizzard character-sheet style (examples/screenshots/ui.png).

    Display only, NO button: the quality-coloured item name leads, then a
    "Slot · Track · iLvl" subtitle, then the survivability signal — a steel
    "↑ +X eHP" when a slot has a better option (the moat — never a bare glyph;
    unmodeled proc trinkets say "run a sim" with no fake number), otherwise a
    quiet "Best you own" — which itself names how many owned alternatives
    exist ("Best you own · 2 options to compare") when the slot has candidates
    that didn't clear the meaningful-upgrade bar, so a near-tied bag item
    (e.g. two trinkets differing only in haste vs. versatility) is still
    discoverable from the sheet instead of reading as "nothing to compare."
    ("Best you own" rather than "Best in slot" — 2026-07-28 gear-card
    review: "BiS" reads as globally-best-in-game to this audience, and
    `recommend.py` already renamed the sheet-wide header away from that
    exact claim for the same reason.)
    A swap card also gets a steel edge (CSS). ``mirror``
    flips the card for the right column (icon on the outer edge, text toward
    the centre gutter). The name + icon are focusable Wowhead links — the
    keyboard path to item detail; the browse / trial-swap interaction lives
    off the sheet (a control below it + the upgrade panel).

    ``gem_rows`` (``None``/empty for a socketless item) adds one trailing
    line per filled socket via ``_gem_row_html`` — almost always exactly one
    (multi-socket items are rare); each stacks as its own line rather than
    collapsing into a name-less aggregate. ``enchant_row`` (``None`` only for
    a structural non-slot for this spec — a trinket, a shield off_hand — in
    Midnight 12.0.5; back/wrist/neck and waist/hands still get a row with an
    honest caption) adds one further line via ``_enchant_row_html``.

    ``class_spec``/``marginals`` drive every per-stat composition line on
    this card (the item swap's own ΔeHP, plus whatever ``gem_rows``/
    ``enchant_row`` add) — threaded straight through to each sub-renderer so
    the whole card shares one class_spec/marginals view, never a stale or
    per-line-inconsistent one.

    ``tier_status_by_item_id`` (``optimizer.tier_sets.tier_status_by_item_id``,
    keyed by item id — ``None``/empty when the character has no tracked tier
    pieces equipped) appends a "{Npc|N/5} {set name}" fragment to the meta
    line when the hero item is a member. Brutoh feedback (2026-07-28): the
    header strip's aggregate badge names the set/count once for the whole
    page, but a single card gave no hint it was even PART of a set — a reader
    had to recognize the item name and cross-check the header by eye, one
    card at a time."""
    klass = "gear-card"
    if mirror:
        klass += " mirror"
    # An empty slot is "nothing equipped here", not "a better option" — don't
    # give it the swap edge even when M+ loot technically beats an empty slot.
    if pick.is_swap and r.item is not None:
        klass += " swap"
    if r.item is None:
        klass += " empty"

    if r.item is None:
        # Empty slot (a cosmetic shirt/tabard, or genuinely unequipped gear):
        # the slot label IS the card text, muted — matching the in-game sheet's
        # quiet placeholder. No subtitle (would dup the name), no signal line.
        icon_html = '<div class="gear-card-icon empty"></div>'
        name_title = _title_attr(r.slot_label)
        name_html = f'<span class="gear-card-name empty"{name_title}>{r.slot_label}</span>'
        text_html = f'<div class="gear-card-text">{name_html}</div>'
        return f'<div class="{klass}">{icon_html}{text_html}</div>'

    # A recommended swap leads with the RECOMMENDED item, not the one the
    # player is currently wearing — the card answers "what should I use,"
    # and the old equipped-item-first hierarchy buried that answer in a
    # small 82%-opacity "→ Item" line while the big quality-colored name up
    # top was the thing to STOP using (ui-craft-critic review, 2026-07-04).
    # Every `pick.is_swap` card is already gated to a non-negative ΔeHP by
    # the meaningful-upgrade bar upstream (`_is_meaningful_swap`), so this
    # never promotes a downgrade — the net-loss case lives on a different
    # surface entirely (the Vault grid's "Trial anyway" rows).
    is_recommended_swap = pick.is_swap and pick.item is not None
    hero_item = pick.item if is_recommended_swap else r.item

    # Quality-colored icon border + focusable Wowhead link (icon + name).
    # "large" (56px native) so the 52px card icon downsamples a sharp source
    # instead of upscaling the 36px "medium" default (P4 polish, 2026-07-02).
    icon_html = _icon_link_html_q(hero_item, "gear-card-icon", size="large")
    qcls = _item_quality_class(hero_item)
    name_title = _title_attr(display_name(hero_item))
    name_html = (
        f'<span class="gear-card-name {qcls}"{name_title}>'
        f"{_item_link_html(hero_item, with_icon=False)}</span>"
    )

    # Subtitle: Slot · Track rank · iLvl — describes the HERO item, so the
    # track/ilvl badge always matches the item named above it.
    meta_parts = [r.slot_label]
    badge = classify_upgrade_track(hero_item)
    if badge is not None and badge.has_track:
        meta_parts.append(
            f"{badge.track} {badge.rank}/{badge.max_rank}"
            if badge.rank and badge.max_rank
            else badge.track
        )
    hero_ilvl = r.ilvl if hero_item is r.item else getattr(hero_item, "ilvl", None)
    if hero_ilvl:
        meta_parts.append(f"i{hero_ilvl}")
    hero_item_id = getattr(hero_item, "item_id", None)
    tier_status = (tier_status_by_item_id or {}).get(hero_item_id) if hero_item_id else None
    if tier_status is not None:
        tag = (
            f"{tier_status.active_threshold}pc"
            if tier_status.active_threshold >= 2
            else f"{tier_status.pieces}/5"
        )
        meta_parts.append(f"{tag} {tier_status.set.name}")
    meta_text = " · ".join(meta_parts)
    meta_html = f'<span class="gear-card-meta"{_title_attr(meta_text)}>{meta_text}</span>'

    # Third line(s) — the survivability signal. A swap names the ΔeHP gain,
    # then demotes the currently-equipped item to a muted "↳ replaces Item"
    # line — the same voice the Vault cards already use ("↳ replaces your
    # Trinket 1"). An unmodeled proc trinket still names the item but no
    # fake number.
    warn_glyph = " ⚠️" if pick.has_warning else ""
    if is_recommended_swap:
        gain = (
            f"↑ {_format_ehp_delta(pick.delta_ehp)}{warn_glyph}"
            if pick.ehp_modeled
            else f"↑ better option — run a sim{warn_glyph}"
        )
        replaces_html = (
            _item_link_html(r.item, with_icon=False) if r.item is not None else r.slot_label
        )
        replaces_label = display_name(r.item) if r.item is not None else r.slot_label
        # The registry-valued trinket ΔeHP isn't a stat-marginal dot product
        # at all (see `_SlotPick.via_trinket_registry`) — say so instead of
        # fabricating a composition. Every other modeled swap gets the real
        # per-stat breakdown (empty when the number itself isn't shown, e.g.
        # an unmodeled proc trinket's "run a sim" line).
        if not pick.ehp_modeled:
            swap_comp = ""
        elif pick.via_trinket_registry:
            swap_comp = trinket_registry_html()
        else:
            swap_comp = composition_html(pick.terms, class_spec, marginals or {})
        swap_to_title = _title_attr(f"↳ replaces {replaces_label}")
        third = (
            f'<span class="gear-card-swap">{gain}</span>'
            f'<span class="gear-card-swap-to"{swap_to_title}>↳ replaces {replaces_html}</span>'
            f"{swap_comp}"
        )
        if pick.breaks_set:
            third += (
                f'<span class="gear-card-setbreak">⚠ breaks {pick.breaks_set} '
                f"— set bonus not in this number</span>"
            )
        # An M+ loot-table drop (extra_sources' "m+ {abbrev}" label, e.g.
        # "m+ Sky") scored and rendered identically to a real owned-item
        # swap — a player would read "N slots recommend a swap" and click
        # Trial on an item they don't have, then see nothing change
        # (round-1 multi-agent review, 2026-07-05). Name it: provenance, not
        # exclusion — chasing a drop is a legitimate answer to "what should
        # I use," it just isn't a swap you can make tonight.
        if pick.source and pick.source.startswith("m+ "):
            third += (
                f'<span class="gear-card-source">↳ {_humanize_source_label(pick.source)}</span>'
            )
    elif pick.n_alternatives > 0:
        n = pick.n_alternatives
        third = (
            f'<span class="gear-card-best">Best you own · '
            f"{n} option{'s' if n != 1 else ''} to compare</span>"
        )
    else:
        third = '<span class="gear-card-best">Best you own</span>'

    # Gem line(s) — a socketless item (no rows) gets nothing.
    gem_html = "".join(_gem_row_html(g, class_spec, marginals) for g in (gem_rows or []))
    # Enchant line — an unenchantable slot (no row passed) gets nothing.
    enchant_html = (
        _enchant_row_html(enchant_row, class_spec, marginals) if enchant_row is not None else ""
    )

    text_html = (
        f'<div class="gear-card-text">{name_html}{meta_html}{third}{gem_html}{enchant_html}</div>'
    )
    return f'<div class="{klass}">{icon_html}{text_html}</div>'
