import json
import plotly.graph_objects as go
import sys
import fnmatch


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
        "objective_value": entry["solution_info"]["objective_value"],
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

time_domain = [0.05, 0.45]
cost_domain = [0.55, 0.98]

x_positions = []
milp_energy = []
h1_energy = []
milp_time = []
h1_time = []

# Lists to hold dynamic formatting
milp_time_colors = []
milp_hover_text = []
h1_hover_text = []

for inst in instances:
    x_positions.append(inst)

    milp = instance_data[inst].get("MILP")
    h1 = instance_data[inst].get("HEURISTIC1")

    if milp:
        m_cost = milp.get("objective_value")
        m_time = milp.get("computation_time")
        m_limit = milp.get("time_limit")
        m_gap = milp.get("gap")

        milp_energy.append(m_cost)
        milp_time.append(m_time)

        # Description
        cost_str = f"{m_cost:.2f} EUR" if m_cost is not None else "N/A"
        time_str = f"{m_time:.3f} s" if m_time is not None else "N/A"
        hover_str = f"<b>Instance: {inst}</b><br>MILP Cost: {cost_str}<br>MILP Time: {time_str}"

        # Add gap
        if m_gap is not None:
            hover_str += f"<br>Gap: {m_gap * 100:.2f}%" if isinstance(m_gap, (int, float)) else f"<br>Gap: {m_gap}"

        # Add time limit info
        if m_time is not None and m_limit is not None and m_time >= m_limit:
            milp_time_colors.append(MILP_TIMEOUT_COLOR)
            hover_str += "<br><br><b>⚠️ Time limit reached</b>"
        else:
            milp_time_colors.append(MILP_COLOR)

        milp_hover_text.append(hover_str)
    else:
        milp_energy.append(None)
        milp_time.append(None)
        milp_time_colors.append(MILP_COLOR)
        milp_hover_text.append(f"<b>Instance: {inst}</b><br>No MILP data")


    # HEURISTIC1
    if h1:
        h_cost = h1.get("objective_value")
        h_time = h1.get("computation_time")

        h1_energy.append(h_cost)
        h1_time.append(h_time)

        cost_str = f"{h_cost:.2f} EUR" if h_cost is not None else "N/A"
        time_str = f"{h_time:.5f} s" if h_time is not None else "N/A"

        h1_hover_text.append(f"<b>Instance: {inst}</b><br>H1 Cost: {cost_str}<br>H1 Time: {time_str}")
    else:
        h1_energy.append(None)
        h1_time.append(None)
        h1_hover_text.append(f"<b>Instance: {inst}</b><br>No H1 data")

# Energy Bars
fig.add_trace(go.Bar(
    x=x_positions,
    y=milp_energy,
    name="MILP",
    legendgroup="MILP",
    marker=dict(color=MILP_COLOR),
    yaxis="y2",
    hovertext=milp_hover_text,
    hovertemplate="%{hovertext}<extra></extra>"
))

fig.add_trace(go.Bar(
    x=x_positions,
    y=h1_energy,
    name="HEURISTIC1",
    legendgroup="HEURISTIC1",
    marker=dict(color=H1_COLOR),
    yaxis="y2",
    hovertext=h1_hover_text,
    hovertemplate="%{hovertext}<extra></extra>"
))


# Time Bars
fig.add_trace(go.Bar(
    x=x_positions,
    y=milp_time,
    name="MILP (time)",
    legendgroup="MILP",
    marker=dict(color=milp_time_colors),
    yaxis="y",
    showlegend=False,
    hovertext=milp_hover_text,
    hovertemplate="%{hovertext}<extra></extra>"
))

fig.add_trace(go.Bar(
    x=x_positions,
    y=h1_time,
    name="HEURISTIC1 (time)",
    legendgroup="HEURISTIC1",
    marker=dict(color=H1_COLOR),
    yaxis="y",
    showlegend=False,
    hovertext=h1_hover_text,
    hovertemplate="%{hovertext}<extra></extra>"
))


# Layout
fig.update_layout(

    title="Benchmark Comparison: MILP vs HEURISTIC1",

    barmode="group",

    # X axis shared
    xaxis=dict(
        title="Instance",
        domain=[0, 1]
    ),

    # Top: energy cost
    yaxis2=dict(
        title="Total Cost [EUR]",
        domain=cost_domain,
        anchor="x"
    ),

    # Bottom: computation time
    yaxis=dict(
        title="Computation Time [s]",
        type="log",
        domain=time_domain,
        anchor="x"
    ),

    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1
    ),

    height=800
)

fig.show()