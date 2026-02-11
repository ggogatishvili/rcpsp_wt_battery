#pragma once

#include <optional>
#include "instance.h"
#include "solution.h"
#include "Map1.h"
#include "Map2.h"
#include "Map3.h"


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
    // Phase 1: Task scheduling (Serial SGS)
    std::vector<int> scheduleTasksSSGS();
    // Intern solver function
    Solution _solve();
};