import json
import plotly.graph_objects as go
import sys

# Colors dictionary for dynamic lookup
COLORS = {
    "MILP": "#4C78A8",
    "H1": "#F58518",
    "GA": "#B279A2"
}
DEFAULT_COLOR = "#7F7F7F"

# Parameters
BATTERY_CAPACITY = 16

# Load results and parse arguments
if len(sys.argv) != 3:
    print("Usage: python script.py <input_file> <method>")
    print("Example: python script.py results.json MILP")
    sys.exit(1)

results_file = sys.argv[1]
target_method = sys.argv[2]

with open(results_file) as f:
    data = json.load(f)

# Organize data per instance size for the target method
size_data = {}

for entry in data:
    if entry["config"]["battery_capacity"] != BATTERY_CAPACITY:
        continue

    method = entry["config"]["method"]

    if method != target_method:
        continue

    inst = entry["instance"]
    size = inst.split("_")[0]
    comp_time = entry["solution_info"]["computation_time"]

    if size not in size_data:
        size_data[size] = []

    if comp_time is not None:
        size_data[size].append(comp_time)

# Sort sizes numerically
sizes_sorted = sorted(list(size_data.keys()), key=lambda x: int(x))

# Prepare data arrays for Plotly
plot_data = []
hover_data = []

for size in sizes_sorted:
    times = size_data[size]

    if times:
        avg_time = sum(times) / len(times)
        plot_data.append(avg_time)
        hover_data.append(f"<b>Size: {size}</b><br>{target_method} Avg Time: {avg_time:.5f} s<br>(based on {len(times)} instances)")
    else:
        plot_data.append(None)
        hover_data.append(f"<b>Size: {size}</b><br>No {target_method} data")

# Prepare figure
fig = go.Figure()

# Add bar trace for the requested method
color = COLORS.get(target_method.upper(), DEFAULT_COLOR)

fig.add_trace(go.Bar(
    x=sizes_sorted,
    y=plot_data,
    name=target_method,
    marker=dict(color=color),
    hovertext=hover_data,
    hovertemplate="%{hovertext}<extra></extra>"
))

# Layout configuration
fig.update_layout(
    title=f"Average Computation Time by Instance Size<br><sup>Method: {target_method}</sup>",
    barmode="group",
    xaxis=dict(
        title="Instance Size",
        categoryorder='array',
        categoryarray=sizes_sorted
    ),
    yaxis=dict(
        title="Average Computation Time [s]"
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1
    ),
    height=600,
    margin=dict(t=80)
)

fig.show()