#pragma once

#include <eo>
#include <eoOp.h>
#include <es/eoReal.h>
#include <algorithm>

#include "config.h"

typedef eoReal<double> Chromosome;

// Priority-only mutator for SolverGAP.  The delay segment is absent from the
// GAP chromosome, so only priority genes are mutated.
class MutatorP : public eoMonOp<Chromosome> {
   const int N;

   static constexpr double GENE_LOWER_BOUND = 0.0;
   static constexpr double GENE_UPPER_BOUND = 1.0;

public:
   explicit MutatorP(int N) : N(N) {}

   bool operator()(Chromosome& chrom) override {
      bool modified = false;

      double totalPrioWeight = Config::weightMutPrioKeep + Config::weightMutPrioNew
                             + Config::weightMutPrioShift;
      double probPrioKeep = Config::weightMutPrioKeep / totalPrioWeight;
      double probPrioNew  = Config::weightMutPrioNew  / totalPrioWeight;

      for (int task = 0; task < N; task++) {
         double geneStrategy = rng.uniform();
         if (geneStrategy < probPrioKeep) {
            continue;
         } else if (geneStrategy < probPrioKeep + probPrioNew) {
            chrom[task] = rng.uniform();
         } else {
            double shift = -Config::mutPrioShiftMag + rng.uniform() * (2 * Config::mutPrioShiftMag);
            chrom[task] = std::max(GENE_LOWER_BOUND, std::min(GENE_UPPER_BOUND, chrom[task] + shift));
         }
         modified = true;
      }

      return modified;
   }
};