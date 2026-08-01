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

#include "SpacesMilpDecoder.h"
#include "config.h"
#include "Map2.h"
#include <fmt/base.h>
#include <algorithm>
#include <stdexcept>

using namespace std;

SpacesMilpDecoder::SpacesMilpDecoder(const Instance* ins)
   : ins(ins), H(ins->maxDuration()), N(ins->nbr_tasks())
{
   buildSpacesArcs();
}

void SpacesMilpDecoder::buildSpacesArcs()
{
   // Mirrors SolverH1::buildSPACESGraph() but enumerates arcs into a flat index.
   const int S = 3;
   const int numNodes = H * S;

   nodeOutArcs.assign(numNodes, {});
   nodeInArcs.assign(numNodes, {});
   procDwellArcAt.assign(H, -1);

   for (int i = 0; i < H; ++i) {
      for (int s = 0; s < S; ++s) {
         const auto cs = static_cast<State>(s);

         // Stay arc: (i, s) → (i+1, s)
         if (i + 1 < H) {
            double energyPerUnit = 0.0;
            if      (cs == State::Proc) energyPerUnit = ins->Proc.cost;
            else if (cs == State::Idle) energyPerUnit = ins->Idle.cost;
            else                        energyPerUnit = ins->Off.cost;

            const int a = static_cast<int>(arcs.size());
            arcs.push_back({i, cs, i + 1, cs,
                            energyPerUnit * ins->costs[i],
                            energyPerUnit});

            nodeOutArcs[nodeId(i,     cs)].push_back(a);
            nodeInArcs [nodeId(i + 1, cs)].push_back(a);

            if (cs == State::Proc) procDwellArcAt[i] = a;
         }

         // Transition arcs
         for (int ns = 0; ns < S; ++ns) {
            if (ns == s) continue;
            const auto next = static_cast<State>(ns);

            int    dur   = 0;
            double epu   = 0.0;
            if      (cs == State::Off  && next == State::Proc) { dur = ins->offProc.time;  epu = ins->offProc.cost;  }
            else if (cs == State::Proc && next == State::Off)  { dur = ins->procOff.time;  epu = ins->procOff.cost;  }
            else if (cs == State::Proc && next == State::Idle) { dur = ins->procIdle.time; epu = ins->procIdle.cost; }
            else if (cs == State::Idle && next == State::Proc) { dur = ins->idleProc.time; epu = ins->idleProc.cost; }
            else continue;

            if (i + dur >= H) continue;

            const int a = static_cast<int>(arcs.size());
            arcs.push_back({i, cs, i + dur, next,
                            epu * ins->cumulative_cost(i, dur),
                            epu});

            nodeOutArcs[nodeId(i,       cs  )].push_back(a);
            nodeInArcs [nodeId(i + dur, next)].push_back(a);
         }
      }
   }
}

DecoderResult SpacesMilpDecoder::solve(const std::vector<int>& h1StartTimes)
{
   // ── Pre-compute EI / NI split ─────────────────────────────────────────────

   const int N_EI = ins->nbr_ei_tasks();
   const int R    = ins->nbr_resources();

   // eiIdx[t] = index into ins->ei_tasks, or -1 for NI tasks
   std::vector<int> eiIdx(N, -1);
   for (int e = 0; e < N_EI; ++e)
      eiIdx[ins->ei_tasks[e]] = e;

   // Feasibility window [lb, ub] for each EI task.
   // lb: max of release date and latest NI-predecessor completion.
   // ub: min of (H - p) and earliest NI-successor start minus p.
   std::vector<int> lb(N_EI), ub(N_EI);
   for (int e = 0; e < N_EI; ++e) {
      const int eit = ins->ei_tasks[e];
      lb[e] = ins->tasks[eit].get_release_date();
      ub[e] = H - ins->getProcessingTime(eit);
   }
   for (int t = 0; t < N; ++t) {
      for (int s : ins->successors(t)) {
         if (eiIdx[t] < 0 && eiIdx[s] >= 0) {
            // NI predecessor → EI successor: tighten lb
            lb[eiIdx[s]] = std::max(lb[eiIdx[s]],
                                    h1StartTimes[t] + ins->getProcessingTime(t));
         }
         if (eiIdx[t] >= 0 && eiIdx[s] < 0) {
            // EI predecessor → NI successor: tighten ub
            ub[eiIdx[t]] = std::min(ub[eiIdx[t]],
                                    h1StartTimes[s] - ins->getProcessingTime(ins->ei_tasks[eiIdx[t]]));
         }
      }
   }

   // Precompute NI resource usage at each time unit
   std::vector<std::vector<int>> niUsage(H, std::vector<int>(R, 0));
   for (int t = 0; t < N; ++t) {
      if (eiIdx[t] >= 0) continue;
      const int p = ins->getProcessingTime(t);
      for (int tau = h1StartTimes[t]; tau < h1StartTimes[t] + p && tau < H; ++tau)
         for (int r = 0; r < R; ++r)
            niUsage[tau][r] += ins->rt(t, r);
   }

   // ── Model ────────────────────────────────────────────────────────────────

   GRBModel model{Config::gurobiEnv()};
   const int A = static_cast<int>(arcs.size());

   // x[e][i]: 1 if EI task e starts at time i (sparse: only i ∈ [lb[e], ub[e]])
   Map2<GRBVar> x;
   for (int e = 0; e < N_EI; ++e)
      for (int i = lb[e]; i <= ub[e]; ++i)
         x.set(e, i, model.addVar(0.0, 1.0, 0.0, GRB_BINARY,
                                  fmt::format("x_{}_{}", e, i)));

   // g[a]: unit-flow on SPACES arc a (continuous — TU network → integral LP)
   std::vector<GRBVar> g(A);
   for (int a = 0; a < A; ++a)
      g[a] = model.addVar(0.0, 1.0, 0.0, GRB_CONTINUOUS, fmt::format("g_{}", a));

   // tard[e]: weighted tardiness for EI task e
   std::vector<GRBVar> tard(N_EI);
   for (int e = 0; e < N_EI; ++e)
      tard[e] = model.addVar(0.0, GRB_INFINITY, 0.0, GRB_CONTINUOUS,
                             fmt::format("tard_{}", e));

   model.update();

   // ── Constraints ──────────────────────────────────────────────────────────

   // Eq. (start-once): each EI task starts exactly once within its window
   for (int e = 0; e < N_EI; ++e) {
      GRBLinExpr expr = 0;
      for (int i = lb[e]; i <= ub[e]; ++i) expr += x.i(e, i);
      model.addConstr(expr == 1, fmt::format("StartOnce_{}", e));
   }

   // Eq. (ei-prec): EI→EI precedences
   for (int e = 0; e < N_EI; ++e) {
      const int eit = ins->ei_tasks[e];
      for (int s : ins->successors(eit)) {
         if (eiIdx[s] < 0) continue; // EI→NI bound already in ub
         const int se = eiIdx[s];
         GRBLinExpr sa = 0, sb = 0;
         for (int i = lb[e];  i <= ub[e];  ++i) sa += i * x.i(e,  i);
         for (int i = lb[se]; i <= ub[se]; ++i) sb += i * x.i(se, i);
         model.addConstr(sa + ins->getProcessingTime(eit) <= sb,
                         fmt::format("EIPrec_{}_{}", e, se));
      }
   }

   // Eq. (res): residual resource capacity for EI tasks at each time unit
   for (int time = 0; time < H; ++time) {
      for (int r = 0; r < R; ++r) {
         const int avail = ins->resource_capacities[r] - niUsage[time][r];
         GRBLinExpr usage = 0;
         bool hasUsage = false;
         for (int e = 0; e < N_EI; ++e) {
            const int req = ins->rt(ins->ei_tasks[e], r);
            if (req == 0) continue;
            const int p = ins->getProcessingTime(ins->ei_tasks[e]);
            const int i2lo = std::max(lb[e], time - p + 1);
            const int i2hi = std::min(ub[e], time);
            for (int i2 = i2lo; i2 <= i2hi; ++i2) {
               usage += req * x.i(e, i2);
               hasUsage = true;
            }
         }
         if (hasUsage)
            model.addConstr(usage <= avail, fmt::format("Res_{}_{}", time, r));
      }
   }

   // SPACES flow conservation (unchanged — covers full horizon)
   {
      GRBLinExpr outflow = 0;
      for (int a : nodeOutArcs[nodeId(0, State::Off)]) outflow += g[a];
      model.addConstr(outflow == 1, "FlowSource");
   }
   {
      GRBLinExpr inflow = 0;
      for (int a : nodeInArcs[nodeId(H - 1, State::Off)]) inflow += g[a];
      model.addConstr(inflow == 1, "FlowSink");
   }
   for (int t = 0; t < H; ++t) {
      for (int s = 0; s < 3; ++s) {
         const int nid = nodeId(t, static_cast<State>(s));
         if (t == 0 && s == static_cast<int>(State::Off)) continue;
         if (t == H - 1 && s == static_cast<int>(State::Off)) continue;
         if (nodeOutArcs[nid].empty() && nodeInArcs[nid].empty()) continue;
         GRBLinExpr inflow = 0, outflow = 0;
         for (int a : nodeInArcs [nid]) inflow  += g[a];
         for (int a : nodeOutArcs[nid]) outflow += g[a];
         model.addConstr(inflow == outflow, fmt::format("FlowCons_{}_{}", t, s));
      }
   }

   // Eq. (couple): proc-dwell arc flow ≥ sum of EI running indicators
   if (!ins->ei_tasks.empty()) {
      for (int i = 0; i < H - 1; ++i) {
         const int pa = procDwellArcAt[i];
         if (pa < 0) continue;
         GRBLinExpr eiRunning = 0;
         for (int e = 0; e < N_EI; ++e) {
            const int p    = ins->getProcessingTime(ins->ei_tasks[e]);
            const int i2lo = std::max(lb[e], i - p + 1);
            const int i2hi = std::min(ub[e], i);
            for (int i2 = i2lo; i2 <= i2hi; ++i2)
               eiRunning += x.i(e, i2);
         }
         model.addConstr(g[pa] >= eiRunning, fmt::format("Couple_{}", i));
      }
   }

   // Eq. (tard): tardiness for EI tasks
   for (int e = 0; e < N_EI; ++e) {
      const int eit = ins->ei_tasks[e];
      const int p   = ins->getProcessingTime(eit);
      const int due = ins->tasks[eit].get_due_date();
      GRBLinExpr completion = 0;
      for (int i = lb[e]; i <= ub[e]; ++i) completion += (i + p - 1) * x.i(e, i);
      model.addConstr(tard[e] >= completion - due, fmt::format("Tard_{}", e));
      model.addConstr(tard[e] >= 0,                fmt::format("TardNN_{}", e));
   }

   // ── Objective: EI tardiness + machine arc costs ───────────────────────────

   GRBLinExpr obj = 0;
   for (int e = 0; e < N_EI; ++e)
      obj += ins->tasks[ins->ei_tasks[e]].get_weight() * tard[e];
   for (int a = 0; a < A; ++a)
      obj += arcs[a].arcCost * g[a];
   model.setObjective(obj, GRB_MINIMIZE);

   // ── Warm start from H1 EI schedule ───────────────────────────────────────

   for (int e = 0; e < N_EI; ++e) {
      const int s = h1StartTimes[ins->ei_tasks[e]];
      if (s >= lb[e] && s <= ub[e])
         x.i(e, s).set(GRB_DoubleAttr_Start, 1.0);
   }

   // ── Solver parameters ────────────────────────────────────────────────────

   model.set(GRB_DoubleParam_TimeLimit,   Config::mathMilpTimeLimit);
   model.set(GRB_IntParam_Threads,        1);
   model.set(GRB_IntParam_OutputFlag,     0);
   model.set(GRB_IntParam_NumericFocus,   2);

   // ── Solve ────────────────────────────────────────────────────────────────

   try {
      model.optimize();
   } catch (const GRBException& e) {
      fmt::println(stderr, "SpacesMilpDecoder::solve Gurobi error: {}", e.getMessage());
      throw;
   }

   if (model.get(GRB_IntAttr_SolCount) == 0)
      throw std::runtime_error("SpacesMilpDecoder: no feasible solution found");

   // ── Solution extraction ───────────────────────────────────────────────────

   DecoderResult res;
   res.milpObjVal = model.get(GRB_DoubleAttr_ObjVal);

   // NI start times come from H1; EI start times come from the MILP
   res.startTimes = h1StartTimes;
   for (int e = 0; e < N_EI; ++e)
      for (int i = lb[e]; i <= ub[e]; ++i)
         if (x.i(e, i).get(GRB_DoubleAttr_X) > 0.5)
            res.startTimes[ins->ei_tasks[e]] = i;

   // Machine energy demand and machine blocks from SPACES arc flows
   res.machineEnergyDemand.assign(H, 0.0);

   struct ActiveArc { int idx; int fromTime; };
   std::vector<ActiveArc> active;
   for (int a = 0; a < A; ++a)
      if (g[a].get(GRB_DoubleAttr_X) > 0.5)
         active.push_back({a, arcs[a].fromTime});
   std::ranges::sort(active, {}, &ActiveArc::fromTime);

   for (const auto& [a, _] : active) {
      const auto& arc = arcs[a];
      for (int i = arc.fromTime; i < arc.toTime && i < H; ++i)
         res.machineEnergyDemand[i] = arc.energyPerUnit;
   }

   for (const auto& [a, ft] : active) {
      const auto& arc = arcs[a];
      const int   end = arc.toTime - 1;
      if (!res.machineBlocks.empty()
          && !res.machineBlocks.back().isTransition()
          && !arc.isTransition()
          && res.machineBlocks.back().endState == arc.fromState)
      {
         res.machineBlocks.back().endTime = end;
      } else {
         res.machineBlocks.push_back({ft, arc.fromState, end, arc.toState});
      }
   }

   return res;
}