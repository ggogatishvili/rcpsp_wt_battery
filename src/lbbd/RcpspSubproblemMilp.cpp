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

// ─────────────────────────────────────────────────────────────────────────────
// Gurobi MILP backend of the LBBD subproblem.
//
// This is the *fallback* backend, compiled when the build has no CPLEX CP
// Optimizer. It is a time-indexed formulation, so its size grows with
// (number of tasks) x (horizon): perfectly usable on the 32-96 task instances,
// increasingly painful beyond that, and not what the original LBBD in the
// `rcpsp` repository uses. Configure with -DWITH_CPOPTIMIZER=ON to get the CP
// backend instead whenever a licence is available -- CP dominates MILP on this
// subproblem and its conflict refiner yields much smaller feasibility cuts
// than an IIS does.
// ─────────────────────────────────────────────────────────────────────────────

#include "lbbd/RcpspSubproblem.h"

#include <algorithm>
#include <memory>
#include <string>

#include <fmt/format.h>
#include <gurobi_c++.h>

#include "config.h"
#include "helpers.h"
#include "lbbd/TimeWindows.h"

namespace
{
   /// One EI fixing, paired with the constraint that enforces it, so an IIS
   /// verdict can be translated straight back into a (task, start) pair.
   struct Fixing
   {
      GRBConstr constr;
      std::pair<int, int> assignment;
   };

   /// Time-indexed RCPSP model over the given start-time windows.
   /// When `fixConstraints` is non-null the EI assignments are added as named
   /// equality constraints and collected there, so an IIS can point at them.
   struct Model
   {
      std::unique_ptr<GRBModel> grb;
      // vars[u][i - offset[u]] : task u starts at interval i.
      std::vector<std::vector<GRBVar>> vars;
      std::vector<int> offset;
   };

   Model build(const Instance* ins,
               const lbbd::TimeWindows& w,
               const std::vector<std::pair<int, int>>& eiAssignment,
               std::vector<Fixing>* fixConstraints)
   {
      const int n = ins->nbr_tasks();
      const int h = ins->maxDuration();
      const int r = ins->nbr_resources();

      Model m;
      m.grb = std::make_unique<GRBModel>(Config::gurobiEnv());
      m.grb->set(GRB_IntParam_OutputFlag, 0);
      m.grb->set(GRB_IntParam_Threads, 1);
      m.grb->set(GRB_DoubleParam_TimeLimit, Config::subproblemTimeLimit);

      m.vars.resize(n);
      m.offset.resize(n);

      Loop(u, n)
      {
         m.offset[u] = w.est[u];
         m.vars[u].reserve(w.lst[u] - w.est[u] + 1);
         LoopFrom(i, w.est[u], w.lst[u] + 1)
            m.vars[u].push_back(m.grb->addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("x_{}_{}", u, i)));
      }
      m.grb->update();

      const auto startExpr = [&](const int u) {
         GRBLinExpr e = 0;
         Loop(k, static_cast<int>(m.vars[u].size())) e += (m.offset[u] + k) * m.vars[u][k];
         return e;
      };

      // Each task starts exactly once.
      Loop(u, n)
      {
         GRBLinExpr e = 0;
         for (const GRBVar& v : m.vars[u]) e += v;
         m.grb->addConstr(e == 1, fmt::format("once_{}", u));
      }

      // Precedence.
      Loop(u, n) iterate(v, ins->successors(u))
         m.grb->addConstr(startExpr(u) + ins->getProcessingTime(u) <= startExpr(v),
                          fmt::format("prec_{}_{}", u, v));

      // Renewable resource capacities, over the whole horizon.
      Loop(k, r) Loop(i, h)
      {
         GRBLinExpr e = 0;
         bool nonEmpty = false;
         Loop(u, n)
         {
            const int req = ins->rt(u, k);
            if ( req <= 0 ) continue;
            const int lo = std::max(w.est[u], i - ins->getProcessingTime(u) + 1);
            const int hi = std::min(w.lst[u], i);
            LoopFrom(l, lo, hi + 1) { e += req * m.vars[u][l - m.offset[u]]; nonEmpty = true; }
         }
         if ( nonEmpty )
            m.grb->addConstr(e <= ins->resource_capacities[k], fmt::format("res_{}_{}", k, i));
      }

      // Weighted tardiness. Completion of u is start + p - 1, matching
      // SolverMILP and SolverH1::computeTardinessCost.
      GRBLinExpr objective = 0;
      Loop(u, n)
      {
         const double weight = ins->tasks[u].get_weight();
         if ( weight <= 0.0 ) continue;
         GRBVar tard = m.grb->addVar(0.0, GRB_INFINITY, 0.0, GRB_CONTINUOUS, fmt::format("tard_{}", u));
         m.grb->addConstr(tard >= startExpr(u) + ins->getProcessingTime(u) - 1 - ins->tasks[u].get_due_date(),
                          fmt::format("tarddef_{}", u));
         objective += weight * tard;
      }
      m.grb->setObjective(objective, GRB_MINIMIZE);

      if ( fixConstraints != nullptr ) {
         fixConstraints->reserve(eiAssignment.size());
         for (const auto& [j, start] : eiAssignment)
         {
            // A start outside the window is already a proof of infeasibility;
            // there is no variable to constrain, so skip it rather than index
            // out of range. The pairing below is why the constraint and the
            // assignment travel together instead of relying on position.
            if ( start < w.est[j] || start > w.lst[j] ) continue;
            fixConstraints->push_back(
               { m.grb->addConstr(m.vars[j][start - m.offset[j]] == 1.0, fmt::format("fix_{}_{}", j, start)),
                 { j, start } });
         }
      }

      m.grb->update();
      return m;
   }
}

namespace lbbd
{

std::string RcpspSubproblem::backendName()
{
   return "gurobi-milp";
}

SubproblemResult RcpspSubproblem::solve(const std::vector<std::pair<int, int>>& eiAssignment,
                                        const bool refineInfeasibility) const
{
   SubproblemResult result;

   // Cheap pass first: pure precedence propagation. When it empties a window it
   // has already proved infeasibility, and it hands back the two pinned tasks
   // that caused it -- a minimal conflict at zero cost.
   const TimeWindows windows = propagate(ins, *closure, eiAssignment);
   if ( !windows.consistent() ) {
      result.status = SubproblemStatus::Infeasible;
      if ( refineInfeasibility ) {
         for (const int j : windows.conflictSources())
            for (const auto& [task, start] : eiAssignment)
               if ( task == j ) result.infeasibilitySet.emplace_back(task, start);
      }
      return result;
   }

   try {
      Model m = build(ins, windows, eiAssignment, nullptr);
      m.grb->optimize();

      const int status   = m.grb->get(GRB_IntAttr_Status);
      const int solCount = m.grb->get(GRB_IntAttr_SolCount);

      if ( status == GRB_INFEASIBLE || status == GRB_INF_OR_UNBD ) {
         result.status = SubproblemStatus::Infeasible;
         if ( refineInfeasibility ) {
            // Second pass with the EI windows left untightened, so that the
            // fixing appears as explicit constraints the IIS can point at.
            // Only reached on infeasible fixings, which are the minority.
            TimeWindows base = windows;
            Loop(u, ins->nbr_tasks()) {
               base.est[u] = closure->earliestStart(u);
               base.lst[u] = closure->latestStart(u);
            }
            std::vector<Fixing> fixes;
            Model refiner = build(ins, base, eiAssignment, &fixes);
            refiner.grb->set(GRB_DoubleParam_TimeLimit, Config::conflictRefinerTimeLimit);
            refiner.grb->computeIIS();
            for (Fixing& f : fixes)
               if ( f.constr.get(GRB_IntAttr_IISConstr) == 1 )
                  result.infeasibilitySet.push_back(f.assignment);
         }
         return result;
      }

      if ( solCount <= 0 ) {
         result.status = SubproblemStatus::Unknown;
         result.lowerBound = closure->unavoidableTardiness();
         return result;
      }

      result.weightedTardiness = m.grb->get(GRB_DoubleAttr_ObjVal);
      result.lowerBound        = std::max(closure->unavoidableTardiness(),
                                          m.grb->get(GRB_DoubleAttr_ObjBound));
      result.status            = status == GRB_OPTIMAL ? SubproblemStatus::Optimal
                                                       : SubproblemStatus::Feasible;
      if ( result.status == SubproblemStatus::Optimal )
         result.lowerBound = result.weightedTardiness;

      result.startTimes.assign(ins->nbr_tasks(), -1);
      Loop(u, ins->nbr_tasks()) Loop(k, static_cast<int>(m.vars[u].size()))
         if ( m.vars[u][k].get(GRB_DoubleAttr_X) > 0.5 ) { result.startTimes[u] = m.offset[u] + k; break; }

      return result;

   } catch ( const GRBException& e ) {
      fmt::println(stderr, "LBBD subproblem (MILP): Gurobi error {}: {}", e.getErrorCode(), e.getMessage());
      result.status = SubproblemStatus::Unknown;
      result.lowerBound = closure->unavoidableTardiness();
      return result;
   }
}

}
