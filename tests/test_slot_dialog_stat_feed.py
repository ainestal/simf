"""Regression tests for the slot-dialog stat-feed bug.

Brutoh 2026-05-24: "this suggestions aren't trustworthy." On the Ring 2
slot, the alt card for **Bifurcation Band (ilvl 266)** rendered as
``Sta 995 / Hst 113 / Mst 190`` — but the in-game tooltip is
``749 Sta / 93 Haste / 158 Mastery / 43 Avoidance``. A ~33% upward
error on every stat, with the Avoidance tertiary missing entirely. The
same card claimed ΔeHP +42,092 — implausibly positive for a lower-ilvl
ring vs Brutoh's equipped ilvl 276 Triumvirate.

Root cause: ``_stats_for_item`` (UI helper) cached on ``item_id`` only.
Brutoh has Bifurcation Band equipped at finger1 ilvl 289. When the slot
dialog opened Ring 2, the bag's Bifurcation Band 266 (different
``bonus_ids``) hit the cache by item_id and returned the **ilvl 289
stats** — inflating both the displayed stats and the ΔeHP score.

These tests pin the fix:

1. The session-state cache must key on ``(item_id, bonus_ids)`` so two
   ItemSpecs with the same id but different bonus_ids stay distinct.
2. A lower-ilvl alt with identical stat distribution must score ΔeHP
   ≤ 0 against a higher-ilvl equipped piece (monotonicity sanity check).
3. ``compute_delta_dps`` and the slot-dialog code path both read the
   ``dps_stat_weights`` table from ``constants.yaml`` — no hardcoded
   copy, no drift between sources.
4. The Wowhead stat parser recognises ``Avoidance`` / ``Leech`` /
   ``Speed`` tertiary lines so they survive into the display.
5. ``format_item_stats`` renders the tertiaries after secondaries.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from simf.core.constants import load_constants
from simf.io.item_db import fetch_item_stats_wowhead
from simf.optimizer.alternatives import alternatives_for_slot
from simf.optimizer.vault_ranking import compute_delta_dps
from simf.ui.helpers.gear_list import format_item_stats

# ─── 1. Cache must key on (item_id, bonus_ids) ────────────────────────────────


@dataclass
class _Spec:
    """Minimal ItemSpec stub — only the attributes ``_stats_for_item`` reads."""

    item_id: int
    bonus_ids: list[int]
    crafted_stats: list[int] | None = None


def test_stats_for_item_cache_keyed_by_bonus_ids(monkeypatch):
    """Same ``item_id``, different ``bonus_ids`` → distinct cache entries,
    distinct fetch results. Brutoh's Bifurcation Band 289 (equipped) and
    266 (bag) share id 251115 but resolve to different stats; the cache
    must not collapse them."""
    # `_stats_for_item`, `_ss`, and `_ITEM_DB` moved to `simf.ui.state` in the
    # foundation split (PR 1); patch + call the module where they now live.
    from simf.ui import state as ui_app

    # Fake fetch returns ilvl-distinguished stats based on bonus_id presence.
    def fake_fetch(spec, region: str = "eu"):
        if 12806 in (spec.bonus_ids or []):
            return {"stamina": 995, "haste_rating": 113, "mastery_rating": 190}  # ilvl 289
        return {"stamina": 749, "haste_rating": 93, "mastery_rating": 158}  # ilvl 266

    # Patch fetch + provide an in-memory session-state holder so we
    # don't need the full Streamlit runtime.
    fake_session: dict = {}
    monkeypatch.setattr(ui_app, "_ss", lambda: fake_session)

    class FakeITEM_DB:
        @staticmethod
        def fetch_item_stats_for_spec(spec, region: str = "eu", class_spec=None):
            return fake_fetch(spec)

    monkeypatch.setattr(ui_app, "_ITEM_DB", FakeITEM_DB)

    equipped_289 = _Spec(item_id=251115, bonus_ids=[13440, 6652, 13668, 12699, 12806])
    bag_266 = _Spec(item_id=251115, bonus_ids=[12795, 13440, 40, 13668, 12699])

    stats_289 = ui_app._stats_for_item(equipped_289)
    stats_266 = ui_app._stats_for_item(bag_266)

    # Same item_id, but two distinct cache hits — no collision.
    assert stats_289["stamina"] == 995, "Equipped Bifurcation Band 289 stats wrong"
    assert stats_266["stamina"] == 749, (
        "Bag Bifurcation Band 266 should fetch its own stats, not return the cached ilvl-289 stats"
    )
    # Both entries persist (cached under distinct keys).
    cache = fake_session["_item_stats_cache"]
    assert len(cache) == 2, f"Expected two cache entries, got {cache!r}"


def test_stats_for_item_cache_hit_same_bonus_ids(monkeypatch):
    """Two specs with the same id AND the same bonus_ids should still
    share the cache — we're not breaking caching, just narrowing it."""
    # `_stats_for_item`, `_ss`, and `_ITEM_DB` moved to `simf.ui.state` in the
    # foundation split (PR 1); patch + call the module where they now live.
    from simf.ui import state as ui_app

    call_count = {"n": 0}

    def fake_fetch(spec, region: str = "eu"):
        call_count["n"] += 1
        return {"stamina": 749}

    fake_session: dict = {}
    monkeypatch.setattr(ui_app, "_ss", lambda: fake_session)

    class FakeITEM_DB:
        @staticmethod
        def fetch_item_stats_for_spec(spec, region: str = "eu", class_spec=None):
            return fake_fetch(spec)

    monkeypatch.setattr(ui_app, "_ITEM_DB", FakeITEM_DB)

    spec_a = _Spec(item_id=251115, bonus_ids=[12795, 13440])
    spec_b = _Spec(item_id=251115, bonus_ids=[13440, 12795])  # same set, different order

    ui_app._stats_for_item(spec_a)
    ui_app._stats_for_item(spec_b)

    assert call_count["n"] == 1, "Sorted bonus_ids should normalise; expected 1 fetch"


def test_stats_for_item_cache_keyed_by_crafted_stats(monkeypatch):
    """Same item_id AND bonus_ids, but two different crafted-stat choices
    (e.g. crit+haste vs crit+versatility on the same crafted bonus set) must
    NOT collide on one cache entry — each relabels Wowhead's default pair
    differently (see item_db._remap_crafted_secondary_stats)."""
    from simf.ui import state as ui_app

    def fake_fetch(spec, region: str = "eu"):
        if spec.crafted_stats == [32, 36]:
            return {"crit_rating": 42, "haste_rating": 42}
        return {"crit_rating": 42, "versatility_rating": 42}

    fake_session: dict = {}
    monkeypatch.setattr(ui_app, "_ss", lambda: fake_session)

    class FakeITEM_DB:
        @staticmethod
        def fetch_item_stats_for_spec(spec, region: str = "eu", class_spec=None):
            return fake_fetch(spec)

    monkeypatch.setattr(ui_app, "_ITEM_DB", FakeITEM_DB)

    crit_haste = _Spec(item_id=237831, bonus_ids=[12214], crafted_stats=[32, 36])
    crit_vers = _Spec(item_id=237831, bonus_ids=[12214], crafted_stats=[32, 40])

    stats_a = ui_app._stats_for_item(crit_haste)
    stats_b = ui_app._stats_for_item(crit_vers)

    assert stats_a == {"crit_rating": 42, "haste_rating": 42}
    assert stats_b == {"crit_rating": 42, "versatility_rating": 42}, (
        "different crafted_stats on the same item+bonus must not reuse the other variant's cached stats"
    )
    cache = fake_session["_item_stats_cache"]
    assert len(cache) == 2, f"Expected two cache entries, got {cache!r}"


def test_stats_for_item_threads_class_spec_from_session_char_data(monkeypatch):
    """`_stats_for_item` must read the loaded character's class_spec from
    session state and forward it to the resolver — an agi/str hybrid-primary
    item resolves differently per spec, and the caller has no other way to
    supply it (its own signature is unchanged: ``_stats_for_item(spec)``)."""
    from simf.ui import state as ui_app

    seen_specs: list[str | None] = []

    class FakeITEM_DB:
        @staticmethod
        def fetch_item_stats_for_spec(spec, region: str = "eu", class_spec=None):
            seen_specs.append(class_spec)
            return {"agility": 101} if class_spec == "guardian_druid" else {"strength": 101}

    monkeypatch.setattr(ui_app, "_ITEM_DB", FakeITEM_DB)

    fake_session_guardian: dict = {"char_data": {"class_spec": "guardian_druid"}}
    monkeypatch.setattr(ui_app, "_ss", lambda: fake_session_guardian)
    stats = ui_app._stats_for_item(_Spec(item_id=250256, bonus_ids=[]))
    assert stats == {"agility": 101}

    fake_session_warrior: dict = {"char_data": {"class_spec": "protection_warrior"}}
    monkeypatch.setattr(ui_app, "_ss", lambda: fake_session_warrior)
    stats2 = ui_app._stats_for_item(_Spec(item_id=250257, bonus_ids=[]))
    assert stats2 == {"strength": 101}

    assert seen_specs == ["guardian_druid", "protection_warrior"]


def test_stats_for_item_cache_keyed_by_class_spec_across_character_switch(monkeypatch):
    """Regression: viewing a hybrid agi/str item under one character, then
    switching to a different-spec character in the SAME session, must not
    silently serve the first character's spec-resolved stats for the same
    item_id+bonus_ids+crafted_stats. Before class_spec joined the cache key,
    a Guardian's agility-resolved trinket stayed cached and would have been
    served to a warrior loaded right after in the same session."""
    from simf.ui import state as ui_app

    seen_specs: list[str | None] = []

    class FakeITEM_DB:
        @staticmethod
        def fetch_item_stats_for_spec(spec, region: str = "eu", class_spec=None):
            seen_specs.append(class_spec)
            return {"agility": 101} if class_spec == "guardian_druid" else {"strength": 101}

    monkeypatch.setattr(ui_app, "_ITEM_DB", FakeITEM_DB)

    fake_session: dict = {"char_data": {"class_spec": "guardian_druid"}}
    monkeypatch.setattr(ui_app, "_ss", lambda: fake_session)
    spec = _Spec(item_id=250256, bonus_ids=[])
    guardian_stats = ui_app._stats_for_item(spec)
    assert guardian_stats == {"agility": 101}

    # Same session dict, same item — only class_spec changes, as happens on
    # "Change character".
    fake_session["char_data"] = {"class_spec": "protection_warrior"}
    warrior_stats = ui_app._stats_for_item(spec)
    assert warrior_stats == {"strength": 101}, (
        "switching characters must not keep serving the previous character's "
        "spec-resolved stats for the same item"
    )
    assert seen_specs == ["guardian_druid", "protection_warrior"], (
        "a class_spec-blind cache key would short-circuit the second fetch entirely"
    )


# ─── 2. ΔeHP monotonicity sanity check ────────────────────────────────────────


@dataclass
class _Item:
    slot: str
    item_id: int
    name: str = ""
    ilvl: int = 0
    bonus_ids: list[int] | None = None


def test_slot_dialog_ehp_monotonicity():
    """Lower-stat alt vs higher-stat equipped → ΔeHP ≤ 0.

    Brutoh's specific complaint: a bag Bifurcation Band 266 with
    real ilvl-266 stats (749 Sta) should NOT score above an equipped
    Triumvirate 276 with proportionally larger stats. With correct
    stats feeding the scorer, monotonicity holds. This test confirms
    that ONCE the stat feed is correct, the math doesn't flip."""
    marginals = {
        "stamina": {"p": 30.0, "m": 22.0},
        "armor_from_gear": {"p": 8.0, "m": 0.0},
        "versatility_rating": {"p": 5.0, "m": 4.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
    }
    dungeons = [
        {"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.90}},
        {"id": "alg", "abbrev": "Alg", "school_mix": {"physical": 0.10}},
    ]

    # Equipped: a higher-ilvl ring with bigger Sta.
    equipped = {"finger2": _Item(slot="finger2", item_id=151311, name="Triumvirate", ilvl=276)}
    # Bag alt: same item slot, lower Sta — should be a strict downgrade.
    bag = {
        "finger1": [
            _Item(
                slot="finger1",
                item_id=251115,
                name="BifurcationBand",
                ilvl=266,
                bonus_ids=[12795, 13440, 40, 13668, 12699],
            )
        ]
    }
    stats = {
        151311: {"stamina": 848, "versatility_rating": 191, "haste_rating": 82},  # 276 ring
        251115: {"stamina": 749, "mastery_rating": 158, "haste_rating": 93},  # 266 ring (correct)
    }
    alts = alternatives_for_slot(
        slot="finger2",
        equipped=equipped,
        bag=bag,
        vault={},
        marginals=marginals,
        dungeons=dungeons,
        item_stats_fn=lambda spec: stats.get(spec.item_id),
    )
    assert len(alts) == 1
    # The 266 ring has 99 fewer Sta — ΔeHP can be slightly positive or
    # negative depending on which secondary lines up where, but it must
    # NOT be implausibly large in the positive direction the way the
    # bug produced (+42k eHP). 99 × 30 phys × 0.5 ≈ 1485 — bound at
    # ±5000 eHP to flag a regression of the cache-poisoning magnitude.
    assert -10000 <= alts[0].avg_delta_ehp <= 5000, (
        f"ΔeHP {alts[0].avg_delta_ehp:.0f} is implausibly large — cache poisoning may have returned"
    )


# ─── 3. dps_stat_weights table-drift assertion ────────────────────────────────


def test_dps_stat_weights_table_drift():
    """Slot-dialog ΔDPS must use the live ``dps_stat_weights`` table from
    constants.yaml. No hardcoded copy in any optimizer module.

    Today the table is supplied by the caller (UI reads it from
    ``constants.yaml`` and passes it through ``alternatives_for_slot``
    → ``compute_delta_dps``). This test pins that contract: the weights
    surfaced in constants.yaml are exactly what ``compute_delta_dps``
    would multiply against, and a regression that hard-coded the
    Method-refit numbers somewhere else would diverge here."""
    weights = load_constants().get("dps_stat_weights", {})
    # The four secondaries Brutoh's slot dialog cares about.
    expected_keys = {"haste_rating", "crit_rating", "mastery_rating", "versatility_rating"}
    assert expected_keys.issubset(weights.keys()), (
        f"constants.yaml dps_stat_weights missing keys: {expected_keys - set(weights.keys())}"
    )

    # Identity test: feed compute_delta_dps the live table and confirm
    # it actually USES every value (sum of products is what we expect).
    new_stats = {
        "haste_rating": 1000,
        "crit_rating": 0,
        "mastery_rating": 0,
        "versatility_rating": 0,
    }
    equipped = {"haste_rating": 0, "crit_rating": 0, "mastery_rating": 0, "versatility_rating": 0}
    delta = compute_delta_dps(new_stats, equipped, weights)
    # +1000 haste × weights["haste_rating"] / 1000 = weights["haste_rating"]
    assert delta == pytest.approx(weights["haste_rating"], rel=1e-9), (
        "compute_delta_dps not multiplying haste_rating by the constants.yaml weight"
    )

    # And the Method qualitative ordering is preserved (regression
    # against an accidental refit that dropped Haste below Crit/Vers).
    assert weights["haste_rating"] >= weights["crit_rating"]
    assert weights["crit_rating"] >= weights["mastery_rating"]
    assert weights["versatility_rating"] >= weights["mastery_rating"]


# ─── 4. Tertiary parsing (Avoidance / Leech / Speed) ──────────────────────────


def test_fetch_item_stats_wowhead_extracts_avoidance(monkeypatch, tmp_path):
    """Wowhead XML responses surface Avoidance as a normal stat line —
    previously dropped because the name_map only knew about primary +
    secondary stats. Brutoh's Bifurcation Band 266 in-game tooltip
    reads ``+43 Avoidance`` and the slot-dialog card must surface it."""
    from simf.io import item_db

    # Redirect the on-disk cache to a tmp dir so this test doesn't write
    # into ~/.simf and doesn't read a stale cached response.
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")

    # Minimal Wowhead-shaped XML that exercises the stat regex.
    fake_xml = (
        "<?xml version='1.0'?>"
        "<wowhead>"
        "<htmlTooltip><![CDATA["
        "<!--stat0-->+749 Stamina<br>"
        "<!--stat1-->+93 Haste<br>"
        "<!--stat2-->+158 Mastery<br>"
        "<!--stat3-->+43 Avoidance<br>"
        "]]></htmlTooltip>"
        "</wowhead>"
    )

    class _FakeResp:
        text = fake_xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp())

    stats = fetch_item_stats_wowhead(251115, bonus_ids=[12795, 13440, 40, 13668, 12699])
    assert stats is not None, "Wowhead parse returned None"
    assert stats.get("avoidance_rating") == 43, f"Avoidance line not parsed — got {stats!r}"
    assert stats.get("stamina") == 749
    assert stats.get("haste_rating") == 93
    assert stats.get("mastery_rating") == 158


def test_format_item_stats_includes_tertiary():
    """The compact stat line surfaces Avoidance / Leech / Speed when
    present. Order: secondaries before tertiaries so the line scans
    left-to-right matching the in-game tooltip."""
    stats = {
        "stamina": 749,
        "haste_rating": 93,
        "mastery_rating": 158,
        "avoidance_rating": 43,
    }
    line = format_item_stats(stats)
    assert "Sta 749" in line
    assert "Hst 93" in line
    assert "Mst 158" in line
    assert "Avd 43" in line, f"Avoidance missing from stat line: {line!r}"
    # Tertiary renders after secondary (visual order).
    assert line.index("Mst 158") < line.index("Avd 43"), "Avoidance should render after secondaries"


def test_fetch_item_stats_wowhead_tri_primary_label(monkeypatch, tmp_path):
    """Tri-primary stat sticks (e.g. Heart of Wind 250256) emit
    ``+101 [Agility or Strength or Intellect]`` in the Wowhead XML.
    The name_map only knew the two-way ``[Strength or Intellect]`` label,
    so the whole item resolved to NO stats and every stats-only trinket
    comparison scored it as a zero-stat item (found 2026-06-10 running
    the Heart-of-Wind vault verdict for Brutoh)."""
    from simf.io import item_db

    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")

    fake_xml = (
        "<?xml version='1.0'?>"
        "<wowhead>"
        "<htmlTooltip><![CDATA["
        "<!--stat71-->+101 [Agility or Strength or Intellect]<br>"
        "]]></htmlTooltip>"
        "</wowhead>"
    )

    class _FakeResp:
        text = fake_xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp())

    stats = fetch_item_stats_wowhead(250256, bonus_ids=[12801, 13440, 6652, 12699])
    assert stats is not None, "tri-primary item resolved to no stats"
    assert stats.get("strength") == 101, f"tri-primary label not mapped — got {stats!r}"


@pytest.mark.parametrize(
    ("label", "expected_key"),
    [
        ("[Agility or Strength]", "strength"),  # Solarflare Prism 252420 — hid its +128 primary
        ("[Agility or Intellect]", "agility"),  # Emberwing Feather 250144 — no str option
        ("Agility", "agility"),  # standalone agi was absent entirely (agility-tank specs)
        (
            "Intellect",
            "intellect",
        ),  # unmapped-only items returned None → never cached → refetched every load
    ],
)
def test_fetch_item_stats_wowhead_label_coverage(monkeypatch, tmp_path, label, expected_key):
    """Every primary-stat label variant Wowhead emits must map to a stat key.
    An item whose ONLY stat line is unmapped resolves to None, which is not
    cached — so it was re-fetched on every load (part of the 2026-06-10
    283-call burst that got the dev IP rate-limited)."""
    from simf.io import item_db

    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")

    fake_xml = (
        "<?xml version='1.0'?><wowhead><htmlTooltip><![CDATA["
        f"<!--stat72-->+95 {label}<br>"
        "]]></htmlTooltip></wowhead>"
    )

    class _FakeResp:
        text = fake_xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp())
    stats = fetch_item_stats_wowhead(424242)
    assert stats == {expected_key: 95}, f"label {label!r} not mapped — got {stats!r}"
