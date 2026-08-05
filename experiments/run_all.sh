#!/usr/bin/env bash
# End-to-end driver. Stops at the first failure.
#
#   bash run_all.sh                              # real solver, profile from config/design.py
#   SOLVER=bin/mock_solver.py bash run_all.sh    # plumbing test, no Gurobi needed
#   RERUN_FAILED=0 bash run_all.sh               # leave previously-failed runs alone
#   WORKERS=32 bash run_all.sh                   # override worker count
#
# Safe to re-run: every stage is idempotent and 03_run.py resumes. Completed
# runs are skipped; by default previously FAILED runs are retried, which is
# what you want after fixing a solver bug. Set RERUN_FAILED=0 to suppress that.
set -euo pipefail
cd "$(dirname "$0")"

SOLVER="${SOLVER:-../build/rcpsp_wt_battery}"
WORKERS="${WORKERS:-}"
RERUN_FAILED="${RERUN_FAILED:-1}"
export RCPSP_EXP_DATA="${RCPSP_EXP_DATA:-$PWD/data}"

echo "=== solver:       $SOLVER"
echo "=== data:         $RCPSP_EXP_DATA"
echo "=== rerun failed: $RERUN_FAILED"
echo

# --- staleness guard -------------------------------------------------------
# Running against a binary older than the sources silently reproduces whatever
# bug you just fixed. This is exactly how the 18,413-run seeding failure would
# come back, so it is a hard stop rather than a warning.
if [ "${SOLVER%.py}" != "$SOLVER" ]; then
  echo "=== solver is a python script; staleness check skipped"
elif [ -x "$SOLVER" ]; then
  # find's own -newer: no shell loop, so `set -o pipefail` cannot trip on the
  # last comparison returning non-zero (which silently skipped this check).
  # Parentheses matter: without them -o binds only the last -name.
  NEWER=$(find ../src ../include \( -name '*.cpp' -o -name '*.h' \) \
            -newer "$SOLVER" -print 2>/dev/null | head -5 || true)
  if [ -n "$NEWER" ]; then
    echo "FATAL: solver binary is older than these sources:" >&2
    echo "$NEWER" | sed 's/^/    /' >&2
    echo "  Rebuild first:  cmake --build build -j" >&2
    exit 1
  fi
  echo "=== binary is newer than all sources (OK)"
else
  echo "FATAL: $SOLVER not found or not executable. Build first." >&2
  exit 1
fi

echo; echo "=== 0/5 preflight (pre-generation)"
python3 bin/00_preflight.py --solver "$SOLVER" --skip-solve

echo; echo "=== 1/5 build instances (incremental)"
python3 bin/01_build_instances.py

echo; echo "=== 0b/5 preflight (end-to-end solve)"
python3 bin/00_preflight.py --solver "$SOLVER"

echo; echo "=== 2/5 runlist + budget gate"
python3 bin/02_make_runlist.py --solver "$SOLVER"

echo; echo "=== 3/5 execute"
RUN_ARGS=()
[ "$RERUN_FAILED" = "1" ] && RUN_ARGS+=(--rerun-failed)
[ -n "$WORKERS" ] && RUN_ARGS+=(--workers "$WORKERS")
python3 bin/03_run.py "${RUN_ARGS[@]}"

echo; echo "=== 4/5 collect + integrity"
python3 bin/04_collect.py

echo; echo "=== 5/5 analyse"
python3 bin/05_analyse.py

echo
echo "Done. Read, in this order:"
echo "  \$RCPSP_EXP_DATA/integrity_report.txt        <- failure rate, then C5 floor"
echo "  \$RCPSP_EXP_DATA/analysis/e0_validation.txt  <- the anytime profile"
echo "  \$RCPSP_EXP_DATA/analysis/e3_tariff.txt      <- is the screening rule identifiable now?"
echo "  \$RCPSP_EXP_DATA/analysis/summary.txt"
