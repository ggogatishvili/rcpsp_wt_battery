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

#include <string_view>
#include <vector>

#include "instance.h"
#include "lbbd/BatteryPostprocess.h"
#include "lbbd/CutStatistics.h"
#include "solution.h"

namespace lbbd
{

/// Total weighted tardiness of a schedule. Completion of task u is
/// start + p - 1, matching SolverMILP and SolverH1::computeTardinessCost.
double weightedTardiness(const Instance* ins, const std::vector<int>& startTimes);

/// Warns on stderr unless the machine blocks tile [0, horizon) exactly once.
/// A violation means the timeline model and the block reconstruction disagree,
/// which would silently corrupt the energy profile and every figure built on
/// it — so it is checked on every solve, not only in debug builds.
void checkTiling(const std::vector<MachineBlock>& blocks, const int horizon, std::string_view who);

/// Warns on stderr when the master's own storage-free energy figure disagrees
/// with the one recomputed from the reconstructed schedule.
void checkEnergyAgreement(const double masterEnergy, const double rebuiltEnergy, std::string_view who);

/**
 * @brief Attaches everything the decomposition experiments need to read.
 *
 * Beyond the cut counters this exports two things the objective alone cannot
 * express:
 *
 *  - `energy_cost_no_battery`: what this exact schedule would cost with no
 *    storage. Together with the reported energy cost it gives the value the
 *    battery post-processing recovered, so the "LBBD with and without
 *    post-processing" comparison needs **one** run rather than two — the
 *    schedule is identical either way, since the master ignores storage in
 *    both cases.
 *
 *  - `bound` and `bound_is_battery_aware`: the master's dual bound, plus
 *    whether it is a valid bound on the *battery-aware* objective. For LBBD it
 *    is not (storage only lowers the bill, so the battery-free master bounds
 *    the wrong problem from the wrong side); for SolverBenders it is. Analyses
 *    must filter on this flag before quoting any optimality gap.
 */
void attachDiagnostics(Solution& solution,
                       const CutStatistics& stats,
                       const BatteryPlan& plan,
                       const double bound,
                       const bool boundIsBatteryAware);

/// One-line verbose summary of a decomposition solve.
void report(std::string_view who,
            const CutStatistics& stats,
            const BatteryPlan& plan,
            const double tardinessCost);

}
