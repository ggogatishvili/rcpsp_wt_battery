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

#include <vector>

#include <gurobi_c++.h>

#include "config.h"
#include "instance.h"
#include "lbbd/CutStatistics.h"
#include "lbbd/EiPlacement.h"
#include "lbbd/PrecedenceClosure.h"
#include "lbbd/RcpspSubproblem.h"
#include "lbbd/SwitchingGraph.h"
#include "solution.h"

/**
 * @brief Branch-and-check logic-based Benders decomposition, weighted-tardiness
 *        objective, with the battery handled as a post-processing step.
 *
 * This is the LBBD of the `rcpsp` repository ported to the battery model, with
 * two deliberate changes:
 *
 *  1. **Objective.** The original minimises a normalised blend of energy cost
 *     and makespan. Here it is `energy cost + total weighted tardiness`, both
 *     already in the same monetary unit and exactly what SolverMILP minimises,
 *     so LBBD numbers drop into the existing comparisons without rescaling.
 *     The `q` variable, formerly the makespan, is now total weighted tardiness.
 *
 *  2. **Battery.** Master and subproblem both ignore storage; once the master
 *     converges, lbbd::BatteryPostprocess prices the resulting machine
 *     schedule against the exact battery LP.
 *
 * ### Structure
 *
 * *Master* (ILP). lbbd::EiPlacement decides where the energy-intensive tasks
 * go; `z[a][b]` binaries decide what the machine does between them, priced by
 * lbbd::SwitchingGraph; `q` lower-bounds total weighted tardiness. The machine
 * timeline is a unit flow through the boundary graph rather than the O(h^3)
 * interval-partition constraint the original uses — see the note in the .cpp.
 *
 * *Subproblem* (lbbd::RcpspSubproblem). Given the EI placement, schedules every
 * remaining task to minimise weighted tardiness. Infeasible fixings yield
 * feasibility cuts, feasible ones yield optimality cuts on `q`.
 *
 * ### Exactness, stated precisely
 *
 * Exact for the **battery-free** problem. Storage can only lower the bill, so
 * the master's objective is an *upper* bound on the battery-aware cost and
 * never a lower bound on it: the MIP gap reported here certifies optimality
 * for the battery-free problem only. For a bound that is valid with storage,
 * see SolverBenders, and docs/BENDERS_BATTERY.md for why it costs the SPACES
 * pre-processing to get one.
 */
class SolverLBBD
{
   public:
      SolverLBBD(const Instance* instance);

      Solution solve() { return _solve(); }
      Solution operator()() { return solve(); }

   private:
      /// Lazy-constraint callback: one subproblem solve per master incumbent.
      class Callback : public GRBCallback
      {
         public:
            Callback(const Instance* ins,
                     const lbbd::PrecedenceClosure* closure,
                     const lbbd::RcpspSubproblem* subproblem,
                     const lbbd::EiPlacement* placement,
                     GRBVar* q)
               : ins(ins), closure(closure), subproblem(subproblem)
               , placement(placement), q(q)
            { }

            const lbbd::CutStatistics& statistics() const & { return stats; }

         protected:
            void callback() override;

         private:
            const Instance* ins;
            const lbbd::PrecedenceClosure* closure;
            const lbbd::RcpspSubproblem* subproblem;
            const lbbd::EiPlacement* placement;
            GRBVar* q;
            lbbd::CutStatistics stats;
      };

      const Instance* ins;
      const int H;

      Solution _solve();

      /// Rebuilds the machine timeline from a master solution.
      std::vector<MachineBlock> assembleMachineBlocks(const lbbd::SwitchingGraph& graph,
                                                      const std::vector<std::pair<int, int>>& eiAssignment,
                                                      const std::vector<std::pair<int, int>>& gapArcs) const;
};