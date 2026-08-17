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
// CPLEX CP Optimizer backend of the LBBD subproblem.
//
// This is the backend to use when comparing against the original LBBD in the
// `rcpsp` repository: it is the same interval-variable / cumulative-function
// model, with the makespan objective replaced by total weighted tardiness and
// release dates added. Its conflict refiner is also what makes the "logic-
// based" part of the decomposition worth anything -- it returns a small
// conflicting subset of the fixing instead of the whole assignment.
//
// Compiled only when the build is configured with -DWITH_CPOPTIMIZER=ON.
// ─────────────────────────────────────────────────────────────────────────────

#include "lbbd/RcpspSubproblem.h"

#include <algorithm>
#include <string>

#include <fmt/format.h>
#include <ilcp/cp.h>

#include "config.h"
#include "helpers.h"
#include "lbbd/TimeWindows.h"

namespace lbbd
{

std::string RcpspSubproblem::backendName()
{
   return "cp-optimizer";
}

SubproblemResult RcpspSubproblem::solve(const std::vector<std::pair<int, int>>& eiAssignment,
                                        const bool refineInfeasibility) const
{
   SubproblemResult result;

   // Cheap pass first: pure precedence propagation. When it empties a window it
   // has already proved infeasibility, and it hands back the pinned tasks that
   // caused it -- a minimal conflict without invoking the refiner at all.
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

   const int n = ins->nbr_tasks();
   const int h = ins->maxDuration();
   const int r = ins->nbr_resources();

   IloEnv env;
   try {
      IloModel model{env};

      std::vector<IloIntervalVar> y(n);
      Loop(u, n)
      {
         y[u] = IloIntervalVar(env, ins->getProcessingTime(u), fmt::format("y_{}", u).c_str());
         y[u].setStartMin(windows.est[u]);
         y[u].setStartMax(windows.lst[u]);
         y[u].setEndMax(h);
         model.add(y[u]);
      }

      // Renewable resources.
      Loop(k, r)
      {
         IloCumulFunctionExpr usage(env);
         bool nonEmpty = false;
         Loop(u, n) if ( ins->rt(u, k) > 0 ) { usage += IloPulse(y[u], ins->rt(u, k)); nonEmpty = true; }
         if ( nonEmpty )
            model.add(usage <= ins->resource_capacities[k]);
      }

      // Precedence.
      Loop(u, n) iterate(v, ins->successors(u))
         model.add(IloEndBeforeStart(env, y[u], y[v]));

      // The fixing, kept in its own array so refineConflict() can be pointed at
      // exactly these constraints and nothing else.
      IloConstraintArray fixings{env};
      std::vector<std::pair<int, int>> fixingOf;
      fixingOf.reserve(eiAssignment.size());
      for (const auto& [j, start] : eiAssignment)
      {
         fixings.add(IloConstraint(IloStartOf(y[j]) == start));
         fixingOf.emplace_back(j, start);
      }
      model.add(fixings);

      // Total weighted tardiness. Completion of u is start + p - 1, i.e.
      // IloEndOf(y[u]) - 1, matching SolverMILP and SolverH1.
      IloNumExpr tardiness(env, 0.0);
      Loop(u, n)
      {
         const double weight = ins->tasks[u].get_weight();
         if ( weight <= 0.0 ) continue;
         tardiness += weight * IloMax(0, IloEndOf(y[u]) - 1 - ins->tasks[u].get_due_date());
      }
      model.add(IloMinimize(env, tardiness));

      IloCP cp{model};
      cp.setIntParameter(IloCP::LogVerbosity, IloCP::Quiet);
      cp.setParameter(IloCP::TimeLimit, Config::subproblemTimeLimit);
      cp.setParameter(IloCP::ConflictRefinerTimeLimit, Config::conflictRefinerTimeLimit);
      // One worker: this runs inside Gurobi's parallel lazy-constraint callback,
      // so the parallelism budget is already spent on the master's tree.
      cp.setParameter(IloCP::Workers, 1);

      const bool solved = cp.solve();
      const IloAlgorithm::Status status = cp.getStatus();

      if ( solved && (status == IloAlgorithm::Optimal || status == IloAlgorithm::Feasible) ) {
         result.status            = status == IloAlgorithm::Optimal ? SubproblemStatus::Optimal
                                                                    : SubproblemStatus::Feasible;
         result.weightedTardiness = cp.getObjValue();
         result.lowerBound        = result.status == SubproblemStatus::Optimal
                                      ? result.weightedTardiness
                                      : std::max(closure->unavoidableTardiness(), cp.getObjBound());
         result.startTimes.assign(n, -1);
         Loop(u, n) result.startTimes[u] = cp.getStart(y[u]);
      }
      else if ( status == IloAlgorithm::Infeasible ) {
         result.status = SubproblemStatus::Infeasible;
         if ( refineInfeasibility && cp.refineConflict(fixings) ) {
            Loop(k, fixings.getSize())
               if ( cp.getConflict(fixings[k]) == IloCP::ConflictMember )
                  result.infeasibilitySet.push_back(fixingOf[k]);
         }
      }
      else {
         result.status     = SubproblemStatus::Unknown;
         result.lowerBound = closure->unavoidableTardiness();
      }

      env.end();
      return result;

   } catch ( const IloException& e ) {
      fmt::println(stderr, "LBBD subproblem (CP): {}", e.getMessage());
      env.end();
      result.status     = SubproblemStatus::Unknown;
      result.lowerBound = closure->unavoidableTardiness();
      return result;
   }
}

}