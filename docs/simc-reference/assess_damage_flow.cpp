// SPDX-License-Identifier: GPL-3.0-or-later
// Excerpted from SimulationCraft (https://github.com/simulationcraft/simc),
// (c) SimulationCraft contributors. Licensed under the GNU GPL v3 -- see
// ./LICENSE for the full text and ./README.md for the exact source path
// and commit this excerpt was taken from. This file is GPL-3.0 code kept
// separate from the rest of this repo (AGPL-3.0-or-later).

// SimC damage-application pipeline — where target_mitigation is called from
// engine/action/action.cpp

// L3236 (within an evaluation/preview path):
        state->result_amount = action.calculate_direct_amount( state );
        if ( state->result == RESULT_CRIT )
        {
          state->result_amount = action.calculate_crit_damage_bonus( state );
        }
        if ( amount_type == result_amount_type::DMG_DIRECT )
          state->target->target_mitigation( action.get_school(), amount_type, state );
        a = state->result_amount;
      }

      if ( average_crit )

// L4362-4365 (per-action snapshot, target-mitigation multipliers set on state):

  if ( flags & STATE_TGT_MITG_DA )
    state->target_mitigation_da_multiplier = composite_target_mitigation( state, true );

  if ( flags & STATE_TGT_MITG_TA )
    state->target_mitigation_ta_multiplier = composite_target_mitigation( state, false );

  if ( flags & STATE_TGT_ARMOR )
    state->target_armor = composite_target_armor( state );

// L5011-5015 — composite_target_mitigation: products composite_mitigation_multiplier
// and composite_mitigation_from_player_multiplier
}

double action_t::composite_target_mitigation( const action_state_t* s, bool direct ) const
{
  return s->target->composite_mitigation_multiplier( s, get_school(), direct ) *
         s->target->composite_mitigation_from_player_multiplier( s->action->player, s, get_school(), direct );
}

