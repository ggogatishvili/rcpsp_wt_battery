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
#include <optional>
#include <string>
#include <cstdint>

// Forward declaration — avoids pulling <gurobi_c++.h> into every TU.
// Callers that use the returned reference must include <gurobi_c++.h> themselves.
class GRBEnv;

#define VERSION "1.1.0"

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
        None
    };

    static std::string to_string(const ResolutionMethod method)
    {
        switch ( method ) {
            case ResolutionMethod::MILP: return "MILP";
            case ResolutionMethod::H1:   return "H1";
            case ResolutionMethod::H1P:  return "H1P";
            case ResolutionMethod::GA:   return "GA";
            case ResolutionMethod::GAP:  return "GAP";
            case ResolutionMethod::MatH: return "MatH";
            default:                     return "None";
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

    // H1P / GAP Parameters
    // --phase1-price-aware: pick EI start time that minimises energy+tardiness cost
    inline static bool phase1PriceAware = false;
    // --phase1-window: max delay window (in time units) when price-aware is on
    inline static int  phase1Window = 24;
    // --phase3-lp: replace greedy battery peak-shaving with an exact Gurobi LP
    inline static bool phase3LP = false;

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
};

// Template specialization for fmt to format ResolutionMethod enum
template <>
struct fmt::formatter<Config::ResolutionMethod> : formatter<string_view>
{
    constexpr auto format(Config::ResolutionMethod method, format_context& ctx) const
    {
        string_view name = "unknown";
        switch ( method ) {
            case Config::ResolutionMethod::MILP: name = "MILP"; break;
            case Config::ResolutionMethod::H1:   name = "H1";   break;
            case Config::ResolutionMethod::H1P:  name = "H1P";  break;
            case Config::ResolutionMethod::GA:   name = "GA";   break;
            case Config::ResolutionMethod::GAP:  name = "GAP";  break;
            case Config::ResolutionMethod::MatH: name = "MatH"; break;
            default: break;
        }
        return formatter<string_view>::format(name, ctx);
    }
};