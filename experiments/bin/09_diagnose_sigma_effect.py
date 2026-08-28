#!/usr/bin/env python3
"""
Stage 9 — why is sigma_effect enormous, and is the MR verdict trustworthy?

WHAT PROVOKED THIS. MR came back with

    sigma_seed   =  1.91 %      (small, and exactly what MR is for)
    sigma_effect = 95.51 %      (nearly fifty times larger)
    rho          =  0.000       (point estimates -0.067 .. 0.579)
    verdict      = MORE INSTANCES on all five experiments, k = inf

and a footer instructing the reader to copy the `k required` column into
`design.SEEDS_PER_EXP`. That column is `inf`. Do not copy it. `inf` is not a
seed count, it is the arithmetic saying the target is unreachable *by seeds*,
which is a statement about the TARGET, not about the campaign.

Three explanations produce that output, and they demand opposite responses:

  (A) MR ran on a broken campaign. T1_ideal failed 100 % of its runs, and
      MR_ARCHETYPES includes T1. If the T1 rows are missing, one third of every
      MR cell is gone, the paired contrasts silently changed sample, and every
      number above is an artefact. Response: re-run MR, discard this output.

  (B) The metric is wrong. `paired_effect_sd` defaults to value="objective",
      and the objective is energy + lambda x tardiness. Tardiness variation has
      nothing to do with the energy bill but is divided by `norm_scale`, an
      ENERGY scale. An instance whose tardiness swings between battery levels
      then contributes a huge "effect" in energy units. Response: measure the
      effects the paper actually reports -- which are energy -- and re-read.

  (C) The effect really is that heterogeneous across instances. Response:
      keep it, and redeclare the MDE honestly in the paper.

(C) is a finding. (A) and (B) are bugs. This script decides which, by
recomputing the same decomposition three ways on the same rows.

    python3 bin/09_diagnose_sigma_effect.py
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design                              # noqa: E402
from analysis import replication as R                  # noqa: E402

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))

# The contrasts MR reports on. Battery level is the paper's central treatment,
# so it is the one whose variance decides the seed count.
CONTRASTS = [
    ("battery_ratio", str(design.BATTERY_ON_RATIO), "0.0"),
    ("battery_ratio", "1.0", "0.0"),
    ("battery_ratio", "1.0", str(design.BATTERY_ON_RATIO)),
]

VALUES = [
    ("objective", "energy + lambda x tardiness -- what MR used"),
    ("energy_cost", "energy only -- what M1-M5 actually report"),
    ("tardiness_cost", "tardiness only -- should be near zero if (B) is wrong"),
]


def load() -> list[dict]:
    p = DATA / "results.csv"
    if p.exists():
        return list(csv.DictReader(p.open()))

    # "run 04_collect.py first" is the right advice exactly once. When the data
    # directory is simply not the one that holds the campaign -- a fresh shell
    # that lost the export, a login node instead of the compute node, a
    # relative RCPSP_EXP_DATA resolved against a different cwd -- that advice
    # sends you to re-run a stage that will fail for the same reason. So say
    # what was actually looked at.
    print(f"FATAL: {p} not found", file=sys.stderr)
    print(f"  RCPSP_EXP_DATA = {os.environ.get('RCPSP_EXP_DATA', '(unset)')}",
          file=sys.stderr)
    print(f"  resolved to      {DATA.resolve()}", file=sys.stderr)
    if DATA.exists():
        kids = sorted(x.name for x in DATA.iterdir())[:12]
        print(f"  that directory exists and contains: "
              f"{kids if kids else '(empty)'}", file=sys.stderr)
    else:
        print("  that directory DOES NOT EXIST", file=sys.stderr)
    here = [c for c in (ROOT / "data", ROOT.parent / "data_v2",
                        Path.home() / "data_v2")
            if (c / "manifest_instances.csv").exists()]
    if here:
        print("\n  a campaign directory WAS found elsewhere:", file=sys.stderr)
        for c in here:
            print(f"    export RCPSP_EXP_DATA={c.resolve()}", file=sys.stderr)
    else:
        print("\n  no manifest_instances.csv in the usual places either. Locate "
              "it with:\n    find $HOME -maxdepth 4 -name manifest_instances.csv "
              "2>/dev/null", file=sys.stderr)
    sys.exit(1)


def describe(x: np.ndarray) -> str:
    """Where the variance lives, not just how big it is."""
    if x.size == 0:
        return "(empty)"
    q = np.percentile(x, [1, 5, 25, 50, 75, 95, 99])
    return (f"n={x.size}  mean={x.mean():+.3f}  sd={x.std(ddof=1):.3f}\n"
            f"      p1={q[0]:+.2f}  p5={q[1]:+.2f}  p25={q[2]:+.2f}  "
            f"med={q[3]:+.2f}  p75={q[4]:+.2f}  p95={q[5]:+.2f}  p99={q[6]:+.2f}\n"
            f"      min={x.min():+.2f}  max={x.max():+.2f}  IQR={q[4]-q[2]:.3f}")


def tail_share(x: np.ndarray) -> tuple[float, float]:
    """Fraction of total squared deviation carried by the extreme 1 % of cells.

    THE QUESTION THIS ANSWERS. A standard deviation of 95 % is produced either
    by every instance differing a lot, or by a handful of instances differing
    absurdly. Those are different papers. If 1 % of the cells carry most of the
    sum of squares, sigma_effect is describing outliers, and the outliers are
    almost always a metric artefact rather than a plant that behaves that way.
    """
    if x.size < 100:
        return float("nan"), float("nan")
    d2 = (x - x.mean()) ** 2
    cut = np.percentile(np.abs(x - x.mean()), 99)
    tail = d2[np.abs(x - x.mean()) >= cut]
    sd_wo = float(np.std(x[np.abs(x - x.mean()) < cut], ddof=1))
    return float(tail.sum() / d2.sum()), sd_wo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mde", type=float, default=design.MDE_TARGET_PCT)
    args = ap.parse_args()

    rows = load()
    L = ["sigma_effect diagnosis", "=" * 78, ""]

    # ---- (A) did MR run on a repaired campaign? --------------------------
    mr = [r for r in rows if r.get("experiment") == "MR"]
    L += ["-" * 78,
          "A. Was MR measured on a complete design?",
          "-" * 78,
          "  MR_ARCHETYPES includes T1_ideal, the profile whose runs all failed.",
          "  If T1 is absent or thin here, every number MR printed describes a",
          "  design missing one third of its machine factor, and the only correct",
          "  response is to re-run MR — not to change the seed counts.",
          ""]
    if not mr:
        L.append("  !! no rows with experiment == MR in results.csv")
    else:
        by_prof = Counter(r.get("machine_profile", "") for r in mr)
        expected = len(mr) / max(1, len(by_prof))
        for prof, n in sorted(by_prof.items()):
            flag = "" if n >= 0.9 * expected else "   <-- THIN"
            L.append(f"    {prof:<20s} {n:6d} rows{flag}")
        want = set(design.MR_ARCHETYPES)
        got = set(by_prof)
        if want - got:
            L += ["", f"  !! MISSING ENTIRELY: {sorted(want - got)}",
                  "  This output predates the T1 fix. Stop here: re-run MR, then",
                  "  re-run this script. Nothing below is interpretable yet."]
        elif min(by_prof.values()) < 0.9 * expected:
            L += ["", "  !! one profile is materially thinner than the others.",
                  "  Check 04b_diagnose_failures.py before reading section C."]
        else:
            L += ["", "  OK — the machine factor is balanced in MR."]

    # ---- (B) which metric ------------------------------------------------
    L += ["", "-" * 78,
          "B. The same contrast, measured three ways",
          "-" * 78,
          "  `sd_effect` is the instance-to-instance spread of the paired",
          "  difference, in percent of norm_scale — an ENERGY scale. If the",
          "  objective column is far larger than the energy column, the extra",
          "  spread is tardiness being divided by an energy denominator, and",
          "  sigma_effect is measuring the wrong thing for every effect the",
          "  paper reports.",
          ""]
    # Same population MR itself analyses: successful GA runs only. Comparing
    # against anything else would make a difference here look like a metric
    # effect when it is only a different sample.
    src = [r for r in (mr if mr else rows)
           if r.get("status", "ok") == "ok"
           and (r.get("method") in ("GA", None, "") or not r.get("method"))]
    if not src:
        src = mr if mr else rows

    # k is the number of seeds actually present per cell. Passing k=1 here
    # would inflate the noise term by the full seed count and drive every
    # sd_effect to zero -- the decomposition subtracts 2 sigma^2 (1-rho) / k,
    # so k must be the k the data were averaged over, not a placeholder.
    k_obs = _seeds_per_cell(src)
    L.append(f"  (seeds per cell, median: k = {k_obs})")
    L.append("")

    summary: dict[tuple, dict] = {}
    for value, why in VALUES:
        if not any(r.get(value, "") not in ("", None) for r in src):
            L.append(f"  {value}: column absent from results.csv — skipped")
            continue
        L += [f"  value = {value}   ({why})"]
        for factor, a, b in CONTRASTS:
            d = R.paired_effect_sd(src, factor, a, b, k=k_obs, value=value)
            summary[(value, a, b)] = d
            if not math.isfinite(d.get("sd_effect", float("nan"))):
                L.append(f"    {a} vs {b:<6s}  {d.get('note', 'not estimable')}")
                continue
            L.append(f"    {a} vs {b:<6s}  n={d['n']:5d}  "
                     f"mean={d['mean']:+9.3f}  sd_obs={d['sd_observed']:9.3f}  "
                     f"sd_effect={d['sd_effect']:9.3f}  "
                     f"sig_local={d.get('sigma_seed_local', float('nan')):7.3f}  "
                     f"rho={d.get('rho_crn', float('nan')):+.3f}  "
                     f"noise={100*d.get('noise_share', float('nan')):5.1f}%")
            if d.get("note"):
                L.append(f"        note: {d['note']}")
        L.append("")

    def _best(value: str, field: str) -> float:
        xs = [summary[k][field] for k in summary if k[0] == value
              and math.isfinite(summary[k].get(field, float("nan")))]
        return max(xs) if xs else float("nan")

    o_eff, e_eff = _best("objective", "sd_effect"), _best("energy_cost", "sd_effect")
    o_obs, e_obs = _best("objective", "sd_observed"), _best("energy_cost", "sd_observed")
    o_sig, e_sig = _best("objective", "sigma_seed_local"), _best("energy_cost", "sigma_seed_local")
    if math.isfinite(o_obs) and math.isfinite(e_obs):
        L += ["                        objective    energy_cost      ratio",
              f"    sd_observed      {o_obs:11.3f} {e_obs:14.3f} "
              f"{o_obs/e_obs if e_obs else float('inf'):10.1f}x",
              f"    sd_effect        {o_eff:11.3f} {e_eff:14.3f} "
              f"{o_eff/e_eff if e_eff else float('inf'):10.1f}x",
              f"    sigma_seed_local {o_sig:11.3f} {e_sig:14.3f} "
              f"{o_sig/e_sig if e_sig else float('inf'):10.1f}x", ""]
        # The artefact shows up in EITHER term. If tardiness is noisy across
        # seeds it lands in sigma_seed_local; if it is heterogeneous across
        # instances it lands in sd_effect. Both are "the objective is not the
        # energy bill", and both invalidate a seed count derived from it.
        infl = max((o_obs / e_obs) if e_obs else 0.0,
                   (o_sig / e_sig) if e_sig else 0.0)
        if infl > 3:
            L += [f"  VERDICT (B): the objective carries {infl:.0f}x the spread of the",
                  "  energy cost. That excess is tardiness measured against an",
                  "  ENERGY denominator (norm_scale). Every managerial claim in",
                  "  M1-M5 is about energy, so the campaign was sized against a",
                  "  quantity it does not report. Re-run MR on energy_cost.", ""]
        else:
            L += ["  VERDICT (B): objective and energy_cost agree to within a",
                  "  factor of three. The metric is not the problem; read C.", ""]

    # ---- (C) is it outliers or genuine heterogeneity? --------------------
    L += ["-" * 78,
          "C. Is the spread the whole population, or a few cells?",
          "-" * 78, ""]
    for value in ("energy_cost", "objective"):
        key = [k for k in summary if k[0] == value]
        if not key:
            continue
        factor, a, b = CONTRASTS[1]
        diffs = _diffs(src, factor, a, b, value)
        if diffs.size == 0:
            continue
        share, sd_wo = tail_share(diffs)
        L += [f"  value = {value},  contrast {a} vs {b}",
              "      " + describe(diffs)]
        if math.isfinite(share):
            L += [f"      extreme 1 % of cells carry {100*share:5.1f} % of the "
                  f"total sum of squares",
                  f"      sd excluding them: {sd_wo:.3f}"]
            if share > 0.5:
                L.append("      -> sigma_effect is an OUTLIER statistic, not a "
                         "population one.")
        L.append("")

    # ---- C2: is the spread one population or two? ------------------------
    # THE SIGNATURE THAT PROVOKED THIS SECTION. The energy contrast came back
    # with p75 = p95 = p99 = max = +0.00 -- more than a quarter of the cells
    # differ by EXACTLY zero, and nothing is above zero. A metaheuristic does
    # not return bit-identical costs by accident twelve seeds running; an exact
    # zero means the battery was never cycled. MR_TARIFFS includes `flat`,
    # where no arbitrage exists by construction, so the obvious candidate is
    # that MR is averaging a placebo population and a treated one together.
    #
    # If so, `sigma_effect` is not the variability of an effect. It is mostly
    # the DISTANCE between a group with no effect and a group with a large one,
    # and sizing a campaign against it is sizing against a design choice.
    L += ["-" * 78,
          "C2. Exact zeros, and whether the sample is one population or two",
          "-" * 78, ""]
    strata_cols = [c for c in ("tariff_family", "price_regime", "price_name")
                   if any(r.get(c) for r in src)]
    for value in ("energy_cost", "objective"):
        factor, a, b = CONTRASTS[1]
        by_stratum = _diffs_by(src, factor, a, b, value, strata_cols)
        if not by_stratum:
            continue
        L.append(f"  value = {value},  contrast {a} vs {b}")
        L.append(f"    {'stratum':<34s} {'n':>5s} {'zeros':>7s} "
                 f"{'mean':>10s} {'sd':>9s}")
        # Placebo strata are EXCLUDED from the pooled figure. A stratum whose
        # SD is structurally zero (flat tariff: no arbitrage exists) is not a
        # low-variance sample of the same population, it is a different
        # population. Pooling it in drags the planning sigma down and
        # under-powers the design -- on the first campaign it turned 30.2 into
        # 24.6, a 19 % under-statement of what has to be resolved.
        pooled_within, tot_n = 0.0, 0
        for name, x in sorted(by_stratum.items()):
            z = int(np.sum(np.abs(x) < 1e-9))
            sd = float(np.std(x, ddof=1)) if x.size > 1 else float("nan")
            placebo = math.isfinite(sd) and sd < 1e-6 and abs(x.mean()) < 1e-6
            L.append(f"    {name:<34s} {x.size:5d} {100*z/x.size:6.1f}% "
                     f"{x.mean():+10.3f} {sd:9.3f}"
                     + ("   [placebo, excluded]" if placebo else ""))
            if x.size > 1 and math.isfinite(sd) and not placebo:
                pooled_within += (x.size - 1) * sd ** 2
                tot_n += x.size - 1
        if tot_n:
            sd_within = math.sqrt(pooled_within / tot_n)
            allx = np.concatenate(list(by_stratum.values()))
            sd_all = float(np.std(allx, ddof=1))
            L += ["",
                  f"    sd pooled WITHIN treated strata {sd_within:9.3f}",
                  f"    sd across the whole set         {sd_all:9.3f}",
                  f"    between-strata share      "
                  f"{100*max(0.0, 1 - sd_within**2/sd_all**2):8.1f} % of the variance"]
            if sd_all > 0 and 1 - sd_within ** 2 / sd_all ** 2 > 0.25:
                L += ["",
                      "    -> a quarter or more of `sigma_effect` is the gap",
                      "       BETWEEN tariff strata, not variability of the",
                      "       effect within any of them. Size the campaign on",
                      "       the within-stratum sigma; report the placebo",
                      "       stratum as the falsification check it is, and",
                      "       never pool the two into one variance."]
        L.append("")

    # ---- what the campaign can actually resolve --------------------------
    L += ["-" * 78,
          "D. What each experiment can resolve, at the sigma you decide to use",
          "-" * 78,
          "  MDE = POWER_Z x sigma_effect / sqrt(n). Seeds do not appear: that",
          "  is the whole content of the `MORE INSTANCES` verdict. The columns",
          "  below are what the campaign resolves at each candidate sigma, and",
          "  the instance count the 0.5 % target would need.",
          ""]
    cands = []
    if math.isfinite(e_eff) and e_eff > 0:
        cands.append(("energy_cost, largest contrast", e_eff))
    if math.isfinite(o_eff) and o_eff > 0:
        cands.append(("objective, largest contrast (what MR used)", o_eff))
    n_by_exp = _instance_counts()
    for label, sig in cands:
        L.append(f"  sigma_effect = {sig:.3f}   ({label})")
        need = (design.POWER_Z * sig / args.mde) ** 2 if args.mde > 0 else float("inf")
        L.append(f"    instances needed for MDE = {args.mde} % : {need:,.0f}")
        for exp, n in sorted(n_by_exp.items()):
            if n <= 0:
                continue
            mde = design.POWER_Z * sig / math.sqrt(n)
            L.append(f"      {exp:<4s} n={n:6d}   resolvable effect >= {mde:8.3f} %")
        L.append("")

    L += ["-" * 78,
          "E. What to do",
          "-" * 78,
          "  1. If section A says MR predates the T1 fix: re-run MR. Stop.",
          "  2. If section B says the objective carries the spread: the seed",
          "     calculation must be redone on energy_cost, because that is the",
          "     quantity every managerial claim is about.",
          "  3. If section C says the extreme 1 % carries the variance: report",
          "     the effect with a robust scale and say so, rather than sizing a",
          "     campaign against a tail.",
          "  4. Only if A, B and C all come back clean is `MORE INSTANCES` a",
          "     finding. Then the honest move is to declare in the paper the",
          "     effect size the design CAN resolve (section D) instead of the",
          "     0.5 % that was assumed before any variance was known.",
          "",
          "  Under no circumstances copy `inf` into design.SEEDS_PER_EXP."]

    out = DATA / "sigma_effect_diagnosis.txt"
    out.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nwritten to {out}")
    return 0


def _diffs(rows: list[dict], factor: str, a: str, b: str,
           value: str) -> np.ndarray:
    """The paired differences themselves, which paired_effect_sd only summarises."""
    scales: dict[str, float] = {}
    import analysis.analyses as A
    for r in rows:
        i = r.get("instance", "")
        if i not in scales:
            scales[i] = A.norm_scale(r)
    other = [c for c in ("instance", "battery_ratio", "machine_profile",
                         "state_policy", "time_limit", "price_name")
             if c != factor]
    cells: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        s = scales.get(r.get("instance", ""), float("nan"))
        try:
            v = float(r.get(value, ""))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(s) and s > 0 and math.isfinite(v)):
            continue
        cells[tuple(str(r.get(c, "")) for c in other)][
            str(r.get(factor, ""))].append(100.0 * v / s)
    out = []
    for lv in cells.values():
        if lv.get(a) and lv.get(b):
            out.append(float(np.mean(lv[a]) - np.mean(lv[b])))
    return np.asarray(out)


def _diffs_by(rows: list[dict], factor: str, a: str, b: str, value: str,
              strata_cols: list[str]) -> dict[str, np.ndarray]:
    """Paired differences, split by tariff stratum.

    Same construction as `_diffs`, but each cell also carries the stratum it
    came from so the pooled variance can be decomposed into within and between.
    """
    import analysis.analyses as A
    scales: dict[str, float] = {}
    for r in rows:
        i = r.get("instance", "")
        if i not in scales:
            scales[i] = A.norm_scale(r)
    other = [c for c in ("instance", "battery_ratio", "machine_profile",
                         "state_policy", "time_limit", "price_name")
             if c != factor]
    cells: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    stratum_of: dict[tuple, str] = {}
    for r in rows:
        s = scales.get(r.get("instance", ""), float("nan"))
        try:
            v = float(r.get(value, ""))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(s) and s > 0 and math.isfinite(v)):
            continue
        key = tuple(str(r.get(c, "")) for c in other)
        cells[key][str(r.get(factor, ""))].append(100.0 * v / s)
        stratum_of.setdefault(
            key, " / ".join(str(r.get(c, "")) for c in strata_cols) or "(all)")
    out: dict[str, list] = defaultdict(list)
    for key, lv in cells.items():
        if lv.get(a) and lv.get(b):
            out[stratum_of[key]].append(float(np.mean(lv[a]) - np.mean(lv[b])))
    return {k: np.asarray(v) for k, v in out.items() if len(v) >= 2}


def _seeds_per_cell(rows: list[dict]) -> int:
    """Median number of distinct seeds in a fully-specified cell.

    The decomposition needs the k the observations were averaged over. Reading
    it from the data rather than from `design.SEEDS_PER_EXP` is deliberate: the
    two disagree exactly when something went wrong, and this script exists for
    those occasions.
    """
    cells: dict[tuple, set] = defaultdict(set)
    for r in rows:
        key = tuple(str(r.get(c, "")) for c in
                    ("instance", "battery_ratio", "machine_profile",
                     "state_policy", "time_limit", "price_name"))
        if r.get("seed", "") != "":
            cells[key].add(str(r["seed"]))
    if not cells:
        return 1
    return max(1, int(np.median([len(v) for v in cells.values()])))


def _instance_counts() -> dict[str, int]:
    """Instance counts per experiment, from the runlist if present."""
    p = DATA / "runlist.csv"
    if not p.exists():
        return {}
    seen: dict[str, set] = defaultdict(set)
    for r in csv.DictReader(p.open()):
        exp = (r.get("experiment") or "").split(".")[0]
        if r.get("instance"):
            seen[exp].add(r["instance"])
    return {k: len(v) for k, v in seen.items() if k.startswith("M")}


if __name__ == "__main__":
    raise SystemExit(main())
