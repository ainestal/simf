"""Danger-pull cheat sheet — a per-dungeon "what to watch for before you
pull" reference panel. ROADMAP.md Batch G / Season 2 Readiness bucket A:
the rendering shell is real and wired end-to-end today; the actual
pull-by-pull content is data-only and waits on real Season 2 (or, today,
Season 1) logs to characterize — see `data/danger_pulls.yaml`'s own header.

Deliberately renders the honest "no data yet" placeholder rather than
nothing at all when a dungeon has no entries: the whole point of staging
this shell now is that it's visible and provably wired before real content
exists, not that it stays invisible until launch day.
"""

from __future__ import annotations

import streamlit as st

from simf.core.constants import load_danger_pulls

_NO_DATA_CAPTION = (
    "No danger-pull data yet for {name} — content lands once real key logs "
    "exist to characterize it."
)


def render_danger_pull_cheatsheet(selected_dungeons: list[dict]) -> None:
    """Collapsed expander, one sub-section per dungeon in `selected_dungeons`
    (the same shape `ui.state._selected_dungeons()` returns — each dict has
    at least an `id`, usually a `name`). Renders nothing when the list is
    empty — there's nothing to be a cheat sheet for.
    """
    if not selected_dungeons:
        return
    danger_pulls = load_danger_pulls()
    with st.expander("Danger pulls for your prog dungeons", expanded=False):
        for dungeon in selected_dungeons:
            name = dungeon.get("name") or dungeon["id"]
            pulls = danger_pulls.get(dungeon["id"]) or []
            st.markdown(f"**{name}**")
            if not pulls:
                st.caption(_NO_DATA_CAPTION.format(name=name))
                continue
            for p in pulls:
                st.markdown(f"- **{p['pull']}** — {p['danger']} *Tip: {p['tip']}*")
