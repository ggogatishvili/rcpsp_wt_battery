import json
import plotly.graph_objects as go
import sys

# Colors
GA_COLOR = "#B279A2"

# Parameters
BATTERY_CAPACITY = 16

# Parse arguments, extracting optional flags
args = sys.argv[1:]

show_actual_sizes = "--actual-sizes" in args
if show_actual_sizes:
    args.remove("--actual-sizes")

show_percentage = "--percentage" in args
if show_percentage:
    args.remove("--percentage")

# Load results
if len(args) < 1:
    print("Usage: python script.py <input_file> [--actual-sizes] [--percentage]")
    sys.exit(1)

results_file = args[0]

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

    size_cat = inst.split("_")[0]
    unique_sizes.add(size_cat)

    size_disp = str(int(size_cat) * 32) if show_actual_sizes else size_cat

    # Calculate GA vs H1 gap (Method - Baseline)
    ratio_ga_h1 = (obj_ga - obj_h1) / (abs(obj_h1) + epsilon)
    if show_percentage:
        ratio_ga_h1 *= 100

    x_gap.append(size_disp)
    y_gap.append(ratio_ga_h1)


# Sort sizes dynamically
sizes_sorted_cat = sorted(list(unique_sizes), key=lambda x: int(x))
sizes_sorted_disp = [str(int(s) * 32) if show_actual_sizes else s for s in sizes_sorted_cat]

# Formatting variables
yaxis_title = "Relative Gap [%]" if show_percentage else "Relative Gap"
xaxis_title = "Instance Size [Number of Tasks]" if show_actual_sizes else "Instance Size"

if show_percentage:
    annotation_text = r"$\text{Relative Gap [%] = } \frac{\text{GA} - \text{H1}}{|\text{H1}| + \epsilon} \times 100$"
    y_hover_format = ".2f"
    y_tick_suffix = "%"
else:
    annotation_text = r"$\text{Relative Gap = } \frac{\text{GA} - \text{H1}}{|\text{H1}| + \epsilon}$"
    y_hover_format = ".4f"
    y_tick_suffix = ""

hover_format_gap = "Size %{x} - Gap: %{y}<extra></extra>"

# Create Boxplot
fig = go.Figure()

# Add trace for GA vs H1
fig.add_trace(go.Box(
    x=x_gap,
    y=y_gap,
    name="GA vs H1",
    boxmean=True,
    marker_color=GA_COLOR,
    hovertemplate=hover_format_gap
))

fig.update_layout(
    title={
        'text': "Relative Gap Comparison: GA vs H1",
        'y': 0.95
    },
    annotations=[
        dict(
            text=annotation_text,
            showarrow=False,
            xref="paper", yref="paper",
            x=-0.008, y=1.05,
            xanchor="left", yanchor="bottom",
            font=dict(size=20)
        )
    ],
    margin=dict(t=120),

    yaxis=dict(
        title=yaxis_title,
        hoverformat=y_hover_format,
        ticksuffix=y_tick_suffix
    ),
    xaxis_title=xaxis_title,
    xaxis=dict(
        categoryorder='array',
        categoryarray=sizes_sorted_disp
    ),
    height=750
)

fig.show()