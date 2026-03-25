#pragma once

#include <eo>
#include <eoOp.h>

typedef eoReal<double> Chromosome;

class Crossover : public eoQuadOp<Chromosome> {
    const int N;
    const int N_EI;

    // Segment Probabilities (Must sum to 1.0)
    const double PROB_STRATEGY_SKIP          = 0.20;
    const double PROB_STRATEGY_PRIORITY_ONLY = 0.20;
    const double PROB_STRATEGY_DELAY_ONLY    = 0.20;
    const double PROB_STRATEGY_BOTH          = 0.40;

public:
    Crossover(int N, int N_EI)
        : N(N), N_EI(N_EI) {}

    bool operator()(Chromosome& chrom1, Chromosome& chrom2) override {
        bool modified = false;

        const double highLevelStrategyRoll = rng.uniform();
        bool crossoverPriorities = false;
        bool crossoverDelays = false;

        // Skip crossover entirely
        if (highLevelStrategyRoll < PROB_STRATEGY_SKIP) {
        }
        // Crossover only priorities
        else if (highLevelStrategyRoll < PROB_STRATEGY_SKIP + PROB_STRATEGY_PRIORITY_ONLY) {
            crossoverPriorities = true;
        }
        // Crossover only delays
        else if (highLevelStrategyRoll < PROB_STRATEGY_SKIP + PROB_STRATEGY_PRIORITY_ONLY + PROB_STRATEGY_DELAY_ONLY) {
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
        if (crossoverDelays) {
            int cut = rng.random(N_EI - 1) + 1;
            for (int tEI = cut; tEI < N_EI; tEI++) {
                std::swap(chrom1[N + tEI], chrom2[N + tEI]);
            }
            modified = true;
        }

        return modified;
    }
};