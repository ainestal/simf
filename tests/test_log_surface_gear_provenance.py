"""`_gear_from_log` — Gear-tab provenance for a log-hydrated character.

A log-hydrated character's items carry ids, not names (COMBATANT_INFO has
no item-name field), and bags/vault stay empty (logs don't carry inventory)
— but until this fix nothing on the Gear surface said the gear came from a
log at all, so a reader just saw cards reading "Item #251098" with no
explanation.

`_render_local_log_flow`'s hydrate-write block sets `_gear_from_log` to the
log's basename; `_render_run_config_strip` (load.py) renders it as a
caption. Pinned via source inspection for the *setting* side (driving the
full local-log flow end-to-end needs a real combat-log fixture with
COMBATANT_INFO + party detection — out of scope for this scoped fix,
consistent with `test_log_view_upload.py`'s own file-uploader pin); the
*clearing* side is covered by real `AppTest` round trips in
`test_load.py`, mirroring its existing `_gear_source` lifecycle tests.
"""

from __future__ import annotations

import inspect

from simf.ui import log_surface


def test_hydrate_write_block_sets_gear_from_log():
    """The (log, target, run) hydrate write must stamp `_gear_from_log`
    with the log's basename so the Gear tab can explain provenance."""
    src = inspect.getsource(log_surface._render_local_log_flow)
    assert 'st.session_state["_gear_from_log"] = os.path.basename(str(log_pick))' in src
    # Must sit inside the same `if not already_pinned:` write block as the
    # other char_data/simc_equipped writes it's here to accompany — a stray
    # top-level write would restamp on every rerun, not just a real hydrate.
    write_block_start = src.index('st.session_state["char_data"] = hydrated.char_data')
    stamp_index = src.index('st.session_state["_gear_from_log"]')
    rerun_index = src.index("if spec_changed or name_changed:")
    assert write_block_start < stamp_index < rerun_index


def test_switch_to_simc_paste_clears_gear_from_log():
    """The 'Switch to SimC paste' override button clears the auto-loaded
    character — `_gear_from_log` must be cleared alongside it, or the Gear
    tab would keep claiming a log-hydrated origin for a character that's no
    longer even loaded from one."""
    src = inspect.getsource(log_surface._render_local_log_flow)
    clear_block_start = src.index("Switch to SimC paste")
    clear_block_end = src.index("st.rerun()", clear_block_start)
    clear_block = src[clear_block_start:clear_block_end]
    assert '"_gear_from_log"' in clear_block
    assert '"char_data_loaded_from_log_for"' in clear_block  # sanity: same tuple
