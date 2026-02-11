#include "RCPSPSolverHeuristic1.h"
#include <algorithm>
#include <vector>
#include <fmt/base.h>

using namespace std;

RCPSPSolverHeuristic1::RCPSPSolverHeuristic1(const Instance* const instance)
        : ins(instance)
{}


// ======================================================
// Main Solve
// ======================================================

Solution RCPSPSolverHeuristic1::_solve()
{
    const int H = ins->maxDuration();

    try
    {
        // Phase 1
        vector<int> startTimes = scheduleTasksSSGS();

        // For now placeholders
        vector<double> batteryLevels(H, 0.0);
        vector<MachineBlock> machineBlocks;

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
                        SolutionStats::defaultStats(),
                        Solution::NoUpdate);
    }
    catch (const std::exception& e)
    {
        fmt::println(stderr,
                     "Heuristic1 failed while solving instance {}: {}",
                     ins->instName(),
                     e.what());

        return Solution::infeasibleSolution(ins);
    }
}



// ======================================================
// Phase 1 — Serial Schedule Generation Scheme
// ======================================================

vector<int> RCPSPSolverHeuristic1::scheduleTasksSSGS()
{
    const int N = ins->nbr_tasks();
    const int H = ins->maxDuration();
    const int R = ins->nbr_resources();

    vector<int> startTimes(N, -1);
    vector<int> earliestStartTimes(N);
    vector<int> remainingPredecessors(N, 0);

    // Initialize the earliest start times with release dates
    for (int task = 0; task < N; task++) {
        earliestStartTimes[task] = ins->tasks[task].get_release_date();

        // EE tasks cannot start before offOn.time + 1
        if (ins->is_ee_task(task)) {
            earliestStartTimes[task] = max(earliestStartTimes[task], ins->offOn.time + 1);
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
    for (int currentTime = 0; currentTime < H; currentTime++)
    {
        if (unscheduledPrecedenceFreeTasks.empty() && scheduledCount < N) {
            // Error: No unscheduled precedence-free tasks, but not all tasks scheduled
            throw std::runtime_error(
                    fmt::format(
                            "Heuristic SSGS error: no unscheduled precedence-free tasks at time {} "
                            "but only {}/{} tasks scheduled. Possible cycle or precedence inconsistency.",
                            currentTime, scheduledCount, N
                    )
            );
        }

        // Build available set
        vector<int> availableTasks;

        for (int task : unscheduledPrecedenceFreeTasks)
        {
            if (currentTime < earliestStartTimes[task]) {
                continue;
            }

            int processingTime = ins->pt(task);

            if (currentTime + processingTime > H) {
                // Error: Task cannot be scheduled within horizon
                throw std::runtime_error(
                        fmt::format(
                                "Task {} cannot be scheduled at time {} (duration {}) — exceeds planning horizon {}.",
                                task, currentTime, processingTime, H
                        )
                );
            }

            bool resourcesAvailable = true;

            for (int i = currentTime; i < currentTime + processingTime; i++)
            {
                for (int resource = 0; resource < R; resource++)
                {
                    if (availableResources[i][resource] < ins->rt(task, resource))
                    {
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

        // EE clustering preference
        bool justAfterEE = (currentTime <= lastEnergyTaskEnd + 1);

        if (justAfterEE)
        {
            vector<int> eeOnly;
            for (int task : availableTasks) {
                if (ins->is_ee_task(task)) {
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

        int processingTime = ins->pt(selectedTask);

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
        for (int successor : ins->successors(selectedTask))
        {
            earliestStartTimes[successor] = max(earliestStartTimes[successor], currentTime + processingTime);

            remainingPredecessors[successor]--;

            if (remainingPredecessors[successor] == 0) {
                unscheduledPrecedenceFreeTasks.push_back(successor);
            }
        }

        if (ins->is_ee_task(selectedTask)) {
            lastEnergyTaskEnd = currentTime + processingTime - 1;
        }
    }

    return startTimes;
}