import json
import sys
import fnmatch
import plotly.graph_objects as go

TARGET_HIGHLIGHT_CAPACITY = 16

# Colors
MILP_COLOR = "#4C78A8"
H1_COLOR = "#F58518"
GA_COLOR = "#B279A2"
SAVINGS_COLOR = "#D3D3D3"
HIGHLIGHT_LINE_COLOR = "#2ca02c"

# Parse arguments
args = sys.argv[1:]
show_actual_sizes = "--actual-sizes" in args
if show_actual_sizes:
    args.remove("--actual-sizes")

if len(args) < 2:
    print("Usage: python script.py <input_file> <method> [instance_pattern] [--actual-sizes]")
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
    if config.get("method") != target_method:
        continue
    cap = config.get("battery_capacity")
    val = entry.get("solution_info", {}).get("objective_value")
    if inst not in instance_data:
        instance_data[inst] = {}
    instance_data[inst][cap] = val

if not instance_data:
    print(f"No valid data found.")
    sys.exit(1)

matched_categorical_sizes = [int(inst.split("_")[0]) for inst in instance_data.keys()]
min_cat_size = min(matched_categorical_sizes)
max_cat_size = max(matched_categorical_sizes)
cat_range = f"{min_cat_size}" if min_cat_size == max_cat_size else f"{min_cat_size}-{max_cat_size}"
actual_range = f"{min_cat_size * 32}" if min_cat_size == max_cat_size else f"{min_cat_size * 32}-{max_cat_size * 32}"
instances_subtitle = f"Instance sizes: {actual_range if show_actual_sizes else cat_range}"

all_capacities = set()
for caps_dict in instance_data.values():
    all_capacities.update(caps_dict.keys())

caps_to_plot = sorted(list(all_capacities))
avg_percentages = []

for cap in caps_to_plot:
    percentage_list = []
    for inst, caps_dict in instance_data.items():
        if 0 in caps_dict and cap in caps_dict:
            val0, val_cap = caps_dict[0], caps_dict[cap]
            if val0 and val0 > 0:
                percentage_list.append((val_cap / val0) * 100)
    avg_percentages.append(sum(percentage_list) / len(percentage_list) if percentage_list else 0.0)

savings_percentages = [max(0, 100 - p) for p in avg_percentages]

fig = go.Figure()
method_color = {"MILP": MILP_COLOR, "H1": H1_COLOR, "GA": GA_COLOR}.get(target_method, MILP_COLOR)

# Bars
fig.add_trace(go.Bar(
    name="Remaining cost",
    x=caps_to_plot, y=avg_percentages,
    marker_color=method_color, width=0.4,
    text=[f"{val:.0f}%" if val > 0 else "" for val in avg_percentages],
    textposition="inside"
))

fig.add_trace(go.Bar(
    name="Savings",
    x=caps_to_plot, y=savings_percentages,
    marker_color=SAVINGS_COLOR, width=0.4,
    text=[f"{val:.0f}%" if val > 0 else "" for val in savings_percentages],
    textposition="inside"
))

# Dummy trace for the legend (Green Highlight)
fig.add_trace(go.Scatter(
    x=[None], y=[None],
    mode='markers',
    marker=dict(
        size=10,
        symbol='square-open',
        color=HIGHLIGHT_LINE_COLOR,
        line=dict(color=HIGHLIGHT_LINE_COLOR, width=2)
    ),
    legendgroup="highlight",
    showlegend=True,
    name="The chosen capacity for further testing"
))

# Highlight logic
if TARGET_HIGHLIGHT_CAPACITY in caps_to_plot:
    target_idx = caps_to_plot.index(TARGET_HIGHLIGHT_CAPACITY)

    fig.add_shape(
        type="rect",
        xref="x", yref="y",
        x0=target_idx - 0.2,
        y0=0,
        x1=target_idx + 0.2,
        y1=100,
        line=dict(color=HIGHLIGHT_LINE_COLOR, width=4),
        fillcolor="rgba(0,0,0,0)"
    )

fig.add_hline(y=100, line_dash="dash", line_color="black",
              annotation_text="Original price without battery", annotation_position="top")

fig.update_layout(
    barmode="stack",
    title=f"Average Savings and Remaining Cost by Battery Capacity<br><sup>Method: {target_method} | {instances_subtitle}</sup>",
    xaxis_title="Battery Capacity [MWh]",
    yaxis_title="Average Percentage [%]",
    template="plotly_white",
    height=600,
    showlegend=True,
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1
    )
)

fig.update_xaxes(type='category')
fig.update_yaxes(range=[0, 115])
fig.show()