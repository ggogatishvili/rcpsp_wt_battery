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

#include <functional>
#include <map>
#include <utility>
#include <vector>

#include <gurobi_c++.h>

#include "helpers.h"
#include "instance.h"
#include "lbbd/PrecedenceClosure.h"

namespace lbbd
{

/**
 * @brief Where the energy-intensive tasks go — the part of the master that
 *        SolverLBBD and SolverBenders have in common.
 *
 * Both decompositions differ only in how they model the machine *between* EI
 * tasks: SolverLBBD collapses each gap into one pre-priced SPACES arc,
 * SolverBenders carries explicit per-interval states so the battery LP's duals
 * have something linear to attach to. Everything about the EI tasks themselves
 * — their time windows, the assignment constraint, precedence among them, the
 * resource relaxation, and the tardiness lower bound that feeds `q` — is
 * identical, and lives here so the two masters cannot drift apart.
 *
 * That last point is not hypothetical: if the two arms of the experiment
 * disagreed about, say, an EI task's feasible window, the measured difference
 * between them would be partly an artefact of the modelling and there would be
 * no way to see it in the results.
 */
class EiPlacement
{
   public:
      /// Objective coefficient of x[task][start]. SolverLBBD passes the SPACES
      /// cost of running the machine there; SolverBenders passes 0, because its
      /// energy lives in the state variables instead.
      using StartCost = std::function<double(int task, int start)>;

      EiPlacement(const Instance* ins, const PrecedenceClosure* closure);

      /// True when some EI task has no feasible start at all, i.e. the instance
      /// cannot be scheduled within the horizon.
      bool infeasible() const { return badTask >= 0; }
      int  infeasibleTask() const { return badTask; }

      /// Creates the x variables. The caller must model.update() afterwards.
      void addVariables(GRBModel& model, const StartCost& startCost);

      /// Assignment, precedence between EI tasks, and the resource capacities
      /// restricted to EI tasks. The last is a relaxation — the subproblem
      /// enforces resources in full — but a cheap one that keeps the master
      /// from proposing placements no schedule could realise.
      void addStructuralConstraints(GRBModel& model) const;

      /// tau variables per task plus `q >= sum_u w_u tau_u`.
      ///
      /// tau_u lower-bounds task u's tardiness: exactly for an EI task, and
      /// through its EI ancestors otherwise (if ancestor j starts at s then u
      /// cannot start before s + minimalDistance(j, u)). One constraint per
      /// (task, EI ancestor) pair is valid but large, so only the
      /// Config::lbbdTardinessBoundsPerTask ancestors with the longest
      /// precedence paths are written; the optimality cuts close the rest.
      void addTardinessRelaxation(GRBModel& model, const GRBVar& q) const;

      const std::map<int, std::pair<int, int>>& windows() const & { return ranges; }

      bool contains(const int task, const int start) const;
      const GRBVar& var(const int task, const int start) const;

      /// sum_s s * x[task][s] — the task's start time as a linear expression.
      GRBLinExpr startExpr(const int task) const;

      /// running[i] = number of EI tasks occupying interval i. Resource 0 has
      /// capacity 1, so this is 0 or 1 in any feasible solution.
      std::vector<GRBLinExpr> runningExpressions() const;

      /**
       * @brief (task, start) pairs read through an arbitrary accessor.
       *
       * The accessor is what makes this usable from three places that Gurobi
       * keeps deliberately separate: GRBVar::get(GRB_DoubleAttr_X) after the
       * solve, GRBCallback::getSolution at an incumbent, and
       * GRBCallback::getNodeRel at a fractional node.
       */
      template <typename ValueFn>
      std::vector<std::pair<int, int>> readAssignment(ValueFn value) const
      {
         std::vector<std::pair<int, int>> assignment;
         assignment.reserve(vars.size());
         for (const auto& [task, column] : vars)
         {
            Loop(k, static_cast<int>(column.size()))
               if ( value(column[k]) > 0.5 ) { assignment.emplace_back(task, ranges.at(task).first + k); break; }
         }
         return assignment;
      }

      /// Sets MIP start values from a complete schedule. Returns false, and
      /// touches nothing, when the schedule does not fit the windows.
      bool applyWarmStart(const std::vector<int>& startTimes);

      /// Every task in the placement is complete in `assignment`.
      bool isComplete(const std::vector<std::pair<int, int>>& assignment) const
      {
         return assignment.size() == vars.size();
      }

   private:
      const Instance* ins;
      const PrecedenceClosure* closure;
      const int H;

      std::map<int, std::pair<int, int>> ranges;      // task -> [first, last] start
      std::map<int, std::vector<GRBVar>> vars;        // task -> variable per start
      int badTask = -1;
};

}
