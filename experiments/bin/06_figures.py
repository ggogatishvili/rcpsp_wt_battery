#!/usr/bin/env python3
"""
Stage 6 — paper figures from data/results.csv.

Outputs vector PDFs into data/figures/, sized for a single column of the
elsarticle two-column layout. No titles: captions belong in LaTeX, and a title
baked into the image duplicates them and cannot be edited during review.

Every figure degrades rather than crashes: if an experiment has no data yet the
figure is skipped with a message, so this can be run against a partial results
table while the cluster is still working.

    python3 bin/06_figures.py              # all figures
    python3 bin/06_figures.py --only e2,e4
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib                                         # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                           # noqa: E402
import numpy as np                                        # noqa: E402

from analysis import analyses as A                        # noqa: E402
from config import economics                              # noqa: E402

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
FIGS = DATA / "figures"

# Colour-blind safe (Okabe-Ito). Greyscale-distinguishable in luminance order.
CB = {"blue": "#0072B2", "orange": "#E69F00", "green": "#009E73",
      "red": "#D55E00", "purple": "#CC79A7", "grey": "#666666",
      "yellow": "#F0E442", "skyblue": "#56B4E9"}
REGIME_COLOUR = {"flat": CB["grey"], "tou2": CB["purple"],
                 "spot_lowvol": CB["skyblue"], "spot_midvol": CB["blue"],
                 "spot_highvol": CB["red"], "synthetic": CB["green"]}

plt.rcParams.update({
    "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 7,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
    "legend.frameon": False, "lines.linewidth": 1.2, "lines.markersize": 3.5,
})
ONE_COL = (3.4, 2.4)
TWO_COL = (7.0, 2.6)


def save(fig, name: str) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(FIGS / f"{name}.{ext}")
    plt.close(fig)
    print(f"  wrote figures/{name}.pdf")


def _ci(v: np.ndarray) -> tuple[float, float]:
    return A.boot_ci(v, n=2000) if len(v) else (float("nan"), float("nan"))


# ---------------------------------------------------------------------------

def fig_e0(rows) -> None:
    """Anytime profile: does the GA/GAP ranking depend on the time budget?"""
    rows = [r for r in rows if r["experiment"] == "E0"
            and r["method"] in ("GA", "GAP")]
    if not rows:
        print("  E0: no data, skipped"); return
    paired = defaultdict(dict)
    for r in rows:
        k = (r["instance"], r["battery_ratio"], int(r["time_limit"]))
        paired[k].setdefault(r["method"], []).append(r["objective"])
    by_tl = defaultdict(list)
    for (inst, b, tl), d in paired.items():
        if "GA" in d and "GAP" in d:
            ga, gp = np.mean(d["GA"]), np.mean(d["GAP"])
            if abs(ga) > 1e-9:
                by_tl[tl].append(100 * (gp - ga) / abs(ga))
    if len(by_tl) < 2:
        print("  E0: fewer than two budgets, skipped"); return

    tls = sorted(by_tl)
    m = [np.mean(by_tl[t]) for t in tls]
    lo = [m_ - _ci(np.array(by_tl[t]))[0] for t, m_ in zip(tls, m)]
    hi = [_ci(np.array(by_tl[t]))[1] - m_ for t, m_ in zip(tls, m)]

    fig, ax = plt.subplots(figsize=ONE_COL)
    ax.axhline(0, color=CB["grey"], lw=0.8, ls="--", zorder=1)
    ax.errorbar(tls, m, yerr=[lo, hi], marker="o", color=CB["blue"],
                capsize=2.5, zorder=3)
    ax.set_xscale("log")
    ax.set_xticks(tls); ax.set_xticklabels([str(t) for t in tls])
    ax.set_xlabel("planning-time budget (s, log scale)")
    ax.set_ylabel("GAP $-$ GA cost (\\%)")
    # The sign is the whole message, so label both sides of zero.
    ax.text(0.02, 0.94, "GAP worse", transform=ax.transAxes, fontsize=6.5,
            color=CB["red"], va="top")
    ax.text(0.02, 0.06, "GAP better", transform=ax.transAxes, fontsize=6.5,
            color=CB["green"], va="bottom")
    save(fig, "fig_e0_anytime")


def fig_e2(rows) -> None:
    """Savings curve and marginal value of storage, by tariff regime."""
    rows = [r for r in rows if r["experiment"] == "E2"]
    if not rows:
        print("  E2: no data, skipped"); return
    cells = A.collapse_seeds(rows, ("instance", "price_regime", "battery_ratio"),
                             how="mean")
    scale = {}
    for r in rows:
        scale.setdefault(r["instance"], A.norm_scale(r))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=TWO_COL)
    for regime in sorted({k[1] for k in cells}):
        insts = sorted({k[0] for k in cells if k[1] == regime})
        ratios = sorted({k[2] for k in cells if k[1] == regime})
        base = {i: cells.get((i, regime, 0.0)) for i in insts}
        xs, ys, es, ns = [], [], [], []
        for b in ratios:
            v = [(base[i] - cells[(i, regime, b)]) / scale[i]
                 for i in insts
                 if base.get(i) and (i, regime, b) in cells
                 and math.isfinite(scale.get(i, float("nan")))]
            if not v:
                continue
            a = np.array(v)
            l, h = _ci(a)
            xs.append(b); ys.append(a.mean()); es.append((a.mean() - l, h - a.mean()))
            ns.append(len(a))
        if not xs:
            continue
        c = REGIME_COLOUR.get(regime, CB["grey"])
        ax1.errorbar(xs, ys, yerr=np.array(es).T, marker="o", color=c,
                     capsize=2, label=regime.replace("spot_", ""))
        mvs = [(ys[i] - ys[i - 1]) / (xs[i] - xs[i - 1])
               for i in range(1, len(xs))]
        ax2.plot(xs[1:], mvs, marker="s", color=c)

    ax1.set_xlabel("$B_{\\max}/E_{\\mathrm{day}}$")
    ax1.set_ylabel("saving (fraction of naive bill)")
    ax1.legend(ncol=1, loc="lower right")
    ax2.set_xlabel("$B_{\\max}/E_{\\mathrm{day}}$")
    ax2.set_ylabel("marginal value per unit capacity")
    ax2.set_yscale("symlog", linthresh=1e-3)
    ax2.axhline(0, color=CB["grey"], lw=0.8, ls="--")
    save(fig, "fig_e2_sizing")


def fig_e2_npv(rows) -> None:
    """Share of instances with NPV>0 over (CAPEX, capacity).

    CAPEX is swept analytically -- NPV is a post-hoc function of the measured
    saving -- so this costs nothing extra in compute and shows how much of the
    investment conclusion is driven by the cost assumption.
    """
    rows = [r for r in rows if r["experiment"] == "E2"]
    if not rows:
        print("  E2-NPV: no data, skipped"); return
    econ = dict(economics.CENTRAL)
    hours_yr = econ["operating_weeks"] * 7 * 24
    cells = A.collapse_seeds(rows, ("instance", "price_regime", "battery_ratio"),
                             how="mean")
    attrs = {}
    for r in rows:
        attrs.setdefault(r["instance"], (float(r.get("inst_horizon", 0) or 0),
                                         float(r.get("inst_e_day", 0) or 0)))
    regime = "spot_midvol" if any(k[1] == "spot_midvol" for k in cells) \
        else sorted({k[1] for k in cells})[0]
    ratios = sorted({k[2] for k in cells if k[1] == regime and k[2] > 0})
    capexes = np.arange(60, 401, 20.0)
    grid = np.full((len(capexes), len(ratios)), np.nan)
    for j, b in enumerate(ratios):
        for i, cap in enumerate(capexes):
            e = dict(econ, capex_eur_per_kwh=float(cap))
            pos = tot = 0
            for inst in {k[0] for k in cells if k[1] == regime}:
                base = cells.get((inst, regime, 0.0))
                cur = cells.get((inst, regime, b))
                h, eday = attrs.get(inst, (0, 0))
                if not base or cur is None or h <= 0 or eday <= 0:
                    continue
                annual = (base - cur) * hours_yr / h
                if A._npv(annual, b * eday, e) > 0:
                    pos += 1
                tot += 1
            if tot:
                grid[i, j] = 100 * pos / tot
    if not np.isfinite(grid).any():
        print("  E2-NPV: no complete cells, skipped"); return

    fig, ax = plt.subplots(figsize=ONE_COL)
    im = ax.imshow(grid, aspect="auto", origin="lower", cmap="viridis",
                   vmin=0, vmax=100,
                   extent=[-0.5, len(ratios) - 0.5, capexes[0], capexes[-1]])
    ax.set_xticks(range(len(ratios)))
    ax.set_xticklabels([f"{b:g}" for b in ratios])
    ax.set_xlabel("$B_{\\max}/E_{\\mathrm{day}}$")
    ax.set_ylabel("turnkey CAPEX (EUR/kWh)")
    cs = ax.contour(np.linspace(0, len(ratios) - 1, len(ratios)), capexes,
                    grid, levels=[50], colors="w", linewidths=1.1)
    ax.clabel(cs, fmt={50: "50%"}, fontsize=6)
    fig.colorbar(im, ax=ax, label="instances with NPV $>$ 0 (\\%)")
    save(fig, "fig_e2_npv")


def fig_e3(rows) -> None:
    """Saving against intra-day spread, with the covariate support visible."""
    rows = [r for r in rows if r["experiment"] == "E3"]
    if not rows:
        print("  E3: no data, skipped"); return
    cells = A.collapse_seeds(rows, ("instance", "battery_ratio"), how="mean")
    meta = {r["instance"]: r for r in rows}
    pts = defaultdict(lambda: ([], []))
    for (inst, b), v in cells.items():
        if b == 0.0:
            continue
        base = cells.get((inst, 0.0))
        sc = A.norm_scale(meta[inst])
        if not base or not math.isfinite(sc):
            continue
        try:
            sp = float(meta[inst]["inst_spread_intraday"])
        except (KeyError, ValueError):
            continue
        reg = meta[inst]["price_regime"]
        pts[reg][0].append(sp)
        pts[reg][1].append((base - v) / sc)
    if not pts:
        print("  E3: no paired cells, skipped"); return

    fig, ax = plt.subplots(figsize=TWO_COL)
    allx = []
    for reg, (x, y) in sorted(pts.items()):
        ax.scatter(x, y, s=3, alpha=0.25, color=REGIME_COLOUR.get(reg, CB["grey"]),
                   label=reg.replace("spot_", ""), edgecolors="none")
        allx += list(x)
    allx = np.array(allx)
    ax.set_xlabel("mean intra-day spread (EUR/MWh)")
    ax.set_ylabel("saving (fraction of naive bill)")
    ax.legend(ncol=3, loc="lower right", markerscale=2.5)

    # Make the support gap explicit: this is what made the original screening
    # rule an extrapolation, so it belongs in the figure rather than a footnote.
    nz = allx[allx > 0]
    if len(nz):
        ax.axvspan(0.5, nz.min(), color=CB["grey"], alpha=0.13, lw=0)
        ax.text(nz.min(), ax.get_ylim()[1], " no tariffs here ",
                fontsize=6, color=CB["grey"], va="top", ha="left")
    save(fig, "fig_e3_spread")


def fig_e4(rows) -> None:
    """The service-energy frontier, with and without storage."""
    rows = [r for r in rows if r["experiment"] == "E4"]
    if not rows:
        print("  E4: no data, skipped"); return
    agg = defaultdict(lambda: {"e": [], "t": []})
    for r in rows:
        try:
            lam = float(r["lam"])
        except (KeyError, ValueError):
            continue
        k = (lam, float(r["battery_ratio"]))
        agg[k]["e"].append(r["energy_cost"])
        agg[k]["t"].append(r["tardiness_cost"])
    series = defaultdict(list)
    for (lam, b), d in agg.items():
        series[b].append((np.mean(d["t"]), np.mean(d["e"]), lam))
    if not series:
        print("  E4: no data, skipped"); return

    fig, ax = plt.subplots(figsize=ONE_COL)
    style = {0.0: (CB["red"], "o", "no storage"),
             1.0: (CB["blue"], "s", "$B=E_{\\mathrm{day}}$")}
    for b, seq in sorted(series.items()):
        seq.sort()
        t = [p[0] for p in seq]; e = [p[1] for p in seq]
        c, mk, lab = style.get(b, (CB["grey"], "^", f"B={b:g}"))
        ax.plot(t, e, marker=mk, color=c, label=lab)
        for tt, ee, lam in seq[::2]:
            ax.annotate(f"$\\lambda$={lam:g}", (tt, ee), fontsize=5.5,
                        xytext=(2, 3), textcoords="offset points", color=c)
    ax.set_xscale("log")
    ax.set_xlabel("total weighted tardiness (log)")
    ax.set_ylabel("grid energy cost")
    ax.legend(loc="upper left")
    save(fig, "fig_e4_frontier")


def fig_e6(rows) -> None:
    """Tornado: technology factors ranked by effect on energy cost."""
    path = DATA / "analysis" / "e6_machine_battery.txt"
    if not path.exists():
        print("  E6: e6_machine_battery.txt not found, skipped"); return
    # Anchor on the "mean [lo, hi]" pattern rather than splitting on ':'.
    # The labels contain both colons and parenthesised numbers
    # ("restart: low->high (rho=0.5, policy=edd)  16.794 [ 11.978, 22.799]"),
    # so a positional parse picks up rho=0.5 as the effect size.
    import re
    pat = re.compile(r"^(?P<label>.+?)\s+(?P<mean>-?\d+(?:\.\d+)?)\s*"
                     r"\[\s*(?P<lo>-?\d+(?:\.\d+)?)\s*,\s*"
                     r"(?P<hi>-?\d+(?:\.\d+)?)\s*\]")
    facs = []
    for line in path.read_text().splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        lo, hi = float(m["lo"]), float(m["hi"])
        mean = float(m["mean"])
        if not (lo <= mean <= hi):        # guards against a mis-parse
            continue
        facs.append((m["label"].strip(), mean, lo, hi))
    if not facs:
        print("  E6: no parsable tornado rows, skipped"); return
    facs.sort(key=lambda f: abs(f[1]))

    fig, ax = plt.subplots(figsize=TWO_COL)
    y = np.arange(len(facs))
    m = [f[1] for f in facs]
    err = [[f[1] - f[2] for f in facs], [f[3] - f[1] for f in facs]]
    cols = [CB["red"] if v > 0 else CB["blue"] for v in m]
    ax.barh(y, m, xerr=err, color=cols, height=0.6, error_kw={"lw": 0.8})
    ax.axvline(0, color=CB["grey"], lw=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels([f[0][:46] for f in facs], fontsize=6.5)
    ax.set_xlabel("effect on energy cost (\\%)")
    save(fig, "fig_e6_tornado")


def _isnum(x: str) -> bool:
    try:
        float(x); return True
    except ValueError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list, e.g. e2,e4")
    args = ap.parse_args()

    res = DATA / "results.csv"
    if not res.exists():
        print("FATAL: run 04_collect.py first", file=sys.stderr)
        return 1
    rows = A.load_results(res)
    print(f"loaded {len(rows)} successful runs")

    want = set(args.only.split(",")) if args.only else \
        {"e0", "e2", "e2npv", "e3", "e4", "e6"}
    if "e0" in want: fig_e0(rows)
    if "e2" in want: fig_e2(rows)
    if "e2npv" in want: fig_e2_npv(rows)
    if "e3" in want: fig_e3(rows)
    if "e4" in want: fig_e4(rows)
    if "e6" in want: fig_e6(rows)
    print(f"\nfigures in {FIGS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
