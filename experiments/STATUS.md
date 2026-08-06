# What runs today, and what is blocked

The harness expands the **whole** design from `EXPERIMENTAL_PLAN.md`. Cells
that need solver features which do not exist yet are written to
`data/runlist_blocked.csv` with a reason and excluded from execution.

`02_make_runlist.py` probes `solver --help` on every invocation. **When a flag
appears, re-run that script and the corresponding cells activate.** The design
never needs editing.

---

## Runs today — no code changes needed

| Exp. | Status | What you get |
|---|---|---|
| **E0** validation | full | gaps and runtimes for MILP/H1/H1P/GA/GAP; gap stability across battery levels; LP overhead |
| **E1** decomposition | **full** | σ × β decomposition against the always-hot/no-battery baseline: `V_σ`, `V_β`, `V_joint`, `I_σβ`, substitution index, plus the Σ₁→Σ₂ rung. Policy × battery retained as secondary. |
| **E2** sizing | full | savings curve, marginal value, saturation, NPV, payback, NPV>0 share |
| **E3** tariffs | full | regression on spread/CV/negative share, screening rule, regime comparison |
| **E4** frontier | full | Pareto frontiers with and without storage, exchange rates, frontier shift |
| **E5** structure | full | standardised regression on instance descriptors |

E1, E3, E4 and E5 work today only because every shop-floor factor is realised
**in the instance file**: EI density, due-date tightness, tardiness scale,
horizon, and the price vector. No solver flag is required for any of them.

---

## Blocked — needs code

Ordered by how much of the paper each unlocks.

### C1 — state-set restriction `--states {proc | proc,idle | all}` — **DONE**
Implemented. E1's Σ₁/Σ₂/Σ₃ ladder is now measurable.

The restriction is applied per *bridge* inside `SolverH1::findOptimalPath`,
not when the SPACES graph is built. That matters: `scheduleMachineUsage`
routes the first bridge **from** Off at t=0 and the last **to** Off at t=h-1
through the same graph, so filtering Off out of the graph would make both
infeasible and abort every run. Restricting only the interior bridges gives
exactly the intended reading — "never re-entered mid-schedule" — while the
model's mandatory start-up and shut-down are untouched.

Verified on a standalone replica: interior bridge cost is monotone in the
ladder (690 → 4,060 → 8,040 as states are removed, as a nested relaxation
must be), boundary bridges stay feasible under all three levels, and applying
the ladder to a boundary bridge is confirmed to make it infeasible.

The MILP was **not** changed: E1 uses GA/GAP only, so the SPACES graph is the
only path that needs it.

Cells activated: 45,000 (Σ₁ and Σ₂, GA only — see below). ~12.5 h.

### C0 — make the Phase-3 LP unconditional
`Config::phase3LP` currently defaults to `false`, but the paper (§4.3) now
presents Phase 3 *as* the LP. Every run in this harness must use it, or the
results do not match the method described. Either flip the default or have
`SolverH1` always call `BatteryLp`.

**This one is silent** — nothing errors, you just measure the greedy
peak-shaver and write it up as an LP. Land it first.

### C5 — `--lambda` tardiness scale
Not blocking: the harness realises λ by scaling weights in the instance file
instead. Listed so nobody implements it twice.

### C2 / C3 / C4 — machine profile, battery efficiency, C-rate
**Blocks E6 entirely.** `e_proc`, `e_idle`, transition costs and durations are
hardcoded in `include/instance.h`; efficiencies are hardcoded at 0.95; C-rate
does not exist in the model.

C4 additionally matters for **credibility of everything else**: a battery that
can fully charge or discharge in one interval inflates every storage benefit
this harness will measure. It is two bound changes in the MILP and the same two
in the Phase-3 LP, both stay linear.

### C7 / C8 — schedule re-costing, baseline policies
**Blocks E7.** C7 needs a mode that applies a committed schedule to a different
price series and recomputes cost. C8 needs P1 (Phase 2 only) and P2 (naive
charge-cheap/discharge-expensive) as reference policies.

### C9 — export per-interval energy flows
Not blocking. `04_collect.py` recovers charge/discharge from the
`battery_levels` trace, which is exact up to the efficiency factor. Exporting
`grid_to_machine`, `grid_to_battery`, `battery_to_machine` directly would
remove that inference and make degradation accounting exact.

---

## Suggested order

1. ~~**C0**~~ done. ~~**C1**~~ done — E1's headline is now measurable.
2. **C4** — before E2 is final, since it will move the sizing result.
4. **C2 / C3** — unlocks E6.
5. **C7 / C8** — unlocks E7.

After each, re-run `02_make_runlist.py` and diff `budget_report.txt` to see how
much compute the new cells add before committing to them.

---

## Also outstanding, not code

- **Price data**: only 2025 ships with the repo. Volatility regimes are
  currently terciles of 2025 windows, not different years. Drop additional
  year CSVs into `instance_generator/` and list them in `EXTRA_PRICE_CSVS`.
- **Economic parameters** in `config/economics.py` are placeholders and need a
  citable source before any payback number is published.
- **Machine archetypes** for E6 (`EXPERIMENTAL_PLAN.md` §3.3) are invented and
  need calibration against published machine energy profiles.
- **`BATTERY_ON_RATIO`** may need revising downward once E2 reports the
  NPV-optimal size — see README limitation 3.
