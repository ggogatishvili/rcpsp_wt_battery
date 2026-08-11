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

#include <utility>
#include <vector>

#include "instance.h"
#include "lbbd/PrecedenceClosure.h"

namespace lbbd
{

/**
 * @brief Start-time windows of every task once the EI tasks have been pinned.
 *
 * Pure precedence + release-date propagation, no resource reasoning. It serves
 * two purposes in the decomposition:
 *
 *  - it shrinks the subproblem before either backend builds a model, which for
 *    the time-indexed MILP backend is the difference between tractable and
 *    hopeless;
 *
 *  - when it empties a window it has *proved* the fixing infeasible without
 *    solving anything, and it knows which two pinned EI tasks did it. That
 *    pair is a ready-made minimal infeasibility set, so the master gets a
 *    tight feasibility cut for free.
 */
struct TimeWindows
{
   std::vector<int> est;
   std::vector<int> lst;

   /// Pinned EI task responsible for est[u] / lst[u]; -1 when the bound comes
   /// from the release date or the horizon rather than from the fixing.
   std::vector<int> estSource;
   std::vector<int> lstSource;

   /// First task whose window came out empty; -1 when every window is non-empty.
   int emptyTask = -1;

   bool consistent() const { return emptyTask < 0; }

   /// The pinned EI tasks that jointly emptied emptyTask's window. Empty when
   /// the windows are consistent, or when the emptiness is due to the instance
   /// itself rather than to the fixing.
   std::vector<int> conflictSources() const;
};

/// @param eiAssignment (task, start) for every EI task; those tasks come back
///        pinned to a single value.
TimeWindows propagate(const Instance* ins,
                      const PrecedenceClosure& closure,
                      const std::vector<std::pair<int, int>>& eiAssignment);

}
