// SPDX-License-Identifier: GPL-3.0-or-later
// Excerpted from SimulationCraft (https://github.com/simulationcraft/simc),
// (c) SimulationCraft contributors. Licensed under the GNU GPL v3 -- see
// ./LICENSE for the full text and ./README.md for the exact source path
// and commit this excerpt was taken from. This file is GPL-3.0 code kept
// separate from the rest of this repo (AGPL-3.0-or-later).

void player_t::target_mitigation( school_e, result_amount_type dmg_type, action_state_t* s )
{
  if ( s->result_amount == 0 )
    return;

  if ( debuffs.invulnerable && debuffs.invulnerable->check() )
  {
    s->result_amount = 0;
    return;
  }

  if ( dmg_type == result_amount_type::DMG_OVER_TIME )
  {
    s->result_amount *= s->target_mitigation_ta_multiplier;
  }
  else if ( dmg_type == result_amount_type::DMG_DIRECT )
  {
    s->result_amount *= s->target_mitigation_da_multiplier;

    if ( !s->action )
      return;

    // Armor
    if ( auto armor = s->target_armor )
    {
      double resist = util::calculate_armor_resist( armor, s->action->player->current.armor_coeff );
      s->result_amount *= 1.0 - resist;

      if ( sim->debug )
      {
        sim->print_debug( "{} {} damage to {} reduced by {:.7g}% from armor (armor={:.7g}, armor coeff={:.7g}).",
                          *s->action->player, *s->action, *s->target, resist * 100, armor,
                          s->action->player->current.armor_coeff );
      }
    }

    // Block and Crit Block work in the same manner as armor and are affected by the same cap
    if ( s->block_result == BLOCK_RESULT_BLOCKED )
    {
      double block_value = s->target_block_value;
      double block_resist = util::calculate_armor_resist( block_value, s->action->player->current.armor_coeff );

      block_resist = clamp( block_resist, 0.0, MAX_ARMOR_DAMAGE_REDUCTION );
      s->result_amount *= 1.0 - block_resist;

      if ( sim->debug )
      {
        sim->print_debug( "{} {} damage to {} reduced by {:.7g}% from block (block value={:.7g}, armor coeff={:.7g}).",
                          *s->action->player, *s->action, *s->target, block_resist * 100, block_value,
                          s->action->player->current.armor_coeff );
      }
    }
  }
}
