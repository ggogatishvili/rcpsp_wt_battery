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

#include "RCPSPSolverMILP.h"
#include <algorithm>
#include <boost/mpl/min_max.hpp>
#include <helpers.h>
#include <config.h>
#include <fmt/base.h>
#include <gurobi_c++.h>
#include <gurobi_c.h>
#include <ilcp/cp.h>
#include <memory>
#include <solution.h>

using namespace std;

RCPSPSolverMILP::RCPSPSolverMILP(const Instance * const instance)
   : ins(instance)
{
   env = std::make_unique<GRBEnv>(true);
   env->set(GRB_IntParam_OutputFlag, 0);
   try {
      env->start();
   } catch ( const GRBException& e ) {
      fmt::println(stderr, "Error: {}", e.getMessage());
      exit(1);
   }
}

Solution RCPSPSolverMILP::_solve() {
    GRBModel model{*env};

    const int H = ins->maxDuration();
    const int N = ins->nbr_tasks();

    /* =============================
     *  Decision Variables
     * ============================= */

    Map2<GRBVar> x; // 1 if task t starts in interval i
    Map2<GRBVar> y; // 1 if task t is running in interval i
    Map2<GRBVar> rs; // 1 if machine state is s in interval i
    Map3<GRBVar> rx; // 1 if machine starts transition from state s1 to s2 in interval i
    Map3<GRBVar> ry; // 1 if machine is in transition from state s1 to s2 in interval i
    Map1<GRBVar> gMach; // grid energy used for machine in interval i
    Map1<GRBVar> gBatt; // grid energy used for charging battery in interval i
    Map1<GRBVar> bMach; // battery energy used for machine in interval i
    Map1<GRBVar> bLevel; // battery level at the start of interval i
    Map1<GRBVar> eMach; // energy demand of machine at interval i
    Map1<GRBVar> tard; // tardiness for task j

    /* Variables initialization */
    Loop(t, N) Loop(i, H) {
            x.set(t, i, model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("x_{}_{}", t, i)));
            y.set(t, i, model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("y_{}_{}", t, i)));
        }

    // States and transitions
    Loop(s, 3) Loop(i, H) {
        rs.set(s, i, model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("rs_{}_{}", s, i)));
    }

    Loop(s1, 3) Loop(s2, 3) Loop(i, H) {
        rx.set(s1, s2, i, model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("rt_{}_{}_{}", s1, s2, i)));
        ry.set(s1, s2, i, model.addVar(0.0, 1.0, 0.0, GRB_BINARY, fmt::format("ry_{}_{}_{}", s1, s2, i)));
    }

    // Energy and battery
    Loop(i, H) {
        gMach.set(i, model.addVar(0.0, GRB_INFINITY, 0.0, GRB_CONTINUOUS, fmt::format("gMach_{}", i)));
        gBatt.set(i, model.addVar(0.0, GRB_INFINITY, 0.0, GRB_CONTINUOUS, fmt::format("gBatt_{}", i)));
        bMach.set(i, model.addVar(0.0, GRB_INFINITY, 0.0, GRB_CONTINUOUS, fmt::format("bMach_{}", i)));
        bLevel.set(i, model.addVar(0.0, ins->Battery.B_max, 0.0, GRB_CONTINUOUS, fmt::format("bLevel_{}", i)));
        eMach.set(i, model.addVar(0.0, GRB_INFINITY, 0.0, GRB_CONTINUOUS, fmt::format("eMach_{}", i)));
    }

    Loop(t, N) {
        tard.set(t, model.addVar(0.0, GRB_INFINITY, 0.0, GRB_CONTINUOUS, fmt::format("tard_{}", t)));
    }

    model.update();

    /* =============================
     *  Constraints
     * ============================= */

    // (1) Every task must start exactly once
    Loop(t, N) {
        GRBLinExpr expr = 0;
        Loop(i, H) expr += x.i(t, i);
        model.addConstr(expr == 1, fmt::format("TaskOnce_{}", t));
    }

    // (2) Running indicator definition y_{j,i}
    Loop(t, N) Loop(i, H) {
        GRBLinExpr expr = 0;
        LoopFrom(i2, std::max(0, i - ins->pj(t) + 1), i + 1) expr += x.i(t, i2);
        model.addConstr(y.i(t, i) == expr, fmt::format("Ydef_{}_{}", t, i));
    }

    // (3) Precedence constraints
    Loop(t1, N) iterate(t2, ins->successors(t1)) {
        GRBLinExpr start_i = 0, start_j = 0;
        Loop(i, H) {
            start_i += i * x.i(t1, i);
            start_j += i * x.i(t2, i);
        }
        model.addConstr(start_i + ins->pj(t1) <= start_j, fmt::format("preced_{}_{}", t1, t2));
    }

    // (4) Resource capacities
    Loop(q, ins->nbr_resources()) Loop(i, H) {
            GRBLinExpr expr = 0;
            Loop(t, N) LoopFrom(l, std::max(0, i - ins->pj(t) + 1), i + 1) {
                 expr += ins->rj(t, q) * x.i(t, l);
            }
            model.addConstr(expr <= ins->resource_capacities[q], fmt::format("ResCap_{}_{}", q, i));
        }

    // (5) Machine is exactly in one state or in one transition at each time interval
    Loop(i, H) {
        GRBLinExpr expr = 0;
        Loop(s, 3) expr += rs.i(s, i);
        Loop(s1, 3) Loop(s2, 3) {
            if (s1 != s2)
                expr += ry.get(s1, s2, i);
        }
        model.addConstr(expr == 1, fmt::format("OneStateOrTransition_{}", i));
    }

    // (6) Machine in processing state when executing EE task
    Loop(i, H) {
        GRBLinExpr procNeeded = 0;
        iterate(j, ins->ee_tasks) procNeeded += y.i(j, i);
        model.addConstr(procNeeded <= rs.i((int) State::Proc, i) * N, fmt::format("ProcDuringEE_{}", i));
    }

    Loop(i, H-1) Loop(s1, 3) {
        GRBLinExpr nextTrans = 0;
        Loop(s2, 3) {
            if (s1 != s2)
                nextTrans += rx.get(s1, s2, i + 1);
        }
        model.addConstr(nextTrans >= rs.i(s1, i) - rs.i(s1, i + 1), fmt::format("StateToNextStateOrTrans_{}_{}", s1, i));
    }

    // (7) Transition logic
    LoopFrom(i, 1, H - 1) { // We can skip first and last interval for transitions (as they must be OFF state)
        Loop(s1, 3) Loop(s2, 3) {
            if (s1 == s2) continue;

            int dur = 0;
            if (s1 == 0 && s2 == 1) dur = ins->offOn.time;
            else if (s1 == 1 && s2 == 0) dur = ins->onOff.time;
            else if (s1 == 1 && s2 == 2) dur = ins->onIdle.time;
            else if (s1 == 2 && s2 == 1) dur = ins->idleOn.time;

            // Invalid transition
            if (dur == 0) {
                model.addConstr(rx.get(s1, s2, i) == 0, fmt::format("InvalidTransitionX_{}_{}_{}", s1, s2, i));
                model.addConstr(ry.get(s1, s2, i) == 0, fmt::format("InvalidTransitionY_{}_{}_{}", s1, s2, i));
                continue;
            }

            // Transition cannot start if it cannot end within horizon
            if (i + dur >= H) {
                model.addConstr(rx.get(s1, s2, i) == 0, fmt::format("TransitionOutOfHorizonX_{}_{}_{}", s1, s2, i));
                model.addConstr(ry.get(s1, s2, i) == 0, fmt::format("TransitionOutOfHorizonY_{}_{}_{}", s1, s2, i));
                continue;
            }

            // The transition can only start from an appropriate state
            model.addConstr(rx.get(s1, s2, i) <= rs.i(s1, i - 1), fmt::format("TransFromState_{}_{}_{}", s1, s2, i));

            // The transition can only end in an appropriate state
            model.addConstr(rx.get(s1, s2, i) <= rs.i(s2, i + dur), fmt::format("TransToState_{}_{}_{}", s1, s2, i));

            model.addConstr(rx.get(s1, s2, i) >= rs.i(s1, i - 1) + rs.i(s2, i + dur) - 1,fmt::format("ForceStartIfStatesMatch_{}_{}_{}", s1, s2, i));

            // Transition lasts for its duration
            Loop(i2, dur) {
                model.addConstr(rx.get(s1, s2, i) <= ry.get(s1, s2, i + i2), fmt::format("TransDuration_{}_{}_{}_{}", s1, s2, i, i2));
            }
        }
    }

    // Only one transition can start at each time interval
    Loop(i, H) {
        GRBLinExpr transStart = 0;
        Loop(s1, 3) Loop(s2, 3) {
            transStart += rx.get(s1, s2, i);
        }
        model.addConstr(transStart <= 1, fmt::format("OneTransStart_{}", i));
    }

    // Two different states are not next to each other (they have transition in between)
    Loop(i, H-1) Loop(s1, 3) Loop(s2, 3) {
        if (s1 == s2) continue;
        model.addConstr(rs.i(s1, i) + rs.i(s2, i+1) <= 1, fmt::format("NoDirectStateChange_{}_{}_{}", s1, s2, i));
    }

    // Two different transition are not next to each other (they have states in between)
    Loop(i, H-1) {
        Loop(s1, 3) Loop(s2, 3) {
            if (s1 == s2) continue;
            Loop(s3, 3) Loop(s4, 3) {
                if (s3 == s4) continue;
                if (s1 == s3 && s2 == s4) continue; // skip same transition
                model.addConstr(ry.get(s1, s2, i) + ry.get(s3, s4, i + 1) <= 1, fmt::format("NoDirectTransChange_{}_{}_{}_{}_{}", s1, s2, s3, s4, i));
            }
        }
    }

    // (8) Start and end in OFF state
    model.addConstr(rs.i((int) State::Off, 0) == 1, "StartOff");
    model.addConstr(rs.i((int) State::Off, H - 1) == 1, "EndOff");

    // (9) Battery initialization
    model.addConstr(bLevel.get(0) == 0, "BatteryInit");

    // (10) Battery balance
    for (int i = 1; i < H; ++i) {
        model.addConstr(
                bLevel.get(i) == bLevel.get(i - 1)
                                 - bMach.get(i - 1)
                                 + ins->Battery.EF_charge * gBatt.get(i - 1),
                fmt::format("BatteryBalance_{}", i)
        );
    }

    // (11) Battery capacity
    Loop(i, H) {
        model.addConstr(bLevel.get(i) >= 0, fmt::format("BatteryNonNeg_{}", i));
        model.addConstr(bLevel.get(i) <= ins->Battery.B_max, fmt::format("BatteryCap_{}", i));
    }

    // (12) Machine energy requirement
    Loop(i, H) {
        GRBLinExpr stateEnergy = 0, transEnergy = 0;
        stateEnergy += rs.i((int) State::Off, i) * ins->Off.cost;
        stateEnergy += rs.i((int) State::Proc, i) * ins->On.cost;
        stateEnergy += rs.i((int) State::Idle, i) * ins->Idle.cost;

        transEnergy += ry.get((int) State::Off, (int) State::Proc, i) * ins->offOn.cost;
        transEnergy += ry.get((int) State::Proc, (int) State::Off, i) * ins->onOff.cost;
        transEnergy += ry.get((int) State::Proc, (int) State::Idle, i) * ins->onIdle.cost;
        transEnergy += ry.get((int) State::Idle, (int) State::Proc, i) * ins->idleOn.cost;

        model.addConstr(eMach.get(i) == stateEnergy + transEnergy, fmt::format("MachineEnergy_{}", i));
    }

    // (13) Energy supply balance
    Loop(i, H) {
        model.addConstr(eMach.get(i) == gMach.get(i) + ins->Battery.EF_discharge * bMach.get(i),
                        fmt::format("EnergyBalance_{}", i));
    }

    // (14) Tardiness definition
    Loop(j, N) {
        GRBLinExpr completion = 0;
        Loop(i, H) completion += (i + ins->pj(j) - 1) * x.i(j, i);
        model.addConstr(tard.get(j) >= completion - ins->tasks[j].get_due_date(), fmt::format("Tardiness_{}", j));
        model.addConstr(tard.get(j) >= 0, fmt::format("TardinessNonNeg_{}", j));
    }

    /* =============================
     *  Objective
     * ============================= */

    GRBLinExpr obj = 0.0;

    // Weighted tardiness
    Loop(j, N)obj += ins->tasks[j].get_weight() * tard.get(j);

    // Energy cost
    Loop(i, H)obj += (gMach.get(i) + gBatt.get(i)) * ins->costs[i];

    model.setObjective(obj, GRB_MINIMIZE);

    /* =============================
     *  Solver parameters
     * ============================= */

    model.set(GRB_DoubleParam_TimeLimit, (1.0) * Config::timeLimit);
    model.set(GRB_IntParam_Threads, Config::threadLimit);
    model.set(GRB_DoubleParam_SoftMemLimit, Config::memoryLimit);
    model.set(GRB_IntParam_NumericFocus, 2);
    model.set(GRB_IntParam_OutputFlag, Config::verbose);

    /* =============================
     *  Solve
     * ============================= */
    try {
        model.optimize();
    } catch (const GRBException &err) {
        fmt::println(stderr, "Gurobi error: {}", err.getMessage());
    }

    /* =============================
     *  Extract Solution
     * ============================= */

    if (model.get(GRB_IntAttr_Status) == GRB_OPTIMAL || model.get(GRB_IntAttr_Status) == GRB_SUBOPTIMAL) {
        std::vector<int> taskAssignments(N, -1);
        Loop(t, N) Loop(i, H) {
                if (x.i(t, i).get(GRB_DoubleAttr_X) > 0.999)
                    taskAssignments[t] = i;
        }

        std::vector<double> batteryLevels(H, 0.0);
        Loop(i, H)
        {
            batteryLevels[i] = bLevel.get(i).get(GRB_DoubleAttr_X);
        }

        std::vector<MachineBlock> machineBlocks;
        // States
        Loop(s, 3) {
            int start = -1;
            Loop(i, H) {
                double val = rs.i(s, i).get(GRB_DoubleAttr_X);
                if (val > 0.999 && start == -1) {
                    start = i;
                }
                if ((val <= 0.999 || i == H-1) && start != -1) {
                    int end = (val <= 0.999) ? i-1 : i;
                    auto st = static_cast<State>(s);
                    std::string stateName = std::string(state_name(st));
                    machineBlocks.push_back({start, end, stateName});
                    start = -1;
                }
            }
        }
        // Transitions
        Loop(s1, 3) Loop(s2, 3) {
            if (s1 == s2) continue; // skip same-state
            int start = -1;
            Loop(i, H) {
                double val = ry.get(s1, s2, i).get(GRB_DoubleAttr_X);
                if (val > 0.999 && start == -1) {
                    start = i;
                }
                if ((val <= 0.999 || i == H-1) && start != -1) {
                    int end = (val <= 0.999) ? i-1 : i;
                    std::string transName = fmt::format("{} -> {}", state_name((State)s1), state_name((State)s2));
                    machineBlocks.push_back({start, end, transName});
                    start = -1;
                }
            }
        }

        return Solution(ins, model.get(GRB_DoubleAttr_ObjVal), 0.0, 0.0, taskAssignments,
                        batteryLevels, machineBlocks, {model.get(GRB_DoubleAttr_MIPGap)}, Solution::NoUpdate);
    } else {
        return Solution::infeasibleSolution();
    }
}