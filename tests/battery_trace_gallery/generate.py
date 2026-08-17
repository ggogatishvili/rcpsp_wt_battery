#!/usr/bin/env python3
"""
Battery/schedule trace gallery — solver runner.

Builds a 5 (instance size) x 3 (battery capacity) x 3 (charging speed) grid,
runs the solver for each cell (MILP, falling back to GA when MILP doesn't
prove a good solution within the time budget; GA outright for the two
largest size classes, which this repo's own experimental design already
established as beyond MILP's reach — see MILP_MAX_SIZE_CLASS in
experiments/config/design.py) plus LBBD, and caches every raw solver JSON
under data/. Resumable: a cell already on disk is skipped.

    python3 generate.py --dry-run          # print the command grid, run nothing
    python3 generate.py                    # run everything (~90 solver calls)
    python3 generate.py --only 1_1         # just one instance
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INSTANCES = ROOT / "instances"
BINARY = ROOT / "build" / "rcpsp_wt_battery"
DATA = HERE / "data"

sys.path.insert(0, str(ROOT / "experiments" / "lib"))
import rcpsp_io  # noqa: E402

# Binary is linked against a Gurobi version no longer installed on this
# machine; point the loader at whichever one is actually present rather than
# touching global env or rebuilding.
GUROBI_LIB_CANDIDATES = [
    "/Library/gurobi1302/macos_universal2/lib",
    "/Library/gurobi1301/macos_universal2/lib",
]

# --- grid --------------------------------------------------------------
SIZE_PREFIXES = [1, 2, 4, 8, 16]           # n in {32,64,128,256,512}
REPLICATE = 1
CAP_RATIOS = {"low": 0.25, "medium": 1.0, "high": 4.0}
CRATE_LEVELS = {"low": 0.25, "medium": 1.0, "high": None}   # None = uncapped
TIME_LIMIT = 180
MILP_MAX_PREFIX = 2        # only n<=64 gets a real MILP attempt
MILP_GOOD_GAP = 0.01
THREADS = 3
SEED = 1

# Mirrors experiments/lib/generate.py::E_PROC — hard-coded in
# include/instance.h and never overridden by this driver.
E_PROC = 4.0
HOURS_PER_DAY = 24


def e_day(inst: rcpsp_io.Instance) -> float:
    """Mean daily processing energy of the EI machine (see generate.py)."""
    ei_dur = sum(inst.tasks[i].duration for i in inst.ei_ids)
    days = inst.horizon / HOURS_PER_DAY
    return E_PROC * ei_dur / days if days else 0.0


def battery_arg(inst: rcpsp_io.Instance, ratio: float) -> int:
    return max(0, round(ratio * e_day(inst)))


@dataclass(frozen=True)
class Cell:
    prefix: int
    cap_label: str
    crate_label: str

    @property
    def instance_name(self) -> str:
        return f"{self.prefix}_{REPLICATE}"

    @property
    def instance_path(self) -> Path:
        return INSTANCES / f"{self.instance_name}.txt"

    @property
    def tag(self) -> str:
        return f"n{self.prefix:02d}_cap-{self.cap_label}_crate-{self.crate_label}"


def all_cells() -> list[Cell]:
    return [Cell(p, cap, cr) for p in SIZE_PREFIXES
            for cap in CAP_RATIOS for cr in CRATE_LEVELS]


def gurobi_env() -> dict:
    env = os.environ.copy()
    for lib in GUROBI_LIB_CANDIDATES:
        if Path(lib).is_dir():
            existing = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = lib + (":" + existing if existing else "")
            break
    return env


def run_solver(method: str, cell: Cell, out_path: Path, extra_args: list[str]) -> bool:
    """Invoke the solver, write JSON to out_path. Returns True on success."""
    if out_path.exists():
        return True
    inst = rcpsp_io.read_extended(cell.instance_path)
    b = battery_arg(inst, CAP_RATIOS[cell.cap_label])
    cmd = [str(BINARY), "-i", str(cell.instance_path), "-m", method,
           "-b", str(b), "--tl", str(TIME_LIMIT), "--thl", str(THREADS),
           "-o", str(out_path)]
    crate = CRATE_LEVELS[cell.crate_label]
    if crate is not None:
        cmd += ["--c-rate", str(crate)]
    cmd += extra_args
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(cmd, env=gurobi_env(), capture_output=True, text=True,
                             timeout=TIME_LIMIT + 120)
    if result.returncode != 0 or not out_path.exists():
        print(f"  FAILED {method} {cell.tag}: rc={result.returncode}\n"
              f"    cmd: {' '.join(cmd)}\n"
              f"    stderr: {result.stderr.strip()[-500:]}", file=sys.stderr)
        return False
    return True


def baseline_extra_args(method: str) -> list[str]:
    return ["-s", str(SEED)] if method == "GA" else []


def lbbd_extra_args() -> list[str]:
    sub_tl = max(1.0, 0.10 * TIME_LIMIT)
    refine_tl = max(1.0, 0.02 * TIME_LIMIT)
    return ["--sub-tl", f"{sub_tl:.1f}", "--refine-tl", f"{refine_tl:.1f}"]


def process_cell(cell: Cell) -> dict:
    """Run baseline (MILP or GA, with fallback) and LBBD for one cell."""
    log = {"cell": cell.tag, "baseline_method": None, "ok": True}

    milp_path = DATA / f"{cell.tag}_MILP.json"
    ga_path = DATA / f"{cell.tag}_GA.json"
    baseline_path = DATA / f"{cell.tag}_baseline.json"

    if baseline_path.exists():
        log["baseline_method"] = json.loads(baseline_path.read_text())["config"]["method"]
    else:
        use_milp = cell.prefix <= MILP_MAX_PREFIX
        chosen = None
        if use_milp:
            ok = run_solver("MILP", cell, milp_path, baseline_extra_args("MILP"))
            if ok:
                sol = json.loads(milp_path.read_text())
                gap = sol["solution_info"]["gap"]
                if sol["solution_info"]["objective_value"] < 1e18 and gap <= MILP_GOOD_GAP:
                    chosen = milp_path
                    log["milp_gap"] = gap
        if chosen is None:
            ok = run_solver("GA", cell, ga_path, baseline_extra_args("GA"))
            log["ok"] = log["ok"] and ok
            if ok:
                chosen = ga_path
        if chosen is not None:
            baseline_path.write_bytes(chosen.read_bytes())
            log["baseline_method"] = json.loads(baseline_path.read_text())["config"]["method"]
        else:
            log["ok"] = False

    lbbd_path = DATA / f"{cell.tag}_LBBD.json"
    ok = run_solver("LBBD", cell, lbbd_path, lbbd_extra_args())
    log["ok"] = log["ok"] and ok
    return log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="instance name filter, e.g. 1_1")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cells = all_cells()
    if args.only:
        cells = [c for c in cells if c.instance_name == args.only]

    if args.dry_run:
        print(f"{len(cells)} cells, {len(cells) * 2} solver calls (baseline + LBBD)")
        for c in cells:
            use_milp = c.prefix <= MILP_MAX_PREFIX
            print(f"  {c.tag}: baseline={'MILP->GA fallback' if use_milp else 'GA'}, LBBD")
        return

    DATA.mkdir(parents=True, exist_ok=True)
    print(f"Running {len(cells)} cells with {args.workers} workers "
          f"(tl={TIME_LIMIT}s per solver call)...")

    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_cell, c): c for c in cells}
        for i, fut in enumerate(as_completed(futures), 1):
            log = fut.result()
            status = "ok" if log["ok"] else "FAILED"
            print(f"[{i}/{len(cells)}] {log['cell']}: baseline={log['baseline_method']} "
                  f"({status})")
            if not log["ok"]:
                failures.append(log["cell"])

    if failures:
        print(f"\n{len(failures)} cells had a failed solver call:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
