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

#include "Solver.h"
#include "SolverMILP.h"
#include "SolverH1.h"
#include "SolverGA.h"
#include "config.h"
#include <fmt/base.h>
#include <solution.h>


Solution solver::solve(const Instance* ins, const Config::ResolutionMethod method, const double alpha)
{
    try {
        switch ( method ) {
            case Config::ResolutionMethod::MILP:
                return SolverMILP(ins)();
            case Config::ResolutionMethod::H1:
                return SolverH1(ins)();
            case Config::ResolutionMethod::GA:
                return SolverGA(ins)();
            default:
                throw std::runtime_error("Unknown resolution method");
        }
    } catch ( const GRBException& err ) {
        fmt::println(stderr, "While solving instance {} with method = {} and ⍺ = {}", ins->instName(), method, alpha);
        fmt::println(stderr, "Error({}): {}", err.getErrorCode(), err.getMessage());
        return Solution::infeasibleSolution(ins);
    }
}