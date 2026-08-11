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
#include <gurobi_c++.h>
#include <memory>
#include <optional>
#include <vector>

// Exact LP for Phase 3 battery scheduling.
// Built once per horizon; on each solve() call only the RHS of the
// demand-balance constraints is updated so Gurobi can warm-start from
// the previous basis — important for high-frequency GA fitness calls.
//
// The GRBEnv is shared process-wide via sharedEnv() (one Gurobi licence
// connection).  Each BatteryLp holds its own GRBModel so parallel threads
// can each own a BatteryLp without contention on the model.
// Optimal value and demand-row duals of one battery LP solve.
//
// The demand rows are the only place the machine's energy profile enters the
// model, so their duals are a subgradient of the optimal cost with respect to
// that profile: alpha_i is the marginal cost of one more energy unit at
// interval i *after* the battery has optimally responded. Because gMach_i >= 0
// carries cost price_i, its reduced cost gives alpha_i <= price_i -- the
// shadow tariff is nowhere above the raw tariff.
//
// SolverBenders turns these into Benders optimality cuts; see
// docs/BENDERS_BATTERY.md for the derivation.
struct BatteryDuals {
   double objVal = 0.0;
   std::vector<double> demandDual;   // alpha, one per interval
};

class BatteryLp {
public:
   explicit BatteryLp(const Instance* ins);

   // Solve the battery LP for the given machine energy demand profile.
   // Returns battery levels (same format as SolverH1::scheduleBatteryUsage).
   // Returns std::nullopt if Gurobi fails (caller should fall back to greedy).
   std::optional<std::vector<double>> solve(const std::vector<double>& eMach);

   // As solve(), but returns the optimal cost and the demand-row duals instead
   // of the level trace. Same model, same warm start; only the extraction
   // differs, so calling this in a hot loop costs the same as solve().
   std::optional<BatteryDuals> solveWithDuals(const std::vector<double>& eMach);

private:
   const Instance* ins;
   const int h;

   std::unique_ptr<GRBModel> model;

   std::vector<GRBVar>    gMach;
   std::vector<GRBVar>    gBatt;
   std::vector<GRBVar>    bMach;
   std::vector<GRBVar>    bLevel;
   std::vector<GRBConstr> demandConstrs;

   void build();
};