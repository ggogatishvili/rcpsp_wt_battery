#pragma once

#include <eo>
#include <eoContinue.h>
#include <es/eoReal.h>
#include <chrono>
#include <string>
#include <fmt/base.h>
#include "../config.h"
#include "../helpers.h"

using namespace std;

template <typename EOT>
class TerminatorT : public eoContinue<EOT> {
    chrono::steady_clock::time_point startTime;
    const int maxSeconds;
    const int maxStagnationGens;
    int currentGen;
    int lastImprovementGen;
    double bestFitness;

public:
    inline TerminatorT(const int timeLimit, const int stagnationLimit)
        : maxSeconds(timeLimit), maxStagnationGens(stagnationLimit),
          currentGen(0), lastImprovementGen(0), bestFitness(-BIG_M) {
        startTime = chrono::steady_clock::now();
    }

    inline bool operator()(const eoPop<EOT>& pop) override {
        currentGen++;
        const auto now = chrono::steady_clock::now();
        auto elapsed = chrono::duration_cast<chrono::seconds>(now - startTime).count();

        const double currentBest = pop.best_element().fitness();

        // Logging
        if (Config::verbose && currentGen % 10 == 0) {
            fmt::println("[Gen {:4d} | Time {:3d}s] Best Cost: {:.2f} (Stagnation: {}/{})",
                         currentGen, elapsed, -currentBest,
                         currentGen - lastImprovementGen, maxStagnationGens);
        }

        // Time Check
        if (elapsed >= maxSeconds) {
            if (Config::verbose) fmt::println("GA Stopped: Time limit reached ({}s).", elapsed);
            return false;
        }

        // Stagnation Check
        if (currentBest > bestFitness + MyEPS) {
            bestFitness = currentBest;
            lastImprovementGen = currentGen;
        } else if (currentGen - lastImprovementGen >= maxStagnationGens) {
            if (Config::verbose) fmt::println("GA Stopped: No improvement for {} generations.", maxStagnationGens);
            return false;
        }

        return true;
    }

    inline string className() const override {
        return "CustomTerminator";
    }
};

// Convenience alias for the classic GA chromosome type.
using Terminator = TerminatorT<eoReal<double>>;