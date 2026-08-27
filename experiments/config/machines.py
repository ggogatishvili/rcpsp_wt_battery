"""
Machine transition graphs — the third managerial lever.

WHY THIS FILE EXISTS. The campaign asks what changes when the machine's
transition graph changes: what if restarting from Off is prohibitively
expensive, what if idling is nearly free, what if the two are combined. In the
solver these are five numbers (`e_proc`, `e_idle`, and the four transition
{time, cost} pairs), but a reader of the paper does not think in five numbers —
they think "my oven", "my CNC", "my continuous line". This file is the mapping
between the two, in one place, so that a figure axis labelled T4 (oven) always
means the same solver flags.

TWO SUB-DESIGNS, DELIBERATELY DIFFERENT IN PURPOSE

  ARCHETYPES  six named profiles spanning the plane, used as a *factor* in the
              M1 ROI cube. They are chosen to be recognisable, not to be
              orthogonal: T1 and T5 are the falsification corners (a machine
              that costs nothing to switch, and a machine that effectively
              cannot be switched off), and the middle four are plausible plants.

  GRID        a clean 3 x 3 factorial over (idle ratio rho, restart penalty),
              used for the M1b response surface. Orthogonal by construction,
              so the two main effects and their interaction are estimable
              without the archetype confound.

CALIBRATION STATUS — READ BEFORE QUOTING A MAGNITUDE. The rho levels and the
transition costs below are *stylised*, not measured. They are internally
consistent (monotone in the intended direction, and the baseline T3 reproduces
`include/instance.h` exactly) and that is enough to support statements of the
form "the return on storage falls by X when restarting becomes expensive".
It is NOT enough to support "an industrial oven saves EUR Y". Until archetype
levels are sourced from published machine energy-profile data, report the
*ordering and the shape* of the effect, not its absolute size, and say so in
Threats to Validity.

UNITS. `e_proc` is the reference load; everything else is relative to it.
`e_proc` is held at 4.0 across every profile on purpose: battery capacities are
expressed as multiples of E_day = e_proc x sum(EI durations) / days, so moving
e_proc would silently change what "B = 1.0 E_day" means and make the machine
factor non-comparable across cells. Only e_idle and the transitions vary.
"""

from __future__ import annotations

# The reference processing load. Fixed. See the UNITS note above.
E_PROC = 4.0

# Baseline transition pairs, matching include/instance.h. Any profile that does
# not override a pair inherits it from here, so a profile definition below
# shows only what makes it different.
_BASELINE_TRANSITIONS = {
    "off_proc":  {"time": 2, "cost": 5.0},     # cold start
    "proc_off":  {"time": 1, "cost": 1.0},     # shutdown
    "proc_idle": {"time": 1, "cost": 2.0},     # step down
    "idle_proc": {"time": 1, "cost": 2.5},     # warm restart
}


def _profile(rho: float, **overrides) -> dict:
    """One machine profile: idle ratio plus any transition overrides."""
    trans = {k: dict(v) for k, v in _BASELINE_TRANSITIONS.items()}
    for k, v in overrides.items():
        trans[k].update(v)
    return {"rho": rho, "e_proc": E_PROC, "e_idle": round(rho * E_PROC, 6),
            **trans}


# ---------------------------------------------------------------------------
# Archetypes — the M1 machine factor
# ---------------------------------------------------------------------------
# Ordered from "most switchable" to "least switchable". That ordering is the
# x-axis of every archetype figure, so keep the dict insertion order.

ARCHETYPES: dict[str, dict] = {
    # T1 — the falsification corner. Switching is free and idling costs
    # nothing, so state management is a pure gain and storage competes with a
    # near-perfect substitute. If storage still pays here, it is not paying
    # merely because the machine is inflexible.
    #
    # NOTE ON THE DURATIONS. They are 1, not 0. The solver reserves a transition
    # duration of 0 to mean "this transition does not exist", so a genuinely
    # instantaneous switch is not expressible; the closest expressible corner is
    # "free but not instantaneous" — one time unit, zero energy. T1 is therefore
    # a lower bound on the ideal machine, not the ideal machine itself, which
    # only strengthens the falsification argument: a still-positive return on
    # storage at T1 would be at least as positive at a truly instantaneous
    # machine. Say this in Threats to Validity; do not describe T1 as
    # "instantaneous" in the paper.
    "T1_ideal": _profile(
        0.00,
        off_proc={"time": 1, "cost": 0.0}, proc_off={"time": 1, "cost": 0.0},
        proc_idle={"time": 1, "cost": 0.0}, idle_proc={"time": 1, "cost": 0.0}),

    # T2 — fast electric load (CNC, induction heating). Cheap to stop and
    # start, low standby draw. The plant type most often used to argue that
    # scheduling alone is sufficient.
    "T2_fast_electric": _profile(
        0.25,
        off_proc={"time": 1, "cost": 2.0}, proc_off={"time": 1, "cost": 1.0},
        proc_idle={"time": 1, "cost": 1.0}, idle_proc={"time": 1, "cost": 1.0}),

    # T3 — the paper's baseline. Reproduces include/instance.h byte for byte;
    # every other experiment in the campaign runs at T3, so it is the anchor
    # against which the machine factor is read.
    "T3_baseline": _profile(0.50),

    # T4 — thermal mass (industrial oven, furnace, curing line). Restart is
    # slow and expensive; idling is the only realistic between-jobs state.
    "T4_thermal_oven": _profile(
        0.50,
        off_proc={"time": 4, "cost": 20.0}, proc_off={"time": 2, "cost": 4.0},
        idle_proc={"time": 1, "cost": 3.0}),

    # T5 — continuous process. Shutdown is nominally available but so costly
    # and so slow that the optimiser will essentially never take it, and
    # standby draw is high. This is the "storage is the only lever left" plant.
    "T5_continuous": _profile(
        0.75,
        off_proc={"time": 6, "cost": 60.0}, proc_off={"time": 3, "cost": 10.0},
        idle_proc={"time": 1, "cost": 3.0}),

    # T6 — cheap idle. Same restart economics as the baseline, but standby is
    # nearly free. Isolates "idling is really beneficial" from "switching off
    # is cheap": T6 vs T3 moves only rho, T2 vs T3 moves mainly the transitions.
    "T6_cheap_idle": _profile(
        0.10,
        idle_proc={"time": 1, "cost": 1.0}, proc_idle={"time": 1, "cost": 1.0}),
}

# The profile every non-machine experiment runs at. Named rather than
# positional so that changing the anchor is a one-line, greppable edit.
BASELINE_ARCHETYPE = "T3_baseline"

# Corners used by the analysis to state the effect as a range, and by the
# falsification checks. T1 must dominate every other profile at every battery
# level; if it does not, the machine factor is not wired through correctly.
BEST_CASE_ARCHETYPE = "T1_ideal"
WORST_CASE_ARCHETYPE = "T5_continuous"


# ---------------------------------------------------------------------------
# Orthogonal grid — the M1b response surface
# ---------------------------------------------------------------------------
# rho: how expensive it is to WAIT hot.  restart: how expensive it is to COME
# BACK from Off. These are the two things a plant engineer can actually quote,
# and the surface over them is the figure a manager reads.

RHO_LEVELS = [0.10, 0.50, 0.90]           # cheap / baseline / idle ~ processing

RESTART_LEVELS: dict[str, dict] = {
    "low":         {"time": 1, "cost": 2.0},
    "med":         {"time": 2, "cost": 5.0},      # = baseline, the T3 anchor
    "prohibitive": {"time": 6, "cost": 60.0},     # = T5's restart
}


def grid_profile(rho: float, restart: str) -> dict:
    """One (rho, restart) cell of the orthogonal surface."""
    return _profile(rho, off_proc=dict(RESTART_LEVELS[restart]))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
# This section exists because of a real failure. The first campaign run lost
# 918 of 6 786 runs — 13.5 %, every one of them on T1 — to a single solver
# message: "Machine profile transition durations must be >= 1 (0 is reserved to
# mean 'no such transition')". The profile was syntactically fine, the runlist
# was fine, the harness was fine; the numbers were simply outside the solver's
# domain, and nothing between this file and the compute node checked. Six
# core-hours of a five-day campaign is a cheap lesson, but only if the check
# now runs at import, where it costs microseconds and fails before a single
# instance is generated.

TRANSITION_KEYS = ("off_proc", "proc_off", "proc_idle", "idle_proc")


def validate_profile(profile: dict, label: str = "profile") -> None:
    """Raise if `profile` is outside the solver's accepted domain.

    Mirrors the checks in the C++ `Instance` constructor. Keep the two in step:
    a check that exists only here will not stop a hand-written command line,
    and a check that exists only there costs a campaign.
    """
    e_proc, e_idle = profile["e_proc"], profile["e_idle"]
    if e_proc <= 0:
        raise ValueError(f"{label}: e_proc must be > 0, got {e_proc}")
    if not 0 <= e_idle <= e_proc:
        raise ValueError(
            f"{label}: e_idle must lie in [0, e_proc] = [0, {e_proc}], "
            f"got {e_idle}. Idling above the processing load is not a machine, "
            f"it is a typo.")
    for k in TRANSITION_KEYS:
        if k not in profile:
            raise ValueError(f"{label}: missing transition '{k}'")
        t, c = profile[k]["time"], profile[k]["cost"]
        if not isinstance(t, int) or t < 1:
            raise ValueError(
                f"{label}: transition '{k}' has time={t!r}. Durations must be "
                f"integers >= 1 — the solver reserves 0 to mean 'no such "
                f"transition', so a free switch is expressed as time=1, "
                f"cost=0.0, not time=0.")
        if c < 0:
            raise ValueError(
                f"{label}: transition '{k}' has cost={c}. Negative transition "
                f"energy would let the schedule earn energy by switching.")


def validate_all() -> int:
    """Validate every profile this file can emit. Returns how many it checked."""
    n = 0
    for name, p in ARCHETYPES.items():
        validate_profile(p, f"ARCHETYPES[{name}]")
        n += 1
    for rho in RHO_LEVELS:
        for restart in RESTART_LEVELS:
            validate_profile(grid_profile(rho, restart),
                             f"grid_profile(rho={rho}, restart={restart!r})")
            n += 1
    return n


# Runs on import. Every entry point — runlist generation, preflight, analysis,
# an interactive `python3 -c 'import config.machines'` — pays for this check,
# and none of them can skip it.
validate_all()


# ---------------------------------------------------------------------------
# Solver flags
# ---------------------------------------------------------------------------

def solver_args(profile: dict) -> list[str]:
    """Command-line flags realising one profile.

    Every field is passed explicitly, including the ones that equal the
    solver's compiled-in default. That is deliberate: a run's argv is the
    record of what it measured, and a profile that relies on defaults becomes
    wrong the day someone edits include/instance.h.
    """
    t = profile
    return [
        "--e-proc", f"{t['e_proc']:g}",
        "--e-idle", f"{t['e_idle']:g}",
        "--e-off", "0",
        "--off-proc-time",  str(t["off_proc"]["time"]),
        "--off-proc-cost",  f"{t['off_proc']['cost']:g}",
        "--proc-off-time",  str(t["proc_off"]["time"]),
        "--proc-off-cost",  f"{t['proc_off']['cost']:g}",
        "--proc-idle-time", str(t["proc_idle"]["time"]),
        "--proc-idle-cost", f"{t['proc_idle']['cost']:g}",
        "--idle-proc-time", str(t["idle_proc"]["time"]),
        "--idle-proc-cost", f"{t['idle_proc']['cost']:g}",
    ]


# Flags the runlist generator probes for before activating any machine cell.
# If the binary predates C2, these experiments are written to the blocked list
# with a reason instead of silently running at the compiled-in profile — which
# is the failure mode that would produce a beautiful, meaningless surface.
REQUIRED_FLAGS = {"--e-proc", "--e-idle", "--off-proc-time", "--off-proc-cost",
                  "--proc-off-time", "--proc-off-cost", "--proc-idle-time",
                  "--proc-idle-cost", "--idle-proc-time", "--idle-proc-cost"}


def summary_table() -> str:
    """Human-readable dump, printed by the preflight and pasted into the paper."""
    hdr = (f"{'id':<18} {'rho':>5} {'e_idle':>7} "
           f"{'off>proc':>10} {'proc>off':>10} {'proc>idle':>10} {'idle>proc':>10}")
    out = [hdr, "-" * len(hdr)]
    for name, p in ARCHETYPES.items():
        f = lambda k: f"{p[k]['time']}h/{p[k]['cost']:g}"        # noqa: E731
        out.append(f"{name:<18} {p['rho']:>5.2f} {p['e_idle']:>7.2f} "
                   f"{f('off_proc'):>10} {f('proc_off'):>10} "
                   f"{f('proc_idle'):>10} {f('idle_proc'):>10}")
    return "\n".join(out)


if __name__ == "__main__":
    print(summary_table())
