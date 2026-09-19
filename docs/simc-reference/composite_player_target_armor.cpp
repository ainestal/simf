// SPDX-License-Identifier: GPL-3.0-or-later
// Excerpted from SimulationCraft (https://github.com/simulationcraft/simc),
// (c) SimulationCraft contributors. Licensed under the GNU GPL v3 -- see
// ./LICENSE for the full text and ./README.md for the exact source path
// and commit this excerpt was taken from. This file is GPL-3.0 code kept
// separate from the rest of this repo (AGPL-3.0-or-later).

double player_t::composite_player_target_armor( player_t* t ) const
{
  double a = t->cache.armor();

  a *= current.armor_penetration;

  return a;
}

