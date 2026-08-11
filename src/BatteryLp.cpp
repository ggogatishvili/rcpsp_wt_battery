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

#include "BatteryLp.h"
#include "config.h"
#include <cmath>
#include <fmt/base.h>

BatteryLp::BatteryLp(const Instance* ins)
   : ins(ins), h(ins->maxDuration())
{
   build();
}

void BatteryLp::build()
{
   const double ef_c = ins->Battery.chargingEfficiency;
   const double ef_d = ins->Battery.dischargingEfficiency;
   const double bmax = static_cast<double>(ins->Battery.batteryCapacity);

   // C-rate (C4): same bound logic as SolverMILP — see comment there.
   const bool   cRateCapped  = std::isfinite(ins->Battery.cRate);
   const double chargeCap    = cRateCapped ? (ins->Battery.cRate * bmax) / ef_c : GRB_INFINITY;
   const double dischargeCap = cRateCapped ? (ins->Battery.cRate * bmax)        : GRB_INFINITY;

   model = std::make_unique<GRBModel>(Config::gurobiEnv());
   model->set(GRB_IntParam_OutputFlag, 0);
   model->set(GRB_IntParam_Threads,    1);
   // Dual simplex recycles basis across repeated solves (key for GA speed).
   model->set(GRB_IntParam_Method,     1);

   gMach.reserve(h);
   gBatt.reserve(h);
   bMach.reserve(h);
   bLevel.reserve(h);

   for (int i = 0; i < h; ++i) {
      gMach.push_back(model->addVar(0.0,  GRB_INFINITY, ins->costs[i], GRB_CONTINUOUS, fmt::format("gM_{}", i)));
      gBatt.push_back(model->addVar(0.0,  chargeCap,    ins->costs[i], GRB_CONTINUOUS, fmt::format("gB_{}", i)));
      bMach.push_back(model->addVar(0.0,  dischargeCap, 0.0,           GRB_CONTINUOUS, fmt::format("bM_{}", i)));
      bLevel.push_back(model->addVar(0.0, bmax,         0.0,           GRB_CONTINUOUS, fmt::format("bL_{}", i)));
   }
   model->update();

   // Battery initialisation: bLevel[0] = 0 (as in SolverMILP)
   model->addConstr(bLevel[0] == 0.0, "bInit");
   // Battery end: bLevel[h-1] = 0 (as in SolverMILP)
   model->addConstr(bLevel[h-1] == 0.0, "bEnd");

   // Battery balance: bLevel[i] = bLevel[i-1] - bMach[i-1] + ef_c * gBatt[i-1]
   for (int i = 1; i < h; ++i)
      model->addConstr(bLevel[i] == bLevel[i-1] - bMach[i-1] + ef_c * gBatt[i-1], fmt::format("bBal_{}", i));

   // No charging at the last time unit (nothing to discharge into)
   model->addConstr(gBatt[h-1] == 0.0, "noChargeLast");

   // Terminal battery level. SolverMILP forces bLevel[h-1] == 0 ("BatteryEnd");
   // this model used to leave it free, and with negative prices in the instance
   // set that is not a harmless difference -- charging at a negative price
   // earns money, so a free terminal level lets every BatteryLp-based method
   // book revenue on energy the horizon never consumes, and undercut the exact
   // MILP on the very same schedule. Matching the MILP is the conservative
   // reading (the battery is a buffer, not a trading position) and keeps the
   // exact method exact. Config::batteryTerminalEmpty exists so the old
   // behaviour can be reproduced when re-checking results produced before this
   // constraint existed.
   if ( Config::batteryTerminalEmpty )
      model->addConstr(bLevel[h-1] == 0.0, "batteryEnd");

   // Demand balance: gMach[i] + ef_d * bMach[i] = eMach[i]
   // RHS is 0 here; updated to the actual eMach values before each solve.
   demandConstrs.reserve(h);
   for (int i = 0; i < h; ++i)
      demandConstrs.push_back(model->addConstr(gMach[i] + ef_d * bMach[i] == 0.0, fmt::format("dem_{}", i)));

   model->update();
}

std::optional<std::vector<double>> BatteryLp::solve(const std::vector<double>& eMach)
{
   try {
      for (int i = 0; i < h; ++i)
         demandConstrs[i].set(GRB_DoubleAttr_RHS, eMach[i]);

      model->optimize();

      const int status = model->get(GRB_IntAttr_Status);
      if ( status != GRB_OPTIMAL && status != GRB_SUBOPTIMAL )
         return std::nullopt;

      std::vector<double> battLevels(h);
      for (int i = 0; i < h; ++i)
         battLevels[i] = bLevel[i].get(GRB_DoubleAttr_X);
      return battLevels;

   } catch ( const GRBException& e ) {
      fmt::println(stderr, "BatteryLp: Gurobi error {}: {}", e.getErrorCode(), e.getMessage());
      return std::nullopt;
   } catch ( ... ) {
      fmt::println(stderr, "BatteryLp: unexpected error in solve()");
      return std::nullopt;
   }
}

std::optional<BatteryDuals> BatteryLp::solveWithDuals(const std::vector<double>& eMach)
{
   try {
      for (int i = 0; i < h; ++i)
         demandConstrs[i].set(GRB_DoubleAttr_RHS, eMach[i]);

      model->optimize();

      const int status = model->get(GRB_IntAttr_Status);
      if ( status != GRB_OPTIMAL )
         return std::nullopt;   // duals are only meaningful at an optimal basis

      BatteryDuals duals;
      duals.objVal = model->get(GRB_DoubleAttr_ObjVal);
      duals.demandDual.resize(h);
      for (int i = 0; i < h; ++i)
         duals.demandDual[i] = demandConstrs[i].get(GRB_DoubleAttr_Pi);
      return duals;

   } catch ( const GRBException& e ) {
      fmt::println(stderr, "BatteryLp: Gurobi error {}: {}", e.getErrorCode(), e.getMessage());
      return std::nullopt;
   } catch ( ... ) {
      fmt::println(stderr, "BatteryLp: unexpected error in solveWithDuals()");
      return std::nullopt;
   }
}