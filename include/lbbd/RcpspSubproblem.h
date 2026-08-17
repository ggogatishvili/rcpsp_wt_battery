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

#include <limits>
#include <string>
#include <utility>
#include <vector>

#include "instance.h"
#include "lbbd/PrecedenceClosure.h"

namespace lbbd
{

enum class SubproblemStatus
{
   Optimal,     ///< solved to proven optimality
   Feasible,    ///< a schedule was found but optimality was not proven (time limit)
   Infeasible,  ///< no schedule completes the fixing within the horizon
   Unknown      ///< the backend gave up without a verdict
};

struct SubproblemResult
{
   SubproblemStatus status = SubproblemStatus::Unknown;

   /// Objective of the schedule that was found. Infinity when none was.
   double weightedTardiness = std::numeric_limits<double>::infinity();

   /// Valid *lower* bound on the subproblem optimum for this fixing. Equal to
   /// weightedTardiness when status == Optimal. This is what the optimality cut
   /// must be built from: using weightedTardiness after a time-limited solve
   /// would cut off schedules that are genuinely better than the one found.
   double lowerBound = 0.0;

   /// Start time of every task; empty unless a schedule was found.
   std::vector<int> startTimes;

   /// A subset of the fixed (task, start) pairs that is on its own infeasible,
   /// ideally minimal. Empty when the subproblem was feasible or when the
   /// backend could not refine the conflict -- the caller must then fall back
   /// to a no-good cut over the whole fixing.
   std::vector<std::pair<int, int>> infeasibilitySet;

   bool hasSchedule() const
   {
      return status == SubproblemStatus::Optimal || status == SubproblemStatus::Feasible;
   }
};

/**
 * @brief The LBBD subproblem: complete a schedule around fixed EI tasks.
 *
 * Given a start time for every energy-intensive task, schedule *all* remaining
 * tasks so as to minimise total weighted tardiness, subject to precedence,
 * resource capacities, release dates and the horizon.
 *
 * The master decides only where the EI tasks sit -- it carries a relaxation of
 * the resource constraints restricted to EI tasks -- so this subproblem is
 * where the full RCPSP is actually enforced. It returns three things the
 * master needs: the objective (to drive optimality cuts), a lower bound valid
 * even under a time limit, and, when infeasible, a small conflicting subset of
 * the fixing (to drive feasibility cuts).
 *
 * Two interchangeable backends exist; CMake compiles exactly one of them:
 *
 *   - CP Optimizer (src/lbbd/RcpspSubproblemCp.cpp), enabled with
 *     -DWITH_CPOPTIMIZER=ON. This mirrors the original LBBD in the `rcpsp`
 *     repository and is the one to use when comparing against it: CP is much
 *     stronger on this subproblem, and its conflict refiner produces far
 *     smaller infeasibility sets than an IIS does.
 *
 *   - Gurobi MILP (src/lbbd/RcpspSubproblemMilp.cpp), the default, so the
 *     repository keeps building for anyone without a CPLEX licence.
 *
 * The choice is a pure performance/quality trade-off: both backends answer the
 * same question and the cuts derived from them are valid either way.
 */
class RcpspSubproblem
{
   public:
      RcpspSubproblem(const Instance* ins, const PrecedenceClosure* closure)
         : ins(ins), closure(closure)
      { }

      /**
       * @param eiAssignment    (task, start time) for every EI task.
       * @param refineInfeasibility  when true and the fixing is infeasible,
       *        spend extra time isolating a small conflicting subset instead
       *        of cutting the whole assignment.
       */
      SubproblemResult solve(const std::vector<std::pair<int, int>>& eiAssignment,
                             const bool refineInfeasibility) const;

      /// Name of the compiled-in backend, for logging and result metadata.
      static std::string backendName();

   private:
      const Instance* ins;
      const PrecedenceClosure* closure;
};

}