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

#include "solution.h"
#include <gurobi_c.h>

Solution::Solution( const Instance* ins
                  , const double ObjVal
                  , const std::vector<int>& taskAssignments
                  , const std::vector<double>& batteryLevels
                  , const std::vector<MachineBlock>& machineBlocks
                  , const SolutionStats&& stats)
   : ins(ins)
   , objVal(ObjVal)
   , taskAssignments(taskAssignments)
   , batteryLevels(batteryLevels)
   , machineBlocks(machineBlocks)
   , stats(stats)
{}