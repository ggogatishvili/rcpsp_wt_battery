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

#include "lbbd/DecompositionReport.h"

#include <algorithm>
#include <cmath>

#include <fmt/base.h>
#include <fmt/format.h>

#include "helpers.h"

namespace lbbd
{

double weightedTardiness(const Instance* ins, const std::vector<int>& startTimes)
{
   double total = 0.0;
   Loop(u, ins->nbr_tasks())
   {
      if ( u >= static_cast<int>(startTimes.size()) || startTimes[u] < 0 ) continue;
      const int completion = startTimes[u] + ins->getProcessingTime(u) - 1;
      const int due        = ins->tasks[u].get_due_date();
      if ( completion > due )
         total += ins->tasks[u].get_weight() * (completion - due);
   }
   return total;
}

void checkTiling(const std::vector<MachineBlock>& blocks, const int horizon, std::string_view who)
{
   std::vector<int> cover(horizon, 0);
   for (const MachineBlock& b : blocks)
      LoopFrom(i, std::max(0, b.startTime), std::min(horizon, b.endTime + 1)) ++cover[i];

   const auto bad = std::ranges::find_if(cover, [](const int c) { return c != 1; });
   if ( bad != cover.end() )
      fmt::println(stderr, "{}: machine timeline does not tile the horizon "
                           "(interval {} covered {} times)",
                   who, std::distance(cover.begin(), bad), *bad);
}

void checkEnergyAgreement(const double masterEnergy, const double rebuiltEnergy, std::string_view who)
{
   if ( std::abs(masterEnergy - rebuiltEnergy) > 1e-4 )
      fmt::println(stderr, "{}: master energy {:.6f} != reconstructed energy {:.6f}; "
                           "the timeline model and the rebuilt schedule disagree.",
                   who, masterEnergy, rebuiltEnergy);
}

void attachDiagnostics(Solution& solution,
                       const CutStatistics& stats,
                       const BatteryPlan& plan,
                       const double bound,
                       const bool boundIsBatteryAware)
{
   for (const auto& [key, value] : stats.asDiagnostics())
      solution.setDiagnostic(key, value);

   solution.setDiagnostic("energy_cost_no_battery", plan.energyCostWithoutBattery);
   solution.setDiagnostic("battery_saving", plan.savings());
   solution.setDiagnostic("battery_lp_ok", plan.usedLp ? 1.0 : 0.0);
   solution.setDiagnostic("bound", bound);
   solution.setDiagnostic("bound_is_battery_aware", boundIsBatteryAware ? 1.0 : 0.0);
}

void report(std::string_view who,
            const CutStatistics& stats,
            const BatteryPlan& plan,
            const double tardinessCost)
{
   fmt::println("{}: {} subproblems, {} feasibility cuts, {} optimality cuts, "
                "{} battery cuts ({} at nodes), {} inconclusive",
                who, stats.subproblems.load(), stats.feasibilityCuts.load(),
                stats.optimalityCuts.load(), stats.batteryCuts.load(),
                stats.batteryNodeCuts.load(), stats.inconclusive.load());
   fmt::println("{}: energy {:.3f} -> {:.3f} with storage (saved {:.3f}), tardiness {:.3f}",
                who, plan.energyCostWithoutBattery, plan.energyCost, plan.savings(), tardinessCost);
   if ( stats.inconclusive.load() > 0 )
      fmt::println(stderr, "{}: {} subproblem solve(s) returned no verdict; the reported gap no longer "
                           "certifies optimality.", who, stats.inconclusive.load());
}

}
