"""The bundled demo must load with a cold cache and no network.

CI run 27272318241 (2026-06-10): the demo-load AppTests timed out on a
fresh runner because a cold ``~/.simf/item_cache`` sent every item-stats
lookup to Wowhead live — and Wowhead 403s datacenter IPs (and rate-limited
home IPs), so the demo click burned ~280 failing round-trips before the
30 s AppTest budget expired. ``src/simf/data/item_cache_seed/`` is the
committed answer: read-only seed entries for everything the demo's first
paint requests, consulted by ``item_db`` when the user cache misses.

These tests are the contract. If a future demo refresh swaps the bundled
SimC file, re-generate the seed (load the demo once with a warm cache and
an instrumented ``_load_named_cache``, copy the hit files) or these fail.
"""

from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from simf.io import item_db
from simf.io.simc_import import parse_simc_string

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = REPO_ROOT / "src" / "simf" / "ui" / "app.py"
SEED_DIR = REPO_ROOT / "src" / "simf" / "data" / "item_cache_seed"
BRUTOH_YAML = REPO_ROOT / "src" / "simf" / "data" / "characters" / "brutoh.yaml"

# Equipped items knowingly absent from the seed. Emptied 2026-06-10 once
# Wowhead unblocked and the [Agility or Strength] parser gap was fixed —
# Solarflare Prism (252420) is seeded now. Do not grow this set casually:
# a missing seed entry means a CI-visible stat gap in the demo.
KNOWN_UNSEEDED_EQUIPPED: set[int] = set()


def _demo_parse():
    # simc_path is now relative to the packaged data dir (so the demo's gear
    # ships in the wheel / public container), not the repo root.
    from simf.core.constants import DATA_DIR

    d = yaml.safe_load(BRUTOH_YAML.read_text())
    simc = (DATA_DIR / d["simc_path"]).read_text()
    return parse_simc_string(simc)


def test_demo_loads_cold_cache_offline(monkeypatch, tmp_path):
    """Demo click with an empty cache dir and NO network must succeed
    within the same 30 s budget the real-flow tests use — this is the
    exact CI-runner condition that run 27272318241 failed under."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path / "item_cache")

    def _no_network(*args, **kwargs):
        raise RuntimeError(f"offline test: blocked network call {args[:1]}")

    monkeypatch.setattr(item_db.requests, "get", _no_network)

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    demo_btn = next((b for b in at.button if "sample build" in (b.label or "").lower()), None)
    assert demo_btn is not None, "Demo button not found on first paint"

    demo_btn.click().run()
    assert not at.exception, f"Exception during cold offline demo load: {at.exception}"
    errors = [str(e.value) for e in at.error]
    assert not errors, f"Cold offline demo load surfaced error(s): {errors}"
    assert "char_data" in at.session_state, "char_data not set after demo click"
    assert at.session_state["char_data"]["name"] == "Brutoh"


def test_seed_covers_demo_equipped_and_vault_items():
    """Every equipped + vault item in the bundled demo SimC has at least
    one Wowhead stats seed entry (any bonus variant), except the explicit
    known-unseeded allowlist."""
    parsed = _demo_parse()
    seeded_ids = {
        f.name.split("_")[1] for f in SEED_DIR.glob("wh_*.json") if not f.name.startswith("wh_icon")
    }

    missing = []
    for slot, item in parsed.items.items():
        if (
            item.item_id
            and str(item.item_id) not in seeded_ids
            and item.item_id not in KNOWN_UNSEEDED_EQUIPPED
        ):
            missing.append(f"equipped {slot}: {item.name or '?'} ({item.item_id})")
    for slot, items in parsed.vault_items.items():
        for item in items:
            if item.item_id and str(item.item_id) not in seeded_ids:
                missing.append(f"vault {slot}: {item.name or '?'} ({item.item_id})")

    assert not missing, (
        "Demo items without any seed entry (re-generate src/simf/data/item_cache_seed/ "
        f"after a demo refresh): {missing}"
    )


def test_seed_entries_have_stats_payload():
    """Seed files must carry the {'stats': {...}} shape _load_seed expects."""
    import json

    files = list(SEED_DIR.glob("*.json"))
    assert len(files) >= 100, f"Seed dir suspiciously small: {len(files)} files"
    for f in files:
        data = json.loads(f.read_text())
        assert "stats" in data, f"{f.name} lacks a 'stats' key"


def test_seed_is_read_only_fallback(monkeypatch, tmp_path):
    """User-cache entries beat seed entries; seed misses return None."""
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    # A key present in the seed resolves through the fallback…
    seeded = sorted(SEED_DIR.glob("wh_*_*.json"))[0].stem
    assert item_db._load_named_cache(seeded) is not None
    # …an unknown key does not.
    assert item_db._load_named_cache("wh_999999999_base") is None


@pytest.mark.parametrize(
    "key", ["wh_250256_6652-12699-12801-13440", "wh_250241_6652-12699-13440-13654"]
)
def test_vault_story_trinkets_are_seeded(key):
    """The two trinkets that carry the demo's vault narrative (Heart of
    Wind offer, Mark of Light incumbent) must be seeded at their exact
    equipped/offered bonus variants."""
    assert (SEED_DIR / f"{key}.json").exists(), f"missing seed: {key}"
