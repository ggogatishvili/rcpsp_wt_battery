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

#include "SolverH1.h"
#include "SolverGA.h"  // for Chromosome, PriorityMetricType
#include <eo>
#include <es/eoReal.h>
#include <vector>

using namespace std;

// GA variant that uses price-aware Phase 1 and exact LP Phase 3 in its
// fitness evaluator.  Chromosome encodes only N priority genes — EI task
// timing is entirely delegated to the price-aware Phase 1.
// -m GA must always use the original H1; this class is the separate control.
class SolverGAP {
public:
   SolverGAP(const Instance* const instance);

   Solution solve()      { return _solve(); }
   Solution operator()() { return _solve(); }

   friend class EvaluatorP;

private:
   const Instance* ins;
   const int H;
   const int N;
   const int N_EI;
   const int chromosomeSize; // == N

   SolverH1 solverH1;

   vector<vector<vector<Edge>>> cachedSPACESGraph;

   Solution _solve();

   eoPop<Chromosome>  generateInitialPopulation() const;
   Chromosome         generateInitialChromosome(PriorityMetricType type) const;
   vector<double>     decodePriorities(const Chromosome& chrom) const;
   Chromosome         generateChromosomeFromSolution(const vector<int>& startTimes) const;
};