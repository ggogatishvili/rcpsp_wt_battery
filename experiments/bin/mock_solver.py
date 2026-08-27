#!/usr/bin/env python3
"""
A fake solver for testing the harness without Gurobi or a build.

It accepts the same CLI as the real binary, produces a *feasible-looking*
schedule and a JSON with the same keys, and fabricates costs that respond to
battery capacity and price shape in roughly the direction the real model would.
It is NOT a model of the problem and its numbers mean nothing.

Purpose: verify that 03_run.py, 04_collect.py and 05_analyse.py wire together
and that the integrity checks fire, before spending five days of real compute.

    python3 bin/03_run.py --limit 500 \
        --solver-override experiments/bin/mock_solver.py
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.rcpsp_io import read_extended, earliest_starts   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", dest="inp", required=True)
    ap.add_argument("-o", dest="out")
    ap.add_argument("-m", dest="method", default="H1")
    ap.add_argument("-b", dest="battery", type=int, default=0)
    ap.add_argument("-s", dest="seed", type=int, default=0)
    ap.add_argument("--tl", type=int, default=60)
    ap.add_argument("--thl", type=int, default=1)
    ap.add_argument("--ml", type=int, default=5)
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--phase1-price-aware", action="store_true")
    ap.add_argument("--phase3-lp", action="store_true")
    # Decomposition flags. They must be *advertised* by --help too, because
    # 02_make_runlist.py probes --help to decide whether the Benders cells are
    # buildable; without them the mock run would silently drop E8/E9 and the
    # dry run would prove nothing about the part that is newest.
    ap.add_argument("--sub-tl", type=float, default=60.0)
    ap.add_argument("--refine-tl", type=float, default=10.0)
    ap.add_argument("--no-warmstart", action="store_true")
    ap.add_argument("--lbbd-tardiness-bounds", type=int, default=3)
    ap.add_argument("--no-benders-node-cuts", action="store_true")
    ap.add_argument("--battery-free-end", action="store_true")
    # Campaign-v2 flags. They must be ADVERTISED in --help, because
    # 02_make_runlist.py probes --help to decide whether a machine-profile or a
    # state-ladder cell is buildable. Without them the dry run silently drops
    # M1 and M4 -- the two newest and least-tested parts of the pipeline -- and
    # proves nothing about them.
    ap.add_argument("--states", default="all")
    ap.add_argument("--lambda", dest="lam", type=float, default=1.0)
    ap.add_argument("--e-proc", type=float, default=4.0)
    ap.add_argument("--e-idle", type=float, default=2.0)
    ap.add_argument("--e-off", type=float, default=0.0)
    ap.add_argument("--off-proc-time", type=int, default=2)
    ap.add_argument("--off-proc-cost", type=float, default=5.0)
    ap.add_argument("--proc-off-time", type=int, default=1)
    ap.add_argument("--proc-off-cost", type=float, default=1.0)
    ap.add_argument("--proc-idle-time", type=int, default=1)
    ap.add_argument("--proc-idle-cost", type=float, default=2.0)
    ap.add_argument("--idle-proc-time", type=int, default=1)
    ap.add_argument("--idle-proc-cost", type=float, default=2.5)
    ap.add_argument("--machine-profile")
    ap.add_argument("--charging-efficiency", type=float, default=0.95)
    ap.add_argument("--discharging-efficiency", type=float, default=0.95)
    ap.add_argument("--c-rate", type=float, default=float("inf"))
    a, _ = ap.parse_known_args()
    if a.version:
        print("mock solver 0.0.0")
        return 0

    # --- domain checks, mirroring the real solver ---------------------------
    # A mock that accepts arguments the real binary refuses is worse than no
    # mock: it certifies a runlist that will fail on the cluster. This exact
    # gap cost 918 runs — the mock's `max(1, off_proc_time)` below silently
    # rescued a zero duration that the C++ Instance constructor rejects. If you
    # add a flag to the real solver's validation, add it here the same day.
    for flag, val in (("--off-proc-time", a.off_proc_time),
                      ("--proc-off-time", a.proc_off_time),
                      ("--proc-idle-time", a.proc_idle_time),
                      ("--idle-proc-time", a.idle_proc_time)):
        if val < 1:
            print("Machine profile transition durations must be >= 1 "
                  "(0 is reserved to mean 'no such transition'). "
                  f"{flag}={val}", file=sys.stderr)
            return 2
    if a.e_proc <= 0 or not 0 <= a.e_idle <= a.e_proc:
        print(f"Machine profile invalid: need 0 <= e_idle <= e_proc, "
              f"got e_idle={a.e_idle}, e_proc={a.e_proc}", file=sys.stderr)
        return 2

    inst = read_extended(Path(a.inp))
    rng = random.Random(a.seed * 7919 + len(inst.tasks))
    es = earliest_starts(inst)
    starts = [min(inst.horizon - t.duration, es[i]) for i, t in enumerate(inst.tasks)]

    prices = inst.prices
    mean_p = sum(prices) / len(prices)
    spread = (max(prices) - min(prices)) / mean_p if mean_p else 0.0
    ei_dur = sum(inst.tasks[i].duration for i in inst.ei_ids)

    # energy falls with battery size and with how much price spread there is
    e_base = 4.0 * ei_dur * mean_p / 1000.0
    relief = min(0.35, 0.25 * spread) * (1 - 1 / (1 + a.battery / 20.0))
    method_bonus = {"H1": 0.0, "H1P": 0.02, "GA": 0.03, "GAP": 0.05,
                    "MILP": 0.07,
                    # Fabricated ordering, and deliberately so: the mock exists
                    # to prove the plumbing carries a difference between these
                    # arms, not to predict one. Do not read anything into it.
                    "LBBD": 0.055, "StateLBBD": 0.05,
                    "Benders": 0.06}.get(a.method, 0.0)
    # --- fabricated response to the campaign-v2 factors ---------------------
    # Again: made up, and only monotone in the direction the real model would
    # plausibly move. The point is that a downstream analysis which cannot see
    # a planted effect here has a wiring bug, not a scientific finding.
    #
    #  * a cheap idle state (low rho) and a cheap restart both give the
    #    scheduler a way to avoid expensive hours WITHOUT storage, so they eat
    #    into what storage can add -- the substitution the paper is about;
    #  * restricting the state set (--states) removes that alternative and
    #    hands the value back to storage.
    rho = a.e_idle / a.e_proc if a.e_proc else 0.5
    restart_penalty = a.off_proc_cost * max(1, a.off_proc_time)
    # State flexibility in [0, 1]: 1 = free to switch off and idle cheaply.
    flex = (1.0 - rho) * (1.0 / (1.0 + restart_penalty / 10.0))
    if a.states == "proc":
        flex *= 0.15          # sigma1: hot for the whole window
    elif a.states.replace(" ", "") == "proc,idle":
        flex *= 0.55          # sigma2: idles, never shuts down
    # State management has TWO distinct effects, and collapsing them into one
    # spread-proportional term (as an earlier version of this mock did) makes
    # the flat tariff look like a placebo for machine states, which it is not.
    #
    #   consumption: idling draws less than processing and Off draws nothing, so
    #     a flexible machine consumes less energy FULL STOP -- at a constant
    #     price just as much as at a volatile one;
    #   arbitrage: it can also shift what it does consume into cheap hours,
    #     which only pays when prices move.
    #
    # Keeping them separate is what lets the pilot exercise M4's positive
    # placebo check (V_sigma must be non-zero under a flat tariff). With the
    # collapsed version the check could never fail and could never pass.
    consumption_relief = 0.12 * flex
    arbitrage_relief = 0.08 * flex * min(1.0, spread)
    state_relief = consumption_relief + arbitrage_relief
    # Storage and state flexibility are partial substitutes: the more the
    # machine can already dodge, the less the battery adds.
    relief *= (1.0 - 0.45 * flex)
    eta = (a.charging_efficiency * a.discharging_efficiency) ** 0.5
    relief *= eta
    if a.c_rate != float("inf"):
        relief *= min(1.0, 0.6 + 0.4 * a.c_rate)

    energy = (e_base * (1 - relief - state_relief - method_bonus)
              * rng.uniform(0.99, 1.01))

    tard = sum(t.weight for t in inst.tasks) * rng.uniform(0.0, 0.4) \
        * (1 + 0.5 * relief)          # shifting production costs service

    levels = [0.0] * inst.horizon
    if a.battery > 0:
        lo = sorted(range(inst.horizon), key=lambda i: prices[i])[:inst.horizon // 6]
        lvl = 0.0
        for t in range(inst.horizon):
            lvl = min(a.battery, lvl + a.battery * 0.3) if t in lo else max(0.0, lvl - a.battery * 0.2)
            levels[t] = round(lvl, 6)

    out = dict(
        solution_info=dict(objective_value=round(energy + tard, 5),
                           energy_cost=round(energy, 5),
                           tardiness_cost=round(tard, 5),
                           computation_time=round(rng.uniform(0.01, 0.4), 4),
                           gap=0.0),
        battery_levels=levels,
        task_assignments=[dict(task_id=i, start_time=starts[i],
                               duration=t.duration,
                               end_time=starts[i] + t.duration - 1,
                               successors=t.successors, release_date=t.release,
                               due_date=t.due, weight=t.weight,
                               resource_requests=t.resources)
                          for i, t in enumerate(inst.tasks)],
        machine_blocks=[],
        instance_summary=dict(task_count=inst.n, resource_count=inst.m,
                              resource_capacities=inst.capacities,
                              energy_costs=inst.prices),
        config=dict(method=a.method, time_limit=a.tl, alpha=0.5,
                    battery_capacity=a.battery),
    )
    # Decomposition methods export diagnostics; the analysis for E8/E9 reads
    # them, so the mock has to emit the same shape or the dry run cannot
    # exercise those code paths at all.
    if a.method in ("LBBD", "StateLBBD", "Benders"):
        battery_aware = a.method == "Benders"
        no_batt = energy * (1.0 + 0.6 * relief)      # storage-free price of the same schedule
        subproblems = rng.randint(5, 200)
        out["diagnostics"] = dict(
            subproblems=subproblems,
            feasibility_cuts=rng.randint(0, subproblems),
            optimality_cuts=rng.randint(0, subproblems),
            battery_cuts=(rng.randint(10, 500) if battery_aware else 0),
            battery_node_cuts=(rng.randint(0, 300) if battery_aware else 0),
            cumul_mifs=rng.randint(0, subproblems) * 2,
            inconclusive=0,
            energy_cost_no_battery=round(no_batt, 5),
            battery_saving=round(no_batt - energy, 5),
            battery_lp_ok=1.0,
            # A bound below the objective, and flagged battery-aware only for
            # the arm whose theta really is one.
            bound=round((energy + tard) * rng.uniform(0.90, 0.999), 5),
            bound_is_battery_aware=1.0 if battery_aware else 0.0,
        )

    print(f"{out['solution_info']['objective_value']:.5f} 0.010")
    if a.out:
        Path(a.out).write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
