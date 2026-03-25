#include "SolverGA.h"
#include "../include/ga/Evaluator.h"
#include "../include/ga/Crossover.h"
#include "../include/ga/Mutator.h"
#include "../include/ga/Terminator.h"
#include "config.h"
#include <fmt/base.h>
#include <algorithm>

#include <eo>
#include <ranges>
#include <eoRealInitBounded.h>
#include <eoDetTournamentSelect.h>
#include <eoEasyEA.h>
#include <eoSelectNumber.h>
#include <eoSGATransform.h>


using namespace std;


SolverGA::SolverGA(const Instance* const instance)
    : ins(instance), H(instance->maxDuration()), N(instance->nbr_tasks()), N_EI(instance->nbr_ei_tasks()), chromosomeSize(instance->nbr_tasks() + instance->nbr_ei_tasks()), solverH1(instance) {

    // Calculate min and max delays for each EI task
    eiDelayBounds = calculateEIDelayBounds();

    // Find cheap time intervals
    cheapIntervals = findCheapIntervals();

    // Create SPACES graph
    cachedSPACESGraph = solverH1.buildSPACESGraph();
}

Solution SolverGA::_solve() {
    // Generate initial population
    eoPop<Chromosome> pop = generateInitialPopulation();

    // Setup GA
    Evaluator eval(ins, *this, solverH1);
    apply<Chromosome>(eval, pop);
    Crossover xover(N, N_EI);
    Mutator mut(N, N_EI);
    eoDetTournamentSelect<Chromosome> selectOne(2);
    eoSelectNumber<Chromosome> selectMany(selectOne, populationSize);
    eoSGATransform<Chromosome> transform(xover, 1.0, mut, 1.0);
    eoPlusReplacement<Chromosome> replace;
    Terminator terminator(static_cast<int>(Config::timeLimit), stagnationLimit);

    if (Config::verbose) {
        fmt::println("\n=================================================");
        fmt::println("GA PARAMETERS:");
        fmt::println("  Chromosome Size : {} ({} Priorities, {} Delays)", chromosomeSize, N, N_EI);
        fmt::println("  Population Size : {}", populationSize);
        fmt::println("  Stagnation Limit  : {} generations", stagnationLimit);
        fmt::println("  Max Time Limit  : {}s", Config::timeLimit);
        fmt::println("  Max Stagnation  : 50 gens");
        fmt::println("=================================================");
        fmt::println("Starting GA Optimization...\n");
    }

    // Evaluate initial population
    #pragma omp parallel for default(none) shared(pop, eval)
    for (auto & chrom : pop) {
        if (chrom.invalid()) {
            eval(chrom);
        }
    }

    // GA loop
    while (terminator(pop)) {
        eoPop<Chromosome> offspring;

        selectMany(pop, offspring);

        transform(offspring);

        #pragma omp parallel for default(none) shared(offspring, eval)
        for (auto & chrom : offspring) {
            if (chrom.invalid()) {
                eval(chrom);
            }
        }

        replace(pop, offspring);
    }

    // Solution extraction
    pop.sort();
    Chromosome bestChrom = pop[0];

    vector<double> priorities = decodePriorities(bestChrom);
    vector<int> eiDelays = decodeDelays(bestChrom);

    try {
        vector<int> startTimes = solverH1.scheduleTasks(priorities, eiDelays);
        vector<MachineBlock> machineBlocks = solverH1.scheduleMachineUsage(startTimes, cachedSPACESGraph);
        solverH1.optimizeMachineBlocks(machineBlocks);
        vector<double> energyRequirements = solverH1.getEnergyRequirements(machineBlocks);
        vector<double> batteryLevels = solverH1.scheduleBatteryUsage(energyRequirements);

        double tardinessCost = solverH1.computeTardinessCost(startTimes);
        double energyCost = solverH1.computeEnergyCost(energyRequirements, batteryLevels);
        double totalCost = tardinessCost + energyCost;

        return { ins, totalCost, energyCost, tardinessCost, startTimes, batteryLevels, machineBlocks, SolutionStats::defaultStats() };
    } catch (const exception& e) {
        fmt::println(stderr, "GA Optimization Failed ({}). Falling back to Base Heuristic.", e.what());
        return solverH1();
    }
}

vector<int> SolverGA::findCheapIntervals() const {
    double percentile = 0.30;

    vector<double> sortedCosts = ins->costs;
    sort(sortedCosts.begin(), sortedCosts.end());
    double costThreshold = sortedCosts[max(0, (int)(percentile * (H - 1)))];

    vector<int> intervals;
    for (int i = 0; i < H; i++) {
        if (ins->costs[i] <= costThreshold) {
            intervals.push_back(i);
        }
    }

    return intervals;
}

eoPop<Chromosome> SolverGA::generateInitialPopulation() const {
    if (populationSize < 100) {
        throw runtime_error(fmt::format("GA error: Population size must be at least 100 to ensure proper injection of heuristics and randomness. Current population size: {}", populationSize));
    }

    eoPop<Chromosome> population;

    // Inject specific priorities with 0 delays
    population.push_back(generateInitialChromosome(PriorityMetricType::ERD, 0.0, false));
    population.push_back(generateInitialChromosome(PriorityMetricType::EDD, 0.0, false));
    population.push_back(generateInitialChromosome(PriorityMetricType::SPT, 0.0, false));
    population.push_back(generateInitialChromosome(PriorityMetricType::LPT, 0.0, false));
    population.push_back(generateInitialChromosome(PriorityMetricType::MAX_SUCC, 0.0, false));
    population.push_back(generateInitialChromosome(PriorityMetricType::WEIGHT, 0.0, false));

    // Inject random priorities with 0 delays
    // 10% of population
    for (int i = 0; i < 0.1 * populationSize; i++) population.push_back(generateInitialChromosome(PriorityMetricType::RANDOM, 0.0, false));

    // Inject priorities with delays to cheap times
    // 7 * 5% = 35% of population
    for (int i = 0; i < 0.05 * populationSize; i++) {
        population.push_back(generateInitialChromosome(PriorityMetricType::ERD, 0.2, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::EDD, 0.2, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::SPT, 0.2, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::LPT, 0.2, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::MAX_SUCC, 0.2, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::WEIGHT, 0.2, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::RANDOM, 0.2, true));
    }

    // Inject priorities with delays to cheap times
    // 7 * 5% = 35% of population
    for (int i = 0; i < 0.05 * populationSize; i++) {
        population.push_back(generateInitialChromosome(PriorityMetricType::ERD, 0.5, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::EDD, 0.5, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::SPT, 0.5, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::LPT, 0.5, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::MAX_SUCC, 0.5, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::WEIGHT, 0.5, true));
        population.push_back(generateInitialChromosome(PriorityMetricType::RANDOM, 0.5, true));
    }

    // Inject random priorities with completely random delays
    // Rest of the population
    while (population.size() < populationSize) {
        population.push_back(generateInitialChromosome(PriorityMetricType::RANDOM, 0.5, false));
    }

    return population;
}

Chromosome SolverGA::generateInitialChromosome(const PriorityMetricType& type, const double delayRate, const bool useCheapIntervals) const {
    Chromosome chromosome;
    chromosome.resize(chromosomeSize);
    for(int i = 0; i < chromosomeSize; i++) chromosome[i] = 0.0;

    vector<pair<double, int>> metrics;
    for (int i = 0; i < N; i++) {
        switch (type) {
            case PriorityMetricType::ERD:
                metrics.emplace_back(ins->tasks[i].get_release_date(), i);
                break;
            case PriorityMetricType::EDD:
                metrics.emplace_back(ins->tasks[i].get_due_date(), i);
                break;
            case PriorityMetricType::SPT:
                metrics.emplace_back(ins->getProcessingTime(i), i);
                break;
            case PriorityMetricType::LPT:
                metrics.emplace_back(-ins->getProcessingTime(i), i);
                break;
            case PriorityMetricType::MAX_SUCC:
                metrics.emplace_back(-(double)ins->successors(i).size(), i);
                break;
            case PriorityMetricType::WEIGHT:
                metrics.emplace_back(-ins->tasks[i].get_weight(), i);
                break;
            case PriorityMetricType::RANDOM:
                metrics.emplace_back(rng.uniform(), i);
                break;
        }
    }

    sort(metrics.begin(), metrics.end());

    // Assign priorities
    for (int i = 0; i < N; i++) {
        int task_id = metrics[i].second;
        chromosome[task_id] = 1.0 - ((double)i / (N - 1));
    }

    // Assign delays
    for (int tEI = 0; tEI < N_EI; tEI++) {
        // Flip coin for delay
        if (delayRate <= 0.0 || !rng.flip(delayRate)) {
            chromosome[N + tEI] = 0.0;
            continue;
        }

        if (!useCheapIntervals) {
            chromosome[N + tEI] = rng.uniform();
            continue;
        }

        int minDelay = eiDelayBounds[tEI].first;
        int maxDelay = eiDelayBounds[tEI].second;

        int task =ins->ei_tasks[tEI];
        int releaseDate = ins->tasks[task].get_release_date();

        // The valid absolute time window for this task to start
        int window_start = releaseDate + minDelay;
        int window_end = releaseDate + maxDelay;

        // Filter cheap times that fall within this task's valid window
        vector<int> validCheapIntervals;
        for (int i : cheapIntervals) {
            if (i >= window_start && i <= window_end) {
                validCheapIntervals.push_back(i);
            }
        }

        // If we found valid cheap times, pick one randomly and translate it to a gene value
        if (!validCheapIntervals.empty()) {
            int selectedStart = validCheapIntervals[rng.random(validCheapIntervals.size())];
            int selectedDelay = selectedStart - releaseDate;

            if (maxDelay > minDelay) {
                // Translate absolute delay back into [0.0, 1.0] gene space
                chromosome[N + tEI] = (double)(selectedDelay - minDelay) / (maxDelay - minDelay);
            } else {
                chromosome[N + tEI] = 0.0;
            }
        } else {
            // Fallback: No cheap times in window, assign random delay
            chromosome[N + tEI] = rng.uniform();
        }
    }

    return chromosome;
}

vector<pair<int, int>> SolverGA::calculateEIDelayBounds() const {
    // Calculate In-Degrees for Topological Sort
    vector<int> inDegree(N, 0);
    for (int task = 0; task < N; task++) {
        for (int succ : ins->successors(task)) {
            inDegree[succ]++;
        }
    }

    // Forward Pass - Calculate Earliest Start Times (EST)
    queue<int> taskQueue;
    vector<int> EST(N);
    for (int task = 0; task < N; task++) {
        EST[task] = ins->tasks[task].get_release_date();

        if (ins->is_ei_task(task)) {
            EST[task] = max(EST[task], ins->offProc.time + 1);
        }

        if (inDegree[task] == 0) {
            taskQueue.push(task);
        }
    }

    vector<int> topologicalOrder;
    while (!taskQueue.empty()) {
        int task = taskQueue.front();
        taskQueue.pop();
        topologicalOrder.push_back(task);

        for (int succ : ins->successors(task)) {
            EST[succ] = max(EST[succ], EST[task] + ins->getProcessingTime(task));
            inDegree[succ]--;
            if (inDegree[succ] == 0) {
                taskQueue.push(succ);
            }
        }
    }

    // Backward Pass - Calculate Latest Start Times (LST)
    vector<int> LST(N);
    int globalDeadline = H - 1 - ins->procOff.time - 1;

    for (int t = 0; t < N; t++) {
        LST[t] = globalDeadline - ins->getProcessingTime(t) + 1;
    }

    // Traverse in reverse topological order
    for (int task : ranges::reverse_view(topologicalOrder)) {
        for (int succ : ins->successors(task)) {
            LST[task] = min(LST[task], LST[succ] - ins->getProcessingTime(task));
        }
    }

    // EST, LST -> min, max delays
    vector<pair<int, int>> bounds(N_EI);

    for (int tEI = 0; tEI < N_EI; tEI++) {
        int task = ins->ei_tasks[tEI];

        bounds[tEI].first = max(0, EST[task] - ins->tasks[task].get_release_date());
        bounds[tEI].second = max(bounds[tEI].first, LST[task] - ins->tasks[task].get_release_date());
    }

    return bounds;
}

vector<double> SolverGA::decodePriorities(const Chromosome& chrom) const {
    vector<double> priorities(N);

    for (int task = 0; task < N; task++) {
        priorities[task] = chrom[task];
    }

    return priorities;
}

vector<int> SolverGA::decodeDelays(const Chromosome& chrom) const {
    vector<int> eiDelays(N_EI);

    for (int tEI = 0; tEI < N_EI; tEI++) {
        int minDelay = eiDelayBounds[tEI].first;
        int maxDelay = eiDelayBounds[tEI].second;

        eiDelays[tEI] = minDelay + static_cast<int>(chrom[N + tEI] * (maxDelay - minDelay));
    }

    return eiDelays;
}
