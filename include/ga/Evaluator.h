#pragma once

#include <eo>
#include <eoEvalFunc.h>
#include <chrono>
#include <optional>
#include <vector>
#include "../SolverGA.h"
#include "../SolverH1.h"
#include "../config.h"
#include "BatteryLp.h"


using namespace std;


typedef eoReal<double> Chromosome;

class Evaluator : public eoEvalFunc<Chromosome> {
public:
    inline Evaluator( const Instance* const instance, SolverGA& solverGA
                    , SolverH1& solverH1, bool useLp = true )
        : ins(instance)
        , H(instance->maxDuration())
        , N(instance->nbr_tasks())
        , N_EI(instance->nbr_ei_tasks())
        , solverGA(solverGA)
        , solverH1(solverH1)
        , useLp(useLp)
        , startTime(chrono::steady_clock::now()) {}

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
            vector<double> batteryLevels;
            if (useLp) {
                thread_local std::optional<BatteryLp> tlBattLp;
                thread_local const Instance* tlIns = nullptr;
                if (tlIns != ins) { tlBattLp.emplace(ins); tlIns = ins; }
                auto opt = tlBattLp->solve(energyRequirements);
                batteryLevels = opt ? std::move(*opt)
                                    : solverH1.scheduleBatteryUsage(energyRequirements);
            } else {
                batteryLevels = solverH1.scheduleBatteryUsage(energyRequirements);
            }

            const double tardinessCost = solverH1.computeTardinessCost(startTimes);
            const double energyCost = solverH1.computeEnergyCost(energyRequirements, batteryLevels);
            double totalCost = tardinessCost + energyCost;

            chrom.fitness(-totalCost);

        } catch (exception& e) {
            chrom.fitness(-BIG_M);
        }
    }

private:
    const Instance* ins;
    const int H;
    const int N;
    const int N_EI;
    SolverGA& solverGA;
    SolverH1& solverH1;
    bool useLp;
    chrono::steady_clock::time_point startTime;
};