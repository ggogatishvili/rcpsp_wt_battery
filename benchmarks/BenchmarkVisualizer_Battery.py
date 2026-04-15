import json
import sys
import fnmatch
import plotly.graph_objects as go

# Colors
MILP_COLOR = "#4C78A8"
H1_COLOR = "#F58518"
GA_COLOR = "#B279A2"

# Parse arguments, extracting the optional flag
args = sys.argv[1:]
show_actual_sizes = "--actual-sizes" in args
if show_actual_sizes:
    args.remove("--actual-sizes")

# Load and sort results
if len(args) < 2:
    print("Usage: python script.py <input_file> <method> [instance_pattern] [--actual-sizes]")
    print("Example: python script.py results.json MILP '1_*' --actual-sizes")
    sys.exit(1)

results_file = args[0]
target_method = args[1]
instance_pattern = args[2] if len(args) > 2 else "*"

with open(results_file) as f:
    data = json.load(f)

instance_data = {}

for entry in data:
    inst = entry.get("instance")

    if not fnmatch.fnmatch(inst, instance_pattern):
        continue

    config = entry.get("config", {})
    method = config.get("method")

    if method != target_method:
        continue

    cap = config.get("battery_capacity")
    val = entry.get("solution_info", {}).get("objective_value")

    if inst not in instance_data:
        instance_data[inst] = {}

    instance_data[inst][cap] = val

if not instance_data:
    print(f"No valid data found for method '{target_method}' matching pattern '{instance_pattern}'.")
    sys.exit(1)

# Extract categorical sizes from matched instances (format: size_index.txt)
matched_categorical_sizes = [int(inst.split("_")[0]) for inst in instance_data.keys()]
min_cat_size = min(matched_categorical_sizes)
max_cat_size = max(matched_categorical_sizes)

# Determine the subtitle string based on the switch
if min_cat_size == max_cat_size:
    cat_range = f"{min_cat_size}"
    actual_range = f"{min_cat_size * 32}"
else:
    cat_range = f"{min_cat_size}-{max_cat_size}"
    actual_range = f"{min_cat_size * 32}-{max_cat_size * 32}"

if show_actual_sizes:
    instances_subtitle = f"Instance sizes: {actual_range} [Number of Tasks]"
else:
    instances_subtitle = f"Instance sizes: {cat_range}"

all_capacities = set()
for caps_dict in instance_data.values():
    all_capacities.update(caps_dict.keys())

if 0 not in all_capacities and len(all_capacities) > 0:
    print(f"Error: Capacity 0 is missing in the data for method '{target_method}'. Cannot calculate percentages.")
    sys.exit(1)

caps_to_plot = sorted([c for c in all_capacities if c != 0])

avg_percentages = []

# Calculate average percentage of original price for each capacity
for cap in caps_to_plot:
    percentage_list = []
    for inst, caps_dict in instance_data.items():
        if 0 in caps_dict and cap in caps_dict:
            val0 = caps_dict[0]
            val_cap = caps_dict[cap]

            if val0 is not None and val_cap is not None and val0 > 0:
                # Calculate percentage relative to the original price (capacity 0)
                perc = (val_cap / val0) * 100
                percentage_list.append(perc)

    if percentage_list:
        avg_percentages.append(sum(percentage_list) / len(percentage_list))
    else:
        avg_percentages.append(0.0)

# Prepare figure
fig = go.Figure()

method_color = {
    "MILP": MILP_COLOR,
    "H1": H1_COLOR,
    "GA": GA_COLOR,
}.get(target_method, MILP_COLOR)

fig.add_trace(go.Scatter(
    x=caps_to_plot,
    y=avg_percentages,
    mode='lines+markers+text',
    line=dict(color=method_color, width=3),
    marker=dict(size=8, color=method_color),
    text=[f"{val:.2f}%" for val in avg_percentages],
    textposition="top center",
    hovertemplate="Capacity: %{x} MWh<br>Avg Percentage: %{y:.2f}%<extra></extra>"
))

# Add horizontal line at 100%
fig.add_hline(
    y=100,
    line_dash="dash",
    line_color=method_color,
    annotation_text="Original price without battery",
    annotation_position="top"
)

# Layout
fig.update_layout(
    title=f"Relative Total Cost by Battery Capacity<br><sup>Method: {target_method} | {instances_subtitle}</sup>",
    xaxis_title="Battery Capacity [MWh]",
    yaxis_title="Relative Cost [%]",
    template="plotly_white",
    height=600,
    margin=dict(t=80)
)

fig.update_xaxes(type='category')

if avg_percentages:
    max_val = max(max(avg_percentages), 100)
    fig.update_yaxes(range=[0, max_val * 1.15])

fig.show()