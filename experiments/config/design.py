"""
Experimental design — single source of truth.

Every script under experiments/ reads this file. Changing a value here changes
the generated instances, the runlist and the analyses consistently. Nothing
else in the package hard-codes a design constant.

Budget model (see PROFILE below): the runlist generator estimates total core
seconds before anything is executed and refuses to proceed if the estimate
exceeds WALL_CLOCK_BUDGET_H * N_WORKERS. Change the profile, not the scripts.
"""

from typing import TypedDict

# ----------------------------------------------------------------------------
# 0. Machine / budget
# ----------------------------------------------------------------------------

N_WORKERS = 60           # leave 4 of 64 cores for OS, I/O and Gurobi overhead
WALL_CLOCK_BUDGET_H = 96  # hard ceiling used by the budget check (5 days = 120h)
                          # 96h leaves ~24h of slack for reruns and analysis

# Master seed. Every random draw in the package is derived deterministically
# from this value plus a stable string key, so the whole benchmark is
# reproducible from this one integer. Never change it after generation.
MASTER_SEED = 20260801

# ----------------------------------------------------------------------------
# 1. Profile
# ----------------------------------------------------------------------------
# "pilot"    ~40 core-h   — sanity check the pipeline and the E1/E4 signal
# "moderate" ~900 core-h  — adequately powered for E1, E2, E4
# "full"     ~3200 core-h — the design described in EXPERIMENTAL_PLAN.md
PROFILE = "full"


class _Profile(TypedDict):
    sizes: list[int]
    reps: int
    core_draws: int
    e3_draws: int
    seeds: int
    e3_shops: int
    e3_synth_draws: int


_PROFILES: dict[str, _Profile] = {
    "pilot":    {"sizes": [2, 4],           "reps": 2,  "core_draws": 2, "e3_draws": 4,
                "seeds": 2, "e3_shops": 12, "e3_synth_draws": 1},
    "moderate": {"sizes": [1, 2, 4, 8],     "reps": 6,  "core_draws": 3, "e3_draws": 12,
                "seeds": 3, "e3_shops": 48, "e3_synth_draws": 1},
    "full":     {"sizes": [1, 2, 4, 8, 16], "reps": 10, "core_draws": 5, "e3_draws": 20,
                "seeds": 5, "e3_shops": 90, "e3_synth_draws": 1},
}
P = _PROFILES[PROFILE]

# ----------------------------------------------------------------------------
# 2. Shop-floor factors  (all realised by the instance generator; no solver
#    changes required)
# ----------------------------------------------------------------------------

# PSPLIB size classes available in instance_generator/instances_original/.
# Class p contains 32*p tasks; 20 independent structures exist per class.
SIZE_CLASSES = P["sizes"]                     # -> n in {32,64,128,256,512}
TASKS_PER_CLASS = 32

REPLICATES = P["reps"]                        # distinct PSPLIB structures per cell

# Energy-intensive task density: share of tasks requiring resource R0.
EI_DENSITY = {"d10": 0.10, "d25": 0.25, "d50": 0.50}

# Due-date tightness. due_j = r_j + p_j + rho * (h - r_j - p_j), rho ~ U(lo,hi).
# "med" reproduces the slack range of the original InstanceGenerator.py.
DUE_TIGHTNESS = {
    "tight": (0.00, 0.05),
    "med":   (0.10, 0.20),
    "loose": (0.35, 0.50),
}

# Horizon: rounded up to whole days from a makespan lower bound, so that every
# generated instance is guaranteed feasible and spans an integer number of
# daily price cycles. HORIZON_SLACK multiplies the lower bound before rounding.
HORIZON_SLACK = 1.45
HOURS_PER_DAY = 24
MIN_HORIZON_DAYS = 3      # storage needs at least a few troughs to arbitrage

# Tardiness cost scale (E4 treatment). 1.0 is the baseline used everywhere else.
LAMBDA_BASE = 1.0
LAMBDA_LEVELS = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

# ----------------------------------------------------------------------------
# 3. Tariff library
# ----------------------------------------------------------------------------
# NOTE ON DATA AVAILABILITY: only Czech 2025 day-ahead data ships with the
# repository (instance_generator/electricity_cost_eur_mwh_2025.csv). The
# multi-year regimes described in EXPERIMENTAL_PLAN.md 3.2 cannot be built
# without additional downloads. Instead we stratify 2025 windows by their
# realised intra-day spread into three volatility regimes, which is honest and
# reproducible. To add real 2019/2022 series later, drop CSVs with the same
# schema into experiments/data/prices_raw/ and list them in EXTRA_PRICE_CSVS.
EXTRA_PRICE_CSVS = []      # e.g. ["electricity_cost_eur_mwh_2022.csv"]

# How many spot windows per regime are materialised for the *core* instance set
# (used by E0/E1/E2/E4) and for the *E3* set. E3 needs many windows because
# tariff variation is its treatment; the core experiments need only enough to
# average over window-specific luck, so crossing every shop with 20 windows
# there would multiply cost without adding statistical power.
CORE_DRAWS_PER_REGIME = P["core_draws"]
E3_DRAWS_PER_REGIME = P["e3_draws"]

# Spot windows actually *used* per experiment (contractual tariffs always
# contribute exactly one series each). Must be <= CORE_DRAWS_PER_REGIME.
SPOT_SERIES_PER_EXP = {"E0": 1, "E1": 3, "E2": 3, "E4": 1, "E6": 1}

# Seeds per experiment for the stochastic methods. E2 sweeps seven capacities
# and is the most expensive cell, so it runs fewer seeds; its quantity of
# interest (the shape of the savings curve) is averaged over 450 instances and
# does not need five seeds each. E6 sweeps two full factor grids on top of the
# instance/regime plane (see §7), so it follows the plan's default assumption
# of a single seed per cell rather than P["seeds"].
SEEDS_PER_EXP = {"E0": P["seeds"], "E1": P["seeds"], "E2": max(1, P["seeds"] - 2),
                 "E3": P["seeds"], "E4": P["seeds"], "E6": 1}

# Spot regimes are defined by terciles of the mean intra-day spread of every
# candidate window, computed over the source year.
SPOT_REGIMES = ["spot_lowvol", "spot_midvol", "spot_highvol"]

# Contractual tariffs. "flat" is the falsification control: under a constant
# price no configuration can create arbitrage value, so any measured saving
# bounds the resolution of every other result in the study.
CONTRACTUAL = {
    "flat":  {"kind": "flat"},
    "tou2":  {"kind": "two_block", "peak_hours": (8, 20), "peak_ratio": 1.6},
}

# Synthetic controlled family (E3). Orthogonal variation of the three price
# characteristics that real data confounds.
SYNTH_SPREADS = [10.0, 30.0, 60.0, 100.0, 150.0]   # EUR/MWh peak-to-trough
SYNTH_NOISE = [0.05, 0.15, 0.30]                   # sd as fraction of mean
SYNTH_NEG_SHARE = [0.0, 0.08]                      # share of hours below zero
SYNTH_MEAN = 90.0                                  # EUR/MWh
SYNTH_DRAWS = P["e3_synth_draws"]

# Which regimes the core experiments use (E0/E1/E2/E4).
CORE_REGIMES = ["flat", "tou2", "spot_midvol"]
E2_REGIMES = ["spot_lowvol", "spot_midvol", "spot_highvol"]
E4_REGIME = "spot_midvol"

# ----------------------------------------------------------------------------
# 4. Solver-side factors
# ----------------------------------------------------------------------------

# Battery capacity is expressed as a multiple of E_day, the mean daily energy
# demand of the EI machine on that instance, and converted to the solver's
# integer -b argument at runlist time.
BATTERY_RATIOS = [0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0]
BATTERY_ON_RATIO = 1.0        # the "battery installed" level used by E1/E3/E4

# Scheduling policy: EDD (H1/GA) vs price-aware (H1P/GAP).
POLICIES = {
    "edd":         {"method_h": "H1",  "method_ga": "GA",  "flags": []},
    "price_aware": {"method_h": "H1P", "method_ga": "GAP", "flags": ["--phase1-price-aware"]},
}

# State policy (E1 sigma dimension). BLOCKED: requires solver support for
# restricting the state set (item C1 in EXPERIMENTAL_PLAN.md). The runlist
# generator probes `solver --help` for "--states" and silently omits these
# cells until the flag exists, so the design does not need editing later.
STATE_POLICIES = {
    "sigma3": {"flag": None,                    "blocked": False},  # full model, current behaviour
    "sigma2": {"flag": "--states=proc,idle",    "blocked": True},
    "sigma1": {"flag": "--states=proc",         "blocked": True},
}

SEEDS = list(range(1, P["seeds"] + 1))   # GA/GAP are stochastic; H1/H1P/MILP are not

# ----------------------------------------------------------------------------
# 5. Time limits (seconds)
# ----------------------------------------------------------------------------

TL = {
    "MILP": 600,     # only run on the two smallest size classes
    "H1":   120,     # constructive; the limit is a guard, not a budget
    "H1P":  120,
    "GA":    60,     # the planning-time budget the paper reports
    "GAP":   60,
}

MILP_MAX_SIZE_CLASS = 2      # MILP only for n <= 64

# Per-run memory ceiling passed to the solver (--ml, in GB). 350GB / 60 workers
# leaves ~5.8GB each; 5 is a safe cap that still lets the MILP breathe.
MEM_LIMIT_GB = 5
THREADS_PER_RUN = 1          # one core per run; parallelism comes from the driver

# ----------------------------------------------------------------------------
# 6. Experiment switches
# ----------------------------------------------------------------------------
# Set to False to drop an experiment from the runlist without editing scripts.
ENABLED = {
    "E0": True,    # solver validation
    "E1": True,    # value decomposition (sigma dimension auto-skipped if blocked)
    "E2": True,    # storage sizing
    "E3": True,    # tariff regimes
    "E4": True,    # service-energy frontier
    "E6": True,    # machine profile / battery efficiency / C-rate (needs C2/C3/C4)
    "E7": False,   # BLOCKED: needs C7 (re-costing mode) and C8 (baseline policies)
}

# ----------------------------------------------------------------------------
# 7. E6 factors — machine profile (C2), battery efficiency (C3), C-rate (C4)
# ----------------------------------------------------------------------------
# These three solver capabilities landed after the rest of this design; none
# of them are baked into instance files (unlike EI density, tau or lambda), so
# E6 needs no new instances — it reuses the existing E3 stratified 90-shop
# subset (B_scr) crossed with CORE_REGIMES, and only adds solver flags.
#
# The run-budget line in EXPERIMENTAL_PLAN.md ("12 machine x 2 beta, + 16
# battery") is a SUM of two independent sub-designs, not one cross-product:
#   E6a - machine substitution map: (rho, restart) grid x policy, on-battery
#   E6b - C-rate retention:         (round-trip eff, C-rate) grid, price-aware
# 12 x 2 x 90 x 3 x 1 seed = 6480, 16 x 1 x 90 x 3 x 1 seed = 4320,
# 6480 + 4320 = 10800, matching the plan's "~10 800" figure.
#
# CALIBRATION WARNING: the archetype numbers below (rho levels, restart
# time/cost, efficiency/C-rate levels) are the same "invented, needs a
# citable source" placeholders EXPERIMENTAL_PLAN.md §3.3 flags — swap them
# for published machine energy-profile data before quoting E6 results.

# e_proc is held fixed at lib/generate.py's E_PROC reference (4.0); only
# e_idle (via rho) and the Off->Proc restart penalty vary. If e_proc is ever
# added to this grid, e_day() in lib/generate.py must be recomputed per cell,
# or every BATTERY_RATIOS / BATTERY_ON_RATIO figure silently changes meaning
# for those runs — see the E_PROC comment there.
E_PROC_REF = 4.0
RHO_LEVELS = [0.0, 0.25, 0.5, 0.75]      # e_idle / e_proc -- archetypes A5, A1, A2, A4

RESTART_LEVELS = {                        # Off->Proc transition {time, cost}
    "low":  {"time": 1, "cost": 2},       # archetype A1-like
    "med":  {"time": 2, "cost": 5},       # archetype A2 (current solver default)
    "high": {"time": 3, "cost": 10},      # archetype A3-like
}

# Round-trip efficiency eta_c * eta_d (EXPERIMENTAL_PLAN.md §3.4). Split
# symmetrically since the solver takes charging/discharging efficiency as two
# separate parameters, not one round-trip figure.
ROUNDTRIP_EFFICIENCY_LEVELS = [0.75, 0.85, 0.95, 1.0]
C_RATE_LEVELS = [0.25, 0.5, 1.0, float("inf")]   # infinity = uncapped (--c-rate omitted)


def machine_profile_args(rho: float, restart: str) -> list[str]:
    """Solver flags for one (rho, restart) E6a cell."""
    r = RESTART_LEVELS[restart]
    return ["--e-proc", str(E_PROC_REF), "--e-idle", str(round(rho * E_PROC_REF, 4)),
            "--off-proc-time", str(r["time"]), "--off-proc-cost", str(r["cost"])]


def battery_profile_args(roundtrip_eff: float, c_rate: float) -> list[str]:
    """Solver flags for one (round-trip efficiency, C-rate) E6b cell."""
    eta = roundtrip_eff ** 0.5
    args = ["--charging-efficiency", f"{eta:.6f}", "--discharging-efficiency", f"{eta:.6f}"]
    if c_rate != float("inf"):
        args += ["--c-rate", str(c_rate)]
    return args
