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

#include <eo>
#include <eoOp.h>
#include <utils/eoRNG.h>
#include "../SolverMatH.h"

// Adjacent-swap mutation for activity lists.
// Attempts a small number of adjacent swaps; each swap is accepted only when
// neither of the two tasks is a topological predecessor of the other, so the
// invariant is preserved without any repair step.
class ActivityListMutator : public eoMonOp<ActivityList> {
   const int N;
   // reach[a][b] = true iff a must precede b (transitive closure of precedence DAG)
   const std::vector<std::vector<bool>>& reach;
   static constexpr int NUM_ATTEMPTS = 3;

public:
   ActivityListMutator(int N, const std::vector<std::vector<bool>>& reach)
      : N(N), reach(reach) {}

   bool operator()(ActivityList& al) override {
      bool modified = false;
      for (int attempt = 0; attempt < NUM_ATTEMPTS; ++attempt) {
         const int pos = static_cast<int>(rng.random(N - 1));
         const int a   = al[pos];
         const int b   = al[pos + 1];
         if (!reach[a][b] && !reach[b][a]) {
            std::swap(al[pos], al[pos + 1]);
            modified = true;
         }
      }
      return modified;
   }
};