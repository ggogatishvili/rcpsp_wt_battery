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

#include "instance.h"
#include "solution.h"
#include <gurobi_c++.h>
#include <vector>
#include <memory>

struct DecoderResult {
   std::vector<int>    startTimes;
   std::vector<double> machineEnergyDemand; // energy units per time interval (no price)
   std::vector<MachineBlock> machineBlocks;
   double milpObjVal;
};

class SpacesMilpDecoder {
public:
   SpacesMilpDecoder(const Instance* ins);

   // Phase 1 (H1) fixes NI task start times; this MILP optimises only the
   // EI task start times and the machine-state schedule jointly, so EI tasks
   // can be placed at energy-optimal moments.  h1StartTimes is the full H1
   // schedule (all N tasks); NI entries are kept fixed, EI entries are the
   // warm start.
   DecoderResult solve(const std::vector<int>& h1StartTimes);

private:
   const Instance* ins;
   const int H, N;

   struct SpacesArc {
      int    fromTime;
      State  fromState;
      int    toTime;   // exclusive end: arc covers intervals [fromTime, toTime-1]
      State  toState;
      double arcCost;        // energy × cumulative price over arc span
      double energyPerUnit;  // energy demand per time unit during this arc
      bool isTransition() const { return fromState != toState; }
   };

   std::vector<SpacesArc>            arcs;
   std::vector<int>                  procDwellArcAt; // arcs[] index of Proc stay arc at time i; -1 if none
   std::vector<std::vector<int>>     nodeOutArcs;    // indexed by time*3+state
   std::vector<std::vector<int>>     nodeInArcs;     // indexed by time*3+state

   void buildSpacesArcs();

   static int nodeId(int time, State s) { return time * 3 + static_cast<int>(s); }
};