"""
Experimental design — single source of truth for the IJPR managerial campaign.

CAMPAIGN v2, 2026-08. Supersedes the v1 design (git history) in three ways:

  1. GAP AND H1P ARE GONE. v1 spent 233,750 of its 266,150 runs on GAP, and
     E0 showed GAP worse than GA at every time budget tested and not gap-stable
     across battery levels. Removing the price-aware policy arm therefore
     removes 88 % of the compute and the confound that came with it (the GA
     parameters were tuned by irace for GA, at 600 s, and then applied to GAP
     at 60 s). The scheduling-policy factor disappears entirely; GA is the
     measurement device and its accuracy is established in M0.

  2. THE COMPUTE FREED GOES INTO THE TIME LIMIT, NOT INTO MORE CELLS. v1 ran
     the metaheuristic at 60 s, a budget derived from the compute envelope and
     nothing else, while its parameters were tuned at 600 s. v2 runs at
     TL_GA = 300 s. That is still below the tuning budget, so re-tuning at 300 s
     is a prerequisite of the campaign, not an optional refinement — see
     RUNBOOK_SERVER.md step 2.

  3. THE MANAGERIAL QUESTIONS ARE CROSSED, NOT SEQUENTIAL. v1 measured
     capacity (E2), tariff (E3) and machine technology (E6) in three separate
     experiments at one another's baselines, so it could report three main
     effects and no interaction. The paper's claim is about a return on
     investment that depends on all three at once, which is a statement about
     the interaction. M1 is therefore a fully crossed
     capacity x tariff x machine cube, and the three "what happens when I
     change X" questions are projections of it rather than separate studies.

Everything under experiments/ reads this file. Changing a value here changes
the instances, the runlist and the analyses consistently.

BUDGET MODEL. 02_make_runlist.py estimates total core-seconds before anything
executes and refuses to proceed if the estimate exceeds
WALL_CLOCK_BUDGET_H x N_WORKERS. Change the profile, not the scripts.
"""

from __future__ import annotations

import math

from typing import TypedDict

from . import machines

# ---------------------------------------------------------------------------
# 0. Machine, budget, reproducibility
# ---------------------------------------------------------------------------

# Target box: 64 physical cores, 380 GB RAM. Four cores are left to the OS,
# the I/O of ~30k result files, and Gurobi's own overhead in the MILP cells.
N_WORKERS = 60
WALL_CLOCK_BUDGET_H = 168          # one week; the design targets ~4.5 days

# Per-run memory ceiling passed as --ml. 380 GB / 60 workers = 6.3 GB each;
# 5 GB is a safe cap that still lets the compact MILP breathe on n = 128.
# 380 GB / 60 workers = 6.3 GB each. The binding case is not the MILP but the
# largest M3 cells: n = 512 with a horizon up to 2064 h builds a SPACES graph
# and a battery LP over 2064 periods. 6 GB leaves no headroom for two of those
# landing on the same worker at once, which is why 03_run.py pins one core per
# process rather than letting a run go wide.
MEM_LIMIT_GB = 6
THREADS_PER_RUN = 1                # parallelism comes from the driver, not the solver

# Master seed. Every random draw in the package derives deterministically from
# this integer plus a stable string key, so the whole campaign is reproducible
# from one number. NEVER change it after generation.
#
# It is deliberately DIFFERENT from v1's 20260801: v2 draws different price
# windows and different shop subsets, and reusing the old seed would make two
# campaigns that are neither identical nor independent, which is the worst of
# both worlds when a reviewer asks whether a result replicates.
MASTER_SEED = 20260824

# ---------------------------------------------------------------------------
# 1. Profile
# ---------------------------------------------------------------------------
# "smoke"    ~2 core-h    — the pipeline runs end to end; numbers are meaningless
# "pilot"    ~120 core-h  — signs and shapes, ~4 h wall; run this FIRST
# "full"     ~6500 core-h — the campaign, ~4.5 days wall on 60 workers
PROFILE = "full"


class _Profile(TypedDict):
    valid_reps: int          # replicate structures per cell in the validation pool
    core_reps: int           # ... in the core pool (M1, M4, M5)
    scale_reps: int          # ... in the scaling pool (M3)
    core_size_classes: list[int]
    scale_size_classes: list[int]
    valid_size_classes: list[int]
    seeds: int
    m2_shops: int            # stratified subset of the core pool used by M2
    spot_windows: int        # spot windows materialised per regime
    real_windows: int        # windows drawn per real market-year series
    tl_ga: int               # metaheuristic budget; only "full" is scientific
    mr_shops: int            # shops in the seed-replication study
    mr_seeds: int            # seeds per cell there
    m1_archetypes: list[str] | None   # None = every archetype
    synth_spreads: list[float] | None # None = the full synthetic family
    synth_noise: list[float] | None


_PROFILES: dict[str, _Profile] = {
    "smoke": {"valid_reps": 1, "core_reps": 1, "scale_reps": 1,
              "core_size_classes": [1], "scale_size_classes": [1, 2],
              "valid_size_classes": [1], "seeds": 1, "m2_shops": 2,
              "spot_windows": 1, "real_windows": 1, "tl_ga": 10,
              "mr_shops": 2, "mr_seeds": 4,
              "m1_archetypes": ["T1_ideal", "T3_baseline", "T5_continuous"],
              "synth_spreads": [1.0, 40.0], "synth_noise": [0.01, 0.25]},
    "pilot": {"valid_reps": 2, "core_reps": 1, "scale_reps": 2,
              "core_size_classes": [2], "scale_size_classes": [1, 2, 4],
              "valid_size_classes": [1, 2], "seeds": 2, "m2_shops": 6,
              "spot_windows": 1, "real_windows": 2, "tl_ga": 60,
              "mr_shops": 6, "mr_seeds": 8,
              # Three archetypes, not six: the pilot's job is to prove the
              # machine factor reaches the model and that T1 dominates T5, which
              # the two corners plus the anchor establish. Running all six would
              # double the pilot to buy resolution nobody reads at this scale.
              "m1_archetypes": ["T1_ideal", "T3_baseline", "T5_continuous"],
              "synth_spreads": [1.0, 15.0, 80.0], "synth_noise": [0.01, 0.25]},
    "full":  {"valid_reps": 5, "core_reps": 2, "scale_reps": 5,
              "core_size_classes": [2, 4], "scale_size_classes": [1, 2, 4, 8, 16],
              "valid_size_classes": [1, 2, 4], "seeds": 5, "m2_shops": 24,
              "spot_windows": 2, "real_windows": 5, "tl_ga": 300,
              "mr_shops": 12, "mr_seeds": 12,
              "m1_archetypes": None, "synth_spreads": None, "synth_noise": None},
}
P = _PROFILES[PROFILE]

# ---------------------------------------------------------------------------
# 2. Shop-floor factors
# ---------------------------------------------------------------------------
# A *shop* is (PSPLIB structure, EI density, due-date tightness, lambda). It
# fixes everything except the price series. Pairing a shop with a series gives
# one instance file. This separation is what lets M2 vary the tariff over 56
# series without regenerating a single structure, and it guarantees that the
# same shop is bit-identical across tariffs.

TASKS_PER_CLASS = 32               # PSPLIB class p contains 32*p tasks
SIZE_CLASSES = sorted(set(P["core_size_classes"]) | set(P["scale_size_classes"])
                      | set(P["valid_size_classes"]))
REPLICATES = max(P["valid_reps"], P["core_reps"], P["scale_reps"])

# Energy-intensive task density: share of tasks requiring the EI machine.
EI_DENSITY = {"d10": 0.10, "d25": 0.25, "d50": 0.50}

# Due-date tightness. due_j = r_j + p_j + rho * (h - r_j - p_j), rho ~ U(lo,hi).
DUE_TIGHTNESS = {
    "tight": (0.00, 0.05),
    "med":   (0.10, 0.20),
    "loose": (0.35, 0.50),
}

# Horizon: rounded up to whole days from a makespan lower bound, so every
# instance is feasible and spans an integer number of daily price cycles.
HORIZON_SLACK = 1.45
HOURS_PER_DAY = 24
MIN_HORIZON_DAYS = 3               # storage needs a few troughs to arbitrage

# DO NOT pad the horizon to work around a solver non-completion rate. Changing
# this rule changes h for 100 % of instances, and because due dates and the
# drawn price window both depend on h, that discards the entire benchmark. See
# the long note in lib/generate.py build_shop().

# Tardiness cost scale. M5's treatment; 1.0 everywhere else.
LAMBDA_BASE = 1.0
LAMBDA_LEVELS = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

# ---------------------------------------------------------------------------
# 3. Tariff library
# ---------------------------------------------------------------------------
# v1's honest limitation: only Czech 2025 day-ahead data shipped with the repo,
# so "volatility regime" meant terciles of one year rather than calm vs crisis.
# The consequence was measured and it was fatal for RQ3 — the spread effect was
# +0.554 (se 0.026) on synthetic tariffs and -0.061 (se 0.108) on real ones,
# i.e. wrong-signed and indistinguishable from zero. The screening rule
# described the generator's sinusoid, not a market.
#
# v2 fixes this from both ends. REAL_MARKET_YEARS brings in genuinely different
# price formations (a calm year, the 2022 crisis, a high-renewable recent year,
# and a second bidding zone), which is what gives the spread coefficient real
# support at the low end. The synthetic family stays, but its role is stated
# differently: it is an orthogonal DESIGN, used to identify the shape of the
# response when real tariffs confound spread with mean and with negative-hour
# share, and never used on its own to assert an external threshold.

REFERENCE_YEAR_CSV = "electricity_cost_eur_mwh_2025.csv"   # ships with the repo

# Additional market-years. Each entry needs a CSV with the same schema as the
# reference (columns day,hour,cost; day as D/M/YYYY; EUR/MWh) dropped into
# instance_generator/.
#
# WHAT THE FOUR CZ YEARS BUY, measured rather than assumed (see
# data/prices/real_years_report.txt):
#   cz2019  mean  40 EUR/MWh, spread  28, 0.7 % negative hours -- a calm market
#   cz2022  mean 247,          spread 183, 0.1 % negative      -- the crisis
#   cz2024  mean  85,          spread 113, 3.6 % negative      -- high renewables
#   cz2025  mean  97,          spread 129, 3.7 % negative      -- the reference
#
# 2024 and 2025 are near-twins: same regime, and that is useful (an effect that
# reproduces across two adjacent years is a stronger claim than one measured on
# one), but it is replication rather than new variation. The variation still
# missing is a SECOND BIDDING ZONE -- a different price formation rather than a
# different year of the same one -- which is what de2025 is for. bin/00b_fetch_prices.py builds them from ENTSO-E or OTE
# downloads. Entries whose file is absent are skipped with a warning, so the
# campaign degrades to "2025 only" rather than failing — but M2's real-tariff
# arm is then not interpretable and the analysis says so.
REAL_MARKET_YEARS: dict[str, dict] = {
    "cz2019": {"file": "electricity_cost_eur_mwh_cz_2019.csv",
               "market": "CZ", "year": 2019, "label": "calm"},
    "cz2022": {"file": "electricity_cost_eur_mwh_cz_2022.csv",
               "market": "CZ", "year": 2022, "label": "crisis"},
    "cz2024": {"file": "electricity_cost_eur_mwh_cz_2024.csv",
               "market": "CZ", "year": 2024, "label": "high-renewable"},
    "cz2025": {"file": REFERENCE_YEAR_CSV,
               "market": "CZ", "year": 2025, "label": "recent"},
    "de2025": {"file": "electricity_cost_eur_mwh_de_2025.csv",
               "market": "DE", "year": 2025, "label": "high-renewable"},
}

# Windows drawn per real market-year, and per volatility regime of the
# reference year.
REAL_WINDOWS_PER_YEAR = P["real_windows"]
SPOT_WINDOWS_PER_REGIME = P["spot_windows"]

# Volatility regimes: terciles of the mean intra-day spread over every
# candidate window of the REFERENCE year. Retained from v1 because the M1 cube
# needs a tariff factor with a clean internal ordering; the external claim
# about spread now rests on REAL_MARKET_YEARS instead.
SPOT_REGIMES = ["spot_lowvol", "spot_midvol", "spot_highvol"]

# Contractual tariffs. "flat" is the falsification control: under a constant
# price no configuration can create arbitrage value, so any measured saving
# bounds the resolution of every other result in the campaign.
CONTRACTUAL = {
    "flat": {"kind": "flat"},
    "tou2": {"kind": "two_block", "peak_hours": (8, 20), "peak_ratio": 1.6},
}

# Synthetic controlled family (M2). Orthogonal variation of the three price
# characteristics real data confounds.
#
# Generator spread and REALISED spread differ: noise inflates the realised
# value, so a nominal 10 came out at 18.4 in v1. The 1/3/5 levels and the 0.01
# noise level exist so the low-spread region is populated at all — without
# them, M2 cannot identify where the value of storage vanishes and any
# screening threshold is extrapolation into an empty gap. Always check the
# realised distribution in data/manifest_instances.csv after regenerating.
SYNTH_SPREADS = P["synth_spreads"] or [1.0, 5.0, 15.0, 40.0, 80.0, 150.0]
SYNTH_NOISE = P["synth_noise"] or [0.01, 0.10, 0.25]   # sd as a share of the mean
SYNTH_NEG_SHARE = [0.0, 0.08]              # share of hours below zero
SYNTH_MEAN = 90.0                          # EUR/MWh
SYNTH_DRAWS = 1

# ---------------------------------------------------------------------------
# 4. Solver-side factors
# ---------------------------------------------------------------------------

# Battery capacity as a multiple of E_day, the mean daily energy demand of the
# EI machine on that instance. Converted to the solver's integer -b at runlist
# time. Expressing it as a ratio is what makes "B = 1.0" mean the same thing on
# a 32-task and a 512-task instance — which M3 depends on entirely.
BATTERY_RATIOS = [0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0]

# The "storage installed" level used by experiments that do not sweep capacity.
#
# v1 used 1.0 and then discovered, in E2, that 1.0 E_day is NPV-negative on
# every instance in every regime — so E1/E3/E4 described an asset no plant
# would buy. v2 does not repeat that: the reduced levels below bracket the
# NPV-optimal region found in v1 (~0.1) and the saturating region (~1.0), and
# every non-sweep experiment carries BOTH. The cost is a factor of two on those
# experiments; the benefit is that no result has to be re-run when the sizing
# answer lands, and that every claim can be stated at a capacity a plant would
# actually install.
BATTERY_ON_RATIOS = [0.1, 1.0]
BATTERY_ON_RATIO = 0.1             # the single level used where only one fits

# Reduced ladder for experiments whose treatment is not capacity but which
# still need storage to vary (M2, M3): off / bought / saturating.
BATTERY_LADDER = [0.0, 0.1, 1.0]

# Machine-state ladder (M4). Realised by the solver's --states flag, which
# restricts only the INTERIOR bridges between mandatory Proc blocks; the
# model's mandatory Off at t=0 and t=h-1 is untouched, so sigma1 means "never
# shuts down mid-schedule" rather than an infeasible model.
#
#   sigma3  full model: the machine may shut down between jobs
#   sigma2  idles between jobs, never shut down mid-schedule
#   sigma1  stays hot for the whole production window
STATE_POLICIES = {
    "sigma3": {"flag": None},
    "sigma2": {"flag": "--states=proc,idle"},
    "sigma1": {"flag": "--states=proc"},
}
STATE_BASELINE = "sigma3"

# --states is honoured by the SPACES graph (H1/GA) only; the compact MILP
# builds its own state model and config.cpp refuses the combination rather than
# silently ignoring it. M4 is therefore GA-only by construction, not by choice.
STATE_METHODS = ["GA"]

SEEDS = list(range(1, P["seeds"] + 1))     # GA is stochastic; H1 and MILP are not
DETERMINISTIC_METHODS = ("H1", "MILP")

# Seed replication per experiment. Not uniform, and the asymmetry is the point.
#
# Seed replication buys precision on the WITHIN-cell variance; instances buy
# precision on the between-instance variance, which is what every reported
# effect is averaged over. M1's cube already averages each cell over 288
# instances, so a fourth and fifth seed there costs 1,400 core-hours to shrink
# a variance component that is already second-order. M0 keeps five because
# seed dispersion IS one of its results -- it is the quantity that says whether
# a 0.4 % gap is a property of the algorithm or of one lucky run. M4 and M5
# keep five because their contrasts are differences of differences, where noise
# compounds, and they are cheap enough that it does not matter.
#
# THESE ARE PROVISIONAL until MR reports. The runbook's step 6 is: run MR,
# read analysis/mr_replication.txt, set the numbers below to what its
# "required seeds" table says, regenerate the runlist, then run the rest.
# Leaving them at the values below without reading MR is the same mistake v1
# made with its time limit -- a design constant chosen because it fit the
# budget, and then reported as though it had been chosen for a reason.
SEEDS_PER_EXP = {"MR": 99, "M0": 5, "M1": 3, "M2": 3, "M3": 3, "M4": 5, "M5": 5}


def seeds(exp: str) -> list[int]:
    """Seeds for one experiment, never more than the profile provides.

    MR is the exception and draws from its own, larger pool: estimating a
    standard deviation from 3 or 5 observations is estimating it to within a
    factor of two, which is not good enough to size the rest of the campaign
    on.
    """
    if exp == "MR":
        return list(range(1, MR_SEEDS + 1))
    return SEEDS[:min(SEEDS_PER_EXP.get(exp, len(SEEDS)), len(SEEDS))]

# ---------------------------------------------------------------------------
# 5. Time limits (seconds)
# ---------------------------------------------------------------------------
#
# PROVENANCE — read before trusting any method comparison made at these values.
#
# v1 ran the metaheuristic at 60 s, chosen because the design fit in the
# compute envelope at 60 s and did not at 600 s. Its GA parameters (popSize,
# stagLimit) came from irace at --tl 600 -m GA. Observed throughput was 41.6 s
# against a 60 s limit, which means a large share of runs stopped on the
# STAGNATION criterion rather than the clock, and GA improved only 0.3 % from
# 60 s to 600 s. Both facts point the same way: the binding constraint was the
# parameter set, not the budget.
#
# v2 raises the budget to 300 s because dropping GAP made it affordable, and
# REQUIRES an irace re-tune at 300 s before the campaign starts. Raising the
# clock without re-tuning would buy very little, for exactly the reason above.
# Set by the profile: 10 s for smoke, 60 s for the pilot, 300 s for the
# campaign. Only the campaign value is scientific -- a pilot at 60 s tells you
# the pipeline works and the factors reach the model, and tells you nothing
# about the size of any effect.
TL_GA = P["tl_ga"]
TL = {
    "GA":   TL_GA,
    "H1":   120,       # constructive; the limit is a guard, not a budget
    "MILP": 1800,      # the reference. See the note in M0 below.
}

# Anytime profile for M0 only. 30 s shows whether the ranking inverts under a
# tight budget; 900 s is three times the campaign budget and bounds what is
# left on the table. The campaign budget itself (300 s) is not repeated here —
# those runs already exist in the main M0 cell.
TL_PROFILE_EXTRA = [30, 900]

# The compact MILP is the reference, so its budget is generous relative to the
# heuristic's: 1800 s is six times TL_GA. That asymmetry is deliberate and must
# be stated in the paper — the claim is "GA is close to what an exact method
# proves given six times the time", which is stronger than a matched-budget
# comparison and is the honest reading of what the runs show.
# --- which instances the compact MILP is offered, fixed in advance ----------
#
# "MILP_MAX_SIZE_CLASS = 4" was not a subsample: the validation pool holds
# classes {1, 2, 4}, so a cap at 4 admitted everything and the 540 validation
# instances all got a MILP run. That is 1,080 runs at 1,800 s, and most of them
# would have been the MILP failing to close on n = 128 -- an expensive way to
# learn something one probe can establish.
#
# The two populations are DIFFERENT EVIDENCE and the design keeps them apart,
# because M0 reports them separately and must never average them:
#
#   PROVE  classes 1-2 (n = 32, 64), every instance. This is where the MILP can
#          close, so this is the population on which "the GA is X % from the
#          OPTIMUM" is a meaningful sentence.
#
#   PROBE  class 4 (n = 128), a stratified subsample. Here the MILP is expected
#          to return an incumbent with a large gap, and the question changes
#          from "how far from optimal" to "where does the exact method stop
#          being a reference at all". That is a qualitative finding and does
#          not need 45 shops to establish; it needs enough to see the gap
#          distribution, and 15 is enough for that while costing a third as
#          much.
#
# The subsample is a deterministic stratified prefix over
# (EI density x due tightness), taken in sorted shop order -- so it is
# reproducible, it is balanced on the two factors M0 breaks results down by,
# and adding shops to the pool later never reshuffles the ones already chosen.
MILP_PROVE_CLASSES = [1, 2]
MILP_PROBE_CLASSES = [4]
MILP_PROBE_SHOPS = 15              # of the 45 class-4 shops in the pool

# Kept for the capability gate and for anything that still asks "is this class
# in scope for the MILP at all".
MILP_MAX_SIZE_CLASS = max(MILP_PROVE_CLASSES + MILP_PROBE_CLASSES)

# ---------------------------------------------------------------------------
# 6. Shop pools
# ---------------------------------------------------------------------------
# Each pool is a full factorial over (size class, replicate, EI density, due
# tightness) at lambda = LAMBDA_BASE, except LAMBDA which crosses lambda in.
#
# Pools overlap by construction: POOL_CORE's shops are a subset of POOL_SCALE's
# size classes, and M2's subset is drawn from POOL_CORE. Overlap is good here —
# it means M1, M2 and M4 are paired on the same shops, so a difference between
# two experiments is a difference between their treatments and nothing else.

POOLS: dict[str, dict] = {
    # M0: validation against the compact MILP. Wider in size (to show where the
    # MILP stops closing) and in replicates (the gap distribution is the
    # result, so it needs sample size), narrow in everything else.
    "valid": {"size_classes": P["valid_size_classes"], "reps": P["valid_reps"],
              "lambdas": [LAMBDA_BASE]},

    # M1, M4: the managerial core. Two size classes so that no headline result
    # rests on a single n, few replicates because the cube is wide.
    "core":  {"size_classes": P["core_size_classes"], "reps": P["core_reps"],
              "lambdas": [LAMBDA_BASE]},

    # M3: scaling. Every size class, more replicates, everything else at
    # baseline so that n is the only thing moving.
    "scale": {"size_classes": P["scale_size_classes"], "reps": P["scale_reps"],
              "lambdas": [LAMBDA_BASE]},

    # M5: the service-energy frontier. Core shops crossed with the lambda
    # ladder; lambda is baked into the instance file (task weights), so these
    # are genuinely different shops rather than a solver flag.
    "lambda": {"size_classes": P["core_size_classes"], "reps": P["core_reps"],
               "lambdas": LAMBDA_LEVELS},
}

# M2 runs on a stratified subset of the core pool: the tariff library is 56
# series wide, so shops are the dimension to economise on. Stratified over
# (size class, density, tightness) so the subset is not accidentally all-tight
# or all-small.
M2_SHOPS = P["m2_shops"]

# ---------------------------------------------------------------------------
# 7. Tariff sets per experiment
# ---------------------------------------------------------------------------
# Names here are RESOLVED against the built library by 01_build_instances.py:
# a regime name expands to that regime's windows, a series name matches exactly.

# The M1 cube's tariff factor: five shapes spanning flat to highly volatile,
# one window per spot regime. One window rather than several is a real
# limitation — the cube would otherwise be five times its size — and it is why
# M2 exists: within-regime tariff variation is measured there, on 56 series,
# rather than inside the cube.
M1_TARIFFS = ["flat", "tou2", "spot_lowvol", "spot_midvol", "spot_highvol"]

# M2 uses everything: the synthetic orthogonal family for identification, the
# real market-years for external validity, and the contractual pair as anchors.
M2_TARIFF_FAMILIES = ["contractual", "real", "synthetic"]

# M3 and M4 hold the tariff at three contrasting shapes: the falsification
# control, a contractual tariff, and a volatile spot week.
M3_TARIFFS = ["flat", "tou2", "spot_midvol"]
M4_TARIFFS = ["flat", "tou2", "spot_midvol"]

# M5's treatment is lambda, so the tariff is held at one volatile week — the
# regime where the energy-service trade-off actually binds.
M5_TARIFFS = ["spot_midvol"]

# M0 compares methods, not tariffs, but a method that is accurate only under a
# flat price is not accurate. Three shapes, cheapest possible.
M0_TARIFFS = ["flat", "tou2", "spot_midvol"]

# ---------------------------------------------------------------------------
# 8. Experiment switches and cell counts
# ---------------------------------------------------------------------------
# Set False to drop an experiment from the runlist without editing scripts.
ENABLED = {
    # MR runs first and gates the seed counts of everything below it.
    "MR": True,   # seed replication: how noisy is the GA, and how many seeds
    "M0": True,   # GA vs the compact MILP — licences everything downstream
    "M1": True,   # ROI cube: capacity x tariff x machine
    "M2": True,   # price volatility and fluctuation height
    "M3": True,   # scaling in the number of tasks
    "M4": True,   # machine-state ladder x storage (substitution)
    "M5": True,   # service-energy frontier (lambda sweep)
}

# --- M0: validation ---------------------------------------------------------
M0_METHODS = ["MILP", "GA", "H1"]
M0_BATTERY_RATIOS = [0.0, BATTERY_ON_RATIO]
# Anytime sub-cell: a subset of the validation pool, one tariff, fewer seeds.
M0_ANYTIME_SHOPS = 45 if PROFILE == "full" else 4
M0_ANYTIME_SEEDS = 3 if PROFILE == "full" else 1
M0_ANYTIME_TARIFF = "spot_midvol"

# --- M1: the ROI cube -------------------------------------------------------
# Fully crossed: 7 capacities x 5 tariffs x 6 machine archetypes.
M1_ARCHETYPES = P["m1_archetypes"] or list(machines.ARCHETYPES)
M1_BATTERY_RATIOS = BATTERY_RATIOS

# M1b: orthogonal (rho, restart) surface at three capacities, one tariff.
# Separate from the archetype factor because the archetypes are recognisable
# but not orthogonal; this grid is orthogonal but not recognisable. The paper
# needs both — the surface to establish the mechanism, the archetypes to name it.
M1B_BATTERY_RATIOS = [0.0, 0.25, 1.0]
M1B_TARIFF = "spot_midvol"

# --- M2: volatility ---------------------------------------------------------
M2_BATTERY_RATIOS = BATTERY_LADDER

# --- M3: scaling ------------------------------------------------------------
M3_BATTERY_RATIOS = BATTERY_LADDER
# n and the horizon move together (h is derived from a makespan lower bound),
# so a raw cost comparison across size classes confounds "more tasks" with
# "more price cycles". Every M3 quantity is therefore reported per horizon day
# and normalised by norm_scale(); the analysis enforces it and the runlist
# records inst_horizon_days so the check is possible at all.

# --- M4: substitution -------------------------------------------------------
M4_BATTERY_RATIOS = [0.0, 0.1, 0.25, 1.0]

# --- M5: frontier -----------------------------------------------------------
M5_BATTERY_RATIOS = [0.0, BATTERY_ON_RATIO]

# ---------------------------------------------------------------------------
# 8b. MR — seed replication, and how many seeds the campaign actually needs
# ---------------------------------------------------------------------------
#
# WHY THIS EXPERIMENT EXISTS, AND WHY IT RUNS FIRST.
#
# The GA is stochastic. Averaging k seeds inside each instance x configuration
# cell removes part of that noise, but not all of it, and the part that
# survives lands exactly where it does the most damage. In a PAIRED comparison
#     d = x_A - x_B
# the between-instance variability cancels -- that is the point of pairing --
# and what is left is
#     Var(d) = sigma_delta^2 + 2 sigma_seed^2 / k
# where sigma_delta is the genuine instance-to-instance variability of the
# effect and sigma_seed is the GA's own run-to-run spread. Every managerial
# number in M1-M5 is a paired difference, so with a small k the seed noise is
# not a second-order correction to the standard error: it can BE the standard
# error. Reporting a mean without knowing sigma_seed is reporting a number
# whose precision is unknown.
#
# Choosing k without measuring sigma_seed first is guesswork, and the guess is
# not cheap: moving M1 from 3 to 5 seeds costs about 1,400 core-hours. MR
# measures it, on the real solver at the real time budget (sigma_seed depends
# on both), and the required k follows from the formula in required_seeds()
# below. It costs ~2 % of the campaign and it runs FIRST, before the runlist
# for M1-M5 is frozen.
#
# It also asks a second question that a single dispersion number cannot: is
# sigma_seed the SAME across treatments? If the GA is noisier with a battery
# than without, or noisier on a machine that cannot switch off, then a
# difference of means between those cells is partly a difference of
# dispersions, and the paired test's assumptions do not hold. That is why MR
# crosses the battery level and three machine archetypes rather than just
# repeating one cell many times.

MR_SHOPS = P["mr_shops"]              # shops drawn from the core pool
MR_SEEDS = P["mr_seeds"]              # seeds per cell -- needs df, so >= 8
MR_TARIFFS = ["flat", "spot_midvol"]  # flat is the placebo: sigma_seed there is pure noise
MR_BATTERY_RATIOS = [0.0, BATTERY_ON_RATIO, 1.0]
MR_ARCHETYPES = [machines.BEST_CASE_ARCHETYPE, machines.BASELINE_ARCHETYPE,
                 machines.WORST_CASE_ARCHETYPE]

# The smallest effect the campaign is meant to resolve, as a percentage of the
# naive energy bill (the norm_scale denominator used throughout).
#
# 0.5 pp is not arbitrary: v1's pre-registration used the same figure as the
# admissibility threshold for the flat-tariff resolution floor, and effects
# below it were already going to be reported as unresolvable. Setting the
# replication target to the same number keeps one definition of "too small to
# matter" instead of two.
MDE_TARGET_PCT = 0.5

# Power convention for required_seeds(): two-sided alpha = 5 %, power = 80 %.
# (z_{0.975} + z_{0.80}) = 1.96 + 0.84 = 2.80.
POWER_Z = 2.80

# Floor on the seed count, whatever the power calculation returns.
#
# The formula below can legitimately return k = 1: with 288 paired instances,
# averaging over instances already delivers the required precision and a second
# seed adds little. Reporting single-run metaheuristic results anyway would be
# wrong twice over. First, disciplinary convention in metaheuristics is several
# independent runs per instance, and a paper that reports one will be asked why
# regardless of the arithmetic. Second, and more concretely, k = 1 makes
# sigma_seed unestimable inside the campaign itself: there is no within-cell
# variance to measure, so the campaign could no longer check that its own noise
# level matches what MR found, nor detect that it drifted. Three is the
# smallest number that leaves 2 degrees of freedom per cell.
MIN_SEEDS = 3

# Cap on the common-random-numbers correlation used for PLANNING.
#
# rho enters the required-seed formula as (1 - rho): a rho near 1 drives the
# within-cell variance term to zero and the required seed count with it. That
# is arithmetically right and practically dangerous, for three reasons.
# First, rho is estimated, often from few pairs, and its sampling error is
# largest exactly where it matters. Second, a rho near 1 usually means the
# instrumentation is wrong rather than that the solver is well behaved -- if
# the same seed produces a perfectly proportional result in two different
# configurations, the configurations are probably not reaching the model (the
# very failure mode the runbook's checkpoint 3 exists to catch). Third, the
# benefit of common random numbers is real but bounded in practice; planning
# for more than 0.9 buys a seed count nobody would defend.
#
# The report therefore plans on min(cap, lower bound of the bootstrap CI on
# rho) and prints the point estimate beside it, with a warning above 0.95.
RHO_PLANNING_CAP = 0.9
RHO_SUSPICIOUS = 0.95

# Practical ceiling on the seed count.
#
# The power formula is finite long after it has stopped being useful advice.
# Monte-Carlo check at sigma_seed = 3, sigma_effect = 1, n = 36: the formula
# returns k = 214, which is arithmetically right (that many seeds really would
# deliver 80 % power) and operationally absurd -- 214 seeds on M1 would be
# 3 million runs. Past this ceiling the honest output is not a seed count but
# the effect size the experiment CAN resolve at a sane k, which is what
# achievable_mde() below reports.
#
# 10 is chosen so that the ceiling never binds on a well-behaved solver: at
# k = 10 the seed-noise term is a tenth of its single-run value, and if that is
# still not enough the constraint is the instance count, not replication.
MAX_SEEDS = 10


def paired_contrast_variance(sigma_a: float, sigma_b: float, rho: float,
                             sigma_effect: float, k: float) -> float:
    """Var(d_i) for one paired contrast, from the components MR measures.

    DERIVATION, written out because the shortcut version of this formula is
    easy to get wrong and a referee is entitled to check it.

    Write the objective of instance i under configuration A, seed s, as

        x_{A,i,s} = mu_A + a_i + e_{A,i,s},      e ~ (0, sigma_A^2)

    where a_i is the instance's own level (huge, and common to A and B, which
    is exactly why the comparison is paired) and e is the GA's run-to-run
    noise. The k-seed cell mean is x_bar_{A,i} = mu_A + a_i + e_bar_{A,i} with
    Var(e_bar) = sigma_A^2 / k. The paired difference is

        d_i = x_bar_{A,i} - x_bar_{B,i}
            = (mu_A - mu_B) + (a_i - a_i) + (e_bar_{A,i} - e_bar_{B,i})
            = delta + u_i + (e_bar_{A,i} - e_bar_{B,i})

    where a_i cancels -- the point of pairing -- and u_i, with variance
    sigma_effect^2, is what remains of the instance: the fact that the EFFECT
    itself differs from plant to plant. Hence

        Var(d_i) = sigma_effect^2 + (sigma_A^2 + sigma_B^2 - 2 rho sigma_A sigma_B) / k

    THE rho TERM IS NOT DECORATION. The campaign runs seed s in cell A and the
    same seed s in cell B, so the two draws are not independent: they share the
    GA's initial population and its whole random stream. That is common random
    numbers, deliberately, and it induces rho > 0, which SHRINKS the variance of
    the contrast. Assuming rho = 0 (the textbook 2 sigma^2 / k) is conservative
    but can overstate the required seed count several-fold. MR measures rho
    directly, per contrast, from the seed-matched pairs.

    Setting sigma_A = sigma_B = sigma_seed and rho = 0 recovers the familiar
    2 sigma_seed^2 / k.
    """
    cov = 2.0 * rho * sigma_a * sigma_b
    within = max(0.0, sigma_a ** 2 + sigma_b ** 2 - cov)
    return sigma_effect ** 2 + within / max(1e-12, k)


def required_seeds(sigma_seed: float, sigma_delta: float, n_instances: int,
                   mde: float = MDE_TARGET_PCT, z: float = POWER_Z,
                   sigma_b: float | None = None, rho: float = 0.0) -> float:
    """Seeds needed per cell to resolve an effect of size `mde`.

    The test is a paired t on the n instance-level differences d_i, so its
    power is governed by

        SE(d_bar) = sqrt( Var(d_i) / n )

    and detecting delta at two-sided alpha with power 1-beta needs
    delta >= z * SE(d_bar) with z = z_{1-alpha/2} + z_{1-beta}. Substituting
    Var(d_i) from paired_contrast_variance and solving for k:

        n (delta/z)^2 >= sigma_effect^2 + W/k,    W = sigma_A^2 + sigma_B^2 - 2 rho sigma_A sigma_B

        k >= W / ( n (delta/z)^2 - sigma_effect^2 )

    THREE APPROXIMATIONS, STATED SO THEY CAN BE CHECKED.

    (1) z rather than t_{n-1}. The exact condition is implicit in k through the
        non-centrality parameter. Measured at this campaign's instance counts:
        at n = 36 the normal approximation under-states k by about 7 % (8.10
        against 8.66, i.e. the same 9 after rounding up), and at n >= 288 the
        two are identical. `required_seeds_t` below solves the exact version by
        bisection on the non-centrality when scipy is available, and the report
        prints both. They have never differed by a whole seed at any setting
        this campaign uses; if they ever do, quote the exact one.

    (2) sigma_seed is ESTIMATED, so k is estimated too. MR reports a bootstrap
        interval on it; planning on the upper end of that interval rather than
        the point estimate is the safe choice and the report prints both.

    (3) Homoscedasticity between the two cells, unless `sigma_b` is passed.
        MR's section 2 tests exactly this, and if it comes back heteroscedastic
        the caller should pass both sigmas rather than one.

    Returns +inf when the denominator is non-positive -- the answer that matters
    most: the instance-to-instance variability of the effect alone already
    exceeds the budget, so no amount of seed replication resolves it and the fix
    is more instances. Floored at MIN_SEEDS (see its definition: a reporting
    convention, not a statistical one).
    """
    sa = sigma_seed
    sb = sigma_seed if sigma_b is None else sigma_b
    room = n_instances * (mde / z) ** 2 - sigma_delta ** 2
    if room <= 0:
        return float("inf")
    within = max(0.0, sa ** 2 + sb ** 2 - 2.0 * rho * sa * sb)
    k = within / room
    return max(float(MIN_SEEDS), k)


def achievable_mde(sigma_seed: float, sigma_delta: float, n_instances: int,
                   k: float, z: float = POWER_Z, sigma_b: float | None = None,
                   rho: float = 0.0) -> float:
    """The smallest effect resolvable at a GIVEN seed count -- the inverse
    question, and the one worth asking whenever the required k is out of reach.

        delta = z * sqrt( Var(d) / n )

    Reported instead of an unusable seed count. "This experiment resolves
    effects down to 1.3 % of the naive bill" is a statement a paper can carry;
    "this experiment needs 214 seeds" is not.
    """
    var = paired_contrast_variance(sigma_seed,
                                   sigma_seed if sigma_b is None else sigma_b,
                                   rho, sigma_delta, max(1.0, k))
    return z * math.sqrt(var / max(1, n_instances))


def required_seeds_t(sigma_seed: float, sigma_delta: float, n_instances: int,
                     mde: float = MDE_TARGET_PCT, alpha: float = 0.05,
                     power: float = 0.80, sigma_b: float | None = None,
                     rho: float = 0.0) -> float:
    """Exact paired-t version of required_seeds, by fixed-point iteration.

    Kept separate rather than replacing the closed form: the closed form is what
    goes in the paper because it can be read and checked by eye, and this
    function exists to demonstrate that doing it properly changes nothing at
    this campaign's scale. If the two ever disagree by more than a seed, the
    paper should quote this one and say why.

    Falls back to the normal approximation without scipy.
    """
    try:
        from scipy import stats
    except ImportError:
        return required_seeds(sigma_seed, sigma_delta, n_instances, mde,
                              POWER_Z, sigma_b, rho)
    df = max(1, n_instances - 1)
    # The non-centrality needed for the given power at this df. Solve
    # P(T_{df, ncp} > t_crit) = power for ncp, by bisection -- monotone in ncp.
    t_crit = stats.t.ppf(1 - alpha / 2, df)
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if stats.nct.sf(t_crit, df, mid) < power:
            lo = mid
        else:
            hi = mid
    ncp = 0.5 * (lo + hi)
    # ncp = delta / SE, so the same algebra with z replaced by ncp.
    return required_seeds(sigma_seed, sigma_delta, n_instances, mde, ncp,
                          sigma_b, rho)


# ---------------------------------------------------------------------------
# 9. Cost model for the budget check
# ---------------------------------------------------------------------------
# Fraction of its time limit each method actually consumes. H1 is constructive
# and finishes in a rounding error; GA runs to the clock or to stagnation
# (v1 measured 0.69 of the limit, and the re-tune is expected to push that
# towards 1.0, so 0.95 is the conservative planning figure); the MILP runs out
# the clock on everything but the smallest instances.
EST_TIME_FRACTION = {"H1": 0.05, "GA": 0.95, "MILP": 0.9}


def machine_args(archetype: str) -> list[str]:
    """Solver flags for one named archetype."""
    return machines.solver_args(machines.ARCHETYPES[archetype])


def grid_machine_args(rho: float, restart: str) -> list[str]:
    """Solver flags for one (rho, restart) cell of the M1b surface."""
    return machines.solver_args(machines.grid_profile(rho, restart))


def state_args(state: str) -> list[str]:
    flag = STATE_POLICIES[state]["flag"]
    return [flag] if flag else []
