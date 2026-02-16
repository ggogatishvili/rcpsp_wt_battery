#include "RCPSPSolverHeuristic1.h"
#include <algorithm>
#include <vector>
#include <fmt/base.h>

using namespace std;

RCPSPSolverHeuristic1::RCPSPSolverHeuristic1(const Instance* const instance) : ins(instance) {}


Solution RCPSPSolverHeuristic1::_solve() {
    const int H = ins->maxDuration();

    try {
        // Phase 1
        vector<int> startTimes = scheduleTasks();

        // Phase 2
        vector<MachineBlock> machineBlocks = scheduleMachineBlocks(startTimes);

        // For now placeholders
        vector<double> batteryLevels(H, 0.0);

        double tardinessCost = 0.0;
        double energyCost = 0.0;
        double totalCost = tardinessCost + energyCost;

        return Solution(ins,
                        totalCost,
                        0.0,
                        0,
                        startTimes,
                        batteryLevels,
                        machineBlocks,
                        SolutionStats::defaultStats());
    }
    catch (const exception& e) {
        fmt::println(stderr,
                     "Heuristic1 failed while solving instance {}: {}",
                     ins->instName(),
                     e.what());

        return Solution::infeasibleSolution(ins);
    }
}


vector<int> RCPSPSolverHeuristic1::scheduleTasks() {
    const int N = ins->nbr_tasks();
    const int H = ins->maxDuration();
    const int R = ins->nbr_resources();

    vector<int> startTimes(N, -1);
    vector<int> earliestStartTimes(N);
    vector<int> remainingPredecessors(N, 0);

    // Initialize the earliest start times with release dates
    for (int task = 0; task < N; task++) {
        earliestStartTimes[task] = ins->tasks[task].get_release_date();

        // EI tasks cannot start before offOn.time + 1
        if (ins->is_ei_task(task)) {
            earliestStartTimes[task] = max(earliestStartTimes[task], ins->offProc.time + 1);
        }
    }

    // Count predecessors
    for (int task = 0; task < N; task++) {
        for (int successor : ins->successors(task)) {
            remainingPredecessors[successor]++;
        }
    }

    // Unscheduled tasks with no predecessors or all predecessors scheduled
    vector<int> unscheduledPrecedenceFreeTasks;
    for (int task = 0; task < N; task++) {
        if (remainingPredecessors[task] == 0) {
            unscheduledPrecedenceFreeTasks.push_back(task);
        }
    }

    // Resource availability
    vector<vector<int>> availableResources(H, vector<int>(R));
    for (int resource = 0; resource < R; resource++) {
        for (int i = 0; i < H; i++) {
            availableResources[i][resource] = ins->resource_capacities[resource];
        }
    }

    int scheduledCount = 0;
    int lastEnergyTaskEnd = -1;

    // Iterate over the planning horizon
    for (int currentTime = 0; currentTime < H; currentTime++) {
        if (unscheduledPrecedenceFreeTasks.empty() && scheduledCount < N) {
            // Error: No unscheduled precedence-free tasks, but not all tasks scheduled
            throw runtime_error(
                    fmt::format(
                            "Heuristic SSGS error: no unscheduled precedence-free tasks at time {} "
                            "but only {}/{} tasks scheduled. Possible cycle or precedence inconsistency.",
                            currentTime, scheduledCount, N
                    )
            );
        }

        // Build available set
        vector<int> availableTasks;

        for (int task : unscheduledPrecedenceFreeTasks) {
            if (currentTime < earliestStartTimes[task]) {
                continue;
            }

            int processingTime = ins->getProcessingTime(task);

            if (currentTime + processingTime > H) {
                // Error: Task cannot be scheduled within horizon
                throw runtime_error(
                        fmt::format(
                                "Task {} cannot be scheduled at time {} (duration {}) — exceeds planning horizon {}.",
                                task, currentTime, processingTime, H
                        )
                );
            }

            bool resourcesAvailable = true;

            for (int i = currentTime; i < currentTime + processingTime; i++) {
                for (int resource = 0; resource < R; resource++) {
                    if (availableResources[i][resource] < ins->rt(task, resource)) {
                        resourcesAvailable = false;
                        break;
                    }
                }

                if (!resourcesAvailable) {
                    break;
                }
            }

            if (resourcesAvailable) {
                availableTasks.push_back(task);
            }
        }

        if (availableTasks.empty()) {
            continue;
        }

        // EI clustering preference
        bool justAfterEITask = (currentTime <= lastEnergyTaskEnd + 1);

        if (justAfterEITask) {
            vector<int> eeOnly;
            for (int task : availableTasks) {
                if (ins->is_ei_task(task)) {
                    eeOnly.push_back(task);
                }
            }

            if (!eeOnly.empty()) {
                availableTasks = eeOnly;
            }
        }

        // Earliest Due Date (EDD)
        int selectedTask = *min_element(
                availableTasks.begin(),
                availableTasks.end(),
                [&](int a, int b)
                {
                    return ins->tasks[a].get_due_date() < ins->tasks[b].get_due_date();
                });

        // Schedule task
        startTimes[selectedTask] = currentTime;

        scheduledCount++;
        if (scheduledCount == N) {
            break;
        }

        int processingTime = ins->getProcessingTime(selectedTask);

        // Reserve resources
        for (int i = currentTime; i < currentTime + processingTime; i++) {
            for (int resource = 0; resource < R; resource++) {
                availableResources[i][resource] -= ins->rt(selectedTask, resource);
            }
        }

        // Remove from eligible
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

        if (ins->is_ei_task(selectedTask)) {
            lastEnergyTaskEnd = currentTime + processingTime - 1;
        }
    }

    return startTimes;
}




vector<MachineBlock> RCPSPSolverHeuristic1::scheduleMachineBlocks(const vector<int>& startTimes) {
    const int H = ins->maxDuration();

    //Build SPACES graph
    auto spacesGraph = buildSPACESGraph();

    // Compute when Proc is required
    auto procRequiredIntervals = getProcRequiredIntervals(startTimes);

    // Initialize machine blocks vector
    vector<MachineBlock> machineBlocks;

    // Initialize current time and state
    int currentTime = 0;
    State currentState = State::Off;

    // Iterate over required Proc intervals and find optimal paths between them
    for (const auto& procRequiredInterval : procRequiredIntervals) {
        // Find and add the optimal path from current time/state to just before the start of the required Proc interval
        if (currentTime < procRequiredInterval.start) {
            auto path= getOptimalPath(
                    spacesGraph,
                    currentTime,
                    currentState,
                    procRequiredInterval.start,
                    State::Proc
            );

            machineBlocks.insert(machineBlocks.end(), path.begin(), path.end());
        }

        // Add the required Proc block
        machineBlocks.emplace_back(
                procRequiredInterval.start,
                State::Proc,
                procRequiredInterval.end,
                State::Proc
        );

        currentTime = procRequiredInterval.end + 1;
        currentState = State::Proc;
    }

    if (currentTime >= H - 1) {
        throw runtime_error("Error in machine block scheduling: current time after scheduling required Proc intervals exceeds or equals horizon.");
    }

    // After last Proc block find and add the optimal path to the end of the horizon, which should end in Off state
    auto path = getOptimalPath(
            spacesGraph,
            currentTime,
            currentState,
            H - 1,
            State::Off
    );

    // The machine should be in Off state at the end of the horizon, so we can extend the last block if it ends in Off state
    // or add a new Off block if it ends with a transition to Off
    if (path.back().endState == State::Off) {
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


vector<Interval> RCPSPSolverHeuristic1::getProcRequiredIntervals(const vector<int>& startTimes) {
    const int H = ins->maxDuration();

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


vector<vector<vector<Edge>>> RCPSPSolverHeuristic1::buildSPACESGraph() {
    const int H = ins->maxDuration();
    const int S = 3;

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


vector<MachineBlock> RCPSPSolverHeuristic1::getOptimalPath(
        const vector<vector<vector<Edge>>>& graph,
        int startTime,
        State startState,
        int endTime,
        State endState
) {
    const int H = ins->maxDuration();
    const int S = 3;

    // Total cost to reach each node (time, state) initialized to infinity, except for the start node
    vector<vector<double>> totalPathCost(H, vector<double>(S, BIG_M));
    totalPathCost[startTime][(int)startState] = 0.0;

    // Best incoming edge to reach each node (time, state) for backtracking the optimal path
    vector<vector<Edge>> bestIncomingEdge(H, vector<Edge>(S));


    // Search for the optimal path using the shortest path algorithm
    // The graph is acyclic with edges going forward in time (there might be negative cost edges)
    // We can safely use a topological order traversal (time from start to end) to find the shortest path
    for (int t = startTime; t < endTime; t++) {
        for (int s = 0; s < S; s++) {
            // Skip unreachable nodes
            if (totalPathCost[t][s] >= BIG_M) {
                continue;
            }

            // Explore outgoing edges from the current node (t, s)
            for (const Edge& e : graph[t][s]) {
                // Skip edges that go beyond the end time
                if (e.toTime > endTime) {
                    continue;
                }

                double newCost = totalPathCost[t][s] + e.cost;

                // Update cost and best incoming edge if a cheaper path is found
                if (newCost < totalPathCost[e.toTime][(int)e.toState]) {
                    totalPathCost[e.toTime][(int)e.toState] = newCost;
                    bestIncomingEdge[e.toTime][(int)e.toState] = e;
                }
            }
        }
    }

    if (totalPathCost[endTime][(int)endState] >= BIG_M) {
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
