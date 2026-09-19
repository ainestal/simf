// SPDX-License-Identifier: GPL-3.0-or-later
// Excerpted from SimulationCraft (https://github.com/simulationcraft/simc),
// (c) SimulationCraft contributors. Licensed under the GNU GPL v3 -- see
// ./LICENSE for the full text and ./README.md for the exact source path
// and commit this excerpt was taken from. This file is GPL-3.0 code kept
// separate from the rest of this repo (AGPL-3.0-or-later).

double player_t::composite_mitigation_multiplier( const action_state_t* s, school_e school, bool ) const
{
  double m = 1.0;

  if ( !is_enemy() && type != HEALING_ENEMY )
  {
    if ( !is_pet() )
    {
      if ( buffs.stoneform && buffs.stoneform->up() && school == SCHOOL_PHYSICAL )
        m *= 1.0 + buffs.stoneform->check_value();

      if ( buffs.elemental_chaos_earth && buffs.elemental_chaos_earth->up() )
        m *= 1.0 + buffs.elemental_chaos_earth->check_value();

      if ( buffs.pain_suppression && buffs.pain_suppression->up() )
        m *= 1.0 + buffs.pain_suppression->check_value();
    }

    m *= 1.0 - cache.mitigation_versatility();

    if ( sim->debug )
    {
      sim->print_debug( "{} {} damage to {} reduced by {:.7g}% from versatility.", *s->action->player, *s->action,
                        *s->target, cache.mitigation_versatility() * 100 );
    }

    if ( s->action->is_aoe() )
      m *= 1.0 - cache.avoidance();
  }

  return m;
}
