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

#include <memory>
#include <vector>

#include <gurobi_c++.h>

#include "BatteryLp.h"
#include "config.h"
#include "instance.h"
#include "lbbd/CutStatistics.h"
#include "lbbd/EiPlacement.h"
#include "lbbd/MachineStateModel.h"
#include "lbbd/PrecedenceClosure.h"
#include "lbbd/RcpspSubproblem.h"
#include "solution.h"

/**
 * @brief Branch-and-check with **two** subproblems: the RCPSP, and the battery.
 *
 * Where SolverLBBD prices energy against the raw tariff and only consults the
 * battery once everything is decided, this solver folds the battery into the
 * search. The master carries a variable `theta` for the battery-adjusted energy
 * cost, and every time it produces a machine schedule the battery LP is solved
 * and its duals become a classical Benders optimality cut on `theta`.
 *
 * ### Why the battery half is the easy half
 *
 * The machine's demand profile enters the battery LP only through the
 * right-hand side of the demand-balance rows. So the optimal cost `Phi(e)` is a
 * convex piecewise-linear function of that profile, the demand-row duals
 * `alpha` are a subgradient of it, and
 *
 *     theta >= Phi(e_bar) + sum_i alpha_i (e_i - e_bar_i)
 *
 * is a valid global underestimator. Three things follow, and all three are
 * unusually favourable:
 *
 *  - the LP is **always feasible** (buy everything from the grid), so there are
 *    no battery feasibility cuts to generate at all;
 *  - `alpha_i <= price_i` always, so `theta` is a genuine *lower* bound on the
 *    battery-aware energy cost — which is exactly what SolverLBBD cannot
 *    provide, and the reason this solver can certify a gap on the real problem;
 *  - because `Phi` is convex, the cut is valid at **fractional** machine-state
 *    solutions too, so it can be separated at tree nodes and not only at
 *    incumbents. The RCPSP subproblem admits no such thing.
 *
 * ### The price: explicit machine states
 *
 * A Benders cut needs `e_i` affine in the master variables, and SolverLBBD's
 * SPACES `z` arcs collapse a whole state path into one pre-priced binary — and
 * priced against the *raw* tariff at that, which under the battery's shadow
 * tariff is the wrong path. So this master uses lbbd::MachineStateModel
 * instead: `O(h)` variables rather than `O(h^2)`, but weaker, because it has to
 * rediscover by branching what SPACES pre-computed. See
 * docs/BENDERS_BATTERY.md §2.
 *
 * ### The control arm
 *
 * That trade-off is why `ResolutionMethod::StateLBBD` exists. It is this exact
 * master with the battery cuts switched off and storage post-processed as in
 * SolverLBBD. Comparing Benders against SolverLBBD alone would move two things
 * at once — battery coordination *and* the loss of SPACES — and no amount of
 * data would separate them afterwards. StateLBBD holds the master fixed so the
 * battery coordination can be measured on its own.
 */
class SolverBenders
{
   public:
      /// @param method ResolutionMethod::Benders or ResolutionMethod::StateLBBD.
      SolverBenders(const Instance* instance, const Config::ResolutionMethod method);

      Solution solve() { return _solve(); }
      Solution operator()() { return solve(); }

   private:
      class Callback : public GRBCallback
      {
         public:
            Callback(const Instance* ins,
                     const lbbd::PrecedenceClosure* closure,
                     const lbbd::RcpspSubproblem* subproblem,
                     const lbbd::EiPlacement* placement,
                     const lbbd::MachineStateModel* machine,
                     GRBVar* theta,
                     GRBVar* q,
                     const bool batteryCuts)
               : ins(ins), closure(closure), subproblem(subproblem)
               , placement(placement), machine(machine)
               , theta(theta), q(q), batteryCuts(batteryCuts)
            { }

            const lbbd::CutStatistics& statistics() const & { return stats; }

         protected:
            void callback() override;

         private:
            const Instance* ins;
            const lbbd::PrecedenceClosure* closure;
            const lbbd::RcpspSubproblem* subproblem;
            const lbbd::EiPlacement* placement;
            const lbbd::MachineStateModel* machine;
            GRBVar* theta;
            GRBVar* q;
            bool batteryCuts;
            lbbd::CutStatistics stats;

            /// Battery LP instance private to the calling thread. Gurobi runs
            /// the callback on several threads and a GRBModel is not shareable
            /// between them, so each gets its own — cheap, because the LP is
            /// built once per thread and thereafter only its right-hand side
            /// changes.
            BatteryLp& threadBatteryLp();

            void addRcpspCuts();
            /// @param atNode true when separating on a fractional relaxation.
            void addBatteryCut(const bool atNode);
      };

      const Instance* ins;
      const Config::ResolutionMethod method;
      const int H;

      /// True for ResolutionMethod::Benders, false for the StateLBBD control.
      bool usesBatteryCuts() const { return method == Config::ResolutionMethod::Benders; }

      Solution _solve();
};
