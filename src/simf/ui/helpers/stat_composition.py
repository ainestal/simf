"""Per-stat ΔeHP composition + mechanism-tag rendering (Phase 2 of the eHP
transparency feature).

Phase 1 (already merged) taught every optimizer scorer to hand back the exact
per-stat arithmetic behind its ΔeHP scalar (``optimizer/per_dungeon.py``'s
``CompositionTerms``) and taught ``optimizer/stat_mechanisms.py`` to gloss
*why* a given stat helps this character survive, gated on their own live
marginals. This module is the single, shared CONSUMER of both: it turns a
``CompositionTerms`` dict into the short "eHP: +905 Stamina · +338
Versatility" line every gem/enchant/gear/vault/upgrade card renders next to
its ΔeHP number, with an inline mechanism tag on each stat and a "+N more"
disclosure for anything past the top few.

The leading "eHP:" label is load-bearing, not decoration (found 2026-07-11:
a real gem, ~9 rating points apart from its alternative, rendered as
"+2,910 Versatility" with a bare "=" prefix — read, reasonably, as 2,910
points of Versatility RATING, which is impossible for a single gem. The
number is actually the derived eHP contribution of that ~9-rating swing,
which legitimately reaches into the thousands once compounded through the
full armor/versatility/Defensive-Stance multiplicative stack on a
several-hundred-thousand-eHP character). Every part after the label is
still a bare "+N <Stat>" — deliberately not "+N eHP (Stat)" per part, which
would repeat the unit on every term and risk the exact long-nowrap-line
overflow PR #279 fixed (this line's CSS already allows wrapping, unlike
that bug's `.gear-col`, so length is lower-risk than it was, but repetition
is still needless once the line states its unit once).

One function (``composition_html``) is reused by EVERY render surface — the
paperdoll swap cards, the gem/enchant lines, the upgrade-panel ranker, the
Vault grid, and the slot-alternatives dialog — so a swap card and a vault
card can never describe the same number differently. Surfaces that build one
big HTML blob per ``st.markdown`` call (the paperdoll, the upgrade panel)
embed the string directly; surfaces that already emit direct ``st.*`` calls
per row (Vault, the slot dialog) wrap it in
``st.markdown(html, unsafe_allow_html=True)``. Either way it's the exact same
markup — no second code path to drift out of sync with the first.

The "+N more" disclosure is a native ``<details>``/``<summary>`` element —
plain HTML/CSS, no JavaScript — specifically so it works standing alone
inside a joined HTML string (an ``st.expander`` there would inject a real
Streamlit widget mid-blob, which the paperdoll/upgrade-panel docstrings both
call out as breaking the "one continuous dark strip" rendering trick).

Trinkets are scored through a completely different path — a proc/on-use
effect registry (``optimizer/trinket_db.py``), not the stat-marginal dot
product every other row uses — so there is no honest composition to show.
``TRINKET_REGISTRY_NOTE`` is the caption those rows render instead; callers
gate on their own explicit "did this number come from the registry" flag
(``VaultRow.via_trinket_registry`` / ``_SlotPick.via_trinket_registry``)
rather than inferring it from the slot name, since a trinket whose registry
lookup failed falls back to the ordinary stat path and *does* get a real
composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from ...optimizer.per_dungeon import CompositionTerms
from ...optimizer.stat_mechanisms import mechanism_tag

# Full display names for the composition sentence — deliberately distinct
# from gear_list.py's abbreviated ``_STAT_DISPLAY`` (e.g. "Vers", "Hst"),
# which exists for compact side-by-side item-stat comparison. This line
# reads as a short sentence ("+338 Versatility"), so it spells the stat out.
STAT_DISPLAY_NAMES: dict[str, str] = {
    "stamina": "Stamina",
    "armor_from_gear": "Armor",
    "versatility_rating": "Versatility",
    "haste_rating": "Haste",
    "crit_rating": "Crit",
    "mastery_rating": "Mastery",
    "strength": "Strength",
    "agility": "Agility",
    "leech_rating": "Leech",
    "avoidance_rating": "Avoidance",
    "speed_rating": "Speed",
}

# Rendered instead of a composition line wherever the ΔeHP came from the
# trinket-effect registry rather than a stat-marginal dot product — see the
# module docstring. Worded for a player, not the internal Python module name
# ("registry" names an implementation detail nobody outside this codebase
# would recognize) — round 1 review flagged the old phrasing as reading like
# an internal note that leaked into player-facing copy.
TRINKET_REGISTRY_NOTE = "valued by its on-use/proc effect, not its stats"

# How many stats show inline before the rest fold into "+N more".
DEFAULT_MAX_INLINE = 3

# Appended after any stat whose mechanism gloss is present (see
# ``_format_part_html``). A 6-persona readability workshop (novice/engaged/
# elite tank, ui-craft-critic, copy-microcopy-editor, calibration-scientist)
# independently flagged the hover-only ``title=`` tooltip as undiscoverable —
# its only cue was a 1px dotted underline in muted gray on a dark card,
# which reviewers had to zoom in to confirm even exists, and which touch
# devices can't reveal at all. The glyph makes "there's more here" part of
# the text itself, scannable without already knowing the dotted-underline
# convention. Deliberately not a tap-to-reveal control — that's deferred
# alongside the rest of the mobile-hierarchy work; the native ``title=``
# hover stays the delivery mechanism for now.
MECHANISM_GLYPH = "ⓘ"  # ⓘ — circled Latin small letter i


@dataclass(frozen=True)
class CompositionPart:
    """One stat's contribution, ready to format."""

    stat: str
    display_name: str
    blended: float
    # Whole-eHP figure actually rendered — NOT ``round(blended)`` in
    # isolation. Every displayed part's ``rounded`` is apportioned by
    # ``_largest_remainder_round`` so the full set sums EXACTLY to the same
    # whole-number total the card's own ΔeHP headline shows (see that
    # function's docstring for why naive independent rounding can't
    # guarantee that).
    rounded: int
    mechanism: str | None  # None -> no parenthetical/tooltip (never invented)


def _largest_remainder_round(values: list[float], target_total: int) -> list[int]:
    """Round ``values`` to whole numbers that sum EXACTLY to ``target_total``.

    Independent per-value ``round()`` (what the old ``{:,.0f}`` formatting
    did) has no reason to land on ``target_total`` — each value's own
    rounding error is invisible to every other value, so the displayed parts
    can silently fail to sum to the ΔeHP total the card headline shows
    (round-1 review finding). This also has to absorb the contribution of
    any stat ``composition_parts`` drops for rounding to 0 eHP on its own:
    that dropped sliver is still part of ``target_total`` (the headline
    number), so it has to land on one of the values that ARE displayed.

    Generalized "largest remainder"/Hamilton apportionment: start from each
    value's own nearest-integer rounding, then repeatedly nudge whichever
    value currently has the largest rounding error in the direction needed
    (largest under-rounding when we need to add, largest over-rounding when
    we need to subtract) until the sum matches exactly. Values may be
    negative (a composition term can be a stat you're giving up in a swap),
    so this is not the textbook non-negative-shares version of the method.
    """
    if not values:
        return []
    result = [round(v) for v in values]
    diff = target_total - sum(result)
    if diff == 0:
        return result
    step = 1 if diff > 0 else -1
    remaining = abs(diff)
    n = len(values)
    while remaining > 0:
        errors = [values[i] - result[i] for i in range(n)]
        idx = max(range(n), key=lambda i: errors[i] * step)
        result[idx] += step
        remaining -= 1
    return result


def composition_parts(
    terms: CompositionTerms, class_spec: str, marginals: dict
) -> list[CompositionPart]:
    """``terms`` -> parts sorted by |blended contribution| descending.

    A stat whose blended contribution rounds to 0 eHP is dropped — it would
    render as "+0 <Stat>", which is noise, not information, and the whole
    point of this feature is that every number shown is real and non-zero.
    The kept parts' ``rounded`` values are then apportioned (see
    ``_largest_remainder_round``) against the TRUE total — the sum of every
    entry in ``terms``, dropped or not — so what's displayed always sums to
    the same whole-eHP figure the card's own headline shows.
    """
    ranked = sorted(terms.items(), key=lambda kv: -abs(kv[1].get("blended", 0.0)))
    kept: list[tuple[str, float]] = []
    for stat, vals in ranked:
        blended = float(vals.get("blended", 0.0))
        if round(blended) == 0:
            continue
        kept.append((stat, blended))
    if not kept:
        return []
    true_total = sum(float(vals.get("blended", 0.0)) for vals in terms.values())
    rounded_values = _largest_remainder_round([blended for _, blended in kept], round(true_total))
    parts: list[CompositionPart] = []
    for (stat, blended), rounded in zip(kept, rounded_values, strict=True):
        display = STAT_DISPLAY_NAMES.get(stat, stat.replace("_", " ").title())
        parts.append(
            CompositionPart(
                stat=stat,
                display_name=display,
                blended=blended,
                rounded=rounded,
                mechanism=mechanism_tag(class_spec, stat, marginals),
            )
        )
    return parts


def split_inline_overflow(
    parts: list[CompositionPart], max_inline: int = DEFAULT_MAX_INLINE
) -> tuple[list[CompositionPart], list[CompositionPart]]:
    """(inline head, overflow tail) — head has at most ``max_inline`` parts."""
    return parts[:max_inline], parts[max_inline:]


def _format_part_text(part: CompositionPart) -> str:
    sign = "+" if part.rounded >= 0 else ""
    text = f"{sign}{part.rounded:,} {part.display_name}"
    if part.mechanism:
        text += f" ({part.mechanism})"
    return text


def _format_part_html(part: CompositionPart) -> str:
    """Same content as ``_format_part_text``, but the mechanism (when present)
    is a hover tooltip (native ``title=``) rather than an always-visible
    parenthetical — keeps the inline line short and immune to the kind of
    long-line grid overflow PR #279 fixed, while still surfacing the
    mechanism per the brief ("a short parenthetical OR a hover/tooltip").

    Renders ``part.rounded`` (the largest-remainder-apportioned whole
    number), never an independent ``round(part.blended)`` — that's the
    whole point of ``_largest_remainder_round``: every part shown has to sum
    EXACTLY to the same total the card's ΔeHP headline shows.

    A glossed stat also gets ``MECHANISM_GLYPH`` appended (inside the same
    ``title=``-bearing span, so hovering the glyph reveals the identical
    tooltip as hovering the number) — see that constant's docstring for why
    the dotted underline alone wasn't a discoverable enough affordance."""
    sign = "+" if part.rounded >= 0 else ""
    label = f"{sign}{part.rounded:,} {escape(part.display_name)}"
    if not part.mechanism:
        return f"<span>{label}</span>"
    glyph = f'<sup class="stat-mechanism-glyph" aria-hidden="true">{MECHANISM_GLYPH}</sup>'
    return f'<span class="stat-mechanism" title="{escape(part.mechanism)}">{label}{glyph}</span>'


def _net_phrase(total: int) -> str:
    """Sign-free wording for a whole-eHP total — deliberately never puts a
    bare ``+``/``-`` immediately before the digits (unlike every per-stat
    part), so this reconciliation phrase reads as prose, not one more
    competing delta number next to the ones it's explaining."""
    if total > 0:
        return f"a gain of {total:,} eHP"
    if total < 0:
        return f"a loss of {abs(total):,} eHP"
    return "no net eHP"


def composition_html(
    terms: CompositionTerms,
    class_spec: str,
    marginals: dict,
    *,
    max_inline: int = DEFAULT_MAX_INLINE,
) -> str:
    """Embeddable HTML fragment for a ΔeHP number's per-stat composition.

    Returns ``""`` when ``terms`` is empty or every part rounds to 0 eHP —
    callers render nothing in that case (there's nothing to add to a number
    that's already 0, or the composition genuinely isn't available, e.g. a
    trinket-registry-valued row — see ``TRINKET_REGISTRY_NOTE``).

    On a swap that trades a large amount of one stat for another, the
    single leading term (sorted first, since it's the largest by |blended|)
    can visually dwarf the actual net ΔeHP total whenever its offsetting
    counterpart is folded into the collapsed "+N more" disclosure —
    round-1 review saw this read as a flat contradiction of the headline
    (e.g. "+8,500 Stamina" next to a "+500 eHP" card). When that's the case
    here — some part is hidden below the fold AND the leading term's own
    magnitude already exceeds the true total — an always-visible one-line
    reconciliation is appended so the leading number never has to be taken
    on faith before expanding the disclosure.
    """
    parts = composition_parts(terms, class_spec, marginals)
    if not parts:
        return ""
    head, rest = split_inline_overflow(parts, max_inline)
    inline = " · ".join(_format_part_html(p) for p in head)
    # "eHP:" is load-bearing, not decoration — every number after it is a
    # derived eHP contribution, NOT a raw stat-rating delta, and a bare "="
    # here previously read (reasonably) as the latter: a single gem a few
    # rating points from its alternative would show e.g. "+2,910
    # Versatility", indistinguishable from 2,910 points of Versatility
    # RATING (impossible for one gem) instead of the ~2,910 eHP that
    # rating swing actually compounds to on a several-hundred-thousand-eHP
    # character. State the unit once, up front, rather than per part (see
    # module docstring for why not per-part).
    html = f'<div class="gear-card-composition">eHP: {inline}</div>'
    if rest:
        rest_html = "".join(f"<div>{_format_part_html(p)}</div>" for p in rest)
        html += (
            '<details class="gear-card-composition-more">'
            f"<summary>+{len(rest)} more</summary>"
            f'<div class="gear-card-composition-more-list">{rest_html}</div>'
            "</details>"
        )
        full_total = sum(p.rounded for p in parts)
        if head and abs(head[0].rounded) > abs(full_total):
            # Two classes, not one: "-net" is this line's own stable,
            # semantic selector (what tests key off of); "-composition-more"
            # is reused ONLY to inherit its already-correct muted-but-legible
            # color in every render context (paperdoll, Vault, slot dialog),
            # including the dark-card-specific override — this module can't
            # touch app.py's CSS, and a brand-new class with no matching
            # rule at all was verified (live, via computed-style inspection)
            # to render at ~1:1 contrast against the dark card background,
            # i.e. functionally invisible. Piggybacking on a class that's
            # already proven legible everywhere this markup is embedded is
            # more robust than inventing a new one that only some future,
            # separately-owned CSS change would ever style.
            html += (
                '<div class="gear-card-composition-net gear-card-composition-more">'
                f"Nets to {_net_phrase(full_total)} once the folded parts above are counted."
                "</div>"
            )
    return html


def trinket_registry_html(css_class: str = "gear-card-composition") -> str:
    """The honest "no stat breakdown" line for a registry-valued trinket
    ΔeHP — same visual slot a real composition line would occupy."""
    return f'<div class="{css_class} trinket-registry">{TRINKET_REGISTRY_NOTE}</div>'


__all__ = [
    "DEFAULT_MAX_INLINE",
    "MECHANISM_GLYPH",
    "STAT_DISPLAY_NAMES",
    "TRINKET_REGISTRY_NOTE",
    "CompositionPart",
    "composition_html",
    "composition_parts",
    "split_inline_overflow",
    "trinket_registry_html",
]
