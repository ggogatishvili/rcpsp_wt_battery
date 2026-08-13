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

#include "lbbd/MachineStateModel.h"

#include <algorithm>
#include <cmath>
#include <ranges>

#include <fmt/format.h>

#include "config.h"
#include "helpers.h"

namespace lbbd
{

MachineStateModel::MachineStateModel(const Instance* ins, GRBModel& model)
   : ins(ins)
   , h(ins->maxDuration())
{
   stateEnergy[static_cast<int>(State::Off)]  = ins->Off.cost;
   stateEnergy[static_cast<int>(State::Proc)] = ins->Proc.cost;
   stateEnergy[static_cast<int>(State::Idle)] = ins->Idle.cost;

   transition[static_cast<int>(State::Off)][static_cast<int>(State::Proc)]  = { ins->offProc.time,  ins->offProc.cost  };
   transition[static_cast<int>(State::Proc)][static_cast<int>(State::Off)]  = { ins->procOff.time,  ins->procOff.cost  };
   transition[static_cast<int>(State::Proc)][static_cast<int>(State::Idle)] = { ins->procIdle.time, ins->procIdle.cost };
   transition[static_cast<int>(State::Idle)][static_cast<int>(State::Proc)] = { ins->idleProc.time, ins->idleProc.cost };

   addVariables(model);
   model.update();
   addTimelineConstraints(model);
   buildEnergyExpressions();
}

void MachineStateModel::addVariables(GRBModel& model)
{
   rs.resize(static_cast<std::size_t>(3) * h);
   rx.resize(static_cast<std::size_t>(9) * h);
   ry.resize(static_cast<std::size_t>(9) * h);

   Loop(s, 3) Loop(i, h)
      rs[idx2(s, i)] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("rs_{}_{}", s, i));

   Loop(s1, 3) Loop(s2, 3) Loop(i, h)
   {
      rx[idx3(s1, s2, i)] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("rx_{}_{}_{}", s1, s2, i));
      ry[idx3(s1, s2, i)] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("ry_{}_{}_{}", s1, s2, i));
   }
}

void MachineStateModel::addTimelineConstraints(GRBModel& model)
{
   // Exactly one state or one in-flight transition per interval.
   Loop(i, h)
   {
      GRBLinExpr e = 0;
      Loop(s, 3) e += rs[idx2(s, i)];
      Loop(s1, 3) Loop(s2, 3) if ( s1 != s2 ) e += ry[idx3(s1, s2, i)];
      model.addConstr(e == 1.0, fmt::format("oneState_{}", i));
   }

   // Leaving a state means starting a transition out of it.
   Loop(i, h - 1) Loop(s1, 3)
   {
      GRBLinExpr next = 0;
      Loop(s2, 3) if ( s1 != s2 ) next += rx[idx3(s1, s2, i + 1)];
      model.addConstr(next >= rs[idx2(s1, i)] - rs[idx2(s1, i + 1)],
                      fmt::format("leave_{}_{}", s1, i));
   }

   // Transition mechanics. Intervals 0 and h-1 are the mandatory Off, so no
   // transition can start there.
   LoopFrom(i, 1, h - 1) Loop(s1, 3) Loop(s2, 3)
   {
      if ( s1 == s2 ) continue;
      const int duration = transition[s1][s2].time;

      if ( duration <= 0 || i + duration >= h ) {
         model.addConstr(rx[idx3(s1, s2, i)] == 0.0, fmt::format("noTransX_{}_{}_{}", s1, s2, i));
         model.addConstr(ry[idx3(s1, s2, i)] == 0.0, fmt::format("noTransY_{}_{}_{}", s1, s2, i));
         continue;
      }

      // It may only start from state s1, or from the end of a transition into s1.
      GRBLinExpr from = rs[idx2(s1, i - 1)];
      Loop(prev, 3)
      {
         if ( prev == s1 ) continue;
         const int d = transition[prev][s1].time;
         if ( d <= 0 || i - d < 0 ) continue;
         from += rx[idx3(prev, s1, i - d)];
      }
      model.addConstr(rx[idx3(s1, s2, i)] <= from, fmt::format("transFrom_{}_{}_{}", s1, s2, i));

      // ... and must land in state s2, or in another transition out of s2.
      GRBLinExpr to = rs[idx2(s2, i + duration)];
      Loop(next, 3)
      {
         if ( next == s2 ) continue;
         const int d = transition[s2][next].time;
         if ( d <= 0 || i + duration + d >= h ) continue;
         to += rx[idx3(s2, next, i + duration)];
      }
      model.addConstr(rx[idx3(s1, s2, i)] <= to, fmt::format("transTo_{}_{}_{}", s1, s2, i));

      // It occupies every interval of its duration.
      Loop(k, duration)
         model.addConstr(rx[idx3(s1, s2, i)] <= ry[idx3(s1, s2, i + k)],
                         fmt::format("transSpan_{}_{}_{}_{}", s1, s2, i, k));
   }

   Loop(i, h)
   {
      GRBLinExpr starts = 0;
      Loop(s1, 3) Loop(s2, 3) starts += rx[idx3(s1, s2, i)];
      model.addConstr(starts <= 1.0, fmt::format("oneTransStart_{}", i));
   }

   // Two different states never touch: a transition always separates them.
   Loop(i, h - 1) Loop(s1, 3) Loop(s2, 3)
   {
      if ( s1 == s2 ) continue;
      model.addConstr(rs[idx2(s1, i)] + rs[idx2(s2, i + 1)] <= 1.0,
                      fmt::format("noJump_{}_{}_{}", s1, s2, i));
   }

   model.addConstr(rs[idx2(static_cast<int>(State::Off), 0)] == 1.0, "startOff");
   model.addConstr(rs[idx2(static_cast<int>(State::Off), h - 1)] == 1.0, "endOff");

   // The --states ladder. It restricts which states may be occupied between
   // Proc blocks and must not touch the mandatory Off at the two boundaries,
   // which is why intervals 0 and h-1 are excluded here -- exactly the reading
   // SolverH1::findOptimalPath implements for the SPACES graph.
   if ( Config::stateSet != Config::StateSet::All )
      LoopFrom(i, 1, h - 1) Loop(s, 3)
         if ( !Config::stateAllowed(s) )
            model.addConstr(rs[idx2(s, i)] == 0.0, fmt::format("ladder_{}_{}", s, i));
}

void MachineStateModel::buildEnergyExpressions()
{
   energy.assign(h, GRBLinExpr{});
   Loop(i, h)
   {
      GRBLinExpr e = 0;
      Loop(s, 3) e += stateEnergy[s] * rs[idx2(s, i)];
      Loop(s1, 3) Loop(s2, 3)
         if ( s1 != s2 && transition[s1][s2].time > 0 )
            e += transition[s1][s2].cost * ry[idx3(s1, s2, i)];
      energy[i] = e;
   }
}

void MachineStateModel::requireProcWhile(GRBModel& model, const std::vector<GRBLinExpr>& runningExpr)
{
   // runningExpr[i] counts EI tasks occupying interval i. Resource 0 has
   // capacity 1, so that count never exceeds 1 and the constraint can be
   // written without the big-M SolverMILP uses -- which is both tighter and
   // one fewer place for N to leak into the formulation.
   Loop(i, std::min<int>(h, static_cast<int>(runningExpr.size())))
      model.addConstr(runningExpr[i] <= rs[idx2(static_cast<int>(State::Proc), i)],
                      fmt::format("procNeeded_{}", i));
}

GRBLinExpr MachineStateModel::rawEnergyCost() const
{
   return std::ranges::fold_left(
         std::views::iota(0, h)
      |  std::views::transform([&](const int i) { return ins->costs[i] * energy[i]; })
      , GRBLinExpr{}
      , std::plus<>()
   );
}

double MachineStateModel::energyCostLowerBound() const
{
   double maxDemand = *std::max_element(stateEnergy, stateEnergy + 3);
   Loop(s1, 3) Loop(s2, 3)
      if ( s1 != s2 && transition[s1][s2].time > 0 )
         maxDemand = std::max(maxDemand, transition[s1][s2].cost);

   // The battery can also buy energy at a negative price to charge. Even with
   // an uncapped C-rate that is bounded per interval by what fits in an empty
   // battery, so the bound stays finite -- the naive "cRate * capacity" form
   // would be infinite here and Gurobi would reject the variable.
   const double capacity  = static_cast<double>(ins->Battery.batteryCapacity);
   const double chargeCap = std::isfinite(ins->Battery.cRate)
      ? std::min(ins->Battery.cRate * capacity, capacity) / ins->Battery.chargingEfficiency
      : capacity / ins->Battery.chargingEfficiency;

   double bound = 0.0;
   Loop(i, h) bound += std::min(0.0, ins->costs[i]) * (maxDemand + chargeCap);
   return bound;
}

std::vector<MachineBlock> MachineStateModel::blocks() const
{
   std::vector<MachineBlock> out;

   const auto sweep = [&](auto& vars, const int s1, const int s2, auto index) {
      int start = -1;
      Loop(i, h)
      {
         const bool on = vars[index(i)].get(GRB_DoubleAttr_X) > 0.5;
         if ( on && start < 0 ) start = i;
         if ( start >= 0 && (!on || i == h - 1) ) {
            out.push_back({ start, static_cast<State>(s1), on ? i : i - 1, static_cast<State>(s2) });
            start = -1;
         }
      }
   };

   Loop(s, 3)  sweep(rs, s, s, [&](const int i) { return idx2(s, i); });
   Loop(s1, 3) Loop(s2, 3)
   {
      if ( s1 == s2 ) continue;
      sweep(ry, s1, s2, [&](const int i) { return idx3(s1, s2, i); });
   }

   std::ranges::sort(out, {}, &MachineBlock::startTime);
   return out;
}

}