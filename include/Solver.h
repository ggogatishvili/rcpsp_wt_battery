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

#include "solution.h"
#include "instance.h"

namespace solver
{
   /**
    * @brief Solves the Resource Constrained Project Scheduling Problem (RCPSP).
    *
    * This function solves the RCPSP using a combination of approaches, including
    * lower bound generation and branch-and-bound with a variable makespan.
    *
    * The method parameter determines which approach to use:
    *
    * - CompactILP uses an ILP model that includes the original instance as well as all its extensions.
    * - NoGoodCuts uses a MIP model without no good cuts.
    * - LogicBenders uses a Benders decomposition with no good cuts.
    * - FreeCP uses a constraint programming model without any cuts.
    *
    * The alpha parameter determines the weight for the energy term in the fitness function.
    * The lower bounds on energy and makespan are also considered.
    *
    * @param ins The instance to solve.
    * @param method The resolution method to use.
    * @param alpha The weight for the energy term in the fitness function. Should be in [0,1].
    * @return A Solution object containing the optimal schedule.
    */
   Solution solve(const Instance* ins, const Config::ResolutionMethod method = Config::method, const double alpha = Config::alpha);
}