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

#include <eo>
#include <eoEvalFunc.h>
#include <chrono>
#include "../SolverGAP.h"
#include "../SolverH1.h"
#include "../BatteryLp.h"
#include "../config.h"
#include "../helpers.h"

using namespace std;

// GA evaluator for GAP: always uses price-aware Phase 1 and LP Phase 3.
class EvaluatorP : public eoEvalFunc<Chromosome> {
public:
   inline EvaluatorP(const Instance* ins, SolverGAP& solverGAP, SolverH1& solverH1)
      : ins(ins), N(ins->nbr_tasks()), N_EI(ins->nbr_ei_tasks()),
        solverGAP(solverGAP), solverH1(solverH1),
        startTime(chrono::steady_clock::now()) {}

   inline void operator()(Chromosome& chrom) override {
      auto now = chrono::steady_clock::now();
      if (chrono::duration_cast<chrono::seconds>(now - startTime).count()
          > Config::timeLimit) {
         chrom.fitness(-BIG_M);
         return;
      }

      const auto priorities = solverGAP.decodePriorities(chrom);

      try {
         const auto startTimes = solverH1.scheduleTasks(
            priorities, vector<int>(N_EI, 0), true, Config::phase1Window);
         auto machineBlocks = solverH1.scheduleMachineUsage(
            startTimes, solverGAP.cachedSPACESGraph);
         solverH1.optimizeMachineBlocks(machineBlocks);
         const auto energyReqs = solverH1.getEnergyRequirements(machineBlocks);

         thread_local std::optional<BatteryLp> tlBattLp;
         thread_local const Instance* tlIns = nullptr;
         if (tlIns != ins) { tlBattLp.emplace(ins); tlIns = ins; }
         auto opt = tlBattLp->solve(energyReqs);
         const auto battLevels = opt ? std::move(*opt)
                                     : solverH1.scheduleBatteryUsage(energyReqs);

         const double tCost = solverH1.computeTardinessCost(startTimes);
         const double eCost = solverH1.computeEnergyCost(energyReqs, battLevels);
         chrom.fitness(-(tCost + eCost));
      } catch (...) {
         chrom.fitness(-BIG_M);
      }
   }

private:
   const Instance* ins;
   const int N;
   const int N_EI;
   SolverGAP& solverGAP;
   SolverH1&  solverH1;
   chrono::steady_clock::time_point startTime;
};