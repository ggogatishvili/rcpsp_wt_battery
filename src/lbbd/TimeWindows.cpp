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

#include "lbbd/TimeWindows.h"

#include <algorithm>

#include "helpers.h"

namespace lbbd
{

std::vector<int> TimeWindows::conflictSources() const
{
   if ( consistent() )
      return {};

   std::vector<int> sources;
   if ( estSource[emptyTask] >= 0 ) sources.push_back(estSource[emptyTask]);
   if ( lstSource[emptyTask] >= 0 ) sources.push_back(lstSource[emptyTask]);
   std::ranges::sort(sources);
   const auto dup = std::ranges::unique(sources);
   sources.erase(dup.begin(), dup.end());
   return sources;
}

TimeWindows propagate(const Instance* ins,
                      const PrecedenceClosure& closure,
                      const std::vector<std::pair<int, int>>& eiAssignment)
{
   const int n = ins->nbr_tasks();

   TimeWindows w;
   w.est.resize(n);
   w.lst.resize(n);
   w.estSource.assign(n, -1);
   w.lstSource.assign(n, -1);

   Loop(u, n)
   {
      w.est[u] = closure.earliestStart(u);
      w.lst[u] = closure.latestStart(u);
   }

   // Pin the EI tasks, then push their consequences along the precedence
   // closure. One pass suffices: the closure is already transitive, so a bound
   // implied by a chain of pinned tasks is implied by its last element too.
   for (const auto& [j, start] : eiAssignment)
   {
      if ( start > w.est[j] ) { w.est[j] = start; w.estSource[j] = j; }
      if ( start < w.lst[j] ) { w.lst[j] = start; w.lstSource[j] = j; }
   }

   for (const auto& [j, start] : eiAssignment)
   {
      Loop(u, n)
      {
         if ( u == j ) continue;

         if ( closure.isDescendant(u, j) ) {
            const int bound = start + static_cast<int>(closure.minimalDistance(j, u));
            if ( bound > w.est[u] ) { w.est[u] = bound; w.estSource[u] = j; }
         }
         if ( closure.isAntecedent(u, j) ) {
            const int bound = start - static_cast<int>(closure.minimalDistance(u, j));
            if ( bound < w.lst[u] ) { w.lst[u] = bound; w.lstSource[u] = j; }
         }
      }
   }

   Loop(u, n) if ( w.est[u] > w.lst[u] )
   {
      w.emptyTask = u;
      break;
   }

   return w;
}

}
