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

#include <vector>

#include <gurobi_c++.h>

#include "instance.h"
#include "solution.h"

namespace lbbd
{

/**
 * @brief The machine's state schedule, as explicit per-interval variables.
 *
 * This is the SolverMILP machine block (rs / rx / ry and the transition logic
 * between them) lifted into something a second master can reuse.
 *
 * ### Why SolverBenders cannot use the SPACES z arcs instead
 *
 * `SolverLBBD`'s master collapses each gap between two EI tasks into a single
 * binary carrying the cost of the *cheapest* state path through it. That is a
 * strong reformulation, but it destroys two things the battery Benders cut
 * needs:
 *
 *  - the per-interval energy demand `e_i`, which must be an affine function of
 *    the master variables for a Benders cut to be writable at all;
 *  - the freedom to pick a different path, because the collapsed path was
 *    optimal against the *raw* tariff, and under the battery's shadow tariff a
 *    different one can be cheaper.
 *
 * So the Benders master pays for explicit states. It is a smaller model
 * (O(h) variables instead of O(h^2)) but a weaker one, and separating that
 * cost from the benefit of battery coordination is exactly what the
 * `StateLBBD` control arm is for. See docs/BENDERS_BATTERY.md §2.
 *
 * ### What this class does and does not own
 *
 * It owns the state variables, the constraints that make them a valid machine
 * timeline, the per-interval energy expression, and the reconstruction of
 * MachineBlocks from a solved model. It does *not* own the EI tasks: the
 * caller supplies, for each interval, the expression "an EI task is running
 * here", and this class forces Proc over it.
 */
class MachineStateModel
{
   public:
      /// Adds every variable and constraint to `model`. Call model.update()
      /// afterwards before reading expressions.
      MachineStateModel(const Instance* ins, GRBModel& model);

      /// Forces Proc whenever `runningExpr[i]` is 1. The caller builds those
      /// expressions from its own task variables.
      void requireProcWhile(GRBModel& model, const std::vector<GRBLinExpr>& runningExpr);

      /// Energy demand of interval i, affine in the state variables. This is
      /// the quantity the battery LP consumes and the Benders cut multiplies
      /// by the shadow tariff.
      const GRBLinExpr& energyExpr(const int i) const { return energy[i]; }

      /// Total energy cost of the machine at the raw tariff, i.e. what the
      /// objective would be with no storage at all. Used by the StateLBBD
      /// control arm, which has no theta.
      GRBLinExpr rawEnergyCost() const;

      /// A valid lower bound on the battery-adjusted energy cost, for theta's
      /// initial bound. Crude -- it assumes every interval simultaneously
      /// draws the largest possible demand at the most negative possible price
      /// -- but finite even with negative prices and an uncapped C-rate, which
      /// a naive bound is not.
      double energyCostLowerBound() const;

      /// Demand profile of a solved (or partially solved) model. `value` reads
      /// one variable, so the same code serves both the final extraction
      /// (GRB_DoubleAttr_X) and the callback (getSolution / getNodeRel).
      template <typename ValueFn>
      std::vector<double> demandProfile(ValueFn value) const
      {
         std::vector<double> demand(h, 0.0);
         for (int i = 0; i < h; ++i) {
            double e = 0.0;
            for (int s = 0; s < 3; ++s)
               e += stateEnergy[s] * value(rs[idx2(s, i)]);
            for (int s1 = 0; s1 < 3; ++s1) for (int s2 = 0; s2 < 3; ++s2)
               if ( s1 != s2 && transition[s1][s2].time > 0 )
                  e += transition[s1][s2].cost * value(ry[idx3(s1, s2, i)]);
            demand[i] = e;
         }
         return demand;
      }

      /// MachineBlocks of a solved model, ready for the Solution object and
      /// for the battery post-processing.
      std::vector<MachineBlock> blocks() const;

   private:
      struct Transition { int time = 0; double cost = 0.0; };

      const Instance* ins;
      const int h;

      double stateEnergy[3];
      Transition transition[3][3];

      std::vector<GRBVar> rs;   // rs[idx2(s, i)]      : machine is in state s during interval i
      std::vector<GRBVar> rx;   // rx[idx3(s1, s2, i)] : transition s1->s2 starts at interval i
      std::vector<GRBVar> ry;   // ry[idx3(s1, s2, i)] : transition s1->s2 covers interval i
      std::vector<GRBLinExpr> energy;

      int idx2(const int s, const int i) const { return s * h + i; }
      int idx3(const int s1, const int s2, const int i) const { return (s1 * 3 + s2) * h + i; }

      void addVariables(GRBModel& model);
      void addTimelineConstraints(GRBModel& model);
      void buildEnergyExpressions();
};

}
