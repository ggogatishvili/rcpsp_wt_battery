#pragma once

#include <eo>
#include <eoEvalFunc.h>
#include <chrono>
#include <vector>
#include "../SolverGA.h"
#include "../SolverH1.h"
#include "../config.h"


using namespace std;


typedef eoReal<double> Chromosome;

class Evaluator : public eoEvalFunc<Chromosome> {
public:
    inline Evaluator(const Instance* const instance, SolverGA& solverGA, SolverH1& solverH1)
        : H(instance->maxDuration()), N(instance->nbr_tasks()), N_EI(instance->nbr_ei_tasks()),
          solverGA(solverGA), solverH1(solverH1),
          startTime(chrono::steady_clock::now()) {}

    inline void operator()(Chromosome& chrom) override {
        auto now = chrono::steady_clock::now();
        if (chrono::duration_cast<chrono::seconds>(now - startTime).count() > Config::timeLimit) {
            chrom.fitness(-BIG_M);
            return;
        }

        vector<double> priorities = solverGA.decodePriorities(chrom);
        vector<int> eiDelays = solverGA.decodeDelays(chrom);

        try {
            const vector<int> startTimes = solverH1.scheduleTasks(priorities, eiDelays);
            vector<MachineBlock> machineBlocks = solverH1.scheduleMachineUsage(startTimes, solverGA.cachedSPACESGraph);
            solverH1.optimizeMachineBlocks(machineBlocks);
            const vector<double> energyRequirements = solverH1.getEnergyRequirements(machineBlocks);
            const vector<double> batteryLevels = solverH1.scheduleBatteryUsage(energyRequirements);

            const double tardinessCost = solverH1.computeTardinessCost(startTimes);
            const double energyCost = solverH1.computeEnergyCost(energyRequirements, batteryLevels);
            double totalCost = tardinessCost + energyCost;

            chrom.fitness(-totalCost);

        } catch (exception& e) {
            chrom.fitness(-BIG_M);
        }
    }

private:
    const int H; // Planning horizon (max duration of the instance)
    const int N; // Number of tasks
    const int N_EI; // Number of energy intensive tasks
    SolverGA& solverGA;
    SolverH1& solverH1;
    chrono::steady_clock::time_point startTime;
};