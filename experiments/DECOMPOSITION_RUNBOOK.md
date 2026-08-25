# E8 / E9 — decomposition experiments: runbook

How far the LBBD gets you, and whether putting the battery LP inside the master
is worth it. Two experiments, one command sequence, ~9 h wall clock on 60
workers at the full profile.

---

## The three questions, and which arm answers each

| | Question | Comparison |
|---|---|---|
| **Q1** | How far from the compact ILP, at equal wall clock? | `LBBD − MILP`, at 60 / 300 / 900 s |
| **Q2** | How much does the battery post-processing recover? | `objective_no_post − objective`, within one run |
| **Q3** | Is the battery better *inside* the master? | `Benders − StateLBBD` |

Four methods, all at the same budgets, all deterministic, all paired on the
instance:

| Method | Master | Battery | Role |
|---|---|---|---|
| `MILP` | monolithic compact ILP | in the model | the reference |
| `LBBD` | SPACES `z` arcs | post-processed | the method under test |
| `StateLBBD` | explicit states | post-processed | control: what does losing SPACES cost? |
| `Benders` | explicit states | Benders cuts | battery folded into the search |

### Why Q2 costs no extra runs

"LBBD without post-processing" and "LBBD with it" are **the same schedule**. The
master ignores storage either way; the two differ only in how that one schedule
is priced. The solver therefore exports both numbers from a single run —
`diagnostics.energy_cost_no_battery` alongside the reported `energy_cost` — and
the analysis differences them. Running it as two arms would have doubled the
cost to produce two identical schedules.

### Why `StateLBBD` has to exist

Benders cannot use the SPACES switching pre-processing: the cut needs the
per-interval energy demand to be affine in the master variables, and a `z` arc
collapses a whole state path into one pre-priced binary — priced against the
*raw* tariff, which under the battery's shadow tariff is the wrong path
(`docs/BENDERS_BATTERY.md` §2). So Benders necessarily also changes the master.

Comparing `Benders` against `LBBD` alone therefore moves two things at once, and
no amount of data separates them afterwards. `StateLBBD` holds the master fixed:

```
Benders − LBBD  =  (Benders − StateLBBD)  +  (StateLBBD − LBBD)
end to end         battery coordination      cost of losing SPACES
```

The analysis reports all three, and the figure plots all three. **Do not quote
the end-to-end number on its own** — the most likely outcome is a real
coordination gain roughly cancelled by a formulation loss, and that is a more
interesting result than either half.

---

## Before you run anything

### 1. Build with the CP subproblem

```bash
cd ..                       # repo root
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DWITH_CPOPTIMIZER=ON
cmake --build build -j
```

`-DWITH_CPOPTIMIZER=ON` matters. The default subproblem backend is a
time-indexed Gurobi MILP, which keeps the build free of CPLEX but is only
comfortable below ~100 tasks — it will make E9 look far worse than the method
is. Check which one you got:

```bash
./build/rcpsp_wt_battery -m LBBD -i instances/1_1.txt -v 2>&1 | grep backend
# LBBD: ... subproblem backend 'cp-optimizer'
```

### 2. Know that the battery accounting changed

`BatteryLp` now forces the battery empty at the end of the horizon, matching
`SolverMILP`'s long-standing `BatteryEnd` constraint. Previously it did not, and
with negative prices in the instance set (minimum −224.49) that let every
LP-based method book revenue on energy the horizon never consumes — and undercut
the exact MILP on the identical schedule.

**Consequence: results already on disk from E0–E6 were produced under the old
convention and are not comparable with E8/E9 numbers.** Either regenerate them,
or run E8/E9 into a separate `RCPSP_EXP_DATA` tree. `--battery-free-end`
restores the old behaviour if you need to reproduce an old figure.

### 3. Dry run the plumbing, with no solver at all

```bash
cd experiments
export RCPSP_EXP_DATA=/tmp/e8dry
python3 bin/01_build_instances.py
python3 bin/02_make_runlist.py --solver "$PWD/bin/mock_solver.py"
python3 bin/03_run.py --workers 8
python3 bin/04_collect.py
python3 bin/05_analyse.py --only E8,E9
python3 bin/06_figures.py --only e8,e8dec,e9
```

`bin/mock_solver.py` fabricates output of the right *shape*, including the
decomposition diagnostics. Its numbers are meaningless; it exists to prove that
the runlist, the collector's `diag_*` passthrough, the new C7 integrity check,
both analyses and all three figures wire together before you spend real compute.
Set `PROFILE = "pilot"` in `config/design.py` first.

---

## Running it

```bash
cd experiments
python3 bin/00_preflight.py
python3 bin/02_make_runlist.py        # re-probes --help; prints the budget
python3 bin/03_run.py                 # resumable; Ctrl-C is safe
python3 bin/04_collect.py
python3 bin/05_analyse.py --only E8,E9
python3 bin/06_figures.py --only e8,e8dec,e9
```

To run only these two experiments, set every other entry of `design.ENABLED`
to `False`. Nothing else needs editing — E8/E9 reuse the existing core instance
set and add no new instances.

### Budget, full profile

| | E8 | E9 |
|---|---|---|
| instances | 180 (2 size classes × 3 regimes × 30 shops) | 90 (3 classes × 1 regime × 30) |
| arms | 4 methods × 2 battery levels | 3 methods × 1 battery level |
| budgets | 60 / 300 / 900 s | 60 / 300 / 900 s |
| runs | 4,320 | 810 |
| ≈ core-hours | 456 | 85 |

Roughly 9 h wall on 60 workers. `02_make_runlist.py` recomputes this from the
actual runlist and refuses to write it if it exceeds the budget — trust that
number, not this table.

Knobs, in the order worth turning:
`DECOMP_SHOPS_PER_CELL` (30 → 12 roughly halves E8), `DECOMP_TL_PROFILE` (drop
900 s and the cost falls by ~70 %), `PROFILE`.

---

## Reading the output

`data/analysis/e8_decomposition.txt` has six sections. Read them in this order,
and stop at the first one that fails.

**§0 — is the reference a reference?** The share of MILP runs that *proved*
optimality. Where this is low, sections 1–2 measure distance to the compact
ILP's incumbent, not to the optimum. That is still the fair equal-time
comparison, but it is a different claim and must be worded as one.

**§6 — the falsification control.** `max |Benders − StateLBBD|` under a flat
tariff. Under a constant price there is no arbitrage, so the battery cannot
create value and these two arms *must* agree. Whatever difference appears is
E8's resolution floor. **Any effect in §1–§3 smaller than this number is not a
finding.** Check this before believing anything above it.

**§4 — which gaps can be quoted.** Only `Benders` carries a bound valid for the
battery-aware problem. `LBBD` and `StateLBBD` price energy at the
raw tariff, so their master bound is an *upper* bound on the true cost and no
gap can be computed from it. The analysis refuses to print one rather than
printing a number that looks meaningful. This is also why `04_collect.py` now
carries check **C7** (`bound > objective`): if that ever fails, cut generation
is unsound and every gap in the run is suspect.

**§5 — cut economy.** The `MIS` column is the mean size of an infeasibility set.
It should be well below the full EI assignment, since that is exactly what the
conflict refiner is for. If it is not, the conflict refiner is not earning its
complexity and Q1's answer is "the logic-based part is decoration".

`incon` counts subproblem solves that returned no verdict. Above zero, that
run's gap certifies nothing — raise `--sub-tl` (it defaults to 10 % of the run
budget) or accept a weaker claim.

**E9** answers a different question: with no MILP to compare against, the
reference is the best incumbent any arm found, and the quantity of interest is
the *frontier* — the largest size class each method still closes on half the
instances. A method whose frontier does not move as the budget grows is limited
by its formulation, not by time. That is the finding that decides whether the
decomposition was worth building.

---

## Expected outcome, written down in advance

So it can be wrong:

- **Q1.** LBBD lands close to the compact ILP on `n ≤ 64` but does not beat it
  there — the ILP is strong at that size and the decomposition pays overhead.
  The decomposition's case is E9, not E8.
- **Q2.** Post-processing recovers most of the storage value under spot tariffs
  and essentially none under `flat`. If it recovers a lot under `flat`, §6 is
  telling you the measurement is noise.
- **Q3.** `Benders − StateLBBD` is negative (coordination helps) but small;
  `StateLBBD − LBBD` is positive and larger (losing SPACES hurts more). Net:
  Benders does not beat LBBD except where storage is large and the spread is
  wide.

If that is what comes out, the honest conclusion is that post-processing is the
right default and the Benders variant is the right tool for the storage-rich
regime — a better result than "new method wins", and one the design can actually
support.

---

## Known limitations

- **The MILP reference caps E8 at `n ≤ 64`.** Above that it stops proving
  anything and the comparison degrades into incumbent-vs-incumbent, which is why
  E9 drops it entirely rather than pretending.
- **No seed dimension.** Every method here is deterministic. Residual
  nondeterminism from Gurobi's thread scheduling is a threat to validity, not a
  factor — if two identical runs disagree, that is a finding for `04_collect`,
  not something to average away.
- **`--states` is not crossed in.** E8/E9 hold the machine-state ladder at
  `sigma3`. Crossing it would triple the cost to answer a question E1 already
  owns.
- **The Benders arm has never been run against a real Gurobi.** The mock proves
  the plumbing; it proves nothing about the cut generation. Run the pilot
  profile with the real solver and check §6 and C7 before committing the full
  budget.
