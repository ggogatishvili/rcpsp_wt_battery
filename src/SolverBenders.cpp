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

#include "SolverBenders.h"

#include <algorithm>
#include <cmath>
#include <optional>

#include <fmt/base.h>
#include <fmt/format.h>

#include "helpers.h"
#include "lbbd/BatteryPostprocess.h"
#include "lbbd/DecompositionReport.h"
#include "SolverH1.h"

SolverBenders::SolverBenders(const Instance* instance, const Config::ResolutionMethod method)
   : ins(instance)
   , method(method)
   , H(instance->maxDuration())
{ }

BatteryLp& SolverBenders::Callback::threadBatteryLp()
{
   // One battery LP per callback thread. The pointer is compared against `ins`
   // so that solving a second instance in the same process rebuilds rather than
   // silently reusing a model built for the previous horizon.
   thread_local const Instance* builtFor = nullptr;
   thread_local std::unique_ptr<BatteryLp> lp;
   if ( !lp || builtFor != ins ) {
      lp = std::make_unique<BatteryLp>(ins);
      builtFor = ins;
   }
   return *lp;
}

void SolverBenders::Callback::addBatteryCut(const bool atNode)
{
   // Demand profile implied by the current machine-state solution. At a node
   // this is fractional, which is fine: it is a convex combination of integral
   // profiles, and Phi is convex, so the supporting hyperplane taken there is
   // still a valid global underestimator.
   const std::vector<double> demand = atNode
      ? machine->demandProfile([this](const GRBVar& v) { return getNodeRel(v); })
      : machine->demandProfile([this](const GRBVar& v) { return getSolution(v); });

   const std::optional<BatteryDuals> duals = threadBatteryLp().solveWithDuals(demand);
   if ( !duals )
      return;   // no optimal basis, no meaningful duals; skip rather than guess

   // Evaluated at e = e_bar the cut's right-hand side is exactly Phi(e_bar),
   // so the violation is Phi(e_bar) - theta. Skip when there is none: at a node
   // an unviolated cut is pure pool growth, and at an incumbent it means theta
   // already accounts for this profile and the solution can stand.
   const double thetaValue = atNode ? getNodeRel(*theta) : getSolution(*theta);
   if ( thetaValue >= duals->objVal - Config::bendersCutTolerance )
      return;

   // theta >= Phi(e_bar) + sum_i alpha_i (e_i - e_bar_i)
   //
   // The constant term is accumulated in a plain double and added once, rather
   // than written as `alpha * (expr - e_bar_i)`. Same value, but it keeps the
   // expression arithmetic to the two Gurobi overloads that are beyond doubt
   // (double * GRBLinExpr, GRBLinExpr += GRBLinExpr) and avoids rebuilding a
   // temporary expression per interval on a path that runs at every node.
   double constant = duals->objVal;
   GRBLinExpr rhs = 0;
   Loop(i, static_cast<int>(demand.size()))
   {
      const double alpha = duals->demandDual[i];
      if ( std::abs(alpha) < 1e-12 ) continue;
      rhs += alpha * machine->energyExpr(i);
      constant -= alpha * demand[i];
   }
   rhs += constant;

   if ( atNode ) {
      addCut(*theta >= rhs);
      stats.batteryNodeCuts.fetch_add(1, std::memory_order_relaxed);
   } else {
      addLazy(*theta >= rhs);
   }
   stats.batteryCuts.fetch_add(1, std::memory_order_relaxed);
}

void SolverBenders::Callback::addRcpspCuts()
{
   const std::vector<std::pair<int, int>> assignment =
      placement->readAssignment([this](const GRBVar& v) { return getSolution(v); });
   if ( !placement->isComplete(assignment) )
      return;

   stats.subproblems.fetch_add(1, std::memory_order_relaxed);
   const lbbd::SubproblemResult result = subproblem->solve(assignment, true);

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
         // Identical cut and identical validity argument to SolverLBBD; see the
         // derivation there. The two solvers share the subproblem, so they must
         // share the cut, or a difference between the arms could come from the
         // cut rather than from the battery.
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
         noGood(assignment);
         stats.feasibilityCuts.fetch_add(1, std::memory_order_relaxed);
         stats.inconclusive.fetch_add(1, std::memory_order_relaxed);
         break;
   }
}

void SolverBenders::Callback::callback()
{
   try {
      if ( where == GRB_CB_MIPSOL ) {
         // Order matters for cost, not correctness: the battery LP costs
         // microseconds and the RCPSP subproblem seconds, so do the cheap one
         // first. If it rejects the incumbent, the expensive one was still
         // worth running -- its cut is valid regardless -- but this way the
         // battery bound is already in place when it is.
         if ( batteryCuts )
            addBatteryCut(false);
         addRcpspCuts();
      }
      else if ( where == GRB_CB_MIPNODE && batteryCuts && Config::bendersNodeCuts
                && getIntInfo(GRB_CB_MIPNODE_STATUS) == GRB_OPTIMAL ) {
         // Node cuts exist only for the battery half. A fractional EI placement
         // is not a fixing, so the RCPSP subproblem has nothing to answer here.
         addBatteryCut(true);
      }
   } catch ( const GRBException& e ) {
      fmt::println(stderr, "Benders callback: Gurobi error {}: {}", e.getErrorCode(), e.getMessage());
   } catch ( const std::exception& e ) {
      fmt::println(stderr, "Benders callback: {}", e.what());
   }
}

Solution SolverBenders::_solve()
{
   const std::string who = Config::to_string(method);

   if ( ins->nbr_ei_tasks() == 0 ) {
      fmt::println(stderr, "{}: instance has no energy-intensive task; nothing to decompose.", who);
      return Solution::infeasibleSolution(ins);
   }
   if ( ins->resource_capacities[0] != 1 )
      fmt::println(stderr, "{}: resource 0 has capacity {}, but the machine-state model assumes a single "
                           "energy-intensive machine. Results will not be meaningful.",
                   who, ins->resource_capacities[0]);

   const lbbd::PrecedenceClosure closure{ins};
   lbbd::EiPlacement placement{ins, &closure};
   if ( placement.infeasible() ) {
      fmt::println(stderr, "{}: EI task {} has an empty start-time window; instance is infeasible.",
                   who, placement.infeasibleTask());
      return Solution::infeasibleSolution(ins);
   }

   const lbbd::RcpspSubproblem subproblem{ins, &closure};

   GRBModel model{Config::gurobiEnv()};

   // EI placement carries no energy coefficient here: with explicit states the
   // machine's energy is entirely in the state variables, and double-counting
   // it on x would be a silent modelling error.
   placement.addVariables(model, [](const int, const int) { return 0.0; });
   lbbd::MachineStateModel machine{ins, model};

   GRBVar q = model.addVar(closure.unavoidableTardiness(), GRB_INFINITY, 1.0, GRB_CONTINUOUS, "q");

   GRBVar theta;
   const bool batteryCuts = usesBatteryCuts();
   if ( batteryCuts ) {
      // Negative prices make a naive theta >= 0 wrong. MachineStateModel's
      // bound assumes every interval draws the largest possible demand at the
      // most negative possible price, and bounds the battery's own purchases by
      // what fits in an empty pack -- crude, but finite even with an uncapped
      // C-rate, and the first cuts dominate it immediately.
      theta = model.addVar(machine.energyCostLowerBound(), GRB_INFINITY, 1.0, GRB_CONTINUOUS, "theta");
   }
   model.update();

   machine.requireProcWhile(model, placement.runningExpressions());
   placement.addStructuralConstraints(model);
   placement.addTardinessRelaxation(model, q);

   if ( !batteryCuts ) {
      // StateLBBD control: no theta, energy priced at the raw tariff exactly as
      // SolverLBBD does, battery applied afterwards.
      GRBLinExpr objective = machine.rawEnergyCost() + q;
      model.setObjective(objective, GRB_MINIMIZE);
   }

   // ── Warm start ───────────────────────────────────────────────────────────
   //
   // Only the EI placement is seeded. Seeding the state variables would mean
   // translating H1's machine blocks into rs/rx/ry, and a single inconsistency
   // there produces a rejected MIP start that is very hard to diagnose; Gurobi
   // completes the partial start on its own.
   std::optional<Solution> warmStart;
   if ( Config::lbbdWarmStart ) {
      Solution h1 = SolverH1(ins).solve();
      if ( !h1.isInfeasible() ) {
         const std::vector<int> starts = h1.getTaskAssignments();
         if ( placement.applyWarmStart(starts) ) {
            q.set(GRB_DoubleAttr_Start, h1.getTardinessCost());
            warmStart = std::move(h1);
            model.update();
         } else if ( Config::verbose ) {
            fmt::println("{}: H1 schedule does not fit the master's EI windows; starting cold.", who);
         }
      }
   }

   // ── Solve ────────────────────────────────────────────────────────────────

   Callback cb{ins, &closure, &subproblem, &placement, &machine, &theta, &q, batteryCuts};
   model.setCallback(&cb);

   model.set(GRB_IntParam_LazyConstraints, 1);
   if ( batteryCuts && Config::bendersNodeCuts )
      model.set(GRB_IntParam_PreCrush, 1);   // required when adding user cuts
   model.set(GRB_DoubleParam_TimeLimit, static_cast<double>(Config::timeLimit));
   model.set(GRB_IntParam_Threads, static_cast<int>(Config::threadLimit));
   model.set(GRB_DoubleParam_SoftMemLimit, static_cast<double>(Config::memoryLimit));
   model.set(GRB_IntParam_NumericFocus, 2);
   model.set(GRB_IntParam_OutputFlag, Config::verbose ? 1 : 0);

   try {
      model.optimize();
   } catch ( const GRBException& e ) {
      fmt::println(stderr, "{} master: Gurobi error {}: {}", who, e.getErrorCode(), e.getMessage());
   }

   if ( model.get(GRB_IntAttr_SolCount) <= 0 ) {
      fmt::println(stderr, "{}: master found no solution (status {}).", who, model.get(GRB_IntAttr_Status));
      return warmStart.value_or(Solution::infeasibleSolution(ins));
   }

   // ── Rebuild the schedule ─────────────────────────────────────────────────

   const std::vector<std::pair<int, int>> eiAssignment =
      placement.readAssignment([](const GRBVar& v) { return v.get(GRB_DoubleAttr_X); });

   const lbbd::SubproblemResult finalSchedule = subproblem.solve(eiAssignment, false);
   if ( !finalSchedule.hasSchedule() ) {
      fmt::println(stderr, "{}: could not re-derive a schedule for the final EI placement.", who);
      return warmStart.value_or(Solution::infeasibleSolution(ins));
   }

   const std::vector<MachineBlock> blocks = machine.blocks();
   lbbd::checkTiling(blocks, H, who);

   // Both arms report the same quantity: the true cost of the schedule with the
   // battery used optimally. For Benders that is what theta was approximating;
   // for StateLBBD it is the post-processing step. Recomputing it the same way
   // in both is what makes the two comparable at all.
   const lbbd::BatteryPostprocess battery{ins};
   const lbbd::BatteryPlan plan = battery(blocks);

   // The state model priced this timeline at the raw tariff; cross-check the
   // reconstruction against it.
   double masterRawEnergy = 0.0;
   {
      const std::vector<double> demand =
         machine.demandProfile([](const GRBVar& v) { return v.get(GRB_DoubleAttr_X); });
      Loop(i, H) masterRawEnergy += ins->costs[i] * demand[i];
   }
   lbbd::checkEnergyAgreement(masterRawEnergy, plan.energyCostWithoutBattery, who);

   const double tardinessCost = lbbd::weightedTardiness(ins, finalSchedule.startTimes);
   const double objVal        = plan.energyCost + tardinessCost;

   Solution solution{ ins, objVal, plan.energyCost, tardinessCost,
                      finalSchedule.startTimes, plan.levels, blocks,
                      SolutionStats{ cb.statistics().subproblems.load(),
                                     cb.statistics().feasibilityCuts.load(),
                                     cb.statistics().cumulMifs.load(),
                                     model.get(GRB_DoubleAttr_MIPGap) } };

   // Only the Benders arm's dual bound is valid for the battery-aware problem.
   // StateLBBD prices energy at the raw tariff, so its bound bounds the
   // battery-free problem exactly as SolverLBBD's does.
   lbbd::attachDiagnostics(solution, cb.statistics(), plan,
                           model.get(GRB_DoubleAttr_ObjBound), batteryCuts);

   if ( Config::verbose )
      lbbd::report(who, cb.statistics(), plan, tardinessCost);

   if ( warmStart && warmStart->getObjVal() < objVal )
      return *warmStart;

   return solution;
}
