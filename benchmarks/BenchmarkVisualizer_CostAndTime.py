import json
import pathlib
import plotly.graph_objects as go
import sys


# Colors
MILP_COLOR = "#4C78A8"
MILP_TIMEOUT_COLOR = "#FF5244"
H1_COLOR = "#F58518"
GA_COLOR = "#B279A2"


# Load and sort results
BENCHMARK_DIR = pathlib.Path(__file__).resolve().parent
RESULTS_FILE = BENCHMARK_DIR / "aggregated_results.json"

if not RESULTS_FILE.exists():
    print("aggregated_results.json not found. Run benchmark.py first.")
    sys.exit(1)

with open(RESULTS_FILE) as f:
    data = json.load(f)

instance_data = {}

for entry in data:
    if entry["config"]["battery_capacity"] != 16:
        continue

    inst = entry["instance"]
    method = entry["config"]["method"]

    if inst not in instance_data:
        instance_data[inst] = {}

    instance_data[inst][method] = {
        "objective_value": entry["solution_info"]["objective_value"],
        "computation_time": entry["solution_info"]["computation_time"],
        "time_limit" : entry["config"]["time_limit"]
    }

def numeric_key(name):
    parts = name.split("_")
    return tuple(int(p) for p in parts)

instances = sorted(instance_data.keys(), key=numeric_key)


# Prepare figure
fig = go.Figure()

time_domain = [0.05, 0.45]
cost_domain = [0.55, 0.98]

x_positions = []
milp_energy = []
h1_energy = []
milp_time = []
milp_time_colors = []
h1_time = []

for inst in instances:
    x_positions.append(inst)

    milp = instance_data[inst].get("MILP")
    h1 = instance_data[inst].get("HEURISTIC1")

    # Cost
    milp_energy.append(milp["objective_value"] if milp and milp["objective_value"] is not None else None)
    h1_energy.append(h1["objective_value"] if h1 and h1["objective_value"] is not None else None)

    # Time
    if milp and milp["computation_time"] is not None:
        milp_time.append(milp["computation_time"])
        milp_time_colors.append(MILP_TIMEOUT_COLOR if milp["computation_time"] >= milp["time_limit"] else MILP_COLOR)
    else:
        milp_time.append(None)
        milp_time_colors.append(MILP_COLOR)

    h1_time.append(h1["computation_time"] if h1 else None)


# Energy Bars
fig.add_trace(go.Bar(
    x=x_positions,
    y=milp_energy,
    name="MILP",
    legendgroup="MILP",
    marker=dict(color=MILP_COLOR),
    yaxis="y2",
    hovertemplate="Instance: %{x}<br>MILP cost: %{y:.2f}<extra></extra>"
))

fig.add_trace(go.Bar(
    x=x_positions,
    y=h1_energy,
    name="HEURISTIC1",
    legendgroup="HEURISTIC1",
    marker=dict(color=H1_COLOR),
    yaxis="y2",
    hovertemplate="Instance: %{x}<br>H1 cost: %{y:.2f} EUR<extra></extra>"
))


# Time Bars
fig.add_trace(go.Bar(
    x=x_positions,
    y=milp_time,
    name="MILP (time)",
    legendgroup="MILP",
    marker=dict(color=milp_time_colors),
    yaxis="y",
    showlegend=False,
    hovertemplate="Instance: %{x}<br>MILP Time: %{y:.3f}s<extra></extra>"
))

fig.add_trace(go.Bar(
    x=x_positions,
    y=h1_time,
    name="HEURISTIC1 (time)",
    legendgroup="HEURISTIC1",
    marker=dict(color=H1_COLOR),
    yaxis="y",
    showlegend=False,
    hovertemplate="Instance: %{x}<br>H1 Time: %{y:.3f}s<extra></extra>"
))


# Layout
fig.update_layout(

    title="Benchmark Comparison: MILP vs HEURISTIC1",

    barmode="group",

    # X axis shared
    xaxis=dict(
        title="Instance",
        domain=[0, 1]
    ),

    # Top: energy cost
    yaxis2=dict(
        title="Total Cost [EUR]",
        domain=cost_domain,
        anchor="x"
    ),

    # Bottom: computation time
    yaxis=dict(
        title="Computation Time [s]",
        type="log",
        domain=time_domain,
        anchor="x"
    ),

    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1
    ),

    height=800
)

fig.show()