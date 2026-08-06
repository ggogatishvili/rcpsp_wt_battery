#pragma once

#include "instance.h"
#include "solution.h"
#include "queue"

using namespace std;


struct Edge {
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


class SolverH1
{
public:
    SolverH1(const Instance* const instance);

    Solution solve()   { return _solve(); }
    Solution operator()() { return solve(); }

    // ── Phase 1 ─────────────────────────────────────────────────────────────

    vector<int> scheduleTasks();
    vector<int> scheduleTasks(const vector<double>& priorities, const vector<int>& eiDelays);
    // Price-aware variant: for EI tasks starting a new cluster, picks the
    // start in [t0, t0+wMax] that minimises estimated energy + tardiness cost.
    vector<int> scheduleTasks(const vector<double>& priorities, const vector<int>& eiDelays,
                               bool priceAware, int wMax);
    // EDD priorities + zero delays, with optional price-aware delay.
    vector<int> scheduleTasks(bool priceAware, int wMax);

    // ── Phase 2 ─────────────────────────────────────────────────────────────

    vector<MachineBlock> scheduleMachineUsage(const vector<int>& startTimes);
    vector<MachineBlock> scheduleMachineUsage(const vector<int>& startTimes,
                                               const vector<vector<vector<Edge>>>& spacesGraph);
    vector<vector<vector<Edge>>> buildSPACESGraph();
    void optimizeMachineBlocks(vector<MachineBlock>& machineBlocks);

    // ── Phase 3 ─────────────────────────────────────────────────────────────

    vector<double> getEnergyRequirements(const vector<MachineBlock>& machineBlocks);
    vector<double> scheduleBatteryUsage(const vector<double>& energyRequirements);

    // ── Cost ────────────────────────────────────────────────────────────────

    double computeTardinessCost(const vector<int>& startTimes);
    double computeEnergyCost(const vector<double>& energyRequirements,
                              const vector<double>& batteryLevels);

private:
    const Instance* ins;
    const int H;
    const int N;
    const int R;
    const int S = 3;

    Solution _solve();




    // Returns the best start time in [t0, min(t0+wMax, …)] for EI task t,
    // minimising estimated execution energy + tardiness + transition cost.
    int findBestEIStart(int task, int t0, int wMax, int lastEiEnd,
                        const vector<vector<int>>& availableResources) const;

    /**
     * Get the list of ready tasks at the current time, which are the tasks that are precedence-free (all predecessors have been scheduled) and can start at the current time based on their earliest start times.
     * @param currentTime the current time unit for which to determine the ready tasks
     * @param unscheduledPrecedenceFreeTasks vector of task IDs that are currently unscheduled but have no remaining predecessors
     * @param earliestStartTimes vector of earliest start times for each task, indexed by task ID, which indicates the earliest time at which each task can start based on release dates and precedence constraints
     * @return vector of task IDs from unscheduledPrecedenceFreeTasks that are ready to be scheduled at the current time
     */
    vector<int> getReadyTasks(int currentTime, const vector<int>& unscheduledPrecedenceFreeTasks, const vector<int>& earliestStartTimes);

    /**
     * Get the list of tasks from the given ready tasks that can be scheduled at the current time based on the availability of resources for the entire processing time of each task.
     * @param currentTime the current time unit for which to determine the available tasks
     * @param readyTasks vector of task IDs that are ready to be scheduled at the current time (precedence-free and earliest start time <= current time)
     * @param availableResources 2D vector of available resources for each time unit and resource type, indexed by time and resource, which indicates how much of each resource is available at each time unit
     * @return vector of task IDs from the readyTasks that can be scheduled at the current time based on resource availability for their entire processing time
     */
    vector<int> getAvailableTasks(int currentTime, const vector<int>& readyTasks, const vector<vector<int>>& availableResources);

    /**
     * Select a task to schedule from the given available tasks.
     * If we are just after an EI task, we prefer to schedule another EI task to keep the machine in Proc state and avoid unnecessary transitions.
     * We are using earliest due date (EDD) approach among either all available tasks or just the available EI tasks.
     * @param currentTime the current time unit for which to select a task to schedule
     * @param lastEiTaskEnd the end time of the last scheduled EI task, which can be used to determine if we are just after an EI task (e.g., if currentTime is equal to lastEiTaskEnd + 1)
     * @param availableTasks vector of task IDs that are available to be scheduled at the current time based on release date, precedence and resource constraints
     * @param priorities vector of priority values for each task, indexed by task ID, which can be used to break ties when selecting among candidate tasks (e.g., by preferring tasks with higher priority values)
     * @return the ID of the selected task
     */
    int selectTaskToSchedule(int currentTime, int lastEiTaskEnd, const vector<int>& availableTasks, const vector<double>& priorities);





    /**
     * Compute the intervals during which the machine must be in Proc state based on the scheduled tasks.
     * @param startTimes vector of start times for each task, indexed by task ID
     * @return vector of intervals (inclusive) during which the machine must be in Proc state
     */
    vector<Interval> computeProcRequiredIntervals(const vector<int>& startTimes);

    /**
     * Given the SPACES graph, find the optimal path from a given start time and state to a given end time and state, minimizing the total cost of the path.
     * The start is inclusive meaning that the first MachineBlock in the returned path starts at startTime and is either in the startState or a transition from startState.
     * The end is exclusive meaning that the last MachineBlock in the returned path ends in endTime -1 and is eiter the same stable state as endState or a transition to the endState.
     * @param graph the SPACES graph representing machine state transitions over time
     * @param startTime  the starting time of the path (inclusive)
     * @param startState  the starting state of the machine at startTime
     * @param endTime  the ending time of the path (exclusive)
     * @param endState  the required state of the machine at endTime
     * @return vector of MachineBlocks representing the optimal path from (startTime, startState) to (endTime, endState), where each block indicates a contiguous time interval during which the machine is in a specific state or transitioning between states
     */
    // restrictStates: apply the --states ladder (interior bridges only;
    // the mandatory boundary Off transitions must stay unrestricted).
    vector<MachineBlock> findOptimalPath(const vector<vector<vector<Edge>>>& graph, int startTime, State startState, int endTime, State endState, bool restrictStates = false);







    /**
     * Find the next maximal interval starting from 'start' (inclusive) during which the machine has any energy requirements
     * @param start the starting time from which to search for the next interval with energy requirements (inclusive)
     * @param energyRequirements vector of energy requirements for each time unit, indexed by time
     * @param processed vector of booleans indicating whether each time unit has already been processed, indexed by time
     * @return an Interval (inclusive) representing the next contiguous time interval during which the machine has energy requirements and has not yet been processed, or {-1, -1} if no such interval exists
     */
    Interval findNextEnergyRequiredInterval(int start, const vector<double>& energyRequirements, const vector<bool>& processed);

    /**
     * Build a max-heap of grid costs for the given interval, where each element in the heap is a pair of (cost, time) and the heap is ordered by cost in descending order.
     * @param interval an Interval (inclusive) representing the time interval for which to build the max-heap of grid costs
     * @return a priority_queue (max-heap) of pairs (cost, time) for each time unit in the given interval, ordered by cost in descending order
     */
    priority_queue<pair<double,int>> buildCostMaxHeapForInterval(const Interval& interval);

    /**
     * Find the time with the highest grid cost from the max-heap that has not yet been processed.
     * @param costMaxHeap a priority_queue (max-heap) of pairs (cost, time) for a given interval, ordered by cost in descending order
     * @param processed vector of booleans indicating whether each time unit has already been processed, indexed by time
     * @return the time with the highest grid cost from the max-heap that has not yet been processed, or -1 if all time units in the max-heap have been processed
     */
    int findUnprocessedPeakTime(priority_queue<pair<double,int>>& costMaxHeap, const vector<bool>& processed);

    /**
     * Attempt to make the given peak and the surrounding times units cheaper by scheduling battery usage.
     * @param peakTime the time unit representing the peak time that we want to make cheaper by scheduling battery usage
     * @param energyRequiredInterval the Interval (inclusive) representing the contiguous time interval during which the machine has energy requirements and includes the peakTime
     * @param batteryLevels vector of battery levels for each time unit, which will be updated if we schedule battery usage to make the peak cheaper
     * @param processed vector of booleans indicating whether each time unit has already been processed, which will be updated to mark time units as processed when we have scheduled battery usage or determined that the time units could not be used to make the cost cheaper
     * @param energyRequirements vector of energy requirements for each time unit, which may be used to determine how much battery usage is needed to make the peak cheaper
     * @return true if the peak was successfully made cheaper by scheduling battery usage, or false if it was determined that the peak cannot be made cheaper and the relevant time units have been marked as processed accordingly
     */
    bool makePeakCheaper(int peakTime, const Interval& energyRequiredInterval, vector<double>& batteryLevels, vector<bool>& processed, const vector<double>& energyRequirements);

    /**
     * Find the cheapest time unit before the given peak time that has not yet been processed.
     * @param peakTime the time unit representing the peak time for which to find the cheapest unprocessed time before it
     * @param processed vector of booleans indicating whether each time unit has already been processed, indexed by time
     * @return the time with the cheapest time before the peak or -1 if not found
     */
    int findCheapestUnprocessedTimeBeforePeak(int peakTime, const vector<bool>& processed);

    /**
     * Mark all the unprocessed following times after start (not included) as processed if the costs are descending (excluding the last cheapest time)
     * Reasoning: if the costs are descending after the start, then it is not worth trying to charge the battery at these times.
     * We keep the last cheapest time unprocessed because it could be still useful for later iterations.
     * @param start the starting time after which to mark following times as processed
     * @param processed vector of booleans indicating whether each time unit has already been processed, indexed by time
     */
    void markFollowingTimesAsProcessed(int start, vector<bool>& processed);

    /**
     * Compute the upper threshold cost for charging the battery at a time before the peak, based on the energy efficiency of charging and discharging the battery and the cost of the peak.
     * It should represent the maximum cost at which it would still be beneficial to charge the battery.
     * @param peakTime the time representing the peak time for which to compute the threshold cost for charging before it
     * @param cheapestTimeBeforePeak the time representing the cheapest unprocessed time before the peak time, which is being considered for charging the battery
     * @param EFComplete the complete energy efficiency of charging and discharging the battery (EF_charge * EF_discharge)
     * @return the upper threshold cost for charging the battery
     */
    double computeUpperThresholdCostForCharging(int peakTime, int cheapestTimeBeforePeak, double EFComplete);

    /**
     * Find a time between the cheapest time before the peak and the peak time that has a cost below the given threshold, which would make it beneficial to charge the battery at that time.
     * We don't necessarily want to use the cheapest time before the peak for charging, because it might be too far from the peak and it would block the usage of the battery for other peaks in between.
     * @param peakTime the time representing the peak time for which to find a cheap enough time before it for charging the battery
     * @param cheapestTimeBeforePeak the time representing the cheapest unprocessed time before the peak time, which is being considered for charging the battery
     * @param upperThresholdCost the upper threshold cost for charging the battery
     * @return a time between the cheapest time before the peak and the peak time that has a cost below the given threshold, or -1 if no such time exists
     */
    int findCheapEnoughTimeBeforePeak(int peakTime, int cheapestTimeBeforePeak, double upperThresholdCost);

};