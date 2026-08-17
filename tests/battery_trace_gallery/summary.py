#!/usr/bin/env python3
"""
Battery/schedule trace gallery — cross-config summary.

Collapses every cached (instance, capacity, charge speed) cell into one CSV
and one aggregate figure, so the 45-cell grid can be read as a whole instead
of eyeballing 90 individual traces.

    python3 summary.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FIGURES = HERE / "figures"

import matplotlib                                         # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                            # noqa: E402

CB = {"blue": "#0072B2", "orange": "#E69F00", "green": "#009E73",
      "red": "#D55E00", "purple": "#CC79A7", "grey": "#666666"}
CAP_ORDER = ["low", "medium", "high"]
CRATE_ORDER = ["low", "medium", "high"]


def parse_tag(tag: str) -> tuple[int, str, str]:
    # n01_cap-low_crate-medium
    n_part, cap_part, crate_part = tag.split("_")
    prefix = int(n_part[1:])
    cap = cap_part.split("-")[1]
    crate = crate_part.split("-")[1]
    return prefix, cap, crate


def collect() -> list[dict]:
    rows = []
    for bpath in sorted(DATA.glob("*_baseline.json")):
        tag = bpath.name.removesuffix("_baseline.json")
        prefix, cap, crate = parse_tag(tag)
        bdoc = json.loads(bpath.read_text())
        lpath = DATA / f"{tag}_LBBD.json"
        row = {
            "tag": tag, "prefix": prefix, "cap": cap, "crate": crate,
            "n_tasks": 32 * prefix,
            "baseline_method": bdoc["config"]["method"],
            "baseline_gap": bdoc["solution_info"]["gap"],
            "baseline_obj": bdoc["solution_info"]["objective_value"],
            "baseline_energy_cost": bdoc["solution_info"]["energy_cost"],
            "baseline_tardiness": bdoc["solution_info"]["tardiness_cost"],
            "battery_capacity": bdoc["config"]["battery_capacity"],
        }
        if lpath.exists():
            ldoc = json.loads(lpath.read_text())
            row["lbbd_obj"] = ldoc["solution_info"]["objective_value"]
            row["lbbd_energy_cost"] = ldoc["solution_info"]["energy_cost"]
            row["lbbd_energy_cost_no_post"] = ldoc["diagnostics"].get("energy_cost_no_battery")
            row["lbbd_battery_saving"] = ldoc["diagnostics"].get("battery_saving")
            row["lbbd_gap_to_baseline"] = (row["lbbd_obj"] - row["baseline_obj"]) / row["baseline_obj"] \
                if row["baseline_obj"] else float("nan")
        rows.append(row)
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    fields = sorted({k for r in rows for k in r})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def fig_saving_vs_grid(rows: list[dict], out_path: Path) -> None:
    prefixes = sorted({r["prefix"] for r in rows})
    fig, axes = plt.subplots(1, len(prefixes), figsize=(3 * len(prefixes), 3),
                              sharey=True)
    if len(prefixes) == 1:
        axes = [axes]
    for ax, p in zip(axes, prefixes):
        sub = [r for r in rows if r["prefix"] == p and r.get("lbbd_battery_saving") is not None]
        for crate in CRATE_ORDER:
            xs, ys = [], []
            for i, cap in enumerate(CAP_ORDER):
                cell = next((r for r in sub if r["cap"] == cap and r["crate"] == crate), None)
                if cell is None:
                    continue
                base = cell["lbbd_energy_cost_no_post"] or 1.0
                pct = 100.0 * cell["lbbd_battery_saving"] / base
                xs.append(i)
                ys.append(pct)
            ax.plot(xs, ys, marker="o", label=crate,
                    color={"low": CB["blue"], "medium": CB["orange"], "high": CB["green"]}[crate])
        ax.set_xticks(range(len(CAP_ORDER)))
        ax.set_xticklabels(CAP_ORDER)
        ax.set_title(f"n={32 * p}")
        ax.set_xlabel("battery capacity")
    axes[0].set_ylabel("post-processing saving (% of no-battery cost)")
    axes[-1].legend(title="charge speed", loc="best", fontsize=7)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def fig_baseline_vs_lbbd(rows: list[dict], out_path: Path) -> None:
    prefixes = sorted({r["prefix"] for r in rows})
    fig, ax = plt.subplots(figsize=(5, 3.2))
    for p in prefixes:
        sub = [r for r in rows if r["prefix"] == p and "lbbd_gap_to_baseline" in r]
        if not sub:
            continue
        ys = sorted(100.0 * r["lbbd_gap_to_baseline"] for r in sub)
        ax.scatter([32 * p] * len(ys), ys, color=CB["blue"], alpha=0.6, s=18)
    ax.axhline(0, color=CB["grey"], lw=0.8)
    ax.set_xlabel("instance size (tasks)")
    ax.set_ylabel("LBBD vs baseline objective, %")
    ax.set_xscale("log", base=2)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main() -> None:
    rows = collect()
    if not rows:
        print("No cached data found under data/. Run generate.py first.", file=sys.stderr)
        sys.exit(1)
    FIGURES.mkdir(parents=True, exist_ok=True)
    write_csv(rows, HERE / "summary.csv")
    fig_saving_vs_grid(rows, FIGURES / "_summary_saving_vs_grid.png")
    fig_baseline_vs_lbbd(rows, FIGURES / "_summary_lbbd_vs_baseline.png")
    print(f"{len(rows)} cells summarised -> summary.csv, "
          f"figures/_summary_saving_vs_grid.png, figures/_summary_lbbd_vs_baseline.png")


if __name__ == "__main__":
    main()
