import json
import pathlib
import plotly.graph_objects as go
import sys


# Load results
BENCHMARK_DIR = pathlib.Path(__file__).resolve().parent
RESULT_FILE = BENCHMARK_DIR / "aggregated_results.json"

if not RESULT_FILE.exists():
    print("result.json not found. Run benchmark.py first.")
    sys.exit(1)

with open(RESULT_FILE) as f:
    data = json.load(f)

# Organize data per instance
instance_data = {}

for entry in data:
    inst = entry["instance"]
    method = entry["method"]
    obj = entry.get("objective_value")

    if inst not in instance_data:
        instance_data[inst] = {}

    instance_data[inst][method] = obj


# Compute ratios
ratios_by_size = {}
epsilon = 1e-6

for inst, values in instance_data.items():

    obj_milp = values.get("MILP")
    obj_h1 = values.get("HEURISTIC1")

    if obj_milp is None or obj_h1 is None:
        continue

    if obj_milp == 0:
        continue

    ratio = (obj_milp - obj_h1) / (abs(obj_milp) + epsilon)

    size = inst.split("_")[0]

    if size not in ratios_by_size:
        ratios_by_size[size] = []

    ratios_by_size[size].append(ratio)


# Sort sizes
sizes_sorted = sorted(ratios_by_size.keys(), key=lambda x: int(x))


# Create Boxplot
fig = go.Figure()

for size in sizes_sorted:
    fig.add_trace(go.Box(
        y=ratios_by_size[size],
        name=f"Size {size}",
        boxmean=True,
        hovertemplate="Gap: %{y:.4f}<extra></extra>"
    ))

fig.update_layout(
    title="Relative Gap: (MILP - H1) / (|MILP| + ε)",
    yaxis_title="Relative Gap",
    xaxis_title="Instance Size",
    height=750
)

fig.show()