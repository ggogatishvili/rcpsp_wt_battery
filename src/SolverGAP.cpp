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

#include "SolverGAP.h"
#include "BatteryLp.h"
#include "../include/gap/EvaluatorP.h"
#include "../include/gap/MutatorP.h"
#include "../include/ga/Crossover.h"
#include "../include/ga/Terminator.h"
#include "config.h"
#include <fmt/base.h>
#include <algorithm>

#include <eo>
#include <es/eoRealInitBounded.h>
#include <eoDetTournamentSelect.h>
#include <eoEasyEA.h>
#include <eoSelectNumber.h>
#include <eoSGATransform.h>

using namespace std;


SolverGAP::SolverGAP(const Instance* const instance)
   : ins(instance)
   , H(instance->maxDuration())
   , N(instance->nbr_tasks())
   , N_EI(instance->nbr_ei_tasks())
   , chromosomeSize(instance->nbr_tasks())
   , solverH1(instance)
{
   cachedSPACESGraph = solverH1.buildSPACESGraph();
}

Solution SolverGAP::_solve()
{
   eoPop<Chromosome> pop = generateInitialPopulation();

   EvaluatorP eval(ins, *this, solverH1);
   apply<Chromosome>(eval, pop);

   Crossover xover(N, 0);
   MutatorP  mut(N);
   eoDetTournamentSelect<Chromosome> selectOne(2);
   eoSelectNumber<Chromosome>        selectMany(selectOne, Config::populationSize);
   eoSGATransform<Chromosome>        transform(xover, 1.0, mut, 1.0);
   eoPlusReplacement<Chromosome>     replace;
   Terminator terminator(static_cast<int>(Config::timeLimit), Config::stagnationLimit);

   if (Config::verbose) {
      fmt::println("\n=================================================");
      fmt::println("GAP PARAMETERS:");
      fmt::println("  Chromosome Size  : {} (priorities only)", chromosomeSize);
      fmt::println("  Population Size  : {}", Config::populationSize);
      fmt::println("  Stagnation Limit : {} generations", Config::stagnationLimit);
      fmt::println("  Phase1 Window    : {} time units", Config::phase1Window);
      fmt::println("=================================================");
      fmt::println("Starting GAP Optimization...\n");
   }

   #pragma omp parallel for default(none) shared(pop, eval)
   for (auto& chrom : pop)
      if (chrom.invalid()) eval(chrom);

   while (terminator(pop)) {
      eoPop<Chromosome> offspring;
      selectMany(pop, offspring);
      transform(offspring);

      #pragma omp parallel for default(none) shared(offspring, eval)
      for (auto& chrom : offspring)
         if (chrom.invalid()) eval(chrom);

      replace(pop, offspring);
   }

   pop.sort();
   const auto priorities = decodePriorities(pop[0]);
   const vector<int> noDelays(N_EI, 0);

   try {
      auto startTimes    = solverH1.scheduleTasks(priorities, noDelays, true, Config::phase1Window);
      auto machineBlocks = solverH1.scheduleMachineUsage(startTimes, cachedSPACESGraph);
      solverH1.optimizeMachineBlocks(machineBlocks);
      const auto energyReqs = solverH1.getEnergyRequirements(machineBlocks);

      BatteryLp battLp(ins);
      auto opt = battLp.solve(energyReqs);
      auto battLevels = opt ? std::move(*opt) : solverH1.scheduleBatteryUsage(energyReqs);

      const double tCost = solverH1.computeTardinessCost(startTimes);
      const double eCost = solverH1.computeEnergyCost(energyReqs, battLevels);
      return {ins, tCost + eCost, eCost, tCost,
              startTimes, battLevels, machineBlocks, SolutionStats::defaultStats()};
   } catch (const exception& e) {
      fmt::println(stderr, "GAP solution extraction failed ({}). Falling back to H1.", e.what());
      // H1 can throw for the same reason the extraction just did (no ordering
      // fits the horizon). Guard the fallback so the process reports an
      // infeasible solution instead of aborting with a non-zero exit status,
      // which the caller cannot distinguish from a crash.
      try {
         return solverH1();
      } catch (const exception& e2) {
         fmt::println(stderr, "GAP fallback to H1 also failed ({}). "
                              "Reporting infeasible.", e2.what());
         return Solution::infeasibleSolution(ins);
      }
   }
}

eoPop<Chromosome> SolverGAP::generateInitialPopulation() const
{
   if (Config::populationSize < 100)
      throw runtime_error(fmt::format(
         "GAP error: population size must be at least 100. Current: {}",
         Config::populationSize));

   eoPop<Chromosome> population;

   // Seed from actual H1 and H1P runs — H1P seeds carry the ordering that
   // price-aware Phase 1 naturally produces, giving the GA a warm start.
   //
   // A seeding ordering may legitimately fail to produce a schedule: Phase 1
   // is a greedy construction without backtracking, so on a tight horizon
   // SolverH1::getReadyTasks throws once a remaining task no longer fits
   // before the mandatory Off tail. The fitness evaluator already treats that
   // as an infeasible individual (fitness -BIG_M, see gap/EvaluatorP.h);
   // seeding must degrade the same way. Letting the exception escape here
   // aborted the entire run before the search started. See SolverGA.cpp for
   // the same guard and the measured impact.
   {
      SolverH1 seeder(ins);
      const vector<int> noDelays(N_EI, 0);
      int skippedSeeds = 0;

      auto seedWith = [&](const vector<double>& prios, const bool priceAware) {
         try {
            population.push_back(generateChromosomeFromSolution(
               seeder.scheduleTasks(prios, noDelays, priceAware,
                                    priceAware ? Config::phase1Window : 0)));
         } catch (const exception&) {
            // This ordering does not fit the horizon; drop the seed. The
            // population is topped up with random chromosomes below, so its
            // size is unaffected.
            ++skippedSeeds;
         }
      };

      for (auto type : { PriorityMetricType::EDD, PriorityMetricType::ERD,
                         PriorityMetricType::SPT }) {
         vector<pair<double,int>> m;
         m.reserve(N);
         for (int i = 0; i < N; i++) {
            switch (type) {
               case PriorityMetricType::EDD: m.emplace_back(ins->tasks[i].get_due_date(), i);     break;
               case PriorityMetricType::ERD: m.emplace_back(ins->tasks[i].get_release_date(), i); break;
               case PriorityMetricType::SPT: m.emplace_back(ins->getProcessingTime(i), i);        break;
               default: break;
            }
         }
         sort(m.begin(), m.end());
         vector<double> prios(N);
         for (int i = 0; i < N; i++) prios[m[i].second] = 1.0 - ((double)i / max(1, N - 1));

         seedWith(prios, false);
         seedWith(prios, true);
      }

      if (Config::verbose && skippedSeeds > 0)
         fmt::println("GAP: {} of 6 heuristic seed(s) skipped "
                      "(schedule did not fit the horizon)", skippedSeeds);
   }

   for (auto t : { PriorityMetricType::ERD, PriorityMetricType::EDD,
                   PriorityMetricType::SPT, PriorityMetricType::LPT,
                   PriorityMetricType::MAX_SUCC, PriorityMetricType::WEIGHT })
      population.push_back(generateInitialChromosome(t));

   for (int i = 0; i < (int)(0.1 * Config::populationSize); i++)
      population.push_back(generateInitialChromosome(PriorityMetricType::RANDOM));

   while ((int)population.size() < Config::populationSize)
      population.push_back(generateInitialChromosome(PriorityMetricType::RANDOM));

   return population;
}

Chromosome SolverGAP::generateInitialChromosome(PriorityMetricType type) const
{
   Chromosome chrom;
   chrom.resize(chromosomeSize);

   vector<pair<double,int>> metrics;
   metrics.reserve(N);
   for (int i = 0; i < N; i++) {
      switch (type) {
         case PriorityMetricType::ERD:      metrics.emplace_back(ins->tasks[i].get_release_date(), i);   break;
         case PriorityMetricType::EDD:      metrics.emplace_back(ins->tasks[i].get_due_date(), i);       break;
         case PriorityMetricType::SPT:      metrics.emplace_back(ins->getProcessingTime(i), i);          break;
         case PriorityMetricType::LPT:      metrics.emplace_back(-ins->getProcessingTime(i), i);         break;
         case PriorityMetricType::MAX_SUCC: metrics.emplace_back(-(double)ins->successors(i).size(), i); break;
         case PriorityMetricType::WEIGHT:   metrics.emplace_back(-ins->tasks[i].get_weight(), i);        break;
         case PriorityMetricType::RANDOM:   metrics.emplace_back(rng.uniform(), i);                      break;
      }
   }
   sort(metrics.begin(), metrics.end());
   for (int i = 0; i < N; i++)
      chrom[metrics[i].second] = 1.0 - ((double)i / max(1, N - 1));

   return chrom;
}

vector<double> SolverGAP::decodePriorities(const Chromosome& chrom) const
{
   vector<double> p(N);
   for (int t = 0; t < N; t++) p[t] = chrom[t];
   return p;
}

Chromosome SolverGAP::generateChromosomeFromSolution(const vector<int>& startTimes) const
{
   Chromosome chrom;
   chrom.resize(chromosomeSize);

   vector<pair<int,int>> order;
   order.reserve(N);
   for (int t = 0; t < N; t++) order.emplace_back(startTimes[t], t);
   sort(order.begin(), order.end());
   for (int rank = 0; rank < N; rank++)
      chrom[order[rank].second] = 1.0 - ((double)rank / max(1, N - 1));

   return chrom;
}