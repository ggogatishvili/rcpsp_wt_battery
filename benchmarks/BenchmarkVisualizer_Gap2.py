import json
import plotly.graph_objects as go
import sys

# Colors
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

x_gap = []
y_gap = []

unique_sizes = set()

for inst, values in instance_data.items():
    obj_h1 = values.get("H1")
    obj_ga = values.get("GA")

    # We need both H1 and GA to compare them
    if obj_h1 is None or obj_ga is None:
        continue

    # Prevent division by zero logic (epsilon helps, but safe to skip absolute zero baseline)
    if obj_h1 == 0:
        continue

    size = inst.split("_")[0]
    unique_sizes.add(size)

    # Calculate H1 vs GA gap
    ratio_ga_h1 = (obj_h1 - obj_ga) / (abs(obj_h1) + epsilon)
    x_gap.append(size)
    y_gap.append(ratio_ga_h1)


# Sort sizes
sizes_sorted = sorted(list(unique_sizes), key=lambda x: int(x))


# Create Boxplot
fig = go.Figure()

# Add trace for H1 vs GA
fig.add_trace(go.Box(
    x=x_gap,
    y=y_gap,
    name="H1 vs GA",
    boxmean=True,
    marker_color=GA_COLOR,
    hovertemplate="Size %{x} - Gap: %{y:.4f}<extra></extra>"
))

fig.update_layout(
    title={
        'text': "Relative Gap Comparison: H1 vs GA",
        'y': 0.95
    },
    annotations=[
        dict(
            text=r"$\text{Relative Gap = } \frac{H1 - GA}{|H1| + \epsilon}$",
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
    xaxis=dict(
        categoryorder='array',
        categoryarray=sizes_sorted
    ),
    height=750
)

fig.show()