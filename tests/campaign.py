#!/usr/bin/env python3
"""
Statistical benchmark campaign: the decomposition methods against GA, with the
compact MILP as reference, over enough instances per size class to support a
significance claim.

    # the campaign (16 cores, ~6 h; interruptible, see --resume)
    python3 tests/campaign.py --workers 16 --csv campaign.csv

    # what it will cost, without running anything
    python3 tests/campaign.py --workers 16 --dry-run

    # after an interruption -- picks up exactly where it stopped
    python3 tests/campaign.py --workers 16 --csv campaign.csv --resume

    # re-report from a finished CSV, no solving at all
    python3 tests/campaign.py --report-only campaign.csv

Instances are `instances/<class>_<replicate>.txt`; a class-p instance has 32*p
tasks. The default design is classes 1,2,3,4,6,8,12,16 (32..512 tasks) with 10
replicates each, denser at the low end because that is where the MILP still
proves optimality and the crossover happens.

SELF-CONTAINED ON PURPOSE
-------------------------
This file imports nothing from test_decomposition.py or benchmark_methods.py.
A campaign runs on a different machine from where it is edited, and a shared
helper whose signature only one side evolves is a version-skew trap -- we hit
exactly that once already. Copy this one file and it runs. The cost is ~80
duplicated lines of instance parsing, which is the cheap side of that trade.

WHAT MAKES THE NUMBERS MEAN SOMETHING
-------------------------------------
* **Pairwise comparisons are reference-free.** For two methods on the same
  instance the reference cancels out of the normalised gap difference:
  (a-ref)/scale - (b-ref)/scale = (a-b)/scale. So the significance tests do
  not care whether the MILP proved, found an incumbent, or found nothing, and
  they are the part of this report to trust most.

* **Paired, not pooled.** Every comparison uses only instances where BOTH arms
  produced a usable run, and the per-class tables are restricted to instances
  where EVERY arm did. Pooling unequal instance sets compares different
  problems and flatters whichever method failed on the hard ones.

* **Exact Wilcoxon signed-rank.** Non-parametric because normalised gaps are
  skewed and heavy-tailed; exact rather than normal-approximated because the
  per-cell n is small. Holm-Bonferroni within each size class, since six
  pre-registered comparisons are tested per class.

* **Percentage gaps are suppressed when they are meaningless.** With negative
  prices an objective can approach zero and a percentage of it is unbounded --
  that is where the previous run produced a "340 %" that meant nothing. gap%
  is reported only when |reference| exceeds 2 % of the instance scale.

* **Time-limit compliance is measured, not assumed.** --tl bounds the solver's
  own search; it does not cover warm start, precedence closure, switching
  graph construction, the final subproblem re-solve, or battery
  post-processing. Wall minus solver time is reported per arm, because an
  equal-time comparison where one side quietly runs 20 % over is not one.

* **GA is stochastic, the rest are not.** It repeats over --seeds and is
  reported by the MEAN of its seeds; best-of-k is biased upward in k and is
  not comparable against a deterministic method's single run.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import queue
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

# ==========================================================================
# design
# ==========================================================================

REFERENCE = "MILP"
TASKS_PER_CLASS = 32
E_PROC = 4.0                      # mirrors MachineProfile archetype A2

DEFAULT_SIZES = [1, 2, 3, 4, 6, 8, 12, 16]
DEFAULT_PER_SIZE = 10


@dataclass(frozen=True)
class Arm:
    """A labelled configuration of the solver.

    `label` is what the report calls it; `method` is what -m receives. The two
    differ so the same solver method can appear more than once under different
    flags -- LBBD and LBBD-f5 are the same code with a different subproblem
    time limit.
    """
    method: str
    extra: tuple[str, ...] = ()
    stochastic: bool = False


ARMS: dict[str, Arm] = {
    "MILP":       Arm("MILP"),
    "GA":         Arm("GA", stochastic=True),
    "LBBD":       Arm("LBBD"),
    # The diagnostic arm. At --sub-tl 60 (10 % of a 600 s budget) the master
    # can afford at most ~10 subproblem calls, hence at most ~10 cuts to steer
    # a search over O(h^2) binaries. If cut starvation is why LBBD collapses
    # above class 1, this arm beats plain LBBD and the fix is a one-line
    # default change. If it does not, the master relaxation is the problem.
    "LBBD-f5":    Arm("LBBD", ("--sub-tl", "5")),
    "NoGoodCuts": Arm("NoGoodCuts"),
    "StateLBBD":  Arm("StateLBBD"),
    "Benders":    Arm("Benders"),
}
DEFAULT_ARMS = list(ARMS)

# Pre-registered comparisons: fixed before the data exists, so the p-values
# below are not the survivors of an all-pairs fishing trip. Reported as
# mean(a) - mean(b) in normalised gap, so NEGATIVE means `a` is better.
COMPARISONS: list[tuple[str, str, str]] = [
    ("LBBD",      "GA",         "does LBBD beat the GA"),
    ("Benders",   "GA",         "does Benders beat the GA"),
    ("LBBD-f5",   "LBBD",       "short subproblem TL: does it fix cut starvation"),
    ("LBBD",      "NoGoodCuts", "is conflict refinement worth anything"),
    ("Benders",   "StateLBBD",  "value of battery coordination (master held fixed)"),
    ("StateLBBD", "LBBD",       "cost of losing the SPACES pre-processing"),
]

# Fraction of its budget each arm actually consumes, for the up-front estimate
# only. This is an UPPER bound in practice: on small classes the MILP and the
# decompositions prove optimality in seconds and never reach --tl.
BUDGET_USE = {"MILP": 0.95, "GA": 1.0, "LBBD": 0.85, "LBBD-f5": 0.9,
              "NoGoodCuts": 0.95, "StateLBBD": 0.9, "Benders": 0.9}

CSV_FIELDS = ["instance", "size_class", "replicate", "n", "ei", "horizon",
              "battery", "scale", "arm", "method", "seed", "ok", "objective",
              "energy", "tardiness", "wall_s", "solver_s", "overhead_s", "gap",
              "proved", "subproblems", "inconclusive", "returncode", "note"]


# ==========================================================================
# instance file  (mirrors Instance::from; kept local, see module docstring)
# ==========================================================================

@dataclass
class Task:
    duration: int
    resources: list[int]
    successors: list[int]
    release: int
    due: int
    weight: float

    @property
    def is_ei(self) -> bool:
        return self.resources[0] > 0


@dataclass
class Instance:
    capacities: list[int]
    tasks: list[Task]
    prices: list[float]

    @property
    def n(self) -> int:
        return len(self.tasks)

    @property
    def horizon(self) -> int:
        return len(self.prices)

    @property
    def ei_ids(self) -> list[int]:
        return [i for i, t in enumerate(self.tasks) if t.is_ei]


def read_instance(path: Path) -> Instance:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    n, m = (int(x) for x in lines[0].split()[:2])
    capacities = [int(x) for x in lines[1].split()[:m]]

    tasks: list[Task] = []
    for row in lines[2:2 + n]:
        f = row.split()
        p = 0
        duration = int(f[p]); p += 1
        resources = [int(f[p + k]) for k in range(m)]; p += m
        nsucc = int(f[p]); p += 1
        successors = [int(f[p + k]) for k in range(nsucc)]; p += nsucc
        release = int(f[p]); p += 1
        due = int(f[p]); p += 1
        weight = float(f[p])
        tasks.append(Task(duration, resources, successors, release, due, weight))

    return Instance(capacities, tasks, [float(x) for x in lines[2 + n].split()])


def battery_size(inst: Instance) -> int:
    """Roughly one day of EI machine demand -- design.py's BATTERY_ON_RATIO=1."""
    ei_duration = sum(inst.tasks[i].duration for i in inst.ei_ids)
    days = max(1.0, inst.horizon / 24.0)
    return max(1, round(E_PROC * ei_duration / days))


def norm_scale(inst: Instance) -> float:
    """A positive, treatment-invariant scale for one instance.

    The energy bill of running the machine flat out at the mean price, with no
    optimisation at all. It does not depend on the method, so a normalised gap
    stays comparable across instances and tariffs where a percentage of a
    near-zero objective does not.
    """
    ei_duration = sum(inst.tasks[i].duration for i in inst.ei_ids)
    mean_price = abs(statistics.fmean(inst.prices)) if inst.prices else 0.0
    return E_PROC * ei_duration * mean_price or float("nan")


def pick_instances(root: Path, sizes: list[int], per_size: int) -> list[Path]:
    out: list[Path] = []
    for p in sizes:
        found = sorted((root / "instances").glob(f"{p}_*.txt"),
                       key=lambda q: int(q.stem.split("_")[1]))
        if not found:
            print(f"  warning: no instance for size class {p}", file=sys.stderr)
        elif len(found) < per_size:
            print(f"  warning: class {p} has only {len(found)} instances, "
                  f"{per_size} requested", file=sys.stderr)
        out.extend(found[:per_size])
    return out


# ==========================================================================
# execution
# ==========================================================================

@dataclass
class Run:
    ok: bool
    returncode: int
    note: str
    sol: dict = field(default_factory=dict)

    @property
    def info(self) -> dict:
        return self.sol.get("solution_info", {}) or {}

    @property
    def diag(self) -> dict:
        return self.sol.get("diagnostics", {}) or {}

    def num(self, key: str) -> float:
        v = self.info.get(key)
        return float("nan") if v is None else float(v)

    def dnum(self, key: str) -> float:
        v = self.diag.get(key)
        return float("nan") if v is None else float(v)

    @property
    def objective(self) -> float:
        return self.num("objective_value")

    @property
    def proved_optimal(self) -> bool:
        """Optimality actually certified, not merely 'finished'.

        A gap of 0 is not enough for a decomposition method: if any subproblem
        returned without a verdict the master's tree was pruned on an
        assumption and the gap stops meaning anything.
        """
        gap = self.num("gap")
        if not math.isfinite(gap) or gap > 1e-6:
            return False
        inc = self.dnum("inconclusive")
        return not (math.isfinite(inc) and inc > 0)

    def why_not_optimal(self) -> str:
        gap, inc = self.num("gap"), self.dnum("inconclusive")
        if not math.isfinite(gap):
            return "no gap reported"
        if gap > 1e-6:
            return f"gap={gap:.2e}"
        if math.isfinite(inc) and inc > 0:
            return f"{int(inc)} inconclusive subproblem(s)"
        return "?"


def run_pinned(solver: Path, arm: str, instance: Path, battery: int, tl: int,
               seed: int | None, mem_gb: int, cpu: int | None,
               workdir: Path) -> Run:
    """One solver invocation, pinned to a single core.

    Pinning is not a nicety when runs execute in parallel: --thl bounds
    Gurobi's own threads but does nothing for the TBB pool ParadisEO uses for
    GA, which defaults to hardware_concurrency() with no env override. Without
    taskset one GA run oversubscribes the box and every wall-clock number in
    the comparison measures contention rather than the method.
    """
    spec = ARMS[arm]
    stem = f"{instance.stem}__{arm}__b{battery}" + (f"__s{seed}" if seed else "")
    out_json = workdir / f"{stem}.json"
    argv = [str(solver), "-i", str(instance), "-m", spec.method, "-b", str(battery),
            "--tl", str(tl), "--thl", "1", "--ml", str(mem_gb), "-o", str(out_json)]
    argv += list(spec.extra)
    if seed:
        argv += ["-s", str(seed)]
    if cpu is not None and shutil.which("taskset"):
        argv = ["taskset", "-c", str(cpu)] + argv

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=tl + 300)
    except subprocess.TimeoutExpired:
        return Run(False, -9, f"hard timeout at {tl + 300}s (solver ignored --tl)")
    if proc.returncode != 0 or not out_json.exists():
        why = (proc.stderr or "").strip()[-300:]
        if not why:
            why = (f"exit {proc.returncode}" if proc.returncode
                   else "exit 0 but no output file")
        return Run(False, proc.returncode, why)
    try:
        sol = json.loads(out_json.read_text())
    except ValueError as exc:
        return Run(False, proc.returncode, f"bad JSON: {exc}")
    finally:
        out_json.unlink(missing_ok=True)      # a long campaign must not fill /tmp
    return Run(True, 0, "", sol)


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


class ResultSink:
    """Appends every finished run to the CSV immediately.

    A campaign of this length will be interrupted -- by a reboot, a scheduler,
    or an impatient Ctrl-C -- and writing only at the end would throw away
    hours of solving. Flushed per row so an interrupted file is still complete
    up to its last line, which is what --resume reads.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.rows: list[dict] = []
        self.fh = None
        self.writer = None
        if path is not None:
            new = not path.exists() or path.stat().st_size == 0
            self.fh = path.open("a", newline="")
            self.writer = csv.DictWriter(self.fh, fieldnames=CSV_FIELDS,
                                         extrasaction="ignore")
            if new:
                self.writer.writeheader()
                self.fh.flush()

    def add(self, row: dict) -> None:
        with self.lock:
            self.rows.append(row)
            if self.writer is not None:
                self.writer.writerow(row)
                self.fh.flush()
                os.fsync(self.fh.fileno())

    def close(self) -> None:
        if self.fh is not None:
            self.fh.close()


def job_key(row_or_job: dict) -> tuple[str, str, str]:
    inst = row_or_job.get("instance") or row_or_job["path"].name
    return (str(inst), str(row_or_job["arm"]), str(row_or_job.get("seed") or ""))


def load_rows(path: Path) -> list[dict]:
    """Reads a campaign CSV back, coercing the numeric columns."""
    numeric = {"size_class", "replicate", "n", "ei", "horizon", "battery",
               "scale", "ok", "objective", "energy", "tardiness", "wall_s",
               "solver_s", "overhead_s", "gap", "proved", "subproblems",
               "inconclusive", "returncode"}
    out: list[dict] = []
    with path.open(newline="") as fh:
        for raw in csv.DictReader(fh):
            row = dict(raw)
            for k in numeric & set(row):
                try:
                    row[k] = float(row[k]) if row[k] != "" else float("nan")
                except ValueError:
                    row[k] = float("nan")
            for k in ("size_class", "replicate", "n", "ei", "horizon", "ok", "proved"):
                if k in row and math.isfinite(row[k]):
                    row[k] = int(row[k])
            out.append(row)
    return out


def run_job(job: dict, args, pool: CorePool, sink: ResultSink,
            progress: dict, lock: threading.Lock) -> None:
    core = pool.acquire()
    try:
        t0 = time.perf_counter()
        run = run_pinned(args.solver, job["arm"], job["path"], job["battery"],
                         args.tl, job["seed"], args.mem, core, args.workdir)
        wall = time.perf_counter() - t0
    finally:
        pool.release(core)

    solver_s = run.num("computation_time") if run.ok else float("nan")
    row = {
        "instance": job["path"].name, "size_class": job["size_class"],
        "replicate": job["replicate"], "n": job["n"], "ei": job["ei"],
        "horizon": job["horizon"], "battery": job["battery"],
        "scale": job["scale"], "arm": job["arm"],
        "method": ARMS[job["arm"]].method, "seed": job["seed"] or "",
        "ok": int(run.ok),
        "objective": run.objective if run.ok else float("nan"),
        "energy": run.num("energy_cost") if run.ok else float("nan"),
        "tardiness": run.num("tardiness_cost") if run.ok else float("nan"),
        "wall_s": round(wall, 3),
        "solver_s": solver_s,
        "overhead_s": round(wall - solver_s, 3) if math.isfinite(solver_s) else float("nan"),
        "gap": run.num("gap") if run.ok else float("nan"),
        "proved": int(run.ok and run.proved_optimal),
        "subproblems": run.dnum("subproblems") if run.ok else float("nan"),
        "inconclusive": run.dnum("inconclusive") if run.ok else float("nan"),
        "returncode": run.returncode, "note": run.note.replace("\n", " ")[:200],
    }
    sink.add(row)

    with lock:
        progress["done"] += 1
        d, tot = progress["done"], progress["total"]
        elapsed = time.perf_counter() - progress["t0"]
        eta = (elapsed / d) * (tot - d) / 60.0 if d else 0.0
        obj = row["objective"]
        state = ("FAILED " + row["note"][:40] if not run.ok else
                 "proved" if row["proved"] else run.why_not_optimal())
        objs = f"{obj:13.3f}" if math.isfinite(obj) else "          n/a"
        print(f"  [{d:4d}/{tot}] {job['path'].name:9s} n={job['n']:4d} "
              f"{job['arm'] + ('/s' + str(job['seed']) if job['seed'] else ''):14s} "
              f"obj={objs} {wall:6.1f}s  {state:36s} eta {eta:5.0f}m", flush=True)


# ==========================================================================
# statistics  (pure stdlib -- scipy is not installed on the run machine)
# ==========================================================================

def average_ranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged, which is what the signed-rank test needs."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def wilcoxon_signed_rank(diffs: list[float]) -> tuple[float, int]:
    """Two-sided exact Wilcoxon signed-rank test. Returns (p, n_used).

    Exact rather than normal-approximated because per-cell n here is 6-20,
    where the approximation is poor. The exact null is computed by a DP over
    achievable rank sums instead of enumerating 2^n sign vectors, so the cost
    is O(n * sum_of_ranks) and n=20 is instant. Ranks are doubled to stay
    integral under tied (half-integer) ranks.

    Zero differences are dropped (Wilcoxon's original handling). That is
    conservative here: identical arms -- LBBD vs NoGoodCuts, say -- yield no
    usable pairs at all rather than a spuriously tiny p.
    """
    d = [x for x in diffs if math.isfinite(x) and x != 0.0]
    n = len(d)
    if n == 0:
        return float("nan"), 0
    ranks = average_ranks([abs(x) for x in d])
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    w_minus = sum(r for r, x in zip(ranks, d) if x < 0)
    w = min(w_plus, w_minus)

    scaled = [int(round(2 * r)) for r in ranks]
    dist: dict[int, int] = {0: 1}
    for s in scaled:
        nxt: dict[int, int] = defaultdict(int)
        for total, count in dist.items():
            nxt[total] += count
            nxt[total + s] += count
        dist = nxt
    target = int(round(2 * w))
    tail = sum(c for t, c in dist.items() if t <= target)
    return min(1.0, 2.0 * tail / float(2 ** n)), n


def bootstrap_ci(values: list[float], reps: int = 10000,
                 alpha: float = 0.05, seed: int = 20260813) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean. Non-parametric, like the test."""
    vals = [v for v in values if math.isfinite(v)]
    if len(vals) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(vals)
    means = sorted(statistics.fmean(rng.choices(vals, k=n)) for _ in range(reps))
    lo = means[int(alpha / 2 * reps)]
    hi = means[min(reps - 1, int((1 - alpha / 2) * reps))]
    return (lo, hi)


def holm(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values.

    Six pre-registered comparisons are tested per size class; without a
    correction one of them clearing 0.05 by chance is close to expected.
    """
    idx = [i for i, p in enumerate(pvals) if math.isfinite(p)]
    adj = [float("nan")] * len(pvals)
    m = len(idx)
    running = 0.0
    for rank, i in enumerate(sorted(idx, key=lambda k: pvals[k])):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


def stars(p: float) -> str:
    if not math.isfinite(p):
        return "   "
    return "***" if p < 0.001 else "** " if p < 0.01 else "*  " if p < 0.05 else "   "


def self_test() -> int:
    """Checks the statistics against values that can be looked up.

    Worth thirty seconds before a multi-hour campaign: these functions are
    hand-rolled because scipy is not installed on the run machine, and a wrong
    p-value is the kind of error that survives all the way into a paper.
    """
    bad: list[str] = []

    def chk(name: str, got: float, want: float, tol: float = 1e-9) -> None:
        ok = math.isfinite(got) and abs(got - want) <= tol
        print(f"  {'ok  ' if ok else 'FAIL'} {name:48s} {got!r:>22} vs {want!r}")
        if not ok:
            bad.append(name)

    print("exact Wilcoxon signed-rank vs textbook values")
    # All-positive differences give the smallest attainable two-sided p, 2/2^n.
    for n in (5, 6, 10):
        p, _ = wilcoxon_signed_rank([1.0] * n)
        chk(f"n={n}, all positive", p, 2.0 / 2 ** n)
    chk("perfectly symmetric -> 1", wilcoxon_signed_rank([1.0, -1.0, 2.0, -2.0])[0], 1.0)
    p, k = wilcoxon_signed_rank([0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    chk("zeros dropped: n_used", float(k), 5.0)
    chk("zeros dropped: p as n=5", p, 2.0 / 32)
    chk("all zero -> n_used 0", float(wilcoxon_signed_rank([0.0, 0.0])[1]), 0.0)
    # n=10, W=8 is the classic two-sided 5 % critical value; W=9 is not.
    p8, _ = wilcoxon_signed_rank([-1.0, -2.0, -5.0, 3, 4, 6, 7, 8, 9, 10])
    p9, _ = wilcoxon_signed_rank([-1.0, -3.0, -5.0, 2, 4, 6, 7, 8, 9, 10])
    print(f"  {'ok  ' if p8 < 0.05 < p9 else 'FAIL'} "
          f"{'n=10 critical value brackets 0.05':48s} W=8 p={p8:.5f}, W=9 p={p9:.5f}")
    if not p8 < 0.05 < p9:
        bad.append("n=10 critical value")
    ptie, _ = wilcoxon_signed_rank([1.0, 1.0, 1.0, -1.0, -1.0, 2.0])
    chk("tied |d| stays a probability", min(ptie, 1.0), ptie)

    print("average ranks")
    chk("no ties, sum = n(n+1)/2", sum(average_ranks([3.0, 1.0, 2.0])), 6.0)
    chk("tie averaged", average_ranks([5.0, 5.0, 1.0])[0], 2.5)

    print("Holm-Bonferroni")
    adj = holm([0.01, 0.02, 0.03])
    chk("holm[0] = 3*0.01", adj[0], 0.03)
    chk("holm[1] = 2*0.02", adj[1], 0.04)
    chk("holm[2] monotone", adj[2], 0.04)
    chk("holm caps at 1", holm([0.5, 0.5])[0], 1.0)
    chk("nan excluded from m", holm([0.001, float("nan")])[0], 0.001)

    print("bootstrap CI")
    lo, hi = bootstrap_ci([1.0] * 20, reps=2000)
    chk("zero variance -> degenerate", lo, 1.0)
    chk("zero variance -> degenerate", hi, 1.0)
    lo, hi = bootstrap_ci([float(x) for x in range(1, 21)], reps=4000)
    ok = lo < 10.5 < hi
    print(f"  {'ok  ' if ok else 'FAIL'} {'1..20 CI brackets the mean 10.5':48s} "
          f"[{lo:.2f}, {hi:.2f}]")
    if not ok:
        bad.append("bootstrap brackets mean")

    print("\n" + ("self-test PASSED" if not bad else f"self-test FAILED: {bad}"))
    return 1 if bad else 0


# ==========================================================================
# reporting
# ==========================================================================

def usable(runs: list[dict]) -> list[dict]:
    return [r for r in runs if r["ok"] and math.isfinite(r["objective"])]


def arm_objective(runs: list[dict]) -> float:
    """One number per (instance, arm): the MEAN over seeds.

    Best-of-k is biased upward in k and would flatter GA against the
    deterministic arms, which run once by construction.
    """
    good = usable(runs)
    return statistics.fmean(r["objective"] for r in good) if good else float("nan")


def reference_for(per_arm: dict[str, list[dict]]) -> tuple[float, str]:
    """The value everything on this instance is measured against.

    Three regimes, and conflating them is the easiest way to publish a wrong
    conclusion:
      proven     the MILP closed   -> this is the optimum
      incumbent  the MILP ran out  -> being beaten here is expected
      best       the MILP found nothing -> fall back to the best any arm
                 produced, which is all the instance can support
    """
    ref = usable(per_arm.get(REFERENCE, []))
    if ref:
        return ref[0]["objective"], "proven" if ref[0]["proved"] else "incumbent"
    finite = [r["objective"] for runs in per_arm.values() for r in usable(runs)]
    return (min(finite), "best") if finite else (float("nan"), "none")


def build_index(rows: list[dict]):
    by_inst: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        by_inst.setdefault(r["instance"], {}).setdefault(r["arm"], []).append(r)
    meta = {}
    for inst, per_arm in by_inst.items():
        any_row = next(iter(per_arm.values()))[0]
        ref, regime = reference_for(per_arm)
        meta[inst] = {"class": any_row["size_class"], "n": any_row["n"],
                      "scale": any_row["scale"], "ref": ref, "regime": regime}
    return by_inst, meta


def section(title: str) -> None:
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


def report_coverage(by_inst, meta, arms: list[str]) -> None:
    section("1. COVERAGE AND REFERENCE STATUS")
    print(f"  {'class':>5s} {'tasks':>6s} {'inst':>5s} {'complete':>9s} "
          f"{'MILP proved':>12s} {'incumbent':>10s} {'nothing':>8s}")
    for c in sorted({m["class"] for m in meta.values()}):
        members = [i for i in by_inst if meta[i]["class"] == c]
        complete = sum(1 for i in members
                       if all(usable(by_inst[i].get(a, [])) for a in arms))
        cnt = {k: sum(1 for i in members if meta[i]["regime"] == k)
               for k in ("proven", "incumbent", "best", "none")}
        print(f"  {c:5d} {c * TASKS_PER_CLASS:6d} {len(members):5d} {complete:9d} "
              f"{cnt['proven']:12d} {cnt['incumbent']:10d} "
              f"{cnt['best'] + cnt['none']:8d}")
    print("\n  'complete' counts instances where every arm produced a usable run;"
          " only those\n  feed the per-class table. Pairwise tests in section 3 "
          "use their own, larger\n  paired subsets. Where 'MILP proved' hits zero"
          " the reference stops being an\n  optimum -- that transition is a "
          "result, not a defect.")


def report_per_class(by_inst, meta, arms: list[str]) -> None:
    section("2. PER-CLASS SUMMARY  (complete instances only; gapN = (obj - ref) / scale)")
    print(f"  {'class':>5s} {'arm':12s} {'n':>3s} {'proved':>7s} {'gapN mean':>10s} "
          f"{'gapN med':>9s} {'gapN p90':>9s} {'gap% mean':>10s} {'wall s':>8s} "
          f"{'over s':>7s} {'wins':>5s}")
    for c in sorted({m["class"] for m in meta.values()}):
        members = [i for i in by_inst if meta[i]["class"] == c
                   and all(usable(by_inst[i].get(a, [])) for a in arms)
                   and math.isfinite(meta[i]["ref"])]
        if not members:
            print(f"  {c:5d}  -- no instance has every arm; see section 3 for "
                  f"pairwise results")
            continue
        best_of = {i: min(arm_objective(by_inst[i][a]) for a in arms) for i in members}
        for arm in arms:
            gn, gp, walls, overs, proved, wins, suppressed = [], [], [], [], 0, 0, 0
            for i in members:
                runs = usable(by_inst[i][arm])
                obj, ref, scale = arm_objective(by_inst[i][arm]), meta[i]["ref"], meta[i]["scale"]
                gn.append((obj - ref) / scale)
                # A percentage of a near-zero reference is unbounded and
                # meaningless; that is where "340 %" came from last time.
                if abs(ref) > 0.02 * abs(scale):
                    gp.append(100.0 * (obj - ref) / abs(ref))
                else:
                    suppressed += 1
                walls.append(statistics.fmean(r["wall_s"] for r in runs))
                ov = [r["overhead_s"] for r in runs if math.isfinite(r["overhead_s"])]
                overs.append(statistics.fmean(ov) if ov else float("nan"))
                proved += statistics.fmean(r["proved"] for r in runs)
                if obj <= best_of[i] + 1e-9:
                    wins += 1
            srt = sorted(gn)
            p90 = srt[min(len(srt) - 1, int(0.9 * len(srt)))]
            ov_ok = [o for o in overs if math.isfinite(o)]
            gps = f"{statistics.fmean(gp):10.3f}" if gp else "       n/a"
            print(f"  {c:5d} {arm:12s} {len(members):3d} {proved / len(members):7.2f} "
                  f"{statistics.fmean(gn):10.5f} {statistics.median(gn):9.5f} "
                  f"{p90:9.5f} {gps} {statistics.fmean(walls):8.1f} "
                  f"{(statistics.fmean(ov_ok) if ov_ok else float('nan')):7.1f} "
                  f"{wins:5d}")
            if suppressed and arm == arms[-1]:
                print(f"        ({suppressed} gap% value(s) suppressed this class: "
                      f"|reference| below 2 % of instance scale)")


def report_pairwise(by_inst, meta, arms: list[str], reps: int) -> None:
    section("3. PRE-REGISTERED PAIRWISE COMPARISONS  (paired, reference-free)")
    print("  Paired difference in normalised gap, (obj_a - obj_b) / scale. The")
    print("  reference cancels, so these hold regardless of whether the MILP")
    print("  proved anything. NEGATIVE means the first arm is BETTER.")
    print("  p from an exact two-sided Wilcoxon signed-rank test, Holm-corrected")
    print("  across the six comparisons within each size class.\n")

    active = [(a, b, why) for a, b, why in COMPARISONS if a in arms and b in arms]
    for c in sorted({m["class"] for m in meta.values()}):
        members = [i for i in by_inst if meta[i]["class"] == c]
        print(f"  class {c}  ({c * TASKS_PER_CLASS} tasks)")
        print(f"    {'comparison':26s} {'n':>3s} {'mean diff':>10s} "
              f"{'95% CI':>19s} {'median':>9s} {'a<b':>5s} {'a>b':>5s} "
              f"{'p':>8s} {'p_holm':>8s}")
        cells = []
        for a, b, _why in active:
            diffs = []
            for i in members:
                if not (usable(by_inst[i].get(a, [])) and usable(by_inst[i].get(b, []))):
                    continue
                sc = meta[i]["scale"]
                if not math.isfinite(sc) or sc == 0:
                    continue
                diffs.append((arm_objective(by_inst[i][a]) -
                              arm_objective(by_inst[i][b])) / sc)
            p, n_used = wilcoxon_signed_rank(diffs)
            cells.append({"a": a, "b": b, "diffs": diffs, "p": p, "n_used": n_used})
        for cell, padj in zip(cells, holm([x["p"] for x in cells])):
            d = cell["diffs"]
            label = cell["a"] + " - " + cell["b"]
            if not d:
                print(f"    {label:26s} {0:>3d}   no paired instance")
                continue
            if cell["n_used"] == 0:
                # Every paired difference is exactly zero. Wilcoxon drops zeros
                # and has nothing left to test, but "identical on all N" is a
                # far stronger statement than the nan it would otherwise print
                # -- and for LBBD vs NoGoodCuts it IS the finding: the conflict
                # refiner never changed a single answer.
                print(f"    {label:26s} {len(d):3d}    IDENTICAL on all "
                      f"{len(d)} paired instance(s)")
                continue
            lo, hi = bootstrap_ci(d, reps=reps)
            below = sum(1 for x in d if x < -1e-12)
            above = sum(1 for x in d if x > 1e-12)
            ci = (f"[{lo:8.5f},{hi:8.5f}]" if math.isfinite(lo) else "".rjust(19))
            print(f"    {cell['a'] + ' - ' + cell['b']:26s} {len(d):3d} "
                  f"{statistics.fmean(d):10.5f} {ci:>19s} "
                  f"{statistics.median(d):9.5f} {below:5d} {above:5d} "
                  f"{cell['p']:8.4f} {padj:8.4f} {stars(padj)}")
        print()


def report_effects(by_inst, meta, arms: list[str], reps: int) -> None:
    """The decomposition from docs/BENDERS_BATTERY.md section 5.

    Benders differs from LBBD in two ways at once, and they pull in opposite
    directions: it prices the battery inside the master (should help) but it
    cannot use the SPACES pre-processing (should hurt). StateLBBD is the
    control that isolates them -- same explicit-state master as Benders, same
    battery post-processing as LBBD -- so the two effects can be measured
    separately instead of being read off a single net number.
    """
    if not {"Benders", "StateLBBD", "LBBD"} <= set(arms):
        return
    section("4. EFFECT DECOMPOSITION: why Benders differs from LBBD")
    print("  battery coordination = Benders - StateLBBD   (master held fixed)")
    print("  cost of losing SPACES = StateLBBD - LBBD     (battery treatment held fixed)")
    print("  net                   = Benders - LBBD       (should equal their sum)\n")
    print(f"  {'class':>5s} {'n':>3s} {'coordination':>13s} {'lost SPACES':>13s} "
          f"{'net':>10s} {'sum check':>10s}")
    for c in sorted({m["class"] for m in meta.values()}):
        members = [i for i in by_inst if meta[i]["class"] == c
                   and all(usable(by_inst[i].get(a, []))
                           for a in ("Benders", "StateLBBD", "LBBD"))]
        if not members:
            continue
        coord, spaces, net = [], [], []
        for i in members:
            sc = meta[i]["scale"]
            ben = arm_objective(by_inst[i]["Benders"])
            sta = arm_objective(by_inst[i]["StateLBBD"])
            lbd = arm_objective(by_inst[i]["LBBD"])
            coord.append((ben - sta) / sc)
            spaces.append((sta - lbd) / sc)
            net.append((ben - lbd) / sc)
        mc, ms, mn = (statistics.fmean(x) for x in (coord, spaces, net))
        print(f"  {c:5d} {len(members):3d} {mc:13.5f} {ms:13.5f} {mn:10.5f} "
              f"{mc + ms:10.5f}")
    print("\n  A negative coordination column with a larger positive SPACES column")
    print("  is the predicted trade-off: pricing the battery in the master helps,")
    print("  but paying for it with the switching-graph pre-processing costs more.")


def report_compliance(rows: list[dict], tl: int, arms: list[str]) -> None:
    section("5. TIME-LIMIT COMPLIANCE AND CUT DIAGNOSTICS")
    print("  --tl bounds the solver's search. It does NOT cover warm start,")
    print("  precedence closure, switching-graph construction, the final")
    print("  subproblem re-solve, or battery post-processing. An equal-time")
    print("  comparison where one arm runs 20 % over is not an equal-time one.\n")
    print(f"  {'arm':12s} {'runs':>5s} {'fail':>5s} {'wall p50':>9s} {'wall max':>9s} "
          f"{'over p50':>9s} {'over max':>9s} {'> tl':>6s} {'subprob':>9s} {'inconcl':>8s}")
    for arm in arms:
        mine = [r for r in rows if r["arm"] == arm]
        if not mine:
            continue
        good = [r for r in mine if r["ok"]]
        walls = sorted(r["wall_s"] for r in good) or [float("nan")]
        overs = sorted(r["overhead_s"] for r in good
                       if math.isfinite(r["overhead_s"])) or [float("nan")]
        over_tl = sum(1 for r in good if r["wall_s"] > tl * 1.05)
        subs = [r["subproblems"] for r in good if math.isfinite(r["subproblems"])]
        incs = [r["inconclusive"] for r in good if math.isfinite(r["inconclusive"])]
        print(f"  {arm:12s} {len(mine):5d} {len(mine) - len(good):5d} "
              f"{statistics.median(walls):9.1f} {max(walls):9.1f} "
              f"{statistics.median(overs):9.1f} {max(overs):9.1f} {over_tl:6d} "
              f"{(statistics.median(subs) if subs else float('nan')):9.1f} "
              f"{(statistics.median(incs) if incs else float('nan')):8.1f} ")
    print("\n  'subprob' is the median number of subproblem solves per run. If it is")
    print("  in single digits for LBBD, the master got single-digit cuts to steer a")
    print("  search over O(h^2) binaries, and cut starvation -- not the cut quality")
    print("  -- is what to fix. LBBD-f5 is the same code with --sub-tl 5; compare.")

    failed = [r for r in rows if not r["ok"]]
    if failed:
        print(f"\n  {len(failed)} failed run(s):")
        seen: dict[tuple[str, str], int] = defaultdict(int)
        for r in failed:
            seen[(r["arm"], (r["note"] or "?")[:70])] += 1
        for (arm, note), k in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f"    {k:4d} x  {arm:12s} {note}")


def summarise(rows: list[dict], arms: list[str], tl: int, reps: int) -> int:
    rows = [r for r in rows if r["arm"] in arms]
    if not rows:
        print("no rows to report", file=sys.stderr)
        return 2
    by_inst, meta = build_index(rows)
    report_coverage(by_inst, meta, arms)
    report_per_class(by_inst, meta, arms)
    report_pairwise(by_inst, meta, arms, reps)
    report_effects(by_inst, meta, arms, reps)
    report_compliance(rows, tl, arms)
    print()
    return 0


# ==========================================================================
# main
# ==========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parents[1]
    cpus = os.cpu_count() or 4
    ap.add_argument("--solver", type=Path, default=root / "build" / "rcpsp_wt_battery")
    ap.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES,
                    help=f"size classes, 32*p tasks each (default {DEFAULT_SIZES})")
    ap.add_argument("--per-size", type=int, default=DEFAULT_PER_SIZE,
                    help=f"replicates per class (default {DEFAULT_PER_SIZE})")
    ap.add_argument("--arms", nargs="+", default=DEFAULT_ARMS, choices=list(ARMS))
    ap.add_argument("--tl", type=int, default=600, help="time limit per run, s")
    ap.add_argument("--seeds", type=int, default=3, help="repeats for stochastic arms")
    ap.add_argument("--battery-ratio", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=max(1, cpus - 1),
                    help=f"parallel runs, one core each (default {max(1, cpus - 1)})")
    ap.add_argument("--mem", type=int, default=4, help="memory cap per run, GB")
    ap.add_argument("--csv", type=Path, help="per-run table, appended as runs finish")
    ap.add_argument("--resume", action="store_true",
                    help="skip jobs already present in --csv")
    ap.add_argument("--report-only", type=Path,
                    help="re-report from an existing CSV without solving")
    ap.add_argument("--bootstrap", type=int, default=10000,
                    help="bootstrap resamples for the CIs (default 10000)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    ap.add_argument("--self-test", action="store_true",
                    help="check the statistics against known values and stop")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.report_only:
        if not args.report_only.exists():
            print(f"FATAL: {args.report_only} not found", file=sys.stderr)
            return 2
        rows = load_rows(args.report_only)
        present = [a for a in ARMS if any(r["arm"] == a for r in rows)]
        print(f"re-reporting {len(rows)} run(s) from {args.report_only}")
        print(f"arms present: {', '.join(present)}")
        return summarise(rows, present, args.tl, args.bootstrap)

    if REFERENCE not in args.arms:
        print(f"FATAL: {REFERENCE} is the reference and must be in --arms",
              file=sys.stderr)
        return 2
    if not args.dry_run and not args.solver.exists():
        print(f"FATAL: solver not found at {args.solver}", file=sys.stderr)
        return 2

    instances = [p for p in pick_instances(root, args.sizes, args.per_size)
                 if p.exists()]
    if not instances:
        print("FATAL: no instances selected", file=sys.stderr)
        return 2

    # ---- job list ----------------------------------------------------
    jobs: list[dict] = []
    for src in instances:
        inst = read_instance(src)
        base = {
            "path": src,
            "size_class": max(1, round(inst.n / TASKS_PER_CLASS)),
            "replicate": int(src.stem.split("_")[1]),
            "n": inst.n, "ei": len(inst.ei_ids), "horizon": inst.horizon,
            "battery": round(args.battery_ratio * battery_size(inst)),
            "scale": norm_scale(inst),
        }
        for arm in args.arms:
            for seed in (range(1, args.seeds + 1) if ARMS[arm].stochastic else [None]):
                jobs.append({**base, "arm": arm, "seed": seed})

    # Replicate-major, longest-first within a replicate. Two things at once:
    # starting the big runs early keeps cores busy to the end of each round,
    # and finishing whole replicates in order means an interrupted campaign
    # still has a BALANCED design -- k complete replicates of every class,
    # which is analysable. Pure longest-first would leave every small class
    # missing if the campaign died at the halfway mark.
    jobs.sort(key=lambda j: (j["replicate"], -j["n"], j["arm"], j["seed"] or 0))

    done: set[tuple[str, str, str]] = set()
    if args.resume and args.csv and args.csv.exists():
        prior = load_rows(args.csv)
        done = {job_key(r) for r in prior}
        jobs = [j for j in jobs if job_key(j) not in done]
        print(f"resume: {len(prior)} run(s) already in {args.csv}, "
              f"{len(jobs)} remaining")

    core_h = sum(args.tl * BUDGET_USE.get(j["arm"], 1.0) for j in jobs) / 3600.0
    workers = max(1, min(args.workers, cpus))
    pinning = shutil.which("taskset") is not None

    print(f"solver     {args.solver}")
    span = (f"{min(j['n'] for j in jobs)}..{max(j['n'] for j in jobs)} tasks"
            if jobs else "nothing outstanding")
    print(f"instances  {len(instances)}  (classes {args.sizes}, "
          f"{args.per_size} replicates, {span})")
    print(f"arms       {', '.join(args.arms)}   (reference: {REFERENCE})")
    print(f"budget     {args.tl}s per run, identical for every arm")
    print(f"runs       {len(jobs)}   <={core_h:.0f} core-h   "
          f"<={core_h / workers:.1f} h wall on {workers} workers  (upper bound: "
          f"small classes finish early)")
    print(f"workers    {workers} of {cpus} cores, {args.mem} GB each "
          f"(~{workers * args.mem} GB peak)")
    print(f"pinning    {'taskset, one core per run' if pinning else 'UNAVAILABLE'}")
    print(f"csv        {args.csv or 'not written -- pass --csv, this run is long'}")

    if not pinning:
        print("           !! without taskset GA's TBB pool oversubscribes the box")
        print("           !! and every wall-clock number below measures contention.")
    if args.workers > cpus:
        print(f"           !! --workers {args.workers} exceeds {cpus} cores; "
              f"timings will be inflated.")
    try:
        gb = int([l for l in Path("/proc/meminfo").read_text().splitlines()
                  if l.startswith("MemTotal")][0].split()[1]) / 1048576.0
        if workers * args.mem > 0.9 * gb:
            print(f"           !! {workers} x {args.mem} GB exceeds 90 % of "
                  f"{gb:.0f} GB RAM; lower --mem or --workers.")
    except (OSError, IndexError, ValueError):
        pass
    if not args.csv:
        print("           !! nothing is written until the end without --csv, and")
        print("           !! an interruption would discard the whole campaign.")
    print()

    if args.dry_run:
        print("dry run: nothing executed.")
        return 0
    if not jobs:
        print("nothing left to run.")
        return summarise(load_rows(args.csv), args.arms, args.tl, args.bootstrap) \
            if args.csv and args.csv.exists() else 0

    # ---- execute -----------------------------------------------------
    args.workdir = Path(tempfile.mkdtemp(prefix="rcpsp_campaign_"))
    sink = ResultSink(args.csv)
    pool = CorePool(workers)
    lock = threading.Lock()
    progress = {"done": 0, "total": len(jobs), "t0": time.perf_counter()}
    interrupted = False

    try:
        with ThreadPoolExecutor(max_workers=workers) as pex:
            futures = [pex.submit(run_job, j, args, pool, sink, progress, lock)
                       for j in jobs]
            for f in as_completed(futures):
                f.result()
    except KeyboardInterrupt:
        interrupted = True
        print("\ninterrupted -- reporting on what finished. Re-run with --resume "
              "to continue.", file=sys.stderr)
    finally:
        sink.close()
        shutil.rmtree(args.workdir, ignore_errors=True)

    elapsed = (time.perf_counter() - progress["t0"]) / 60.0
    print(f"\nfinished {progress['done']}/{len(jobs)} runs in {elapsed:.1f} min")
    if args.csv:
        print(f"per-run table: {args.csv}")

    rows = load_rows(args.csv) if args.csv and args.csv.exists() else sink.rows
    rc = summarise(rows, args.arms, args.tl, args.bootstrap)
    return 130 if interrupted else rc


if __name__ == "__main__":
    raise SystemExit(main())
