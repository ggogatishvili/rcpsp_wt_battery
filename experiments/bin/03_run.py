#!/usr/bin/env python3
"""
Stage 3 — execute the runlist.

Design notes that matter on a 5-day job:

  * Resumable. One result file per run, written atomically. Re-running skips
    anything already complete, so a crash, a reboot or a deliberate Ctrl-C
    costs only the runs in flight. Never rely on a single append-only log.
  * Hard timeouts. The solver takes --tl, but a wedged process ignores it, so
    the driver kills at tl + TIMEOUT_MARGIN and records a `timeout` status
    rather than hanging a worker for the rest of the week.
  * Failures are data. A non-zero exit or a missing JSON is recorded with its
    stderr and does not stop the run; 05_collect.py reports the failure rate,
    and a failure rate above a threshold is a finding, not an inconvenience.
  * Shuffled execution order. Runs are executed in a deterministic shuffle so
    that a partial run is a *representative* sample of the design rather than
    all of E0 and none of E4. If you stop early, you can still analyse.

Usage
    python3 bin/03_run.py                      # all runs, N_WORKERS from design
    python3 bin/03_run.py --experiments E1,E2  # subset
    python3 bin/03_run.py --workers 32 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
RESULTS = DATA / "results"
LOGS = DATA / "logs"

TIMEOUT_MARGIN = 120        # seconds of grace beyond --tl before SIGKILL

# One thread per instance, enforced here rather than trusting the solver.
# --thl 1 (already in every argv, see design.THREADS_PER_RUN) only bounds
# Gurobi's own thread count (SolverMILP.cpp); it does nothing for the TBB
# pool ParadisEO uses for GA/GAP, which defaults to hardware_concurrency()
# threads with no env-var override. With N_WORKERS parallel processes that
# oversubscribes the box badly, so every subprocess is pinned to exactly one
# CPU core via taskset -- that caps it regardless of what runs inside it.
# taskset is Linux-only (util-linux); on other platforms (e.g. this may be
# tested on macOS) pinning is silently skipped and only the env-var caps
# below apply.
TASKSET = shutil.which("taskset")
SINGLE_THREAD_ENV = {
    **os.environ,
    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
}

_stop = False


def _sigint(signum, frame):
    global _stop
    _stop = True
    print("\ninterrupt received — finishing in-flight runs, then stopping "
          "(re-run this script to resume)", flush=True)


def provenance(solver: Path) -> dict:
    def sh(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=30, check=False).stdout.strip()
        except Exception:
            return ""
    return { "started_utc": datetime.now(timezone.utc).isoformat()
           , "hostname": platform.node()
           , "platform": platform.platform()
           , "python": sys.version.split()[0]
           , "cpu_count": os.cpu_count()
           , "solver_path": str(solver)
           , "solver_sha256": _sha(solver)
           , "solver_version": sh([str(solver), "--version"])
           , "git_commit": sh(["git", "-C", str(ROOT.parent), "rev-parse", "HEAD"])
           , "git_dirty": bool(sh(["git", "-C", str(ROOT.parent), "status", "--porcelain"]))
           , "design_profile": design.PROFILE
           , "master_seed": design.MASTER_SEED }


def _sha(p: Path) -> str:
    import hashlib
    try:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def run_one(row: dict, core: int | None) -> dict:
    rid = row["run_id"]
    out_json = RESULTS / f"{rid}.json"
    argv = row["argv"].split("\t") + ["-o", str(out_json)]
    if TASKSET and core is not None:
        argv = [TASKSET, "-c", str(core)] + argv
    tl = int(row["time_limit"])
    t0 = time.time()
    status, rc, err = "ok", 0, ""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False,
                              timeout=tl + TIMEOUT_MARGIN, env=SINGLE_THREAD_ENV)
        rc = proc.returncode
        if rc != 0:
            status, err = "error", (proc.stderr or "")[-2000:]
        elif not out_json.exists():
            status, err = "no_output", (proc.stderr or "")[-2000:]
    except subprocess.TimeoutExpired:
        status, err = "timeout", f"killed after {tl + TIMEOUT_MARGIN}s"
    except OSError as exc:
        status, err = "exec_error", str(exc)
    wall = time.time() - t0

    meta = { "run_id": rid
           , "status": status
           , "returncode": rc
           , "wall_seconds": round(wall, 3)
           , "stderr_tail": err }
    (RESULTS / f"{rid}.meta.json").write_text(json.dumps(meta))
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=design.N_WORKERS)
    ap.add_argument("--experiments", default="", help="comma list, e.g. E1,E2")
    ap.add_argument("--limit", type=int, default=0, help="run at most N (smoke test)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rerun-failed", action="store_true",
                    help="also redo runs previously recorded as failed")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _sigint)
    RESULTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader((DATA / "runlist.csv").open()))
    if not rows:
        print("FATAL: empty runlist; run 02_make_runlist.py first", file=sys.stderr)
        return 1
    if args.experiments:
        keep = set(args.experiments.split(","))
        rows = [r for r in rows if r["experiment"] in keep]

    # Deterministic shuffle: a partial run stays representative of the design.
    random.Random(design.MASTER_SEED).shuffle(rows)

    todo = []
    for r in rows:
        meta = RESULTS / f"{r['run_id']}.meta.json"
        if meta.exists():
            if not args.rerun_failed:
                continue
            try:
                if json.loads(meta.read_text()).get("status") == "ok":
                    continue
            except (OSError, ValueError):
                pass
        todo.append(r)
    if args.limit:
        todo = todo[:args.limit]

    solver = Path(rows[0]["argv"].split("\t")[0])
    free_gb = shutil.disk_usage(DATA).free / 1e9
    print(f"runlist {len(rows)}  already done {len(rows)-len(todo)}  todo {len(todo)}")
    print(f"workers {args.workers}   free disk {free_gb:.1f} GB")
    print(f"CPU pinning: {'taskset -c <core>, 1 core per worker slot' if TASKSET else 'DISABLED (taskset not found on this platform)'}")
    if free_gb < 20:
        print("WARNING: less than 20 GB free; solution JSONs will fill it")

    if args.dry_run:
        for r in todo[:5]:
            print("  " + " ".join(r["argv"].split("\t")))
        print(f"  ... ({len(todo)} runs)")
        return 0

    prov = provenance(solver)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (LOGS / f"provenance_{stamp}.json").write_text(json.dumps(prov, indent=2))
    print(json.dumps(prov, indent=2))

    t0 = time.time()
    done = 0
    fails: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {}
        it = iter(todo)
        # Each concurrent slot keeps the same core for its whole lifetime: a
        # replacement submitted after a slot's task completes reuses that
        # exact core, so at most `args.workers` distinct cores are ever
        # pinned at once, matching the executor's own concurrency cap. Prime
        # exactly one task per slot (not 2x) -- assigning a core to more than
        # `args.workers` in-flight-or-queued tasks would let two of them run
        # concurrently on the same core, since completion order isn't
        # submission order.
        for core in range(min(args.workers, len(todo))):
            try:
                r = next(it)
            except StopIteration:
                break
            futures[ex.submit(run_one, r, core)] = (r, core)
        while futures:
            for fut in as_completed(list(futures), timeout=None):
                r, core = futures.pop(fut)
                meta = fut.result()
                done += 1
                if meta["status"] != "ok":
                    fails.append(meta)
                if done % 200 == 0 or done == len(todo):
                    el = time.time() - t0
                    rate = done / el if el else 0
                    eta = (len(todo) - done) / rate if rate else 0
                    print(f"  {done}/{len(todo)}  {100*done/len(todo):5.1f}%  "
                          f"{rate*3600:7.0f} runs/h  ETA {eta/3600:5.2f} h  "
                          f"fails {len(fails)}", flush=True)
                if not _stop:
                    try:
                        nxt = next(it)
                        futures[ex.submit(run_one, nxt, core)] = (nxt, core)
                    except StopIteration:
                        pass
                break
            if _stop and not futures:
                break

    el = time.time() - t0
    summary = { "finished_utc": datetime.now(timezone.utc).isoformat()
              , "attempted": done
              , "failures": len(fails)
              , "wall_hours": round(el / 3600, 3)
              , "core_hours": round(done * el / 3600 / max(1, args.workers), 3)
              , "interrupted": _stop }
    (LOGS / f"run_summary_{stamp}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if fails:
        with (LOGS / f"failures_{stamp}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(fails[0].keys()))
            w.writeheader()
            w.writerows(fails)
        print(f"{len(fails)} failures written to logs/failures_{stamp}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
