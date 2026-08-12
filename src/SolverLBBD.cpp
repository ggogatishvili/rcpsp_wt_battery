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

#include "SolverLBBD.h"

#include <algorithm>
#include <optional>
#include <set>

#include <fmt/base.h>
#include <fmt/format.h>

#include "helpers.h"
#include "lbbd/BatteryPostprocess.h"
#include "lbbd/DecompositionReport.h"
#include "SolverH1.h"

namespace
{
   /// One candidate machine gap: the cheapest state path covering intervals
   /// [a, b-1]. `a` is the boundary an EI task (or the horizon start) leaves
   /// behind, `b` the boundary the next EI task (or the horizon end) needs.
   struct GapArc
   {
      int a;
      int b;
      GRBVar var;
   };
}

SolverLBBD::SolverLBBD(const Instance* instance, const Config::ResolutionMethod method)
   : ins(instance)
   , method(method)
   , H(instance->maxDuration())
{ }

void SolverLBBD::Callback::callback()
{
   if ( where != GRB_CB_MIPSOL )
      return;

   try {
      const std::vector<std::pair<int, int>> assignment =
         placement->readAssignment([this](const GRBVar& v) { return getSolution(v); });
      if ( !placement->isComplete(assignment) )
         return;   // refuse to cut on a half-read solution

      stats.subproblems.fetch_add(1, std::memory_order_relaxed);

      // Conflict refinement is exactly what separates the two methods: LBBD
      // asks for a small conflicting subset, NoGoodCuts does not and pays for
      // it with a cut that only excludes one point of the search space.
      const bool refine = (method == Config::ResolutionMethod::LBBD);
      const lbbd::SubproblemResult result = subproblem->solve(assignment, refine);

      const auto noGood = [&](const std::vector<std::pair<int, int>>& subset) {
         GRBLinExpr expr = 0;
         for (const auto& [j, s] : subset) expr += placement->var(j, s);
         addLazy(expr <= static_cast<double>(subset.size()) - 1.0);
      };

      switch ( result.status )
      {
         case lbbd::SubproblemStatus::Optimal:
         case lbbd::SubproblemStatus::Feasible:
         {
            // Strengthened optimality cut.
            //
            //   q >= B - (B - L) * sum_{j in EI} (1 - x[j][s_j])
            //
            // with B a valid lower bound on the subproblem optimum under THIS
            // fixing, and L = closure->unavoidableTardiness(), a lower bound
            // valid under EVERY fixing (it drops the resource constraints, so
            // no master decision can invalidate it). Writing k for the number
            // of EI tasks that move away from the incumbent:
            //
            //   k = 0 : the right-hand side is B, by definition a lower bound
            //           on what this fixing can achieve;
            //   k >= 1: the right-hand side is B - k(B-L) <= L, and L lower
            //           bounds every schedule whatsoever.
            //
            // So the cut never removes a better solution. Using
            // result.weightedTardiness instead of result.lowerBound would break
            // the k = 0 case whenever the subproblem stopped on its time limit,
            // which is why the backends report the two separately.
            const double floorLb = closure->unavoidableTardiness();
            const double bound   = std::max(result.lowerBound, floorLb);

            if ( bound > floorLb + MyEPS ) {
               GRBLinExpr moved = 0;
               for (const auto& [j, s] : assignment) moved += (1.0 - placement->var(j, s));
               addLazy(*q >= bound - (bound - floorLb) * moved);
               stats.optimalityCuts.fetch_add(1, std::memory_order_relaxed);
            }
            if ( result.status == lbbd::SubproblemStatus::Feasible )
               stats.inconclusive.fetch_add(1, std::memory_order_relaxed);
            break;
         }

         case lbbd::SubproblemStatus::Infeasible:
         {
            const auto& subset = result.infeasibilitySet.empty() ? assignment : result.infeasibilitySet;
            noGood(subset);
            stats.feasibilityCuts.fetch_add(1, std::memory_order_relaxed);
            stats.cumulMifs.fetch_add(static_cast<unsigned>(subset.size()), std::memory_order_relaxed);
            break;
         }

         default:
            // No verdict. Excluding the assignment keeps the search moving but
            // may discard a feasible point, so the run stops being certifiably
            // optimal -- surfaced through CutStatistics::inconclusive.
            noGood(assignment);
            stats.feasibilityCuts.fetch_add(1, std::memory_order_relaxed);
            stats.inconclusive.fetch_add(1, std::memory_order_relaxed);
            break;
      }
   } catch ( const GRBException& e ) {
      fmt::println(stderr, "LBBD callback: Gurobi error {}: {}", e.getErrorCode(), e.getMessage());
   } catch ( const std::exception& e ) {
      fmt::println(stderr, "LBBD callback: {}", e.what());
   }
}

std::vector<MachineBlock> SolverLBBD::assembleMachineBlocks(
   const lbbd::SwitchingGraph& graph,
   const std::vector<std::pair<int, int>>& eiAssignment,
   const std::vector<std::pair<int, int>>& gapArcs) const
{
   std::vector<MachineBlock> blocks;

   for (const auto& [j, s] : eiAssignment)
      blocks.push_back({ s, State::Proc, s + ins->getProcessingTime(j) - 1, State::Proc });

   for (const auto& [a, b] : gapArcs)
   {
      const std::vector<MachineBlock> path = graph.path(a, b);
      blocks.insert(blocks.end(), path.begin(), path.end());
   }

   std::ranges::sort(blocks, {}, &MachineBlock::startTime);

   // A gap that begins by staying in Proc produces a Proc block butting
   // straight against the EI task that preceded it; merge those so the
   // schedule reads the way the visualiser expects.
   std::vector<MachineBlock> merged;
   for (const MachineBlock& b : blocks)
   {
      if ( !merged.empty() && !merged.back().isTransition() && !b.isTransition()
           && merged.back().startState == b.startState
           && merged.back().endTime + 1 == b.startTime )
         merged.back().endTime = b.endTime;
      else
         merged.push_back(b);
   }

   lbbd::checkTiling(merged, H, "LBBD");
   return merged;
}

Solution SolverLBBD::_solve()
{
   if ( ins->nbr_ei_tasks() == 0 ) {
      fmt::println(stderr, "LBBD: instance has no energy-intensive task; nothing to decompose.");
      return Solution::infeasibleSolution(ins);
   }
   if ( ins->resource_capacities[0] != 1 )
      fmt::println(stderr, "LBBD: resource 0 has capacity {}, but the machine-state model assumes a single "
                           "energy-intensive machine. Results will not be meaningful.",
                   ins->resource_capacities[0]);

   const lbbd::PrecedenceClosure closure{ins};
   lbbd::EiPlacement placement{ins, &closure};
   if ( placement.infeasible() ) {
      fmt::println(stderr, "LBBD: EI task {} has an empty start-time window; instance is infeasible.",
                   placement.infeasibleTask());
      return Solution::infeasibleSolution(ins);
   }

   // Boundaries a gap can start at (right after an EI task, or the horizon
   // start) and end at (right before an EI task, or the horizon end). Only
   // these rows of the c* matrix are ever consulted, which is usually far fewer
   // than all H of them.
   std::set<int> gapStartSet{0};
   std::set<int> gapEndSet{H};
   for (const auto& [j, range] : placement.windows())
   {
      const int p = ins->getProcessingTime(j);
      LoopFrom(s, range.first, range.second + 1) { gapStartSet.insert(s + p); gapEndSet.insert(s); }
   }

   const lbbd::SwitchingGraph graph{ins, std::vector<int>(gapStartSet.cbegin(), gapStartSet.cend())};
   const lbbd::RcpspSubproblem subproblem{ins, &closure};

   if ( Config::verbose )
      fmt::println("LBBD: {} EI tasks, horizon {}, subproblem backend '{}'",
                   ins->nbr_ei_tasks(), H, lbbd::RcpspSubproblem::backendName());

   GRBModel model{Config::gurobiEnv()};

   // ── Variables ────────────────────────────────────────────────────────────

   placement.addVariables(model, [&graph](const int task, const int start) {
      return graph.procCost(task, start);
   });

   std::vector<GapArc> arcs;
   std::map<int, std::vector<int>> arcsOut;   // boundary -> indices of arcs leaving it
   std::map<int, std::vector<int>> arcsIn;    // boundary -> indices of arcs arriving at it
   for (const int a : gapStartSet) for (const int b : gapEndSet)
   {
      if ( b <= a || !graph.hasSwitching(a, b) ) continue;
      arcs.push_back({ a, b, model.addVar(0.0, 1.0, graph.switchingDistance(a, b), GRB_BINARY,
                                          fmt::format("z_{}_{}", a, b)) });
      arcsOut[a].push_back(static_cast<int>(arcs.size()) - 1);
      arcsIn[b].push_back(static_cast<int>(arcs.size()) - 1);
   }

   GRBVar q = model.addVar(closure.unavoidableTardiness(), GRB_INFINITY, 1.0, GRB_CONTINUOUS, "q");
   model.update();

   // ── Machine timeline as a unit flow ──────────────────────────────────────
   //
   // The original master states "every interval is covered by exactly one EI
   // task or exactly one switching arc", which costs O(h^3) terms to write down
   // and is the practical size limit of that formulation. The same set of
   // solutions is described by a unit flow through the boundary graph: leave
   // boundary 0, arrive at boundary H, and at every boundary in between what
   // comes in goes out. Arcs are the switching arcs and the EI task blocks, and
   // because both cover contiguous interval ranges that meet at boundaries, a
   // 0-to-H path tiles [0, H) exactly once. Writing it costs O(#arcs) instead,
   // and the flow structure is far stronger in the LP relaxation.

   {
      // No EI task can start at boundary 0: instance.cpp rejects transition
      // durations below 1, so the earliest Proc interval is at least 2.
      GRBLinExpr out = 0;
      for (const int idx : arcsOut[0]) out += arcs[idx].var;
      model.addConstr(out == 1.0, "flow_source");

      GRBLinExpr in = 0;
      for (const int idx : arcsIn[H]) in += arcs[idx].var;
      model.addConstr(in == 1.0, "flow_sink");
   }

   std::set<int> boundaries;
   boundaries.insert(gapStartSet.cbegin(), gapStartSet.cend());
   boundaries.insert(gapEndSet.cbegin(), gapEndSet.cend());
   for (const int t : boundaries)
   {
      if ( t == 0 || t == H ) continue;

      GRBLinExpr in = 0, out = 0;
      for (const int idx : arcsIn[t])  in  += arcs[idx].var;
      for (const int idx : arcsOut[t]) out += arcs[idx].var;
      for (const auto& [j, range] : placement.windows())
      {
         const int endBoundary = t - ins->getProcessingTime(j);
         if ( placement.contains(j, endBoundary) ) in  += placement.var(j, endBoundary);
         if ( placement.contains(j, t) )           out += placement.var(j, t);
      }
      model.addConstr(in == out, fmt::format("flow_{}", t));
   }

   placement.addStructuralConstraints(model);
   placement.addTardinessRelaxation(model, q);

   // ── Warm start ───────────────────────────────────────────────────────────

   std::optional<Solution> warmStart;
   if ( Config::lbbdWarmStart ) {
      Solution h1 = SolverH1(ins).solve();
      // By value: h1 is moved into warmStart below, and a reference into it
      // would dangle the moment that happens.
      const std::vector<int> starts = h1.isInfeasible() ? std::vector<int>{} : h1.getTaskAssignments();

      if ( !starts.empty() && placement.applyWarmStart(starts) ) {
         std::vector<std::pair<int, int>> ei;
         for (const auto& [j, range] : placement.windows()) ei.emplace_back(j, starts[j]);
         std::ranges::sort(ei, {}, [](const auto& e) { return e.second; });

         std::vector<std::pair<int, int>> gaps;
         bool usable = true;
         int cursor = 0;
         for (const auto& [j, s] : ei)
         {
            if ( cursor < s ) gaps.emplace_back(cursor, s);
            else if ( cursor > s ) { usable = false; break; }
            cursor = s + ins->getProcessingTime(j);
         }
         if ( usable && cursor < H ) gaps.emplace_back(cursor, H);
         for (const auto& g : gaps)
            if ( !graph.hasSwitching(g.first, g.second) ) { usable = false; break; }

         if ( usable ) {
            const std::set<std::pair<int, int>> chosen(gaps.cbegin(), gaps.cend());
            for (GapArc& arc : arcs)
               arc.var.set(GRB_DoubleAttr_Start, chosen.contains({arc.a, arc.b}) ? 1.0 : 0.0);
            q.set(GRB_DoubleAttr_Start, h1.getTardinessCost());
            warmStart = std::move(h1);
            model.update();
         } else if ( Config::verbose ) {
            fmt::println("LBBD: H1 gaps are not realisable in the switching graph; starting cold.");
         }
      } else if ( Config::verbose ) {
         fmt::println("LBBD: H1 schedule does not fit the master's EI windows; starting cold.");
      }
   }

   // ── Solve ────────────────────────────────────────────────────────────────

   Callback cb{ins, &closure, &subproblem, &placement, &q, method};
   model.setCallback(&cb);

   model.set(GRB_IntParam_LazyConstraints, 1);
   model.set(GRB_DoubleParam_TimeLimit, static_cast<double>(Config::timeLimit));
   model.set(GRB_IntParam_Threads, static_cast<int>(Config::threadLimit));
   model.set(GRB_DoubleParam_SoftMemLimit, static_cast<double>(Config::memoryLimit));
   model.set(GRB_IntParam_NumericFocus, 2);
   model.set(GRB_IntParam_OutputFlag, Config::verbose ? 1 : 0);

   try {
      model.optimize();
   } catch ( const GRBException& e ) {
      fmt::println(stderr, "LBBD master: Gurobi error {}: {}", e.getErrorCode(), e.getMessage());
   }

   if ( model.get(GRB_IntAttr_SolCount) <= 0 ) {
      fmt::println(stderr, "LBBD: master found no solution (status {}).", model.get(GRB_IntAttr_Status));
      return warmStart.value_or(Solution::infeasibleSolution(ins));
   }

   // ── Rebuild the schedule the master settled on ───────────────────────────

   const std::vector<std::pair<int, int>> eiAssignment =
      placement.readAssignment([](const GRBVar& v) { return v.get(GRB_DoubleAttr_X); });

   std::vector<std::pair<int, int>> gapArcs;
   for (const GapArc& arc : arcs)
      if ( arc.var.get(GRB_DoubleAttr_X) > 0.5 ) gapArcs.emplace_back(arc.a, arc.b);

   const lbbd::SubproblemResult finalSchedule = subproblem.solve(eiAssignment, false);
   if ( !finalSchedule.hasSchedule() ) {
      fmt::println(stderr, "LBBD: could not re-derive a schedule for the final EI placement.");
      return warmStart.value_or(Solution::infeasibleSolution(ins));
   }

   const std::vector<MachineBlock> blocks = assembleMachineBlocks(graph, eiAssignment, gapArcs);

   const lbbd::BatteryPostprocess battery{ins};
   const lbbd::BatteryPlan plan = battery(blocks);

   // The master priced this very timeline without storage; if the two numbers
   // disagree, the c* coefficients and the reconstructed blocks have drifted
   // apart and every downstream figure is suspect.
   double masterEnergy = 0.0;
   for (const auto& [j, s] : eiAssignment) masterEnergy += graph.procCost(j, s);
   for (const auto& [a, b] : gapArcs)      masterEnergy += graph.switchingDistance(a, b);
   lbbd::checkEnergyAgreement(masterEnergy, plan.energyCostWithoutBattery, "LBBD");

   const double tardinessCost = lbbd::weightedTardiness(ins, finalSchedule.startTimes);
   const double objVal        = plan.energyCost + tardinessCost;

   Solution solution{ ins, objVal, plan.energyCost, tardinessCost,
                      finalSchedule.startTimes, plan.levels, blocks,
                      SolutionStats{ cb.statistics().subproblems.load(),
                                     cb.statistics().feasibilityCuts.load(),
                                     cb.statistics().cumulMifs.load(),
                                     model.get(GRB_DoubleAttr_MIPGap) } };

   lbbd::attachDiagnostics(solution, cb.statistics(), plan,
                           model.get(GRB_DoubleAttr_ObjBound),   // battery-FREE bound only
                           /*boundIsBatteryAware=*/false);

   if ( Config::verbose )
      lbbd::report("LBBD", cb.statistics(), plan, tardinessCost);

   // The warm start is a genuine schedule too; keep whichever is cheaper.
   if ( warmStart && warmStart->getObjVal() < objVal )
      return *warmStart;

   return solution;
}