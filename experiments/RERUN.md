# Rerun runbook

Three things changed since the first full run. This is the order to apply them
and what each costs.

| # | Change | Effect |
|---|---|---|
| 1 | GA/GAP seeding guarded (`SolverGA.cpp`, `SolverGAP.cpp`, `SolverMatH.cpp`) | recovers the 18,413 runs that aborted before the search started |
| 2 | `TL_PROFILE = [10, 60, 600]` for E0 | adds 18,000 runs; makes the GA/GAP ranking a function of budget instead of a single confounded row |
| 3 | Budget gate now keys on *remaining* work | lets an incremental extension through instead of blocking on the whole design |

**Nothing already computed is discarded.** Verified against the real option
set of `src/config.cpp`: all 266,150 existing `run_id`s survive regeneration,
0 orphaned, 18,000 added. The `tl` tag is appended to a `run_id` only when the
budget differs from the method default, which is what keeps the existing 60 s
runs addressable under their original identifiers.

---

## 0. Rebuild — do this first

```bash
cd /path/to/rcpsp_wt_battery
cmake --build build -j
./build/rcpsp_wt_battery --version      # expect >= 1.1.1
```

The seeding guard only exists in source. Running stage 3 against a stale
binary silently reproduces every one of the 18,413 failures.

## 1. Regenerate the runlist

```bash
cd experiments
export RCPSP_EXP_DATA=/path/to/data      # if not experiments/data
python3 bin/02_make_runlist.py           # probes the real binary
```

Do **not** re-run `01_build_instances.py`. The instances are unchanged and
regenerating them is a no-op, but there is no reason to touch 17,084 files.

Read `data/budget_report.txt` and check the `REMAINING TO RUN` line before
going further. Expected, from the current state:

```
  already complete       247,614 runs
  REMAINING TO RUN        36,413 runs
    18,413  previously failed (all budgets, mostly GA/GAP at 60 s)
    18,000  new E0 cells      (GA/GAP at 10 s and 600 s)
```

Cost: roughly **1,832 core-h ≈ 31 h on 60 workers**, against 5,760 available.
About 32 % utilisation, so there is ample headroom for a second pass.

## 2. Execute

```bash
python3 bin/03_run.py --rerun-failed
```

`--rerun-failed` is required: without it the driver skips anything with a
`.meta.json`, which includes every failed run. With it, runs that already
succeeded are still skipped, so this executes exactly the 36,413 above.

Resumable and safe to interrupt. To do it in two stages instead:

```bash
python3 bin/03_run.py --rerun-failed --experiments E1,E2,E3,E4,E6   # ~5 h
python3 bin/03_run.py --rerun-failed --experiments E0               # ~26 h
```

## 3. Collect and check

```bash
python3 bin/04_collect.py
```

**Read `integrity_report.txt` before the analysis.** Two numbers decide whether
the rerun worked:

- **Failure rate.** Should fall from 6.96 % to near zero. If it does not, the
  seeding guard was not the whole cause and the next suspect is the extraction
  path — the new stderr messages (`fallback to H1 also failed`) distinguish
  them.
- **C5 resolution floor.** Recorded *before* looking at any effect. The
  pre-registration fixes 0.5 % as the level above which the metaheuristic is
  too noisy to support the managerial claims.

## 4. Analyse

```bash
python3 bin/05_analyse.py
```

E0 now leads with an anytime profile: mean objective by budget, paired on
instance × battery, with the GAP−GA gap and its bootstrap CI at each of
10 / 60 / 600 s. Gaps are then reported at the reference budget (60 s) as
before.

---

## What to look at first, and what it means for the paper

**The anytime profile is the headline.** Three outcomes, three different papers:

- *GAP catches or passes GA by 600 s* — the current negative result was a
  budget artefact. Section 5's improvement claim stands, restated as
  "price-awareness needs a budget to pay off". The E1 policy main effect
  should be re-estimated at the budget where the methods are comparable.
- *The gap persists at every budget* — the negative result is real and robust,
  and is stronger for having survived a 60× budget range. Section 5 needs
  rewriting, but honestly reported this is a publishable finding.
- *Neither improves from 60 s to 600 s* — the search is not searching, and the
  problem is `popSize = 1500`, not price-awareness. That points at the irace
  re-tune rather than at the algorithm.

**Then re-check E1.** Its policy main effect is GA vs GAP by another name and
inherits whatever the profile shows. Its interaction term is a
difference-in-differences and is far less exposed — which is fortunate, since
the complementarity result is the section's actual contribution.

**Expect E5 to move.** The 6.96 % non-completion was concentrated in small,
low-EI-density, loosely-due-dated instances, so the sample was unbalanced along
three of the covariates E5 regresses on. Its R² of 0.028 may be partly that.

---

## Still outstanding after this rerun

- **E1's σ dimension** (`--states`) remains unimplemented; 90,000 cells stay
  blocked and the machine-state × storage question stays unanswered.
- **irace was tuned at `--tl 600`, `-m GA` only.** The profile measures the
  consequence but does not fix it. A per-method, per-budget re-tune is the
  real remedy; `tuning/` already has the scenario, parameters and runner.
- **MatH is implemented but absent** from both the paper and the design, and
  still calls the greedy battery rather than the Phase-3 LP.
