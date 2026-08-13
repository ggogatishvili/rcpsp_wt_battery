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
    a, _ = ap.parse_known_args()
    if a.version:
        print("mock solver 0.0.0")
        return 0

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
                    "NoGoodCuts": 0.04, "LBBD": 0.055, "StateLBBD": 0.05,
                    "Benders": 0.06}.get(a.method, 0.0)
    energy = e_base * (1 - relief - method_bonus) * rng.uniform(0.99, 1.01)

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
    if a.method in ("LBBD", "NoGoodCuts", "StateLBBD", "Benders"):
        battery_aware = a.method == "Benders"
        no_batt = energy * (1.0 + 0.6 * relief)      # storage-free price of the same schedule
        subproblems = rng.randint(5, 200)
        mis = (1 if a.method == "NoGoodCuts" else 0)
        out["diagnostics"] = dict(
            subproblems=subproblems,
            feasibility_cuts=rng.randint(0, subproblems),
            optimality_cuts=rng.randint(0, subproblems),
            battery_cuts=(rng.randint(10, 500) if battery_aware else 0),
            battery_node_cuts=(rng.randint(0, 300) if battery_aware else 0),
            cumul_mifs=rng.randint(0, subproblems) * (len(inst.ei_ids) if mis else 2),
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
