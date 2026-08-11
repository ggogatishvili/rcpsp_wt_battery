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

#include "lbbd/SwitchingGraph.h"

#include <algorithm>
#include <limits>
#include <stdexcept>

#include <fmt/format.h>
#include <oneapi/tbb/parallel_for_each.h>

#include "config.h"
#include "helpers.h"

namespace
{
   constexpr double kInf = std::numeric_limits<double>::infinity();
}

namespace lbbd
{

bool SwitchingGraph::ladderActive()
{
   return Config::stateSet != Config::StateSet::All;
}

SwitchingGraph::SwitchingGraph(const Instance* ins, const std::vector<int>& sourceBoundaries)
   : ins(ins)
   , h(ins->maxDuration())
{
   if ( h < 2 )
      throw std::runtime_error("SwitchingGraph: horizon must span at least two intervals");

   buildArcs();

   dist.assign(static_cast<std::size_t>(h + 1) * static_cast<std::size_t>(h + 1), kInf);

   // Boundary 0 is always needed: it is where the mandatory start-up begins.
   std::vector<int> rows = sourceBoundaries;
   rows.push_back(0);
   std::ranges::sort(rows);
   const auto last = std::ranges::unique(rows);
   rows.erase(last.begin(), last.end());
   std::erase_if(rows, [this](const int i) { return i < 0 || i > h - 1; });

   // Cost of the mandatory Off during the very last interval. SolverH1 routes
   // its final bridge to (h-1, Off) and then extends that Off block to h-1, so
   // the last interval is always paid at the Off rate; c*'s last column has to
   // account for it too or the master would systematically under-price
   // shutting down.
   const double tailOffCost = ins->Off.cost * ins->costs[h - 1];

   tbb::parallel_for_each(rows.cbegin(), rows.cend(), [&](const int i)
   {
      const bool  isStartUpRow = (i == 0);
      const State fromState    = isStartUpRow ? State::Off : State::Proc;

      // Interior gaps obey the --states ladder; the mandatory start-up and
      // shut-down do not. When the ladder is off both dynamic programs are
      // identical, so only one is run.
      const bool restrictInterior = !isStartUpRow && ladderActive();

      const std::vector<double> interior = shortestFrom(i, fromState, restrictInterior);

      LoopFrom(j, i + 1, h)
      {
         dist[index(i, j)] = interior[node(j, static_cast<int>(State::Proc))];
      }

      // Second sweep only when the ladder actually differs; otherwise the two
      // dynamic programs are the same one and re-running it would be waste.
      const std::vector<double> unrestricted = restrictInterior
         ? shortestFrom(i, fromState, false)
         : std::vector<double>{};
      const std::vector<double>& toSink = restrictInterior ? unrestricted : interior;

      const double toOff = toSink[node(h - 1, static_cast<int>(State::Off))];
      dist[index(i, h)] = std::isfinite(toOff) ? toOff + tailOffCost : kInf;
   });
}

void SwitchingGraph::buildArcs()
{
   arcs.assign(static_cast<std::size_t>(h) * 3, {});

   // Per-interval energy demand of each stable state.
   const double stayEnergy[3] = { ins->Off.cost, ins->Proc.cost, ins->Idle.cost };

   // (duration, energy per interval) of each transition; duration 0 means the
   // transition does not exist. Index [from][to] using State's integer values.
   struct TransitionSpec { int time; double cost; };
   TransitionSpec trans[3][3] = {};
   trans[static_cast<int>(State::Off)][static_cast<int>(State::Proc)]  = { ins->offProc.time,  ins->offProc.cost  };
   trans[static_cast<int>(State::Proc)][static_cast<int>(State::Off)]  = { ins->procOff.time,  ins->procOff.cost  };
   trans[static_cast<int>(State::Proc)][static_cast<int>(State::Idle)] = { ins->procIdle.time, ins->procIdle.cost };
   trans[static_cast<int>(State::Idle)][static_cast<int>(State::Proc)] = { ins->idleProc.time, ins->idleProc.cost };

   // Every arc must land on a boundary <= h-1: interval h-1 is reserved for the
   // mandatory Off and is priced separately in the c* last column.
   Loop(t, h) Loop(s, 3)
   {
      auto& out = arcs[node(t, s)];

      if ( t + 1 <= h - 1 )
         out.push_back({ t + 1, static_cast<State>(s), stayEnergy[s] * ins->costs[t], false });

      Loop(s2, 3)
      {
         if ( s == s2 ) continue;
         const auto& spec = trans[s][s2];
         if ( spec.time <= 0 || t + spec.time > h - 1 ) continue;
         out.push_back({ t + spec.time, static_cast<State>(s2),
                         spec.cost * ins->cumulative_cost(t, spec.time), true });
      }
   }
}

std::vector<double> SwitchingGraph::shortestFrom(const int fromTime, const State fromState, const bool restrictStates) const
{
   std::vector<double> cost(static_cast<std::size_t>(h) * 3, kInf);
   cost[node(fromTime, static_cast<int>(fromState))] = 0.0;

   // The graph is a DAG whose arcs strictly increase time, so a single sweep in
   // increasing boundary order is a correct topological relaxation -- and it
   // stays correct with the negative electricity prices this instance family
   // contains, which is why no Dijkstra appears anywhere here.
   LoopFrom(t, fromTime, h) Loop(s, 3)
   {
      const double base = cost[node(t, s)];
      if ( !std::isfinite(base) ) continue;
      if ( restrictStates && !Config::stateAllowed(s) ) continue;

      for (const Arc& a : arcs[node(t, s)])
      {
         if ( restrictStates && !Config::stateAllowed(static_cast<int>(a.toState)) ) continue;
         double& target = cost[node(a.toTime, static_cast<int>(a.toState))];
         if ( base + a.cost < target )
            target = base + a.cost;
      }
   }

   return cost;
}

std::vector<MachineBlock> SwitchingGraph::path(const int i, const int j) const
{
   const bool  isStartUp = (i == 0);
   const bool  isShutDown = (j == h);
   const int   fromTime  = i;
   const State fromState = isStartUp ? State::Off : State::Proc;
   const int   toTime    = isShutDown ? h - 1 : j;
   const State toState   = isShutDown ? State::Off : State::Proc;
   const bool  restrictStates = !isStartUp && !isShutDown && ladderActive();

   std::vector<double> cost(static_cast<std::size_t>(h) * 3, kInf);
   std::vector<int>    predNode(static_cast<std::size_t>(h) * 3, -1);
   std::vector<char>   predIsTransition(static_cast<std::size_t>(h) * 3, 0);
   cost[node(fromTime, static_cast<int>(fromState))] = 0.0;

   LoopFrom(t, fromTime, toTime) Loop(s, 3)
   {
      const double base = cost[node(t, s)];
      if ( !std::isfinite(base) ) continue;
      if ( restrictStates && !Config::stateAllowed(s) ) continue;

      for (const Arc& a : arcs[node(t, s)])
      {
         if ( a.toTime > toTime ) continue;
         if ( restrictStates && !Config::stateAllowed(static_cast<int>(a.toState)) ) continue;
         const int to = node(a.toTime, static_cast<int>(a.toState));
         if ( base + a.cost < cost[to] ) {
            cost[to]             = base + a.cost;
            predNode[to]         = node(t, s);
            predIsTransition[to] = a.isTransition ? 1 : 0;
         }
      }
   }

   const int target = node(toTime, static_cast<int>(toState));
   if ( !std::isfinite(cost[target]) )
      throw std::runtime_error(fmt::format(
         "SwitchingGraph: no machine-state path covering intervals [{}, {}]", i, j - 1));

   std::vector<MachineBlock> reversed;
   int current = target;
   while ( current != node(fromTime, static_cast<int>(fromState)) )
   {
      const int previous = predNode[current];
      const int fromT = previous / 3, fromS = previous % 3;
      const int toT   = current  / 3, toS   = current  % 3;

      // Merge consecutive stay arcs of the same state into a single block.
      if ( !predIsTransition[current] && !reversed.empty()
           && !reversed.back().isTransition() && reversed.back().startState == static_cast<State>(fromS) )
         reversed.back().startTime = fromT;
      else
         reversed.push_back({ fromT, static_cast<State>(fromS), toT - 1, static_cast<State>(toS) });

      current = previous;
   }
   std::ranges::reverse(reversed);

   // The shut-down gap ends at boundary h-1; interval h-1 itself is the
   // mandatory Off that c*'s last column already paid for, so materialise it.
   if ( isShutDown ) {
      if ( reversed.empty() )
         reversed.push_back({ h - 1, State::Off, h - 1, State::Off });
      else if ( !reversed.back().isTransition() && reversed.back().startState == State::Off )
         reversed.back().endTime = h - 1;
      else
         reversed.push_back({ reversed.back().endTime + 1, State::Off, h - 1, State::Off });
   }

   return reversed;
}

double SwitchingGraph::procCost(const int task, const int start) const
{
   const int p = ins->getProcessingTime(task);
   if ( start < 0 || start + p > h )
      return kInf;
   return ins->Proc.cost * ins->cumulative_cost(start, p);
}

}
