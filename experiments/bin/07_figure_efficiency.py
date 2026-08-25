#!/usr/bin/env python3
"""
Publication figure: which method is most efficient, and why Benders differs
from LBBD.

Reads a campaign CSV written by tests/campaign.py and emits vector output
sized for the manuscript. Every number is computed from the data; nothing is
transcribed.

    python3 experiments/bin/07_figure_efficiency.py campaign.csv
    python3 experiments/bin/07_figure_efficiency.py campaign.csv \
        --out paper/paper_tex/figures/fig_efficiency.pdf --width 6.5
    python3 experiments/bin/07_figure_efficiency.py campaign.csv --panels abc
    python3 experiments/bin/07_figure_efficiency.py campaign.csv --keep-degenerate

PANELS
------
a  Normalised gap to the best solution found, by size class, one line per arm.
   This is the answer to "which method is most efficient": lower is better,
   and the vertical axis is comparable across instances because it is divided
   by a method-independent instance scale rather than by an objective that can
   approach zero under negative prices.

b  Effect decomposition (docs/BENDERS_BATTERY.md section 5). Benders differs
   from LBBD in two ways at once -- it prices the battery in the master and it
   abandons the SPACES pre-processing -- and they pull in opposite directions.
   StateLBBD is the control that separates them, so the two curves sum to the
   net one. That sum is checked and the script refuses to plot if it fails.

c  What panel a had to drop, and why. Two distinct failures. A run is SILENT
   when the callback never fired, so no cut was attempted at all and the arm
   returned its warm-start schedule -- typically a master too large to clear
   its root node. A run is BLIND when cuts were attempted and every one was
   vacuous: the CP subproblem returned no verdict, its bound fell back to the
   tardiness floor, the strengthened cut reduced to `q >= lb_TWT`, and the
   solver suppressed it because the master already had that bound. Either way
   the run consumed its budget and constrained nothing, so its objective says
   nothing about the method and pooling it with functioning runs makes a
   formulation that fails to start look like one that merely performs badly.
   Blindness is not fatal for Benders -- its battery cuts are unaffected --
   so those runs are retained and do not appear here.

WHY THE MILP LINE STOPS
-----------------------
Above a certain size the compact model returns no feasible solution at all
within the budget. There is no value to plot, so the line ends and the count
of empty instances is annotated. Drawing it at zero, or omitting it silently,
would both be misleading.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                    # noqa: E402
from matplotlib.lines import Line2D                                # noqa: E402
from matplotlib.patches import Patch                               # noqa: E402

TASKS_PER_CLASS = 32
REFERENCE = "MILP"

# Fixed order, fixed style. Colour is never the only cue: every arm also has a
# distinct dash pattern and marker, so the figure survives greyscale printing
# and colour-blind readers.
STYLE = {
    "GA":         {"c": "#1baf7a", "ls": "-",   "m": "o", "z": 5},
    "MILP":       {"c": "#898781", "ls": (0, (1, 1)), "m": "s", "z": 2},
    "LBBD":       {"c": "#2a78d6", "ls": "--",  "m": "^", "z": 4},
    "LBBD-f5":    {"c": "#85b7eb", "ls": (0, (4, 1, 1, 1)), "m": "v", "z": 3},
    "StateLBBD":  {"c": "#eb6834", "ls": "-.",  "m": "D", "z": 4},
    "Benders":    {"c": "#e34948", "ls": (0, (3, 1, 1, 1, 1, 1)), "m": "*", "z": 4},
}
ORDER = ["GA", "StateLBBD", "Benders", "LBBD", "LBBD-f5", "MILP"]


# ==========================================================================
# data
# ==========================================================================

def load(path: Path) -> list[dict]:
    num = ("size_class", "replicate", "n", "ei", "horizon", "battery", "scale",
           "ok", "objective", "energy", "tardiness", "wall_s", "solver_s",
           "overhead_s", "gap", "proved", "subproblems", "inconclusive", "battery_cuts",
           "battery_node_cuts", "optimality_cuts", "feasibility_cuts")
    rows = []
    with path.open(newline="") as fh:
        for raw in csv.DictReader(fh):
            r = dict(raw)
            for k in num:
                if k not in r:
                    continue
                try:
                    r[k] = float(r[k]) if r[k] != "" else float("nan")
                except ValueError:
                    r[k] = float("nan")
            rows.append(r)
    if not rows:
        raise SystemExit(f"FATAL: {path} has no rows")
    return rows


def usable(r: dict) -> bool:
    return r["ok"] == 1 and math.isfinite(r["objective"])


def degenerate(r: dict) -> bool:
    """The run contributed no cut at all, so its objective measures nothing.

    Two failures, and only one of them applies to every arm. A run is SILENT
    when the callback never fired -- diagnostics absent, and in practice no
    reported gap either -- which means a master too large to clear its root
    node handed back its warm start. A run is TARDINESS-BLIND when every RCPSP
    call came back inconclusive, so `q` never rose above the tardiness floor.

    Blindness is fatal for LBBD and StateLBBD, whose only cut family is that
    one. It is not fatal for Benders: its battery subproblem is an LP that is
    never infeasible, so every incumbent still yields a cut on theta and the
    energy half of the bound kept tightening. Counting those as "no cut" was
    wrong in the first version of this script.
    """
    if r["arm"] in ("GA", REFERENCE):
        return False
    s_, i_ = r["subproblems"], r["inconclusive"]
    if not math.isfinite(s_):
        return True                       # silent: nothing of any kind
    if not (s_ > 0 and math.isfinite(i_) and i_ >= s_ - 1e-9):
        return False                      # at least one verdict -> a cut on q
    b = r.get("battery_cuts", float("nan"))
    if r["arm"] == "Benders" and (not math.isfinite(b) or b > 0):
        return False                      # blind, but still cutting on theta
    return True


def per_instance(rows: list[dict], drop_degenerate: bool):
    """(instance, arm) -> objective, with GA meaned over its seeds.

    Best-of-k over seeds would be biased upward in k and would flatter the one
    stochastic arm against the deterministic ones, so the mean is used.
    """
    bucket: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        bucket[(r["instance"], r["arm"])].append(r)

    obj: dict[str, dict[str, float]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    dead: dict[str, set[str]] = defaultdict(set)
    for (inst, arm), rs in bucket.items():
        meta.setdefault(inst, {"class": int(rs[0]["size_class"]),
                               "scale": rs[0]["scale"]})
        good = [r for r in rs if usable(r)]
        if any(degenerate(r) for r in rs):
            dead[inst].add(arm)
            if drop_degenerate:
                continue
        if good:
            obj[inst][arm] = statistics.fmean(r["objective"] for r in good)
    return obj, meta, dead


def common_subset(obj, meta, arms, classes):
    """Instances, per class, on which EVERY compared arm has a usable value.

    Without this each line of panel (a) is an average over whatever instances
    that arm happened to survive, and the arms that fail most are scored only
    on the instances they found easy. That flatters them and penalises the arms
    that returned something everywhere -- the comparison then measures which
    method fails more gracefully, not which method is better.

    The price is sample size, and it is paid honestly: the retained count is
    printed on the figure and the losses are itemised in panel (c).
    """
    out: dict[int, list[str]] = {}
    for c in classes:
        members = [i for i in obj if meta[i]["class"] == c]
        out[c] = [i for i in members
                  if all(a in obj[i] and math.isfinite(obj[i][a]) for a in arms)]
    return out


def gaps(obj, meta, arms, common, also=()):
    """Normalised gap to the best value found on each retained instance.

    `arms` are the methods the comparison is *about*. They must all be present
    on a retained instance, and the per-instance reference is the best value
    among them -- so the reference is drawn from an identical set for every one
    of them and cannot shift with which method happened to answer.

    `also` are methods plotted for context without being held to that standard,
    which in practice means the reference model. It is allowed to be missing:
    requiring it would empty the subset above the size where it stops returning
    anything, and that behaviour is precisely what panel (c) is for. Its gap is
    measured against the same reference as everyone else, so it can go negative
    where it beats them all, and it is averaged over fewer instances -- the
    count is annotated on the panel so the difference is visible rather than
    implied.
    """
    out: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for c, insts in common.items():
        for inst in insts:
            per = obj[inst]
            sc = meta[inst]["scale"]
            if not math.isfinite(sc) or sc == 0:
                continue
            best = min(per[a] for a in arms)
            for a in arms:
                out[c][a].append((per[a] - best) / sc)
            for a in also:
                if a in per and math.isfinite(per[a]):
                    out[c][a].append((per[a] - best) / sc)
    return out


def boot_se(v: list[float], reps: int = 4000, seed: int = 20260813) -> float:
    if len(v) < 2:
        return 0.0
    rng = random.Random(seed)
    n = len(v)
    return statistics.pstdev(statistics.fmean(rng.choices(v, k=n))
                             for _ in range(reps))


def merge_identical(obj, arms):
    """Collapse arms that produced the same answer on every shared instance.

    Two arms that only differ in some internal search heuristic can end up
    numerically identical when that heuristic never changes the verdict.
    Plotting both then hides one line completely under the other, which reads
    as a missing series rather than as the finding it actually is. Merging
    them into a single labelled line states the result instead of concealing
    it.

    Returns the surviving arms and a label map.
    """
    label = {a: a for a in arms}
    alive, absorbed = [], set()
    for i, a in enumerate(arms):
        if a in absorbed:
            continue
        same = []
        for b in arms[i + 1:]:
            if b in absorbed:
                continue
            shared = [inst for inst, per in obj.items() if a in per and b in per]
            if shared and all(abs(obj[i2][a] - obj[i2][b]) <= 1e-6 * max(1.0, abs(obj[i2][a]))
                              for i2 in shared):
                same.append(b)
        for b in same:
            absorbed.add(b)
        if same:
            label[a] = " / ".join([a] + same)
        alive.append(a)
    return alive, label, absorbed


# ==========================================================================
# panels
# ==========================================================================

def panel_gap(ax, g, obj, meta, arms, classes, label, required=None):
    for arm in [a for a in ORDER if a in arms]:
        st = STYLE[arm]
        xs, ys, es = [], [], []
        for c in classes:
            v = g.get(c, {}).get(arm, [])
            if not v:
                continue
            xs.append(c)
            ys.append(statistics.fmean(v))
            es.append(boot_se(v))
        if not xs:
            continue
        ax.plot(xs, ys, color=st["c"], linestyle=st["ls"], marker=st["m"],
                markersize=4.2, linewidth=1.4, label=label.get(arm, arm),
                zorder=st["z"], markeredgewidth=0)
        ax.fill_between(xs, [y - e for y, e in zip(ys, es)],
                        [y + e for y, e in zip(ys, es)],
                        color=st["c"], alpha=0.13, linewidth=0, zorder=1)

    # The reference model's coverage is panel (c)'s business now, so this
    # panel says nothing about it unless it is actually one of the lines.
    if REFERENCE in arms:
        empty = [c for c in classes if not g.get(c, {}).get(REFERENCE)]
        if empty:
            ax.annotate(f"{REFERENCE}: nothing feasible\nfrom {min(empty) * TASKS_PER_CLASS} tasks",
                        xy=(0.03, 0.97), xycoords="axes fraction",
                        fontsize=6.2, color="#898781", ha="left", va="top")

    required = required or arms
    ns = [len(g.get(c, {}).get(required[0], [])) for c in classes]
    extra = [a for a in arms if a not in required]
    if ns:
        note = "instances retained: " + "/".join(str(k) for k in ns)
        for a in extra:
            ks = [len(g.get(c, {}).get(a, [])) for c in classes]
            note += f"\n{a} present on: " + "/".join(str(k) for k in ks)
        ax.annotate(note,
                    xy=(0.03, 0.97 if REFERENCE not in arms else 0.84), xycoords="axes fraction",
                    fontsize=6.0, color="#898781", ha="left", va="top")
    ax.set_xlabel("tasks")
    ax.set_ylabel("normalised gap to best found")
    ax.set_xscale("log")
    ax.set_xticks(classes)
    ax.set_xticklabels([str(c * TASKS_PER_CLASS) for c in classes])
    ax.tick_params(axis="x", which="minor", bottom=False)
    ax.set_title("(a) solution quality", loc="left", fontsize=8)


def panel_effect(ax, obj, meta, classes):
    need = ("Benders", "StateLBBD", "LBBD")
    series = {"coordination": [], "spaces": [], "net": [], "x": []}
    for c in classes:
        co, sp, ne = [], [], []
        for inst, per in obj.items():
            if meta[inst]["class"] != c or not all(a in per for a in need):
                continue
            sc = meta[inst]["scale"]
            co.append((per["Benders"] - per["StateLBBD"]) / sc)
            sp.append((per["StateLBBD"] - per["LBBD"]) / sc)
            ne.append((per["Benders"] - per["LBBD"]) / sc)
        if not co:
            continue
        series["x"].append(c)
        series["coordination"].append(statistics.fmean(co))
        series["spaces"].append(statistics.fmean(sp))
        series["net"].append(statistics.fmean(ne))

    if not series["x"]:
        ax.annotate("needs Benders, StateLBBD and LBBD\non a common instance",
                    xy=(0.5, 0.5), xycoords="axes fraction", fontsize=6.6,
                    color="#898781", ha="center", va="center")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title("(b) why Benders differs from LBBD", loc="left", fontsize=8)
        return

    for a, b, n in zip(series["coordination"], series["spaces"], series["net"]):
        if abs((a + b) - n) > 1e-9:
            raise SystemExit("FATAL: effect decomposition does not sum; the "
                             "three arms are not on the same instance set")

    ax.axhline(0, color="#c3c2b7", linewidth=0.8, zorder=1)
    ax.plot(series["x"], series["spaces"], color="#eb6834", linestyle="-",
            marker="D", markersize=4.2, linewidth=1.4, markeredgewidth=0,
            label="cost of losing SPACES", zorder=4)
    ax.plot(series["x"], series["coordination"], color="#2a78d6",
            linestyle="--", marker="s", markersize=4.2, linewidth=1.4,
            markeredgewidth=0, label="battery coordination", zorder=4)
    ax.plot(series["x"], series["net"], color="#4a3aa7", linestyle=(0, (2, 2)),
            marker="^", markersize=4.2, linewidth=1.4, markeredgewidth=0,
            label="net (Benders $-$ LBBD)", zorder=3)

    ax.set_xlabel("tasks")
    ax.set_ylabel("paired difference, normalised")
    ax.set_xscale("log")
    ax.set_xticks(series["x"])
    ax.set_xticklabels([str(c * TASKS_PER_CLASS) for c in series["x"]])
    ax.tick_params(axis="x", which="minor", bottom=False)
    ax.set_title("(b) why Benders differs from LBBD", loc="left", fontsize=8)
    ax.legend(fontsize=6.2, frameon=False, loc="lower left")


def is_silent(r: dict) -> bool:
    """The callback never fired: no cut was even attempted."""
    return r["arm"] not in ("GA", REFERENCE) and not math.isfinite(r["subproblems"])


def panel_coverage(ax, rows, arms, classes):
    """How often each method returns nothing usable at all.

    Panel (a) can only compare methods on instances where all of them
    answered, so this is the other half of the picture: what each method costs
    you in coverage to buy the quality shown there. A method that looks good in
    (a) while failing here half the time is not a good method.

    Two bars stacked. The lower one is the plain failure: no feasible solution
    returned, whether because the model produced none in the budget or the run
    died. The upper one is the softer failure of returning a schedule that no
    working decomposition produced -- the master never cut, so what came back
    was the warm start.
    """
    order = [a for a in ORDER if a in arms]
    width = 0.8 / max(1, len(order))
    for k, arm in enumerate(order):
        xs, none, warm = [], [], []
        for j, c in enumerate(classes):
            mine = [r for r in rows if r["size_class"] == c and r["arm"] == arm]
            if not mine:
                continue
            insts = {r["instance"] for r in mine}
            n = len(insts)
            no_sol = {i for i in insts
                      if not any(usable(r) for r in mine if r["instance"] == i)}
            warm_only = {i for i in insts - no_sol
                         if all(degenerate(r) for r in mine if r["instance"] == i)}
            xs.append(j + (k - (len(order) - 1) / 2) * width)
            none.append(100.0 * len(no_sol) / n)
            warm.append(100.0 * len(warm_only) / n)
        ax.bar(xs, none, width=width, color=STYLE[arm]["c"], linewidth=0)
        ax.bar(xs, warm, width=width, bottom=none, color=STYLE[arm]["c"],
               linewidth=0, alpha=0.4, hatch="////", edgecolor="white")

    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([str(c * TASKS_PER_CLASS) for c in classes])
    ax.set_xlabel("tasks")
    ax.set_ylabel("instances (%)")
    ax.set_ylim(0, 100)
    ax.set_title("(c) failure to deliver", loc="left", fontsize=8)
    ax.legend(handles=[
        Patch(facecolor="#898781", label="no solution returned"),
        Patch(facecolor="#898781", alpha=0.4, hatch="////", edgecolor="white",
              label="warm start only, no cut")],
        fontsize=6.0, frameon=False, loc="upper left", borderaxespad=0.2,
        handlelength=1.4, handletextpad=0.5)


# ==========================================================================
# main
# ==========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--out", type=Path,
                    default=Path("paper/paper_tex/figures/fig_efficiency.pdf"))
    ap.add_argument("--panels", default="ab",
                    help="subset of 'abc' to draw (default ab)")
    ap.add_argument("--width", type=float, default=6.5,
                    help="figure width in inches (default 6.5, full text width)")
    ap.add_argument("--height", type=float, default=2.55)
    ap.add_argument("--keep-degenerate", action="store_true",
                    help="include runs that produced no optimality cut")
    ap.add_argument("--with-milp", action="store_true",
                    help="require the reference model in the common subset of "
                         "panel (a); empties it above the size where it returns "
                         "nothing")
    ap.add_argument("--also-png", action="store_true")
    args = ap.parse_args()

    rows = load(args.csv)
    arms = [a for a in ORDER if any(r["arm"] == a for r in rows)]
    obj, meta, dead = per_instance(rows, drop_degenerate=not args.keep_degenerate)
    classes = sorted({m["class"] for m in meta.values()})
    plot_arms, label, absorbed = merge_identical(obj, arms)
    # The reference model is excluded from the head-to-head by default: above a
    # certain size it returns nothing, so requiring it would empty the common
    # subset exactly where the comparison matters. Its coverage is reported in
    # panel (c) alongside everyone else's, which is the honest place for it.
    compare = [a for a in plot_arms if a != REFERENCE or args.with_milp]
    # The reference is drawn but not required: it is plotted on the same
    # instances as everyone else wherever it answered, and simply absent where
    # it did not. Letting it constrain the subset would delete the comparison
    # at the sizes that matter most.
    also = [] if args.with_milp else [a for a in plot_arms if a == REFERENCE]
    common = common_subset(obj, meta, compare, classes)
    g = gaps(obj, meta, compare, common, also)

    plt.rcParams.update({
        "font.family": "serif", "font.size": 8, "axes.labelsize": 8,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 6.4,
        "axes.linewidth": 0.6, "grid.linewidth": 0.4, "lines.linewidth": 1.4,
        "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "figure.dpi": 200, "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })

    keys = [k for k in "abc" if k in args.panels]
    fig, axes = plt.subplots(1, len(keys), figsize=(args.width, args.height))
    axes = [axes] if len(keys) == 1 else list(axes)

    for ax in axes:
        ax.grid(True, color="#e1e0d9", linewidth=0.4, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    for ax, k in zip(axes, keys):
        if k == "a":
            panel_gap(ax, g, obj, meta, compare + also, classes, label,
                      required=compare)
        elif k == "b":
            panel_effect(ax, obj, meta, classes)
        else:
            panel_coverage(ax, rows, arms, classes)

    if "a" in keys:
        ordered = [a for a in ORDER if a in compare + also]
        handles = [Line2D([], [], color=STYLE[a]["c"], linestyle=STYLE[a]["ls"],
                          marker=STYLE[a]["m"], markersize=4, markeredgewidth=0,
                          label=label.get(a, a)) for a in ordered]
        ncol = min(len(handles), 4 if args.width < 7.5 else len(handles))
        rows_ = math.ceil(len(handles) / ncol)
        fig.legend(handles=handles, loc="upper center", ncol=ncol,
                   frameon=False, fontsize=6.4,
                   bbox_to_anchor=(0.5, 1.02 + 0.055 * rows_),
                   columnspacing=1.2, handlelength=2.4)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(w_pad=2.2)
    fig.savefig(args.out)
    if args.also_png:
        fig.savefig(args.out.with_suffix(".png"))
    plt.close(fig)

    n_dead = sum(len(v) for v in dead.values())
    print(f"wrote {args.out}")
    print(f"  {len(meta)} instances, classes {classes}, arms {', '.join(arms)}")
    for a in plot_arms:
        if label[a] != a:
            print(f"  merged: {label[a]} are identical on every shared instance")
    for c in classes:
        print(f"  class {c:2d}: {len(common[c])} instance(s) common to "
              f"{', '.join(compare)}")
    print(f"  {n_dead} (instance, arm) runs are degenerate "
          f"({'kept' if args.keep_degenerate else 'excluded from panel a'})")
    for c in classes:
        miss = sum(1 for i in meta if meta[i]["class"] == c
                   and REFERENCE not in obj.get(i, {}))
        tot = sum(1 for i in meta if meta[i]["class"] == c)
        if miss:
            print(f"  class {c:2d}: {REFERENCE} returned nothing on {miss}/{tot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
