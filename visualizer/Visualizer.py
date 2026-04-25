import sys
import json
import plotly.graph_objects as go
import os


# Parse command line arguments
args = sys.argv[1:]

show_actual_sizes = "--actual-sizes" in args
if show_actual_sizes:
    args.remove("--actual-sizes")

show_actual_time = "--actual-time" in args
if show_actual_time:
    args.remove("--actual-time")

if len(args) < 1:
    print("Usage: python script.py <input_file> [--actual-sizes] [--actual-time]")
    sys.exit(1)

file = args[0]
filename = os.path.basename(file)

if show_actual_sizes:
    parts = filename.split("_", 1)
    if len(parts) == 2 and parts[0].isdigit():
        filename = f"{int(parts[0]) * 32}_{parts[1]}"


# Load JSON data
with open(file, "r") as f:
    data = json.load(f)

battery = data["battery_levels"]
energy = data["instance_summary"]["energy_costs"]
tasks = data["task_assignments"]
resource_count = data["instance_summary"]["resource_count"]
objective_value = data["solution_info"]["objective_value"]
energy_cost = data["solution_info"]["energy_cost"]
tardiness_cost = data["solution_info"]["tardiness_cost"]


# Time formatting
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def hour_to_full_str(hour_index):
    if not show_actual_time:
        return str(hour_index)

    week = hour_index // 168
    hour_in_week = hour_index % 168
    day = hour_in_week // 24
    hour = hour_in_week % 24
    return f"Week {week+1} - {DAYS[day]} {hour:02d}:00"

def hour_to_short_str(hour_index):
    if not show_actual_time:
        return str(hour_index)

    hour = hour_index % 24
    return f"{hour:02d}"

# Colors
ENERGY_COLOR = "#FF5C46"
BATTERY_COLOR = "#A876FF"
STATE_COLORS = {
    "Off": "#B9B9B9",
    "Transition": "#6D6D6D",
    "Idle": "#5CA3AF",
    "Proc": "#005EBC",
}
TASK_COLORS = {
    "regular": "#90EE90",
    "energy_intensive": "#006400",
    "tardy_regular": "#FFC547",
    "tardy_energy_intensive": "#D66C00"
}

# --- Create figure ---
fig = go.Figure()

# Define vertical domains (stacked layout)
task_domain = [0.0, 0.4]
machine_state_domain = [0.4, 0.45]
battery_domain = [0.45, 0.65]
energy_domain = [0.65, 1.0]

time_horizon = len(energy)

# --- ENERGY COST LINE ---
fig.add_trace(go.Scatter(
    x=list(range(time_horizon)),
    y=energy,
    mode="lines+markers",
    name="Energy cost",
    line=dict(color=ENERGY_COLOR),
    hovertemplate="Energy cost: %{y:.2f}<extra></extra>",
    yaxis="y4"
))

# --- BATTERY LINE ---
fig.add_trace(go.Scatter(
    x=list(range(time_horizon)),
    y=battery,
    mode="lines+markers",
    name="Battery level",
    line=dict(color=BATTERY_COLOR),
    hovertemplate="Battery level: %{y:.2f}<extra></extra>",
    yaxis="y3"
))

# --- MACHINE STATE LINE ---
for block in data["machine_blocks"]:
    state = block["description"]
    start, end = block["start_time"], block["end_time"]
    color = STATE_COLORS.get(state, STATE_COLORS.get("Transition"))

    display_end = end + 1 if show_actual_time else end

    fig.add_trace(go.Scatter(
        x=[start, end + 1, end + 1, start], # The end is inclusive, so we add 1 to extend it to the end of current time interval
        y=[0, 0, 1, 1],  # fill a single line from 0 to 1 in yaxis2
        fill="toself",
        mode="lines",
        line=dict(width=1, color=color),
        showlegend=False,
        yaxis="y2",
        hoverinfo="text",
        text=(
            f"State: {state}<br>"
            f"Start: {hour_to_full_str(start)}<br>"
            f"End: {hour_to_full_str(display_end)}"
        )
    ))

# --- TASK BARS ---
def assign_task_rows(tasks):
    rows = []
    task_rows = {}
    for t in sorted(tasks, key=lambda x: x["start_time"]):
        start, end = t["start_time"], t["end_time"]
        for i, intervals in enumerate(rows):
            if all(end < s or start > e for s, e in intervals):
                intervals.append((start, end))
                task_rows[t["task_id"]] = i
                break
        else:
            rows.append([(start, end)])
            task_rows[t["task_id"]] = len(rows) - 1
    return task_rows, len(rows)

task_rows, max_task_rows = assign_task_rows(tasks)

for t in tasks:
    row = task_rows[t["task_id"]]
    uses_R0 = t["resource_requests"][0] > 0
    is_tardy = t.get("due_date", float('inf')) < t["end_time"]

    if is_tardy and uses_R0:
        color = TASK_COLORS["tardy_energy_intensive"]
    elif is_tardy:
        color = TASK_COLORS["tardy_regular"]
    elif uses_R0:
        color = TASK_COLORS["energy_intensive"]
    else:
        color = TASK_COLORS["regular"]

    task_name = f"T{t['task_id']+1}" # Task IDs are 0-based in data, so we add 1 for display
    start, end = t["start_time"], t["end_time"]

    display_end = end + 1 if show_actual_time else end
    display_due = t['due_date'] + 1 if show_actual_time else t['due_date']

    resources_desc = ", ".join([f"R{i}={r}" for i, r in enumerate(t["resource_requests"])])
    successors = [f"T{int(s) + 1}" for s in t["successors"]] # Convert to 1-based IDs
    successors_str = ", ".join(successors) if successors else "None"
    hover_text = (
        f"<b>{task_name}</b><br>"
        f"Start: {hour_to_full_str(start)}<br>"
        f"End: {hour_to_full_str(display_end)}<br>"
        f"Duration: {t['duration']} [h]<br>"
        f"Release: {hour_to_full_str(t['release_date'])}<br>"
        f"Due: {hour_to_full_str(display_due)}<br>"
        f"Tardiness cost: {t['weight']} [EUR/h]<br>"
        f"Resources: {resources_desc}<br>"
        f"Successors: {successors_str}"
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

    x_center = (start + end + 1) / 2  # center of the bar (end is inclusive, bars use end+1)
    y_center = row + 0.4              # middle of the bar vertical span (row .. row+0.8)
    fig.add_annotation(
        x=x_center,
        y=y_center,
        text=task_name,
        showarrow=False,
        font=dict(color="black", size=10),
        align="center",
        xanchor="center",
        yanchor="middle",
        bgcolor="rgba(255,255,255,0.6)"  # optional: semi-transparent bg for readability
    )

# --- LEGEND ITEMS ---
legend_items1 = [
    (STATE_COLORS["Off"], "Machine off"),
    (STATE_COLORS["Transition"], "Machine transitioning"),
    (STATE_COLORS["Idle"], "Machine idle"),
    (STATE_COLORS["Proc"], "Machine processing"),
]

for color, desc in legend_items1:
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode="markers",
        marker=dict(size=10, color=color),
        legendgroup="legend1",
        showlegend=True,
        name=desc
    ))

legend_items2 = [
    (TASK_COLORS["regular"], "Regular task"),
    (TASK_COLORS["energy_intensive"], "Energy-intensive task"),
    (TASK_COLORS["tardy_regular"], "Tardy regular task"),
    (TASK_COLORS["tardy_energy_intensive"], "Tardy energy-intensive task"),
]

for color, desc in legend_items2:
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode="markers",
        marker=dict(size=10, color=color),
        legendgroup="legend2",
        showlegend=True,
        name=desc
    ))

# --- LAYOUT ---

if show_actual_time:
    # Day labels
    total_days = (time_horizon - 1) // 24 + 1
    for d in range(total_days):
        day_start = d * 24
        day_name = DAYS[d % 7]

        fig.add_annotation(
            x=day_start,
            y=-0.10,
            xref="x",
            yref="paper",
            text=day_name,
            showarrow=False,
            xanchor="left",
            font=dict(size=11)
        )

    # Week labels
    total_weeks = (time_horizon - 1) // 168 + 1
    if total_weeks > 1:
        for w in range(total_weeks):
            week_start = w * 168

            fig.add_annotation(
                x=week_start,
                y=-0.15,
                xref="x",
                yref="paper",
                text=f"Week {w+1}",
                showarrow=False,
                xanchor="left",
                font=dict(size=12)
            )

# Toggle buttons for task annotations
all_annotations = list(fig.layout.annotations)
num_task_annotations = len(tasks)
task_annotations = all_annotations[:num_task_annotations]  # First annotations are task labels
static_annotations = all_annotations[num_task_annotations:]

fig.update_layout(
    updatemenus=[
        {
            "type": "buttons",
            "direction": "left",
            "x": 0,
            "xanchor": "left",
            "y": -0.17,
            "yanchor": "top",
            "buttons": [
                {
                    "label": "Show Task Names",
                    "method": "relayout",
                    "args": [{
                        "annotations": task_annotations + static_annotations
                    }],
                },
                {
                    "label": "Hide Task Names",
                    "method": "relayout",
                    "args": [{
                        "annotations": static_annotations
                    }],
                },
            ],
        }
    ],

    title=(
        f"Schedule Visualization \u2013 {filename}"
        + (
            f"<br><sup>Total cost: {objective_value:.2f} EUR (energy: {(lambda v: f'{v:.2f}' if v is not None else 'N/A')(energy_cost)}, tardiness: {(lambda v: f'{v:.2f}' if v is not None else 'N/A')(tardiness_cost)})</sup>"
        ) if objective_value is not None else ""
    ),

    xaxis=dict(
        title=dict(
            text="Time",
            standoff=84
        ),
        tickmode="array",
        tickvals=list(range(0, time_horizon + 1, 12)),
        ticktext=[hour_to_short_str(t) for t in range(0, time_horizon + 1, 12)],
        domain=[0, 1]
    ),


    # Energy
    yaxis4=dict(
        title=dict(text="Energy Cost<br>[EUR/MWh]", font=dict(color=ENERGY_COLOR)),
        tickfont=dict(color="red"),
        domain=energy_domain,
        anchor="x",
        side="left"
    ),

    # Battery
    yaxis3=dict(
        title=dict(text="Battery Level<br>[MWh]", font=dict(color=BATTERY_COLOR)),
        tickfont=dict(color=BATTERY_COLOR),
        domain=battery_domain,
        anchor="x",
        side="left"
    ),

    # Machine states
    yaxis2=dict(
        title=dict(text="Machine<br>State", font=dict(color="#6D6D6D")),
        tickfont=dict(color="#6D6D6D"),
        domain=machine_state_domain,
        anchor="x",
        side="left",
        showticklabels=False
    ),

    # Tasks
    yaxis=dict(
        title=dict(text="Tasks", font=dict(color="#6D6D6D")),
        tickfont=dict(color="#6D6D6D"),
        domain=task_domain,
        showticklabels=False
    ),

    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),

    height=750
)

fig.show()
