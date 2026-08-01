#!/usr/bin/env bash
# End-to-end driver. Stops at the first failure.
#
#   bash run_all.sh                       # real solver, profile from config/design.py
#   SOLVER=bin/mock_solver.py bash run_all.sh   # plumbing test, no Gurobi needed
#
# Safe to re-run: every stage is idempotent and 03_run.py resumes.
set -euo pipefail
cd "$(dirname "$0")"

SOLVER="${SOLVER:-../build/rcpsp_wt_battery}"
WORKERS="${WORKERS:-}"
export RCPSP_EXP_DATA="${RCPSP_EXP_DATA:-$PWD/data}"

echo "=== solver: $SOLVER"
echo "=== data:   $RCPSP_EXP_DATA"
echo

echo "=== 0/5 preflight (pre-generation)"
python3 bin/00_preflight.py --solver "$SOLVER" --skip-solve

echo; echo "=== 1/5 build instances"
python3 bin/01_build_instances.py

echo; echo "=== 0b/5 preflight (end-to-end solve)"
python3 bin/00_preflight.py --solver "$SOLVER"

echo; echo "=== 2/5 runlist + budget gate"
python3 bin/02_make_runlist.py --solver "$SOLVER"

echo; echo "=== 3/5 execute"
if [ -n "$WORKERS" ]; then
  python3 bin/03_run.py --workers "$WORKERS"
else
  python3 bin/03_run.py
fi

echo; echo "=== 4/5 collect + integrity"
python3 bin/04_collect.py

echo; echo "=== 5/5 analyse"
python3 bin/05_analyse.py

echo
echo "Done. Read, in this order:"
echo "  \$RCPSP_EXP_DATA/integrity_report.txt   <- C5 resolution floor FIRST"
echo "  \$RCPSP_EXP_DATA/analysis/summary.txt"
