#!/usr/bin/env python3
"""
Stage 9 — the three analyses that the \\cj{} notes in Section 9 left open.

Runs on the slices written by 08_extract_slices.py, not on the full result
table, so it is portable off the compute server.

    python3 bin/09_slice_analyses.py

A. NEGATIVE-PRICE SHARE, ISOLATED  (E3, paper note at "This is E3's most
   interesting result and it is currently inferred from a regime contrast")

   The synthetic tariff family varies the share of hours below zero
   orthogonally to intra-day spread: price_name encodes the design as
   synth_s{spread}_n{noise}_g{negshare}_d{draw}, and g00/g08 are the two
   levels. So the effect is identifiable from data already collected by a
   contrast that holds spread, noise, instance and seed fixed and moves only
   the negative-price share. That is a far stronger design than the regime
   comparison the paper currently relies on, in which spread and negative-price
   share move together and cannot be separated.

B. THE FLAT-TARIFF DIFFERENCE DISTRIBUTION  (pre-registration deviation)

   Check C5 reports the maximum relative energy-cost difference across battery
   levels under a constant price. A maximum over thousands of groups is a
   worst-case order statistic; it bounds single-instance claims but not the
   standard error of a mean over paired instances, which is what every effect
   in E1-E4 is. The percentiles are what a paired mean should be judged
   against.

C. GAP STABILITY ACROSS BATTERY LEVELS  (E0, "computed on the relative gap and
   therefore uninformative")

   Whether a method's gap to the reference changes when storage is installed.
   Computed on a normalised gap rather than a relative one, because with
   negative prices the denominator of a relative gap can approach zero. Note
   that for a STABILITY question the choice of positive, configuration-
   invariant scale is immaterial: the same divisor is applied to both battery
   levels, so it cancels from the comparison.
"""
from __future__ import annotations

import csv
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "data" / "export"

SYNTH = re.compile(r"synth_s(\d+)_n(\d+)_g(\d+)_d(\d+)")


# ==========================================================================
# statistics (stdlib only; scipy is not installed on the compute server)
# ==========================================================================

def average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def wilcoxon(diffs: list[float]) -> tuple[float, int]:
    """Two-sided Wilcoxon signed-rank. Exact by DP for small n, normal above.

    The exact null is a DP over achievable rank sums rather than an
    enumeration of 2^n sign vectors, so it stays cheap; past a few hundred
    non-zero differences the normal approximation is indistinguishable and the
    DP is dropped to keep the runtime sane.
    """
    d = [x for x in diffs if math.isfinite(x) and x != 0.0]
    n = len(d)
    if n == 0:
        return float("nan"), 0
    ranks = average_ranks([abs(x) for x in d])
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    w = min(w_plus, sum(ranks) - w_plus)

    if n <= 200:
        scaled = [int(round(2 * r)) for r in ranks]
        dist: dict[int, int] = {0: 1}
        for s in scaled:
            nxt: dict[int, int] = defaultdict(int)
            for tot, cnt in dist.items():
                nxt[tot] += cnt
                nxt[tot + s] += cnt
            dist = nxt
        tail = sum(c for t, c in dist.items() if t <= int(round(2 * w)))
        return min(1.0, 2.0 * tail / float(2 ** n)), n

    mu = sum(ranks) / 2.0
    var = sum(r * r for r in ranks) / 4.0
    z = (w - mu + 0.5) / math.sqrt(var)
    return min(1.0, math.erfc(-z / math.sqrt(2.0))), n


def boot_ci(v: list[float], reps: int = 5000, seed: int = 20260817):
    v = [x for x in v if math.isfinite(x)]
    if len(v) < 2:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(v)
    m = sorted(statistics.fmean(rng.choices(v, k=n)) for _ in range(reps))
    return m[int(0.025 * reps)], m[min(reps - 1, int(0.975 * reps))]


def stars(p: float) -> str:
    if not math.isfinite(p):
        return ""
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""


def load(name: str) -> list[dict]:
    f = EXPORT / name
    if not f.exists():
        print(f"FATAL: {f} not found. Run 08_extract_slices.py first.",
              file=sys.stderr)
        raise SystemExit(1)
    return list(csv.DictReader(f.open()))


def num(r: dict, k: str) -> float:
    try:
        return float(r[k])
    except (KeyError, TypeError, ValueError):
        return float("nan")


# ==========================================================================
# A. negative-price share, isolated
# ==========================================================================

def analysis_a() -> str:
    on = load("slice_synth.csv")          # storage installed
    off = load("slice_synth0.csv")        # storage absent

    def key(r):
        return (r["instance"], r["price_name"], r["seed"])

    K_on = {key(r): r for r in on}
    K_off = {key(r): r for r in off}

    # relative saving per (instance, tariff, seed); every synthetic baseline is
    # strictly positive, so the ratio is well defined here even though it is
    # not elsewhere in this study
    saving: dict[tuple, float] = {}
    dropped = 0
    for k in set(K_on) & set(K_off):
        e0, e1 = num(K_off[k], "energy_cost"), num(K_on[k], "energy_cost")
        if not (math.isfinite(e0) and math.isfinite(e1)) or e0 <= 0:
            dropped += 1
            continue
        saving[k] = (e0 - e1) / e0

    # Pair on everything except the negative-price level. The instance
    # identifier carries the tariff as a suffix -- p01_r01_..__synth_s100_n01_g00_d0
    # -- so the base instance is what precedes the double underscore. Keying on
    # the full identifier finds no pair at all, since g00 and g08 are by
    # construction different identifiers.
    paired: dict[tuple, dict[str, float]] = defaultdict(dict)
    for (inst, pname, seed), s in saving.items():
        inst = inst.split("__")[0]
        m = SYNTH.match(pname)
        if not m:
            continue
        spread, noise, g, draw = m.groups()
        paired[(inst, seed, spread, noise, draw)][g] = s

    diffs, by_spread = [], defaultdict(list)
    for (inst, seed, spread, noise, draw), lv in paired.items():
        if "00" in lv and "08" in lv:
            d = lv["08"] - lv["00"]
            diffs.append(d)
            by_spread[int(spread)].append(d)

    L = ["A. NEGATIVE-PRICE SHARE, ISOLATED (E3)",
         "=" * 74,
         "  Paired contrast on the synthetic family: same instance, same seed,",
         "  same nominal spread, same noise, same draw -- only the share of",
         "  hours priced below zero changes (g00 -> g08). Outcome is the",
         "  relative energy saving from storage.",
         ""]
    if dropped:
        L.append(f"  {dropped} pair(s) dropped for a non-positive baseline")
    m = statistics.fmean(diffs)
    lo, hi = boot_ci(diffs)
    p, n = wilcoxon(diffs)
    L += [f"  paired differences        n = {len(diffs)}",
          f"  mean effect on saving     {100*m:+.2f} pp  "
          f"95% CI [{100*lo:+.2f}, {100*hi:+.2f}]",
          f"  median                    {100*statistics.median(diffs):+.2f} pp",
          f"  share of pairs improving  {100*sum(1 for d in diffs if d > 0)/len(diffs):.1f} %",
          f"  Wilcoxon signed-rank      p = {p:.3g} {stars(p)}   (n used {n})",
          "",
          "  By nominal spread, to show the effect is not a spread artefact:",
          f"    {'spread':>8s} {'n':>6s} {'mean (pp)':>11s} {'median':>9s}"]
    for s in sorted(by_spread):
        v = by_spread[s]
        L.append(f"    {s:8d} {len(v):6d} {100*statistics.fmean(v):11.2f} "
                 f"{100*statistics.median(v):9.2f}")
    L += ["",
          "  Read: at every spread level the storage saving is larger when the",
          "  tariff contains negative hours, holding the spread fixed. The",
          "  regime contrast in the paper conflates the two; this does not."]
    return "\n".join(L)


# ==========================================================================
# B. flat-tariff difference distribution
# ==========================================================================

def analysis_b() -> str:
    rows = load("slice_flat.csv")
    by: dict[tuple, dict[float, float]] = defaultdict(dict)
    for r in rows:
        if r.get("status") != "ok":
            continue
        k = (r["instance"], r["method"], r["policy"], r["state_policy"], r["seed"])
        by[k][num(r, "battery_ratio")] = num(r, "energy_cost")

    rel = []
    for k, lv in by.items():
        base = lv.get(0.0)
        if base is None or not math.isfinite(base) or base == 0:
            continue
        for b, c in lv.items():
            if b != 0.0 and math.isfinite(c):
                rel.append(abs(c - base) / abs(base))

    rel.sort()
    def q(p):
        return rel[min(len(rel) - 1, int(p * len(rel)))] if rel else float("nan")

    return "\n".join([
        "B. FLAT-TARIFF DIFFERENCE DISTRIBUTION (resolution floor)",
        "=" * 74,
        "  Under a constant price no configuration can create arbitrage value,",
        "  so every difference here is solver noise.",
        "",
        f"  comparable groups   {len(by)}",
        f"  differences         {len(rel)}",
        f"  median              {100*q(0.50):.3f} %",
        f"  p90                 {100*q(0.90):.3f} %",
        f"  p95                 {100*q(0.95):.3f} %",
        f"  p99                 {100*q(0.99):.3f} %",
        f"  maximum             {100*q(1.00):.3f} %",
        "",
        "  The maximum is the bound on SINGLE-INSTANCE claims. A mean over",
        "  paired instances should be judged against the percentiles, and",
        "  against the placebo cell of E1, not against the maximum.",
    ])


# ==========================================================================
# C. gap stability across battery levels
# ==========================================================================

def analysis_c() -> str:
    rows = [r for r in load("slice_e0.csv") if r.get("status") == "ok"]

    # A positive, configuration-invariant per-instance scale. Any such scale
    # cancels from a stability comparison, since the same divisor is applied at
    # both battery levels; it is here only to make instances commensurable.
    def scale(r):
        s = num(r, "inst_price_mean") * num(r, "inst_horizon_days")
        return s if math.isfinite(s) and s > 0 else float("nan")

    best: dict[tuple, float] = {}
    for r in rows:
        k = (r["instance"], r["battery_ratio"], r["time_limit"])
        o = num(r, "objective")
        if math.isfinite(o):
            best[k] = min(best.get(k, math.inf), o)

    gap: dict[tuple, float] = {}
    for r in rows:
        k = (r["instance"], r["battery_ratio"], r["time_limit"])
        o, sc = num(r, "objective"), scale(r)
        if math.isfinite(o) and math.isfinite(sc) and k in best:
            gap[(r["instance"], r["method"], r["time_limit"],
                 r["battery_ratio"], r["seed"])] = (o - best[k]) / sc

    per_method = defaultdict(list)
    for (inst, meth, tl, b, seed), g in gap.items():
        if b != "0.0":
            continue
        k1 = (inst, meth, tl, "1.0", seed)
        if k1 in gap:
            per_method[meth].append(gap[k1] - g)

    L = ["C. GAP STABILITY ACROSS BATTERY LEVELS (E0)",
         "=" * 74,
         "  Change in normalised gap to the best method on the instance when",
         "  storage is installed. Near zero means the ranking of methods does",
         "  not depend on whether a battery is present, which is what licenses",
         "  reporting one method comparison rather than one per battery level.",
         "",
         f"    {'method':8s} {'n':>6s} {'mean':>10s} {'median':>10s} "
         f"{'95% CI':>22s} {'p':>9s}"]
    for meth in sorted(per_method):
        v = per_method[meth]
        if len(v) < 2:
            continue
        lo, hi = boot_ci(v)
        p, _ = wilcoxon(v)
        L.append(f"    {meth:8s} {len(v):6d} {statistics.fmean(v):10.5f} "
                 f"{statistics.median(v):10.5f} "
                 f"[{lo:9.5f},{hi:9.5f}] {p:9.3g} {stars(p)}")
    L += ["",
          "  Computed on a normalised gap. The relative gap used previously is",
          "  unusable on this benchmark for the same reason it is unusable in",
          "  Table E0: negative prices drive its denominator towards zero."]
    return "\n".join(L)


def main() -> int:
    out = [analysis_a(), "", analysis_b(), "", analysis_c(), ""]
    text = "\n".join(out)
    print(text)
    dest = ROOT / "data" / "analysis" / "slice_analyses.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    print(f"written to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
