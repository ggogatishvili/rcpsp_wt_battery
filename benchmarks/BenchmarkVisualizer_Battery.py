import json
import plotly.graph_objects as go
import sys
import fnmatch


# Colors
CAP0_COLOR = "#4C78A8"
CAP16_COLOR = "#84FF24"

# Parameters
METHOD = "MILP"
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
    if entry["config"]["method"] != METHOD:
        continue

    inst = entry["instance"]

    if not fnmatch.fnmatch(inst, instance_pattern):
        continue

    cap = entry["config"]["battery_capacity"]

    if inst not in instance_data:
        instance_data[inst] = {}

    instance_data[inst][cap] = {
        "objective_value": entry["solution_info"]["objective_value"]
    }

def numeric_key(name):
    parts = name.split("_")
    return tuple(int(p) for p in parts)

instances = sorted(instance_data.keys(), key=numeric_key)

fig = go.Figure()

x_positions = []
cap0_energy = []
cap16_energy = []
savings = []
hover_savings = []

for inst in instances:
    x_positions.append(inst)

    c0 = instance_data[inst].get(CAPACITY_A)
    c16 = instance_data[inst].get(CAPACITY_B)

    val0 = c0["objective_value"] if c0 and c0["objective_value"] is not None else None
    val16 = c16["objective_value"] if c16 and c16["objective_value"] is not None else None

    cap0_energy.append(val0)
    cap16_energy.append(val16)

    if val0 is not None and val16 is not None and val0 > 0:
        imp = ((val0 - val16) / val0) * 100
        savings.append(imp)
        hover_savings.append(f"{imp:.2f}%")
    else:
        hover_savings.append("N/A")

average_savings = sum(savings) / len(savings) if savings else 0

fig.add_trace(go.Bar(
    x=x_positions,
    y=cap0_energy,
    name=f"Battery capacity {CAPACITY_A} MWh",
    marker=dict(color=CAP0_COLOR),
    hovertemplate=f"Instance: %{{x}}<br>Capacity: {CAPACITY_A} MWh<br>Cost: %{{y:.2f}} EUR<extra></extra>"
))

fig.add_trace(go.Bar(
    x=x_positions,
    y=cap16_energy,
    name=f"Battery capacity {CAPACITY_B} MWh",
    marker=dict(color=CAP16_COLOR),
    customdata=hover_savings,
    hovertemplate=f"Instance: %{{x}}<br>Capacity: {CAPACITY_B} MWh<br>Cost: %{{y:.2f}} EUR<br>Saving: %{{customdata}}<extra></extra>"
))

fig.update_layout(
    title=f"Battery Capacity {CAPACITY_A} vs {CAPACITY_B} MWh"
          + f"<br><sup>Average savings: {average_savings:.2f}%</sup>"
          + f"<br><sup>Method: {METHOD}</sup>",
    barmode="group",
    xaxis=dict(
        title="Instance"
    ),
    yaxis=dict(
        title="Total Cost [EUR]"
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1
    ),
    height=600
)

fig.show()