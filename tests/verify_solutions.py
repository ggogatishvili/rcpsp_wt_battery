#!/usr/bin/env python3
"""
Independent verification of the schedules the decompositions return.

Written because StateLBBD -- an arm built only as an experimental control,
which uses neither the SPACES pre-processing nor the integrated battery --
came out ahead of both proposed methods. A control that wins is the result
most likely to be a bug, and it deserves more scrutiny than the methods it
beat, not less.

    python3 tests/verify_solutions.py                       # default sweep
    python3 tests/verify_solutions.py --sizes 8 12 16 --per-size 5
    python3 tests/verify_solutions.py --instances instances/16_4.txt
    python3 tests/verify_solutions.py --from-dir campaign_json/   # no solving

Nothing here trusts the solver. Every quantity is recomputed from the raw
instance file and the returned schedule, and compared against what the solver
reported. The machine profile constants mirror MachineProfile's defaults in
include/instance.h; if you change those, change these, and the energy checks
will start failing until you do -- which is the intended behaviour.

THE CHECK THAT MATTERS MOST
---------------------------
V15. On an instance where the MILP proved optimality, no method may report a
cost below that optimum. If StateLBBD or Benders ever does, its schedule is
infeasible or its cost accounting is wrong, and every comparison in the
campaign is void. Everything else here is a way of localising such a failure.

The converse is NOT a bug for the post-processing arms: LBBD and StateLBBD are
exact for the battery-FREE problem, so reporting a cost strictly above a proven
battery-aware optimum is expected, and V15 measures that gap rather than
flagging it. For Benders it IS a bug, because its bound is battery-aware.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# machine profile -- mirrors MachineProfile defaults in include/instance.h
# --------------------------------------------------------------------------
E_PROC, E_IDLE, E_OFF = 4.0, 2.0, 0.0
TRANSITION = {                      # (from, to) -> (intervals, energy/interval)
    ("Off", "Proc"):  (2, 5.0),
    ("Proc", "Off"):  (1, 1.0),
    ("Proc", "Idle"): (1, 2.0),
    ("Idle", "Proc"): (1, 2.5),
}
STATE_E = {"Proc": E_PROC, "Idle": E_IDLE, "Off": E_OFF}
ETA_C = ETA_D = 0.95

ABS_TOL = 1e-4
REL_TOL = 1e-6
# Two solvers that both closed their gap can still stop a little apart: Gurobi's
# default MIPGap is 1e-4. Cross-method comparisons use this looser bound so the
# check flags real errors rather than termination tolerances.
OPT_REL_TOL = 2e-3

DEFAULT_ARMS = ["MILP", "LBBD", "StateLBBD", "Benders"]


def close(a: float, b: float, rel: float = REL_TOL) -> bool:
    return math.isfinite(a) and math.isfinite(b) and \
        abs(a - b) <= max(ABS_TOL, rel * max(abs(a), abs(b)))


# ==========================================================================
# instance
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
    path: Path

    @property
    def n(self) -> int:
        return len(self.tasks)

    @property
    def horizon(self) -> int:
        return len(self.prices)


def read_instance(path: Path) -> Instance:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    n, m = (int(x) for x in lines[0].split()[:2])
    caps = [int(x) for x in lines[1].split()[:m]]
    tasks = []
    for row in lines[2:2 + n]:
        f = row.split()
        p = 0
        dur = int(f[p]); p += 1
        res = [int(f[p + k]) for k in range(m)]; p += m
        ns = int(f[p]); p += 1
        succ = [int(f[p + k]) for k in range(ns)]; p += ns
        rel = int(f[p]); p += 1
        due = int(f[p]); p += 1
        tasks.append(Task(dur, res, succ, rel, due, float(f[p])))
    return Instance(caps, tasks, [float(x) for x in lines[2 + n].split()], path)


def battery_size(inst: Instance) -> int:
    ei = sum(t.duration for t in inst.tasks if t.is_ei)
    return max(1, round(E_PROC * ei / max(1.0, inst.horizon / 24.0)))


# ==========================================================================
# report
# ==========================================================================

@dataclass
class Report:
    passed: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)
    detail: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, name: str, ok: bool, msg: str = "") -> bool:
        with self.lock:
            if ok:
                self.passed += 1
            else:
                self.failed += 1
                self.detail[name].append(msg)
            return ok

    def note(self, msg: str) -> None:
        with self.lock:
            self.notes.append(msg)


# ==========================================================================
# the checks
# ==========================================================================

def timeline(inst: Instance, sol: dict):
    """Per-interval (state, demand) implied by the reported machine blocks.

    Returns (demand, states, problem). `problem` is non-empty when the blocks
    fail to tile [0, h) exactly once, which makes every downstream energy
    number meaningless -- so it is reported rather than worked around.
    """
    h = inst.horizon
    demand: list[float | None] = [None] * h
    state: list[str | None] = [None] * h
    blocks = sol.get("machine_blocks", [])
    if not blocks:
        return None, None, "no machine blocks reported"

    for b in blocks:
        desc = str(b.get("description", "")).strip()
        if "->" in desc:
            a, c = (x.strip() for x in desc.split("->"))
            spec = TRANSITION.get((a, c))
            if spec is None:
                return None, None, f"illegal transition '{desc}'"
            dur, e = spec
            if b["end_time"] - b["start_time"] + 1 != dur:
                return None, None, (f"transition '{desc}' spans "
                                    f"{b['end_time'] - b['start_time'] + 1} "
                                    f"intervals, profile says {dur}")
            label = desc
        else:
            e = STATE_E.get(desc)
            if e is None:
                return None, None, f"unknown state '{desc}'"
            label = desc
        for u in range(b["start_time"], b["end_time"] + 1):
            if u < 0 or u >= h:
                return None, None, f"block '{desc}' runs outside [0,{h})"
            if demand[u] is not None:
                return None, None, f"interval {u} covered twice"
            demand[u], state[u] = e, label
    if any(d is None for d in demand):
        miss = [u for u, d in enumerate(demand) if d is None]
        return None, None, f"{len(miss)} interval(s) uncovered, first {miss[0]}"

    # Adjacent blocks must chain: the state a transition ends in is the state
    # the next block starts from.
    ordered = sorted(blocks, key=lambda b: b["start_time"])
    for x, y in zip(ordered, ordered[1:]):
        dx, dy = str(x["description"]).strip(), str(y["description"]).strip()
        left = dx.split("->")[-1].strip() if "->" in dx else dx
        right = dy.split("->")[0].strip() if "->" in dy else dy
        if left != right:
            return None, None, f"blocks do not chain: '{dx}' then '{dy}'"
    return demand, state, ""


def verify_one(inst: Instance, arm: str, sol: dict, battery: int, rep: Report):
    tag = f"{inst.path.name}/{arm}"
    starts = [t["start_time"] for t in sol.get("task_assignments", [])]
    info = sol.get("solution_info", {}) or {}
    diag = sol.get("diagnostics", {}) or {}
    h = inst.horizon

    def num(d, k):
        v = d.get(k)
        return float("nan") if v is None else float(v)

    if len(starts) != inst.n:
        rep.check("V0 schedule present", False,
                  f"{tag}: {len(starts)} starts for {inst.n} tasks")
        return
    rep.check("V0 schedule present", True)

    # ---- V1..V4  the schedule is a feasible RCPSP solution ----------------
    bad = [j for j in range(inst.n) if starts[j] < inst.tasks[j].release]
    rep.check("V1 release dates", not bad, f"{tag}: tasks {bad[:3]}")

    bad = [(u, v) for u in range(inst.n) for v in inst.tasks[u].successors
           if starts[v] < starts[u] + inst.tasks[u].duration]
    rep.check("V2 precedences", not bad, f"{tag}: {bad[:3]}")

    bad = [j for j in range(inst.n)
           if starts[j] + inst.tasks[j].duration - 1 >= h or starts[j] < 0]
    rep.check("V3 horizon", not bad, f"{tag}: tasks {bad[:3]}")

    over = []
    for k, cap in enumerate(inst.capacities):
        usage = [0] * h
        for j, t in enumerate(inst.tasks):
            if t.resources[k] == 0:
                continue
            for u in range(starts[j], min(h, starts[j] + t.duration)):
                usage[u] += t.resources[k]
        hot = [u for u, v in enumerate(usage) if v > cap]
        if hot:
            over.append(f"r{k} over {cap} at t={hot[:3]}")
    rep.check("V4 resource capacities", not over, f"{tag}: {'; '.join(over)}")

    # ---- V5..V7  the machine timeline ------------------------------------
    demand, state, problem = timeline(inst, sol)
    if not rep.check("V5 timeline tiles horizon", not problem, f"{tag}: {problem}"):
        return

    bad = []
    for j, t in enumerate(inst.tasks):
        if not t.is_ei:
            continue
        for u in range(starts[j], starts[j] + t.duration):
            if state[u] != "Proc":
                bad.append((j, u, state[u]))
                break
    rep.check("V6 Proc while EI runs", not bad, f"{tag}: {bad[:3]}")

    rep.check("V7 Off at both ends",
              state[0] == "Off" and state[h - 1] == "Off",
              f"{tag}: starts '{state[0]}', ends '{state[h - 1]}'")

    # ---- V8..V10  energy and battery -------------------------------------
    raw = sum(inst.prices[u] * demand[u] for u in range(h))
    levels = [0.0 if x is None else float(x) for x in sol.get("battery_levels", [])]

    if battery > 0 and len(levels) == h:
        bad = [u for u, L in enumerate(levels) if L < -ABS_TOL or L > battery + ABS_TOL]
        rep.check("V8 battery within capacity", not bad,
                  f"{tag}: t={bad[:3]}, cap {battery}")
        rep.check("V9 battery starts empty", close(levels[0], 0.0),
                  f"{tag}: level[0]={levels[0]:.4f}")
        # The terminal level is a modelling convention, not a law: the MILP
        # forces it to zero and BatteryLp follows only when
        # Config::batteryTerminalEmpty is set. Report, do not fail.
        if levels[-1] > ABS_TOL:
            rep.note(f"{tag}: ends with {levels[-1]:.2f} stored "
                     f"(terminal-empty convention off?)")
    else:
        rep.check("V8 battery within capacity", True)
        rep.check("V9 battery starts empty", True)

    e_no_batt = num(diag, "energy_cost_no_battery")
    reported_e = num(info, "energy_cost")

    if battery == 0:
        rep.check("V10 energy cost recomputes", close(reported_e, raw, 1e-4),
                  f"{tag}: reported {reported_e:.4f}, recomputed {raw:.4f}")
    else:
        # With storage the grid purchase is not the raw bill, and reproducing
        # it requires re-solving the LP. What can be checked without doing so
        # is the direction: storage cannot make the bill worse, because buying
        # everything from the grid is always feasible for that LP.
        rep.check("V10 storage never costs more",
                  not math.isfinite(e_no_batt) or reported_e <= e_no_batt + ABS_TOL,
                  f"{tag}: with battery {reported_e:.2f} > without {e_no_batt:.2f}")
        if math.isfinite(e_no_batt):
            rep.check("V10b no-battery cost recomputes", close(e_no_batt, raw, 1e-4),
                      f"{tag}: reported {e_no_batt:.4f}, recomputed {raw:.4f}")

    # ---- V11..V13  the objective -----------------------------------------
    twt = sum(t.weight * max(0.0, starts[j] + t.duration - 1 - t.due)
              for j, t in enumerate(inst.tasks))
    reported_t = num(info, "tardiness_cost")
    rep.check("V11 tardiness recomputes", close(reported_t, twt, 1e-6),
              f"{tag}: reported {reported_t:.4f}, recomputed {twt:.4f}")

    obj = num(info, "objective_value")
    rep.check("V12 objective is energy + tardiness",
              close(obj, reported_e + reported_t, 1e-6),
              f"{tag}: {obj:.4f} vs {reported_e:.4f}+{reported_t:.4f}")

    # ---- V14  the bound is consistent with what it bounds -----------------
    bound = num(diag, "bound")
    aware = num(diag, "bound_is_battery_aware")
    if math.isfinite(bound):
        if aware == 1:
            ok = bound <= obj + max(ABS_TOL, 1e-4 * abs(obj))
            rep.check("V14 battery-aware bound <= objective", ok,
                      f"{tag}: bound {bound:.4f} > objective {obj:.4f}")
        else:
            # A battery-free bound bounds the battery-free cost, which is the
            # raw energy bill plus tardiness -- not the post-processed cost.
            ceiling = (e_no_batt if math.isfinite(e_no_batt) else raw) + reported_t
            ok = bound <= ceiling + max(ABS_TOL, 1e-4 * abs(ceiling))
            rep.check("V14 battery-free bound <= battery-free cost", ok,
                      f"{tag}: bound {bound:.4f} > battery-free {ceiling:.4f}")


def explain(inst: Instance, sols: dict[str, dict], battery: int) -> None:
    """Decompose one instance's costs, to separate Proposition 4 from a bug.

    When a post-processing arm certifies optimality and is still beaten, there
    are exactly two explanations and they are distinguishable:

      Proposition 4.  The arm found the battery-FREE optimum, and the winner
      found a schedule that is battery-free WORSE but responds better to
      storage. Then raw(arm) <= raw(winner), and the whole difference sits in
      the battery saving. This is correct behaviour and is the paper's point.

      A bug.  The arm did not find the battery-free optimum after all, i.e.
      raw(arm) > raw(winner). Then its exactness claim is false and the cuts,
      the master, or the post-processing write-back are wrong.

    The raw column is recomputed here from the machine blocks and the tariff,
    so it does not depend on the solver agreeing with itself.
    """
    print(f"\n{'=' * 84}")
    print(f"COST DECOMPOSITION  --  {inst.path.name}")
    print(f"{'=' * 84}")
    print(f"  horizon {inst.horizon}, battery {battery}, "
          f"{sum(1 for t in inst.tasks if t.is_ei)} EI task(s), "
          f"prices {min(inst.prices):.1f}..{max(inst.prices):.1f}")
    print(f"\n  {'arm':11s} {'raw energy':>12s} {'with battery':>13s} "
          f"{'saving':>9s} {'tardiness':>10s} {'objective':>12s} {'proved':>7s}")

    table = {}
    for arm, sol in sorted(sols.items()):
        info = sol.get("solution_info", {}) or {}
        diag = sol.get("diagnostics", {}) or {}
        demand, _state, problem = timeline(inst, sol)
        raw = (sum(inst.prices[u] * demand[u] for u in range(inst.horizon))
               if demand else float("nan"))
        e = float(info.get("energy_cost", float("nan")))
        t = float(info.get("tardiness_cost", float("nan")))
        o = float(info.get("objective_value", float("nan")))
        gap = info.get("gap")
        inc = diag.get("inconclusive")
        proved = (gap is not None and float(gap) <= 1e-6
                  and not (inc is not None and float(inc) > 0))
        table[arm] = (raw, e, o, proved)
        note = "" if not problem else f"  <- {problem}"
        print(f"  {arm:11s} {raw:12.2f} {e:13.2f} {raw - e:9.2f} "
              f"{t:10.2f} {o:12.2f} {str(proved):>7s}{note}")

    winner = min((v[2], k) for k, v in table.items() if math.isfinite(v[2]))[1]
    print(f"\n  best objective: {winner}")
    for arm, (raw, _e, o, proved) in sorted(table.items()):
        if arm == winner or not proved or not math.isfinite(raw):
            continue
        rawv = table[winner][0]
        if not math.isfinite(rawv):
            continue
        if raw <= rawv + ABS_TOL:
            print(f"  {arm}: raw {raw:.2f} <= {winner}'s raw {rawv:.2f}. It DID "
                  f"find the battery-free\n      optimum; the entire "
                  f"{o - table[winner][2]:.2f} gap is battery coordination. "
                  f"Proposition 4, not a bug.")
        else:
            print(f"  {arm}: raw {raw:.2f} > {winner}'s raw {rawv:.2f}. It certifies "
                  f"optimality of the\n      battery-free problem but is NOT "
                  f"battery-free optimal. THIS IS A BUG.")


def cross_check(results: dict, rep: Report):
    """V15 -- the check the whole script exists for.

    On an instance the MILP proved, nobody may be cheaper than the optimum.
    Beating a proven optimum is not a good result; it is a wrong one.
    """
    for inst_name, per_arm in sorted(results.items()):
        milp = per_arm.get("MILP")
        if not milp or not milp["proved"] or not math.isfinite(milp["objective"]):
            continue
        opt = milp["objective"]
        for arm, r in sorted(per_arm.items()):
            if arm == "MILP" or not math.isfinite(r["objective"]):
                continue
            tol = max(ABS_TOL, OPT_REL_TOL * abs(opt))
            rep.check("V15 nobody beats a proven optimum",
                      r["objective"] >= opt - tol,
                      f"{inst_name}/{arm}: {r['objective']:.4f} < proven "
                      f"optimum {opt:.4f} by {opt - r['objective']:.4f}")

            if r["proved"]:
                if arm == "Benders":
                    # Benders' bound is battery-aware, so a certified Benders
                    # answer must BE the optimum, not merely not beat it.
                    rep.check("V16 certified Benders equals the optimum",
                              close(r["objective"], opt, OPT_REL_TOL),
                              f"{inst_name}: Benders certified {r['objective']:.4f} "
                              f"but optimum is {opt:.4f}")
                elif r["objective"] > opt + tol:
                    # Expected, and the whole point of Proposition 4: these
                    # arms certify the battery-free problem. Quantify it.
                    rep.note(f"{inst_name}/{arm}: certifies optimality at "
                             f"{r['objective']:.2f}, true optimum {opt:.2f} "
                             f"(+{100 * (r['objective'] - opt) / abs(opt):.2f} %)")


# ==========================================================================
# running
# ==========================================================================

def run(solver: Path, arm: str, inst: Instance, battery: int, tl: int,
        mem: int, workdir: Path, keep: Path | None):
    out = workdir / f"{inst.path.stem}__{arm}.json"
    argv = [str(solver), "-i", str(inst.path), "-m", arm, "-b", str(battery),
            "--tl", str(tl), "--thl", "1", "--ml", str(mem), "-o", str(out)]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=tl + 300)
    except subprocess.TimeoutExpired:
        return None, f"hard timeout at {tl + 300}s"
    if p.returncode != 0 or not out.exists():
        return None, (p.stderr or "").strip()[-200:] or f"exit {p.returncode}"
    try:
        sol = json.loads(out.read_text())
    except ValueError as exc:
        return None, f"bad JSON: {exc}"
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
        shutil.copy(out, keep / out.name)
    return sol, ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parents[1]
    ap.add_argument("--solver", type=Path, default=root / "build" / "rcpsp_wt_battery")
    ap.add_argument("--instances", type=Path, nargs="+")
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3, 4, 8, 12, 16])
    ap.add_argument("--per-size", type=int, default=4)
    ap.add_argument("--arms", nargs="+", default=DEFAULT_ARMS)
    ap.add_argument("--tl", type=int, default=600)
    ap.add_argument("--mem", type=int, default=4)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--battery-ratio", type=float, default=1.0)
    ap.add_argument("--keep-json", type=Path,
                    help="copy every solution JSON here for later re-checking")
    ap.add_argument("--from-dir", type=Path,
                    help="verify saved JSONs instead of running the solver; "
                         "files must be named <instance>__<arm>.json")
    ap.add_argument("--explain", action="store_true",
                    help="print a raw/battery/tardiness cost decomposition per "
                         "instance, which separates Proposition 4 from a bug")
    args = ap.parse_args()

    if args.instances:
        paths = args.instances
    else:
        paths = []
        for c in args.sizes:
            found = sorted((root / "instances").glob(f"{c}_*.txt"),
                           key=lambda q: int(q.stem.split("_")[1]))
            paths.extend(found[:args.per_size])
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("FATAL: no instances", file=sys.stderr)
        return 2

    rep = Report()
    results: dict[str, dict[str, dict]] = defaultdict(dict)
    kept: dict[str, tuple] = {}
    workdir = Path(tempfile.mkdtemp(prefix="rcpsp_verify_"))
    lock = threading.Lock()
    done = [0]
    total = len(paths) * len(args.arms)

    def job(path: Path, arm: str):
        inst = read_instance(path)
        batt = round(args.battery_ratio * battery_size(inst))
        if args.from_dir:
            f = args.from_dir / f"{path.stem}__{arm}.json"
            if not f.exists():
                return
            sol, err = json.loads(f.read_text()), ""
        else:
            sol, err = run(args.solver, arm, inst, batt, args.tl, args.mem,
                           workdir, args.keep_json)
        with lock:
            done[0] += 1
            head = f"  [{done[0]:3d}/{total}] {path.name:10s} {arm:10s}"
        if sol is None:
            rep.check("V0 schedule present", False, f"{path.name}/{arm}: {err}")
            print(head + "  FAILED " + err[:40], flush=True)
            return
        info = sol.get("solution_info", {}) or {}
        diag = sol.get("diagnostics", {}) or {}
        gap = info.get("gap")
        inc = diag.get("inconclusive")
        proved = (gap is not None and float(gap) <= 1e-6
                  and not (inc is not None and float(inc) > 0))
        obj = info.get("objective_value")
        with lock:
            results[path.name][arm] = {
                "objective": float("nan") if obj is None else float(obj),
                "proved": proved}
            kept.setdefault(path.name, (inst, batt, {}))[2][arm] = sol
        verify_one(inst, arm, sol, batt, rep)
        print(head + f"  obj={float(obj):13.2f}" if obj is not None else head,
              flush=True)

    print(f"verifying {len(paths)} instance(s) x {len(args.arms)} arm(s)"
          f"{' from ' + str(args.from_dir) if args.from_dir else ''}\n")
    with ThreadPoolExecutor(max_workers=args.workers) as pex:
        for path in paths:
            for arm in args.arms:
                pex.submit(job, path, arm)

    if args.explain:
        for name in sorted(kept):
            inst, batt, sols = kept[name]
            explain(inst, sols, batt)

    cross_check(results, rep)
    shutil.rmtree(workdir, ignore_errors=True)

    print("\n" + "=" * 84)
    print(f"{rep.passed} checks passed, {rep.failed} failed")
    print("=" * 84)
    for name, msgs in sorted(rep.detail.items()):
        print(f"\nFAILED  {name}   ({len(msgs)} occurrence(s))")
        for m in msgs[:6]:
            print(f"    {m}")
        if len(msgs) > 6:
            print(f"    ... and {len(msgs) - 6} more")
    if rep.notes:
        print(f"\nINFORMATIONAL ({len(rep.notes)}):")
        for m in rep.notes[:15]:
            print(f"    {m}")
        if len(rep.notes) > 15:
            print(f"    ... and {len(rep.notes) - 15} more")
    if not rep.failed:
        print("\nNo check failed. The schedules are feasible, the costs "
              "recompute, and\nnobody beat a proven optimum.")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
