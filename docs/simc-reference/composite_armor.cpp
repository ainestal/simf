// SPDX-License-Identifier: GPL-3.0-or-later
// Excerpted from SimulationCraft (https://github.com/simulationcraft/simc),
// (c) SimulationCraft contributors. Licensed under the GNU GPL v3 -- see
// ./LICENSE for the full text and ./README.md for the exact source path
// and commit this excerpt was taken from. This file is GPL-3.0 code kept
// separate from the rest of this repo (AGPL-3.0-or-later).

double player_t::composite_armor() const
{
  double a = current.stats.armor;

  a *= composite_base_armor_multiplier();

  a += cache.bonus_armor();

  // "Modify Armor%" effects affect all armor multiplicatively
  // TODO: What constitutes "bonus armor" and "base armor" in terms of client data?
  a *= composite_armor_multiplier();

  return a;
}

double player_t::composite_base_armor_multiplier() const
{
  double a = current.base_armor_multiplier;

  if ( meta_gem == META_AUSTERE_PRIMAL )
  {
    a += 0.02;
  }

  return a;
}

double player_t::composite_armor_multiplier() const
{
  double a = current.armor_multiplier;

  return a;
}
