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
if len(sys.argv) not in [3, 4]:
    print("Usage: python script.py <input_file> <method> [--actual-sizes]")
    print("Example: python script.py results.json MILP")
    print("       python script.py results.json MILP --actual-sizes")
    sys.exit(1)

results_file = sys.argv[1]
target_method = sys.argv[2]
show_actual_sizes = (len(sys.argv) == 4 and sys.argv[3] == "--actual-sizes")

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

# Sort sizes numerically based on the categorical value
sizes_sorted = sorted(list(size_data.keys()), key=lambda x: int(x))

# Prepare data arrays for Plotly
display_sizes = []
plot_data = []
hover_data = []

for size in sizes_sorted:
    # Determine the size label to display
    display_size = str(int(size) * 32) if show_actual_sizes else size
    display_sizes.append(display_size)

    times = size_data[size]

    if times:
        avg_time = sum(times) / len(times)
        plot_data.append(avg_time)
        hover_data.append(f"<b>Size: {display_size}</b><br>{target_method} Avg Time: {avg_time:.5f} s<br>(based on {len(times)} instances)")
    else:
        plot_data.append(None)
        hover_data.append(f"<b>Size: {display_size}</b><br>No {target_method} data")

# Prepare figure
fig = go.Figure()

# Add line chart trace for the requested method
color = COLORS.get(target_method.upper(), DEFAULT_COLOR)

fig.add_trace(go.Scatter(
    x=display_sizes,
    y=plot_data,
    name=target_method,
    mode='lines+markers',
    line=dict(color=color, width=3),
    marker=dict(color=color, size=10),
    hovertext=hover_data,
    hovertemplate="%{hovertext}<extra></extra>"
))

# Determine x-axis title
xaxis_title = "Instance Size [Number of Tasks]" if show_actual_sizes else "Instance Size"

# Layout configuration
fig.update_layout(
    title=f"Average Computation Time by Instance Size<br><sup>Method: {target_method}</sup>",
    xaxis=dict(
        title=xaxis_title,
        categoryorder='array',
        categoryarray=display_sizes
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
    margin=dict(t=80),
    template="plotly_white"
)

fig.show()