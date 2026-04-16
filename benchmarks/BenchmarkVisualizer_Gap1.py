import json
import plotly.graph_objects as go
import sys

# Colors
MILP_COLOR = "#4C78A8"
H1_COLOR = "#F58518"
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

    size_cat = inst.split("_")[0]
    unique_sizes.add(size_cat)

    size_disp = str(int(size_cat) * 32) if show_actual_sizes else size_cat

    # Calculate H1 gap
    if obj_h1 is not None:
        ratio_h1 = (obj_h1 - obj_milp) / (abs(obj_milp) + epsilon)
        if show_percentage:
            ratio_h1 *= 100
        x_h1.append(size_disp)
        y_h1.append(ratio_h1)

    # Calculate GA gap
    if obj_ga is not None:
        ratio_ga = (obj_ga - obj_milp) / (abs(obj_milp) + epsilon)
        if show_percentage:
            ratio_ga *= 100
        x_ga.append(size_disp)
        y_ga.append(ratio_ga)


# Sort sizes
sizes_sorted_cat = sorted(list(unique_sizes), key=lambda x: int(x))
sizes_sorted_disp = [str(int(s) * 32) if show_actual_sizes else s for s in sizes_sorted_cat]

# Formatting variables
yaxis_title = "Relative Gap [%]" if show_percentage else "Relative Gap"
xaxis_title = "Instance Size [Number of Tasks]" if show_actual_sizes else "Instance Size"

if show_percentage:
    annotation_text = r"$\text{Relative Gap [%] = } \frac{\text{Method} - \text{MILP}}{|\text{MILP}| + \epsilon} \times 100$"
    y_hover_format = ".2f"
    y_tick_suffix = "%"
else:
    annotation_text = r"$\text{Relative Gap = } \frac{\text{Method} - \text{MILP}}{|\text{MILP}| + \epsilon}$"
    y_hover_format = ".4f"
    y_tick_suffix = ""

hover_format_h1 = "Size %{x} - H1 Gap: %{y}<extra></extra>"
hover_format_ga = "Size %{x} - GA Gap: %{y}<extra></extra>"

# Create Grouped Boxplot
fig = go.Figure()

# Add trace for H1
fig.add_trace(go.Box(
    x=x_h1,
    y=y_h1,
    name="H1 (Baseline: MILP)",
    boxmean=True,
    marker_color=H1_COLOR,
    hovertemplate=hover_format_h1
))

# Add trace for GA
fig.add_trace(go.Box(
    x=x_ga,
    y=y_ga,
    name="GA (Baseline: MILP)",
    boxmean=True,
    marker_color=GA_COLOR,
    hovertemplate=hover_format_ga
))

# Add MILP baseline indicator
fig.add_hline(
    y=0,
    line_dash="solid",
    line_color=MILP_COLOR,
    annotation_text="MILP baseline",
    annotation_position="bottom left",
    annotation_xshift=10,
    annotation_font_color=MILP_COLOR,
    annotation_font_size=12
)

# Add formula annotation safely so it does not overwrite the line text
fig.add_annotation(
    text=annotation_text,
    showarrow=False,
    xref="paper", yref="paper",
    x=-0.008, y=1.05,
    xanchor="left", yanchor="bottom",
    font=dict(size=20)
)

fig.update_layout(
    title={
        'text': "Relative Gap of H1 and GA Compared to MILP Baseline",
        'y': 0.95
    },
    margin=dict(t=120),
    yaxis=dict(
        title=yaxis_title,
        hoverformat=y_hover_format,
        ticksuffix=y_tick_suffix
    ),
    xaxis_title=xaxis_title,
    boxmode='group',
    xaxis=dict(
        categoryorder='array',
        categoryarray=sizes_sorted_disp
    ),
    height=750
)

fig.show()