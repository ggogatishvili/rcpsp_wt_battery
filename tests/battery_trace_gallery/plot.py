#!/usr/bin/env python3
"""
Battery/schedule trace gallery — figures.

Reads the raw solver JSON cached by generate.py under data/ and draws, per
grid cell, two 4-panel figures sharing a time axis:

  - <tag>_baseline.png   MILP or GA (whichever generate.py picked)
  - <tag>_lbbd.png       LBBD, with (solid) and without (dashed) the battery
                         post-processing step drawn together

Panels: (1) machine state + late-task markers, (2) energy source at each
instant (grid->machine, grid->battery charge, battery->machine discharge),
(3) battery level, (4) tariff + realised/cumulative cost.

    python3 plot.py                # all cached cells
    python3 plot.py --only 1_1     # just one instance
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FIGURES = HERE / "figures"

import matplotlib                                         # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                            # noqa: E402
import numpy as np                                         # noqa: E402

# Colour-blind safe (Okabe-Ito), matching experiments/bin/06_figures.py.
CB = {"blue": "#0072B2", "orange": "#E69F00", "green": "#009E73",
      "red": "#D55E00", "purple": "#CC79A7", "grey": "#666666",
      "yellow": "#F0E442", "skyblue": "#56B4E9"}

STATE_COLOUR = {"Proc": CB["blue"], "Idle": CB["yellow"], "Off": CB["grey"]}
TRANSITION_COLOUR = CB["orange"]

plt.rcParams.update({
    "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 7,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 150, "savefig.bbox": "tight", "savefig.pad_inches": 0.05,
    "legend.frameon": False, "lines.linewidth": 1.1,
})

# Transition energy costs per unit time. Mirror src/config.cpp CLI defaults
# (off-proc-cost=5, proc-off-cost=1, proc-idle-cost=2, idle-proc-cost=2.5);
# generate.py never overrides them, so these are exact, not approximations.
TRANSITION_COST = {
    ("Off", "Proc"): 5.0,
    ("Proc", "Off"): 1.0,
    ("Proc", "Idle"): 2.0,
    ("Idle", "Proc"): 2.5,
}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def machine_demand(doc: dict) -> np.ndarray:
    """Per-instant machine energy draw eMach(t), reconstructed from
    machine_blocks and the (never-overridden) machine profile costs."""
    h = len(doc["battery_levels"])
    e = np.zeros(h)
    cfg = doc["config"]
    state_cost = {"Proc": cfg["e_proc"], "Idle": cfg["e_idle"], "Off": cfg["e_off"]}
    for blk in doc["machine_blocks"]:
        desc = blk["description"]
        if " -> " in desc:
            a, b = desc.split(" -> ")
            cost = TRANSITION_COST[(a, b)]
        else:
            cost = state_cost[desc]
        e[blk["start_time"]:blk["end_time"] + 1] = cost
    return e


def energy_split(doc: dict, e_mach: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reproduce BatteryLp's demand balance: gMach + eta_d*bMach = eMach,
    battery level change funded/absorbed via gBatt/bMach — same algorithm as
    SolverH1::computeEnergyCost (src/SolverH1.cpp:1103)."""
    levels = np.asarray(doc["battery_levels"])
    h = len(levels)
    eta_c = doc["config"]["charging_efficiency"]
    eta_d = doc["config"]["discharging_efficiency"]
    next_level = np.append(levels[1:], 0.0)
    delta = next_level - levels
    g_batt = np.where(delta > 0, delta / eta_c, 0.0)
    b_mach = np.where(delta < 0, -delta, 0.0)
    energy_from_batt = b_mach * eta_d
    g_mach = e_mach - energy_from_batt
    return g_mach, g_batt, b_mach


def late_ticks(doc: dict) -> list[float]:
    return [t["end_time"] for t in doc["task_assignments"] if t["end_time"] > t["due_date"]]


def panel_state(ax, doc: dict, ticks: list[float]) -> None:
    for blk in doc["machine_blocks"]:
        desc = blk["description"]
        colour = TRANSITION_COLOUR if " -> " in desc else STATE_COLOUR[desc]
        ax.axvspan(blk["start_time"], blk["end_time"] + 1, color=colour, lw=0)
    # Dedicated strip above the state area (axes-relative y), so late-task
    # markers stay visible regardless of what's underneath them.
    if ticks:
        ax.scatter(ticks, [1.06] * len(ticks), marker="v", s=14, color=CB["red"],
                  clip_on=False, transform=ax.get_xaxis_transform(), zorder=5)
    ax.set_yticks([])
    ax.set_ylim(0, 1)
    ax.set_ylabel("machine")
    handles = [plt.Rectangle((0, 0), 1, 1, color=STATE_COLOUR["Off"]),
               plt.Rectangle((0, 0), 1, 1, color=STATE_COLOUR["Idle"]),
               plt.Rectangle((0, 0), 1, 1, color=STATE_COLOUR["Proc"]),
               plt.Rectangle((0, 0), 1, 1, color=TRANSITION_COLOUR),
               plt.Line2D([0], [0], marker="v", color="none", markerfacecolor=CB["red"], lw=0)]
    ax.legend(handles, ["Off", "Idle", "Proc", "transition", "task late"],
              loc="upper right", ncol=5, fontsize=6)


def panel_energy_source(ax, t: np.ndarray, g_mach, g_batt, b_mach, e_mach_no_post=None) -> None:
    ax.stackplot(t, g_mach, g_batt, b_mach,
                 colors=[CB["blue"], CB["orange"], CB["green"]],
                 labels=["grid -> machine", "grid -> battery (charge)",
                         "battery -> machine (discharge)"])
    if e_mach_no_post is not None:
        ax.plot(t, e_mach_no_post, color=CB["red"], ls="--", lw=1.0,
                label="grid draw, no post-processing")
    ax.set_ylabel("energy / unit time")
    ax.legend(loc="upper right", fontsize=6, ncol=2)


def panel_battery(ax, t: np.ndarray, levels: np.ndarray, capacity: float,
                  levels_no_post: np.ndarray | None = None) -> None:
    ax.plot(t, levels, color=CB["blue"], label="battery level (with post-processing)")
    if levels_no_post is not None:
        ax.plot(t, levels_no_post, color=CB["blue"], ls="--", lw=1.0,
                label="battery level (no post-processing)")
    ax.axhline(capacity, color=CB["grey"], ls=":", lw=0.8, label="capacity")
    ax.set_ylabel("battery level")
    ax.legend(loc="upper right", fontsize=6)


def panel_cost(ax, t: np.ndarray, price: np.ndarray, cost: np.ndarray,
              cost_no_post: np.ndarray | None = None) -> None:
    ax.plot(t, price, color=CB["grey"], lw=0.8, label="tariff (price/unit)")
    ax2 = ax.twinx()
    ax2.plot(t, np.cumsum(cost), color=CB["blue"], label="cumulative cost")
    if cost_no_post is not None:
        ax2.plot(t, np.cumsum(cost_no_post), color=CB["blue"], ls="--", lw=1.0,
                label="cumulative cost, no post-processing")
    ax.set_ylabel("price")
    ax2.set_ylabel("cumulative cost")
    ax.set_xlabel("time")
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=6)


def figure_baseline(doc: dict, out_path: Path, title: str) -> None:
    h = len(doc["battery_levels"])
    t = np.arange(h)
    e_mach = machine_demand(doc)
    g_mach, g_batt, b_mach = energy_split(doc, e_mach)
    price = np.asarray(doc["instance_summary"]["energy_costs"])
    cost = price * (g_mach + g_batt)

    fig, axes = plt.subplots(4, 1, figsize=(8, 8), sharex=True)
    panel_state(axes[0], doc, late_ticks(doc))
    panel_energy_source(axes[1], t, g_mach, g_batt, b_mach)
    panel_battery(axes[2], t, np.asarray(doc["battery_levels"]), doc["config"]["battery_capacity"])
    panel_cost(axes[3], t, price, cost)
    fig.suptitle(title, fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)


def figure_lbbd(doc: dict, out_path: Path, title: str) -> None:
    h = len(doc["battery_levels"])
    t = np.arange(h)
    e_mach = machine_demand(doc)
    g_mach, g_batt, b_mach = energy_split(doc, e_mach)
    price = np.asarray(doc["instance_summary"]["energy_costs"])
    cost = price * (g_mach + g_batt)

    levels_no_post = np.zeros(h)
    cost_no_post = price * e_mach

    fig, axes = plt.subplots(4, 1, figsize=(8, 8), sharex=True)
    panel_state(axes[0], doc, late_ticks(doc))
    panel_energy_source(axes[1], t, g_mach, g_batt, b_mach, e_mach_no_post=e_mach)
    panel_battery(axes[2], t, np.asarray(doc["battery_levels"]), doc["config"]["battery_capacity"],
                 levels_no_post=levels_no_post)
    panel_cost(axes[3], t, price, cost, cost_no_post=cost_no_post)
    fig.suptitle(title, fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="tag substring filter, e.g. n01")
    args = ap.parse_args()

    baseline_files = sorted(DATA.glob("*_baseline.json"))
    if args.only:
        baseline_files = [p for p in baseline_files if args.only in p.name]

    if not baseline_files:
        print("No cached baseline JSON found under data/. Run generate.py first.",
              file=sys.stderr)
        sys.exit(1)

    for bpath in baseline_files:
        tag = bpath.name.removesuffix("_baseline.json")
        doc = load(bpath)
        method = doc["config"]["method"]
        gap = doc["solution_info"]["gap"]
        gap_str = f"{gap:.3f}" if gap is not None else "n/a"
        title = f"{tag} — baseline: {method} (gap={gap_str}, obj={doc['solution_info']['objective_value']:.1f})"
        figure_baseline(doc, FIGURES / f"{tag}_baseline.png", title)

        lpath = DATA / f"{tag}_LBBD.json"
        if lpath.exists():
            ldoc = load(lpath)
            saving = ldoc["diagnostics"].get("battery_saving", float("nan"))
            ltitle = (f"{tag} — LBBD (with vs without battery post-processing, "
                     f"saving={saving:.1f})")
            figure_lbbd(ldoc, FIGURES / f"{tag}_lbbd.png", ltitle)
        print(f"  wrote figures for {tag}")


if __name__ == "__main__":
    main()
