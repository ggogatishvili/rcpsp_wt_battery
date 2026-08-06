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
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
DEFAULT_SOLVER = ROOT.parent / "build" / "rcpsp_wt_battery"


def probe(solver: Path) -> set[str]:
    """Return the set of long options the solver advertises in --help."""
    try:
        launcher = ([sys.executable, str(solver)] if str(solver).endswith(".py")
                    else [str(solver)])
        out = subprocess.run(launcher + ["--help"], capture_output=True,
                             text=True, timeout=60, check=False)
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
    flags = probe(args.solver)
    print(f"solver flags detected: {len(flags)}")

    # Battery arguments need E_day, which is in the manifest.
    def barg(row: dict, ratio: float) -> int:
        return max(0, round(ratio * float(row["e_day"])))

    runs: list[dict] = []
    blocked: list[dict] = []

    def add(exp: str, row: dict, method: str, ratio: float, policy: str,
            state: str, seed: int | None, extra: list[str],
            tag: str = "", extra_cols: dict | None = None,
            tl_override: int | None = None) -> None:
        spec = design.STATE_POLICIES[state]
        if spec["flag"] and spec["flag"].split("=")[0] not in flags:
            blocked.append({"experiment": exp, "instance": row["instance"],
                            "method": method, "state_policy": state,
                            "reason": f"solver lacks {spec['flag'].split('=')[0]} (item C1)"})
            return
        b = barg(row, ratio)
        # The tl tag is appended ONLY when the budget differs from the method
        # default. That keeps every run_id generated before TL_PROFILE existed
        # byte-identical, so the results already on disk are not orphaned and
        # only the genuinely new budgets have to be executed.
        tl = design.TL[method] if tl_override is None else tl_override
        if tl != design.TL[method]:
            tag = f"tl{tl}" if not tag else f"{tag}__tl{tl}"
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
                         f"b{ratio:g}", f"s{seed if seed is not None else 0}"]
                        + ([tag] if tag else []))
        run = {
            "run_id": rid, "experiment": exp, "instance": row["instance"],
            "instance_path": row["path"], "method": method, "policy": policy,
            "state_policy": state, "battery_ratio": ratio, "battery_arg": b,
            "seed": seed if seed is not None else "", "time_limit": tl,
            "size_class": row["size_class"], "ei_density_level": row["ei_density_level"],
            "due_tightness_level": row["due_tightness_level"], "lam": row["lam"],
            "price_name": row["price_name"], "price_regime": row["price_regime"],
            "argv": "\t".join(argv),
        }
        if extra_cols:
            run.update(extra_cols)
        runs.append(run)

    def in_subset(r: dict, tag: str) -> bool:
        return tag in r["subset"].split(",")

    core = [r for r in man if in_subset(r, "core")]
    e3 = [r for r in man if in_subset(r, "e3")]
    e4 = [r for r in man if in_subset(r, "e4")]

    def is_det(method: str) -> bool:
        return method in ("H1", "H1P", "MILP")

    def seeds_for(method: str, exp: str) -> Sequence[int | None]:
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
                    pol = "edd" if method in ("H1", "GA") else "price_aware"
                    # Anytime profile: only the metaheuristics have a genuine
                    # time budget. H1/H1P are constructive (their --tl is a
                    # guard) and the MILP already runs at its own limit, so
                    # sweeping those would burn compute for no information.
                    budgets = (design.TL_PROFILE if method in ("GA", "GAP")
                               else [design.TL[method]])
                    for tl in budgets:
                        for s in seeds_for(method, "E0"):
                            add("E0", r, method, ratio, pol, "sigma3", s, [],
                                tl_override=tl)
                if int(r["size_class"]) <= design.MILP_MAX_SIZE_CLASS:
                    add("E0", r, "MILP", ratio, "exact", "sigma3", None, [])

    # ---- E1: value decomposition ----------------------------------------
    if design.ENABLED["E1"]:
        for r in core:
            if r["price_regime"] not in design.CORE_REGIMES or not series_ok(r, "E1"):
                continue
            for state in design.STATE_POLICIES:
                for pol, spec in design.POLICIES.items():
                    # The sigma ladder is crossed with GA only (see
                    # design.E1_LADDER_POLICIES). sigma3 keeps every policy so
                    # that the runs already on disk stay addressable.
                    if state != "sigma3" and pol not in design.E1_LADDER_POLICIES:
                        continue
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

    # ---- E6: machine profile (C2) / battery efficiency (C3) / C-rate (C4) --
    # No new instances: none of these three parameters are baked into the
    # instance file, so E6 reuses E3's stratified 90-shop subset (B_scr)
    # crossed with CORE_REGIMES (flat, tou2, one spot_midvol window) and only
    # adds solver flags. Two independent sub-designs, summed per
    # EXPERIMENTAL_PLAN.md's run-budget line — see design.py §7 for why.
    if design.ENABLED["E6"]:
        e6_flags_needed = {"--e-proc", "--off-proc-time", "--charging-efficiency", "--c-rate"}
        missing = e6_flags_needed - flags
        if missing:
            blocked.append({"experiment": "E6", "instance": "", "method": "", "state_policy": "",
                            "reason": f"solver lacks {sorted(missing)} (items C2/C3/C4)"})
        else:
            e6_shop_ids = {r["shop_id"] for r in e3} or {r["shop_id"] for r in core}
            pool = [r for r in core
                    if r["shop_id"] in e6_shop_ids
                    and r["price_regime"] in design.CORE_REGIMES
                    and series_ok(r, "E6")]

            # E6a: machine substitution map -- (rho, restart) grid x policy, battery on
            for r in pool:
                for rho in design.RHO_LEVELS:
                    for restart in design.RESTART_LEVELS:
                        machine_args = design.machine_profile_args(rho, restart)
                        for pol, spec in design.POLICIES.items():
                            method = spec["method_ga"]
                            for s in seeds_for(method, "E6"):
                                add("E6", r, method, design.BATTERY_ON_RATIO, pol, "sigma3", s,
                                    machine_args, tag=f"mach_rho{rho:g}_{restart}",
                                    extra_cols={"e6_subdesign": "machine", "rho": rho,
                                               "restart_level": restart,
                                               "roundtrip_eff": "", "c_rate": ""})

            # E6b: C-rate retention -- (round-trip efficiency, C-rate) grid, price-aware only
            for r in pool:
                for eff in design.ROUNDTRIP_EFFICIENCY_LEVELS:
                    for crate in design.C_RATE_LEVELS:
                        batt_args = design.battery_profile_args(eff, crate)
                        method = design.POLICIES["price_aware"]["method_ga"]
                        crate_tag = "inf" if crate == float("inf") else f"{crate:g}"
                        for s in seeds_for(method, "E6"):
                            add("E6", r, method, design.BATTERY_ON_RATIO, "price_aware",
                                "sigma3", s, batt_args, tag=f"batt_eff{eff:g}_c{crate_tag}",
                                extra_cols={"e6_subdesign": "battery", "rho": "",
                                           "restart_level": "", "roundtrip_eff": eff,
                                           "c_rate": crate_tag})

    # ---- budget ----------------------------------------------------------
    # Deterministic constructive methods finish far below their time limit;
    # only the metaheuristics and the MILP actually consume their budget.
    est_frac = {"H1": 0.05, "H1P": 0.08, "GA": 1.0, "GAP": 1.0, "MILP": 0.9}
    # Use each run's own time_limit, not the method default: with TL_PROFILE
    # the same method appears at several budgets and the default would
    # under-count the 600 s cells by an order of magnitude.
    def cost_of(rs) -> float:
        return sum(int(r["time_limit"]) * est_frac[r["method"]] for r in rs) / 3600.0

    # A run that produced a solution has both {rid}.json and {rid}.meta.json;
    # a failed one has only the meta file. So completion can be read from the
    # filenames alone, without opening 266k files.
    done = {p.name[:-5] for p in (DATA / "results").glob("*.json")
            if not p.name.endswith(".meta.json")}
    todo = [r for r in runs if r["run_id"] not in done]

    core_h = cost_of(runs)
    todo_h = cost_of(todo)
    wall_h = core_h / design.N_WORKERS
    todo_wall = todo_h / design.N_WORKERS
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
        f"  whole design           {core_h:10.1f} core-h  "
        f"({wall_h:6.1f} h wall, {wall_h/24:.2f} days)",
        f"  already complete       {len(done):10d} runs",
        f"  REMAINING TO RUN       {len(todo):10d} runs = {todo_h:.1f} core-h  "
        f"({todo_wall:.1f} h wall, {todo_wall/24:.2f} days)",
        (f"  available core-hours   {avail_h:10.1f}"
         f"  ({design.N_WORKERS} workers x {design.WALL_CLOCK_BUDGET_H} h)"),
        f"  budget utilisation     {100*todo_h/avail_h:10.1f} %  (of REMAINING work)",
    ]
    if todo:
        rem_exp = Counter(r["experiment"] for r in todo)
        rem_tl = Counter((r["method"], int(r["time_limit"])) for r in todo)
        lines += ["", "  remaining by experiment:",
                  *[f"    {k:5s} {v:8d}" for k, v in sorted(rem_exp.items())],
                  "  remaining by (method, budget):",
                  *[f"    {k[0]:5s} tl={k[1]:4d}s {v:8d}"
                    for k, v in sorted(rem_tl.items())]]
    if blocked:
        lines += ["", "  blocked cells by reason:"]
        for reason, k in Counter(b["reason"] for b in blocked).items():
            lines.append(f"    {k:8d}  {reason}")
    report = "\n".join(lines)
    (DATA / "budget_report.txt").write_text(report + "\n")
    print(report)

    # Gate on REMAINING work: already-completed runs are sunk cost, and gating
    # on the whole design would block every incremental extension once the bulk
    # of the benchmark exists.
    if todo_h > avail_h and not args.allow_over_budget:
        print("\nFATAL: remaining cost exceeds the budget. Lower PROFILE in "
              "config/design.py, trim TL_PROFILE, or pass --allow-over-budget.",
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
    # Union of keys across all rows, not just rows[0]: E6 rows carry extra
    # columns (rho, restart_level, roundtrip_eff, c_rate) that E0-E4 rows
    # don't, and DictWriter errors on a row with a key outside fieldnames.
    cols: list[str] = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path.name} ({len(rows)} rows)")


if __name__ == "__main__":
    raise SystemExit(main())
