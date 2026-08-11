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
#include <vector>

#include "instance.h"

namespace lbbd
{

/**
 * @brief Transitive longest-path closure of the precedence graph.
 *
 * `Instance` deliberately does not carry this matrix (the line is commented out
 * in its constructor): it costs O(N^2) memory and none of the heuristics
 * (H1/H1P/GA/GAP/MatH) ever read it, so building it on every run would be a
 * pure regression for them.
 *
 * The LBBD master, on the other hand, needs it in three places -- precedence
 * between energy-intensive (EI) tasks, the "an EI task cannot start before its
 * non-EI predecessors could have finished" constraints, and the tardiness
 * lower bounds -- so the closure lives here and is built once per solve.
 *
 * Time indexing follows the rest of the repository: a task started at interval
 * `s` occupies intervals [s, s + p - 1] and *completes* at `s + p - 1`.
 */
class PrecedenceClosure
{
   public:
      /// Returned by minimalDistance() when j is not reachable from i.
      static constexpr long unreachable = std::numeric_limits<long>::min() / 4;

      explicit PrecedenceClosure(const Instance* ins);

      /// Minimum number of time units between start(i) and start(j), i.e. the
      /// longest path from i to j in the precedence graph. `unreachable` when
      /// no path exists. minimalDistance(i, i) == 0.
      long minimalDistance(const int i, const int j) const { return distances[i][j]; }

      /// True when j must start at least one time unit after i.
      bool isDescendant(const int j, const int i) const { return i != j && distances[i][j] >= 1; }
      bool isAntecedent(const int j, const int i) const { return isDescendant(i, j); }

      /// Earliest start of j under release dates and precedence alone.
      int earliestStart(const int j) const { return est[j]; }
      /// Latest start of j that still lets j and all its descendants end within
      /// the horizon (end <= h).
      int latestStart(const int j) const { return lst[j]; }

      /**
       * @brief Weighted tardiness that no schedule can avoid.
       *
       * Computed from the release-date + precedence relaxation only, ignoring
       * resource capacities, so it is a valid lower bound on the subproblem
       * objective for *every* assignment of the master variables. That global
       * validity is exactly what makes it usable as the floor term of the
       * strengthened optimality cut in SolverLBBD -- see the derivation there.
       */
      double unavoidableTardiness() const { return floorTardiness; }

   private:
      const Instance* ins;
      std::vector<std::vector<long>> distances;
      std::vector<int> est;
      std::vector<int> lst;
      double floorTardiness;
};

}
