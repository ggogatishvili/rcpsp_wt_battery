import json
import plotly.graph_objects as go
import sys


# Colors
H1_COLOR = "#F58518"
GA_COLOR = "#B279A2"

# Parameters
BATTERY_CAPACITY = 16

# Load results
if len(sys.argv) < 2:
    print("Usage: python script.py <input_file>")
    sys.exit(1)

results_file = sys.argv[1]

with open(results_file) as f:
    data = json.load(f)

# Organize data per instance
instance_data = {}

for entry in data:
    if entry["config"]["battery_capacity"] != BATTERY_CAPACITY:
        continue

    inst = entry["instance"]
    method = entry["config"]["method"]
    obj = entry["solution_info"]["objective_value"]

    if inst not in instance_data:
        instance_data[inst] = {}

    instance_data[inst][method] = obj


# Compute ratios and prepare arrays
epsilon = 1e-6

x_h1 = []
y_h1 = []

x_ga = []
y_ga = []

unique_sizes = set()

for inst, values in instance_data.items():
    obj_milp = values.get("MILP")
    obj_h1 = values.get("H1")
    obj_ga = values.get("GA")

    if obj_milp is None or obj_milp == 0:
        continue

    size = inst.split("_")[0]
    unique_sizes.add(size)

    # Calculate H1 gap
    if obj_h1 is not None:
        ratio_h1 = (obj_milp - obj_h1) / (abs(obj_milp) + epsilon)
        x_h1.append(size)
        y_h1.append(ratio_h1)

    # Calculate GA gap
    if obj_ga is not None:
        ratio_ga = (obj_milp - obj_ga) / (abs(obj_milp) + epsilon)
        x_ga.append(size)
        y_ga.append(ratio_ga)


# Sort sizes
sizes_sorted = sorted(list(unique_sizes), key=lambda x: int(x))


# Create Grouped Boxplot
fig = go.Figure()

# Add trace for H1
fig.add_trace(go.Box(
    x=x_h1,
    y=y_h1,
    name="MILP vs H1",
    boxmean=True,
    marker_color=H1_COLOR,
    hovertemplate="Size %{x} - H1 Gap: %{y:.4f}<extra></extra>"
))

# Add trace for GA
fig.add_trace(go.Box(
    x=x_ga,
    y=y_ga,
    name="MILP vs GA",
    boxmean=True,
    marker_color=GA_COLOR,
    hovertemplate="Size %{x} - GA Gap: %{y:.4f}<extra></extra>"
))

fig.update_layout(
    title={
        'text': "Relative Gap Comparison: MILP vs H1 and MILP vs GA",
        'y': 0.95
    },
    annotations=[
        dict(
            text=r"$\text{Relative Gap = } \frac{MILP - \text{Method}}{|MILP| + \epsilon}$",
            showarrow=False,
            xref="paper", yref="paper",
            x=-0.008, y=1.05,
            xanchor="left", yanchor="bottom",
            font=dict(size=20)
        )
    ],
    margin=dict(t=120),

    yaxis_title="Relative Gap",
    xaxis_title="Instance Size",
    boxmode='group',
    xaxis=dict(
        categoryorder='array',
        categoryarray=sizes_sorted
    ),
    height=750
)

fig.show()