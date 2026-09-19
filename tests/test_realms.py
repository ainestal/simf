"""Bundled realm-list loader (src/simf/io/realms.py)."""

from __future__ import annotations

from simf.io.realms import realm_names, slug_for


def test_realm_names_sorted_and_scoped_to_region():
    eu = realm_names("eu")
    assert "Uldum" in eu
    assert "Barthilas" not in eu, "Barthilas is a US-only realm, must not appear in EU"
    assert eu == sorted(eu, key=str.casefold), "names must be sorted case-insensitively"
    assert realm_names("unknown-region") == []


def test_all_regions_present_nonempty_and_clean():
    for region in ("eu", "us", "kr", "tw"):
        names = realm_names(region)
        assert names, f"{region} realm list is empty"
        # Test/instance realms (ALL-CAPS codes like EU1A1-INST) must be filtered.
        assert all(any(c.islower() for c in n) for n in names), (
            f"{region} contains an ALL-CAPS test/instance realm"
        )


def test_slug_for_uses_real_blizzard_slug_not_naive():
    # Plain realm: slug is the lowercased name.
    assert slug_for("eu", "Uldum") == "uldum"
    # Special-char realm: the real Blizzard slug strips the apostrophe — a naive
    # name.lower().replace(" ","-") would yield "drak'tharon" and 404.
    assert slug_for("us", "Drak'Tharon") == "draktharon"
    assert slug_for("eu", "Aggra (Português)") and "(" not in slug_for("eu", "Aggra (Português)")


def test_slug_for_unknown_or_blank_returns_none():
    assert slug_for("eu", None) is None
    assert slug_for("eu", "") is None
    assert slug_for("eu", "Not A Real Realm") is None  # free-typed custom realm
