#pragma once

#include <eo>
#include <eoOp.h>
#include <es/eoReal.h>

#include "config.h"

typedef eoReal<double> Chromosome;

class Crossover : public eoQuadOp<Chromosome> {
    const int N;
    const int N_EI;

public:
    Crossover(int N, int N_EI)
        : N(N), N_EI(N_EI) {}

    bool operator()(Chromosome& chrom1, Chromosome& chrom2) override {
        bool modified = false;

        // Calculate relative probabilities from Config weights
        double totalWeight = Config::weightCrossSkip + Config::weightCrossPriorityOnly + Config::weightCrossDelayOnly + Config::weightCrossBoth;
        double probSkip = Config::weightCrossSkip / totalWeight;
        double probPrioOnly = Config::weightCrossPriorityOnly / totalWeight;
        double probDelayOnly = Config::weightCrossDelayOnly / totalWeight;

        const double highLevelStrategyRoll = rng.uniform();
        bool crossoverPriorities = false;
        bool crossoverDelays = false;

        // Skip crossover entirely
        if (highLevelStrategyRoll < probSkip) {
        }
        // Crossover only priorities
        else if (highLevelStrategyRoll < probSkip + probPrioOnly) {
            crossoverPriorities = true;
        }
        // Crossover only delays
        else if (highLevelStrategyRoll < probSkip + probPrioOnly + probDelayOnly) {
            crossoverDelays = true;
        }
        // Crossover both
        else {
            crossoverPriorities = true;
            crossoverDelays = true;
        }

        // Crossover priorities
        if (crossoverPriorities) {
            int cut = rng.random(N - 1) + 1;
            for (int task = cut; task < N; task++) {
                std::swap(chrom1[task], chrom2[task]);
            }
            modified = true;
        }

        // Crossover delays
        if (crossoverDelays && N_EI > 0) {
            int cut = rng.random(N_EI - 1) + 1;
            for (int tEI = cut; tEI < N_EI; tEI++) {
                std::swap(chrom1[N + tEI], chrom2[N + tEI]);
            }
            modified = true;
        }

        return modified;
    }
};