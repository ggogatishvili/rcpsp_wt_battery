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

#include "SolverH1P.h"
#include "SolverH1.h"
#include "BatteryLp.h"
#include "config.h"
#include <fmt/base.h>

Solution SolverH1P::solve()
{
   try {
      SolverH1 h1(ins);
      BatteryLp battLp(ins);

      // Phase 1: EDD priorities with price-aware EI delay
      const int wMax = Config::phase1Window;
      auto startTimes = h1.scheduleTasks(true, wMax);

      // Phase 2
      auto spacesGraph  = h1.buildSPACESGraph();
      auto machineBlks  = h1.scheduleMachineUsage(startTimes, spacesGraph);
      h1.optimizeMachineBlocks(machineBlks);

      // Phase 3: exact LP battery scheduling
      const auto energyReqs = h1.getEnergyRequirements(machineBlks);
      auto opt = battLp.solve(energyReqs);
      auto battLevels = opt ? std::move(*opt) : h1.scheduleBatteryUsage(energyReqs);

      const double tCost = h1.computeTardinessCost(startTimes);
      const double eCost = h1.computeEnergyCost(energyReqs, battLevels);
      return {ins, tCost + eCost, eCost, tCost,
              startTimes, battLevels, machineBlks, SolutionStats::defaultStats()};
   } catch (const std::exception& e) {
      fmt::println(stderr, "SolverH1P failed ({}), falling back to H1.", e.what());
      return SolverH1(ins)();
   }
}