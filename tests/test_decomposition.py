#!/usr/bin/env python3
"""
Bring-up tests for the decomposition methods (LBBD / NoGoodCuts / StateLBBD /
Benders).

This is the sequence from docs/LBBD_REVIEW.md §4, automated. It is not a unit
test suite -- it drives the real binary on real instances and checks properties
that must hold if the implementation is correct.

The tests are ordered so that the cheapest and most diagnostic run first. If
T1 fails nothing else means anything, so the script stops early on that.

    python3 tests/test_decomposition.py                       # default set
    python3 tests/test_decomposition.py --quick               # skip MILP oracles
    python3 tests/test_decomposition.py --solver build/rcpsp_wt_battery \\
        --instances instances/1_1.txt instances/1_2.txt --tl 300

WHAT MAKES THIS MORE THAN A SMOKE TEST
--------------------------------------
Three properties give real oracles rather than "it didn't crash":

  * With `-b 0` there is no battery, so the battery-free problem IS the
    problem. Every method here is exact for it, so MILP, LBBD, NoGoodCuts,
    StateLBBD and Benders must all reach the SAME optimum. Any disagreement is
    a bug in exactly one of them and the others tell you which.

  * With a battery, Benders is still exact (its theta is the true battery cost
    for the chosen machine schedule), so Benders must equal MILP. LBBD and
    StateLBBD are not exact there -- they post-process -- so they may only be
    WORSE, never better. A decomposition beating the exact method is the
    signature of an accounting bug, which is precisely the class of bug that
    the terminal-battery-level issue was.

  * Under a flat tariff there is no arbitrage, so folding the battery into the
    master cannot help: Benders must equal StateLBBD exactly. This is the
    sharpest single test of the Benders cut and it costs one extra run.

Equality is only asserted when both runs PROVED optimality (gap ~ 0 and no
inconclusive subproblem). Otherwise the test reports SKIP with the reason,
because comparing two incumbents proves nothing.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Machine profile.
#
# Mirrors the defaults in include/instance.h (MachineProfile, archetype A2).
# The tests never pass --machine-profile or the individual flags, so these are
# the values in force. If you change the solver defaults, change these too --
# the energy reconstruction in T6 will start failing otherwise, which is the
# intended behaviour but the message will point at the wrong place.
# --------------------------------------------------------------------------
E_PROC, E_IDLE, E_OFF = 4.0, 2.0, 0.0
TRANSITION_COST = {
    ("Off", "Proc"): 5.0,
    ("Proc", "Off"): 1.0,
    ("Proc", "Idle"): 2.0,
    ("Idle", "Proc"): 2.5,
}
ETA_C = ETA_D = 0.95

TOL_ABS = 1e-4
TOL_REL = 1e-6
# Looser tolerance for cross-method optimum comparisons: Gurobi's default
# MIPGap is 1e-4, so two methods can legitimately stop 0.01 % apart.
TOL_OPT_REL = 2e-3

DECOMP_METHODS = ("LBBD", "NoGoodCuts", "StateLBBD", "Benders")
ALL_METHODS = ("MILP",) + DECOMP_METHODS


# ==========================================================================
# instance file
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
    """Parses the solver's own instance format (see Instance::from)."""
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

    prices = [float(x) for x in lines[2 + n].split()]
    return Instance(capacities, tasks, prices)


def write_instance(inst: Instance, path: Path, prices: list[float] | None = None) -> Path:
    """Writes a copy, optionally with a different tariff.

    Used to build the flat-tariff variant for T11 without shipping another
    instance file: the schedule structure is held fixed and only the prices
    change, which is what makes that comparison a controlled one.
    """
    out = [f"{inst.n} {len(inst.capacities)}",
           " ".join(str(c) for c in inst.capacities)]
    for t in inst.tasks:
        out.append(" ".join(map(str, [t.duration, *t.resources, len(t.successors),
                                      *t.successors, t.release, t.due, t.weight])))
    out.append(" ".join(f"{p:.6f}" for p in (prices if prices is not None else inst.prices)))
    path.write_text("\n".join(out) + "\n")
    return path


# ==========================================================================
# running the solver
# ==========================================================================

@dataclass
class Run:
    method: str
    instance: Path
    battery: int
    ok: bool
    returncode: int
    stderr: str
    sol: dict = field(default_factory=dict)

    # --- convenience accessors -------------------------------------------
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
    def starts(self) -> list[int]:
        return [t["start_time"] for t in self.sol.get("task_assignments", [])]

    @property
    def levels(self) -> list[float]:
        return [0.0 if x is None else float(x) for x in self.sol.get("battery_levels", [])]

    @property
    def blocks(self) -> list[dict]:
        return self.sol.get("machine_blocks", [])

    @property
    def proved_optimal(self) -> bool:
        """Optimality actually certified, not merely 'finished'.

        A gap of 0 is not enough on its own for a decomposition method: if any
        subproblem returned without a verdict the master's tree was pruned on
        an assumption, and the gap stops meaning anything. That is exactly what
        diag_inconclusive exists to record.
        """
        gap = self.num("gap")
        if not math.isfinite(gap) or gap > 1e-6:
            return False
        inc = self.dnum("inconclusive")
        return not (math.isfinite(inc) and inc > 0)

    def why_not_optimal(self) -> str:
        gap = self.num("gap")
        inc = self.dnum("inconclusive")
        if not math.isfinite(gap):
            return "no gap reported"
        if gap > 1e-6:
            return f"gap={gap:.2e}"
        if math.isfinite(inc) and inc > 0:
            return f"{int(inc)} inconclusive subproblem(s)"
        return "?"


def run_solver(solver: Path, method: str, instance: Path, battery: int,
               tl: int, extra: list[str] | None = None,
               workdir: Path | None = None) -> Run:
    workdir = workdir or Path(tempfile.mkdtemp(prefix="rcpsp_test_"))
    out_json = workdir / f"{instance.stem}__{method}__b{battery}.json"
    argv = [str(solver), "-i", str(instance), "-m", method, "-b", str(battery),
            "--tl", str(tl), "--thl", "1", "--ml", "8", "-o", str(out_json)]
    argv += (extra or [])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=tl + 180)
    except subprocess.TimeoutExpired:
        return Run(method, instance, battery, False, -9,
                   f"hard timeout after {tl + 180}s (the solver ignored --tl)")

    if proc.returncode != 0 or not out_json.exists():
        return Run(method, instance, battery, False, proc.returncode,
                   (proc.stderr or "")[-2000:])
    try:
        sol = json.loads(out_json.read_text())
    except ValueError as exc:
        return Run(method, instance, battery, False, proc.returncode, f"bad JSON: {exc}")
    return Run(method, instance, battery, True, 0, (proc.stderr or "")[-2000:], sol)


# ==========================================================================
# reporting
# ==========================================================================

class Report:
    def __init__(self, verbose: bool) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.verbose = verbose

    def _emit(self, status: str, name: str, detail: str) -> None:
        self.rows.append((status, name, detail))
        colour = {"PASS": "", "FAIL": "", "SKIP": "", "INFO": ""}[status]
        if status != "PASS" or self.verbose:
            print(f"  {colour}{status:4s}  {name}" + (f"  --  {detail}" if detail else ""))
        else:
            print(f"  {status:4s}  {name}")

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self._emit("PASS" if ok else "FAIL", name, detail if not ok else "")
        return ok

    def skip(self, name: str, why: str) -> None:
        self._emit("SKIP", name, why)

    def info(self, name: str, detail: str) -> None:
        self._emit("INFO", name, detail)

    @property
    def failures(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == "FAIL")

    def summary(self) -> int:
        n = {s: sum(1 for x, _, _ in self.rows if x == s)
             for s in ("PASS", "FAIL", "SKIP", "INFO")}
        print("\n" + "=" * 70)
        print(f"  {n['PASS']} passed, {n['FAIL']} failed, {n['SKIP']} skipped, "
              f"{n['INFO']} informational")
        if n["FAIL"]:
            print("\n  failures:")
            for s, name, detail in self.rows:
                if s == "FAIL":
                    print(f"    {name}: {detail}")
        print("=" * 70)
        return 1 if n["FAIL"] else 0


def close(a: float, b: float, rel: float = TOL_REL) -> bool:
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= max(TOL_ABS, rel * max(abs(a), abs(b)))


# ==========================================================================
# property checks on a single run
# ==========================================================================

def check_schedule(inst: Instance, run: Run, rep: Report, label: str) -> None:
    """T2 - the schedule is feasible for the RCPSP it claims to solve."""
    starts = run.starts
    if len(starts) != inst.n:
        rep.check(f"T2 schedule/{label}", False,
                  f"{len(starts)} start times for {inst.n} tasks")
        return

    problems: list[str] = []
    for i, t in enumerate(inst.tasks):
        s = starts[i]
        if s < t.release:
            problems.append(f"task {i} starts {s} before release {t.release}")
        if s + t.duration > inst.horizon:
            problems.append(f"task {i} ends {s + t.duration} beyond horizon {inst.horizon}")
        for j in t.successors:
            if starts[j] < s + t.duration:
                problems.append(f"precedence {i}->{j} violated ({s}+{t.duration} > {starts[j]})")
        if len(problems) > 4:
            break

    # Resource capacities, per resource per interval.
    for k, cap in enumerate(inst.capacities):
        usage = [0] * inst.horizon
        for i, t in enumerate(inst.tasks):
            if t.resources[k] <= 0:
                continue
            for u in range(starts[i], min(inst.horizon, starts[i] + t.duration)):
                usage[u] += t.resources[k]
        over = [u for u, v in enumerate(usage) if v > cap]
        if over:
            problems.append(f"resource {k} over capacity ({cap}) at t={over[:3]}")

    rep.check(f"T2 schedule/{label}", not problems, "; ".join(problems[:4]))


def block_demand(inst: Instance, run: Run) -> list[float] | None:
    """Per-interval machine energy demand implied by the reported blocks.

    Returns None when the blocks do not tile the horizon exactly once, which is
    itself the failure T5 reports.
    """
    demand = [None] * inst.horizon
    for b in run.blocks:
        desc = b["description"]
        if "->" in desc:
            a, c = (x.strip() for x in desc.split("->"))
            e = TRANSITION_COST.get((a, c))
            if e is None:
                return None
        else:
            e = {"Proc": E_PROC, "Idle": E_IDLE, "Off": E_OFF}.get(desc.strip())
            if e is None:
                return None
        for u in range(b["start_time"], b["end_time"] + 1):
            if u < 0 or u >= inst.horizon or demand[u] is not None:
                return None          # out of range, or covered twice
        for u in range(b["start_time"], b["end_time"] + 1):
            demand[u] = e
    if any(d is None for d in demand):
        return None                  # a gap
    return demand


def check_timeline(inst: Instance, run: Run, rep: Report, label: str) -> list[float] | None:
    """T5 - the machine timeline tiles the horizon and covers the EI tasks."""
    demand = block_demand(inst, run)
    if demand is None:
        rep.check(f"T5 timeline/{label}", False,
                  "machine blocks do not tile [0, H) exactly once")
        return None

    problems = []
    # State during each interval, for the Proc requirement below.
    state = [None] * inst.horizon
    for b in run.blocks:
        for u in range(b["start_time"], b["end_time"] + 1):
            state[u] = b["description"].strip()
    if state[0] != "Off":
        problems.append(f"interval 0 is {state[0]}, must be Off")
    if state[inst.horizon - 1] != "Off":
        problems.append(f"interval H-1 is {state[inst.horizon - 1]}, must be Off")

    starts = run.starts
    for i in inst.ei_ids:
        for u in range(starts[i], min(inst.horizon, starts[i] + inst.tasks[i].duration)):
            if state[u] != "Proc":
                problems.append(f"EI task {i} runs at t={u} while machine is {state[u]}")
                break
        if len(problems) > 3:
            break

    rep.check(f"T5 timeline/{label}", not problems, "; ".join(problems[:4]))
    return demand


def grid_cost(inst: Instance, demand: list[float], levels: list[float]) -> float:
    """Mirror of BatteryPostprocess::gridCost / SolverH1::computeEnergyCost."""
    total = 0.0
    h = inst.horizon
    for i in range(h):
        price = inst.prices[i]
        lvl = levels[i] if i < len(levels) else 0.0
        nxt = levels[i + 1] if (i + 1) < len(levels) and i < h - 1 else 0.0
        delta = nxt - lvl
        if delta > 0:
            total += price * (delta / ETA_C)
        from_battery = -delta * ETA_D if delta < 0 else 0.0
        from_grid = demand[i] - from_battery
        if from_grid > 0:
            total += price * from_grid
    return total


def check_costs(inst: Instance, run: Run, demand: list[float] | None,
                rep: Report, label: str, strict_energy: bool) -> None:
    """T3/T4/T6 - the reported costs are the costs of the reported schedule."""
    obj, e, t = run.objective, run.num("energy_cost"), run.num("tardiness_cost")
    rep.check(f"T3 objective=energy+tardiness/{label}", close(obj, e + t),
              f"{obj:.6f} != {e:.6f} + {t:.6f}")

    tard = 0.0
    for i, task in enumerate(inst.tasks):
        completion = run.starts[i] + task.duration - 1
        if completion > task.due:
            tard += task.weight * (completion - task.due)
    rep.check(f"T4 tardiness recomputed/{label}", close(tard, t),
              f"recomputed {tard:.6f} != reported {t:.6f}")

    if demand is None:
        rep.skip(f"T6 energy recomputed/{label}", "timeline unusable")
        return
    recomputed = grid_cost(inst, demand, run.levels)
    ok = close(recomputed, e, rel=1e-5)
    if strict_energy:
        rep.check(f"T6 energy recomputed/{label}", ok,
                  f"recomputed {recomputed:.6f} != reported {e:.6f}")
    elif not ok:
        # The MILP tracks grid purchases as their own variables, so it can in
        # principle charge and discharge in the same interval; reconstructing
        # from the level trace alone cannot see that. Never optimal, but not a
        # bug either, so it is reported rather than failed.
        rep.info(f"T6 energy recomputed/{label}",
                 f"recomputed {recomputed:.6f} vs reported {e:.6f} "
                 f"(MILP: simultaneous charge/discharge would explain this)")
    else:
        rep.check(f"T6 energy recomputed/{label}", True)


def check_battery(inst: Instance, run: Run, rep: Report, label: str) -> None:
    """T7/T8 - the battery trace respects its own bounds."""
    levels = run.levels
    cap = float(run.battery)
    problems = []
    if len(levels) != inst.horizon:
        problems.append(f"{len(levels)} levels for horizon {inst.horizon}")
    else:
        if min(levels) < -TOL_ABS:
            problems.append(f"negative level {min(levels):.6f}")
        if max(levels) > cap + TOL_ABS:
            problems.append(f"level {max(levels):.6f} above capacity {cap}")
        if abs(levels[0]) > TOL_ABS:
            problems.append(f"starts at {levels[0]:.6f}, must be 0")
        # Config::batteryTerminalEmpty defaults to true, so both the MILP and
        # the LP now end empty. If this fires, either the flag was flipped or
        # the constraint did not make it into the model.
        if abs(levels[-1]) > TOL_ABS:
            problems.append(f"ends at {levels[-1]:.6f}, must be 0 "
                            f"(unless --battery-free-end)")
    rep.check(f"T7 battery bounds/{label}", not problems, "; ".join(problems))

    if cap == 0:
        zero = all(abs(x) <= TOL_ABS for x in levels)
        rep.check(f"T8 no battery => flat trace/{label}", zero,
                  "battery capacity is 0 but the level trace is not")


def check_bound(run: Run, rep: Report, label: str) -> None:
    """T12 - the dual bound bounds, and only claims what it can."""
    bound = run.dnum("bound")
    if not math.isfinite(bound):
        rep.skip(f"T12 bound/{label}", "method exports no bound")
        return
    rep.check(f"T12 bound <= objective/{label}",
              bound <= run.objective + max(TOL_ABS, TOL_REL * abs(run.objective)),
              f"bound {bound:.6f} > objective {run.objective:.6f}")

    aware = run.dnum("bound_is_battery_aware")
    expected = 1.0 if run.method == "Benders" else 0.0
    rep.check(f"T12 bound flagged correctly/{label}", close(aware, expected, rel=0),
              f"{run.method} reports battery_aware={aware}, expected {expected}")


# ==========================================================================
# cross-method oracles
# ==========================================================================

def compare_optima(runs: dict[str, Run], rep: Report, label: str,
                   members: list[str], name: str) -> None:
    """All listed methods are exact here, so they must agree."""
    usable = {m: r for m, r in runs.items()
              if m in members and r.ok and r.proved_optimal}
    if len(usable) < 2:
        missing = [m for m in members if m not in usable]
        why = ", ".join(f"{m}: {runs[m].why_not_optimal() if m in runs else 'not run'}"
                        for m in missing)
        rep.skip(f"{name}/{label}", f"fewer than two proved optimal ({why})")
        return

    ref_m, ref = next(iter(usable.items()))
    bad = [f"{m}={r.objective:.6f}" for m, r in usable.items()
           if not close(r.objective, ref.objective, rel=TOL_OPT_REL)]
    rep.check(f"{name}/{label}", not bad,
              f"{ref_m}={ref.objective:.6f} but " + ", ".join(bad))


def compare_no_better_than_exact(runs: dict[str, Run], rep: Report, label: str,
                                 heuristics: list[str]) -> None:
    """T10 - a post-processing method must not beat the exact one.

    This is the check that would have caught the terminal-battery-level bug:
    an accounting difference shows up here as a heuristic reporting a lower
    cost than the method that is provably optimal.
    """
    exact = runs.get("MILP")
    if not exact or not exact.ok or not exact.proved_optimal:
        rep.skip(f"T10 heuristic >= exact/{label}",
                 f"MILP not proved optimal ({exact.why_not_optimal() if exact else 'not run'})")
        return
    bad = []
    for m in heuristics:
        r = runs.get(m)
        if not r or not r.ok:
            continue
        if r.objective < exact.objective - max(TOL_ABS, TOL_OPT_REL * abs(exact.objective)):
            bad.append(f"{m}={r.objective:.6f} < MILP={exact.objective:.6f}")
    rep.check(f"T10 heuristic >= exact/{label}", not bad,
              "; ".join(bad) + "  (an accounting bug, not a better schedule)")


def check_refiner(runs: dict[str, Run], rep: Report, label: str) -> None:
    """T13 - is the conflict refiner producing smaller cuts than no-good?"""
    lbbd, ngc = runs.get("LBBD"), runs.get("NoGoodCuts")
    if not (lbbd and ngc and lbbd.ok and ngc.ok):
        rep.skip(f"T13 conflict refiner/{label}", "need both LBBD and NoGoodCuts")
        return

    def mean_mis(r: Run) -> float:
        cuts = r.dnum("feasibility_cuts")
        return r.dnum("cumul_mifs") / cuts if math.isfinite(cuts) and cuts > 0 else float("nan")

    a, b = mean_mis(lbbd), mean_mis(ngc)
    if not (math.isfinite(a) and math.isfinite(b)):
        rep.info(f"T13 conflict refiner/{label}",
                 "no feasibility cuts were generated -- this instance never made the "
                 "master propose an infeasible EI placement, so the refiner is untested")
        return
    rep.check(f"T13 conflict refiner/{label}", a <= b + TOL_ABS,
              f"mean infeasibility set: LBBD={a:.2f} > NoGoodCuts={b:.2f}; "
              f"the refiner is producing LARGER cuts than plain no-good")


def check_flat_tariff(solver: Path, inst: Instance, src: Path, tl: int,
                      battery: int, rep: Report, workdir: Path) -> None:
    """T11 - under a constant price, Benders must equal StateLBBD.

    The sharpest available test of the battery cut. With no price variation
    there is nothing to arbitrage, so coordinating the battery with the
    schedule cannot possibly help, and the two arms differ only in whether they
    do that coordination. Any gap between them is the Benders cut inventing
    value that does not exist.
    """
    flat = write_instance(inst, workdir / f"{src.stem}__flat.txt",
                          prices=[100.0] * inst.horizon)
    runs = {m: run_solver(solver, m, flat, battery, tl, workdir=workdir)
            for m in ("StateLBBD", "Benders")}

    for m, r in runs.items():
        if not r.ok:
            rep.check(f"T11 flat tariff/{m}", False, f"run failed: {r.stderr[:200]}")
            return

    a, b = runs["Benders"], runs["StateLBBD"]
    if not (a.proved_optimal and b.proved_optimal):
        rep.skip("T11 flat tariff: Benders == StateLBBD",
                 f"Benders {a.why_not_optimal()}, StateLBBD {b.why_not_optimal()}")
    else:
        rep.check("T11 flat tariff: Benders == StateLBBD",
                  close(a.objective, b.objective, rel=TOL_OPT_REL),
                  f"Benders={a.objective:.6f} vs StateLBBD={b.objective:.6f}; "
                  f"the battery cut is creating value a flat tariff cannot contain")

    # And storage itself must be worthless here.
    for m, r in runs.items():
        saving = r.dnum("battery_saving")
        if math.isfinite(saving):
            rep.check(f"T11 flat tariff: no storage value/{m}", abs(saving) <= 1e-3,
                      f"battery saved {saving:.6f} under a constant price")


def check_determinism(solver: Path, method: str, instance: Path, battery: int,
                      tl: int, rep: Report, workdir: Path) -> None:
    """T14 - two identical invocations agree.

    None of these methods is stochastic. A difference here means Gurobi's
    thread scheduling is leaking into the answer, which would make every paired
    comparison in E8/E9 noisier than the effects being measured.
    """
    a = run_solver(solver, method, instance, battery, tl, workdir=workdir)
    b = run_solver(solver, method, instance, battery, tl, workdir=workdir)
    if not (a.ok and b.ok):
        rep.skip(f"T14 determinism/{method}", "a run failed")
        return
    rep.check(f"T14 determinism/{method}",
              close(a.objective, b.objective, rel=TOL_OPT_REL),
              f"{a.objective:.6f} vs {b.objective:.6f} on identical input")


# ==========================================================================
# driver
# ==========================================================================

def battery_size(inst: Instance) -> int:
    """A battery worth having: roughly one day of EI machine demand.

    Mirrors experiments/config/design.py's BATTERY_ON_RATIO = 1.0 on e_day.
    Too small and the storage tests measure nothing; too large and the battery
    is never the binding constraint.
    """
    ei_duration = sum(inst.tasks[i].duration for i in inst.ei_ids)
    days = max(1.0, inst.horizon / 24.0)
    return max(1, round(E_PROC * ei_duration / days))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parents[1]
    ap.add_argument("--solver", type=Path, default=root / "build" / "rcpsp_wt_battery")
    ap.add_argument("--instances", type=Path, nargs="+",
                    default=[root / "instances" / "1_1.txt"])
    ap.add_argument("--tl", type=int, default=300,
                    help="time limit per run, seconds (default 300)")
    ap.add_argument("--quick", action="store_true",
                    help="skip the MILP oracle runs (much faster, much weaker)")
    ap.add_argument("--no-flat", action="store_true", help="skip T11")
    ap.add_argument("--no-determinism", action="store_true", help="skip T14")
    ap.add_argument("--keep", action="store_true", help="keep the JSON output")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not args.solver.exists():
        print(f"FATAL: solver not found at {args.solver}\n"
              f"       build it first:\n"
              f"         cmake -S . -B build -DCMAKE_BUILD_TYPE=Release "
              f"-DWITH_CPOPTIMIZER=ON\n"
              f"         cmake --build build -j", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="rcpsp_decomp_test_"))
    rep = Report(args.verbose)
    methods = list(DECOMP_METHODS) if args.quick else list(ALL_METHODS)

    print(f"solver     {args.solver}")
    print(f"methods    {', '.join(methods)}")
    print(f"time limit {args.tl}s per run")
    print(f"scratch    {workdir}\n")

    for src in args.instances:
        if not src.exists():
            rep.check(f"instance {src.name}", False, "file not found")
            continue
        inst = read_instance(src)
        cap = battery_size(inst)
        print(f"\n{'=' * 70}\n{src.name}: {inst.n} tasks, {len(inst.ei_ids)} EI, "
              f"horizon {inst.horizon}, battery {cap}\n{'=' * 70}")

        for battery in (0, cap):
            tag = "b0" if battery == 0 else "bON"
            print(f"\n-- battery {battery} --")
            runs: dict[str, Run] = {}
            for m in methods:
                r = run_solver(args.solver, m, src, battery, args.tl, workdir=workdir)
                runs[m] = r
                label = f"{m}/{tag}"
                if not rep.check(f"T1 runs/{label}", r.ok,
                                 f"rc={r.returncode} {r.stderr[:300]}"):
                    continue

                check_schedule(inst, r, rep, label)
                demand = check_timeline(inst, r, rep, label)
                check_costs(inst, r, demand, rep, label,
                            strict_energy=(m != "MILP"))
                check_battery(inst, r, rep, label)
                if m in DECOMP_METHODS:
                    check_bound(r, rep, label)

            # ---- cross-method oracles ---------------------------------
            if battery == 0:
                # No battery means the battery-free problem IS the problem, and
                # every method here is exact for it.
                compare_optima(runs, rep, tag, methods,
                               "T9 all methods agree (no battery)")
            else:
                # With storage only MILP and Benders remain exact.
                compare_optima(runs, rep, tag, ["MILP", "Benders"],
                               "T10 Benders == MILP (exact with storage)")
                compare_no_better_than_exact(runs, rep, tag, ["LBBD", "StateLBBD",
                                                              "NoGoodCuts"])
            check_refiner(runs, rep, tag)

        if not args.no_flat:
            print("\n-- flat tariff --")
            check_flat_tariff(args.solver, inst, src, args.tl, cap, rep, workdir)

        if not args.no_determinism:
            print("\n-- determinism --")
            check_determinism(args.solver, "Benders", src, cap, args.tl, rep, workdir)

    code = rep.summary()
    if args.keep:
        print(f"\noutput kept in {workdir}")
    else:
        print(f"\n(scratch dir {workdir} -- pass --keep to inspect the JSON)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
