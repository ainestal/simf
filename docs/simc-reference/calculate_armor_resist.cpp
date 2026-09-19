// SPDX-License-Identifier: GPL-3.0-or-later
// Excerpted from SimulationCraft (https://github.com/simulationcraft/simc),
// (c) SimulationCraft contributors. Licensed under the GNU GPL v3 -- see
// ./LICENSE for the full text and ./README.md for the exact source path
// and commit this excerpt was taken from. This file is GPL-3.0 code kept
// separate from the rest of this repo (AGPL-3.0-or-later).

double calculate_armor_resist( double armor, double armor_coeff, double multipler )
{
  double resist = armor / ( armor + armor_coeff );
  resist *= multipler;
  resist = clamp( resist, 0.0, MAX_ARMOR_DAMAGE_REDUCTION );

  return resist;
}
} // namespace util
