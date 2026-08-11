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

#include <atomic>
#include <map>
#include <string>

namespace lbbd
{

/**
 * @brief Cut and subproblem counters, shared by both decomposition solvers.
 *
 * Written from Gurobi's callback threads, so every counter is atomic. These
 * are the numbers the decomposition experiments actually compare — a method
 * that reaches the same objective with an order of magnitude fewer subproblem
 * solves is the interesting result, and it is invisible in the objective alone.
 *
 * `inconclusive` is the one to watch: it counts subproblem solves that came
 * back without a verdict (a time limit, or a backend failure). Anything above
 * zero means the reported gap no longer certifies optimality, and the analysis
 * scripts filter on it rather than silently averaging such runs in.
 */
struct CutStatistics
{
   std::atomic<unsigned> subproblems{0};
   std::atomic<unsigned> feasibilityCuts{0};
   std::atomic<unsigned> optimalityCuts{0};
   std::atomic<unsigned> batteryCuts{0};
   std::atomic<unsigned> batteryNodeCuts{0};
   std::atomic<unsigned> cumulMifs{0};
   std::atomic<unsigned> inconclusive{0};

   /// Flattened for Solution::setDiagnostic, hence the CSV.
   std::map<std::string, double> asDiagnostics() const
   {
      return {
         {"subproblems",       static_cast<double>(subproblems.load())},
         {"feasibility_cuts",  static_cast<double>(feasibilityCuts.load())},
         {"optimality_cuts",   static_cast<double>(optimalityCuts.load())},
         {"battery_cuts",      static_cast<double>(batteryCuts.load())},
         {"battery_node_cuts", static_cast<double>(batteryNodeCuts.load())},
         {"cumul_mifs",        static_cast<double>(cumulMifs.load())},
         {"inconclusive",      static_cast<double>(inconclusive.load())},
      };
   }
};

}
