"""WoW combat log parser, scoped to extracting damage-taken events for one character
— thin re-export facade.

The implementation was decomposed into topic-named sibling modules
(``combat_log_core`` / ``combat_log_damage`` / ``combat_log_runs`` /
``combat_log_encounters`` / ``combat_log_roles`` / ``combat_log_buffs`` /
``combat_log_casts`` / ``combat_log_gear`` / ``combat_log_summary``). This module
now only re-exports their public + test-referenced symbols so
``from simf.io.combat_log import ...`` keeps resolving for every existing caller.

Targets COMBAT_LOG_VERSION 22 (Midnight 12.0.5).
"""

from simf.io.combat_log_buffs import (  # noqa: F401
    _AURA_APPLY_EVENTS,
    _AURA_REMOVE_EVENTS,
    detect_active_buffs,
    parse_self_buff_windows,
    parse_source_debuff_windows,
)
from simf.io.combat_log_casts import (  # noqa: F401
    CastEvent,
    EnergizeEvent,
    parse_cast_events,
    parse_energize_events,
    parse_interrupted_spell_ids,
)
from simf.io.combat_log_core import (  # noqa: F401
    DAMAGE_EVENTS,
    HEAL_EVENTS,
    SCHOOL_FLAGS,
    npc_id_from_guid,
    parse_combat_log_line,
    school_name,
)
from simf.io.combat_log_damage import (  # noqa: F401
    DamageTakenEvent,
    iter_damage_events,
    parse_damage_event,
)
from simf.io.combat_log_encounters import (  # noqa: F401
    EncounterWindow,
    RunSegment,
    find_encounter_at,
    parse_encounters,
    segment_run,
)
from simf.io.combat_log_gear import (  # noqa: F401
    _COMBATANT_INFO_SLOT_ORDER,
    CombatantInfoEquippedItem,
    CombatantInfoFull,
    _parse_combatant_info_gear_block,
    _parse_int_tuple,
    _split_top_level,
    iter_combatant_info_full,
)
from simf.io.combat_log_healing import (  # noqa: F401
    HealReceivedEvent,
    iter_heal_events,
    parse_heal_event,
)
from simf.io.combat_log_roles import (  # noqa: F401
    _RACE_BY_RACIAL_CAST,
    PartyMember,
    count_party_deaths_in_run,
    detect_destination_player_names,
    detect_party_roles,
    detect_race,
    iter_combatant_info,
)
from simf.io.combat_log_runs import (  # noqa: F401
    ChallengeModeRun,
    _last_event_time,
    _par_time_for_map,
    parse_challenge_modes,
)
from simf.io.combat_log_summary import (  # noqa: F401
    DeathRecord,
    LogSummary,
    iter_death_events,
    summarize_events_direct,
    summarize_run,
)
