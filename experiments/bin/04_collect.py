#!/usr/bin/env python3
"""
Stage 4 — collect solution JSONs into one tidy table, running integrity checks
on the way.

The integrity checks are the point of this stage. A results table that has not
been validated against the model is a table of numbers, not evidence. Every
check below has a failure mode it is designed to catch:

  C1 objective decomposition   objective == energy + tardiness (to tolerance)
                               catches an objective that silently includes
                               something the paper does not report
  C2 battery bounds            0 <= level_t <= capacity for all t
                               catches capacity being ignored
  C3 zero-battery invariance   with -b 0 the battery level is identically 0
                               catches a battery that "helps" when absent
  C4 tardiness sign            tardiness >= 0
  C5 flat-tariff falsification  under the flat tariff, energy cost must be
                               (near) identical across battery levels; any
                               difference is solver noise and is reported as
                               the resolution floor of the whole study
  C6 schedule sanity           every task starts within the horizon and after
                               its release date; precedence respected

Outputs
    data/results.csv          one row per run, merged with instance covariates
    data/integrity_report.txt pass/fail per check, with offending run ids
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.rcpsp_io import read_extended

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
RESULTS = DATA / "results"

TOL_ABS = 1e-4
TOL_REL = 1e-6


def battery_stats(levels: list[float], capacity: float) -> dict:
    """Derive cycling statistics from the level trace.

    The solver exports battery_levels only (item C9 in EXPERIMENTAL_PLAN.md),
    so charge and discharge are recovered from positive and negative level
    deltas. With charging efficiency eta_c this understates grid energy drawn
    for charging by a factor eta_c; that is corrected in the analysis, not
    here, because eta_c is a solver constant and belongs in one place.
    """
    if not levels:
        return {"batt_charge": 0.0, "batt_discharge": 0.0, "batt_efc": 0.0,
                "batt_peak": 0.0, "batt_used": 0}
    chg = sum(max(0.0, b - a) for a, b in pairwise(levels))
    dis = sum(max(0.0, a - b) for a, b in pairwise(levels))
    peak = max(levels)
    efc = (dis / capacity) if capacity > 0 else 0.0
    return {"batt_charge": round(chg, 6), "batt_discharge": round(dis, 6),
            "batt_efc": round(efc, 6), "batt_peak": round(peak, 6),
            "batt_used": int(dis > TOL_ABS)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any integrity check fails")
    args = ap.parse_args()

    man = {r["instance"]: r for r in csv.DictReader((DATA / "manifest_instances.csv").open())}
    runs = {r["run_id"]: r for r in csv.DictReader((DATA / "runlist.csv").open())}

    rows: list[dict] = []
    fails: dict[str, list[str]] = defaultdict(list)
    n_meta = n_ok = n_missing = 0

    for meta_path in sorted(RESULTS.glob("*.meta.json")):
        n_meta += 1
        meta = json.loads(meta_path.read_text())
        rid = meta["run_id"]
        run = runs.get(rid)
        if run is None:
            fails["orphan_result"].append(rid)
            continue
        inst_row = man.get(run["instance"], {})
        base = dict(run)
        base.pop("argv", None)
        base.update({f"inst_{k}": v for k, v in inst_row.items()
                     if k not in ("instance", "path", "sha256")})
        base.update(status=meta["status"], wall_seconds=meta["wall_seconds"],
                    returncode=meta["returncode"])

        sol_path = RESULTS / f"{rid}.json"
        if meta["status"] != "ok" or not sol_path.exists():
            n_missing += 1
            rows.append(base)
            continue
        try:
            sol = json.loads(sol_path.read_text())
        except ValueError:
            fails["unparseable_json"].append(rid)
            rows.append(base)
            continue

        si = sol.get("solution_info", {})
        # nlohmann::json serializes non-finite doubles (NaN/inf) as JSON null,
        # so a present-but-null field must fall back to nan same as a missing one.
        def _num(key: str) -> float:
            v = si.get(key)
            return float("nan") if v is None else float(v)

        obj = _num("objective_value")
        ec = _num("energy_cost")
        tc = _num("tardiness_cost")
        levels = [float("nan") if x is None else float(x)
                  for x in sol.get("battery_levels", [])]
        cap = float(run["battery_arg"])

        # ---- C1 objective decomposition ---------------------------------
        if (math.isfinite(obj) and math.isfinite(ec) and math.isfinite(tc)
                and abs(obj - (ec + tc)) > max(TOL_ABS, TOL_REL * abs(obj))):
            fails["C1_objective_decomposition"].append(rid)
        # ---- C2 battery bounds -------------------------------------------
        if levels and (min(levels) < -TOL_ABS or max(levels) > cap + TOL_ABS):
            fails["C2_battery_bounds"].append(rid)
        # ---- C3 zero-battery invariance ----------------------------------
        if cap == 0 and levels and max(abs(x) for x in levels) > TOL_ABS:
            fails["C3_zero_battery_nonzero_level"].append(rid)
        # ---- C4 tardiness sign -------------------------------------------
        if math.isfinite(tc) and tc < -TOL_ABS:
            fails["C4_negative_tardiness"].append(rid)

        base.update(objective=obj, energy_cost=ec, tardiness_cost=tc,
                    gap=si.get("gap", ""),
                    solver_seconds=si.get("computation_time", ""),
                    n_machine_blocks=len(sol.get("machine_blocks", [])),
                    **battery_stats(levels, cap))

        # Per-method measurements, passed through verbatim as diag_* columns.
        # Deliberately generic: the decomposition methods export cut counts, a
        # dual bound and the storage-free cost of the same schedule, and none
        # of those mean anything to H1/GA, so hard-coding them here would put
        # a wall of empty columns in front of every other experiment. Methods
        # that export nothing produce no columns at all.
        for key, value in (sol.get("diagnostics") or {}).items():
            base[f"diag_{key}"] = float("nan") if value is None else float(value)

        # ---- C7 dual-bound sanity (decomposition methods only) -----------
        # A bound must not exceed the objective it bounds -- but *which*
        # objective depends on the method, and this is the easy thing to get
        # wrong. Only Benders' theta bounds the battery-aware cost. LBBD,
        # NoGoodCuts and StateLBBD price energy at the raw tariff, so their
        # master bounds the battery-FREE problem; checking them against the
        # post-processed objective flags every run in which the battery saved
        # anything, i.e. almost all of them.
        bound = base.get("diag_bound")
        aware = base.get("diag_bound_is_battery_aware")
        no_batt = base.get("diag_energy_cost_no_battery")
        if bound is not None and math.isfinite(bound):
            if aware is not None and aware > 0.5:
                reference = obj
            elif no_batt is not None and math.isfinite(no_batt) and math.isfinite(tc):
                reference = no_batt + tc
            else:
                reference = None
            if (reference is not None and math.isfinite(reference)
                    and bound > reference + max(TOL_ABS, TOL_REL * abs(reference))):
                fails["C7_bound_above_objective"].append(rid)

        # ---- C8 storage never hurts --------------------------------------
        # The post-processed cost cannot exceed the storage-free cost of the
        # same schedule: the battery LP can always choose to do nothing.
        if (no_batt is not None and math.isfinite(no_batt) and math.isfinite(ec)
                and ec > no_batt + max(TOL_ABS, TOL_REL * abs(no_batt))):
            fails["C8_battery_increased_cost"].append(rid)

        rows.append(base)
        n_ok += 1

    # ---- C5 flat-tariff falsification ------------------------------------
    flat = defaultdict(dict)
    for r in rows:
        if r.get("price_regime") == "flat" and r.get("status") == "ok":
            key = (r["instance"], r["method"], r["policy"], r["state_policy"], r["seed"])
            flat[key][float(r["battery_ratio"])] = float(r["energy_cost"])
    resolution = 0.0
    n_flat = 0
    for key, by_b in flat.items():
        if len(by_b) < 2:
            continue
        n_flat += 1
        base_c = by_b.get(0.0)
        if not base_c:
            continue
        for b, c in by_b.items():
            if b == 0.0:
                continue
            rel = abs(c - base_c) / abs(base_c) if base_c else 0.0
            resolution = max(resolution, rel)

    # ---- C6 schedule sanity (sampled: full re-check is O(runs x n)) -------
    import random
    sample = random.Random(0).sample(
        [r for r in rows if r.get("status") == "ok"],
        min(200, sum(1 for r in rows if r.get("status") == "ok"))) if rows else []
    for r in sample:
        sol_path = RESULTS / f"{r['run_id']}.json"
        try:
            sol = json.loads(sol_path.read_text())
            inst = read_extended(DATA / man[r["instance"]]["path"])
        except (OSError, ValueError, KeyError):
            continue
        starts = {t["task_id"]: t["start_time"] for t in sol.get("task_assignments", [])}
        for i, t in enumerate(inst.tasks):
            s = starts.get(i)
            if s is None:
                continue
            if s < t.release or s + t.duration > inst.horizon:
                fails["C6_schedule_window"].append(r["run_id"])
                break
            if any(starts.get(j, 0) < s + t.duration for j in t.successors):
                fails["C6_precedence"].append(r["run_id"])
                break

    # ---- write -----------------------------------------------------------
    if rows:
        cols: list[str] = []
        for r in rows:
            for c in r:
                if c not in cols:
                    cols.append(c)
        with (DATA / "results.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

    lines = [
        "integrity report",
        f"  result files found     {n_meta}",
        f"  parsed successfully    {n_ok}",
        f"  failed / missing       {n_missing}",
        f"  failure rate           {100*n_missing/max(1,n_meta):.2f} %",
        "",
        "  checks:",
    ]
    checks = ["C1_objective_decomposition", "C2_battery_bounds",
              "C3_zero_battery_nonzero_level", "C4_negative_tardiness",
              "C6_schedule_window", "C6_precedence",
              "C7_bound_above_objective", "C8_battery_increased_cost",
              "orphan_result", "unparseable_json"]
    for c in checks:
        bad = fails.get(c, [])
        mark = "PASS" if not bad else "FAIL"
        lines.append(f"    {mark}  {c:34s} {len(bad):6d} offending")
        for rid in bad[:5]:
            lines.append(f"            e.g. {rid}")
    lines += [
        "",
        f"  C5 flat-tariff falsification: {n_flat} comparable groups",
        (f"     max relative energy-cost difference across battery levels "
         f"under a constant price: {resolution:.3e}"),
        "     -> this is the RESOLUTION FLOOR of the study. Any effect in E1-E4",
        "        smaller than this is indistinguishable from solver noise and",
        "        must not be reported as a finding.",
    ]
    report = "\n".join(lines)
    (DATA / "integrity_report.txt").write_text(report + "\n")
    print(report)

    any_fail = any(fails.get(c) for c in checks)
    return 1 if (args.strict and any_fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
