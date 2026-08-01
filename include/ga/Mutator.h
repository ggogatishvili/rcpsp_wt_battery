#pragma once

#include <eo>
#include <eoOp.h>
#include <es/eoReal.h>
#include <algorithm>

#include "config.h"
#include "SolverGA.h"

typedef eoReal<double> Chromosome;

class Mutator : public eoMonOp<Chromosome> {
    const int N;
    const int N_EI;
    SolverGA& solverGA;

    // Priority and Delay genes are represented as normalized values in [0.0, 1.0].
    // Decoding functions will map these to actual priorities and delays.
    static constexpr double GENE_LOWER_BOUND = 0.0;
    static constexpr double GENE_UPPER_BOUND = 1.0;

public:
    Mutator(int N, int N_EI, SolverGA& solverGA)
        : N(N), N_EI(N_EI), solverGA(solverGA) {}

    bool operator()(Chromosome& chrom) override {
        bool modified = false;

        // High-Level Strategy
        double totalStratWeight = Config::weightMutSkip + Config::weightMutPriorityOnly + Config::weightMutDelayOnly + Config::weightMutBoth;
        double probStratSkip = Config::weightMutSkip / totalStratWeight;
        double probStratPrioOnly = Config::weightMutPriorityOnly / totalStratWeight;
        double probStratDelayOnly = Config::weightMutDelayOnly / totalStratWeight;

        const double segmentStrategy = rng.uniform();
        bool mutatePriorities = false;
        bool mutateDelays = false;

        // Skip mutations entirely
        if (segmentStrategy < probStratSkip) {
        }
        // Mutate only priorities segment
        else if (segmentStrategy < probStratSkip + probStratPrioOnly) {
            mutatePriorities = true;
        }
        // Mutate only delays segment
        else if (segmentStrategy < probStratSkip + probStratPrioOnly + probStratDelayOnly) {
            mutateDelays = true;
        }
        // Mutate both segments
        else {
            mutatePriorities = true;
            mutateDelays = true;
        }

        // Mutate Priorities
        if (mutatePriorities) {
            double totalPrioWeight = Config::weightMutPrioKeep + Config::weightMutPrioNew + Config::weightMutPrioShift;
            double probPrioKeep = Config::weightMutPrioKeep / totalPrioWeight;
            double probPrioNew = Config::weightMutPrioNew / totalPrioWeight;

            for (int task = 0; task < N; task++) {
                double geneStrategy = rng.uniform();

                // Keep existing priority
                if (geneStrategy < probPrioKeep) {
                    continue;
                }
                // Generate completely new random priority
                else if (geneStrategy < probPrioKeep + probPrioNew) {
                    chrom[task] = rng.uniform();
                }
                // Shift existing priority
                else {
                    double shift = -Config::mutPrioShiftMag + rng.uniform() * (2 * Config::mutPrioShiftMag);

                    chrom[task] = std::max(GENE_LOWER_BOUND, std::min(GENE_UPPER_BOUND, chrom[task] + shift));
                }

                modified = true;
            }
        }

        // Mutate Delays
        if (mutateDelays) {
            double totalDelayWeight = Config::weightMutDelayKeep + Config::weightMutDelayZero + Config::weightMutDelayNewRandom + Config::weightMutDelayNewCheap + Config::weightMutDelayShift;
            double probDelKeep = Config::weightMutDelayKeep / totalDelayWeight;
            double probDelZero = Config::weightMutDelayZero / totalDelayWeight;
            double probDelNewRnd = Config::weightMutDelayNewRandom / totalDelayWeight;
            double probDelNewChp = Config::weightMutDelayNewCheap / totalDelayWeight;

            for (int tEI = 0; tEI < N_EI; tEI++) {
                double geneStrategy = rng.uniform();

                // Keep existing delay
                if (geneStrategy < probDelKeep) {
                    continue;
                }
                // Make delay 0
                else if (geneStrategy < probDelKeep + probDelZero) {
                    chrom[N + tEI] = GENE_LOWER_BOUND;
                }
                // Generate completely new random delay
                else if (geneStrategy < probDelKeep + probDelZero + probDelNewRnd) {
                    chrom[N + tEI] = rng.uniform();
                }
                // Generate new delay to random cheap interval
                else if (geneStrategy < probDelKeep + probDelZero + probDelNewRnd + probDelNewChp) {
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
                    double shift = -Config::mutDelayShiftMag + rng.uniform() * (2 * Config::mutDelayShiftMag);

                    chrom[N + tEI] = max(GENE_LOWER_BOUND, min(GENE_UPPER_BOUND, chrom[N + tEI] + shift));
                }

                modified = true;
            }
        }

        return modified;
    }
};