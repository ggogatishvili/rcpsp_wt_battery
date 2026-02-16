/*

Copyright (c) 2025, Corentin JUVIGNY

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

*/

#pragma once

#include <config.h>
#include <list>
#include <fmt/format.h>
#include <sstream>
#include "instance.h"
#include <helpers.h>
#include <memory>

struct MachineBlock {
    int startTime;
    State startState;
    int endTime;
    State endState;

    bool isTransition() const {
        return startState != endState;
    }

    std::string getDescription() const {
        return isTransition()
               ? fmt::format("{} -> {}", state_name(startState), state_name(endState))
               : std::string(state_name(startState));
    }
};

struct SolutionStats
{
    unsigned int nbr_subproblems;
    unsigned int nbr_lazy;
    unsigned int cumul_mifs;
    double       gap_to_optimal;

    constexpr SolutionStats() noexcept
            : SolutionStats(defaultStats())
    { }
    constexpr SolutionStats(const double gap_to_optimal) noexcept
            : nbr_subproblems(0), nbr_lazy(0), cumul_mifs(0), gap_to_optimal(gap_to_optimal)
    { }
    constexpr SolutionStats(const unsigned int nbr_subproblems, const unsigned int nbr_lazy, const unsigned int cumul_mifs, const double gap_to_optimal) noexcept
            : nbr_subproblems(nbr_subproblems), nbr_lazy(nbr_lazy), cumul_mifs(cumul_mifs), gap_to_optimal(gap_to_optimal)
    { }

    static constexpr SolutionStats defaultStats() noexcept
    {
        return { 0, 0, 0, std::numeric_limits<double>::infinity() };
    }
};

class Solution
{
public:
    Solution() noexcept : Solution(infeasibleSolution()) { }

    Solution( const Instance* ins
            , const double ObjVal
            , const double energyCost
            , const unsigned int makespan
            , const std::vector<int>& taskAssignments
            , const std::vector<double>& batteryLevels
            , const std::vector<MachineBlock>& machineBlocks
            , const SolutionStats&& stats = SolutionStats::defaultStats());

    static inline Solution infeasibleSolution(const Instance* const ins = nullptr) noexcept
    {
        return Solution { ins, INFINITE, INFINITE, std::numeric_limits<unsigned int>::max(), {}, {}, {}, SolutionStats::defaultStats() };
    }

    double getObjVal() const { return objVal; }
    double getObjVal(const double alpha, const double lb_energy, const double lb_makespan) const { return alpha * lb_energy * energyCost + (1 - alpha) * lb_makespan * makespan; }
    double getEnergyCost() const { return energyCost; }
    unsigned int getMakespan() const { return makespan; }
    const SolutionStats& getStats() const & { return stats; }
    const std::vector<int>& getTaskAssignments() const & { return taskAssignments; }
    const std::vector<double>& getBatteryLevels() const & { return batteryLevels; }
    const std::vector<MachineBlock>& getMachineBlocks() const & { return machineBlocks; }
    const std::list<std::pair<State,State>>& getMachineTransitions() const & { return machineTransitions; }
    const Instance* getInstance() const { return ins; }
    void setObjVal(const double objval) { objVal = objval; }
    void setStats(const SolutionStats& newStats) { stats = newStats; }
    void setStats(const SolutionStats&& newStats) { stats = newStats; }
    bool isInfeasible() const { return objVal >= GRB_INFINITY && makespan == std::numeric_limits<unsigned int>::max(); }

private:
    const Instance* ins;
    double objVal;
    double energyCost;
    unsigned int makespan;
    std::vector<int> taskAssignments;
    std::vector<double> batteryLevels;
    std::vector<MachineBlock> machineBlocks;
    std::list<std::pair<State,State>> machineTransitions;
    SolutionStats stats;
};

// Formatter specialization for Solution class
template <>
struct fmt::formatter<Solution>
{
    constexpr auto parse(format_parse_context& ctx) -> decltype(ctx.begin())
    {
        return ctx.begin();
    }

    template <typename FormatContext>
    auto format(const Solution& s, FormatContext& ctx) const -> decltype(ctx.out())
    {
        std::stringstream ss;
        ss << "Solution: \n"
           << "   optimal value: " << s.getObjVal() << "\n"
           << "   energy cost:   " << s.getEnergyCost() << "\n"
           << "   makespan:      " << s.getMakespan() << "\n"
           << "   task assignments: ";
        for (int i = 0; i < s.getTaskAssignments().size(); i++, ss << " ")
            ss << fmt::format("({}, {})", i+1, s.getTaskAssignments()[i]);
        for (const auto& level : s.getBatteryLevels())
            ss << fmt::format("({:.2f}) ", level);
        for (const auto& block : s.getMachineBlocks())
            ss << fmt::format("[{}-{}: {}] ", block.startTime, block.endTime, block.getDescription());
        ss << std::endl;
        ss << "   Stats:\n";
        ss << fmt::format("      mip_gap: {:.3f}%\n", 100 * s.getStats().gap_to_optimal);
        return fmt::format_to(ctx.out(), "{}", ss.str());
    }
};