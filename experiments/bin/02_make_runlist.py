#!/usr/bin/env python3
"""
Stage 2 — expand the design into an explicit runlist, and check the budget
BEFORE any compute is spent.

Every row of runlist.csv is one solver invocation, fully specified. Nothing
downstream reinterprets the design: the driver reads argv from this file and
executes it. That makes the experiment auditable — you can diff two runlists,
count cells, and verify the factorial is balanced without running anything.

Solver capability probing
    Some design cells need solver features that do not exist yet (state-set
    restriction, C-rate limits, schedule re-costing). This script probes
    `solver --help`, marks unsupported cells as blocked, writes them to
    runlist_blocked.csv with the reason, and excludes them from runlist.csv.
    When the feature lands, re-run this script and the cells appear.

Outputs
    data/runlist.csv          runnable invocations
    data/runlist_blocked.csv  design cells awaiting solver features
    data/budget_report.txt    estimated core-hours vs available
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design                     # noqa: E402
from lib.rcpsp_io import read_extended        # noqa: E402
from lib.generate import battery_arg          # noqa: E402

import os
DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
DEFAULT_SOLVER = ROOT.parent / "build" / "rcpsp_wt_battery"


def probe(solver: Path) -> set[str]:
    """Return the set of long options the solver advertises in --help."""
    try:
        launcher = ([sys.executable, str(solver)] if str(solver).endswith(".py")
                    else [str(solver)])
        out = subprocess.run(launcher + ["--help"], capture_output=True,
                             text=True, timeout=60)
        text = (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"WARNING: could not probe solver ({exc}); assuming current feature set")
        return {"--phase1-price-aware", "--phase3-lp"}
    flags = set()
    for tok in text.replace(",", " ").split():
        if tok.startswith("--"):
            flags.add(tok.split("=")[0].strip("[]"))
    return flags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", type=Path, default=DEFAULT_SOLVER)
    ap.add_argument("--allow-over-budget", action="store_true")
    args = ap.parse_args()

    man = list(csv.DictReader((DATA / "manifest_instances.csv").open()))
    if not man:
        print("FATAL: run 01_build_instances.py first", file=sys.stderr)
        return 1
    by_name = {r["instance"]: r for r in man}
    flags = probe(args.solver)
    print(f"solver flags detected: {len(flags)}")

    # Battery arguments need E_day, which is in the manifest.
    def barg(row: dict, ratio: float) -> int:
        return max(0, int(round(ratio * float(row["e_day"]))))

    runs: list[dict] = []
    blocked: list[dict] = []

    def add(exp: str, row: dict, method: str, ratio: float, policy: str,
            state: str, seed: int | None, extra: list[str]) -> None:
        spec = design.STATE_POLICIES[state]
        if spec["flag"] and spec["flag"].split("=")[0] not in flags:
            blocked.append(dict(experiment=exp, instance=row["instance"],
                                method=method, state_policy=state,
                                reason=f"solver lacks {spec['flag'].split('=')[0]} (item C1)"))
            return
        b = barg(row, ratio)
        tl = design.TL[method]
        launcher = ([sys.executable, str(args.solver)] if str(args.solver).endswith(".py")
                    else [str(args.solver)])
        argv = launcher + ["-i", str((DATA / row["path"]).resolve()), "-m", method,
                "-b", str(b), "--tl", str(tl), "--thl", str(design.THREADS_PER_RUN),
                "--ml", str(design.MEM_LIMIT_GB)]
        if seed is not None:
            argv += ["-s", str(seed)]
        if spec["flag"]:
            argv.append(spec["flag"])
        argv += extra
        rid = "__".join([exp, row["instance"], method, policy, state,
                         f"b{ratio:g}", f"s{seed if seed is not None else 0}"])
        runs.append(dict(
            run_id=rid, experiment=exp, instance=row["instance"],
            instance_path=row["path"], method=method, policy=policy,
            state_policy=state, battery_ratio=ratio, battery_arg=b,
            seed=seed if seed is not None else "", time_limit=tl,
            size_class=row["size_class"], ei_density_level=row["ei_density_level"],
            due_tightness_level=row["due_tightness_level"], lam=row["lam"],
            price_name=row["price_name"], price_regime=row["price_regime"],
            argv="\t".join(argv),
        ))

    def in_subset(r: dict, tag: str) -> bool:
        return tag in r["subset"].split(",")

    core = [r for r in man if in_subset(r, "core")]
    e3 = [r for r in man if in_subset(r, "e3")]
    e4 = [r for r in man if in_subset(r, "e4")]

    def is_det(method: str) -> bool:
        return method in ("H1", "H1P", "MILP")

    def seeds_for(method: str, exp: str) -> list[int | None]:
        if is_det(method):
            return [None]
        k = design.SEEDS_PER_EXP.get(exp, len(design.SEEDS))
        return design.SEEDS[:k]

    def window_index(row: dict) -> int:
        """0 for contractual tariffs, w-index for spot windows."""
        name = row["price_name"]
        return int(name.rsplit("_w", 1)[1]) if "_w" in name else 0

    def series_ok(row: dict, exp: str) -> bool:
        """Limit how many spot windows each experiment consumes."""
        if row["price_regime"] in design.CONTRACTUAL or row["price_regime"] == "synthetic":
            return True
        return window_index(row) < design.SPOT_SERIES_PER_EXP.get(exp, 10 ** 6)

    # ---- E0: solver validation ------------------------------------------
    if design.ENABLED["E0"]:
        for r in core:
            if r["price_regime"] != "spot_midvol" or not series_ok(r, "E0"):
                continue
            for ratio in (0.0, design.BATTERY_ON_RATIO):
                for method in ("H1", "H1P", "GA", "GAP"):
                    for s in seeds_for(method, "E0"):
                        pol = "edd" if method in ("H1", "GA") else "price_aware"
                        add("E0", r, method, ratio, pol, "sigma3", s, [])
                if int(r["size_class"]) <= design.MILP_MAX_SIZE_CLASS:
                    add("E0", r, "MILP", ratio, "exact", "sigma3", None, [])

    # ---- E1: value decomposition ----------------------------------------
    if design.ENABLED["E1"]:
        for r in core:
            if r["price_regime"] not in design.CORE_REGIMES or not series_ok(r, "E1"):
                continue
            for state in design.STATE_POLICIES:
                for pol, spec in design.POLICIES.items():
                    method = spec["method_ga"]
                    for ratio in (0.0, design.BATTERY_ON_RATIO):
                        for s in seeds_for(method, "E1"):
                            add("E1", r, method, ratio, pol, state, s, [])

    # ---- E2: storage sizing ---------------------------------------------
    if design.ENABLED["E2"]:
        for r in core:
            if r["price_regime"] not in design.E2_REGIMES or not series_ok(r, "E2"):
                continue
            for ratio in design.BATTERY_RATIOS:
                for s in seeds_for("GAP", "E2"):
                    add("E2", r, "GAP", ratio, "price_aware", "sigma3", s,
                        ["--phase1-price-aware"] if "--phase1-price-aware" in flags else [])

    # ---- E3: tariff regimes ---------------------------------------------
    if design.ENABLED["E3"]:
        for r in e3:
            for ratio in (0.0, design.BATTERY_ON_RATIO):
                for s in seeds_for("GAP", "E3"):
                    add("E3", r, "GAP", ratio, "price_aware", "sigma3", s, [])

    # ---- E4: service-energy frontier ------------------------------------
    if design.ENABLED["E4"]:
        pool = e4 + [r for r in core
                     if r["price_regime"] == design.E4_REGIME and series_ok(r, "E4")]
        for r in pool:
            for ratio in (0.0, design.BATTERY_ON_RATIO):
                for s in seeds_for("GAP", "E4"):
                    add("E4", r, "GAP", ratio, "price_aware", "sigma3", s, [])

    # ---- budget ----------------------------------------------------------
    # Deterministic constructive methods finish far below their time limit;
    # only the metaheuristics and the MILP actually consume their budget.
    est_frac = {"H1": 0.05, "H1P": 0.08, "GA": 1.0, "GAP": 1.0, "MILP": 0.9}
    core_s = sum(design.TL[r["method"]] * est_frac[r["method"]] for r in runs)
    core_h = core_s / 3600.0
    wall_h = core_h / design.N_WORKERS
    avail_h = design.WALL_CLOCK_BUDGET_H * design.N_WORKERS

    by_exp = Counter(r["experiment"] for r in runs)
    by_meth = Counter(r["method"] for r in runs)
    lines = [
        "budget report",
        f"  profile            {design.PROFILE}",
        f"  runnable runs      {len(runs)}",
        f"  blocked cells      {len(blocked)}",
        "",
        "  runs per experiment:",
        *[f"    {k:5s} {v:8d}" for k, v in sorted(by_exp.items())],
        "  runs per method:",
        *[f"    {k:5s} {v:8d}" for k, v in sorted(by_meth.items())],
        "",
        f"  estimated core-hours   {core_h:10.1f}",
        f"  available core-hours   {avail_h:10.1f}"
        f"  ({design.N_WORKERS} workers x {design.WALL_CLOCK_BUDGET_H} h)",
        f"  estimated wall-clock   {wall_h:10.1f} h  ({wall_h/24:.2f} days)",
        f"  budget utilisation     {100*core_h/avail_h:10.1f} %",
    ]
    if blocked:
        lines += ["", "  blocked cells by reason:"]
        for reason, k in Counter(b["reason"] for b in blocked).items():
            lines.append(f"    {k:8d}  {reason}")
    report = "\n".join(lines)
    (DATA / "budget_report.txt").write_text(report + "\n")
    print(report)

    if core_h > avail_h and not args.allow_over_budget:
        print("\nFATAL: estimated cost exceeds the budget. Lower PROFILE in "
              "config/design.py, or pass --allow-over-budget to proceed anyway.",
              file=sys.stderr)
        return 2

    _write(DATA / "runlist.csv", runs)
    _write(DATA / "runlist_blocked.csv", blocked)
    return 0


def _write(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        print(f"wrote {path.name} (empty)")
        return
    cols = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path.name} ({len(rows)} rows)")


if __name__ == "__main__":
    raise SystemExit(main())
