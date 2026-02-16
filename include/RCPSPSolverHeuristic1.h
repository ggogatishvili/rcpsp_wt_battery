#pragma once

#include <optional>
#include "instance.h"
#include "solution.h"
#include "Map1.h"
#include "Map2.h"
#include "Map3.h"

using namespace std;

struct Edge
{
    int fromTime;
    State fromState;

    int toTime;
    State toState;

    double cost;

    const bool isTransition() const {
        return fromState != toState;
    }
};

struct Interval { int start; int end; }; // inclusive

class RCPSPSolverHeuristic1
{
public:
    RCPSPSolverHeuristic1(const Instance* const instance);

    Solution solve()
    {
        return _solve();
    }


    inline Solution operator()()
    {
        return solve();
    }

    /* Compute the cost of a job j at time i */
    inline double cjob(const int j, const int i) const
    {
        return ins->cjob(j, i);
    }

private:
    // Pointer to the instance to solve
    const Instance* ins;

    /**
     * Phase 1: Schedule tasks while respecting precedence and resource constraints, using a heuristic approach (e.g., EDD with EI clustering).
     * @return vector of start times for each task, indexed by task ID
     */
    vector<int> scheduleTasks();

    /**
     * Phase 2: Given the scheduled tasks and their start times, determine the optimal machine state schedule (Proc, Idle, Off) over time to minimize energy costs while ensuring that the machine is in Proc state whenever an EI task is being processed.
     * @param startTimes vector of start times for each task, indexed by task ID
     * @return vector of MachineBlocks representing the machine state schedule, where each block indicates a contiguous time interval during which the machine is in a specific state (Proc, Idle, Off) or transitioning between states
     */
    vector<MachineBlock> scheduleMachineBlocks(const vector<int>& startTimes);

    /**
     * Build the SPACES graph representing all possible state transitions of the machine over time, along with their associated costs.
     * @return 2D graph where graph[time][state] is a list of outgoing edges from that time and state
     */
    vector<vector<vector<Edge>>> buildSPACESGraph();

    /**
     * Compute the intervals during which the machine must be in Proc state based on the scheduled tasks.
     * @param startTimes vector of start times for each task, indexed by task ID
     * @return vector of intervals (inclusive) during which the machine must be in Proc state
     */
    vector<Interval> getProcRequiredIntervals(const vector<int>& startTimes);

    /**
     * Given the SPACES graph, find the optimal path from a given start time and state to a given end time and state, minimizing the total cost of the path.
     * The start is inclusive meaning that the first MachineBlock in the returned path starts at startTime and is either in the startState or a transition from startState.
     * The end is exclusive meaning that the last MachineBlock in the returned path ends in endTime-1 and is eiter the same stable state as endState or a transition to the endState.
     * @param graph the SPACES graph representing machine state transitions over time
     * @param startTime  the starting time of the path (inclusive)
     * @param startState  the starting state of the machine at startTime
     * @param endTime  the ending time of the path (exclusive)
     * @param endState  the required state of the machine at endTime
     * @return vector of MachineBlocks representing the optimal path from (startTime, startState) to (endTime, endState), where each block indicates a contiguous time interval during which the machine is in a specific state or transitioning between states
     */
    vector<MachineBlock> getOptimalPath(const vector<vector<vector<Edge>>>& graph, int startTime, State startState, int endTime, State endState);


    // Intern solver function
    Solution _solve();
};