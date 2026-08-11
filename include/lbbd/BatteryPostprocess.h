/*

Copyright (c) 2025, Corentin JUVIGNY

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

*/

#pragma once

#include <vector>

#include "instance.h"
#include "solution.h"

namespace lbbd
{

/// Outcome of pricing one fixed machine schedule against the battery.
struct BatteryPlan
{
   std::vector<double> demand;   ///< machine energy demand per interval, unpriced
   std::vector<double> levels;   ///< battery level at the start of each interval
   double energyCost = 0.0;               ///< grid cost with the battery used optimally
   double energyCostWithoutBattery = 0.0; ///< same schedule priced with no storage
   bool   usedLp = false;                 ///< false when the LP failed and no storage was used

   double savings() const { return energyCostWithoutBattery - energyCost; }
};

/**
 * @brief Prices a fixed machine-state schedule against the battery.
 *
 * The LBBD master reasons about a battery-free world: its z arcs carry the raw
 * grid tariff and its x columns the raw cost of running the machine. Once the
 * master has converged, the machine schedule it produced is handed here, and
 * the exact battery LP (BatteryLp) chooses the charge/discharge profile for
 * that now-fixed demand profile.
 *
 * This is deliberately a *post*-processing step and not a decomposition: the
 * battery never feeds back into where the EI tasks were placed. Two
 * consequences follow, and both matter when reading the numbers:
 *
 *   - the resulting schedule is optimal for the battery-free problem, not for
 *     the battery problem, so LBBD is exact only in the former sense;
 *   - because storage can only ever reduce the bill, the master's objective is
 *     an upper bound on the true cost -- never a lower bound on it. The MIP gap
 *     LBBD reports therefore certifies optimality for the battery-free problem
 *     alone.
 *
 * docs/BENDERS_BATTERY.md discusses the variant that closes that loop by
 * feeding LP duals back into the master as optimality cuts.
 */
class BatteryPostprocess
{
   public:
      explicit BatteryPostprocess(const Instance* ins) : ins(ins) { }

      /// Energy demand per interval implied by a machine-block schedule.
      std::vector<double> demandProfile(const std::vector<MachineBlock>& blocks) const;

      /// Runs the battery LP on that demand profile and prices the result.
      BatteryPlan operator()(const std::vector<MachineBlock>& blocks) const;

      /**
       * @brief Grid cost of a demand profile served by a given battery trace.
       *
       * Charging is paid before the charging efficiency; discharging delivers
       * `dischargingEfficiency` times what leaves the battery, and the shortfall
       * is bought from the grid. Identical accounting to
       * SolverH1::computeEnergyCost -- see the note in the .cpp about why the
       * duplication is worth removing.
       */
      static double gridCost(const Instance* ins,
                             const std::vector<double>& demand,
                             const std::vector<double>& levels);

   private:
      const Instance* ins;
};

}
