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

#include "SolverMatH.h"
#include "config.h"
#include "math/ActivityListXover.h"
#include "math/ActivityListMutator.h"
#include "math/MatHEvaluator.h"
#include "ga/Terminator.h"

#include <eo>
#include <eoDetTournamentSelect.h>
#include <eoSelectNumber.h>
#include <eoSGATransform.h>
#include <utils/eoRNG.h>
#include <fmt/base.h>
#include <algorithm>
#include <queue>
#include <stdexcept>

using namespace std;

// ── Constructor ──────────────────────────────────────────────────────────────

SolverMatH::SolverMatH(const Instance* ins)
   : ins(ins)
   , H(ins->maxDuration())
   , N(ins->nbr_tasks())
   , N_EI(ins->nbr_ei_tasks())
   , solverH1(ins)
   , decoder(ins)
   , reach(computeReach())
{}

// ── Transitive closure ───────────────────────────────────────────────────────

vector<vector<bool>> SolverMatH::computeReach() const
{
   // Floyd-Warshall on the precedence DAG: O(N^3), fine for N ≤ a few hundred.
   vector<vector<bool>> r(N, vector<bool>(N, false));
   for (int t = 0; t < N; ++t)
      for (int s : ins->successors(t))
         r[t][s] = true;

   for (int k = 0; k < N; ++k)
      for (int i = 0; i < N; ++i)
         if (r[i][k])
            for (int j = 0; j < N; ++j)
               if (r[k][j]) r[i][j] = true;

   return r;
}

// ── Initial population ───────────────────────────────────────────────────────

ActivityList SolverMatH::generateInitialChromosome(ActivityMetricType type) const
{
   // Compute metric value per task.
   vector<pair<double,int>> metrics(N);
   for (int t = 0; t < N; ++t) {
      double m = 0.0;
      switch (type) {
         case ActivityMetricType::ERD:      m =  ins->tasks[t].get_release_date();          break;
         case ActivityMetricType::EDD:      m =  ins->tasks[t].get_due_date();              break;
         case ActivityMetricType::SPT:      m =  ins->getProcessingTime(t);                 break;
         case ActivityMetricType::LPT:      m = -ins->getProcessingTime(t);                 break;
         case ActivityMetricType::MAX_SUCC: m = -static_cast<double>(ins->successors(t).size()); break;
         case ActivityMetricType::WEIGHT:   m = -ins->tasks[t].get_weight();                break;
         case ActivityMetricType::RANDOM:   m =  rng.uniform();                             break;
      }
      metrics[t] = {m, t};
   }
   sort(metrics.begin(), metrics.end());

   // Repair to topological order using Kahn's algorithm with metric as priority.
   // Tasks that are "better" (lower metric index) are scheduled first when ready.
   vector<int> metricRank(N);
   for (int i = 0; i < N; ++i) metricRank[metrics[i].second] = i;

   vector<int> inDeg(N, 0);
   for (int t = 0; t < N; ++t)
      for (int s : ins->successors(t))
         inDeg[s]++;

   // Min-priority queue: lower rank (= better metric) comes first.
   auto cmp = [&](int a, int b){ return metricRank[a] > metricRank[b]; };
   priority_queue<int, vector<int>, decltype(cmp)> pq(cmp);
   for (int t = 0; t < N; ++t)
      if (inDeg[t] == 0) pq.push(t);

   ActivityList al;
   al.resize(N);
   for (int pos = 0; pos < N; ++pos) {
      const int t = pq.top(); pq.pop();
      al[pos] = t;
      for (int s : ins->successors(t))
         if (--inDeg[s] == 0) pq.push(s);
   }
   return al;
}

eoPop<ActivityList> SolverMatH::generateInitialPopulation() const
{
   if (Config::populationSize < 10)
      throw runtime_error("MatH: population size must be at least 10");

   eoPop<ActivityList> pop;

   // One chromosome per deterministic heuristic ordering.
   for (auto type : { ActivityMetricType::ERD, ActivityMetricType::EDD,
                      ActivityMetricType::SPT, ActivityMetricType::LPT,
                      ActivityMetricType::MAX_SUCC, ActivityMetricType::WEIGHT })
      pop.push_back(generateInitialChromosome(type));

   // Fill remainder with random topological orders.
   while (static_cast<int>(pop.size()) < Config::populationSize)
      pop.push_back(generateInitialChromosome(ActivityMetricType::RANDOM));

   return pop;
}

// ── Main GA loop ─────────────────────────────────────────────────────────────

Solution SolverMatH::_solve()
{
   // Build cached SPACES graph for H1 evaluator.
   const auto spacesGraph = solverH1.buildSPACESGraph();

   eoPop<ActivityList> pop = generateInitialPopulation();

   // Two evaluators: fast H1 for the bulk, full MILP for elite individuals.
   MatHH1Evaluator   h1Eval  (ins, solverH1, spacesGraph);
   MatHMilpEvaluator milpEval(ins, solverH1, decoder, spacesGraph);

   // Evaluate initial population with H1.
   apply<ActivityList>(h1Eval, pop);

   ActivityListXover    xover(N);
   ActivityListMutator  mut(N, reach);
   eoDetTournamentSelect<ActivityList> selectOne(2);
   eoSelectNumber<ActivityList>        selectMany(selectOne, Config::populationSize);
   eoSGATransform<ActivityList>        transform(xover, 1.0, mut, 1.0);
   eoPlusReplacement<ActivityList>     replace;
   TerminatorT<ActivityList>           terminator(static_cast<int>(Config::timeLimit),
                                                  Config::stagnationLimit);

   if (Config::verbose) {
      fmt::println("\n=================================================");
      fmt::println("MATH PARAMETERS:");
      fmt::println("  Population Size  : {}", Config::populationSize);
      fmt::println("  Stagnation Limit : {} generations", Config::stagnationLimit);
      fmt::println("  Elite Ratio      : {:.0f}%", Config::mathEliteRatio * 100.0);
      fmt::println("  MILP Time Limit  : {}s per solve", Config::mathMilpTimeLimit);
      fmt::println("=================================================\n");
   }

   while (terminator(pop)) {
      eoPop<ActivityList> offspring;
      selectMany(pop, offspring);
      transform(offspring);

      // Evaluate offspring with fast H1.
      for (auto& al : offspring)
         if (al.invalid()) h1Eval(al);

      replace(pop, offspring);

      // Re-evaluate the elite fraction with the full MILP decoder.
      if (Config::mathEliteRatio > 0.0) {
         pop.sort();
         const int eliteSize = max(1, static_cast<int>(std::round(
            Config::mathEliteRatio * static_cast<double>(pop.size()))));
         for (int e = 0; e < eliteSize; ++e)
            milpEval(pop[e]);
      }
   }

   // ── Extract best solution ────────────────────────────────────────────────
   // Run H1 to get NI-fixed start times, then run the MILP decoder to
   // optimally place EI tasks and the machine-state schedule.

   pop.sort();
   const ActivityList& best = pop[0];

   vector<double> priorities(N);
   for (int pos = 0; pos < N; ++pos)
      priorities[best[pos]] = 1.0 - static_cast<double>(pos) / (N - 1);

   const vector<int> noDelays(ins->nbr_ei_tasks(), 0);
   const auto h1Times = solverH1.scheduleTasks(priorities, noDelays);
   const auto res     = decoder.solve(h1Times);
   const auto battLvls = solverH1.scheduleBatteryUsage(res.machineEnergyDemand);
   const double tCost  = solverH1.computeTardinessCost(res.startTimes);
   const double eCost  = solverH1.computeEnergyCost(res.machineEnergyDemand, battLvls);

   return { ins, tCost + eCost, eCost, tCost,
            res.startTimes, battLvls, res.machineBlocks,
            SolutionStats::defaultStats() };
}