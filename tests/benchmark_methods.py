#!/usr/bin/env python3
"""
Small head-to-head benchmark: the decomposition methods against GA, with the
compact MILP as the reference.

Every method gets the SAME time limit on the SAME instance, which is the only
comparison that means anything. Reports objective quality relative to the MILP
and wall-clock time, per method.

    python3 tests/benchmark_methods.py
    python3 tests/benchmark_methods.py --tl 120 --instances instances/1_*.txt
    python3 tests/benchmark_methods.py --methods MILP GA LBBD Benders --csv out.csv

Deliberately reuses run_solver / read_instance from test_decomposition.py, so
the benchmark and the correctness tests cannot disagree about how a run is
executed or parsed.

THREE THINGS THIS GUARDS AGAINST
--------------------------------
* **The reference may not be one.** A gap "to the MILP" is a gap to the optimum
  only where the MILP proved optimality. Instances where it did not are
  reported separately rather than averaged in, because there the sign of the
  gap carries no information -- another method beating the MILP incumbent is
  expected, not a finding.

* **Relative gaps blow up.** With negative prices in these tariffs an objective
  can approach zero, and a percentage of it is unbounded. A normalised gap is
  reported alongside: the difference divided by a positive, method-independent
  instance scale (the naive energy bill). Trust that column when the percentage
  column looks wild.

* **GA is stochastic, the rest are not.** It runs `--seeds` times. Both the mean
  and the best-of-k are reported: best-of-k is biased upward in k and is not
  comparable with a deterministic method's single run, so the mean is the
  honest column and the best is the one people usually quote.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("td", _HERE / "test_decomposition.py")
td = importlib.util.module_from_spec(_spec)
sys.modules["td"] = td            # dataclasses needs the module registered
_spec.loader.exec_module(td)

REFERENCE = "MILP"
DEFAULT_METHODS = ["MILP", "GA", "LBBD", "NoGoodCuts", "StateLBBD", "Benders"]
STOCHASTIC = {"GA", "GAP"}


def norm_scale(inst) -> float:
    """A positive, treatment-invariant scale for one instance.

    The energy bill of running the machine flat out at the mean price, with no
    optimisation at all. Same quantity experiments/analysis/analyses.py uses,
    and for the same reason: it does not depend on the method, so a normalised
    gap is comparable across instances and tariffs in a way a percentage of a
    near-zero objective is not.
    """
    ei_duration = sum(inst.tasks[i].duration for i in inst.ei_ids)
    mean_price = abs(statistics.fmean(inst.prices)) if inst.prices else 0.0
    return td.E_PROC * ei_duration * mean_price or float("nan")


def timed_run(solver: Path, method: str, instance: Path, battery: int, tl: int,
              seed: int | None, workdir: Path):
    """One solver invocation, wall-clocked from the outside.

    Wall clock rather than the solver's own reported time: model building,
    the H1 warm start and the final battery LP all sit outside what the solver
    times, and a user waiting for an answer pays for those too.
    """
    extra = ["-s", str(seed)] if seed is not None else []
    t0 = time.perf_counter()
    run = td.run_solver(solver, method, instance, battery, tl, extra=extra, workdir=workdir)
    return run, time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = _HERE.parent
    ap.add_argument("--solver", type=Path, default=root / "build" / "rcpsp_wt_battery")
    ap.add_argument("--instances", type=Path, nargs="+",
                    default=sorted((root / "instances").glob("1_[1-5].txt")))
    ap.add_argument("--methods", nargs="+", default=DEFAULT_METHODS)
    ap.add_argument("--tl", type=int, default=120, help="time limit per run (s)")
    ap.add_argument("--seeds", type=int, default=3, help="repeats for stochastic methods")
    ap.add_argument("--battery-ratio", type=float, default=1.0,
                    help="battery capacity as a multiple of daily EI demand; 0 disables storage")
    ap.add_argument("--csv", type=Path, help="write the per-run table here")
    args = ap.parse_args()

    if not args.solver.exists():
        print(f"FATAL: solver not found at {args.solver}", file=sys.stderr)
        return 2
    if REFERENCE not in args.methods:
        print(f"FATAL: {REFERENCE} is the reference and must be in --methods", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="rcpsp_bench_"))
    rows: list[dict] = []

    print(f"solver     {args.solver}")
    print(f"methods    {', '.join(args.methods)}   (reference: {REFERENCE})")
    print(f"budget     {args.tl}s per run, identical for every method")
    print(f"seeds      {args.seeds} for {'/'.join(sorted(STOCHASTIC))}, 1 otherwise\n")

    for src in args.instances:
        if not src.exists():
            print(f"  skipping {src}: not found")
            continue
        inst = td.read_instance(src)
        battery = round(args.battery_ratio * td.battery_size(inst))
        scale = norm_scale(inst)
        print(f"{src.name}: n={inst.n} EI={len(inst.ei_ids)} H={inst.horizon} b={battery}")

        for method in args.methods:
            seeds = range(1, args.seeds + 1) if method in STOCHASTIC else [None]
            for seed in seeds:
                run, wall = timed_run(args.solver, method, src, battery, args.tl,
                                      seed, workdir)
                rows.append({
                    "instance": src.name, "n": inst.n, "ei": len(inst.ei_ids),
                    "horizon": inst.horizon, "battery": battery, "scale": scale,
                    "method": method, "seed": seed if seed is not None else "",
                    "ok": int(run.ok),
                    "objective": run.objective if run.ok else float("nan"),
                    "energy": run.num("energy_cost") if run.ok else float("nan"),
                    "tardiness": run.num("tardiness_cost") if run.ok else float("nan"),
                    "wall_s": round(wall, 3),
                    "solver_s": run.num("computation_time") if run.ok else float("nan"),
                    "gap": run.num("gap") if run.ok else float("nan"),
                    "proved": int(run.ok and run.proved_optimal),
                    "subproblems": run.dnum("subproblems") if run.ok else float("nan"),
                })
                tag = f"{method}" + (f"/s{seed}" if seed else "")
                status = (f"obj={run.objective:12.3f}  {wall:7.1f}s  "
                          f"{'proved' if run.ok and run.proved_optimal else run.why_not_optimal()}"
                          if run.ok else f"FAILED rc={run.returncode}")
                print(f"    {tag:16s} {status}")
        print()

    if args.csv:
        with args.csv.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"per-run table written to {args.csv}\n")

    return summarise(rows, args)


def summarise(rows: list[dict], args) -> int:
    """Aggregate against the reference, keeping the two regimes apart."""
    by_inst: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        by_inst.setdefault(r["instance"], {}).setdefault(r["method"], []).append(r)

    solid, weak = [], []          # instances where the reference did / did not close
    for inst, per_method in by_inst.items():
        ref = per_method.get(REFERENCE, [])
        if ref and ref[0]["ok"]:
            (solid if ref[0]["proved"] else weak).append(inst)

    print("=" * 84)
    print(f"reference proved optimal on {len(solid)}/{len(by_inst)} instance(s)")
    if weak:
        print(f"  NOT proved on: {', '.join(sorted(weak))}")
        print("  -> on those, 'gap' is a gap to the MILP's incumbent, not to the optimum,")
        print("     and a method beating it there is expected rather than notable.")
    print("=" * 84)

    for label, instances in (("gap to the OPTIMUM", solid),
                             ("gap to the MILP INCUMBENT", weak)):
        if not instances:
            continue
        print(f"\n{label}  ({len(instances)} instance(s))")
        print(f"  {'method':12s} {'n':>4s} {'proved':>7s} {'gap% mean':>10s} "
              f"{'gap% med':>9s} {'gapN mean':>10s} {'time mean':>10s} "
              f"{'time med':>9s} {'vs ref':>8s} {'better':>7s}")

        ref_times = [by_inst[i][REFERENCE][0]["wall_s"] for i in instances
                     if REFERENCE in by_inst[i]]

        for method in args.methods:
            gaps_pct, gaps_norm, times, proved, better, n = [], [], [], 0, 0, 0
            for inst in instances:
                per = by_inst[inst]
                if method not in per or REFERENCE not in per:
                    continue
                ref_obj = per[REFERENCE][0]["objective"]
                runs = [r for r in per[method] if r["ok"] and math.isfinite(r["objective"])]
                if not runs or not math.isfinite(ref_obj):
                    continue
                # Mean over seeds: best-of-k is biased in k and would flatter the
                # stochastic method against the deterministic ones.
                obj = statistics.fmean(r["objective"] for r in runs)
                n += 1
                proved += sum(r["proved"] for r in runs) / len(runs)
                times.append(statistics.fmean(r["wall_s"] for r in runs))
                if abs(ref_obj) > 1e-9:
                    gaps_pct.append(100.0 * (obj - ref_obj) / abs(ref_obj))
                sc = per[method][0]["scale"]
                if math.isfinite(sc) and sc > 0:
                    gaps_norm.append((obj - ref_obj) / sc)
                if obj < ref_obj - 1e-6:
                    better += 1

            if not n:
                continue
            speed = (statistics.fmean(ref_times) / statistics.fmean(times)
                     if times and ref_times and statistics.fmean(times) > 0 else float("nan"))
            print(f"  {method:12s} {n:4d} {proved / n:7.2f} "
                  f"{(statistics.fmean(gaps_pct) if gaps_pct else float('nan')):10.3f} "
                  f"{(statistics.median(gaps_pct) if gaps_pct else float('nan')):9.3f} "
                  f"{(statistics.fmean(gaps_norm) if gaps_norm else float('nan')):10.5f} "
                  f"{statistics.fmean(times):10.1f} {statistics.median(times):9.1f} "
                  f"{speed:8.2f} {better:7d}")

        if instances is solid:
            print("\n  'better' counts instances beating a PROVEN optimum. It must be 0:")
            print("  anything else is an accounting bug, not a better schedule.")
            offenders = [m for m in args.methods if m != REFERENCE and any(
                REFERENCE in by_inst[i] and m in by_inst[i]
                and statistics.fmean(r["objective"] for r in by_inst[i][m] if r["ok"])
                < by_inst[i][REFERENCE][0]["objective"] - 1e-6
                for i in instances if by_inst[i].get(m) and by_inst[i][REFERENCE][0]["ok"])]
            if offenders:
                print(f"  *** {', '.join(offenders)} beat a proven optimum -- investigate "
                      f"before reading anything else in this table.")

    print("\n  'vs ref' is reference time / method time: >1 means faster than the MILP.")
    print("  'proved' is the share of runs that certified optimality; for LBBD, "
          "NoGoodCuts")
    print("  and StateLBBD that certifies the battery-FREE problem only "
          "(see docs/LBBD_REVIEW.md).")
    print("  'gapN' divides by the naive energy bill instead of the objective, and is "
          "the")
    print("  column to trust when prices go negative and 'gap%' misbehaves.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
