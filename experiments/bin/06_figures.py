#!/usr/bin/env python3
"""
Stage 6 — figures, campaign v2.

WHY THIS FILE WAS REWRITTEN. The previous version drew E0, E2, E3, E4 and E6:
the experiments of campaign v1. v2 runs MR and M0-M5, so every figure reported
"no data, skipped" on a complete, successful campaign. Nothing was broken and
nothing was missing -- the generator was simply looking for experiments that no
longer exist.

WHAT IT DRAWS. One figure per managerial claim, in the order the paper makes
them, each reading the CSV that `05_analyse.py` already wrote. Reading the CSVs
rather than re-deriving from `results.csv` is deliberate: the analysis owns the
estimator (pairing, bootstrap CIs, cluster-robust SEs, the norm_scale
denominator), and a figure that recomputed any of it could disagree with the
table beside it in the paper. If a number in a figure looks wrong, the fix
belongs in `analysis/`, not here.

  F1  saving vs capacity, by archetype and tariff     insights (a) and (c)
  F2  ROI surface: b* and NPV by archetype x tariff   insight  (a)
  F3  the orthogonal (rho, restart) grid              insight  (e)
  F4  saving vs price spread, synthetic and real      insight  (b)
  F5  saving as the shop grows                        insight  (d)
  F6  substitution: storage against state management  supporting mechanism
  F7  the energy/tardiness frontier                   supporting mechanism
  F8  GA against the compact MILP                     licences the GA
  F9  seed replication                                licences every CI

COLOUR. The categorical palette is Okabe-Ito minus yellow and grey, in a fixed
order, validated for colour-vision deficiency (worst adjacent pair dE 9.6
deutan, 16.4 normal). The order is FIXED: T1 is always blue, T5 is always
purple, whichever subset of archetypes a given figure happens to show. Every
series also carries a distinct marker, so identity survives a black-and-white
print and the sub-3:1 contrast of the lighter hues.

Signed quantities (savings, which can be negative) use a diverging map centred
on zero with a neutral midpoint; unsigned magnitudes use a single-hue ramp.
Never a rainbow, and never two y-axes.

    python3 bin/06_figures.py
    python3 bin/06_figures.py --only F1,F4
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
import numpy as np                                       # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
ANALYSIS = DATA / "analysis"
FIG = DATA / "figures"

# Fixed categorical order. Validated: see the module docstring.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9"]
MARKERS = ["o", "s", "^", "D", "v", "P"]
INK = "#1a1a1a"
MUTED = "#666666"

# Sequential (magnitude) and diverging (signed) maps, built from the palette's
# own hues so figures read as one system.
SEQ = LinearSegmentedColormap.from_list("seq", ["#f2f7fb", "#0072B2", "#00304d"])
DIV = LinearSegmentedColormap.from_list("div", ["#8c3b00", "#D55E00", "#f2f2f2",
                                                "#4da2c9", "#0072B2"])

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 300, "savefig.bbox": "tight",
    "font.size": 9, "axes.labelsize": 9, "axes.titlesize": 9.5,
    "legend.fontsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#e6e6e6", "grid.linewidth": 0.6,
    "lines.linewidth": 1.6, "lines.markersize": 4.5,
    "legend.frameon": False,
})


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------

def read(name: str) -> list[dict]:
    p = ANALYSIS / name
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def num(row: dict, key: str, default: float = float("nan")) -> float:
    try:
        v = row.get(key, "")
        return default if v in ("", None) else float(v)
    except (TypeError, ValueError):
        return default


_WRITTEN: list[str] = []


def save(fig, name: str) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(FIG / f"{name}.{ext}")
    plt.close(fig)
    _WRITTEN.append(name)
    print(f"  {name}: written")


def skip(name: str, why: str) -> None:
    print(f"  {name}: {why}, skipped")


def mean_ci(values: list[float], n_boot: int = 2000, seed: int = 12345):
    """Mean and a 95 % percentile bootstrap interval.

    The analysis clusters by shop where it matters; a figure's error bar is a
    reading aid, not the inferential claim, so a plain instance bootstrap is
    honest here PROVIDED the caption says so. It does.
    """
    x = np.asarray([v for v in values if math.isfinite(v)], dtype=float)
    if x.size == 0:
        return float("nan"), float("nan"), float("nan")
    if x.size < 3:
        return float(x.mean()), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    b = x[rng.integers(0, x.size, size=(n_boot, x.size))].mean(axis=1)
    return float(x.mean()), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def level_key(x):
    """Sort levels numerically when they are numbers, alphabetically otherwise."""
    try:
        return (0, float(x), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(x))


# Tariff regimes have a MEANING order -- increasing volatility -- and it is not
# the alphabetical one (which interleaves highvol before lowvol). Every figure
# that puts regimes on an axis uses this, so the reader always scans left to
# right from "no arbitrage possible" to "as volatile as the year gets".
_REGIME_ORDER = ["flat", "contractual", "tou", "spot_lowvol", "spot_midvol",
                 "spot_highvol"]


def regime_key(x) -> tuple:
    s = str(x)
    for i, name in enumerate(_REGIME_ORDER):
        if s == name or s.startswith(name):
            return (0, i, s)
    return (1, 0, s)        # real market-years and anything new, after


def cell_ink(rgba) -> str:
    """Black or white, whichever the cell's own fill can carry.

    A fixed ink colour on a heatmap is an anti-pattern: the dark end of any
    sequential ramp swallows dark text, and the light end swallows white. The
    relative-luminance test is cheap and removes the judgement call.
    """
    r, g, b = rgba[:3]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    return INK if lum > 0.42 else "#ffffff"


def style(i: int) -> dict:
    return {"color": PALETTE[i % len(PALETTE)],
            "marker": MARKERS[i % len(MARKERS)]}


# ---------------------------------------------------------------------------
# F1 — saving vs capacity, by archetype, faceted by tariff regime
# ---------------------------------------------------------------------------

def f1_capacity() -> None:
    rows = read("m1_cube_savings.csv")
    if not rows:
        return skip("F1", "m1_cube_savings.csv not found")

    regimes = sorted({r.get("regime", "") for r in rows if r.get("regime")},
                     key=regime_key)
    archs = sorted({r["machine_profile"] for r in rows if r.get("machine_profile")},
                   key=level_key)
    if not regimes or not archs:
        return skip("F1", "no archetype or regime column")

    fig, axes = plt.subplots(1, len(regimes), figsize=(3.1 * len(regimes), 3.0),
                             sharey=True, squeeze=False)
    for ax, reg in zip(axes[0], regimes):
        sub = [r for r in rows if r.get("regime") == reg]
        for i, arch in enumerate(archs):
            pts = defaultdict(list)
            for r in sub:
                if r.get("machine_profile") == arch:
                    pts[num(r, "battery_ratio")].append(num(r, "saving"))
            bs = sorted(b for b in pts if math.isfinite(b))
            if not bs:
                continue
            m = [mean_ci(pts[b]) for b in bs]
            y = [t[0] for t in m]
            lo = [t[0] - t[1] if math.isfinite(t[1]) else 0.0 for t in m]
            hi = [t[2] - t[0] if math.isfinite(t[2]) else 0.0 for t in m]
            ax.errorbar(bs, y, yerr=[lo, hi], capsize=2, elinewidth=0.8,
                        label=arch, **style(i))
        ax.axhline(0, color=MUTED, lw=0.8, ls=":")
        ax.set_title(reg)
    axes[0][0].set_ylabel("saving  (% of naive energy bill)")
    # One shared x label, not one per facet: repeated four times it collides
    # with its neighbours and the last one is clipped.
    fig.tight_layout()
    fig.supxlabel("battery capacity  $b$  (multiples of $E_{day}$)", fontsize=9,
                  y=-0.04)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, title="machine", loc="center left",
               bbox_to_anchor=(1.005, 0.5))
    fig.suptitle("Return on storage rises with capacity, and the machine's "
                 "transition graph sets how fast", y=1.06, fontsize=10)
    save(fig, "f1_saving_by_capacity")


# ---------------------------------------------------------------------------
# F2 — ROI surface: optimal capacity and NPV, archetype x tariff
# ---------------------------------------------------------------------------

def f2_roi_surface() -> None:
    rows = read("m1_roi_surface.csv")
    if not rows:
        return skip("F2", "m1_roi_surface.csv not found")
    scen = {r.get("scenario", "") for r in rows}
    central = "central" if "central" in scen else sorted(scen)[0]
    rows = [r for r in rows if r.get("scenario") == central]
    if not rows:
        return skip("F2", "no rows for the central scenario")

    archs = sorted({r["archetype"] for r in rows}, key=level_key)
    tars = sorted({r["tariff"] for r in rows}, key=regime_key)
    if len(archs) < 2 or len(tars) < 2:
        return skip("F2", "needs at least a 2x2 surface")

    def grid(field):
        M = np.full((len(archs), len(tars)), np.nan)
        for r in rows:
            M[archs.index(r["archetype"]), tars.index(r["tariff"])] = num(r, field)
        return M

    bstar, npv = grid("b_star"), grid("npv_kEUR")
    fig, axes = plt.subplots(1, 2, figsize=(3.9 * 2, 0.38 * len(archs) + 2.2))

    # Left: optimal capacity — an unsigned magnitude, so a single-hue ramp.
    im0 = axes[0].imshow(bstar, cmap=SEQ, aspect="auto")
    axes[0].set_title("optimal capacity $b^*$")
    fig.colorbar(im0, ax=axes[0], fraction=0.046, pad=0.04,
                 label="$b^*$  ($E_{day}$)")

    # Right: NPV — signed, so diverging with a neutral zero.
    fin = npv[np.isfinite(npv)]
    if fin.size and fin.min() < 0 < fin.max():
        norm = TwoSlopeNorm(vmin=fin.min(), vcenter=0.0, vmax=fin.max())
        im1 = axes[1].imshow(npv, cmap=DIV, norm=norm, aspect="auto")
    else:
        im1 = axes[1].imshow(npv, cmap=SEQ, aspect="auto")
    axes[1].set_title("NPV at $b^*$")
    fig.colorbar(im1, ax=axes[1], fraction=0.046, pad=0.04, label="kEUR")

    for k, (ax, M, im, fmt) in enumerate(((axes[0], bstar, im0, "{:.2f}"),
                                          (axes[1], npv, im1, "{:.0f}"))):
        ax.set_xticks(range(len(tars)), tars, rotation=30, ha="right")
        # Row labels once, on the left panel only: repeating them puts a second
        # column of text between the first colourbar and the second heatmap.
        if k == 0:
            ax.set_yticks(range(len(archs)), archs)
        else:
            ax.set_yticks(range(len(archs)), [""] * len(archs))
        ax.grid(False)
        for i in range(len(archs)):
            for j in range(len(tars)):
                if math.isfinite(M[i, j]):
                    ax.text(j, i, fmt.format(M[i, j]), ha="center", va="center",
                            fontsize=7, color=cell_ink(im.cmap(im.norm(M[i, j]))))
    fig.tight_layout()
    fig.suptitle(f"Where storage pays, and how much  (economics: {central})",
                 y=1.04, fontsize=10)
    save(fig, "f2_roi_surface")


# ---------------------------------------------------------------------------
# F3 — the orthogonal (rho, restart) grid
# ---------------------------------------------------------------------------

def f3_machine_grid() -> None:
    rows = read("m1b_grid_savings.csv")
    if not rows:
        return skip("F3", "m1b_grid_savings.csv not found")
    rhos = sorted({r["rho"] for r in rows if r.get("rho")}, key=level_key)
    rests = sorted({r["restart_level"] for r in rows if r.get("restart_level")},
                   key=level_key)
    if not rhos or not rests:
        return skip("F3", "grid factors absent")

    bs = sorted({num(r, "battery_ratio") for r in rows}, reverse=True)
    b = next((x for x in bs if math.isfinite(x)), float("nan"))
    sub = [r for r in rows if num(r, "battery_ratio") == b]

    M = np.full((len(rhos), len(rests)), np.nan)
    for i, rh in enumerate(rhos):
        for j, rs in enumerate(rests):
            v = [num(r, "saving") for r in sub
                 if r["rho"] == rh and r["restart_level"] == rs]
            if v:
                M[i, j] = mean_ci(v)[0]

    fig, ax = plt.subplots(figsize=(3.6, 3.0))
    fin = M[np.isfinite(M)]
    if fin.size and fin.min() < 0 < fin.max():
        im = ax.imshow(M, cmap=DIV, aspect="auto",
                       norm=TwoSlopeNorm(vmin=fin.min(), vcenter=0.0,
                                         vmax=fin.max()))
    else:
        im = ax.imshow(M, cmap=SEQ, aspect="auto")
    ax.set_xticks(range(len(rests)), rests)
    ax.set_yticks(range(len(rhos)), rhos)
    ax.set_xlabel("restart penalty  (Off $\\to$ Proc)")
    ax.set_ylabel(r"idle ratio  $\rho = e_{idle}/e_{proc}$")
    ax.grid(False)
    for i in range(len(rhos)):
        for j in range(len(rests)):
            if math.isfinite(M[i, j]):
                ax.text(j, i, f"{M[i, j]:.1f}", ha="center", va="center",
                        fontsize=7.5, color=cell_ink(im.cmap(im.norm(M[i, j]))))
    fig.colorbar(im, ax=ax, fraction=0.046, label="saving (%)")
    ax.set_title(f"Storage is worth most where the machine is worth least\n"
                 f"(orthogonal grid, $b$ = {b:g})", fontsize=9.5)
    save(fig, "f3_machine_grid")


# ---------------------------------------------------------------------------
# F4 — saving against price spread: synthetic deciles and real market-years
# ---------------------------------------------------------------------------

def f4_volatility() -> None:
    dec = read("m2_spread_deciles.csv")
    yrs = read("m2_market_years.csv")
    if not dec and not yrs:
        return skip("F4", "no M2 CSV found")

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0),
                             gridspec_kw={"width_ratios": [1.25, 1]})

    ax = axes[0]
    if dec:
        x = [0.5 * (num(r, "spread_lo") + num(r, "spread_hi")) for r in dec]
        y = [num(r, "saving") for r in dec]
        ax.plot(x, y, color=PALETTE[0], marker=MARKERS[0],
                label="mean saving")
        for r, xi, yi in zip(dec, x, y):
            fp = num(r, "frac_pos")
            if math.isfinite(fp) and fp >= 0.5:
                ax.annotate("first decile where NPV > 0", (xi, yi),
                            textcoords="offset points", xytext=(-8, 14),
                            fontsize=6.5, color=MUTED, ha="right",
                            arrowprops={"arrowstyle": "-", "color": MUTED,
                                        "linewidth": 0.6})
                break
        ax.axhline(0, color=MUTED, lw=0.8, ls=":")
        ax.set_xlabel("intra-day price spread  (EUR/MWh)")
        ax.set_ylabel("saving  (% of naive energy bill)")
        ax.set_title("synthetic tariffs, by spread decile")
        ax.legend(loc="best")
    else:
        ax.set_axis_off()
        ax.text(0.5, 0.5, "no decile data", ha="center", color=MUTED)

    ax = axes[1]
    if yrs:
        labs, m, lo, hi = [], [], [], []
        for r in yrs:
            labs.append(f"{r.get('market','')} {r.get('year','')}".strip())
            m.append(num(r, "saving"))
            lo.append(num(r, "saving") - num(r, "ci_lo"))
            hi.append(num(r, "ci_hi") - num(r, "saving"))
        order = np.argsort(m)
        labs = [labs[i] for i in order]
        m = [m[i] for i in order]
        lo = [max(0.0, lo[i]) if math.isfinite(lo[i]) else 0.0 for i in order]
        hi = [max(0.0, hi[i]) if math.isfinite(hi[i]) else 0.0 for i in order]
        ypos = np.arange(len(labs))
        ax.barh(ypos, m, xerr=[lo, hi], color=PALETTE[2], height=0.6,
                error_kw={"elinewidth": 0.9, "capsize": 2, "ecolor": MUTED})
        ax.set_yticks(ypos, labs)
        ax.axvline(0, color=MUTED, lw=0.8, ls=":")
        ax.set_xlabel("saving  (% of naive energy bill)")
        ax.set_title("real market-years")
        ax.grid(axis="y", visible=False)
    else:
        ax.set_axis_off()
        ax.text(0.5, 0.5, "no market-year data", ha="center", color=MUTED)

    fig.suptitle("The value of storage tracks the height of the price "
                 "fluctuation, not the price level", y=1.04, fontsize=10)
    save(fig, "f4_volatility")


# ---------------------------------------------------------------------------
# F5 — saving as the shop grows
# ---------------------------------------------------------------------------

def f5_scaling() -> None:
    rows = read("m3_savings.csv")
    if not rows:
        return skip("F5", "m3_savings.csv not found")
    bs = sorted({num(r, "battery_ratio") for r in rows if
                 math.isfinite(num(r, "battery_ratio"))})
    if not bs:
        return skip("F5", "no battery levels")

    fig, ax = plt.subplots(figsize=(4.4, 3.1))
    for i, b in enumerate(bs):
        pts = defaultdict(list)
        for r in rows:
            if num(r, "battery_ratio") == b:
                n = num(r, "n")
                if math.isfinite(n):
                    pts[n].append(num(r, "saving"))
        ns = sorted(pts)
        if not ns:
            continue
        stats = [mean_ci(pts[n]) for n in ns]
        y = [s[0] for s in stats]
        lo = [s[0] - s[1] if math.isfinite(s[1]) else 0.0 for s in stats]
        hi = [s[2] - s[0] if math.isfinite(s[2]) else 0.0 for s in stats]
        ax.errorbar(ns, y, yerr=[lo, hi], capsize=2, elinewidth=0.8,
                    label=f"$b$ = {b:g}", **style(i))
    ax.axhline(0, color=MUTED, lw=0.8, ls=":")
    ax.set_xscale("log")
    ax.set_xlabel("tasks per shop  (log scale)")
    ax.set_ylabel("saving  (% of naive energy bill)")
    ax.legend(title="capacity", loc="best")
    ax.set_title("Does the return survive the shop getting bigger?", fontsize=9.5)
    save(fig, "f5_scaling")


# ---------------------------------------------------------------------------
# F6 — substitution: storage against state management
# ---------------------------------------------------------------------------

def f6_substitution() -> None:
    rows = read("m4_decomposition.csv")
    if not rows:
        return skip("F6", "m4_decomposition.csv not found")
    real = [r for r in rows if str(r.get("placebo", "")).lower()
            in ("", "false", "0", "none")]
    rows = real or rows
    regimes = sorted({r.get("regime", "") for r in rows}, key=level_key)
    if not regimes:
        return skip("F6", "no regime column")

    fig, axes = plt.subplots(1, len(regimes), figsize=(3.0 * len(regimes), 3.0),
                             sharey=True, squeeze=False)
    comps = [("V_sigma", "state management alone"),
             ("V_beta", "storage alone"),
             ("I", "interaction (negative = substitutes)")]
    for ax, reg in zip(axes[0], regimes):
        sub = sorted([r for r in rows if r.get("regime") == reg],
                     key=lambda r: num(r, "battery_ratio"))
        bs = [num(r, "battery_ratio") for r in sub]
        w = 0.26
        for i, (field, lab) in enumerate(comps):
            ax.bar(np.arange(len(bs)) + (i - 1) * w,
                   [num(r, field) for r in sub], width=w * 0.92,
                   color=PALETTE[i], label=lab)
        ax.axhline(0, color=MUTED, lw=0.8)
        ax.set_xticks(range(len(bs)), [f"{b:g}" for b in bs])
        ax.set_xlabel("capacity $b$")
        ax.set_title(reg)
    axes[0][0].set_ylabel("value  (% of naive energy bill)")
    axes[0][-1].legend(loc="best")
    fig.suptitle("Storage and state management are partial substitutes: the "
                 "interaction is negative", y=1.04, fontsize=10)
    save(fig, "f6_substitution")


# ---------------------------------------------------------------------------
# F7 — the energy / tardiness frontier
# ---------------------------------------------------------------------------

def f7_frontier() -> None:
    rows = read("m5_frontier.csv")
    if not rows:
        return skip("F7", "m5_frontier.csv not found")
    bs = sorted({num(r, "battery_ratio") for r in rows
                 if math.isfinite(num(r, "battery_ratio"))})
    if not bs:
        return skip("F7", "no battery levels")

    fig, ax = plt.subplots(figsize=(4.4, 3.3))
    for i, b in enumerate(bs):
        sub = sorted([r for r in rows if num(r, "battery_ratio") == b],
                     key=lambda r: num(r, "lam"))
        x = [num(r, "tard_norm") for r in sub]
        y = [num(r, "energy_norm") for r in sub]
        ax.plot(x, y, label=f"$b$ = {b:g}", **style(i))
        # Direct-label the two extreme lambdas on the first curve only, so the
        # reader learns which end of the frontier is which without every point
        # carrying a number.
        if i == 0 and len(sub) >= 2:
            for r, xi, yi in ((sub[0], x[0], y[0]), (sub[-1], x[-1], y[-1])):
                ax.annotate(f"$\\lambda$={num(r,'lam'):g}", (xi, yi),
                            textcoords="offset points", xytext=(5, 4),
                            fontsize=7, color=MUTED)
    ax.set_xlabel("tardiness  (normalised)")
    ax.set_ylabel("energy cost  (normalised)")
    ax.legend(title="capacity", loc="best")
    ax.set_title("What the energy saving costs in delivery performance",
                 fontsize=9.5)
    save(fig, "f7_frontier")


# ---------------------------------------------------------------------------
# F8 — GA against the compact MILP
# ---------------------------------------------------------------------------

def f8_validation() -> None:
    rows = read("m0_gaps.csv")
    if not rows:
        return skip("F8", "m0_gaps.csv not found")
    meths = sorted({r["method"] for r in rows if r.get("method")}, key=level_key)
    if not meths:
        return skip("F8", "no method column")

    # Proven-optimal cells only: a "gap" against a MILP that itself timed out
    # is a difference between two heuristics, not a distance from the optimum.
    proven = [r for r in rows if str(r.get("proven", "")).lower()
              in ("true", "1", "yes")]
    used, note = (proven, "proven-optimal cells only") if len(proven) >= 10 \
        else (rows, "ALL cells — the MILP did not prove optimality everywhere")

    fig, ax = plt.subplots(figsize=(4.6, 3.1))
    data, labs, cols = [], [], []
    for i, m in enumerate(meths):
        v = [num(r, "norm") for r in used if r.get("method") == m]
        v = [x for x in v if math.isfinite(x)]
        if v:
            data.append(v)
            labs.append(f"{m}\n(n={len(v)})")
            cols.append(PALETTE[i % len(PALETTE)])
    if not data:
        return skip("F8", "no finite gaps")
    # `labels` was renamed `tick_labels` in matplotlib 3.9. The compute server
    # and a laptop rarely run the same version, and a figure script that dies
    # on a deprecation is a figure script nobody runs.
    lab_kw = ("tick_labels" if tuple(int(x) for x in
                                     matplotlib.__version__.split(".")[:2])
              >= (3, 9) else "labels")
    bp = ax.boxplot(data, patch_artist=True, widths=0.55,
                    medianprops={"color": INK, "linewidth": 1.2},
                    flierprops={"marker": ".", "markersize": 3,
                                "markerfacecolor": MUTED,
                                "markeredgecolor": "none"},
                    **{lab_kw: labs})
    for patch, c in zip(bp["boxes"], cols):
        patch.set_facecolor(c)
        patch.set_alpha(0.35)
        patch.set_edgecolor(c)
    ax.axhline(0, color=MUTED, lw=0.8, ls=":")
    ax.set_ylabel("gap to the MILP optimum\n(% of naive energy bill)")
    ax.grid(axis="x", visible=False)
    ax.set_title(f"The GA is close enough to the optimum to be the evaluator\n"
                 f"({note})", fontsize=9)
    save(fig, "f8_ga_vs_milp")


# ---------------------------------------------------------------------------
# F9 — seed replication
# ---------------------------------------------------------------------------

def f9_replication() -> None:
    rows = read("mr_contrasts.csv")
    if not rows:
        return skip("F9", "mr_contrasts.csv not found")

    labs = [r.get("contrast", "?") for r in rows]
    eff = [num(r, "sd_effect") for r in rows]
    noi = [num(r, "sd_noise") for r in rows]
    if not any(math.isfinite(x) for x in eff):
        return skip("F9", "no finite sd_effect")

    y = np.arange(len(labs))
    fig, ax = plt.subplots(figsize=(5.6, 0.55 * len(labs) + 1.8))
    ax.barh(y - 0.19, eff, height=0.34, color=PALETTE[0],
            label="between instances (only more instances reduce this)")
    ax.barh(y + 0.19, noi, height=0.34, color=PALETTE[1],
            label="seed noise (more seeds reduce this)")
    for i, (e, n) in enumerate(zip(eff, noi)):
        if math.isfinite(e) and math.isfinite(n) and e > 0:
            ax.text(max(e, n) * 1.02, i, f"noise {100*n*n/(e*e+n*n):.1f} %",
                    va="center", fontsize=7, color=MUTED)
    ax.set_yticks(y, [l.replace(" vs ", "\nvs ") for l in labs])
    ax.set_xlabel("standard deviation  (% of naive energy bill)")
    ax.legend(loc="lower right")
    ax.grid(axis="y", visible=False)
    ax.set_title("Where the error in a paired comparison comes from",
                 fontsize=9.5)
    save(fig, "f9_replication")


FIGURES = {"F1": f1_capacity, "F2": f2_roi_surface, "F3": f3_machine_grid,
           "F4": f4_volatility, "F5": f5_scaling, "F6": f6_substitution,
           "F7": f7_frontier, "F8": f8_validation, "F9": f9_replication}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list, e.g. F1,F4")
    args = ap.parse_args()

    if not ANALYSIS.exists():
        print(f"FATAL: {ANALYSIS} not found — run 05_analyse.py first",
              file=sys.stderr)
        return 1

    want = [w.strip().upper() for w in args.only.split(",") if w.strip()]
    todo = {k: v for k, v in FIGURES.items() if not want or k in want}
    if not todo:
        print(f"no figure matches {want}; known: {sorted(FIGURES)}",
              file=sys.stderr)
        return 1

    print(f"figures -> {FIG}")
    for name, fn in todo.items():
        try:
            fn()
        except Exception as exc:                          # noqa: BLE001
            # One broken figure must not cost the other eight. The traceback is
            # printed rather than swallowed, because a figure that fails
            # silently is how a paper ends up with a stale PDF from last week.
            import traceback
            print(f"  {name}: FAILED — {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)
    print(f"\n{len(_WRITTEN)} of {len(todo)} figure(s) written to {FIG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
