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

# Prepare figure
fig = go.Figure()

x_positions = []
milp_time = []
h1_time = []
ga_time = []

# Lists to hold dynamic formatting
milp_time_colors = []
milp_hover_text = []
h1_hover_text = []
ga_hover_text = []
reached_time_limits = set()

for inst in instances:
    x_positions.append(inst)

    milp = instance_data[inst].get("MILP")
    h1 = instance_data[inst].get("H1")
    ga = instance_data[inst].get("GA")

    if milp:
        m_time = milp.get("computation_time")
        m_limit = milp.get("time_limit")
        m_gap = milp.get("gap")

        milp_time.append(m_time)

        # Description
        time_str = f"{m_time:.3f} s" if m_time is not None else "N/A"
        hover_str = f"<b>Instance: {inst}</b><br>MILP Time: {time_str}"

        # Add gap
        if m_gap is not None:
            hover_str += f"<br>Gap: {m_gap * 100:.2f}%" if isinstance(m_gap, (int, float)) else f"<br>Gap: {m_gap}"

        # Add time limit info
        if m_time is not None and m_limit is not None and m_time >= m_limit:
            milp_time_colors.append(MILP_TIMEOUT_COLOR)
            hover_str += "<br><br><b>⚠️ Time limit reached</b>"
            reached_time_limits.add(m_limit)
        else:
            milp_time_colors.append(MILP_COLOR)

        milp_hover_text.append(hover_str)
    else:
        milp_time.append(None)
        milp_time_colors.append(MILP_COLOR)
        milp_hover_text.append(f"<b>Instance: {inst}</b><br>No MILP data")

    if h1:
        h_time = h1.get("computation_time")
        h1_time.append(h_time)

        time_str = f"{h_time:.5f} s" if h_time is not None else "N/A"
        h1_hover_text.append(f"<b>Instance: {inst}</b><br>H1 Time: {time_str}")
    else:
        h1_time.append(None)
        h1_hover_text.append(f"<b>Instance: {inst}</b><br>No H1 data")

    if ga:
        g_time = ga.get("computation_time")
        ga_time.append(g_time)

        time_str = f"{g_time:.5f} s" if g_time is not None else "N/A"
        ga_hover_text.append(f"<b>Instance: {inst}</b><br>GA Time: {time_str}")
    else:
        ga_time.append(None)
        ga_hover_text.append(f"<b>Instance: {inst}</b><br>No GA data")

# Calculate averages
valid_milp_times = [t for t in milp_time if t is not None]
avg_milp = sum(valid_milp_times) / len(valid_milp_times) if valid_milp_times else 0

valid_h1_times = [t for t in h1_time if t is not None]
avg_h1 = sum(valid_h1_times) / len(valid_h1_times) if valid_h1_times else 0

valid_ga_times = [t for t in ga_time if t is not None]
avg_ga = sum(valid_ga_times) / len(valid_ga_times) if valid_ga_times else 0

# Construct multi-line title
plot_title = (
    "Computation Time Comparison: MILP vs H1 vs GA<br>"
    f"<span style='font-size:13px; font-weight:normal;'>Average MILP Time: {avg_milp:.3f} s</span><br>"
    f"<span style='font-size:13px; font-weight:normal;'>Average H1 Time: {avg_h1:.5f} s</span><br>"
    f"<span style='font-size:13px; font-weight:normal;'>Average GA Time: {avg_ga:.5f} s</span>"
)


# Time Bars
fig.add_trace(go.Bar(
    x=x_positions,
    y=milp_time,
    name="MILP",
    marker=dict(color=milp_time_colors),
    hovertext=milp_hover_text,
    hovertemplate="%{hovertext}<extra></extra>"
))

fig.add_trace(go.Bar(
    x=x_positions,
    y=h1_time,
    name="H1",
    marker=dict(color=H1_COLOR),
    hovertext=h1_hover_text,
    hovertemplate="%{hovertext}<extra></extra>"
))

fig.add_trace(go.Bar(
    x=x_positions,
    y=ga_time,
    name="GA",
    marker=dict(color=GA_COLOR),
    hovertext=ga_hover_text,
    hovertemplate="%{hovertext}<extra></extra>"
))

# Add horizontal red dotted lines for time limits
for limit in reached_time_limits:
    fig.add_hline(
        y=limit,
        line_dash="dot",
        line_color=MILP_TIMEOUT_COLOR
    )

    fig.add_annotation(
        x=0,
        xref="paper",
        y=math.log10(limit),
        text=f"Time limit: {int(limit)}s",
        showarrow=False,
        xanchor="left",
        yanchor="bottom",
        yshift=2,
        font=dict(color=MILP_TIMEOUT_COLOR, size=12),
        xshift=2
    )

# Layout
fig.update_layout(
    title=plot_title,
    barmode="group",
    xaxis=dict(
        title="Instance",
    ),
    yaxis=dict(
        title="Computation Time [s]",
        type="log",
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1
    ),
    height=750,
    margin=dict(t=200)
)

fig.show()