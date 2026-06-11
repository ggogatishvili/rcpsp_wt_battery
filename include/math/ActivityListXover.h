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

// Precedence-preserving one-point crossover for activity lists.
//
// Both children are produced: child1 takes [0, cut) from parent1 then
// the remaining tasks of parent2 in parent2's relative order; child2
// is the symmetric. The result is always topologically valid because:
//   - the first segment is a prefix of a valid topological order;
//   - the second segment preserves the relative order of the other parent,
//     which is itself topological, so no precedence relation is violated.
class ActivityListXover : public eoQuadOp<ActivityList> {
   const int N;
public:
   explicit ActivityListXover(int N) : N(N) {}

   bool operator()(ActivityList& a, ActivityList& b) override {
      if (N <= 1) return false;
      const int cut = static_cast<int>(rng.random(N - 1)) + 1; // in [1, N-1]

      auto child = [&](const ActivityList& first, const ActivityList& second) {
         ActivityList result;
         result.resize(N);
         std::vector<bool> placed(N, false);
         for (int i = 0; i < cut; ++i) {
            result[i] = first[i];
            placed[first[i]] = true;
         }
         int pos = cut;
         for (int t : second) {
            if (!placed[t]) result[pos++] = t;
         }
         return result;
      };

      ActivityList c1 = child(a, b);
      ActivityList c2 = child(b, a);
      // Preserve fitness validity: children are new, mark invalid
      c1.invalidate();
      c2.invalidate();
      a = std::move(c1);
      b = std::move(c2);
      return true;
   }
};