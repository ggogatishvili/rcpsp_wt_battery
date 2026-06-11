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
#include "solution.h"
#include "SolverH1.h"
#include "SpacesMilpDecoder.h"
#include <eo>
#include <eoVector.h>
#include <vector>

// Activity-list chromosome: a precedence-feasible permutation of task IDs.
// Fitness is double (negative total cost, maximized); genes are int task IDs.
using ActivityList = eoVector<double, int>;

enum class ActivityMetricType {
   ERD,
   EDD,
   SPT,
   LPT,
   MAX_SUCC,
   WEIGHT,
   RANDOM
};

class SolverMatH {
public:
   SolverMatH(const Instance* ins);

   Solution solve()       { return _solve(); }
   Solution operator()()  { return solve(); }

   friend class ActivityListXover;
   friend class ActivityListMutator;
   friend class MatHEvaluator;

private:
   const Instance* ins;
   const int H, N, N_EI;

   SolverH1              solverH1;
   SpacesMilpDecoder     decoder;

   // Transitive-closure reachability matrix: reach[a][b] = true iff a must precede b.
   // Used by the mutation operator to check swap validity in O(1).
   std::vector<std::vector<bool>> reach;

   std::vector<std::vector<bool>> computeReach() const;

   Solution _solve();

   eoPop<ActivityList> generateInitialPopulation() const;
   ActivityList generateInitialChromosome(ActivityMetricType type) const;
};