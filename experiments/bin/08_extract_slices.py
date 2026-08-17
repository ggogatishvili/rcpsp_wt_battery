#!/usr/bin/env python3
"""
Stage 8 — extract small, transferable slices of data/results.csv.

The collected result table is too large to move off the compute server, but
the three analyses still open (see the \\cj{} notes in the paper) each need only
a few thousand rows and a couple of dozen columns. This script never loads the
table into memory: it streams it and writes only what is asked for.

Run it in two steps, because the second depends on what the first reveals.

    # 1. census -- a few kilobytes, tells us what is actually in the table
    python3 bin/08_extract_slices.py --census

    # 2. slices -- once the filters are confirmed against the census
    python3 bin/08_extract_slices.py --slices

Everything lands in data/export/. Sizes are printed so nothing is transferred
blind.

WHY A CENSUS FIRST
------------------
The local price manifest lists only flat, tou2 and the three spot regimes; the
paper reports a synthetic family of 5760 observations that does not appear in
it. Something was generated on the server that is not reflected locally, so the
regime label of the synthetic family is unknown here and guessing it would
silently produce an empty slice. The census resolves that in one pass, and it
costs one read of the file.

WHAT EACH SLICE IS FOR
----------------------
synth   E3's open question: isolate the effect of the share of negative-price
        hours. SYNTH_NEG_SHARE = [0.0, 0.08] varies orthogonally to spread in
        the design, so the contrast is identifiable from data already
        collected -- it has simply never been extracted. This is currently the
        paper's most interesting E3 claim and it rests on a regime contrast
        rather than on that orthogonal variation.

flat    The placebo cell. The paper reports the mean and its interval; the
        pre-registration deviation note asks additionally for a high percentile
        of the flat-tariff difference distribution, which needs the individual
        values rather than a summary.

e0      The optimality-gap stability check across battery levels, currently
        computed on the relative gap and therefore uninformative wherever the
        objective approaches zero. Recomputing it on the normalised gap needs
        the per-run objective, not the gap.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
RESULTS = DATA / "results.csv"
EXPORT = DATA / "export"

# Columns worth carrying. Anything absent from the header is skipped silently,
# so this list can name more than a given table happens to have.
KEEP = [
    # identity and pairing keys
    "run_id", "experiment", "instance", "inst_shop_id", "price_regime",
    "price_name", "method", "policy", "state_policy", "battery_ratio",
    "battery_arg", "seed", "lam", "size_class", "time_limit", "status",
    # outcomes
    "objective", "energy_cost", "tardiness_cost", "wall_seconds",
    # instance descriptors, incl. everything norm_scale() needs
    "inst_n", "inst_ei_duration_share", "inst_ei_density", "inst_horizon_days",
    "inst_mean_due_slack", "inst_order_strength", "inst_resource_strength",
    # tariff descriptors -- inst_neg_share is the point of the synth slice
    "inst_neg_share", "inst_spread_intraday", "inst_price_cv", "inst_price_mean",
    # E6 technology factors
    "rho", "restart_level", "roundtrip_eff", "c_rate",
]

# Categorical columns to enumerate in the census. Numeric ones are summarised
# by quantiles instead, further down.
CATEGORICAL = ["experiment", "price_regime", "price_name", "method", "policy",
               "state_policy", "battery_ratio", "lam", "size_class",
               "time_limit", "status", "rho", "restart_level", "roundtrip_eff",
               "c_rate", "seed"]
NUMERIC = ["inst_neg_share", "inst_spread_intraday", "inst_price_cv",
           "inst_price_mean", "inst_n", "inst_horizon_days"]

MAX_DISTINCT = 40          # beyond this a column is summarised, not listed


def quantiles(v: list[float]) -> str:
    if not v:
        return "no finite value"
    v = sorted(v)
    q = lambda p: v[min(len(v) - 1, int(p * len(v)))]      # noqa: E731
    return (f"n={len(v)}  min={v[0]:.4g}  p10={q(0.10):.4g}  med={q(0.50):.4g}  "
            f"p90={q(0.90):.4g}  max={v[-1]:.4g}")


def census(path: Path, out: Path) -> int:
    if not path.exists():
        print(f"FATAL: {path} not found. Run 04_collect.py first.", file=sys.stderr)
        return 1

    with path.open(newline="") as fh:
        rdr = csv.DictReader(fh)
        header = rdr.fieldnames or []
        cats = {c: Counter() for c in CATEGORICAL if c in header}
        nums = {c: [] for c in NUMERIC if c in header}
        cross = Counter()
        neg_by_regime = defaultdict(Counter)
        total = 0
        for r in rdr:
            total += 1
            for c, ctr in cats.items():
                ctr[r.get(c, "")] += 1
            for c, acc in nums.items():
                try:
                    x = float(r[c])
                    if math.isfinite(x):
                        acc.append(x)
                except (KeyError, TypeError, ValueError):
                    pass
            cross[(r.get("experiment", ""), r.get("price_regime", ""))] += 1
            if "inst_neg_share" in header:
                neg_by_regime[r.get("price_regime", "")][r.get("inst_neg_share", "")] += 1

    lines = [
        "census of " + str(path),
        f"  size on disk   {path.stat().st_size / 1e6:.1f} MB",
        f"  rows           {total}",
        f"  columns        {len(header)}",
        "",
        "HEADER",
        "  " + ", ".join(header),
        "",
        "MISSING FROM HEADER (named in KEEP but absent)",
        "  " + (", ".join(c for c in KEEP if c not in header) or "none"),
        "",
    ]

    lines.append("CATEGORICAL COLUMNS")
    for c, ctr in cats.items():
        if len(ctr) > MAX_DISTINCT:
            lines.append(f"  {c}: {len(ctr)} distinct values (too many to list); "
                         f"most common {ctr.most_common(5)}")
        else:
            body = ", ".join(f"{k or '<empty>'}={v}" for k, v in sorted(ctr.items()))
            lines.append(f"  {c}: {body}")
    lines.append("")

    lines.append("NUMERIC COLUMNS")
    for c, acc in nums.items():
        lines.append(f"  {c}: {quantiles(acc)}")
    lines.append("")

    lines.append("EXPERIMENT x PRICE_REGIME")
    for (e, g), k in sorted(cross.items()):
        lines.append(f"  {e or '<empty>':6s} x {g or '<empty>':16s} {k}")
    lines.append("")

    # The decisive question for the E3 slice: does any regime carry BOTH
    # neg-share levels? If one does, the contrast is identifiable within it and
    # no new runs are needed.
    lines.append("NEG-SHARE LEVELS PRESENT WITHIN EACH REGIME")
    for g, ctr in sorted(neg_by_regime.items()):
        vals = sorted(ctr.items())
        flag = "  <-- both levels, contrast identifiable here" if len(ctr) > 1 else ""
        lines.append(f"  {g or '<empty>':16s} " +
                     ", ".join(f"{k or '<empty>'}({v})" for k, v in vals[:8]) +
                     ("..." if len(vals) > 8 else "") + flag)

    out.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    (out / "census.txt").write_text(text)
    print(text)
    print(f"written to {out / 'census.txt'} "
          f"({(out / 'census.txt').stat().st_size / 1024:.1f} kB) -- "
          f"small enough to paste or transfer as is")
    return 0


def slices(path: Path, out: Path, specs: list[tuple[str, str, str]],
           cap: int) -> int:
    """Write one CSV per (name, column, value) filter, streaming the source."""
    if not path.exists():
        print(f"FATAL: {path} not found.", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)

    with path.open(newline="") as fh:
        header = (csv.DictReader(fh).fieldnames or [])
    cols = [c for c in KEEP if c in header]
    missing = [c for c in KEEP if c not in header]
    if missing:
        print(f"note: {len(missing)} requested column(s) absent and skipped: "
              f"{', '.join(missing)}")

    writers, files, counts, truncated = {}, {}, Counter(), set()
    for name, col, _val in specs:
        if col not in header:
            print(f"WARNING: slice '{name}' filters on '{col}', which is not a "
                  f"column. Skipped.", file=sys.stderr)
            continue
        f = (out / f"slice_{name}.csv").open("w", newline="")
        files[name] = f
        writers[name] = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writers[name].writeheader()

    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            for name, col, val in specs:
                if name not in writers:
                    continue
                if val != "*" and r.get(col, "") != val:
                    continue
                if counts[name] >= cap:
                    truncated.add(name)
                    continue
                writers[name].writerow(r)
                counts[name] += 1

    for name, f in files.items():
        f.close()
    print()
    for name, _col, _val in specs:
        p = out / f"slice_{name}.csv"
        if not p.exists():
            continue
        mark = "  TRUNCATED at the cap" if name in truncated else ""
        print(f"  {p.name:28s} {counts[name]:8d} rows  "
              f"{p.stat().st_size / 1e6:7.2f} MB{mark}")
    tot = sum((out / f"slice_{n}.csv").stat().st_size
              for n, _c, _v in specs if (out / f"slice_{n}.csv").exists())
    print(f"\n  total {tot / 1e6:.2f} MB in {out}")
    if truncated:
        print("  a truncated slice is not a random sample -- raise --cap and "
              "re-run rather than analysing it")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--out", type=Path, default=EXPORT)
    ap.add_argument("--census", action="store_true",
                    help="enumerate what the table contains, and stop")
    ap.add_argument("--slices", action="store_true",
                    help="write the slices named by --slice (or the defaults)")
    ap.add_argument("--slice", action="append", default=[], metavar="NAME:COL=VAL",
                    help="e.g. --slice synth:price_regime=synthetic ; repeatable. "
                         "VAL may be * to take every row")
    ap.add_argument("--cap", type=int, default=200_000,
                    help="maximum rows per slice, a guard against writing "
                         "something untransferable (default 200000)")
    args = ap.parse_args()

    if args.census or not (args.slices or args.slice):
        return census(args.results, args.out)

    specs: list[tuple[str, str, str]] = []
    for s in args.slice:
        try:
            name, rest = s.split(":", 1)
            col, val = rest.split("=", 1)
            specs.append((name, col, val))
        except ValueError:
            print(f"FATAL: cannot parse --slice '{s}', expected NAME:COL=VAL",
                  file=sys.stderr)
            return 2
    if not specs:
        # Defaults, deliberately conservative: the flat and E0 filters are
        # certain, the synthetic one is a guess the census exists to correct.
        specs = [("flat", "price_regime", "flat"),
                 ("e0",   "experiment",   "E0"),
                 ("e3",   "experiment",   "E3")]
        print("no --slice given; using defaults "
              "(check them against the census first)")
    return slices(args.results, args.out, specs, args.cap)


if __name__ == "__main__":
    raise SystemExit(main())
