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

#define VERSION "0.3.0"

class Config
{
public:
    // Resolution method class
    enum class ResolutionMethod
    {
        // Resolution is performed using the MILP
        MILP,
        HEURISTIC1,
        // No resolution method chosen
        None
    };

    static std::string to_string(ResolutionMethod method)
    {
        switch (method) {
            case ResolutionMethod::MILP:
                return "MILP";
            case ResolutionMethod::HEURISTIC1:
                return "HEURISTIC1";
            default:
                return "None";
        }
    }

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
    inline static double alpha = 1.0;

    // Default resolution method
    inline static ResolutionMethod method = ResolutionMethod::MILP;

    // Battery capacity: in MWh
    inline static int batteryCapacity = 16;

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
            case Config::ResolutionMethod::MILP:
                name = "MILP";
                break;
            case Config::ResolutionMethod::HEURISTIC1:
                name = "HEURISTIC1";
                break;
            default:
                break;
        }
        return formatter<string_view>::format(name, ctx);
    }
};