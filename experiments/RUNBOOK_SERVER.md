# Runbook — executing campaign v2 on the 64-core server

Target: 64 physical cores, 380 GB RAM, Linux. Expect **5 days** of wall clock
at 60 workers for 88,716 runs — of which the first 5 hours are MR, which
decides the seed counts for everything after it.

Every stage is idempotent and `03_run.py` resumes, so an interrupted campaign
costs only the runs in flight. Read the checkpoints — three of them can fail
silently and produce a complete, balanced, meaningless set of results.

---

## 0. Isolate the campaign from v1

v1's results are still on disk and are still cited by the methods paper. Do not
overwrite them. Every script honours `RCPSP_EXP_DATA`:

```bash
cd ~/rcpsp_wt_battery/experiments
export RCPSP_EXP_DATA=$PWD/data_v2
mkdir -p "$RCPSP_EXP_DATA"
```

Put that export in the shell profile of whatever session runs the campaign, or
in the systemd unit / tmux session. A single stage run without it writes into
`data/` and mixes the two campaigns.

Disk: instances 20 MB, results roughly 2 GB (one JSON per run, plus the meta
files). Check there is 10 GB free.

---

## 0b. Check the checkout is complete — 2 seconds, saves 2 hours

```bash
python3 bin/00_preflight.py --skip-solve
```

The first line must read `pipeline imports -- all 10 modules import`. If it
does not, **stop**: the analysis runs last, so a missing analysis module lets
the whole campaign complete and then dies at the final stage.

This is not hypothetical. A pilot ran 1 h 54 min, completed all 5,704 solver
runs, collected them, and died with

```
ImportError: cannot import name 'managerial' from 'analysis' (unknown location)
```

because `analysis/managerial.py` and `analysis/replication.py` had never been
`git add`ed on the machine the push came from. Untracked files do not travel
with git, and `git push` says nothing about them.

Before pushing to the compute server, on the machine you push FROM:

```bash
git status --porcelain | grep '^??'    # anything here will NOT arrive
```

`data/` and `__pycache__/` are ignored on purpose (instances regenerate from
`MASTER_SEED`); everything else in that list is either missing from the server
or should be.

## 1. Build the solver, and prove it is not stale

```bash
cd ~/rcpsp_wt_battery
export GUROBI_HOME=/path/to/gurobi GRB_LICENSE_FILE=/path/to/gurobi.lic
conan install . --output-folder=build --build=missing \
      -o "hwloc/*:shared=True" -s compiler.cppstd=23
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)"
./build/rcpsp_wt_battery --version
```

`run_all.sh` refuses to start if any source under `src/` or `include/` is
newer than the binary. That guard exists because running against a stale binary
silently reproduces whatever bug was just fixed — it is how v1's 18,413-run
seeding failure would have come back.

---

## 2. Re-tune the GA at 300 s — **before** generating anything

This is a prerequisite, not a refinement. The parameters currently compiled in
were tuned by irace at `--tl 600`; v1 then ran them at 60 s, where measured
throughput (41.6 s against a 60 s limit) showed most runs stopping on the
*stagnation* criterion rather than the clock, and where the GA improved only
0.3 % going from 60 s to 600 s. Both say the binding constraint was the
parameter set. Raising the clock to 300 s without re-tuning buys very little.

```bash
cd ~/rcpsp_wt_battery/tuning
# scenario.txt: set the target-runner budget to 300 s to match design.TL_GA
chmod +x target-runner
Rscript -e "irace::irace(scenario = irace::readScenario('scenario.txt'))"
```

Take the winning configuration and either recompile the defaults or record the
flags. **If you record flags rather than recompiling, add them to
`design.machine_args`'s caller** — a tuned parameter passed on some runs and
not others is worse than an untuned one applied uniformly.

Log what you did in `PREREGISTRATION.md` §8 either way, including "we did not
re-tune" if that is the decision. It changes how M0's anytime profile reads.

---

## 3. Three solver checkpoints — each catches a silent failure

Run these on one small instance before spending five days.

**(a) Machine-profile flags actually reach the model.** M1 is 51 % of the
campaign and rests entirely on this.

```bash
I=data_v2/instances/core/$(ls "$RCPSP_EXP_DATA/instances/core" | head -1)
B=./build/rcpsp_wt_battery
# T1 ideal: free switching, no idle draw.
# Durations are 1, not 0: the solver reserves 0 to mean "no such transition"
# and rejects the profile outright. See config/machines.py.
$B -m GA -i "$I" -b 0 --tl 30 -s 1 -o /tmp/t1.json \
   --e-proc 4 --e-idle 0 --e-off 0 \
   --off-proc-time 1 --off-proc-cost 0 --proc-off-time 1 --proc-off-cost 0 \
   --proc-idle-time 1 --proc-idle-cost 0 --idle-proc-time 1 --idle-proc-cost 0
# T5 continuous: shutdown effectively unavailable
$B -m GA -i "$I" -b 0 --tl 30 -s 1 -o /tmp/t5.json \
   --e-proc 4 --e-idle 3 --e-off 0 \
   --off-proc-time 6 --off-proc-cost 60 --proc-off-time 3 --proc-off-cost 10 \
   --proc-idle-time 1 --proc-idle-cost 2 --idle-proc-time 1 --idle-proc-cost 3
python3 -c "import json;f=lambda p:json.load(open(p))['solution_info']['objective_value'];\
a,b=f('/tmp/t1.json'),f('/tmp/t5.json');print(f'T1={a:.4f} T5={b:.4f}');\
print('OK' if a<b else 'FATAL: the machine profile is not reaching the model')"
```

T1 must be strictly cheaper than T5. If the two objectives are equal, the flags
are parsed and ignored, and M1 will produce a beautiful surface of a constant.

**(b) The state ladder restricts something.** Same instance, three levels:

```bash
for S in all "proc,idle" proc; do
  $B -m GA -i "$I" -b 0 --tl 30 -s 1 --states="$S" -o /tmp/s.json >/dev/null
  python3 -c "import json;print('$S', json.load(open('/tmp/s.json'))['solution_info']['objective_value'])"
done
```

Cost must be non-decreasing as states are removed (`all` ≤ `proc,idle` ≤
`proc`) — it is a nested relaxation, so anything else is a bug.

**(c) The Phase-3 LP is unconditional.** v1's item C0: `Config::phase3LP`
defaulted to `false` while the paper presented Phase 3 *as* the LP. This one
fails completely silently — you measure the greedy peak-shaver and write it up
as an LP. Confirm in `src/SolverH1.cpp` that `BatteryLp` is always called, or
that `--phase3-lp` is in every argv.

---

## 4. Real price data (M2's real-tariff arm)

The campaign runs without this, but M2's central diagnostic — the same
regression on synthetic and on real tariffs — is vacuous with one market-year.

```bash
python3 bin/00b_fetch_prices.py            # prints exactly what to download
python3 bin/00b_fetch_prices.py --check    # what is present now
```

**The four Czech years are already built** from OTE-CR *Annual market report*
workbooks, version 2 (the final monthly evaluation — the only version that will
never be revised, which is what makes the campaign reproducible). To rebuild
them from the workbooks:

```bash
python3 bin/00b_fetch_prices.py --from-ote-annual <dir-with-the-xls-files> --force
```

That mode reads the `DAM` sheet, locates the EUR price column **by name** (it
sits at index 7 in 2019 and 8 in 2022/2024 — 2019 has no `Saldo DM` column),
and cross-checks every row against the CZK column and the CNB rate. It needs
`xlrd` for the legacy `.xls` files and `openpyxl` for `.xlsx`; both imports are
local and say so if missing.

Downloads are manual (the node is offline and an ENTSO-E token is personal):
ENTSO-E Transparency → Transmission → Day-ahead Prices, bidding zone `BZN|CZ`
for 2019 and 2022 and `BZN|DE-LU` for 2025, full year, 60 min, "Actual Data
(CSV)" — month by month if the portal refuses a whole year. Drop them in
`$RCPSP_EXP_DATA/prices/downloads/entsoe/`, then:

```bash
python3 bin/00b_fetch_prices.py --from-entsoe "$RCPSP_EXP_DATA/prices/downloads/entsoe"
```

Read the warnings. The script shouts if the year labelled "calm" turns out more
volatile than the year labelled "crisis" — the campaign's labels feed straight
into M2's tables, and a mislabelled year is a wrong conclusion, not a typo.

**If the files arrive after the campaign has started**, re-run stages 1 and 2
and then M2 in full (`03_run.py --experiments M2`). Partial and full tariff
coverage are not merged in one regression — see `PREREGISTRATION.md` §6.

---

## 5. Pilot first — three hours, not five days

```bash
sed -i 's/^PROFILE = "full"/PROFILE = "pilot"/' config/design.py
RCPSP_EXP_DATA=$PWD/data_pilot bash run_all.sh
```

The pilot is 5,704 runs and about 183 core-h — **three hours on 60 workers** —
and it exercises every code path with the real solver. It runs the GA at 60 s
rather than 300 s and uses three machine archetypes rather than six, because
its job is to prove the pipeline works and that the factors reach the model,
not to measure anything: no effect size from the pilot is quotable.

What to check before promoting to full:

| check | where | what is wrong if it fails |
|---|---|---|
| failure rate < 2 % | `integrity_report.txt` | a solver crash path; find it before it costs four days |
| all integrity checks PASS | `integrity_report.txt` | C2/C3 failing means the battery bound is not enforced |
| flat-tariff floor small | `integrity_report.txt` C5 | if it is large, no managerial effect will be resolvable |
| every block 100 % complete | `balance_report.txt` | a design hole; fix before scaling it up 50× |
| M1's T1 beats T5 | `analysis/m1_roi_cube.txt` §1 | checkpoint 3(a) passed but the effect is not surviving to the analysis |
| GA solve time ≈ the limit | `analysis/m0_validation.txt` §6 | far below means stagnation is binding, so step 2 was skipped or did not take |

Then restore the profile:

```bash
sed -i 's/^PROFILE = "pilot"/PROFILE = "full"/' config/design.py
```

---

## 6. Generate, price, and read the budget before running

```bash
export RCPSP_EXP_DATA=$PWD/data_v2
python3 bin/00_preflight.py
python3 bin/01_build_instances.py          # ~2,820 instances, 20 MB
python3 bin/02_make_runlist.py             # refuses on holes or over budget
```

Then generate the design contract, which predicts every count from the design
constants by a different code path and fails if it disagrees with the runlist:

```bash
python3 bin/07_design_contract.py      # exit 3 on any disagreement
```

A mismatch means one of the two arithmetic paths dropped or double-counted a
factor. Neither wins by default: find which, before running anything. The most
likely culprit is the regime-versus-series distinction — a tariff selector such
as `spot_midvol` resolves to `SPOT_WINDOWS_PER_REGIME` series, not one.

Read four files before going further:

* `generation_report.txt` — market-years present, instances per tariff family,
  and the low-spread support warning. If it says fewer than 2 % of instances
  have a realised spread below 10 EUR/MWh, M2's threshold is extrapolation.
* `balance_report.txt` — every block must read `complete 100.0 %`.
* `budget_report.txt` — expect ≈ 88,716 runs and ≈ 7,298 core-h. If it is much
  larger, something in `config/design.py` moved.
* `DESIGN_CONTRACT.md` — the per-block factor decomposition, with every count
  recomputed and checked. This is the file to cite in the paper and in the
  campaign document; never copy a run count out of prose.

`02_make_runlist.py` probes `solver --help` and writes any cell whose flag is
missing to `runlist_blocked.csv` instead of running it at the compiled-in
default. **`runlist_blocked.csv` must be empty.** If it is not, the binary
predates a flag the design needs; fix that rather than proceeding.

---

## 7. Run MR first, and set the seed counts from it

**This is five hours that decides how much the other five days are worth.**

The GA is stochastic, so every cell is measured with error. Averaging k seeds
removes part of it; MR measures what is left. The reason it cannot be skipped
is that every managerial number in M1-M5 is a *paired* difference, and in a
paired difference the instance-to-instance variability cancels while the seed
noise does not:

    Var(d) = sigma_effect^2 + 2 sigma_seed^2 / k

With a small k that second term is not a correction to the standard error --
it can be the standard error. The seed counts sitting in `design.py` right now
are placeholders; running the campaign on them means reporting effects whose
precision nobody measured.

```bash
python3 bin/03_run.py --experiments MR --workers 60   # ~5 h, 3,888 runs
python3 bin/04_collect.py
python3 bin/05_analyse.py --only MR
less data_v2/analysis/mr_replication.txt
```

Then act on three sections, in this order:

**§2 first — is the GA equally noisy under every treatment?** If any factor
reads `HETEROGENEOUS`, effects across that factor must be reported against the
larger sigma, not the pooled one, and Threats to Validity has to say so. Write
it into `PREREGISTRATION.md` §8 now, while you are looking at it. This does not
stop the campaign; it changes what may be claimed from it.

**§4 — the required-seed table.** Copy the `k required` column into
`design.SEEDS_PER_EXP`. Any row reading `RAISE` means the configured count
cannot resolve a 0.5 % effect at that experiment's instance count. Any row
reading `MORE INSTANCES` means seeds are not the constraint at all — widen that
experiment's shop pool, or state in the paper what effect size it can resolve.

**§5 — the diminishing-returns curve.** Sanity check: if the noise share is
still near 100 % at k = 12, sigma_effect is tiny and the effect is uniform
across instances, which is itself worth a sentence in the paper.

Then regenerate and re-price:

```bash
python3 bin/02_make_runlist.py            # re-expands with the new seed counts
less data_v2/budget_report.txt            # does it still fit?
```

Going from 3 to 5 seeds on M1 adds about 1,400 core-h (roughly one more day);
going from 3 to 2 saves about 1,100. Both fit the one-week envelope. If the
required count does not fit, reduce the instance pool or raise the declared
minimum detectable effect and say so — do not lower k below what the formula
returns and report the effects anyway.

---

## 8. Run the campaign

```bash
tmux new -s campaign
export RCPSP_EXP_DATA=$PWD/data_v2
python3 bin/03_run.py --workers 60 2>&1 | tee -a data_v2/run.log
```

Notes that matter on a five-day job:

* **Resumable.** One result file per run, written atomically. Re-running skips
  what is complete, so a crash, a reboot or a deliberate Ctrl-C costs only the
  runs in flight.
* **Execution order is deterministically shuffled**, so stopping early yields a
  representative subset of the whole design rather than all of M0 and none of
  M5. You can analyse a partial campaign.
* **Every process is pinned to one core** via `taskset`. `--thl 1` only bounds
  Gurobi's threads; it does nothing for the TBB pool ParadisEO uses, which
  otherwise spawns `hardware_concurrency()` threads per process and
  oversubscribes the box catastrophically at 60 workers.
* **Hard timeout** at `--tl + 120 s`. A wedged process is recorded as `timeout`
  rather than holding a worker for the rest of the week.
* Failures are data. A non-zero exit is recorded with its stderr and does not
  stop the campaign.

Run a single experiment with `--experiments M1`; a smoke test with
`--limit 500`. MR is already done at this point and `03_run.py` skips it.

Watch, roughly hourly at first:

```bash
ls data_v2/results/*.meta.json | wc -l                       # progress
grep -c '"status": "ok"' data_v2/results/*.meta.json | tail  # failures
uptime; free -g                                              # load and RAM
```

The memory risk is not the MILP; it is the largest M3 cells (n = 512 with a
horizon up to 2,064 h build a SPACES graph and a battery LP over 2,064
periods). `MEM_LIMIT_GB = 6` and one core per process is what keeps two of
those from landing together. If RSS climbs past ~200 GB, drop to 48 workers —
the campaign then takes 6.3 days instead of 5.07 and still fits.

---

## 9. Collect and analyse — including while it is still running

```bash
python3 bin/04_collect.py
python3 bin/05_analyse.py --economics central,low,high
```

Both are safe on a partial campaign; the analyses report what is missing rather
than failing. Reading M0 early is worth the interruption: if the GA's gap turns
out unstable across battery levels, you want to know on day one, not day five.

**Read in this order.** The order is not cosmetic — each step decides how the
next one is allowed to be read:

1. `integrity_report.txt` — failure rate, C1–C8, and the **C5 flat-tariff
   floor**. That floor is the resolution of the whole campaign. Write it down.
2. `analysis/mr_replication.txt` — how precise the instrument is, and whether
   its precision is the same everywhere. Already read at step 7; re-read §1 at
   full scale, since sigma_seed is now estimated from far more cells.
3. `analysis/m0_validation.txt` — is the instrument *accurate*, and is its
   error invariant to the battery level.
4. `analysis/m1_roi_cube.txt` — the falsification check is §0 of that report;
   read it before §1.
5. Everything else. Every GA report ends with a seed-noise footnote giving the
   standard error that seed noise alone contributes to any paired difference in
   it; an effect near that size is not resolved by that experiment, whatever
   its p-value says.

NPV is a post-hoc function of the measured saving, so all three economic
scenarios cost no solver time. Never quote a payback figure without the band:
in v1 the NPV-positive share at the smallest capacity moved from 3 % to 55 % to
95 % across HIGH, CENTRAL and LOW on identical physical savings. The cost
assumption decides the sign of the investment answer.

---

## 10. Archive

```bash
tar czf campaign_v2_$(date +%Y%m%d).tar.gz \
    data_v2/manifest_instances.csv data_v2/prices/manifest_prices.csv \
    data_v2/runlist.csv data_v2/results.csv data_v2/analysis \
    data_v2/analysis/mr_required_seeds.csv \
    data_v2/*_report.txt config/ CAMPAIGN_IJPR.md PREREGISTRATION.md
```

That excludes the per-run JSONs (a few GB) and keeps everything needed to
reproduce every number in the paper. The instances themselves regenerate
byte-identically from `MASTER_SEED`, so they do not need archiving — but the
manifest does, because it records the sha256 of each one.

---

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `runlist_blocked.csv` non-empty | binary predates a design flag | rebuild; check `--help` for the flag named in the reason column |
| every block 100 % but `M2.real` tiny | only one market-year present | step 4; re-run stages 1–2, then M2 in full |
| GA solve time far below `--tl` | stagnation binding, not the clock | step 2 (irace at 300 s), or raise `stagLimit` |
| T1 and T5 give identical objectives | machine flags parsed and ignored | step 3(a); do not run M1 until fixed |
| all runs at one machine profile fail, others clean | that profile is outside the solver's domain (this happened: T1 had transition durations of 0, which the model reserves for "no such transition") | fix `config/machines.py`, re-run `02_make_runlist.py` — **argv is frozen in `runlist.csv`, so fixing the config alone changes nothing** — then `03_run.py --rerun-failed`. Preflight 3b now probes every profile against the real binary and catches this at stage 0 |
| flat-tariff floor > 0.5 % | metaheuristic too noisy at this budget | raise `TL_GA` or seed replication, and re-run — do not report the effects anyway |
| MR says `HETEROGENEOUS` on battery level | the GA's variance moves with the treatment | not fatal: report across that factor against the larger sigma, and log it in `PREREGISTRATION.md` §8 |
| MR says `MORE INSTANCES` | sigma_effect alone exceeds the power budget | seeds cannot fix it — widen the shop pool or raise the declared MDE |
| noise share still ~100 % at k = 12 | the effect is uniform across instances | expected for some contrasts; worth a sentence, not a fix |
| high `nan` rate in relative gaps | baseline cost near zero under negative prices | expected; read the `norm %` column, not `rel %` |
| workers idle, load < 60 | driver throttled by I/O on the results dir | move `RCPSP_EXP_DATA` to local NVMe, not NFS |
| `07_design_contract.py` exits 3 | a factor was dropped or double-counted on one of the two paths | usually regime-vs-series; fix before running |
| MR reports rho > 0.95 | the treatment may not be reaching the model | re-run checkpoint 3(a); a near-perfect seed correlation between two configurations usually means they are the same configuration |
