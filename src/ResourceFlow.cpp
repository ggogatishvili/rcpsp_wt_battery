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

#include "ResourceFlow.h"
#include <algorithm>
#include <queue>

namespace ResourceFlow {

// Each unit of resource currently free in the pool.
// Sorted ascending by (releasePos, taskId) so the earliest-released unit is
// always at the front of the min-heap — "FIFO by release time, then task index".
struct PoolUnit {
   int releasePos; // permutation position at which this unit became free; -1 = dummy source
   int taskId;     // task that released this unit; -1 = dummy source

   bool operator>(const PoolUnit& o) const {
      if (releasePos != o.releasePos) return releasePos > o.releasePos;
      return taskId > o.taskId;
   }
};

using MinPool = std::priority_queue<PoolUnit, std::vector<PoolUnit>, std::greater<PoolUnit>>;

std::vector<std::pair<int,int>> induce(
   const std::vector<int>& perm,
   const Instance* ins)
{
   const int N = ins->nbr_tasks();
   const int R = ins->nbr_resources();

   // One min-heap per resource, seeded with Q_r dummy-source units.
   std::vector<MinPool> pools(R);
   for (int r = 0; r < R; ++r)
      for (int u = 0; u < ins->resource_capacities[r]; ++u)
         pools[r].push({-1, -1});

   // Arc set — start with direct precedence arcs from the instance.
   // Use a sorted vector + dedup instead of std::set for cache-friendly access.
   std::vector<std::pair<int,int>> arcs;
   arcs.reserve(N * 4);
   for (int t = 0; t < N; ++t)
      for (int s : ins->successors(t))
         arcs.emplace_back(t, s);

   // Process tasks in permutation order.
   for (int pos = 0; pos < static_cast<int>(perm.size()); ++pos) {
      const int t = perm[pos];
      for (int r = 0; r < R; ++r) {
         const int need = ins->rt(t, r);
         for (int u = 0; u < need; ++u) {
            // Claim the earliest-released unit.
            PoolUnit unit = pools[r].top();
            pools[r].pop();
            // Record arc from provider to t (skip dummy source arcs).
            if (unit.taskId >= 0)
               arcs.emplace_back(unit.taskId, t);
            // Release unit back at current permutation position, tagged with t.
            pools[r].push({pos, t});
         }
      }
   }

   // Deduplicate arcs.
   std::ranges::sort(arcs);
   arcs.erase(std::ranges::unique(arcs).begin(), arcs.end());
   return arcs;
}

} // namespace ResourceFlow