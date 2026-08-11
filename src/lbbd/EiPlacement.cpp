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

#include "lbbd/EiPlacement.h"

#include <algorithm>
#include <functional>

#include <fmt/format.h>

#include "config.h"
#include "helpers.h"

namespace lbbd
{

EiPlacement::EiPlacement(const Instance* ins, const PrecedenceClosure* closure)
   : ins(ins)
   , closure(closure)
   , H(ins->maxDuration())
{
   // Interval 0 must be Off and interval H-1 must be Off, and the Off<->Proc
   // transitions are the only way in and out, so Proc can only occur strictly
   // between them. Both bounds are exact for this state model, not heuristic
   // tightenings.
   const int firstProc = 1 + ins->offProc.time;
   const int lastProc  = H - 2 - ins->procOff.time;

   iterate(j, ins->ei_tasks)
   {
      const int p     = ins->getProcessingTime(j);
      const int first = std::max(closure->earliestStart(j), firstProc);
      const int last  = std::min(closure->latestStart(j), lastProc - p + 1);
      ranges.emplace(j, std::make_pair(first, last));
      if ( first > last && badTask < 0 )
         badTask = j;
   }
}

void EiPlacement::addVariables(GRBModel& model, const StartCost& startCost)
{
   for (const auto& [task, range] : ranges)
   {
      std::vector<GRBVar> column;
      column.reserve(range.second - range.first + 1);
      LoopFrom(s, range.first, range.second + 1)
         column.push_back(model.addVar(0.0, 1.0, startCost(task, s), GRB_BINARY,
                                       fmt::format("x_{}_{}", task, s)));
      vars.emplace(task, std::move(column));
   }
}

bool EiPlacement::contains(const int task, const int start) const
{
   const auto it = ranges.find(task);
   return it != ranges.cend() && start >= it->second.first && start <= it->second.second;
}

const GRBVar& EiPlacement::var(const int task, const int start) const
{
   return vars.at(task)[start - ranges.at(task).first];
}

GRBLinExpr EiPlacement::startExpr(const int task) const
{
   const std::vector<GRBVar>& column = vars.at(task);
   const int first = ranges.at(task).first;
   GRBLinExpr e = 0;
   Loop(k, static_cast<int>(column.size())) e += (first + k) * column[k];
   return e;
}

std::vector<GRBLinExpr> EiPlacement::runningExpressions() const
{
   std::vector<GRBLinExpr> running(H, GRBLinExpr{});
   for (const auto& [task, column] : vars)
   {
      const int first = ranges.at(task).first;
      const int p     = ins->getProcessingTime(task);
      Loop(k, static_cast<int>(column.size()))
      {
         const int start = first + k;
         LoopFrom(i, start, std::min(H, start + p)) running[i] += column[k];
      }
   }
   return running;
}

void EiPlacement::addStructuralConstraints(GRBModel& model) const
{
   for (const auto& [task, column] : vars)
   {
      GRBLinExpr e = 0;
      for (const GRBVar& v : column) e += v;
      model.addConstr(e == 1.0, fmt::format("assign_{}", task));
   }

   iterate(j1, ins->ei_tasks) iterate(j2, ins->ei_tasks)
   {
      if ( !closure->isDescendant(j2, j1) ) continue;
      model.addConstr(startExpr(j2) - startExpr(j1) >= static_cast<double>(closure->minimalDistance(j1, j2)),
                      fmt::format("prec_{}_{}", j1, j2));
   }

   Loop(k, ins->nbr_resources())
   {
      // Resource 0 is the machine itself; with capacity 1 it is already implied
      // by whatever timeline model the master carries.
      if ( k == 0 && ins->resource_capacities[0] == 1 ) continue;
      Loop(i, H)
      {
         GRBLinExpr e = 0;
         bool nonEmpty = false;
         for (const auto& [task, column] : vars)
         {
            const int req = ins->rt(task, k);
            if ( req <= 0 ) continue;
            const int first = ranges.at(task).first;
            const int lo = std::max(first, i - ins->getProcessingTime(task) + 1);
            const int hi = std::min(ranges.at(task).second, i);
            LoopFrom(s, lo, hi + 1) { e += req * column[s - first]; nonEmpty = true; }
         }
         if ( nonEmpty )
            model.addConstr(e <= ins->resource_capacities[k], fmt::format("res_{}_{}", k, i));
      }
   }
}

void EiPlacement::addTardinessRelaxation(GRBModel& model, const GRBVar& q) const
{
   GRBLinExpr tardinessSum = 0;

   Loop(u, ins->nbr_tasks())
   {
      const double weight = ins->tasks[u].get_weight();
      if ( weight <= 0.0 ) continue;

      const int p   = ins->getProcessingTime(u);
      const int due = ins->tasks[u].get_due_date();

      std::vector<std::pair<long, int>> ancestors;   // (precedence distance, EI task)
      iterate(j, ins->ei_tasks)
         if ( j == u || closure->isDescendant(u, j) )
            ancestors.emplace_back(j == u ? 0L : closure->minimalDistance(j, u), j);
      std::ranges::sort(ancestors, std::greater{});
      if ( static_cast<int>(ancestors.size()) > Config::lbbdTardinessBoundsPerTask )
         ancestors.resize(Config::lbbdTardinessBoundsPerTask);

      const double staticBound = static_cast<double>(closure->earliestStart(u) + p - 1 - due);
      if ( ancestors.empty() && staticBound <= 0.0 ) continue;

      GRBVar tau = model.addVar(std::max(0.0, staticBound), GRB_INFINITY, 0.0, GRB_CONTINUOUS,
                                fmt::format("tau_{}", u));
      for (const auto& [distance, j] : ancestors)
         model.addConstr(tau >= startExpr(j) + static_cast<double>(distance) + p - 1 - due,
                         fmt::format("taudef_{}_{}", u, j));
      tardinessSum += weight * tau;
   }

   model.addConstr(q >= tardinessSum, "tardiness_lb");
}

bool EiPlacement::applyWarmStart(const std::vector<int>& startTimes)
{
   for (const auto& [task, range] : ranges)
      if ( task >= static_cast<int>(startTimes.size()) || !contains(task, startTimes[task]) )
         return false;

   for (auto& [task, column] : vars)
   {
      const int first = ranges.at(task).first;
      Loop(k, static_cast<int>(column.size()))
         column[k].set(GRB_DoubleAttr_Start, startTimes[task] == first + k ? 1.0 : 0.0);
   }
   return true;
}

}
