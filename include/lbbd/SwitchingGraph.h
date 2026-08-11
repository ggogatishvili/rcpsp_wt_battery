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

#include <cmath>
#include <vector>

#include "instance.h"
#include "solution.h"

namespace lbbd
{

/**
 * @brief The SPACES machine-state graph (Benedikt et al., 2020).
 *
 * Nodes are *boundaries*: node (t, s) means "just before interval t, the
 * machine is in state s". Arcs are
 *   - stay:       (t, s)  -> (t+1, s)    covering interval t;
 *   - transition: (t, s1) -> (t+d, s2)   covering intervals t .. t+d-1;
 * each weighted by (energy demand per interval) x (electricity price summed
 * over the intervals the arc covers). This is the same graph SolverH1 builds
 * in buildSPACESGraph(), with the same costs and the same boundary handling,
 * so LBBD numbers are directly comparable with H1/H1P/GA/GAP.
 *
 * Two services are exposed, and the LBBD master needs both:
 *
 *  - switchingDistance(i, j): the c* matrix that supplies the objective
 *    coefficients of the master's z variables. It is the cheapest way for the
 *    machine to leave Proc at boundary i and be back in Proc at boundary j,
 *    i.e. the price of the gap covering intervals [i, j-1].
 *      * row i == 0        : the machine starts Off at t = 0;
 *      * column j == horizon: the machine must be Off during the last interval.
 *
 *  - path(i, j): the concrete MachineBlock sequence realising that gap. Only
 *    needed once, at the end of the solve, to build the Solution object and
 *    the energy-demand profile the battery post-processing consumes -- so it
 *    re-runs a small dynamic program rather than keeping O(h^2) predecessors.
 *
 * The --states ladder (Config::stateSet) is honoured exactly as SolverH1 does:
 * it restricts interior gaps only, never the mandatory start-up from Off or
 * the mandatory shut-down to Off.
 */
class SwitchingGraph
{
   public:
      /**
       * @param ins              the instance.
       * @param sourceBoundaries the boundaries for which a c* row is needed --
       *        that is, every interval at which a gap can begin. The master
       *        derives them from the EI tasks' time windows, which is usually
       *        far fewer than all h boundaries. Boundary 0 is always included.
       *        Rows that are not requested stay at +infinity.
       */
      SwitchingGraph(const Instance* ins, const std::vector<int>& sourceBoundaries);

      /// Cheapest cost of a gap covering intervals [i, j-1]; +infinity when no
      /// such state path exists or when row i was not requested.
      double switchingDistance(const int i, const int j) const { return dist[index(i, j)]; }

      /// True when the gap (i, j) is realisable at finite cost.
      bool hasSwitching(const int i, const int j) const { return std::isfinite(switchingDistance(i, j)); }

      /// MachineBlocks realising the cheapest gap covering intervals [i, j-1].
      /// Throws std::runtime_error when the gap is unreachable.
      std::vector<MachineBlock> path(const int i, const int j) const;

      /// Energy cost of keeping the machine in Proc while EI task `task` runs
      /// from interval `start` -- the objective coefficient of x[task][start].
      double procCost(const int task, const int start) const;

      int horizon() const { return h; }

   private:
      struct Arc
      {
         int    toTime;
         State  toState;
         double cost;
         bool   isTransition;
      };

      const Instance* ins;
      const int h;

      // arcs[t * 3 + s] : outgoing arcs of boundary node (t, s).
      std::vector<std::vector<Arc>> arcs;
      // Flat (h+1) x (h+1) c* matrix; see index().
      std::vector<double> dist;

      std::size_t index(const int i, const int j) const
      {
         return static_cast<std::size_t>(i) * static_cast<std::size_t>(h + 1) + static_cast<std::size_t>(j);
      }

      static int node(const int t, const int s) { return t * 3 + s; }

      void buildArcs();

      /// Shortest-path costs from (fromTime, fromState) to every boundary node,
      /// as a flat (h) x 3 array. `restrict` applies the --states ladder.
      std::vector<double> shortestFrom(const int fromTime, const State fromState, const bool restrictStates) const;

      /// True when interior gaps must obey the --states ladder.
      static bool ladderActive();
};

}
