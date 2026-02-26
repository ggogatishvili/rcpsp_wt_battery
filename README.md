# RCPSP

A Resource Constrained Project Scheduling Problem (RCPSP) solver and supporting tools. This repository contains a C++ solver (CLion / WSL toolchain), a Python instance_generator, and a Python visualizer.

## Important subdirectories:
- src/ — C++ solver source
- instance_generator/ — Python input instance generator
- visualizer/ — Python results visualizer
- instances/ — input data files

## Prerequisites:
- Linux environment
- build-essential (gcc, g++)
- cmake
- conan
- python3, pip, venv
- Optional: gdb for debugging
- Solvers: GUROBI, CPLEX

## My specific setup:
- OS: Ubuntu 22.04.1 LTS (WSL)
- WSL CMake: 3.22.1
- C compiler: /usr/bin/gcc
- C++ compiler: /usr/bin/g++
- GDB: 12.0.90
- GUROBI_HOME=/opt/gurobi1203/linux64
- CPLEX_HOME=/opt/ibm/ILOG/CPLEX_Studio2211
- Python: 3.10.12
- Gurobi Optimizer: 12.0.3
- IBM ILOG CPLEX Interactive Optimizer: 22.1.1.0

Ensure the environment variables are set up for your CMake generation and build:
GUROBI_HOME=/opt/gurobi1203/linux64;CPLEX_HOME=/opt/ibm/ILOG/CPLEX_Studio2211


## Solver:

### Compilation:

In the repository root

```
mkdir build
conan install . --output-folder=build --build=missing
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make
```

### Run:

From the repository root, run the solver with specified method, input file, and output file:

```
./build/rcpsp_wt_battery -m MILP -i ../instances/1_10.txt -o ../results/1_10.txt
```

Alternatively, set up CLion target to rcpsp_wt_battery with program arguments:
```
-m MILP -i ../instances/1_10.txt -o ../results/1_10.txt
```

You can aso check --help for more options:
```
./build/rcpsp_wt_battery --help
```

## Instance Generator

A Python utility for generating extended RCPSP instances used by the solver.  
The generator takes existing benchmark instances and augments them with task release dates, due dates, and tardiness costs, while preserving the original precedence relations and resource constraints.

The tool is intended for preparing consistent input data for computational experiments and solver evaluation.

Location:
`./instance_generator/`

Input and output paths, as well as generation parameters, are specified directly in the script.


## Visualizer:

A Python utility for visualizing schedules produced by the RCPSP solver.  
The visualizer reads solver output files and generates Gantt-like charts that illustrate task execution over time together with machine states, time-dependent energy prices, and battery usage.

Interactive elements allow detailed task information to be inspected by hovering over task bars in the resulting chart.

Location:
`./visualizer/`

Run the visualizer script with the path to the solver output file:

```
python3 ./visualizer.py --input ../results/result1.json
```
