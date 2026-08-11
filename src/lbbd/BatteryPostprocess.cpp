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

#include "lbbd/BatteryPostprocess.h"

#include <fmt/base.h>

#include "BatteryLp.h"
#include "config.h"
#include "helpers.h"

namespace lbbd
{

std::vector<double> BatteryPostprocess::demandProfile(const std::vector<MachineBlock>& blocks) const
{
   std::vector<double> demand(ins->maxDuration(), 0.0);
   for (const MachineBlock& block : blocks)
   {
      const double perInterval = block.getRequiredEnergyPerTimeUnit(ins);
      LoopFrom(i, block.startTime, block.endTime + 1)
         if ( i >= 0 && i < ins->maxDuration() ) demand[i] = perInterval;
   }
   return demand;
}

double BatteryPostprocess::gridCost(const Instance* ins,
                                    const std::vector<double>& demand,
                                    const std::vector<double>& levels)
{
   // NOTE: this is byte-for-byte the accounting in SolverH1::computeEnergyCost.
   // It is duplicated here rather than shared because that one is a private
   // member of SolverH1 and the LBBD path has no business constructing a
   // heuristic solver just to price a profile. Hoisting the shared version out
   // of SolverH1 into this class would be the right clean-up -- flagged in the
   // review rather than done here, since it touches H1's numbers.
   const int h = static_cast<int>(demand.size());
   const double ef_c = ins->Battery.chargingEfficiency;
   const double ef_d = ins->Battery.dischargingEfficiency;

   double total = 0.0;
   Loop(i, h)
   {
      const double price = ins->costs[i];
      const double level = levels.empty() ? 0.0 : levels[i];
      const double next  = (i == h - 1 || levels.empty()) ? 0.0 : levels[i + 1];
      const double delta = next - level;

      if ( delta > 0 )
         total += price * (delta / ef_c);

      const double fromBattery = delta < 0 ? -delta * ef_d : 0.0;
      const double fromGrid    = demand[i] - fromBattery;
      if ( fromGrid > 0 )
         total += price * fromGrid;
   }
   return total;
}

BatteryPlan BatteryPostprocess::operator()(const std::vector<MachineBlock>& blocks) const
{
   BatteryPlan plan;
   plan.demand = demandProfile(blocks);
   plan.energyCostWithoutBattery = gridCost(ins, plan.demand, {});

   if ( ins->Battery.batteryCapacity <= 0 ) {
      plan.levels.assign(ins->maxDuration(), 0.0);
      plan.energyCost = plan.energyCostWithoutBattery;
      plan.usedLp = true;   // nothing to solve; the answer is exact
      return plan;
   }

   BatteryLp lp{ins};
   if ( auto levels = lp.solve(plan.demand) ) {
      plan.levels     = std::move(*levels);
      plan.energyCost = gridCost(ins, plan.demand, plan.levels);
      plan.usedLp     = true;
   } else {
      // Report the storage-free cost rather than guessing. Falling back to the
      // greedy peak-shaver would mean reaching into SolverH1, and quietly
      // reporting a cost the LP never certified is worse than reporting a
      // conservative one.
      fmt::println(stderr, "LBBD: battery LP failed; reporting this schedule without storage.");
      plan.levels.assign(ins->maxDuration(), 0.0);
      plan.energyCost = plan.energyCostWithoutBattery;
      plan.usedLp     = false;
   }

   return plan;
}

}
