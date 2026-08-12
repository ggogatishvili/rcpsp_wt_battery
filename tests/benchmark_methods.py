#!/usr/bin/env python3
"""
Head-to-head benchmark across instance sizes: the decomposition methods against
GA, with the compact MILP as the reference.

Every method gets the SAME time limit on the SAME instance, and every run is
pinned to its own core so wall-clock numbers measure the method rather than
contention.

    python3 tests/benchmark_methods.py                       # sizes 1,2,4,8
    python3 tests/benchmark_methods.py --sizes 1 2 4 8 16 --per-size 3 --tl 900
    python3 tests/benchmark_methods.py --workers 32 --csv bench.csv
    python3 tests/benchmark_methods.py --dry-run             # plan + cost, no runs

Instances are `instances/<class>_<replicate>.txt`, where a class-p instance has
32*p tasks. Sizes 1,2,4,8 are 32..256 tasks; add 16 for 512.

Reuses run_solver / read_instance from test_decomposition.py, so the benchmark
and the correctness tests cannot disagree about how a run is executed or parsed.

WHAT THIS GUARDS AGAINST
------------------------
* **The reference stops being one as instances grow.** That is the whole point
  of going bigger, and it has to be visible rather than averaged away. Results
  are split three ways per size class: instances where the MILP PROVED
  optimality (gap to the true optimum), where it only found an incumbent (gap
  to that incumbent -- being beaten there is expected), and where it found
  nothing at all (gap to the best incumbent any method produced).

* **Relative gaps blow up.** With negative prices an objective can approach
  zero and a percentage of it is unbounded. A normalised gap -- the difference
  over a positive, method-independent instance scale -- is reported alongside.

* **Parallelism corrupts timing.** Runs are pinned one-per-core via taskset and
  the script refuses to pretend otherwise if the core budget is oversubscribed.

* **GA is stochastic, the rest are not.** It repeats over `--seeds` and is
  reported by the MEAN; best-of-k is biased upward in k and is not comparable
  with a deterministic method's single run.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import os
import queue
import shutil
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("td", _HERE / "test_decomposition.py")
if _spec is None or _spec.loader is None:
    raise ImportError("tests/test_decomposition.py not found or not loadable")
td = importlib.util.module_from_spec(_spec)
sys.modules["td"] = td            # dataclasses needs the module registered
_spec.loader.exec_module(td)

REFERENCE = "MILP"
DEFAULT_METHODS = ["MILP", "GA", "LBBD", "NoGoodCuts", "StateLBBD", "Benders"]
STOCHASTIC = {"GA", "GAP"}
TASKS_PER_CLASS = 32

# Fraction of its budget each method actually consumes, for the up-front
# estimate only. The decomposition methods and the MILP either prove optimality
# early or run out the clock; on anything above the smallest class the second
# dominates. GA always uses its full budget by construction.
BUDGET_USE = {"MILP": 0.95, "GA": 1.0, "LBBD": 0.85, "NoGoodCuts": 0.95,
              "StateLBBD": 0.9, "Benders": 0.9}


def norm_scale(inst) -> float:
    """A positive, treatment-invariant scale for one instance.

    The energy bill of running the machine flat out at the mean price, with no
    optimisation at all. Same quantity experiments/analysis/analyses.py uses,
    and for the same reason: it does not depend on the method, so a normalised
    gap stays comparable across instances and tariffs where a percentage of a
    near-zero objective does not.
    """
    ei_duration = sum(inst.tasks[i].duration for i in inst.ei_ids)
    mean_price = abs(statistics.fmean(inst.prices)) if inst.prices else 0.0
    return td.E_PROC * ei_duration * mean_price or float("nan")


def pick_instances(root: Path, sizes: list[int], per_size: int) -> list[Path]:
    """`per_size` replicates of each requested size class, lowest first."""
    out: list[Path] = []
    for p in sizes:
        found = sorted((root / "instances").glob(f"{p}_*.txt"),
                       key=lambda q: int(q.stem.split("_")[1]))
        if not found:
            print(f"  warning: no instance found for size class {p}", file=sys.stderr)
        out.extend(found[:per_size])
    return out


# ==========================================================================
# execution
# ==========================================================================

class CorePool:
    """Hands each concurrent run its own core, and takes it back afterwards."""

    def __init__(self, n: int) -> None:
        self.available = shutil.which("taskset") is not None
        self.q: queue.Queue[int] = queue.Queue()
        for c in range(n):
            self.q.put(c)

    def acquire(self) -> int | None:
        return self.q.get() if self.available else None

    def release(self, core: int | None) -> None:
        if core is not None:
            self.q.put(core)


def run_job(job: dict, args, pool: CorePool, lock: threading.Lock,
            workdir: Path, progress: dict) -> dict:
    core = pool.acquire()
    try:
        t0 = time.perf_counter()
        run = td.run_solver(
            args.solver, job["method"], job["path"], job["battery"], args.tl,
            extra=(["-s", str(job["seed"])] if job["seed"] else []),
            workdir=workdir, threads=1, mem_gb=args.mem, cpu=core,
            tag=f"s{job['seed']}" if job["seed"] else "")
        wall = time.perf_counter() - t0
    finally:
        pool.release(core)

    row = {
        "instance": job["path"].name, "size_class": job["size_class"],
        "n": job["n"], "ei": job["ei"], "horizon": job["horizon"],
        "battery": job["battery"], "scale": job["scale"],
        "method": job["method"], "seed": job["seed"] or "",
        "ok": int(run.ok),
        "objective": run.objective if run.ok else float("nan"),
        "energy": run.num("energy_cost") if run.ok else float("nan"),
        "tardiness": run.num("tardiness_cost") if run.ok else float("nan"),
        "wall_s": round(wall, 3),
        "solver_s": run.num("computation_time") if run.ok else float("nan"),
        "gap": run.num("gap") if run.ok else float("nan"),
        "proved": int(run.ok and run.proved_optimal),
        "subproblems": run.dnum("subproblems") if run.ok else float("nan"),
        "inconclusive": run.dnum("inconclusive") if run.ok else float("nan"),
    }

    with lock:
        progress["done"] += 1
        obj = row["objective"]
        state = ("FAILED" if not run.ok else
                 "proved" if row["proved"] else run.why_not_optimal())
        objs = f"{obj:13.3f}" if math.isfinite(obj) else "          n/a"
        print(f"  [{progress['done']:3d}/{progress['total']}] "
              f"{job['path'].name:10s} n={job['n']:4d} "
              f"{job['method'] + ('/s' + str(job['seed']) if job['seed'] else ''):14s} "
              f"obj={objs} {wall:7.1f}s  {state}", flush=True)
    return row


# ==========================================================================
# reporting
# ==========================================================================

def reference_for(per_method: dict) -> tuple[float, str]:
    """The value everything on this instance is measured against.

    Three regimes, and conflating them is the easiest way to publish a wrong
    conclusion:
      proven    the MILP closed  -> this is the optimum
      incumbent the MILP ran out -> being beaten here is expected
      best      the MILP found nothing at all -> fall back to the best any
                method produced, which is all the instance can support
    """
    ref = per_method.get(REFERENCE, [])
    if ref and ref[0]["ok"] and math.isfinite(ref[0]["objective"]):
        return ref[0]["objective"], "proven" if ref[0]["proved"] else "incumbent"
    finite = [r["objective"] for runs in per_method.values() for r in runs
              if r["ok"] and math.isfinite(r["objective"])]
    return (min(finite), "best") if finite else (float("nan"), "none")


def summarise(rows: list[dict], args) -> int:
    by_inst: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        by_inst.setdefault(r["instance"], {}).setdefault(r["method"], []).append(r)

    regime, ref_value, size_of = {}, {}, {}
    for inst, per_method in by_inst.items():
        ref_value[inst], regime[inst] = reference_for(per_method)
        size_of[inst] = next(iter(per_method.values()))[0]["size_class"]

    print("\n" + "=" * 96)
    print("reference status by size class  (n = 32 x class)")
    print("=" * 96)
    classes = sorted({size_of[i] for i in by_inst})
    print(f"  {'class':>6s} {'tasks':>6s} {'instances':>10s} {'MILP proved':>12s} "
          f"{'MILP incumbent':>15s} {'MILP nothing':>13s}")
    for c in classes:
        members = [i for i in by_inst if size_of[i] == c]
        counts = {k: sum(1 for i in members if regime[i] == k)
                  for k in ("proven", "incumbent", "best", "none")}
        print(f"  {c:6d} {c * TASKS_PER_CLASS:6d} {len(members):10d} "
              f"{counts['proven']:12d} {counts['incumbent']:15d} "
              f"{counts['best'] + counts['none']:13d}")
    print("\n  Where 'MILP proved' drops to zero the reference is no longer an "
          "optimum, and the")
    print("  tables below switch to comparing incumbents. That transition is "
          "the result, not")
    print("  a defect: it is where the compact model stops being usable.")

    for kind, title in (("proven",    "GAP TO THE OPTIMUM (MILP proved)"),
                        ("incumbent", "GAP TO THE MILP INCUMBENT (MILP ran out of time)"),
                        ("best",      "GAP TO THE BEST KNOWN (MILP found nothing)")):
        chosen = [i for i in by_inst if regime[i] == kind]
        if not chosen:
            continue
        print("\n" + "=" * 96)
        print(f"{title}  --  {len(chosen)} instance(s)")
        print("=" * 96)
        print(f"  {'class':>5s} {'method':12s} {'n':>3s} {'proved':>7s} "
              f"{'gap% mean':>10s} {'gap% med':>9s} {'gapN mean':>10s} "
              f"{'time mean':>10s} {'vs ref':>8s} {'better':>7s}")

        for c in sorted({size_of[i] for i in chosen}):
            members = [i for i in chosen if size_of[i] == c]
            ref_times = [by_inst[i][REFERENCE][0]["wall_s"] for i in members
                         if REFERENCE in by_inst[i]]
            for method in args.methods:
                gp, gn, times, proved, better, n = [], [], [], 0.0, 0, 0
                for inst in members:
                    runs = [r for r in by_inst[inst].get(method, [])
                            if r["ok"] and math.isfinite(r["objective"])]
                    if not runs or not math.isfinite(ref_value[inst]):
                        continue
                    # Mean over seeds: best-of-k is biased in k and would
                    # flatter the stochastic method against deterministic ones.
                    obj = statistics.fmean(r["objective"] for r in runs)
                    n += 1
                    proved += statistics.fmean(r["proved"] for r in runs)
                    times.append(statistics.fmean(r["wall_s"] for r in runs))
                    ref = ref_value[inst]
                    if abs(ref) > 1e-9:
                        gp.append(100.0 * (obj - ref) / abs(ref))
                    sc = runs[0]["scale"]
                    if math.isfinite(sc) and sc > 0:
                        gn.append((obj - ref) / sc)
                    if obj < ref - 1e-6:
                        better += 1
                if not n:
                    continue
                speed = (statistics.fmean(ref_times) / statistics.fmean(times)
                         if ref_times and times and statistics.fmean(times) > 0
                         else float("nan"))
                print(f"  {c:5d} {method:12s} {n:3d} {proved / n:7.2f} "
                      f"{(statistics.fmean(gp) if gp else float('nan')):10.3f} "
                      f"{(statistics.median(gp) if gp else float('nan')):9.3f} "
                      f"{(statistics.fmean(gn) if gn else float('nan')):10.5f} "
                      f"{statistics.fmean(times):10.1f} {speed:8.2f} {better:7d}")

        if kind == "proven":
            offenders = sorted({
                m for i in chosen for m in args.methods
                if m != REFERENCE and by_inst[i].get(m)
                and math.isfinite(ref_value[i])
                and statistics.fmean(r["objective"] for r in by_inst[i][m]
                                     if r["ok"] and math.isfinite(r["objective"]))
                < ref_value[i] - 1e-6})
            print("\n  'better' must be 0 here: beating a PROVEN optimum is an "
                  "accounting bug,")
            print("  not a better schedule -- the same failure mode as the "
                  "terminal-battery issue.")
            if offenders:
                print(f"  *** {', '.join(offenders)} beat a proven optimum. Stop and "
                      f"investigate before reading anything else.")

    print("\n  'vs ref'  reference wall time / method wall time; >1 means faster "
          "than the MILP.")
    print("  'proved'  share of runs that certified optimality. For LBBD, "
          "NoGoodCuts and")
    print("            StateLBBD that certifies the battery-FREE problem only "
          "(docs/LBBD_REVIEW.md);")
    print("            at --battery-ratio 0 the two coincide and the column "
          "means what it says.")
    print("  'gapN'    normalised by the naive energy bill instead of the "
          "objective. Trust it")
    print("            over 'gap%' wherever prices go negative.")
    return 0


# ==========================================================================
# driver
# ==========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = _HERE.parent
    cpus = os.cpu_count() or 4
    ap.add_argument("--solver", type=Path, default=root / "build" / "rcpsp_wt_battery")
    ap.add_argument("--instances", type=Path, nargs="+", default=None,
                    help="explicit instance files; overrides --sizes/--per-size")
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 4, 8],
                    help="size classes; class p has 32*p tasks (default 1 2 4 8)")
    ap.add_argument("--per-size", type=int, default=2,
                    help="replicates per size class (default 2)")
    ap.add_argument("--methods", nargs="+", default=DEFAULT_METHODS)
    ap.add_argument("--tl", type=int, default=600, help="time limit per run (s)")
    ap.add_argument("--seeds", type=int, default=3, help="repeats for stochastic methods")
    ap.add_argument("--battery-ratio", type=float, default=1.0,
                    help="battery capacity as a multiple of daily EI demand; 0 disables storage")
    ap.add_argument("--workers", type=int, default=max(1, cpus - 1),
                    help=f"parallel runs, one core each (default {max(1, cpus - 1)})")
    ap.add_argument("--mem", type=int, default=8, help="memory cap per run, GB")
    ap.add_argument("--csv", type=Path, help="write the per-run table here")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    args = ap.parse_args()

    if not args.dry_run and not args.solver.exists():
        print(f"FATAL: solver not found at {args.solver}", file=sys.stderr)
        return 2
    if REFERENCE not in args.methods:
        print(f"FATAL: {REFERENCE} is the reference and must be in --methods", file=sys.stderr)
        return 2

    instances = args.instances or pick_instances(root, args.sizes, args.per_size)
    instances = [p for p in instances if p.exists()]
    if not instances:
        print("FATAL: no instances selected", file=sys.stderr)
        return 2

    # ---- build the job list ------------------------------------------
    jobs: list[dict] = []
    for src in instances:
        inst = td.read_instance(src)
        base = {
            "path": src, "size_class": max(1, round(inst.n / TASKS_PER_CLASS)),
            "n": inst.n, "ei": len(inst.ei_ids), "horizon": inst.horizon,
            "battery": round(args.battery_ratio * td.battery_size(inst)),
            "scale": norm_scale(inst),
        }
        for method in args.methods:
            for seed in (range(1, args.seeds + 1) if method in STOCHASTIC else [None]):
                jobs.append({**base, "method": method, "seed": seed})

    # Longest first: the class-8 MILP runs dominate the makespan, and starting
    # them last leaves most cores idle at the end.
    jobs.sort(key=lambda j: (-j["n"], j["method"]))

    core_h = sum(args.tl * BUDGET_USE.get(j["method"], 1.0) for j in jobs) / 3600.0
    workers = max(1, min(args.workers, cpus))
    pinning = shutil.which("taskset") is not None

    print(f"solver     {args.solver}")
    print(f"instances  {len(instances)}  (classes {sorted({j['size_class'] for j in jobs})}, "
          f"{min(j['n'] for j in jobs)}..{max(j['n'] for j in jobs)} tasks)")
    print(f"methods    {', '.join(args.methods)}   (reference: {REFERENCE})")
    print(f"budget     {args.tl}s per run, identical for every method")
    print(f"runs       {len(jobs)}   ~{core_h:.1f} core-h   "
          f"~{core_h / workers:.1f} h wall on {workers} workers")
    print(f"workers    {workers} of {cpus} cores, {args.mem} GB each "
          f"(~{workers * args.mem} GB peak)")
    print(f"pinning    {'taskset, one core per run' if pinning else 'UNAVAILABLE'}")
    if not pinning:
        print("           !! without taskset, GA's TBB pool will oversubscribe the box")
        print("           !! and every wall-clock number below measures contention.")
    if args.workers > cpus:
        print(f"           !! --workers {args.workers} exceeds {cpus} cores; timings "
              f"will be inflated.")
    print()

    if args.dry_run:
        print("dry run: nothing executed.")
        return 0

    # ---- execute -----------------------------------------------------
    workdir = Path(tempfile.mkdtemp(prefix="rcpsp_bench_"))
    pool = CorePool(workers)
    lock = threading.Lock()
    progress = {"done": 0, "total": len(jobs)}
    rows: list[dict] = []

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pex:
        futures = [pex.submit(run_job, j, args, pool, lock, workdir, progress)
                   for j in jobs]
        for f in as_completed(futures):
            rows.append(f.result())
    print(f"\nfinished {len(rows)} runs in {(time.perf_counter() - started) / 60:.1f} min")

    if args.csv and rows:
        with args.csv.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: (r["size_class"], r["instance"],
                                                    r["method"], str(r["seed"]))))
        print(f"per-run table written to {args.csv}")

    return summarise(rows, args)


if __name__ == "__main__":
    raise SystemExit(main())
