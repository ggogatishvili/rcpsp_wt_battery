import json
import plotly.graph_objects as go
import os

file = "../results/1_10.json"
filename = os.path.basename(file)

# Load JSON data
with open(file, "r") as f:
    data = json.load(f)

battery = data["battery_levels"]
energy = data["instance_summary"]["energy_costs"]
tasks = data["task_assignments"]
resource_count = data["instance_summary"]["resource_count"]
objective_value = data.get("objective_value", None)

# --- Assign task rows ---
def assign_task_rows(tasks):
    rows = []
    task_rows = {}
    for t in sorted(tasks, key=lambda x: x["start_time"]):
        start, end = t["start_time"], t["end_time"]
        for i, intervals in enumerate(rows):
            if all(end <= s or start >= e for s, e in intervals):
                intervals.append((start, end))
                task_rows[t["task_id"]] = i
                break
        else:
            rows.append([(start, end)])
            task_rows[t["task_id"]] = len(rows) - 1
    return task_rows, len(rows)

task_rows, max_task_rows = assign_task_rows(tasks)

# --- Create figure ---
fig = go.Figure()

# Define vertical domains (stacked layout)
task_domain = [0.0, 0.4]
machine_state_domain = [0.4, 0.45]
battery_domain = [0.45, 0.65]
energy_domain = [0.65, 1.0]

# --- TASK BARS ---
COLORS = {
    "regular": "#90EE90",
    "energy_intensive": "#006400",
    "tardy_regular": "#FFA500",
    "tardy_energy_intensive": "#FF7500"
}

for t in tasks:
    row = task_rows[t["task_id"]]
    uses_R0 = t["resource_requests"][0] > 0
    is_tardy = t.get("due_date", float('inf')) < t["end_time"]

    if is_tardy and uses_R0:
        color = COLORS["tardy_energy_intensive"]
    elif is_tardy:
        color = COLORS["tardy_regular"]
    elif uses_R0:
        color = COLORS["energy_intensive"]
    else:
        color = COLORS["regular"]

    task_name = f"T{t['task_id']}"
    start, end = t["start_time"], t["end_time"]
    resources_desc = ", ".join([f"R{i}={r}" for i, r in enumerate(t["resource_requests"])])
    hover_text = (
        f"<b>{task_name}</b><br>"
        f"Start: {t['start_time']}<br>"
        f"End: {t['end_time']}<br>" 
        f"Duration: {t['duration']}<br>"
        f"Release: {t['release_date']}<br>"
        f"Due: {t['due_date']}<br>"
        f"Weight: {t['weight']}<br>"
        f"Resources: {resources_desc}"
    )

    fig.add_trace(go.Scatter(
        x=[start, end + 1, end + 1, start],   # The end is inclusive, so we add 1 to extend it to the end of current time interval
        y=[row, row, row + 0.8, row + 0.8],
        fill="toself",
        name=task_name,
        text=hover_text,
        hoverinfo="text",
        mode="lines",
        line=dict(width=1, color=color),
        opacity=0.7,
        showlegend=False,
        yaxis="y"
    ))

# --- MACHINE STATE LINE ---
# --- MACHINE STATE LINE ---
STATE_COLORS = {
    "Off": "lightcoral",       # light red
    "Idle": "lightsalmon",     # light orange
    "Proc": "lightgreen"       # light green
}

machine_blocks = data["machine_blocks"]

for block in machine_blocks:
    state = block["description"]
    start, end = block["start_time"], block["end_time"]
    color = STATE_COLORS.get(state, "lightgrey")  # fallback color

    fig.add_trace(go.Scatter(
        x=[start, end + 1, end + 1, start], # The end is inclusive, so we add 1 to extend it to the end of current time interval
        y=[0, 0, 1, 1],  # fill a single line from 0 to 1 in yaxis2
        fill="toself",
        mode="lines",
        line=dict(width=1, color=color),
        showlegend=False,
        yaxis="y2",
        hoverinfo="text",
        text=f"State: {state}<br>Start: {start}<br>End: {end}"
    ))


# --- BATTERY LINE ---
fig.add_trace(go.Scatter(
    x=list(range(len(battery))),
    y=battery,
    mode="lines+markers",
    name="Battery level",
    line=dict(color="blue"),
    hovertemplate="Battery level: %{y:.2f}<extra></extra>",
    yaxis="y3"
))

# --- ENERGY COST LINE ---
fig.add_trace(go.Scatter(
    x=list(range(len(energy))),
    y=energy,
    mode="lines+markers",
    name="Energy cost",
    line=dict(color="red"),
    hovertemplate="Energy cost: %{y:.2f}<extra></extra>",
    yaxis="y4"
))

# --- LEGEND ITEMS ---
legend_items = [
    (COLORS["regular"], "Regular task"),
    (COLORS["energy_intensive"], "Energy-intensive task"),
    (COLORS["tardy_regular"], "Tardy regular task"),
    (COLORS["tardy_energy_intensive"], "Tardy energy-intensive task"),
]

for color, desc in legend_items:
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode="markers",
        marker=dict(size=10, color=color),
        legendgroup="legend",
        showlegend=True,
        name=desc
    ))

# --- LAYOUT ---
fig.update_layout(
    title=f"Schedule Visualization – {filename}"
          + (f"<br><sup>Total cost: {objective_value:.2f}</sup>" if objective_value is not None else ""),
    xaxis=dict(title="Time", domain=[0, 1]),

    # Tasks
    yaxis=dict(
        title="Tasks",
        domain=task_domain,
        showticklabels=False
    ),

    # Machine states
    yaxis2=dict(
        title=dict(text="Machine State", font=dict(color="grey")),
        tickfont=dict(color="grey"),
        domain=machine_state_domain,
        anchor="x",
        side="left",
        showticklabels=False
    ),

    # Battery
    yaxis3=dict(
        title=dict(text="Battery Level", font=dict(color="blue")),
        tickfont=dict(color="blue"),
        domain=battery_domain,
        anchor="x",
        side="left"
    ),

    # Energy
    yaxis4=dict(
        title=dict(text="Energy Cost", font=dict(color="red")),
        tickfont=dict(color="red"),
        domain=energy_domain,
        anchor="x",
        side="left"
    ),

    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=700
)

fig.show()
