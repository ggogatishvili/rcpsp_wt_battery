#!/usr/bin/env python3
"""
Stage 0 — preflight. Run this before anything else on a new machine.

Checks, in order of how expensive the mistake is if you skip it:

  1. solver binary exists, is executable, and reports a version
  2. Gurobi is reachable and can create+solve a trivial model  (every single
     run needs this; discovering it after 40 hours is the worst outcome here)
  3. the solver actually completes one real instance end-to-end and produces a
     JSON with the fields 04_collect.py expects
  4. python version and numpy
  5. cores, RAM and free disk against the configured design
  6. PSPLIB source structures present for the configured size classes

Exit code is non-zero if any hard check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design                      # noqa: E402

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
ORIG = ROOT.parent / "instance_generator" / "instances_original"

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
_hard_fail = False


def check_pipeline_imports() -> None:
    """Import every module the pipeline will need, now, before anything runs.

    THE FAILURE THIS EXISTS FOR. A pilot ran for one hour and fifty-four
    minutes, completed all 5,704 solver runs, collected them, and then died at
    stage 5 of 5 with

        ImportError: cannot import name 'managerial' from 'analysis'
                     (unknown location)

    because two analysis modules had never reached the compute server: they were
    untracked in git, so `git push` left them behind. Every ingredient of that
    failure was knowable in two seconds at stage 0.

    The check is deliberately an IMPORT and not an `os.path.exists`. A file that
    is present but has a syntax error, a missing third-party dependency, or a
    circular import fails here too, which is the same class of "the analysis
    cannot run" problem and deserves the same early stop.

    Adding a module to the pipeline means adding it here. That is the point:
    the list is the manifest of what a working checkout contains.
    """
    import importlib

    REQUIRED = [
        ("config.design", "the design of record"),
        ("config.machines", "machine archetypes (M1)"),
        ("config.economics", "NPV parameters (M1)"),
        ("lib.prices", "tariff library"),
        ("lib.generate", "instance generation"),
        ("lib.rcpsp_io", "instance file format"),
        ("lib.rng", "deterministic substreams"),
        ("analysis.analyses", "campaign-v1 analyses, and shared helpers"),
        ("analysis.managerial", "campaign-v2 analyses M0-M5"),
        ("analysis.replication", "seed-replication analysis MR"),
    ]
    missing = []
    for mod, why in REQUIRED:
        try:
            importlib.import_module(mod)
        except Exception as exc:                     # noqa: BLE001 - report anything
            missing.append((mod, why, f"{type(exc).__name__}: {exc}"))

    if not missing:
        report(OK, "pipeline imports", f"all {len(REQUIRED)} modules import")
        return

    for mod, why, err in missing:
        report(FAIL, f"import {mod}", f"{why} -- {err}")
    # The most common cause by far, named explicitly so nobody has to rediscover
    # it. Untracked files do not travel with git.
    names = ", ".join(m for m, _, _ in missing)
    report(FAIL, "pipeline imports",
           f"{len(missing)} module(s) unusable: {names}. If this checkout came "
           f"from git, check `git status --porcelain | grep '^??'` on the "
           f"machine you pushed FROM -- untracked files are not pushed. "
           f"Do not start a campaign until this passes: the analysis runs last "
           f"and will fail after every solver hour has been spent.")


def report(status: str, name: str, detail: str = "") -> None:
    global _hard_fail
    if status == FAIL:
        _hard_fail = True
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", type=Path,
                    default=ROOT.parent / "build" / "rcpsp_wt_battery")
    ap.add_argument("--skip-solve", action="store_true")
    args = ap.parse_args()

    print(f"preflight — profile '{design.PROFILE}', data root {DATA}\n")

    # 0 pipeline imports ---------------------------------------------------
    # First, and cheapest. Everything below can pass on a checkout whose
    # analysis modules never arrived, and the campaign would then run to
    # completion before discovering it.
    check_pipeline_imports()

    # 1 solver ------------------------------------------------------------
    s = args.solver
    if not s.exists():
        report(FAIL, "solver binary", f"{s} not found — build the project first")
    elif not os.access(s, os.X_OK):
        report(FAIL, "solver binary", f"{s} is not executable")
    else:
        try:
            v = subprocess.run([str(s), "--version"], capture_output=True,
                               text=True, timeout=60)
            report(OK, "solver binary", (v.stdout or v.stderr).strip()[:60])
        except Exception as exc:
            report(FAIL, "solver binary", f"could not run --version: {exc}")

    # 2 gurobi ------------------------------------------------------------
    try:
        import gurobipy as gp
        m = gp.Model("preflight")
        m.setParam("OutputFlag", 0)
        x = m.addVar(lb=0, ub=1)
        m.setObjective(x, gp.GRB.MAXIMIZE)
        m.optimize()
        report(OK, "gurobi (python)", f"version {gp.gurobi.version()}, "
                                      f"trivial LP status {m.Status}")
    except ImportError:
        report(WARN, "gurobi (python)",
               "gurobipy not importable; only matters if you use the python "
               "API — the solver links the C++ API separately")
    except Exception as exc:
        report(FAIL, "gurobi (python)", f"license or runtime problem: {exc}")

    # 3 end-to-end solve --------------------------------------------------
    if not args.skip_solve and s.exists() and os.access(s, os.X_OK):
        man = DATA / "manifest_instances.csv"
        inst = None
        if man.exists():
            import csv
            rows = list(csv.DictReader(man.open()))
            small = sorted(rows, key=lambda r: int(r["n"]))
            inst = DATA / small[0]["path"] if small else None
        if inst and inst.exists():
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "probe.json"
                cmd = [str(s), "-i", str(inst), "-m", "H1", "-b", "8",
                       "--tl", "60", "--thl", "1", "-o", str(out)]
                t0 = time.time()
                try:
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                    if p.returncode != 0:
                        report(FAIL, "end-to-end solve",
                               f"exit {p.returncode}: {(p.stderr or '')[-300:]}")
                    elif not out.exists():
                        report(FAIL, "end-to-end solve", "no JSON written")
                    else:
                        j = json.loads(out.read_text())
                        need = {"solution_info", "battery_levels", "task_assignments"}
                        missing = need - set(j)
                        if missing:
                            report(FAIL, "end-to-end solve",
                                   f"JSON missing keys: {sorted(missing)}")
                        else:
                            si = j["solution_info"]
                            report(OK, "end-to-end solve",
                                   f"obj={si.get('objective_value')} in "
                                   f"{time.time()-t0:.1f}s")
                except Exception as exc:
                    report(FAIL, "end-to-end solve", str(exc))
        else:
            report(WARN, "end-to-end solve",
                   "no instances yet — run 01_build_instances.py, then re-run preflight")

    # 4 python ------------------------------------------------------------
    if sys.version_info < (3, 9):
        report(FAIL, "python", f"{sys.version.split()[0]} — need >= 3.9")
    else:
        report(OK, "python", sys.version.split()[0])
    try:
        import numpy
        report(OK, "numpy", numpy.__version__)
    except ImportError:
        report(FAIL, "numpy", "pip install numpy")

    # 5 resources ---------------------------------------------------------
    cores = os.cpu_count() or 1
    if cores < design.N_WORKERS:
        report(WARN, "cores", f"{cores} available, design wants "
                              f"{design.N_WORKERS} workers")
    else:
        report(OK, "cores", f"{cores} available, using {design.N_WORKERS}")

    try:
        with open("/proc/meminfo") as fh:
            kb = int(next(l for l in fh if l.startswith("MemTotal")).split()[1])
        gb = kb / 1e6
        need = design.N_WORKERS * design.MEM_LIMIT_GB
        report(OK if gb >= need else WARN, "RAM",
               f"{gb:.0f} GB total; design may request up to {need:.0f} GB "
               f"({design.N_WORKERS} x {design.MEM_LIMIT_GB} GB)")
    except Exception:
        report(WARN, "RAM", "could not read /proc/meminfo")

    DATA.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(DATA).free / 1e9
    report(OK if free > 50 else WARN, "disk",
           f"{free:.0f} GB free at {DATA} (full profile needs ~200 MB of "
           f"instances plus ~1 GB per 100k solution JSONs)")

    # 6 source structures --------------------------------------------------
    missing = []
    for p in design.SIZE_CLASSES:
        for rep in range(1, design.REPLICATES + 1):
            if not (ORIG / f"{p}_{rep}.txt").exists():
                missing.append(f"{p}_{rep}")
    if missing:
        report(FAIL, "PSPLIB structures",
               f"{len(missing)} missing, e.g. {missing[:5]}")
    else:
        report(OK, "PSPLIB structures",
               f"all {len(design.SIZE_CLASSES)*design.REPLICATES} present")

    print("\nPREFLIGHT " + ("FAILED — fix the items above before running."
                            if _hard_fail else "PASSED."))
    return 1 if _hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
