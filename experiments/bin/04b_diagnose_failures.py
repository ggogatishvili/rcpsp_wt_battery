#!/usr/bin/env python3
"""
Stage 4b — why did runs fail?

`04_collect.py` reports a failure RATE. The pre-registration says a rate above
2 % must be investigated before any analysis is interpreted -- and then gives
no way to investigate it. This script is that way.

It reads the per-run `.meta.json` files (status, return code, wall time,
stderr tail), joins them to the runlist so every failure carries its factor
levels, and answers the four questions that decide what to do next:

  WHERE   which experiment, method, size class, tariff, capacity, machine
          profile. A failure rate concentrated in one cell is a bug in that
          cell; one spread evenly is a bug in the harness or the machine.

  WHAT    timeout, non-zero exit, or no output. These have different causes and
          different fixes, and averaging them into one percentage hides that.

  WHY     the distinct stderr messages, grouped and counted. Ten thousand runs
          usually fail for two or three reasons, not ten thousand.

  HOW BAD is the surviving design still balanced? This is the question that
          actually matters for the paper. Failures that fall entirely inside
          one arm of a paired comparison do far more damage than a larger
          number spread evenly, because every paired test drops those cells
          list-wise and the comparison silently changes sample.

    python3 bin/04b_diagnose_failures.py
    python3 bin/04b_diagnose_failures.py --experiment M1 --show-stderr 5
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
RESULTS = DATA / "results"

# Factors worth breaking failures down by. Order matters: the report prints
# them in this order and the first one to show concentration is usually the
# cause.
FACTORS = ["experiment", "method", "size_class", "block", "state_policy",
           "machine_profile", "battery_ratio", "price_regime", "time_limit",
           "ei_density_level", "due_tightness_level", "seed"]


def normalise_stderr(text: str) -> str:
    """Collapse a stderr tail to its recognisable shape.

    Paths, numbers, addresses and instance names differ on every run and would
    make every failure look unique; the point is to see that eight hundred runs
    died the same way. Keep the last non-empty line -- for a C++ abort or a
    Python traceback that is the message that names the cause.
    """
    if not text:
        return "(no stderr captured)"
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "(empty stderr)"
    msg = lines[-1]
    msg = re.sub(r"/\S+/", "/…/", msg)                 # paths
    msg = re.sub(r"\b\d+\.\d+\b", "<float>", msg)      # floats
    msg = re.sub(r"\b\d{2,}\b", "<n>", msg)            # multi-digit integers
    msg = re.sub(r"0x[0-9a-fA-F]+", "<addr>", msg)     # addresses
    return msg[:160]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="", help="restrict to one experiment")
    ap.add_argument("--show-stderr", type=int, default=3,
                    help="print this many full stderr tails per distinct message")
    ap.add_argument("--top", type=int, default=12,
                    help="rows per breakdown table")
    args = ap.parse_args()

    runlist = DATA / "runlist.csv"
    if not runlist.exists():
        print(f"FATAL: {runlist} not found", file=sys.stderr)
        return 1
    runs = {r["run_id"]: r for r in csv.DictReader(runlist.open())}

    metas = sorted(RESULTS.glob("*.meta.json"))
    if not metas:
        print(f"FATAL: no .meta.json under {RESULTS}", file=sys.stderr)
        return 1

    ok, bad = [], []
    for p in metas:
        try:
            m = json.loads(p.read_text())
        except ValueError:
            continue
        rid = m.get("run_id", p.name[:-10])
        row = dict(runs.get(rid, {}))
        row.update(_meta=m, run_id=rid, status=m.get("status", "?"))
        if args.experiment and row.get("experiment") != args.experiment:
            continue
        (ok if m.get("status") == "ok" else bad).append(row)

    total = len(ok) + len(bad)
    if not total:
        print("no runs matched")
        return 0

    L = ["failure diagnosis", "=" * 74,
         f"  runs with a meta file   {total}",
         f"  succeeded               {len(ok)}",
         f"  failed                  {len(bad)}  ({100*len(bad)/total:.2f} %)"]
    if args.experiment:
        L.append(f"  restricted to           {args.experiment}")
    if not bad:
        L.append("\n  Nothing to diagnose.")
        print("\n".join(L))
        return 0

    # ---- WHAT ------------------------------------------------------------
    L += ["", "-" * 74,
          "1. What kind of failure",
          "-" * 74,
          "  timeout    the driver killed it at --tl + 120 s. The run was alive,",
          "             just too slow: a budget or an instance-size problem.",
          "  error      non-zero exit. The solver decided it could not continue;",
          "             the message is in section 3.",
          "  no_output  exit 0 but no JSON written. The worst kind: the solver",
          "             believes it succeeded. Usually a crash after the last",
          "             flush, or a path it could not write.",
          ""]
    for st, n in Counter(r["status"] for r in bad).most_common():
        L.append(f"    {st:<12s} {n:7d}   {100*n/len(bad):5.1f} % of failures")

    # ---- WHERE -----------------------------------------------------------
    L += ["", "-" * 74,
          "2. Where they are concentrated",
          "-" * 74,
          "  'rate' is failures over ATTEMPTS in that cell, which is the number",
          "  that matters -- a level with few attempts and every one failing is",
          "  a different problem from a level with many attempts and a few.",
          ""]
    for f in FACTORS:
        att = Counter(str(r.get(f, "")) for r in (ok + bad) if str(r.get(f, "")) != "")
        fail = Counter(str(r.get(f, "")) for r in bad if str(r.get(f, "")) != "")
        if len(att) <= 1 or not fail:
            continue
        rows = sorted(((lv, fail.get(lv, 0), att[lv]) for lv in att),
                      key=lambda t: (-(t[1] / max(1, t[2])), -t[1]))
        interesting = [r for r in rows if r[1]]
        if not interesting:
            continue
        L.append(f"  by {f}:")
        for lv, nf, na in interesting[:args.top]:
            bar = "#" * int(round(20 * nf / max(1, na)))
            L.append(f"    {lv:<22s} {nf:6d} / {na:6d}   {100*nf/na:6.2f} %  {bar}")
        clean = [lv for lv, nf, _ in rows if nf == 0]
        if clean:
            L.append(f"    ({len(clean)} level(s) with zero failures: "
                     f"{', '.join(map(str, clean[:6]))}"
                     f"{' …' if len(clean) > 6 else ''})")
        L.append("")

    # ---- WHY -------------------------------------------------------------
    L += ["-" * 74,
          "3. Distinct failure messages",
          "-" * 74, ""]
    by_msg: dict[str, list] = defaultdict(list)
    for r in bad:
        by_msg[normalise_stderr(r["_meta"].get("stderr_tail", ""))].append(r)
    for msg, rs in sorted(by_msg.items(), key=lambda kv: -len(kv[1])):
        L.append(f"  [{len(rs):6d}]  {msg}")
        exps = Counter(r.get("experiment", "?") for r in rs)
        meths = Counter(r.get("method", "?") for r in rs)
        L.append(f"            experiments: {dict(exps.most_common(4))}   "
                 f"methods: {dict(meths.most_common(4))}")
        for r in rs[:args.show_stderr]:
            tail = (r["_meta"].get("stderr_tail") or "").strip().splitlines()
            L.append(f"            e.g. {r['run_id'][:70]}")
            for ln in tail[-4:]:
                L.append(f"                 | {ln[:100]}")
        L.append("")

    # ---- HOW BAD ---------------------------------------------------------
    # The question the paper actually depends on. A paired comparison drops a
    # cell if EITHER side is missing, so what matters is not the failure rate
    # but whether failures are paired-symmetric.
    L += ["-" * 74,
          "4. What this does to the paired comparisons",
          "-" * 74,
          "  Every effect in this campaign is a paired difference on a common",
          "  instance. A failure removes not one observation but the whole PAIR,",
          "  and if failures sit mostly in one arm the comparison quietly",
          "  changes which instances it is averaging over.",
          ""]
    # Group by (instance, everything except battery_ratio) and see how many
    # groups lose a member.
    key_cols = ["instance", "method", "state_policy", "machine_profile",
                "time_limit", "seed", "block"]
    groups: dict[tuple, dict[str, str]] = defaultdict(dict)
    for r in ok + bad:
        if not r.get("instance"):
            continue
        k = tuple(str(r.get(c, "")) for c in key_cols)
        groups[k][str(r.get("battery_ratio", ""))] = r["status"]
    intact = broken = 0
    for k, byb in groups.items():
        if len(byb) < 2:
            continue
        if all(v == "ok" for v in byb.values()):
            intact += 1
        else:
            broken += 1
    if intact + broken:
        L += [f"  storage contrasts intact   {intact:7d}",
              f"  storage contrasts broken   {broken:7d}   "
              f"({100*broken/(intact+broken):.2f} % of pairs lost)",
              "",
              "  Compare that percentage with the raw failure rate above. If it",
              "  is MUCH larger, failures are clustered inside pairs and the",
              "  damage is worse than the rate suggests. If it is similar,",
              "  failures are scattered and re-running them restores the design."]
    else:
        L.append("  (no complete storage contrast found; too little data)")

    L += ["", "-" * 74,
          "5. What to do",
          "-" * 74,
          "  Re-running is free of scientific risk: 03_run.py is resumable and",
          "  a rerun uses the identical command line and seed, which the",
          "  pre-registration requires.",
          "",
          "    python3 bin/03_run.py --rerun-failed",
          "",
          "  That is the right response to timeouts and to transient errors. It",
          "  is NOT the right response to a message that names a real bug: a",
          "  deterministic failure will fail again, and 918 identical reruns",
          "  cost the same as 918 original runs.",
          "",
          "  If section 2 shows one factor level carrying nearly all failures,",
          "  fix that first -- the pre-registration's 2 % threshold exists so",
          "  that this conversation happens before the analysis, not after."]

    out = DATA / "failure_diagnosis.txt"
    out.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
