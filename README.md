# RCPSP Solver and Tools

This repository contains tools to generate problem instances, solve energy-aware scheduling problems using exact, heuristic, and metaheuristic methods, and visualize the final schedules.

The software uses several components written in different programming languages. The core solver is written in C++ and uses external mathematical optimization libraries. The additional tools for instance generation, benchmark execution, and result visualization are written in Python. Additionally, the R language is used for GA tuning.

## Software Requirements and Environment

A **Linux environment** is required to build and run the software.

The software requires the following tools and libraries:
* **gcc** and **g++** (version 14.3.0 or higher)
* **CMake** (version 4.1.2 or higher) for build configuration
* **Conan** for C++ dependency management
* **Python 3** (version 3.13.12), along with **pip** and **venv** for instance generation, benchmark execution, and visualization
* **R** (version 4.5.1) and the **irace** package (version 4.2.0) for parameter tuning
* **ParadisEO** version 3.1.3 for the genetic algorithm
* **Gurobi Optimizer** version 12.0.3 for solving the exact MILP model

The Gurobi solver requires an active license and the following environment variables to be set before building the software:

```bash
export GUROBI_HOME=/path/to/gurobi
export GRB_LICENSE_FILE=/path/to/gurobi.lic
```

## Compilation of the Solver

The solver is built in a separate build directory. The following commands are executed from the root directory of the repository:

```bash
mkdir build
conan install . --output-folder=build --build=missing -o "hwloc/*:shared=True" -s compiler.cppstd=23
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)
```

After a successful compilation, the solver executable is located in the `build` directory.

## Running the Solver

The solver is executed from the repository root. A solution method, an input file, and an output file must be specified. For example, the MILP method is executed as follows:

```bash
./build/rcpsp_wt_battery -m MILP -i ./instances/1_10.txt -o ./results/1_10.txt
```

The solver includes many additional configuration options. These include time limit, battery capacity, and specific parameter weights for the genetic algorithm. All available command-line options can be viewed using the help command:

```bash
./build/rcpsp_wt_battery --help
```

### Logic-based Benders decomposition (`LBBD` / `nogood`)

Two additional methods decompose the problem instead of solving it monolithically. An ILP master places the energy-intensive tasks and chooses the machine's state schedule; an RCPSP subproblem schedules everything else to minimise total weighted tardiness and feeds cuts back to the master.

```bash
./build/rcpsp_wt_battery -m LBBD   -i ./instances/1_10.txt -o ./results/1_10.txt
./build/rcpsp_wt_battery -m nogood -i ./instances/1_10.txt -o ./results/1_10.txt
```

`LBBD` refines infeasible fixings into a small conflicting subset before cutting; `nogood` excludes the whole assignment instead. The two differ only in that respect, which is what makes them a controlled comparison.

Relevant options: `--sub-tl` (per-call subproblem time limit), `--refine-tl` (conflict-refinement budget), `--no-warmstart`, `--lbbd-tardiness-bounds`.

Two things to know before reading the results:

* **The battery is applied afterwards, not during.** The master and the subproblem both reason about a battery-free world; the exact battery LP prices the resulting machine schedule once at the end. LBBD is therefore exact for the battery-free problem and a strong heuristic for the battery problem, and the MIP gap it reports certifies only the former. `docs/BENDERS_BATTERY.md` works through what a battery-aware version would take.
* **The subproblem backend matters.** By default it is a Gurobi MILP, which keeps the build free of CPLEX but is only comfortable on the smaller instances. Configure with `-DWITH_CPOPTIMIZER=ON` to use CP Optimizer instead — much stronger here, and it is what the original LBBD uses.

## Running Benchmarks

The benchmark tool automates the execution of the solver across multiple instances and solution methods. The tool and its configuration files are located in the `./benchmarks/` directory.

Before running the benchmarks, you need to set the execution parameters. A template file named `config_EXAMPLE.json` is provided in the directory. You must create a copy of this file, rename it to `config.json`, and edit it to set your specific parameters. This configuration file defines the time limit, the selected methods (such as MILP, H1, or GA), the battery capacities to test, and the file pattern for the input instances.

Once the configuration is set, the benchmark script is executed without any additional command-line arguments:

```bash
cd benchmarks
python3 Benchmark.py
```

The script runs the solver for all defined combinations. It saves the individual schedules and creates one aggregated JSON file containing a summary of all the results.

## Visualizing Benchmark Data

To analyze the aggregated benchmark results, several visualization scripts are provided in the `./benchmarks/` directory. Each script reads the aggregated JSON file and generates a specific interactive Plotly chart.

The following visualization tools are available:
* `BenchmarkVisualizer_AverageTime.py`: displays the average computation time for a specific method across different instance sizes.
* `BenchmarkVisualizer_Battery.py`: displays the average cost savings and remaining costs for different battery capacities.
* `BenchmarkVisualizer_CostAndTime.py`: displays the total schedule cost and computation time of all methods side-by-side for individual instances.
* `BenchmarkVisualizer_Gap1.py`: displays the relative gap of the H1 and GA methods compared to the exact MILP baseline.
* `BenchmarkVisualizer_Gap2.py`: displays the relative gap of the GA compared to the H1 baseline for large-scale instances.
* `BenchmarkVisualizer_Time.py`: displays the computation times of the different methods for individual instances.

To view a chart, we run the desired visualizer script using Python. If you run a script with missing or wrong arguments, it will print a usage example to the terminal to help you fix the command.

Depending on the specific script, the following command-line arguments are used:
* **input_file** (required): the path to the aggregated results JSON file.
* **method** (required for some scripts): the specific optimization method to evaluate (e.g., MILP, H1, GA).
* **instance_pattern** (optional): a text string used to filter the displayed data. For example, using `"[1-3]_*"` displays results for the first three instance sizes, which correspond to instances with 32 to 96 tasks.
* **--actual-sizes** (optional flag): changes the x-axis to display the actual total number of tasks instead of the basic categorical size label. The instance names use a simple categorical size index, so this flag just multiplies that index by 32 to calculate the real task count.
* **--percentage** (optional flag): formats the relative gap values as percentages instead of raw decimal ratios.

## Visualizing a Specific Schedule

The visualization tool is a Python script based on Plotly. It reads the JSON output file from the solver and creates an interactive plot in a web browser. The plot shows panels for energy prices, battery levels, machine states, and the task schedule.

The visualizer script, `Visualizer.py`, is located in the `./visualizer/` directory.

We run the script by providing the path to a solver output file. For example:

```bash
cd visualizer
python3 Visualizer.py ../results/1_10.txt
```

There are also some optional flags to make the charts easier to read. The `--actual-time` flag converts plain time indexes into human-readable hours, days, and weeks. The `--actual-sizes` flag converts the plain categorical instance size into an instance size defined by the total number of tasks. The resulting visualization is interactive, and hovering over the individual task bars shows detailed scheduling information.

## Generating Instances

All problem instances used for our experiments are already included in the project. The main instances for the final benchmark testing are located in the `./instances` directory, and the separate instances used for tuning are in the `./tuning/instances_tuning` directory. You do not need to generate them yourself to reproduce the results. However, if you want to create a new set of instances, this section describes how to use the generator.

The instance generator is a Python script that prepares problem instances for the experiments. It takes existing RCPSP benchmark instances and adds task release dates, due dates, and tardiness costs. It keeps the original precedence relations and resource constraints. It also replaces the old energy prices with real market data from the year 2025.

The generator script, `InstanceGenerator.py`, is located in the `./instance_generator/` directory. It reads the base instances from an `instances_original` folder and the electricity prices from the `electricity_cost_eur_mwh_2025.csv` file.

The input paths, output paths, and generation parameters are set directly inside the code. Because of this, we can run it without any command-line arguments:

```bash
cd instance_generator
python3 InstanceGenerator.py
```

The script processes all files in the input folder. It saves the newly generated instances into the `../instances_new/` directory. These files are then ready to be used as input for the solver.

## GA Tuning

The best parameters for the genetic algorithm are already provided in the solver's default configuration. You do not need to run the tuning process to use the solver. However, if you want to re-tune the algorithm or test different parameter ranges, this section describes how to use `irace`.

All files used for tuning the genetic algorithm are located in the `./tuning/` directory. This process uses the `irace` package to find the best population sizes, stagnation limits, operator weights, and shift magnitudes.

### Configuration and Parameters

The tuning setup is defined by several configuration files:
* **parameters.txt**: defines the search space for GA parameters.
* **scenario.txt**: defines how the tuning is executed.
* **instances-list.txt**: provides the list of training instances.
* **target-runner**: a script that runs the solver and returns a score based on the objective value and computation time.

By default, `irace` samples data at random, but we configured it to use our specific list. We generate `instances-list.txt` using the `instances-list-generator.py` script. This script shuffles the instances within each size group but keeps them in even blocks that match the settings in the scenario. This ensures the tuning tests the algorithm evenly across all problem sizes.

### Running the Tuning

To start the tuning, the `target-runner` script must be made executable. Then, `irace` is started through `Rscript`:

```bash
cd tuning
chmod +x target-runner
Rscript -e "irace::irace(scenario = irace::readScenario('scenario.txt'))"
```

The final results and the best parameter sets are saved in an R data file named `irace.Rdata`.