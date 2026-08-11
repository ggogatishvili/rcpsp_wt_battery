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

#include <fmt/base.h>
#include <limits>
#include <optional>
#include <string>
#include <cstdint>

// Forward declaration — avoids pulling <gurobi_c++.h> into every TU.
// Callers that use the returned reference must include <gurobi_c++.h> themselves.
class GRBEnv;

#define VERSION "1.2.0"

class Config
{
public:
    // Resolution method class
    enum class ResolutionMethod
    {
        MILP,
        H1,
        H1P,
        GA,
        GAP,
        MatH,
        // Logic-based Benders decomposition (branch & check): ILP master on the
        // energy-intensive task placement + machine states, RCPSP subproblem on
        // everything else, battery applied as a post-processing step.
        LBBD,
        // The same algorithm with conflict refinement switched off, i.e. plain
        // no-good cuts over the whole fixing. This is the baseline the
        // "logic-based" part of LBBD is meant to beat, so it is a method in its
        // own right rather than a flag.
        NoGoodCuts,
        // Explicit-state master + battery LP contributing classical Benders
        // optimality cuts, so the EI placement becomes battery-aware.
        // docs/BENDERS_BATTERY.md.
        Benders,
        // The control for Benders: the same explicit-state master, but the
        // battery is post-processed exactly as in LBBD instead of cut into the
        // master. Comparing Benders against LBBD alone would confound two
        // changes -- battery coordination AND the loss of the SPACES switching
        // pre-processing -- so this arm exists to separate them.
        StateLBBD,
        None
    };

    static std::string to_string(const ResolutionMethod method)
    {
        switch ( method ) {
            case ResolutionMethod::MILP:       return "MILP";
            case ResolutionMethod::H1:         return "H1";
            case ResolutionMethod::H1P:        return "H1P";
            case ResolutionMethod::GA:         return "GA";
            case ResolutionMethod::GAP:        return "GAP";
            case ResolutionMethod::MatH:       return "MatH";
            case ResolutionMethod::LBBD:       return "LBBD";
            case ResolutionMethod::NoGoodCuts: return "NoGoodCuts";
            case ResolutionMethod::Benders:    return "Benders";
            case ResolutionMethod::StateLBBD:  return "StateLBBD";
            default:                           return "Undefined";
        }
    }

    // Process-wide Gurobi environment — created on first call, destroyed at exit.
    // All GRBModel objects must be built from this env.
    static GRBEnv& gurobiEnv();

    // Displays current configuration
    static void showConfig();

    // Parses command line arguments
    static void fromArgs(const int argc, const char* const argv[]);

    // Configuration settings and their default values

    // Input file: mandatory
    inline static std::string inputFile = "default_input_file.txt";

    // Output file: if not specified, no output file is generated. Json format.
    inline static std::optional<std::string> outputFile = std::nullopt;

    // Verbose mode: if true, prints Gurobi output
    inline static bool verbose = false;

    // When quiet, indicates if stats are printed or not
    inline static bool withStats = false;

    // Time limit: in seconds
    inline static long timeLimit = 3600;

    // Thread limit: number of threads used by Gurobi (Fixed at initialization, if compiled with GCC/Clang)
    inline static unsigned threadLimit = 12;

    // Memory limit (in Gb)
    inline static long memoryLimit = 25;

    // ⍺ in [0, 1]: ⍺ value of objective function
    inline static double alpha = 0.5;

    // Default resolution method
    inline static ResolutionMethod method = ResolutionMethod::MILP;

    // Battery capacity: in MWh
    inline static int batteryCapacity = 16;

    // Machine energy/transition profile (C2). Defaults reproduce the
    // values that used to be hardcoded in instance.h — archetype "A2" in
    // EXPERIMENTAL_PLAN.md §3.3. Overridable via --machine-profile <json>
    // and/or the individual flags below (individual flags win over the file).
    inline static std::optional<std::string> machineProfileFile = std::nullopt;
    inline static double eProc = 4;
    inline static double eIdle = 2;
    inline static double eOff  = 0;
    inline static int    offProcTime  = 2;
    inline static double offProcCost  = 5;
    inline static int    procOffTime  = 1;
    inline static double procOffCost  = 1;
    inline static int    procIdleTime = 1;
    inline static double procIdleCost = 2;
    inline static int    idleProcTime = 1;
    inline static double idleProcCost = 2.5;

    // Battery charge/discharge efficiency (C3)
    inline static double chargingEfficiency    = 0.95;
    inline static double dischargingEfficiency = 0.95;
    // C-rate (C4): max charge/discharge power as a multiple of capacity per
    // hour. Default: uncapped (legacy behaviour).
    inline static double cRate = std::numeric_limits<double>::infinity();

    // Tardiness cost scale (C5): multiplies every task weight on load.
    inline static double lambda = 1.0;

    // Force the battery empty at the end of the horizon. SolverMILP has always
    // done this; BatteryLp did not, which under negative prices let every
    // LP-based method bank revenue on energy never consumed and undercut the
    // exact MILP on the same schedule. Default true so the two agree; set
    // --battery-free-end to reproduce results produced before the fix.
    inline static bool batteryTerminalEmpty = true;

    // GA Parameters
    inline static std::optional<uint32_t> seed = std::nullopt;
    inline static int populationSize = 1500;
    inline static int stagnationLimit = 25;

    // Crossover High-Level Strategy Weights
    inline static int weightCrossSkip = 1;
    inline static int weightCrossPriorityOnly = 8;
    inline static int weightCrossDelayOnly = 1;
    inline static int weightCrossBoth = 6;

    // Mutation High-Level Strategy Weights
    inline static int weightMutSkip = 2;
    inline static int weightMutPriorityOnly = 9;
    inline static int weightMutDelayOnly = 5;
    inline static int weightMutBoth = 3;

    // Priority Mutation Weights
    inline static int weightMutPrioKeep = 10;
    inline static int weightMutPrioNew = 1;
    inline static int weightMutPrioShift = 7;

    // Priority Shift Magnitude
    inline static double mutPrioShiftMag = 0.01;

    // Delay Mutation Weights
    inline static int weightMutDelayKeep = 3;
    inline static int weightMutDelayZero = 1;
    inline static int weightMutDelayNewRandom = 6;
    inline static int weightMutDelayNewCheap = 5;
    inline static int weightMutDelayShift = 8;

    // Delay Shift Magnitude
    inline static double mutDelayShiftMag = 0.01;

    // Machine-state ladder (--states). Restricts which states the machine may
    // occupy *between* mandatory Proc blocks. It does NOT touch the boundary
    // conditions: constraints (3.14) force Off at t=0 and t=h-1 and those are
    // handled outside the SPACES graph, so "no Off" means "never shuts down
    // mid-schedule", which is the operational reading intended.
    //
    //   All      Off/Idle/Proc      full model (default, = sigma3)
    //   ProcIdle Idle/Proc          idles between jobs, never shuts down
    //   ProcOnly Proc               stays hot for the whole production window
    //
    // This is the sigma dimension of experiment E1: it is what makes the
    // machine-state x storage interaction measurable at all.
    enum class StateSet { All, ProcIdle, ProcOnly };

    inline static StateSet stateSet = StateSet::All;

    static std::string to_string(const StateSet s)
    {
        switch ( s ) {
            case StateSet::ProcIdle: return "proc,idle";
            case StateSet::ProcOnly: return "proc";
            default:                 return "all";
        }
    }

    // True when state `s` may be used for bridging between Proc blocks.
    static bool stateAllowed(const int s)
    {
        switch ( stateSet ) {
            case StateSet::ProcOnly: return s == 1;             // State::Proc
            case StateSet::ProcIdle: return s == 1 || s == 2;   // Proc, Idle
            default:                 return true;
        }
    }

    // H1P / GAP Parameters
    // --phase1-price-aware: pick EI start time that minimises energy+tardiness cost
    inline static bool phase1PriceAware = false;
    // --phase1-window: max delay window (in time units) when price-aware is on
    inline static int  phase1Window = 24;
    // --phase3-lp: replace greedy battery peak-shaving with an exact Gurobi LP
    inline static bool phase3LP = true;

    // LBBD / NoGoodCuts Parameters
    // Per-call time limit of the RCPSP subproblem, in seconds. The subproblem
    // is re-solved at every master incumbent, so this is the single most
    // important knob: too tight and the optimality cuts fall back to weak
    // bounds, too loose and the master barely branches.
    inline static double subproblemTimeLimit = 60.0;
    // Time budget for isolating a small conflicting subset of an infeasible
    // fixing (CP conflict refiner, or Gurobi IIS in the MILP backend). Only
    // spent on infeasible subproblems, and only for method LBBD.
    inline static double conflictRefinerTimeLimit = 10.0;
    // Seed the master with the H1 schedule. Costs one H1 run and guarantees an
    // incumbent even if the master times out.
    inline static bool lbbdWarmStart = true;
    // How many EI-ancestor tardiness bounds to write per task in the master
    // relaxation. Every such constraint is valid; keeping only the few with the
    // longest precedence paths keeps the master small without measurably
    // weakening it, since the optimality cuts close the rest.
    inline static int lbbdTardinessBoundsPerTask = 3;

    // Benders Parameters
    // Separate battery cuts at fractional nodes as well as at incumbents. The
    // battery LP is convex in the demand profile, so a cut taken at a
    // fractional machine-state solution is still a valid global underestimator
    // -- and it is where most of the early bound improvement comes from. Cheap
    // enough to leave on; the flag exists to measure what it is worth.
    inline static bool bendersNodeCuts = true;
    // Skip a node cut when the incumbent theta already satisfies it by more
    // than this margin, to stop the cut pool growing without bound.
    inline static double bendersCutTolerance = 1e-6;

    // MatH Parameters
    // Fraction of population re-evaluated with MILP per generation (0 = all H1, 1 = all MILP).
    // Recommended: 0.05–0.10; the MILP is far slower than H1.
    inline static double mathEliteRatio = 0.05;
    // Per-evaluation MILP time limit in seconds. A capped solve still returns the best
    // incumbent found, so the GA degrades gracefully when the limit is tight.
    inline static double mathMilpTimeLimit = 10.0;

private:
    // Private constructor ("Called once at program startup")
    [[gnu::constructor]] static void init_config();

    // Parses a string to a resolution method
    static ResolutionMethod parseResolutionMethod(const std::string& method);

    // Parses --states ("all" | "proc,idle" | "proc")
    static StateSet parseStateSet(const std::string& spec);
};

// Template specialization for fmt to format ResolutionMethod enum
template <>
struct fmt::formatter<Config::ResolutionMethod> : formatter<string_view>
{
    constexpr auto format(Config::ResolutionMethod method, format_context& ctx) const
    {
        string_view name = "unknown";
        switch ( method ) {
            case Config::ResolutionMethod::MILP:       name = "MILP";       break;
            case Config::ResolutionMethod::H1:         name = "H1";         break;
            case Config::ResolutionMethod::H1P:        name = "H1P";        break;
            case Config::ResolutionMethod::GA:         name = "GA";         break;
            case Config::ResolutionMethod::GAP:        name = "GAP";        break;
            case Config::ResolutionMethod::MatH:       name = "MatH";       break;
            case Config::ResolutionMethod::LBBD:       name = "LBBD";       break;
            case Config::ResolutionMethod::NoGoodCuts: name = "NoGoodCuts"; break;
            case Config::ResolutionMethod::Benders:    name = "Benders";    break;
            case Config::ResolutionMethod::StateLBBD:  name = "StateLBBD";  break;
            default: break;
        }
        return formatter<string_view>::format(name, ctx);
    }
};