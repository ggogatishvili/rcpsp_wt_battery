#pragma once

#include <eo>
#include <eoOp.h>
#include <algorithm>
#include <utility>

typedef eoReal<double> Chromosome;

class Mutator : public eoMonOp<Chromosome> {
    const int N;
    const int N_EI;
    SolverGA& solverGA;

    // Priority and Delay genes are represented as normalized values in [0.0, 1.0].
    // Decoding functions will map these to actual priorities and delays.
    static constexpr double GENE_LOWER_BOUND = 0.0;
    static constexpr double GENE_UPPER_BOUND = 1.0;


    // Segment Probabilities (Must sum to 1.0)
    static constexpr double PROB_STRATEGY_SKIP          = 0.20;
    static constexpr double PROB_STRATEGY_PRIORITY_ONLY = 0.30;
    static constexpr double PROB_STRATEGY_DELAY_ONLY    = 0.30;
    static constexpr double PROB_STRATEGY_BOTH          = 0.20;


    // Priority Gene Probabilities (Must sum to 1.0)
    static constexpr double PROB_PRIORITY_KEEP = 0.40;
    static constexpr double PROB_PRIORITY_NEW   = 0.30;
    static constexpr double PROB_PRIORITY_SHIFT = 0.30;

    static constexpr std::pair<double, double> PRIORITY_SHIFT_INTERVAL = {-0.20, 0.20};


    // Delay Gene Probabilities (Must sum to 1.0)
    static constexpr double PROB_DELAY_KEEP         = 0.20;
    static constexpr double PROB_DELAY_ZERO         = 0.20;
    static constexpr double PROB_DELAY_NEW_RANDOM   = 0.20;
    static constexpr double PROB_DELAY_NEW_CHEAP    = 0.20;
    static constexpr double PROB_DELAY_SHIFT        = 0.20;

    static constexpr std::pair<double, double> DELAY_SHIFT_INTERVAL = {-0.20, 0.20};

public:
    Mutator(int N, int N_EI, SolverGA& solverGA)
        : N(N), N_EI(N_EI), solverGA(solverGA) {}

    bool operator()(Chromosome& chrom) override {
        bool modified = false;

        const double segmentStrategy = rng.uniform();
        bool mutatePriorities = false;
        bool mutateDelays = false;

        // Skip mutations entirely
        if (segmentStrategy < PROB_STRATEGY_SKIP) {
        }
        // Mutate only priorities segment
        else if (segmentStrategy < PROB_STRATEGY_SKIP + PROB_STRATEGY_PRIORITY_ONLY) {
            mutatePriorities = true;
        }
        // Mutate only delays segment
        else if (segmentStrategy < PROB_STRATEGY_SKIP + PROB_STRATEGY_PRIORITY_ONLY + PROB_STRATEGY_DELAY_ONLY) {
            mutateDelays = true;
        }
        // Mutate both segments
        else {
            mutatePriorities = true;
            mutateDelays = true;
        }

        // Mutate Priorities
        if (mutatePriorities) {
            for (int task = 0; task < N; task++) {
                double geneStrategy = rng.uniform();

                // Keep existing priority
                if (geneStrategy < PROB_PRIORITY_KEEP) {
                    continue;
                }
                // Generate completely new random priority
                else if (geneStrategy < PROB_PRIORITY_KEEP + PROB_PRIORITY_NEW) {
                    chrom[task] = rng.uniform();
                }
                // Shift existing priority
                else {
                    double minShift = PRIORITY_SHIFT_INTERVAL.first;
                    double maxShift = PRIORITY_SHIFT_INTERVAL.second;

                    double shift = minShift + rng.uniform() * (maxShift - minShift);

                    chrom[task] = std::max(GENE_LOWER_BOUND, std::min(GENE_UPPER_BOUND, chrom[task] + shift));
                }

                modified = true;
            }
        }

        // Mutate Delays
        if (mutateDelays) {
            for (int tEI = 0; tEI < N_EI; tEI++) {
                double geneStrategy = rng.uniform();

                // Keep existing delay
                if (geneStrategy < PROB_DELAY_KEEP) {
                    continue;
                }
                // Make delay 0
                else if (geneStrategy < PROB_DELAY_KEEP + PROB_DELAY_ZERO) {
                    chrom[N + tEI] = GENE_LOWER_BOUND;
                }
                // Generate completely new random delay
                else if (geneStrategy < PROB_DELAY_KEEP + PROB_DELAY_ZERO + PROB_DELAY_NEW_RANDOM) {
                    chrom[N + tEI] = rng.uniform();
                }
                // Generate new delay to random cheap interval
                else if (geneStrategy < PROB_DELAY_KEEP + PROB_DELAY_ZERO + PROB_DELAY_NEW_RANDOM + PROB_DELAY_NEW_CHEAP) {
                    int task =  solverGA.ins->ei_tasks[tEI];
                    int releaseDate = solverGA.ins->tasks[task].get_release_date();

                    int selectedStart = solverGA.cheapIntervals[rng.random(solverGA.cheapIntervals.size())];
                    int selectedDelay = selectedStart - releaseDate;

                    int minDelay = solverGA.absoluteEIDelayBounds[tEI].first;
                    int maxDelay = solverGA.absoluteEIDelayBounds[tEI].second;

                    if (selectedDelay < minDelay || selectedDelay > maxDelay) {
                        selectedDelay = minDelay;
                    }

                    chrom[N + tEI] = solverGA.calculateRelativeDelay(selectedDelay, minDelay, maxDelay);
                }
                // Shift existing delay
                else {
                    double minShift = DELAY_SHIFT_INTERVAL.first;
                    double maxShift = DELAY_SHIFT_INTERVAL.second;

                    double shift = minShift + rng.uniform() * (maxShift - minShift);

                    chrom[N + tEI] = max(GENE_LOWER_BOUND, min(GENE_UPPER_BOUND, chrom[N + tEI] + shift));
                }

                modified = true;
            }
        }

        return modified;
    }
};