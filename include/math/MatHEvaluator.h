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
#include "../SolverMatH.h"
#include "../SolverH1.h"
#include "../SpacesMilpDecoder.h"
#include "../config.h"
#include "../helpers.h"

// Fast H1-based evaluator used for the bulk of the population.
// Decodes the activity-list via H1's task scheduler, schedules machine
// states on the cached SPACES graph, then runs Phase 3 (battery).
class MatHH1Evaluator : public eoEvalFunc<ActivityList> {
public:
   MatHH1Evaluator(const Instance* ins, SolverH1& h1,
                   const std::vector<std::vector<std::vector<Edge>>>& spacesGraph)
      : ins(ins), N(ins->nbr_tasks()), h1(h1), spacesGraph(spacesGraph)
      , startTime(std::chrono::steady_clock::now()) {}

   void operator()(ActivityList& al) override {
      using namespace std::chrono;
      if (duration_cast<seconds>(steady_clock::now() - startTime).count() >= Config::timeLimit) {
         al.fitness(-BIG_M);
         return;
      }
      try {
         // Phase 1: schedule tasks in the activity-list order by overriding priorities
         // so the permutation defines the scheduling order directly.
         std::vector<double> priorities(N);
         for (int pos = 0; pos < N; ++pos)
            priorities[al[pos]] = 1.0 - static_cast<double>(pos) / (N - 1);

         const std::vector<int> noDelays(ins->nbr_ei_tasks(), 0);
         const auto startTimes     = h1.scheduleTasks(priorities, noDelays);
         auto       machineBlocks  = h1.scheduleMachineUsage(startTimes, spacesGraph);
         h1.optimizeMachineBlocks(machineBlocks);
         const auto energyReqs     = h1.getEnergyRequirements(machineBlocks);
         const auto battLevels     = h1.scheduleBatteryUsage(energyReqs);
         const double cost         = h1.computeTardinessCost(startTimes)
                                   + h1.computeEnergyCost(energyReqs, battLevels);
         al.fitness(-cost);
      } catch (...) {
         al.fitness(-BIG_M);
      }
   }

private:
   const Instance* ins;
   const int N;
   SolverH1& h1;
   const std::vector<std::vector<std::vector<Edge>>>& spacesGraph;
   std::chrono::steady_clock::time_point startTime;
};

// Full MILP-based evaluator for elite individuals.
// H1 fixes NI task start times; the MILP re-optimises only EI task placement
// and the machine-state schedule jointly.
class MatHMilpEvaluator : public eoEvalFunc<ActivityList> {
public:
   MatHMilpEvaluator(const Instance* ins, SolverH1& h1, SpacesMilpDecoder& decoder,
                     const std::vector<std::vector<std::vector<Edge>>>& spacesGraph)
      : ins(ins), N(ins->nbr_tasks()), h1(h1), decoder(decoder)
      , spacesGraph(spacesGraph)
      , startTime(std::chrono::steady_clock::now()) {}

   void operator()(ActivityList& al) override {
      using namespace std::chrono;
      if (duration_cast<seconds>(steady_clock::now() - startTime).count() >= Config::timeLimit) {
         al.fitness(-BIG_M);
         return;
      }
      try {
         // Compute H1 start times (NI fixed, EI as warm start for the MILP).
         std::vector<double> priorities(N);
         for (int pos = 0; pos < N; ++pos)
            priorities[al[pos]] = 1.0 - static_cast<double>(pos) / (N - 1);
         const std::vector<int> noDelays(ins->nbr_ei_tasks(), 0);
         const auto h1Times = h1.scheduleTasks(priorities, noDelays);

         const auto res      = decoder.solve(h1Times);
         const auto battLvls = h1.scheduleBatteryUsage(res.machineEnergyDemand);
         const double cost   = h1.computeTardinessCost(res.startTimes)
                             + h1.computeEnergyCost(res.machineEnergyDemand, battLvls);
         al.fitness(-cost);
      } catch (...) {
         // leave fitness unchanged on MILP failure
      }
   }

private:
   const Instance* ins;
   const int N;
   SolverH1& h1;
   SpacesMilpDecoder& decoder;
   const std::vector<std::vector<std::vector<Edge>>>& spacesGraph;
   std::chrono::steady_clock::time_point startTime;
};