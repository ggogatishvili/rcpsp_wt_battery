import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys
import fnmatch


# Colors
CAP0_COLOR = "#4C78A8"
CAP16_COLOR = "#84FF24"

# Parameters
CAPACITY_A = 0
CAPACITY_B = 16

# Load and sort results
if len(sys.argv) < 2:
    print("Usage: python script.py <input_file> [instance_pattern]")
    print("Example: python script.py results.json '1_*'")
    sys.exit(1)

results_file = sys.argv[1]
instance_pattern = sys.argv[2] if len(sys.argv) > 2 else "*"

with open(results_file) as f:
    data = json.load(f)

instance_data = {}

for entry in data:
    inst = entry["instance"]

    if not fnmatch.fnmatch(inst, instance_pattern):
        continue

    method = entry["config"]["method"]
    cap = entry["config"]["battery_capacity"]

    if inst not in instance_data:
        instance_data[inst] = {"MILP": {}, "HEURISTIC1": {}}

    if method in ["MILP", "HEURISTIC1"]:
        instance_data[inst][method][cap] = entry["solution_info"]["objective_value"]

def numeric_key(name):
    parts = name.split("_")
    return tuple(int(p) for p in parts)

instances = sorted(instance_data.keys(), key=numeric_key)

def process_method_data(method_name):
    cap0_energy = []
    cap16_energy = []
    all_savings = []
    hover_savings = []

    for inst in instances:
        val0 = instance_data[inst][method_name][CAPACITY_A]
        val16 = instance_data[inst][method_name][CAPACITY_B]

        cap0_energy.append(val0)
        cap16_energy.append(val16)

        if val0 is not None and val16 is not None and val0 > 0:
            current_savings = ((val0 - val16) / val0) * 100
            all_savings.append(current_savings)
            hover_savings.append(f"{current_savings:.2f}%")
        else:
            hover_savings.append("N/A")

    avg_savings = sum(all_savings) / len(all_savings) if all_savings else 0
    return cap0_energy, cap16_energy, hover_savings, avg_savings

milp_0, milp_16, milp_hover, milp_avg = process_method_data("MILP")
h1_0, h1_16, h1_hover, h1_avg = process_method_data("HEURISTIC1")

fig = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.1,
    subplot_titles=(
        f"MILP (Average savings: {milp_avg:.2f}%)",
        f"HEURISTIC1 (Average savings: {h1_avg:.2f}%)"
    )
)

# Add MILP traces
fig.add_trace(go.Bar(
    x=instances, y=milp_0,
    name=f"Battery capacity {CAPACITY_A} MWh",
    marker=dict(color=CAP0_COLOR),
    legendgroup="cap0",
    hovertemplate=f"Instance: %{{x}}<br>Method: MILP<br>Capacity: {CAPACITY_A} MWh<br>Cost: %{{y:.2f}} EUR<extra></extra>"
), row=1, col=1)

fig.add_trace(go.Bar(
    x=instances, y=milp_16,
    name=f"Battery capacity {CAPACITY_B} MWh",
    marker=dict(color=CAP16_COLOR),
    legendgroup="cap16",
    customdata=milp_hover,
    hovertemplate=f"Instance: %{{x}}<br>Method: MILP<br>Capacity: {CAPACITY_B} MWh<br>Cost: %{{y:.2f}} EUR<br>Saving: %{{customdata}}<extra></extra>"
), row=1, col=1)


# Add HEURISTIC1 traces
fig.add_trace(go.Bar(
    x=instances, y=h1_0,
    name=f"Battery capacity {CAPACITY_A} MWh",
    marker=dict(color=CAP0_COLOR),
    legendgroup="cap0",
    showlegend=False, # Hide from legend to prevent duplicates
    hovertemplate=f"Instance: %{{x}}<br>Method: HEURISTIC1<br>Capacity: {CAPACITY_A} MWh<br>Cost: %{{y:.2f}} EUR<extra></extra>"
), row=2, col=1)

fig.add_trace(go.Bar(
    x=instances, y=h1_16,
    name=f"Battery capacity {CAPACITY_B} MWh",
    marker=dict(color=CAP16_COLOR),
    legendgroup="cap16",
    showlegend=False, # Hide from legend to prevent duplicates
    customdata=h1_hover,
    hovertemplate=f"Instance: %{{x}}<br>Method: HEURISTIC1<br>Capacity: {CAPACITY_B} MWh<br>Cost: %{{y:.2f}} EUR<br>Saving: %{{customdata}}<extra></extra>"
), row=2, col=1)


# Layout
fig.update_layout(
    title=f"Cost Comparison: Capacity {CAPACITY_A} vs {CAPACITY_B} MWh",
    barmode="group",
    height=750,
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.05,
        xanchor="right",
        x=1
    )
)

# Axis titles
fig.update_yaxes(title_text="Total Cost [EUR]", row=1, col=1)
fig.update_yaxes(title_text="Total Cost [EUR]", row=2, col=1)
fig.update_xaxes(title_text="Instance", row=2, col=1)

fig.show()