#pragma once

#include "SolverH1.h"
#include <eo>
#include <es/eoReal.h>
#include <vector>

using namespace std;

enum class PriorityMetricType {
    ERD,
    EDD,
    SPT,
    LPT,
    MAX_SUCC,
    WEIGHT,
    RANDOM
};

typedef eoReal<double> Chromosome;

class SolverGA {
public:
    SolverGA(const Instance* const instance);

    Solution solve()
    {
        return _solve();
    }

    inline Solution operator()() {
        return _solve();
    }

    friend class Evaluator;
    friend class Mutator;

private:
    const Instance* ins; // Pointer to the instance to solve
    const int H; // Planning horizon (max duration of the instance)
    const int N; // Number of tasks
    const int N_EI; // Number of energy intensive tasks
    const int chromosomeSize;

    SolverH1 solverH1;

    vector<pair<int, int>> absoluteEIDelayBounds;
    vector<int> cheapIntervals;
    vector<vector<vector<Edge>>> cachedSPACESGraph;

    Solution _solve();

    vector<int> findCheapIntervals() const;
    eoPop<Chromosome> generateInitialPopulation() const;
    Chromosome generateInitialChromosome(const PriorityMetricType& type, const double delayRate, const bool useCheapIntervals) const;


    vector<pair<int, int>> calculateAbsoluteEIDelayBounds() const;
    double calculateRelativeDelay(int absoluteSelectedDelay, int absoluteMinDelay, int absoluteMaxDelay) const;

    vector<double> decodePriorities(const Chromosome& chrom) const;
    vector<int> decodeDelays(const Chromosome& chrom) const;
};