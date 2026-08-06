#include "SolverH1.h"
#include "BatteryLp.h"
#include "config.h"
#include "helpers.h"
#include <algorithm>
#include <vector>
#include <fmt/base.h>
#include "queue"

using namespace std;

SolverH1::SolverH1(const Instance* const instance)
    : ins(instance), H(instance->maxDuration()), N(instance->nbr_tasks()), R(instance->nbr_resources()) {}


Solution SolverH1::_solve() {
    try {
        // Phase 1 (price-aware if flag is set)
        vector<int> startTimes = Config::phase1PriceAware
            ? scheduleTasks(true, Config::phase1Window)
            : scheduleTasks();

        // Phase 2
        vector<MachineBlock> machineBlocks = scheduleMachineUsage(startTimes);

        // Phase 2.5: Maximize negative energy price usage
        optimizeMachineBlocks(machineBlocks);

        // Phase 3
        vector<double> energyRequirements = getEnergyRequirements(machineBlocks);
        vector<double> batteryLevels;
        if (Config::phase3LP) {
            BatteryLp battLp(ins);
            auto opt = battLp.solve(energyRequirements);
            batteryLevels = opt ? std::move(*opt) : scheduleBatteryUsage(energyRequirements);
        } else {
            batteryLevels = scheduleBatteryUsage(energyRequirements);
        }


        auto tardinessCost = computeTardinessCost(startTimes);
        double energyCost = computeEnergyCost(energyRequirements, batteryLevels);
        double totalCost = tardinessCost + energyCost;


        return {
                ins,
                totalCost,
                energyCost,
                tardinessCost,
                startTimes,
                batteryLevels,
                machineBlocks,
                SolutionStats::defaultStats()
        };
    }
    catch (const exception& e) {
        fmt::println(stderr, "Heuristic1 failed while solving instance {}: {}", ins->instName(), e.what());

        return Solution::infeasibleSolution(ins);
    }
}




vector<int> SolverH1::scheduleTasks() {
    // Set earliest due dates as highest priorities
    vector<pair<double, int>> metrics;

    for (int task = 0; task < N; task++) {
        metrics.emplace_back(ins->tasks[task].get_due_date(), task);
    }

    sort(metrics.begin(), metrics.end());

    vector<double> priorities(N);
    for (int t = 0; t < N; t++) {
        int task = metrics[t].second;
        priorities[task] = 1.0 - ((double)t / (N - 1));
    }

    // Set 0 delays
    vector<int> eiDelays(ins->nbr_ei_tasks(), 0);

    return scheduleTasks(priorities, eiDelays);
}

vector<int> SolverH1::scheduleTasks(const vector<double>& priorities, const vector<int>& eiDelays) {
    return scheduleTasks(priorities, eiDelays, false, 0);
}

vector<int> SolverH1::scheduleTasks(bool priceAware, int wMax) {
    vector<pair<double,int>> metrics;
    metrics.reserve(N);
    for (int task = 0; task < N; task++)
        metrics.emplace_back(ins->tasks[task].get_due_date(), task);
    sort(metrics.begin(), metrics.end());

    vector<double> priorities(N);
    for (int t = 0; t < N; t++)
        priorities[metrics[t].second] = 1.0 - (static_cast<double>(t) / (N - 1));

    vector<int> eiDelays(ins->nbr_ei_tasks(), 0);
    return scheduleTasks(priorities, eiDelays, priceAware, wMax);
}

vector<int> SolverH1::scheduleTasks(const vector<double>& priorities, const vector<int>& eiDelays,
                                     bool priceAware, int wMax) {
    vector<int> startTimes(N, -1);
    vector<int> earliestStartTimes(N);
    vector<int> remainingPredecessors(N, 0);
    vector<int> unscheduledPrecedenceFreeTasks;
    vector<vector<int>> availableResources(H, vector<int>(R));

    // Initialize the earliest start times with release dates
    int tEI = 0;
    for (int task = 0; task < N; task++) {
        earliestStartTimes[task] = ins->tasks[task].get_release_date();

        // EI tasks cannot start before offProc.time + 1
        // 0 is stable off state, then offProc can start at 1 and lasts until (1 + offProc.time - 1),
        // then machine can be in proc state earliest (1 + offProc.time - 1 + 1) = offProc.time + 1
        if (ins->is_ei_task(task)) {
            earliestStartTimes[task] = max(earliestStartTimes[task] + eiDelays[tEI], ins->offProc.time + 1);
            tEI++;
        }
    }

    // Initialize remaining predecessors counts
    for (int task = 0; task < N; task++) {
        for (int successor : ins->successors(task)) {
            remainingPredecessors[successor]++;
        }
    }

    // Initialize unscheduled tasks with no predecessors or all predecessors scheduled
    for (int task = 0; task < N; task++) {
        if (remainingPredecessors[task] == 0) {
            unscheduledPrecedenceFreeTasks.push_back(task);
        }
    }

    // Initialize available resources
    for (int resource = 0; resource < R; resource++) {
        for (int i = 0; i < H; i++) {
            availableResources[i][resource] = ins->resource_capacities[resource];
        }
    }

    int scheduledCount = 0;
    int lastEiTaskEnd = -1;

    // Iterate over the planning horizon
    for (int currentTime = 0; currentTime < H; currentTime++) {
        if (unscheduledPrecedenceFreeTasks.empty() && scheduledCount < N) {
            // Error: No unscheduled precedence-free tasks, but not all tasks scheduled
            throw runtime_error(
                    fmt::format(
                            "Heuristic error: no unscheduled precedence-free tasks at time {} "
                            "but only {}/{} tasks scheduled. Possible cycle or precedence inconsistency.",
                            currentTime, scheduledCount, N
                    )
            );
        }

        // Get tasks that are ready to be scheduled at the current time (precedence-free and earliest start time <= current time)
        auto readyTasks = getReadyTasks(currentTime, unscheduledPrecedenceFreeTasks, earliestStartTimes);

        if (readyTasks.empty()) {
            continue;
        }

        // Get tasks that are ready and have available resources at the current time
        auto availableTasks = getAvailableTasks(currentTime, readyTasks, availableResources);

        // Try to schedule as many tasks as possible
        while (!availableTasks.empty()) {
            // Select a task to schedule
            auto selectedTask = selectTaskToSchedule(currentTime, lastEiTaskEnd, availableTasks, priorities);

            // Price-aware delay: EI tasks that begin a new cluster may be postponed
            // to a cheaper energy window within [currentTime, currentTime+wMax].
            if (priceAware && ins->is_ei_task(selectedTask) && currentTime > lastEiTaskEnd + 1) {
                int bestStart = findBestEIStart(selectedTask, currentTime, wMax,
                                                lastEiTaskEnd, availableResources);
                if (bestStart > currentTime) {
                    earliestStartTimes[selectedTask] = bestStart;
                    availableTasks.erase(
                        remove(availableTasks.begin(), availableTasks.end(), selectedTask),
                        availableTasks.end());
                    continue;
                }
            }

            // Schedule the selected task
            startTimes[selectedTask] = currentTime;
            scheduledCount++;

            int processingTime = ins->getProcessingTime(selectedTask);

            // Reserve resources
            for (int i = currentTime; i < currentTime + processingTime; i++) {
                for (int resource = 0; resource < R; resource++) {
                    availableResources[i][resource] -= ins->rt(selectedTask, resource);
                }
            }

            // Remove from availableTasks
            availableTasks.erase(
                    remove(availableTasks.begin(), availableTasks.end(), selectedTask),
                    availableTasks.end()
            );

            // Update availableTasks - resource availability has changed
            availableTasks = getAvailableTasks(currentTime, availableTasks, availableResources);

            // Remove from unscheduledPrecedenceFreeTasks
            unscheduledPrecedenceFreeTasks.erase(
                    remove(unscheduledPrecedenceFreeTasks.begin(), unscheduledPrecedenceFreeTasks.end(), selectedTask),
                    unscheduledPrecedenceFreeTasks.end()
            );

            // Update successors
            for (int successor : ins->successors(selectedTask)) {
                earliestStartTimes[successor] = max(earliestStartTimes[successor], currentTime + processingTime);

                remainingPredecessors[successor]--;

                if (remainingPredecessors[successor] == 0) {
                    unscheduledPrecedenceFreeTasks.push_back(successor);
                }
            }

            // Update EI clustering info
            if (ins->is_ei_task(selectedTask)) {
                lastEiTaskEnd = currentTime + processingTime - 1;
            }
        }

        // All tasks scheduled
        if (scheduledCount == N) {
            break;
        }
    }

    return startTimes;
}

int SolverH1::findBestEIStart(int task, int t0, int wMax, int lastEiEnd,
                               const vector<vector<int>>& availableResources) const {
    const int    p   = ins->getProcessingTime(task);
    const int    due = ins->tasks[task].get_due_date();
    const double w   = ins->tasks[task].get_weight();

    // Latest start keeping tardiness zero (only if t0 is already on-time).
    const int horizonUB = H - 1 - ins->procOff.time - p;
    const int dueUB     = (t0 + p - 1 <= due) ? (due - p + 1) : t0;
    const int wUB       = std::min({t0 + wMax, horizonUB, dueUB});

    if (wUB <= t0) return t0;

    // Cost estimate for EI task starting at s.
    // Includes: Proc execution energy, tardiness, Off->Proc transition energy.
    // Bridge hold (Off state, cost=0) is dominant over Idle; both included via min.
    auto estimateCost = [&](int s) -> double {
        const double execCost = static_cast<double>(ins->Proc.cost)
                              * ins->cumulative_cost(s, p);
        const double tardCost = w * static_cast<double>(std::max(0, s + p - 1 - due));
        // Off->Proc transition arriving just as task starts
        const int    ts       = s - ins->offProc.time;
        const double transCost = (ts >= 0)
            ? static_cast<double>(ins->offProc.cost)
              * ins->cumulative_cost(ts, ins->offProc.time)
            : 0.0;
        // Bridge gap [t0, ts-1]: machine Off or Idle; pick cheaper hold
        const int    holdLen  = std::max(0, ts - t0);
        const double offHold  = holdLen > 0
            ? static_cast<double>(ins->Off.cost)
              * ins->cumulative_cost(t0, holdLen) : 0.0;
        const double idleHold = holdLen > 0
            ? static_cast<double>(ins->Idle.cost)
              * ins->cumulative_cost(t0, holdLen) : 0.0;
        return execCost + tardCost + transCost + std::min(offHold, idleHold);
    };

    double bestCost  = estimateCost(t0);
    int    bestStart = t0;

    for (int s = t0 + 1; s <= wUB; ++s) {
        // Resource feasibility check at candidate start s
        bool feasible = true;
        for (int tau = s; tau < s + p && feasible; ++tau)
            for (int r = 0; r < R && feasible; ++r)
                if (availableResources[tau][r] < ins->rt(task, r))
                    feasible = false;
        if (!feasible) continue;

        const double c = estimateCost(s);
        if (c < bestCost - MyEPS) {
            bestCost  = c;
            bestStart = s;
        }
    }

    return bestStart;
}

vector<int> SolverH1::getReadyTasks(int currentTime, const vector<int>& unscheduledPrecedenceFreeTasks, const vector<int>& earliestStartTimes) {
    vector<int> readyTasks;

    for (int task : unscheduledPrecedenceFreeTasks) {
        if (currentTime < earliestStartTimes[task]) {
            continue;
        }

        int processingTime = ins->getProcessingTime(task);

        if (currentTime + processingTime - 1 > H - 1 - ins->procOff.time - 1) { // H - 1 must be Off and there must be time for procOff transition if we start the task at currentTime
            // Error: Task cannot be scheduled within horizon
            throw runtime_error(
                    fmt::format(
                            "Task {} cannot be scheduled at time {} (duration {}) — exceeds planning horizon {} (with respect to turning off the machine).",
                            task, currentTime, processingTime, H
                    )
            );
        }

        readyTasks.push_back(task);
    }

    return readyTasks;
}

vector<int> SolverH1::getAvailableTasks(int currentTime, const vector<int>& readyTasks, const vector<vector<int>>& availableResources) {
    vector<int> availableTasks;

    for (int task : readyTasks) {
        bool resourcesAvailable = true;

        int processingTime = ins->getProcessingTime(task);

        for (int i = currentTime; i < currentTime + processingTime && resourcesAvailable; i++) {
            for (int resource = 0; resource < R; resource++) {
                if (availableResources[i][resource] < ins->rt(task, resource)) {
                    resourcesAvailable = false;
                    break;
                }
            }
        }

        if (resourcesAvailable) {
            availableTasks.push_back(task);
        }
    }

    return availableTasks;
}

int SolverH1::selectTaskToSchedule(int currentTime, int lastEiTaskEnd, const vector<int>& availableTasks, const vector<double>& priorities) {
    const vector<int>* candidateTasks = &availableTasks;

    // EI clustering
    // If we are just after an EI task, we prefer to schedule another EI task if available to keep the machine in Proc state and avoid unnecessary transitions.
    bool justAfterEiTask = (currentTime <= lastEiTaskEnd + 1);

    vector<int> availableEiTasks;

    if (justAfterEiTask) {
        for (int task : availableTasks) {
            if (ins->is_ei_task(task)) {
                availableEiTasks.push_back(task);
            }
        }

        if (!availableEiTasks.empty()) {
            candidateTasks = &availableEiTasks;
        }
    }

    // Select highest priority
    int selectedTask = *max_element(
            candidateTasks->begin(),
            candidateTasks->end(),
            [&](int a, int b) {
                return priorities[a] < priorities[b];
            }
    );

    return selectedTask;
}

vector<MachineBlock> SolverH1::scheduleMachineUsage(const vector<int>& startTimes) {
    //Build SPACES graph
    auto spacesGraph = buildSPACESGraph();

    return scheduleMachineUsage(startTimes, spacesGraph);
}


vector<MachineBlock> SolverH1::scheduleMachineUsage(const vector<int>& startTimes, const vector<vector<vector<Edge>>>& spacesGraph) {
    // Compute when Proc is required
    auto procRequiredIntervals = computeProcRequiredIntervals(startTimes);

    // Initialize machine blocks vector
    vector<MachineBlock> machineBlocks;

    // Initialize current time and state
    int currentTime = 0;
    State currentState = State::Off;

    // Iterate over required Proc intervals and find optimal paths between them
    for (const auto& procRequiredInterval : procRequiredIntervals) {
        // Find and add the optimal path from current time/state to just before the start of the required Proc interval
        if (currentTime < procRequiredInterval.start) {
            // Interior bridge (Proc -> ... -> Proc): the state ladder applies.
            // The very first bridge starts from Off at t=0, which is mandatory
            // rather than a mid-schedule shutdown, so it is left unrestricted.
            const bool isFirstBridge = machineBlocks.empty();
            auto path= findOptimalPath(
                    spacesGraph,
                    currentTime,
                    currentState,
                    procRequiredInterval.start,
                    State::Proc,
                    !isFirstBridge
            );

            // If previous block is Proc and the first block of the path is also Proc, we can merge them into one block
            if (!machineBlocks.empty() &&
                !machineBlocks.back().isTransition() && machineBlocks.back().startState == State::Proc &&
                !path.front().isTransition() && path.front().startState == State::Proc)
            {
                // Extend previous block to cover first path block
                machineBlocks.back().endTime = path.front().endTime;

                // Insert remaining blocks (skip first)
                machineBlocks.insert(machineBlocks.end(), path.begin() + 1, path.end());
            }
            // Otherwise, insert all blocks from the path
            else {
                machineBlocks.insert(machineBlocks.end(), path.begin(), path.end());
            }
        }

        // Add the required Proc block
        if (!machineBlocks.empty() && !machineBlocks.back().isTransition() && machineBlocks.back().startState == State::Proc)
        {
            // Extend existing Proc block
            machineBlocks.back().endTime = procRequiredInterval.end;
        }
        else { // Add new Proc block
            machineBlocks.emplace_back(
                    procRequiredInterval.start,
                    State::Proc,
                    procRequiredInterval.end,
                    State::Proc
            );
        }

        currentTime = procRequiredInterval.end + 1;
        currentState = State::Proc;
    }

    if (currentTime >= H - 1) {
        throw runtime_error("Error in machine block scheduling: current time after scheduling required Proc intervals exceeds or equals horizon.");
    }

    // After last Proc block find and add the optimal path to the end of the horizon, which should end in Off state
    // Final bridge to the mandatory Off at t=h-1: unrestricted, for the same
    // reason as the first one.
    auto path = findOptimalPath(
            spacesGraph,
            currentTime,
            currentState,
            H - 1,
            State::Off,
            false
    );

    // The machine should be in Off state at the end of the horizon, so we can extend the last block if it ends in Off state
    // or add a new Off block if it ends with a transition to Off
    if (!path.back().isTransition()) {
        path.back().endTime = H - 1;
    } else {
        path.emplace_back(
                path.back().endTime + 1,
                State::Off,
                H - 1,
                State::Off
        );
    }

    machineBlocks.insert(machineBlocks.end(), path.begin(), path.end());


    return machineBlocks;
}

vector<Interval> SolverH1::computeProcRequiredIntervals(const vector<int>& startTimes) {
    // Initialize all time units as not requiring Proc
    vector<bool> requiredTimes(H, false);

    // Iterate over EI tasks and mark time units that require Proc state
    for (int t : ins->ei_tasks) {
        int startTime = startTimes[t];
        int processingTime = ins->getProcessingTime(t);

        for (int i = startTime; i < startTime + processingTime; i++)
            requiredTimes[i] = true;
    }

    // Initialize intervals vector
    vector<Interval> requiredIntervals;

    // Iterate over time units to find contiguous intervals of required Proc state
    for (int i = 0; i < H; i++) {
        if (!requiredTimes[i])
            continue;

        int j = i;
        while (j + 1 < H && requiredTimes[j + 1]) {
            j++;
        }

        requiredIntervals.push_back({i, j});
        i = j;
    }

    return requiredIntervals;
}

vector<vector<vector<Edge>>> SolverH1::buildSPACESGraph() {
    // 2D graph: graph[time][state] = list of outgoing edges
    vector<vector<vector<Edge>>> graph(H,vector<vector<Edge>>(S));

    for (int i = 0; i < H; i++) {
        for (int s = 0; s < S; s++) {
            auto currentState = (State)s;

            // Stay edge (duration 1)
            if (i + 1 < H) {
                double stateEnergyRequirement = 0.0; // per unit of time

                if (currentState == State::Proc)
                    stateEnergyRequirement = ins->Proc.cost;
                else if (currentState == State::Idle)
                    stateEnergyRequirement = ins->Idle.cost;
                else
                    stateEnergyRequirement = ins->Off.cost;

                double cost = stateEnergyRequirement * ins->costs[i];

                graph[i][s].push_back(
                        Edge{
                                i,
                                currentState,
                                i + 1,
                                currentState,
                                cost
                        });
            }

            // Transition edges
            for (int ns = 0; ns < S; ns++) {
                auto nextState = (State)ns;
                if (nextState == currentState) continue;

                int transitionDuration = 0;
                double transitionEnergyRequirement = 0.0; // per unit of time

                if (currentState == State::Off && nextState == State::Proc) {
                    transitionDuration = ins->offProc.time;
                    transitionEnergyRequirement = ins->offProc.cost;
                }
                else if (currentState == State::Proc && nextState == State::Off) {
                    transitionDuration = ins->procOff.time;
                    transitionEnergyRequirement = ins->procOff.cost;
                }
                else if (currentState == State::Proc && nextState == State::Idle) {
                    transitionDuration = ins->procIdle.time;
                    transitionEnergyRequirement = ins->procIdle.cost;
                }
                else if (currentState == State::Idle && nextState == State::Proc) {
                    transitionDuration = ins->idleProc.time;
                    transitionEnergyRequirement = ins->idleProc.cost;
                }
                else {
                    continue;
                }

                if (i + transitionDuration >= H) continue;

                double cost = transitionEnergyRequirement * ins->cumulative_cost(i, transitionDuration);

                graph[i][s].push_back(
                        Edge{
                                i,
                                currentState,
                                i + transitionDuration,
                                nextState,
                                cost
                        });
            }
        }
    }

    return graph;
}

vector<MachineBlock> SolverH1::findOptimalPath(
        const vector<vector<vector<Edge>>>& graph,
        int startTime,
        State startState,
        int endTime,
        State endState,
        bool restrictStates
) {
    // Machine-state ladder (Config::stateSet, --states) -- the sigma dimension
    // of experiment E1.
    //
    // The restriction is applied HERE, per bridge, and not when the graph is
    // built. Constraints (3.14) force the machine Off at t=0 and t=h-1, and
    // scheduleMachineUsage() realises that by routing the first bridge FROM
    // Off and the last bridge TO Off through this same graph. Filtering Off
    // out of the shared graph would therefore make those two bridges
    // infeasible and every run would abort.
    //
    // Excluding a state only for the interior bridges gives exactly the
    // intended operational reading: "never re-entered mid-schedule", i.e.
    //   proc,idle -> idles between jobs but is never shut down
    //   proc      -> stays hot for the whole production window
    // while the mandatory start-up and shut-down remain untouched.
    const auto allowed = [restrictStates](const int st) {
        return !restrictStates || Config::stateAllowed(st);
    };
    // Total cost to reach each node (time, state) initialized to infinity, except for the start node
    vector<vector<double>> totalPathCost(H, vector<double>(S, BIG_M));
    totalPathCost[startTime][(int)startState] = 0.0;

    // Best incoming edge to reach each node (time, state) for backtracking the optimal path
    vector<vector<Edge>> bestIncomingEdge(H, vector<Edge>(S));


    // Search for the optimal path using the shortest path algorithm
    // The graph is acyclic with edges going forward in time (there might be negative cost edges)
    // We can safely use a topological order traversal (time from start to end) to find the shortest path
    for (int i = startTime; i < endTime; i++) {
        for (int s = 0; s < S; s++) {
            // Skip unreachable nodes
            if (GREATEREQ(totalPathCost[i][s], BIG_M)) {
                continue;
            }

            // Skip states excluded by the machine-state ladder
            if (!allowed(s)) {
                continue;
            }

            // Explore outgoing edges from the current node (t, s)
            for (const Edge& e : graph[i][s]) {
                // Skip edges that go beyond the end time
                if (e.toTime > endTime) {
                    continue;
                }

                // ... and edges that would enter an excluded state
                if (!allowed((int)e.toState)) {
                    continue;
                }

                double newCost = totalPathCost[i][s] + e.cost;

                // Update cost and best incoming edge if a cheaper path is found
                if (LESSER(newCost, totalPathCost[e.toTime][(int)e.toState])) {
                    totalPathCost[e.toTime][(int)e.toState] = newCost;
                    bestIncomingEdge[e.toTime][(int)e.toState] = e;
                }
            }
        }
    }

    if (GREATEREQ(totalPathCost[endTime][(int)endState], BIG_M)) {
        throw runtime_error(
                fmt::format(
                        "No path found in SPACES graph from time {} state {} to time {} state {}.",
                        startTime, state_name(startState), endTime, state_name(endState)
                )
        );
    }

    // Backtracking

    // Initialize vector to hold the reversed path (from end to start)
    vector<MachineBlock> reversedBlocks;

    int currentTime = endTime;
    State currentState = endState;

    while (currentTime > startTime) {
        const Edge& currentEdge = bestIncomingEdge[currentTime][(int)currentState];

        int blockStartTime = currentEdge.fromTime;
        int blockEndTime   = currentEdge.toTime - 1;

        // If the last block exists and is same stable state, extend it
        if (!reversedBlocks.empty() && !reversedBlocks.back().isTransition() && !currentEdge.isTransition() && currentEdge.fromState == reversedBlocks.back().startState) {
            reversedBlocks.back().startTime = blockStartTime;
        }
        // Otherwise, add a new block for the current edge
        else {
            reversedBlocks.emplace_back(
                    blockStartTime,
                    currentEdge.fromState,
                    blockEndTime,
                    currentEdge.toState
            );
        }

        currentTime = currentEdge.fromTime;
        currentState = currentEdge.fromState;
    }

    // Reverse the blocks to get the correct order from start to end
    reverse(reversedBlocks.begin(), reversedBlocks.end());

    return reversedBlocks;
}



void SolverH1::optimizeMachineBlocks(vector<MachineBlock>& machineBlocks) {
    // Max usable energy from a fully charged battery
    double maxBatteryEnergy = ins->Battery.batteryCapacity * ins->Battery.dischargingEfficiency;

    // Iterate over machine blocks
    for (int currentBlockIndex = 0; currentBlockIndex < machineBlocks.size(); currentBlockIndex++) {
        auto& currentBlock = machineBlocks[currentBlockIndex];

        // We are looking for a transition turning the machine off
        if (!currentBlock.isTransition() || currentBlock.startState != State::Proc || currentBlock.endState != State::Off) {
            continue;
        }

        // Next block should be stable Off
        if (currentBlockIndex + 1 >= machineBlocks.size()) continue;
        auto& nextBlock = machineBlocks[currentBlockIndex + 1];
        if (nextBlock.isTransition() || nextBlock.startState != State::Off) continue;

        // Maximum extension allowed by the following Off block
        int maxExtension = nextBlock.endTime - nextBlock.startTime + 1;

        // There must be a stable off state at H-1, so procOff can end at most at H-2
        maxExtension = min(maxExtension, (H - 2) - currentBlock.endTime);

        if (maxExtension <= 0) continue;

        // Precompute original energy requirements
        vector<double> originalEnergyRequirements(H, 0.0);
        for (int bi = 0; bi <= currentBlockIndex; bi++) {
            for(int i = machineBlocks[bi].startTime; i <= machineBlocks[bi].endTime; i++) {
                originalEnergyRequirements[i] = machineBlocks[bi].getRequiredEnergyPerTimeUnit(ins);
            }
        }

        int bestExtension = 0;

        // Iterate over possible extensions
        for (int extension = 1; extension <= maxExtension; extension++) {
            // New start and end for the current procOff block
            int newStartForCurrentBlock = currentBlock.startTime + extension;
            int newEndForCurrentBlock = currentBlock.endTime + extension;

            // Find the last negative price before the new end time
            int lastNegativePriceTime = -1;
            for (int i = newEndForCurrentBlock; i >= 0; i--) {
                if (ins->costs[i] < 0) {
                    lastNegativePriceTime = i;
                    break;
                }
            }

            // No negative price found
            if (lastNegativePriceTime == -1) break;

            // Calculate energy consumed after the last negative price
            double energySum = 0.0;
            for (int i = lastNegativePriceTime + 1; i <= newEndForCurrentBlock; ++i) {
                if (i < currentBlock.startTime) {
                    energySum += originalEnergyRequirements[i]; // Original blocks before the original procOff transition
                } else if (i < newStartForCurrentBlock) {
                    energySum += ins->Proc.cost; // New Proc state during the extension gap
                } else {
                    energySum += currentBlock.getRequiredEnergyPerTimeUnit(ins); // The shifted procOff transition
                }
            }

            // Check if the battery can supply enough energy
            if (energySum <= maxBatteryEnergy) {
                bestExtension = extension;
            } else {
                break;
            }
        }

        // We can't extend
        if (bestExtension <= 0) continue;

        // Extend existing Proc state or inject a new one
        if (currentBlockIndex > 0 && !machineBlocks[currentBlockIndex - 1].isTransition() && machineBlocks[currentBlockIndex - 1].startState == State::Proc) {
            machineBlocks[currentBlockIndex - 1].endTime += bestExtension;
        } else {
            MachineBlock newProc(currentBlock.startTime, State::Proc, currentBlock.startTime + bestExtension - 1, State::Proc);
            machineBlocks.insert(machineBlocks.begin() + currentBlockIndex, newProc);
            currentBlockIndex++; // Advance index to point to the shifted procOff block
        }

        // Shift the current procOff block
        machineBlocks[currentBlockIndex].startTime += bestExtension;
        machineBlocks[currentBlockIndex].endTime += bestExtension;

        // Shrink the following stable Off state
        machineBlocks[currentBlockIndex + 1].startTime += bestExtension;

        // Clean up the Off state if fully used for the extension
        if (machineBlocks[currentBlockIndex + 1].startTime > machineBlocks[currentBlockIndex + 1].endTime) {
            machineBlocks.erase(machineBlocks.begin() + currentBlockIndex + 1);
        }
    }
}




vector<double> SolverH1::scheduleBatteryUsage(const vector<double>& energyRequirements) {
    vector<double> batteryLevels(H, 0.0);
    vector<bool> processed(H, false);

    int i = 0;
    while (true) {
        // Find next interval with energy requirements that has not been processed yet
        auto energyRequiredInterval = findNextEnergyRequiredInterval(i, energyRequirements, processed);

        if (energyRequiredInterval.start == -1) {
            break;
        }

        // Build max-heap of the costs for the current interval
        auto costMaxHeap = buildCostMaxHeapForInterval(energyRequiredInterval);

        while (!costMaxHeap.empty()) {

            auto peakTime = findUnprocessedPeakTime(costMaxHeap, processed);

            if (peakTime == -1) {
                break;
            }

            auto success = makePeakCheaper(peakTime, energyRequiredInterval, batteryLevels, processed, energyRequirements);

            // If the current peak could not be made cheaper, we don't need to try the same for the smaller peaks in the current interval
            if (!success) {
                break;
            }
        }

        i = energyRequiredInterval.end + 1;
    }

    return batteryLevels;
}

vector<double> SolverH1::getEnergyRequirements(const vector<MachineBlock> &machineBlocks) {
    vector<double> energyRequirements(H, 0.0);

    for (const auto& block : machineBlocks) {
        double perTimeUnit = block.getRequiredEnergyPerTimeUnit(ins);

        for (int i = block.startTime; i <= block.endTime; i++) {
            energyRequirements[i] = perTimeUnit;
        }
    }

    return energyRequirements;
}

Interval SolverH1::findNextEnergyRequiredInterval(int start, const vector<double> &energyRequirements, const vector<bool> &processed) {
    // Find the start of the next interval where energy is required and not yet processed
    int i = start;
    while (i < H && (EQUALS(energyRequirements[i], 0.0) || processed[i])) {
        i++;
    }

    if (i >= H) {
        return {-1, -1};
    }

    // Find the end of this interval
    int j = i;
    while (j + 1 < H && GREATER(energyRequirements[j + 1], 0.0) && !processed[j + 1]) {
        j++;
    }

    return {i, j};
}

priority_queue<pair<double, int>>SolverH1::buildCostMaxHeapForInterval(const Interval &interval) {
    priority_queue<pair<double,int>> maxHeap;

    for (int i = interval.start; i <= interval.end; i++) {
        maxHeap.emplace(ins->costs[i], i);
    }

    return maxHeap;
}

int SolverH1::findUnprocessedPeakTime(priority_queue<pair<double, int>>& costMaxHeap, const vector<bool>& processed) {
    int peakTime = -1;

    while (!costMaxHeap.empty()) {
        auto [cost, time] = costMaxHeap.top();
        costMaxHeap.pop();
        if (!processed[time]) {
            peakTime = time;
            break;
        }
    }

    return peakTime;
}

bool SolverH1::makePeakCheaper(int peakTime, const Interval& energyRequiredInterval, vector<double>& batteryLevels, vector<bool>& processed, const vector<double>& energyRequirements) {
    const double batteryCapacity = ins->Battery.batteryCapacity;
    const double chargingEfficiency  = ins->Battery.chargingEfficiency;
    const double dischargingEfficiency  = ins->Battery.dischargingEfficiency;
    const double roundtripEfficiency = chargingEfficiency * dischargingEfficiency;

    // Find the cheapest unprocessed time before the peak
    auto cheapestTimeBeforePeak = findCheapestUnprocessedTimeBeforePeak(peakTime, processed);

    // If the cheapest time before the peak is not cheap enough compared to the peak, we skip trying to charge before the peak and mark all times up to the peak as processed
    if (cheapestTimeBeforePeak == -1 || GREATEREQ(ins->costs[cheapestTimeBeforePeak], ins->costs[peakTime] * roundtripEfficiency)) {
        int j = peakTime;
        while (j >= 0 && !processed[j]) {
            processed[j] = true;
            j--;
        }
        markFollowingTimesAsProcessed(peakTime, processed);
        return false;
    }

    // Find a time before the peak that is cheap enough to charge the battery
    auto upperThreshold = computeUpperThresholdCostForCharging(peakTime, cheapestTimeBeforePeak, roundtripEfficiency);
    auto cheapEnoughTimeBeforePeak = findCheapEnoughTimeBeforePeak(peakTime, cheapestTimeBeforePeak, upperThreshold);
    if (cheapEnoughTimeBeforePeak <= energyRequiredInterval.start) {
        cheapEnoughTimeBeforePeak = cheapestTimeBeforePeak; // There can't be any more unprocessed peaks between the cheapest time and the peak, so we can safely use the cheapest time
    }
    double cheapEnoughCost =  ins->costs[cheapEnoughTimeBeforePeak];

    // Time interval around the peak which we are making cheaper by discharging the battery
    int currentTime = peakTime;
    int left = peakTime;
    int right = peakTime;

    double initialChargeAmount = 0.0; // The amount of charge we are putting in the battery during cheapEnoughTimeBeforePeak

    while (LESSER(initialChargeAmount, batteryCapacity)) {
        // Compute how much we need to charge the battery at cheapEnoughTimeBeforePeak to make the current time cheaper
        double currentEnergyRequirement = energyRequirements[currentTime]; // The amount of energy the machine needs at the current time
        double currentEnergyChargeRequirement = max(0.0, currentEnergyRequirement / dischargingEfficiency); // The amount of energy we need to put in the battery to cover the current energy requirement, considering the discharge efficiency
        double currentAvailableSpaceInBattery = max(0.0, batteryCapacity - initialChargeAmount); // The available space in the battery considering the charge we are already putting in at cheapEnoughTimeBeforePeak
        double currentChargeAmount = min(currentAvailableSpaceInBattery, currentEnergyChargeRequirement); // Resulting charge amount needed at cheapEnoughTimeBeforePeak to make the current time cheaper

        // Update the initial charge amount and the battery levels with the current charge amount
        initialChargeAmount += currentChargeAmount;
        for (int i = cheapEnoughTimeBeforePeak + 1; i <= currentTime; i++) {
            batteryLevels[i] += currentChargeAmount;
        }

        // Expand the time interval around the peak that we are making cheaper to the left or right (whichever is more expensive thus more beneficial to make cheaper)

        double nextLeftCost = (left - 1 >= energyRequiredInterval.start && left - 1 > cheapEnoughTimeBeforePeak &&
                               !processed[left - 1] && GREATER(ins->costs[left - 1] * roundtripEfficiency, cheapEnoughCost))
                               ? ins->costs[left-1]
                               : -BIG_M;

        double nextRightCost = (right + 1 <= energyRequiredInterval.end &&
                                !processed[right + 1] && GREATER(ins->costs[right + 1] * roundtripEfficiency, cheapEnoughCost))
                                ? ins->costs[right+1]
                                : -BIG_M;

        if (GREATER(nextLeftCost, nextRightCost) && GREATER(nextLeftCost, -BIG_M)) {
            left--;
            currentTime = left;
        }
        else if (GREATER(nextRightCost, -BIG_M)) {
            right++;
            currentTime = right;
        } else {
            break;
        }
    }

    // Mark the time units from charging to the last discharged time unit as processed
    for (int j = cheapEnoughTimeBeforePeak; j <= right; j++) {
        processed[j] = true;
    }

    // Also mark the following times as processed if the costs are descending, as it would not be worth trying to charge at these times in later iterations
    markFollowingTimesAsProcessed(right, processed);

    return true;
}

int SolverH1::findCheapestUnprocessedTimeBeforePeak(int peakTime, const vector<bool> &processed) {
    int cheapestTime = -1;
    double cheapestCost = BIG_M;

    int i = peakTime - 1;
    while (i >= 0 && !processed[i])
    {
        if (ins->costs[i] < cheapestCost) {
            cheapestCost = ins->costs[i];
            cheapestTime = i;
        }
        i--;
    }

    return cheapestTime;
}

void SolverH1::markFollowingTimesAsProcessed(int start, vector<bool> &processed) {
    int i = start + 1;

    while (i + 1 < H && ins->costs[i - 1] >= ins->costs[i] && ins->costs[i] >= ins->costs[i + 1]) {
        processed[i] = true;
        i++;
    }
}

double SolverH1::computeUpperThresholdCostForCharging(int peakTime, int cheapestTimeBeforePeak, double EFComplete) {
    vector<double> costs;

    for (int i = cheapestTimeBeforePeak; i <= peakTime; i++) {
        costs.push_back(ins->costs[i]);
    }

    sort(costs.begin(), costs.end());

    double percentile = 0.50;
    double threshold = costs[max(0, (int)(percentile * costs.size()))];

    threshold = min(threshold, ins->costs[peakTime] * EFComplete);

    return threshold;
}

int SolverH1::findCheapEnoughTimeBeforePeak(int peakTime, int cheapestTimeBeforePeak, double upperThresholdCost) {
    int cheapEnoughTime = -1;
    double cheapEnoughCost = BIG_M;

    bool below = false;

    for (int i = peakTime; i >= cheapestTimeBeforePeak; i--) {
        // Remember we crossed below the threshold
        if (ins->costs[i] < upperThresholdCost) {
            below = true;
        }

        // Break if we went back above the threshold after crossing below
        if (below && ins->costs[i] > upperThresholdCost) {
            break;
        }

        if (ins->costs[i] < cheapEnoughCost) {
            cheapEnoughCost = ins->costs[i];
            cheapEnoughTime = i;
        }
    }

    return cheapEnoughTime;
}




double SolverH1::computeTardinessCost(const vector<int>& startTimes) {
    double tardinessCost = 0.0;

    // Iterate over tasks and compute their tardiness cost if they are completed after their due date
    for (int t = 0; t < N; t++) {
        int completion = startTimes[t] + ins->getProcessingTime(t) - 1;
        int due = ins->tasks[t].get_due_date();

        if (completion > due) {
            tardinessCost += ins->tasks[t].get_weight() * (completion - due);
        }
    }

    return tardinessCost;
}

double SolverH1::computeEnergyCost(const vector<double>& energyRequirements, const vector<double>& batteryLevels) {
    const double chargingEfficiency  = ins->Battery.chargingEfficiency;
    const double dischargingEfficiency  = ins->Battery.dischargingEfficiency;

    double totalEnergyCost = 0.0;

    // Iterate over time units and compute the cost of energy from the grid used to charge the battery and directly used by the machine
    for (int i = 0; i < H; ++i) {
        double currentEnergyCost = ins->costs[i];

        // Compute the change in battery level during the current time unit
        double currentBatteryLevel = batteryLevels[i];
        double nextBatteryLevel = (i == H - 1 ? 0.0 : batteryLevels[i + 1]);
        double batteryDelta = nextBatteryLevel - currentBatteryLevel;

        // Add the cost of charging the battery
        if (batteryDelta > 0) {
            totalEnergyCost += currentEnergyCost * (batteryDelta / chargingEfficiency);
        }

        // Compute if and how much energy are we taking from the battery
        double batteryDischarge = batteryDelta < 0 ? -batteryDelta : 0.0;
        double energyFromBattery = batteryDischarge * dischargingEfficiency;

        // Compute how much energy we are using directly from the grid
        double directGridUsage = energyRequirements[i] - energyFromBattery;

        // Add cost of using energy from the grid directly for the machine
        if (directGridUsage > 0) {
            totalEnergyCost += currentEnergyCost * directGridUsage;
        }
    }

    return totalEnergyCost;
}
