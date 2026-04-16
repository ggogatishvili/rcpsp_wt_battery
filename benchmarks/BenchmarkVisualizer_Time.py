import json
import plotly.graph_objects as go
import sys
import fnmatch
import math

# Colors
MILP_COLOR = "#4C78A8"
MILP_TIMEOUT_COLOR = "#FF5244"
H1_COLOR = "#F58518"
GA_COLOR = "#B279A2"

# Parameters
BATTERY_CAPACITY = 16

# Parse arguments, extracting the optional flag
args = sys.argv[1:]

show_actual_sizes = "--actual-sizes" in args
if show_actual_sizes:
    args.remove("--actual-sizes")

# Load and sort results
if len(args) < 1:
    print("Usage: python script.py <input_file> [instance_pattern] [--actual-sizes]")
    print("Example: python script.py results.json '1_*' --actual-sizes")
    sys.exit(1)

results_file = args[0]
instance_pattern = args[1] if len(args) > 1 else "*"

with open(results_file) as f:
    data = json.load(f)

instance_data = {}

for entry in data:
    if entry["config"]["battery_capacity"] != BATTERY_CAPACITY:
        continue

    inst = entry["instance"]

    if not fnmatch.fnmatch(inst, instance_pattern):
        continue

    method = entry["config"]["method"]

    if inst not in instance_data:
        instance_data[inst] = {}

    instance_data[inst][method] = {
        "computation_time": entry["solution_info"]["computation_time"],
        "time_limit": entry["config"]["time_limit"],
        "gap": entry["solution_info"]["gap"]
    }

def numeric_key(name):
    parts = name.split("_")
    return tuple(int(p) for p in parts)

instances = sorted(instance_data.keys(), key=numeric_key)

x_positions = []
milp_time, h1_time, ga_time = [], [], []
milp_hover_text, h1_hover_text, ga_hover_text = [], [], []
milp_marker_colors = []
reached_time_limits = set()

for inst in instances:
    parts = inst.split("_")
    display_inst = f"{int(parts[0]) * 32}_{parts[1]}" if show_actual_sizes and len(parts) >= 2 else inst
    x_positions.append(display_inst)

    methods = ["MILP", "H1", "GA"]
    for m in methods:
        data_entry = instance_data[inst].get(m)
        time_val = data_entry.get("computation_time") if data_entry else None

        t_str = f"{time_val:.5f} s" if time_val is not None else "N/A"
        h_str = f"<b>Instance: {display_inst}</b><br>{m} Time: {t_str}"

        if m == "MILP":
            milp_time.append(time_val)
            if data_entry:
                m_gap = data_entry.get("gap")
                m_limit = data_entry.get("time_limit")
                if m_gap is not None:
                    h_str += f"<br>Gap: {m_gap * 100:.2f}%" if isinstance(m_gap, (int, float)) else f"<br>Gap: {m_gap}"
                if time_val and m_limit and time_val >= m_limit:
                    milp_marker_colors.append(MILP_TIMEOUT_COLOR)
                    h_str += "<br><br><b>⚠️ Time limit reached</b>"
                    reached_time_limits.add(m_limit)
                else:
                    milp_marker_colors.append(MILP_COLOR)
            else:
                milp_marker_colors.append(MILP_COLOR)
            milp_hover_text.append(h_str)
        elif m == "H1":
            h1_time.append(time_val)
            h1_hover_text.append(h_str)
        else:
            ga_time.append(time_val)
            ga_hover_text.append(h_str)

def get_avg(lst):
    v = [t for t in lst if t is not None]
    return sum(v) / len(v) if v else 0

avg_milp, avg_h1, avg_ga = get_avg(milp_time), get_avg(h1_time), get_avg(ga_time)


plot_title = (
    "Computation Time Comparison: MILP vs H1 vs GA<br>"
    f"<span style='font-size:13px; font-weight:normal;'>Average MILP Time: {avg_milp:.3f} s</span><br>"
    f"<span style='font-size:13px; font-weight:normal;'>Average H1 Time: {avg_h1:.5f} s</span><br>"
    f"<span style='font-size:13px; font-weight:normal;'>Average GA Time: {avg_ga:.5f} s</span>"
)

fig = go.Figure()

# Dummy trace to force the MILP legend color to remain blue
fig.add_trace(go.Scatter(
    x=[None], y=[None], name="MILP",
    mode='lines+markers', line=dict(color=MILP_COLOR, width=3),
    marker=dict(color=MILP_COLOR, size=10),
    legendgroup="milp_group",
    showlegend=True
))

# Actual MILP data trace
fig.add_trace(go.Scatter(
    x=x_positions, y=milp_time, name="MILP",
    mode='lines+markers', line=dict(color=MILP_COLOR, width=3),
    marker=dict(color=milp_marker_colors, size=10),
    hovertext=milp_hover_text, hovertemplate="%{hovertext}<extra></extra>",
    legendgroup="milp_group",
    showlegend=False
))

fig.add_trace(go.Scatter(
    x=x_positions, y=h1_time, name="H1",
    mode='lines+markers', line=dict(color=H1_COLOR, width=3),
    marker=dict(size=10),
    hovertext=h1_hover_text, hovertemplate="%{hovertext}<extra></extra>"
))

fig.add_trace(go.Scatter(
    x=x_positions, y=ga_time, name="GA",
    mode='lines+markers', line=dict(color=GA_COLOR, width=3),
    marker=dict(size=10),
    hovertext=ga_hover_text, hovertemplate="%{hovertext}<extra></extra>"
))

# Add horizontal red dotted lines for time limits
for limit in reached_time_limits:
    fig.add_hline(
        y=limit,
        line_dash="dot",
        line_color=MILP_TIMEOUT_COLOR
    )

    fig.add_annotation(
        x=0.5,
        xref="paper",
        y=math.log10(limit),
        text=f"Time limit: {int(limit)}s",
        showarrow=False,
        xanchor="center",
        yanchor="bottom",
        yshift=10,
        font=dict(color=MILP_TIMEOUT_COLOR, size=12)
    )

# Layout
fig.update_layout(
    title=plot_title,
    xaxis=dict(
        title="Instance",
        type='category'
    ),
    yaxis=dict(title="Computation Time [s]", type="log"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=750,
    margin=dict(t=200),
    template="plotly_white"
)

fig.show()