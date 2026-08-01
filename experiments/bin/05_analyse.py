#!/usr/bin/env python3
"""
Stage 5 — run every analysis over data/results.csv.

Writes one plain-text report per experiment into data/analysis/, plus a
combined summary. Text rather than plots by default: the numbers are what go
into the paper, and a text report diffs cleanly between runs so you can see
exactly what changed when you add data.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis import analyses as A        # noqa: E402
from config import economics              # noqa: E402

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list, e.g. E1,E4")
    ap.add_argument("--seed-aggregation", default="mean", choices=["mean", "best"],
                    help="how to collapse seeds in E1 (default mean; 'best' "
                         "reproduces best-of-run reporting and is biased in k)")
    args = ap.parse_args()

    res = DATA / "results.csv"
    if not res.exists():
        print("FATAL: run 04_collect.py first", file=sys.stderr)
        return 1
    rows = A.load_results(res)
    print(f"loaded {len(rows)} successful runs")
    if not rows:
        return 1

    out = DATA / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    want = set(args.only.split(",")) if args.only else {"E0", "E1", "E2", "E3", "E4", "E5", "E6"}

    econ = economics.CENTRAL
    parts = []
    if "E0" in want:
        parts.append(A.e0(rows, out))
    if "E1" in want:
        parts.append(A.e1(rows, out, how=args.seed_aggregation))
    if "E2" in want:
        parts.append(A.e2(rows, out, econ))
    if "E3" in want:
        parts.append(A.e3(rows, out))
    if "E4" in want:
        parts.append(A.e4(rows, out))
    if "E5" in want:
        parts.append(A.e5(rows, out))
    if "E6" in want:
        parts.append(A.e6(rows, out))

    combined = "\n\n".join(parts)
    (out / "summary.txt").write_text(combined)
    print(combined)
    print(f"\nreports written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
