# Experimental setup — RCPSP with machine states, TOU tariffs and storage

Self-contained harness for the managerial experiments E0–E5 described in
`../EXPERIMENTAL_PLAN.md`. Copy this directory (with the repository) to the
compute server, build the solver, and run six commands.

Everything is deterministic from one integer (`MASTER_SEED` in
`config/design.py`), resumable after interruption, and validated before and
after execution.

---

## Quick start

```bash
# 0. build the solver first (repo root)
cd .. && cmake --preset release && cmake --build build -j && cd experiments

# optional: put the heavy data tree on a scratch disk
export RCPSP_EXP_DATA=/scratch/rcpsp/data

python3 bin/00_preflight.py          # solver, Gurobi, cores, RAM, disk, sources
python3 bin/01_build_instances.py    # price library + all instances (~4 min)
python3 bin/00_preflight.py          # re-run: now also does a real end-to-end solve
python3 bin/02_make_runlist.py       # expand design, estimate budget, STOP if over
python3 bin/03_run.py                # execute (resumable; Ctrl-C is safe)
python3 bin/04_collect.py            # tidy table + integrity checks
python3 bin/05_analyse.py            # reports into data/analysis/
```

Or `bash run_all.sh`, which chains these and stops at the first failure.

### Before committing five days, do a dry run

```bash
# edit config/design.py: PROFILE = "pilot"
bash run_all.sh                      # ~10 min end-to-end with the real solver
```

The pilot uses the same code paths and the same checks on a 1/50 design. If the
pilot's E1 interaction and E4 frontier are flat, the full run will be too, and
the framing needs revisiting before spending the compute.

You can also exercise the harness with **no solver and no Gurobi** at all:

```bash
python3 bin/02_make_runlist.py --solver "$PWD/bin/mock_solver.py"
python3 bin/03_run.py && python3 bin/04_collect.py && python3 bin/05_analyse.py
```

`bin/mock_solver.py` fabricates plausibly-shaped output. Its numbers are
meaningless; it exists only to prove the plumbing works.

---

## Budget

Measured on the configured design (`PROFILE = "full"`, 60 workers):

| | |
|---|---|
| shop structures | 450 (5 sizes × 3 EI densities × 3 tightness × 10 replicates) |
| instance files | 18,614 (≈200 MB) |
| price series | 782 across 46 distinct horizons |
| runnable solver invocations | 255,350 |
| estimated core-hours | 4,278 |
| **estimated wall-clock** | **71 h ≈ 3.0 days** |
| budget utilisation | 74 % of 60 workers × 96 h |

`02_make_runlist.py` recomputes this from the actual runlist and **refuses to
write the runlist if the estimate exceeds the budget**. Lower `PROFILE` rather
than overriding, unless you know why.

Solution JSONs are the disk risk, not the instances: each is roughly the size
of its instance, so budget ~15–20 GB for a full run.

---

## Layout

```
config/design.py        THE design. One source of truth; nothing else holds a constant.
config/economics.py     CAPEX/WACC/life for the E2 investment appraisal (placeholders).

lib/rng.py              keyed deterministic substreams
lib/rcpsp_io.py         instance format read/write + structural descriptors
lib/prices.py           price library: contractual, spot, synthetic
lib/generate.py         shop construction (density, horizon, dates, weights)

bin/00_preflight.py     environment checks; run twice (before and after stage 1)
bin/01_build_instances.py
bin/02_make_runlist.py  design -> explicit invocations + budget gate
bin/03_run.py           parallel, resumable driver
bin/04_collect.py       tidy results + integrity checks
bin/05_analyse.py       E0-E5 reports
bin/mock_solver.py      fake solver for plumbing tests

analysis/analyses.py    the statistics (numpy only, no statsmodels dependency)

data/                   generated; safe to delete and regenerate
  instances/{core,e3,e4}/*.txt
  prices/manifest_prices.csv
  manifest_instances.csv       every instance + all covariates + sha256
  runlist.csv                  every invocation
  runlist_blocked.csv          design cells awaiting solver features
  budget_report.txt
  results/                     one JSON + one meta JSON per run
  results.csv                  tidy table
  integrity_report.txt
  analysis/*.txt
  logs/                        provenance, run summaries, failures
```

---

## What the design varies, and how

Everything below is realised **in the instance files**, so no solver change is
needed:

| Factor | Levels | Mechanism |
|---|---|---|
| project size | n ∈ {32, 64, 128, 256, 512} | PSPLIB size classes |
| EI density δ | 10 %, 25 %, 50 % | which tasks require resource R0 |
| due-date tightness τ | tight / med / loose | slack fraction of remaining horizon |
| tardiness scale λ | 0.1 … 10 | multiplies all task weights (E4) |
| tariff | flat, 2-block TOU, 3 spot volatility regimes, 30 synthetic | price vector |
| horizon | derived, whole days | from a makespan lower bound × 1.45 |

Varied through the **solver CLI**: battery capacity (`-b`), scheduling policy
(`H1`/`H1P`/`GA`/`GAP` + `--phase1-price-aware`), seed.

**Blocked** — needs code that does not exist yet, see `STATUS.md`: machine
state policy Σ₁/Σ₂/Σ₃ (E1's third dimension), machine energy profile, battery
efficiency, C-rate, schedule re-costing, baseline policies P0–P2.

The runlist generator probes `solver --help` and re-enables blocked cells
automatically once the flags appear. You will not need to edit the design.

---

## Design decisions you should know about

**Shops and tariffs are separate.** A *shop* fixes structure, dates and
weights; pairing it with a price series yields an instance. The same shop is
byte-identical across tariffs, so a tariff effect can never be confounded with
structural noise. This is what makes E3 possible at all.

**Tardiness weights use a fixed reference price**, the source year's annual
mean — not the mean of the attached series. Otherwise the tardiness/energy
balance would shift between regimes and E3 could not separate a tariff effect
from a re-weighting artefact.

**Horizons are derived, not fixed.** `h = 24 × ceil(LB × 1.45 / 24)` where LB
is a makespan lower bound (critical path, EI serialisation, resource area).
This guarantees feasibility at every EI density and gives whole numbers of
daily price cycles. Consequence: horizon correlates with size and density, so
`horizon_days` is carried as a covariate in E5 and must stay in the model.

**Execution order is deterministically shuffled.** Stop the run at any point
and what you have is a representative sample of the whole design, not all of E0
and none of E4.

**Seeds are averaged, not best-of.** `05_analyse.py --seed-aggregation mean` is
the default because best-of-k is biased upward in k and k differs between
experiments. `--seed-aggregation best` reproduces best-of-run reporting.

---

## Integrity checking

`04_collect.py` runs six checks and writes `integrity_report.txt`:

| | catches |
|---|---|
| C1 objective decomposition | objective ≠ energy + tardiness |
| C2 battery bounds | capacity constraint ignored |
| C3 zero-battery invariance | battery "helping" when absent |
| C4 tardiness sign | negative tardiness |
| C5 flat-tariff falsification | **the resolution floor of the study** |
| C6 schedule sanity | release/horizon violations, precedence breaks |

C5 is the one to read first. Under a constant price no configuration can
create arbitrage value, so whatever difference it measures is solver noise.
**Any effect in E1–E4 smaller than that floor is not a finding.**

---

## Known limitations, stated plainly

1. **Only 2025 Czech price data ships with the repo.** The multi-year regimes
   in the plan are approximated by stratifying 2025 windows into volatility
   terciles. Real 2019/2022 series can be dropped into
   `instance_generator/` and listed in `EXTRA_PRICE_CSVS`. Until then the paper
   must not claim to compare crisis and non-crisis years.
2. **Long-horizon instances draw overlapping price windows.** At n=512, δ=50 %
   the horizon reaches 88 days, so windows from an 8,760-hour year overlap
   heavily and are not independent draws. Stage 1 warns when a horizon exceeds
   half the source year.
3. **`BATTERY_ON_RATIO = 1.0`** — one full day of machine energy — is the
   "battery installed" level for E1/E3/E4. It may be far above the
   economically sensible size. Run E2 first; if the NPV-optimal ratio is much
   smaller, set `BATTERY_ON_RATIO` to it and re-run E1/E3/E4, or E1's headline
   number describes a battery nobody would buy.
4. **`E_PROC = 4.0` is duplicated** in `lib/generate.py` and
   `include/instance.h`. Every battery ratio depends on it. If item C2 lands
   and it becomes instance data, change both together.
5. **Energy units are an interpretation.** E2 assumes 1 solver energy unit =
   1 MWh and 1 interval = 1 h, making the machine a 4 MW load. Every currency
   figure inherits this. See the docstring of `analyses.e2`.
6. **The economic parameters are placeholders** and need a citable source
   before any payback figure goes in the paper.

---

## Reproducing a single instance

```python
from lib.generate import build_shop
from lib.prices import load_year_csv
year = load_year_csv("../instance_generator/electricity_cost_eur_mwh_2025.csv")
inst = build_shop(Path("../instance_generator/instances_original/4_7.txt"),
                  p=4, rep=7, dens="d25", tight="med", lam=1.0,
                  ref_price=sum(year)/len(year))
```

Byte-identical to the shipped file, independently of what else was generated,
because every draw comes from a substream keyed by the shop id rather than
from shared generator state.
