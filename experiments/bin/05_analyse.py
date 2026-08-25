#!/usr/bin/env python3
"""
Stage 5 — run every analysis over data/results.csv.

Writes one plain-text report per experiment into data/analysis/, plus a
combined summary. Text rather than plots by default: the numbers are what go
into the paper, and a text report diffs cleanly between runs so you can see
exactly what changed when you add data.

TWO GENERATIONS OF ANALYSIS LIVE HERE. `analysis/managerial.py` handles the v2
campaign (M0-M5); `analysis/analyses.py` handles the v1 experiments (E0-E9) and
is kept because the v1 results are still on disk and still referenced by the
methods paper. Which one runs is decided by the `experiment` column, so a
results table containing both is analysed correctly without a flag.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis import analyses as A          # noqa: E402
from analysis import managerial as M        # noqa: E402
from analysis import replication as REP     # noqa: E402
from config import economics                # noqa: E402

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))

V2 = ["MR", "M0", "M1", "M2", "M3", "M4", "M5"]
V1 = ["E0", "E1", "E2", "E3", "E4", "E5", "E6", "E8", "E9"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    help="comma list, e.g. M1,M2. Default: whatever the results "
                         "table actually contains.")
    ap.add_argument("--economics", default="central",
                    help="comma list of config/economics.py SENSITIVITY keys. "
                         "NPV is a post-hoc function of the measured saving, so "
                         "extra corners cost no solver time -- always run all "
                         "three before quoting a payback figure.")
    ap.add_argument("--seed-aggregation", default="mean", choices=["mean", "best"],
                    help="how to collapse seeds (default mean; 'best' reproduces "
                         "best-of-run reporting and is biased in the seed count)")
    args = ap.parse_args()

    econ_keys = [k.strip() for k in args.economics.split(",") if k.strip()]
    bad = [k for k in econ_keys if k not in economics.SENSITIVITY]
    if bad:
        print(f"FATAL: unknown economics key(s) {bad}; have "
              f"{sorted(economics.SENSITIVITY)}", file=sys.stderr)
        return 2

    res = DATA / "results.csv"
    if not res.exists():
        print("FATAL: run 04_collect.py first", file=sys.stderr)
        return 1
    rows = A.load_results(res)
    print(f"loaded {len(rows)} successful runs")
    if not rows:
        return 1

    present = {r.get("experiment", "") for r in rows}
    want = ({e.strip() for e in args.only.split(",") if e.strip()}
            or (present & set(V2 + V1)))
    out = DATA / "analysis"
    out.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []

    # --- campaign v2 -------------------------------------------------------
    # MR first, and not only for tidiness: it estimates sigma_seed, which every
    # other report then quotes in its seed-noise footnote. Reading an effect
    # before knowing the noise floor of the instrument that measured it is the
    # mistake the whole experiment exists to prevent.
    if "MR" in want:
        parts.append(REP.mr(rows, out))
    if "M0" in want:
        parts.append(M.m0(rows, out))
    # Every GA experiment gets the same footnote, computed once from MR's
    # sigma_seed: how much of its precision is the solver rather than the
    # plant. Appended rather than woven in, so the reports stay diffable
    # against earlier runs.
    def _with_noise(text: str, exp: str) -> str:
        return text + "\n".join(REP.seed_noise_note(rows, exp))

    if "M1" in want:
        for k in econ_keys:
            parts.append(f"\n=== M1 under economics = {k} ===\n"
                         + _with_noise(M.m1(rows, out,
                                            econ=economics.SENSITIVITY[k]), "M1"))
    if "M2" in want:
        parts.append(_with_noise(
            M.m2(rows, out, econ=economics.SENSITIVITY[econ_keys[0]]), "M2"))
    if "M3" in want:
        parts.append(_with_noise(M.m3(rows, out), "M3"))
    if "M4" in want:
        parts.append(_with_noise(M.m4(rows, out), "M4"))
    if "M5" in want:
        parts.append(_with_noise(M.m5(rows, out), "M5"))

    # --- campaign v1, kept for the methods paper ---------------------------
    legacy = {"E0": A.e0, "E1": None, "E2": None, "E3": A.e3, "E4": A.e4,
              "E5": A.e5, "E6": A.e6, "E8": A.e8, "E9": A.e9}
    for name, fn in legacy.items():
        if name not in want:
            continue
        if name == "E1":
            parts.append(A.e1(rows, out, how=args.seed_aggregation))
        elif name == "E2":
            for k in econ_keys:
                parts.append(f"\n=== E2 under economics = {k} ===\n"
                             + A.e2(rows, out, economics.SENSITIVITY[k]))
        else:
            parts.append(fn(rows, out))

    combined = "\n\n".join(parts)
    (out / "summary.txt").write_text(combined)
    print(combined)
    print(f"\nreports written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
