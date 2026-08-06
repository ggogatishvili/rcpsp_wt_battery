# Rerun runbook

Four things changed since the first full run. This is the order to apply them
and what each costs.

| # | Change | Effect |
|---|---|---|
| 1 | GA/GAP seeding guarded (`SolverGA.cpp`, `SolverGAP.cpp`, `SolverMatH.cpp`) | recovers the 18,413 runs that aborted before the search started |
| 2 | `TL_PROFILE = [10, 60, 600]` for E0 | adds 18,000 runs; makes the GA/GAP ranking a function of budget instead of a single confounded row |
| 3 | Budget gate now keys on *remaining* work | lets an incremental extension through instead of blocking on the whole design |
| 4 | `SYNTH_SPREADS` and `SYNTH_NOISE` extended at the low end | fills the identification hole in E3 (see below); adds 3,060 instances and 30,600 runs |

### Why (4)

E3's spread covariate was close to bimodal: a flat control at 0, then nothing
until \~18.4 EUR/MWh, then the bulk above 45. No threshold below 18.4 was
identifiable, so the screening rule originally reported (1 % saving at
3.8 EUR/MWh) was extrapolation into an empty region and has been removed from
the paper.

Adding low nominal spreads alone does not fix it, because **noise dominates
the realised spread**: at `noise = 0.05`, a nominal spread of 1 still realises
at 18.3. Only `noise = 0.01` reaches the low region (realised 3.6). Both
levels were therefore added. Measured effect on the E3 subset:

| | before | after |
|---|---|---|
| min non-zero realised spread | 18.4 | **3.3** |
| observations in (0, 18.4) | 0 | **525** |
| E3 instances | 8,280 | 11,340 |

**Nothing already computed is discarded.** Verified against the real option
set of `src/config.cpp`: all 266,150 existing `run_id`s survive regeneration
— 0 orphaned, 48,600 added. The `tl` tag is appended to a `run_id` only when
the budget differs from the method default, which is what keeps the existing
60 s runs addressable under their original identifiers.

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
```

**You must re-run `01_build_instances.py`** — change (4) adds new synthetic
tariffs. It is incremental: existing instance files are byte-identical and are
skipped, only the ~3,060 new ones are written (~27 MB).

```bash
python3 bin/01_build_instances.py     # incremental, ~1 min
python3 bin/02_make_runlist.py        # probes the real binary
```

Read `data/budget_report.txt` and check the `REMAINING TO RUN` line before
going further. Expected, from the current state:

```
  already complete       247,614 runs
  REMAINING TO RUN        67,136 runs = 2,344 core-h (39.1 h wall)
  budget utilisation         40.7 %  (of REMAINING work)
    18,536  previously failed  (all budgets, mostly GA/GAP at 60 s)
    18,000  new E0 cells       (GA/GAP at 10 s and 600 s)
    30,600  new E3 cells       (low-spread synthetic tariffs)
```

Cost: **2,344 core-h ≈ 39 h on 60 workers**, against 5,760 available — about
41 % utilisation, leaving headroom for a second pass. Verified against a
simulation of the current completion state: **0 existing run_ids orphaned,
48,600 added.**

## 2. Execute

```bash
python3 bin/03_run.py --rerun-failed
```

`--rerun-failed` is required: without it the driver skips anything with a
`.meta.json`, which includes every failed run. With it, runs that already
succeeded are still skipped, so this executes exactly the 67,136 above.

Resumable and safe to interrupt. To do it in stages instead:

```bash
python3 bin/03_run.py --rerun-failed --experiments E1,E2,E4,E6   # ~4 h
python3 bin/03_run.py --rerun-failed --experiments E3            # ~9 h
python3 bin/03_run.py --rerun-failed --experiments E0            # ~26 h
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

## 4. Analyse and plot

```bash
python3 bin/05_analyse.py
python3 bin/06_figures.py     # vector PDFs into data/figures/
```

`06_figures.py` produces the six paper figures. It degrades rather than
crashes on a partial results table, so it can be run while the cluster is
still working:

| file | shows |
|---|---|
| `fig_e0_anytime.pdf` | GAP $-$ GA cost vs time budget, with CIs |
| `fig_e2_sizing.pdf`  | savings curve and marginal value, by regime |
| `fig_e2_npv.pdf`     | NPV$>$0 share over CAPEX $\times$ capacity, with the 50 % contour |
| `fig_e3_spread.pdf`  | saving vs intra-day spread, with the support gap shaded |
| `fig_e4_frontier.pdf`| the two service--energy frontiers |
| `fig_e6_tornado.pdf` | technology factors ranked, with CIs |

E0 now leads with an anytime profile: mean objective by budget, paired on
instance × battery, with the GAP−GA gap and its bootstrap CI at each of
10 / 60 / 600 s. Gaps are then reported at the reference budget (60 s) as
before.

E3 now leads with the non-parametric regime means and demotes the regression
to a descriptive fit, guarded by three diagnostics that print automatically:
variance inflation (spread and CV sit at 5.0 and 7.4, so their coefficients
are flagged as not separately identified), covariate support (a screening
threshold is only emitted where observations actually exist, otherwise it
prints `NOT IDENTIFIABLE`), and a real-vs-synthetic split of the spread
coefficient. **With the new low-spread tariffs the screening rule may become
identifiable for the first time** — if it does, the diagnostic will emit it
with the supporting observation count, and it can go back into the paper.

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

## Changed since the last analysis pass — read this

**A normalised metric replaces the unbounded one.** Savings and gaps were
reported as a fraction of the baseline cost, which degenerates when negative
prices push that baseline towards zero. `analyses.norm_scale()` divides
instead by `e_day x horizon_days x mean_price` — the bill for running the EI
machine flat out at the mean price, which is positive, configuration-invariant
and comparable across regimes. E0 gains a `norm gap` column, E2 a `norm`
column, and E6's tornado now uses it throughout.

**E6 was affected badly and its paper table has been rewritten.** On the
current data the old denominator gives a policy effect of $+233\%$ with a CI
of $[2, 691]$, and a restart effect of $-28\%$ with a CI of $[-125, +22]$ that
does not determine its sign. Both are now reported as *not estimable* in the
paper, and an earlier draft figure of $+16.8\%$ for the restart penalty is
superseded — **do not reuse it**. The three stable rows (efficiency, C-rate,
rho) are unchanged and are what the subsection now rests on. Expect the two
suppressed rows to become estimable on this pass.

## A second pass is now queued: E1's σ ladder

`--states` landed *after* this run started, so the 45,000 ladder cells
(Σ₁ and Σ₂, GA only) are in the runlist but not in this execution. Run them
when the current pass finishes:

```bash
python3 bin/02_make_runlist.py          # picks up --states, blocked list -> empty
python3 bin/03_run.py --rerun-failed --experiments E1     # ~12.5 h
python3 bin/05_analyse.py --only E1
```

`e1()` then reports the σ × β decomposition as its PRIMARY section. Read
`I_sigma_beta` and the substitution index: negative SI means complements,
positive means substitutes, and near zero means additive — all three are
legitimate findings, but only one matches the introduction as written.

**Check the C5 resolution floor first.** The interaction was ~1.9 pp against a
17.96 % worst-case floor on the pre-fix run. If the floor has not fallen
substantially, the ladder result will not be resolvable either and the 12.5 h
is better spent on the irace re-tune.

## Still outstanding after this rerun

- **irace was tuned at `--tl 600`, `-m GA` only.** The profile measures the
  consequence but does not fix it. A per-method, per-budget re-tune is the
  real remedy; `tuning/` already has the scenario, parameters and runner.
- **MatH is implemented but absent** from both the paper and the design, and
  still calls the greedy battery rather than the Phase-3 LP.
