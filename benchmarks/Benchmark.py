import subprocess
import json
import pathlib
import datetime
import sys


timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

# Configuration
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / "build"
EXECUTABLE = BUILD_DIR / "rcpsp_wt_battery"
INSTANCE_DIR = PROJECT_ROOT / "instances"
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"
CONFIG = BENCHMARKS_DIR / "config.json"
INDIVIDUAL_RESULTS_DIR = BENCHMARKS_DIR / f"individual_results_{timestamp}"
AGGREGATED_RESULTS = BENCHMARKS_DIR / f"aggregated_results_{timestamp}.json"

if not CONFIG.exists():
    print("config.json not found in benchmarks directory.")
    sys.exit(1)

with open(CONFIG) as f:
    config = json.load(f)

METHODS = config["methods"]
INSTANCE_PATTERN = config["instancePattern"]
BATTERY_CAPACITIES = config.get("batteryCapacities", [16])
TIME_LIMIT = config.get("timeLimitSeconds", 10 * 60)

# Create directories
INDIVIDUAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# Run benchmarks
def numeric_key(path):
    parts = path.stem.split("_")
    return tuple(int(p) for p in parts)

instances = sorted(INSTANCE_DIR.glob(INSTANCE_PATTERN), key=numeric_key)
summary = []

for method in METHODS:
    for battery_capacity in BATTERY_CAPACITIES:
        for instance in instances:

            output_file = INDIVIDUAL_RESULTS_DIR / f"{instance.stem}_{method}_bc{battery_capacity}.json"

            cmd = [
                str(EXECUTABLE),
                "-m", method,
                "-b", str(battery_capacity),
                "--tl", str(TIME_LIMIT),
                "-i", str(instance),
                "-o", str(output_file)
            ]

            print(f"Running {method} with battery capacity {battery_capacity} on instance {instance.name}...")

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
                "solution_info": data["solution_info"],
                "config": data["config"]
            })


# Save aggregated results
with open(AGGREGATED_RESULTS, "w") as f:
    json.dump(summary, f, indent=4)

print(f"\nAggregated results written to {AGGREGATED_RESULTS}")