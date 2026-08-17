# Battery/schedule trace gallery — analysis

Grid: 5 instances (`n` ∈ {32, 64, 128, 256, 512} tasks, one shop replicate
each) × 3 battery capacities (0.25× / 1.0× / 4.0× `E_day`) × 3 charge speeds
(0.25C / 1.0C / uncapped) = 45 cells, each solved with a baseline method
(MILP with GA fallback) and LBBD, 180 s per solver call. All 90 traces are
under `figures/`; per-cell numbers are in `summary.csv`. Reproduce with
`python3 generate.py && python3 plot.py && python3 summary.py`.

**Budget caveat up front:** 180 s is an illustrative budget for this gallery,
not the 900 s used in `DECOMPOSITION_RUNBOOK.md`'s statistical campaign.
Absolute gaps below are shaped by that choice and should not be read as the
methods' ceiling — only the qualitative patterns (which regime helps,
which method degrades first) are likely to generalise.

## 1. Post-processing recovers real value, and it scales with capacity, not size

The battery post-processing step (pricing the LBBD schedule's storage
optimally, `energy_cost_no_battery` vs `energy_cost` in the LBBD diagnostics)
recovers **3–38% of the no-battery energy cost**, and the driver is capacity,
not instance size — mean saving is 8.9% at low capacity, 18.2% at medium,
27.7% at high, essentially flat across `n` (`figures/_summary_saving_vs_grid.png`).
That makes sense: post-processing arbitrages price swings within a fixed
horizon, and a bigger battery can arbitrage more of that swing regardless of
how many tasks are being scheduled. The `n01_cap-high_crate-high` pair is the
cleanest illustration: the battery fills once near the horizon's cheapest
hour and empties into the single price spike that follows, cutting cost from
6962 to 4317 (38%).

## 2. Charge speed only matters when it's the binding constraint

`figures/_summary_saving_vs_grid.png` shows the medium (1.0C) and high
(uncapped) charge-speed curves overlapping almost exactly at every capacity
and every instance size — confirmed in `summary.csv` (e.g.
`n01_cap-high`: saving is bit-identical, 2645.82, at both medium and high).
At 1C the battery can already move as much energy in one time-step as these
schedules ever ask of it, so removing the cap entirely buys nothing further.
Low (0.25C) is a different story: saving drops to a mean of 8.9% there
(vs. 19.1% pooled over medium/high), and the battery-level panel for a
low-crate cell (`n08_cap-low_crate-low_lbbd.png`) shows why — instead of one
clean fill-and-drain, the battery is forced into dozens of small partial
cycles because it physically cannot move enough energy per step to exploit
a price move in a single shot.

## 3. LBBD tracks the baseline at small sizes, then falls behind

Comparing LBBD's objective to the baseline's (`lbbd_gap_to_baseline` in
`summary.csv`), the gap grows monotonically with size and is not small:

| n tasks | mean gap to baseline |
|---|---|
| 32  | 0.5% |
| 64  | 6.8% |
| 128 | 14.6% |
| 256 | 21.1% |
| 512 | 37.5% |

At n=32 the baseline is a MILP proven optimal (gap 0.0 in every one of the 9
cells), so LBBD is genuinely within 1% of optimal there. From n=64 up, the
baseline itself stops being MILP (see §4) — GA fills in, and often beats
MILP's own 180 s incumbent outright (8 of 9 n=64 cells: GA landed below
MILP's unproven incumbent). So the growing "gap" from n=128 onward is really
LBBD falling behind a *good heuristic*, not just a proven bound — a more
demanding comparison than the runbook's own E9 design, which only asks LBBD
to beat other decomposition arms.
The LBBD JSON's own diagnostics explain part of this: `bound_is_battery_aware`
is 0 throughout (as `DECOMPOSITION_RUNBOOK.md` §4 already documents — LBBD's
master bound is priced at the raw tariff, not the battery-aware cost, so it
is an upper bound only and the solver's self-reported gap, e.g. 35% at
n=512, is not a trustworthy optimality gap either way). What the trend does
support is that at a 180 s budget, the fraction of that budget actually
available per subproblem call (`--sub-tl` = 18 s here) becomes the limiting
resource as the RCPSP subproblem itself grows — this is a budget effect, and
the runbook's own 900 s/60 s-subproblem design exists specifically to give
LBBD more room here.

## 4. The MILP → GA fallback rule did real work

MILP was only attempted for n=32/64, per `MILP_MAX_SIZE_CLASS` in the
existing experimental design. It proved optimal at n=32 in all 9 cells, but
at n=64 it hit the 180 s cap every time with gaps of 3.7–10.3% (all above the
1% cutoff), correctly triggering the GA fallback — and GA's result actually
was numerically better in 8 of those 9 cells. The 1%-gap fallback rule was
not a formality here; it changed which schedule the figures are built from.

## 5. Tardiness is real at this budget, and it's visible

Late tasks (red markers, top strip of panel 1) are essentially absent up to
n=64, then appear in clusters as size grows: 0 late tasks at n=32 (MILP
proves 0 tardiness cost everywhere), a few hundred at n=64, up to 306k in
tardiness cost by n=512 (`n16_cap-medium_crate-medium`). All observed late
completions fall inside the machine's active window, not in the idle tail —
i.e. lateness here is a scheduling-pressure effect (GA running out of search
time at 180 s to sequence 512 tasks well), not something the battery/energy
side of the model is causing. This is a budget artefact of the 180 s cap
chosen for this gallery, not a property of GA in general — the calibrated
GA budget used elsewhere in this repo's experiments is itself tuned at a
similar-or-larger budget precisely because 60–180 s is known to be tight at
these sizes.

## Take-aways

- Battery capacity is the first-order lever on how much the post-processing
  step is worth; charge speed only matters below ~1C.
- LBBD is competitive with a strong baseline up to n≈64 and degrades
  steadily beyond that at this (short) per-call budget — consistent with,
  but more pronounced than, the runbook's own expected-outcome note that
  "LBBD's case is E9 [large n], not E8 [small n]" was written assuming a
  longer subproblem budget than used here.
- The MILP/GA fallback threshold (gap ≤ 1%) is doing genuine work, not just
  formally switching methods for large instances.
