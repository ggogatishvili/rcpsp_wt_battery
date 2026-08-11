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

#include "lbbd/PrecedenceClosure.h"

#include <algorithm>

#include "helpers.h"
#include "precedenceGraph.h"

namespace lbbd
{

PrecedenceClosure::PrecedenceClosure(const Instance* ins)
   : ins(ins)
   , floorTardiness(0.0)
{
   const int n = ins->nbr_tasks();
   const int h = ins->maxDuration();

   // PrecedenceGraph stores each arc i -> j with weight -p_i, so its all-pairs
   // *shortest* path matrix is the negation of the all-pairs *longest* path
   // matrix we actually want. Unreachable pairs come back as a huge positive
   // value from Boost, which must not be negated into a huge "distance".
   const std::vector<std::vector<long>> shortest = PrecedenceGraph(ins)();

   constexpr long boostUnreachable = std::numeric_limits<long>::max() / 2;

   distances.assign(n, std::vector<long>(n, unreachable));
   Loop(i, n) Loop(j, n)
   {
      distances[i][j] = shortest[i][j] >= boostUnreachable ? unreachable : -shortest[i][j];
   }

   // Earliest start: the release date of j itself, pushed forward by every
   // ancestor's release date plus the longest path from that ancestor to j.
   est.assign(n, 0);
   Loop(j, n) est[j] = ins->tasks[j].get_release_date();
   Loop(i, n) Loop(j, n) if ( isDescendant(j, i) )
   {
      est[j] = std::max<int>(est[j], ins->tasks[i].get_release_date() + static_cast<int>(distances[i][j]));
   }

   // Latest start: j must end by the horizon, and so must every descendant.
   lst.assign(n, 0);
   Loop(j, n) lst[j] = h - ins->getProcessingTime(j);
   Loop(j, n) Loop(u, n) if ( isDescendant(u, j) )
   {
      lst[j] = std::min<int>(lst[j], h - ins->getProcessingTime(u) - static_cast<int>(distances[j][u]));
   }

   // Tardiness forced by release dates and precedence alone. Resource
   // capacities are ignored on purpose: dropping them keeps the bound valid
   // for every master assignment, which is what the optimality cut needs.
   Loop(j, n)
   {
      const int completion = est[j] + ins->getProcessingTime(j) - 1;
      const int due        = ins->tasks[j].get_due_date();
      if ( completion > due )
         floorTardiness += ins->tasks[j].get_weight() * (completion - due);
   }
}

}
