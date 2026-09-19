// SPDX-License-Identifier: GPL-3.0-or-later
// Excerpted from SimulationCraft (https://github.com/simulationcraft/simc),
// (c) SimulationCraft contributors. Licensed under the GNU GPL v3 -- see
// ./LICENSE for the full text and ./README.md for the exact source path
// and commit this excerpt was taken from. This file is GPL-3.0 code kept
// separate from the rest of this repo (AGPL-3.0-or-later).

// warrior_t::target_mitigation ============================================

void warrior_t::target_mitigation( school_e school, result_amount_type dtype, action_state_t* s )
{
  parse_player_effects_t::target_mitigation( school, dtype, s );

  if ( s->block_result == BLOCK_RESULT_CRIT_BLOCKED )
  {
    double block_value = s->target_block_value;
    double block_resist = util::calculate_armor_resist( block_value, s->action->player->current.armor_coeff, 2.0 );

    s->result_amount *= 1.0 - block_resist;

    if ( sim->debug )
    {
      sim->print_debug(
        "{} {} damage to {} reduced by {:.7g}% from crit block (block value={:.7g}, armor coeff={:.7g}).",
        *s->action->player, *s->action, *s->target, block_resist * 100, block_value,
        s->action->player->current.armor_coeff );
    }
  }
}
