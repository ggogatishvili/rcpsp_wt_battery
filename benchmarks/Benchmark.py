import subprocess
import json
import pathlib
import datetime


timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

# Configuration
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / "build"
EXECUTABLE = BUILD_DIR / "rcpsp_wt_battery"
INSTANCE_DIR = PROJECT_ROOT / "instances"
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"
INDIVIDUAL_RESULTS_DIR = BENCHMARKS_DIR / f"individual_results_{timestamp}"
AGGREGATED_RESULTS = BENCHMARKS_DIR / f"aggregated_results_{timestamp}.json"

METHODS = ["MILP", "HEURISTIC1"]
INSTANCE_PATTERN = "1_*.txt"


# Create directories
INDIVIDUAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# Run benchmarks
instances = sorted(INSTANCE_DIR.glob(INSTANCE_PATTERN))
summary = []

for instance in instances:
    for method in METHODS:

        output_file = INDIVIDUAL_RESULTS_DIR / f"{instance.stem}_{method}.json"

        cmd = [
            str(EXECUTABLE),
            "-m", method,
            "-i", str(instance),
            "-o", str(output_file)
        ]

        print(f"Running {instance.name} with {method}...")

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            print(f"Error running {instance.name} with {method}")
            continue

        # Read result JSON
        with open(output_file) as f:
            data = json.load(f)

        summary.append({
            "instance": instance.stem,
            "method": method,
            "objective_value": data["objective_value"],
            "computation_time": data["computation_time"]
        })


# Save aggregated results
with open(AGGREGATED_RESULTS, "w") as f:
    json.dump(summary, f, indent=4)

print(f"\nAggregated results written to {AGGREGATED_RESULTS}")