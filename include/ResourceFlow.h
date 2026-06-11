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

#include "instance.h"
#include <vector>
#include <utility>

namespace ResourceFlow {
   // Returns the induced precedence arcs F = (direct precedence arcs) ∪ (resource-flow arcs),
   // deduplicated. Each pair (a, b) means task a must complete before task b starts.
   //
   // Algorithm: process tasks in permutation order; maintain per resource a FIFO pool of
   // (releasePosition, taskId) units. Dummy source has release position -1. Each task claims
   // units from the pool (earliest released first), records arcs from providers, then returns
   // its units at its permutation position. Deterministic: same permutation → same F.
   std::vector<std::pair<int,int>> induce(
      const std::vector<int>& perm,
      const Instance* ins
   );
}