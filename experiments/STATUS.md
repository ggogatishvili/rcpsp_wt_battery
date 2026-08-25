# What runs today, and what is blocked — campaign v2

The harness expands the whole design in `config/design.py`. Cells needing
solver features that do not exist in the binary are written to
`data/runlist_blocked.csv` with a reason and excluded from execution.

`02_make_runlist.py` probes `solver --help` on every invocation. **When a flag
appears, re-run that script and the corresponding cells activate.** The design
never needs editing.

**`runlist_blocked.csv` must be empty before the campaign starts.** A blocked
cell is a hole in a factorial; a cell that runs at a compiled-in default
instead of the flag it asked for is worse, because it produces a complete,
balanced, meaningless result. The probe exists to make the first happen rather
than the second.

---

## Runs today

| Exp. | Needs | Status |
|---|---|---|
| **MR** replication | nothing beyond the base solver and the machine-profile flags | full |
| **M0** validation | nothing beyond the base solver | full |
| **M1** ROI cube | `--e-proc`, `--e-idle`, `--e-off`, and the eight transition flags (C2) | full |
| **M2** volatility | nothing in the solver; real market-year CSVs for its real arm | full, degraded without the CSVs |
| **M3** scaling | nothing | full |
| **M4** substitution | `--states` (C1) | full |
| **M5** frontier | nothing — lambda is realised in the instance file | full |

M2, M3 and M5 work without any solver flag because every shop-floor factor is
realised **in the instance file**: EI density, due-date tightness, tardiness
scale, horizon and the price vector.

---

## Outstanding, in order of what it blocks

### 0. MR must run before the runlist is frozen — blocks the seed counts
Not a gap, a sequencing rule. `design.SEEDS_PER_EXP` currently holds
placeholders. MR measures sigma_seed at the real budget and its report prints
the required k per experiment from a formula fixed in `PREREGISTRATION.md` §6.
Running M1-M5 on the placeholders means reporting effects whose precision
nobody measured -- and because every one of them is a paired difference, seed
noise does not cancel there the way instance variability does. Five hours.

### 1. irace re-tune at 300 s — blocks the interpretation of every result
Not a code gap; a calibration gap. The compiled-in GA parameters were tuned at
600 s, v1 ran them at 60 s, and measured throughput (41.6 s against a 60 s
limit) showed most runs stopping on **stagnation** rather than on the clock.
The GA improved 0.3 % between 60 s and 600 s. Raising the budget to 300 s
without re-tuning buys very little, and M0's anytime profile will show exactly
that. See `RUNBOOK_SERVER.md` step 2.

### 2. Real price data — blocks M2's headline
Only the reference year ships with the repository. Without additional
market-years, M2's central diagnostic — the same regression estimated on
synthetic and on real tariffs — has one real regime and is vacuous.
`bin/00b_fetch_prices.py` converts manual ENTSO-E or OTE downloads;
`--check` reports what is present. This is the campaign's answer to v1's
largest weakness, so it is worth the hour of manual downloading.

### 3. Machine archetype calibration — blocks quoting M1 magnitudes
The rho levels and transition penalties in `config/machines.py` are stylised,
not sourced. The ordering and shape of the machine effect are informative
without calibration; the magnitudes are not quotable for any named technology
until they are anchored to published machine energy-profile data.

### 4. C-rate and efficiency, if a reviewer presses
`--c-rate`, `--charging-efficiency` and `--discharging-efficiency` exist in the
solver but are **not** factors in v2's design. v1's E6 measured them: capping
at a four-hour rate cost 1.07 % [−5.6, +2.2], and round-trip efficiency moved
the saving by −8.0 % [−10.8, −4.8] across the 0.75–1.0 range. That evidence is
reusable as a defensive appendix. Adding them as a factor here would multiply
M1 by four for a result already in hand.

### 5. C7 / C8 — forecast re-costing and baseline policies
Still unimplemented, and still the cheapest remaining managerial angle: if a
naive charge-cheap/discharge-expensive rule captures most of the benefit, the
honest recommendation is a rule plus a battery rather than an optimisation
deployment. Out of scope for v2; worth flagging in Future Work rather than
leaving a reviewer to raise it.

---

## Retired from v1

* **GAP and H1P.** v1's E0 showed GAP worse than GA at every budget tested and
  not gap-stable across battery levels. Removed from the design and from the
  paper; the scheduling-policy factor goes with them.
* **The LBBD family** (`LBBD`, `StateLBBD`, `Benders`, experiments E8/E9). Still
  in the solver and still the subject of the methods paper; not part of this
  campaign, which is about managerial questions rather than about how the
  problem is solved.
* **`analysis/analyses.py`** remains, and `05_analyse.py` still dispatches to
  it for any E0–E9 rows in the results table, so v1 results stay reproducible.
