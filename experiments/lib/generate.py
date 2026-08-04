"""
Instance generation.

A *shop* is a (PSPLIB structure, EI density, due-date tightness, lambda) tuple.
A shop fixes everything except the price series; pairing a shop with a price
series yields one instance file. Keeping the shop separate from the tariff is
what makes E3 possible without regenerating structures, and it guarantees that
the same shop is bit-identical across tariffs, so tariff effects are not
confounded with structural noise.

Generation order per shop:
  1. load structure           (durations, resources, precedence)
  2. impose EI density        (which tasks need resource R0)
  3. derive horizon           (from a makespan lower bound -> whole days)
  4. release dates            (longest path, resource-unconstrained)
  5. due dates                (tightness level)
  6. tardiness weights        (reference price level x lambda)
  7. attach prices            (one file per series)

Step 6 uses a *fixed reference price level* (the source year's annual mean)
rather than the mean of the attached series. If weights tracked each regime's
own mean, the tardiness/energy balance would shift between regimes and E3 could
not separate a tariff effect from a re-weighting artefact.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

from config import design
from .rcpsp_io import (Instance, descriptors, earliest_starts,
                       makespan_lower_bound, read_original, write_instance)
from .rng import substream

# Energy consumed per interval while the machine is in Proc.
# HARD-CODED IN include/instance.h (struct Proc { int cost = 4; }). If item C2
# of EXPERIMENTAL_PLAN.md lands and this becomes instance data, update here and
# there together, or every battery-capacity ratio silently changes meaning.
E_PROC = 4.0


def shop_id(p: int, rep: int, dens: str, tight: str, lam: float) -> str:
    return f"p{p:02d}_r{rep:02d}_{dens}_{tight}_lam{int(round(lam * 100)):04d}"


def build_shop(orig_path: Path, p: int, rep: int, dens: str, tight: str,
               lam: float, ref_price: float) -> Instance:
    """Produce a fully-specified shop with an empty price vector."""
    inst = read_original(orig_path)
    sid = shop_id(p, rep, dens, tight, lam)
    n = inst.n

    # --- 2. EI density -----------------------------------------------------
    target = max(1, round(design.EI_DENSITY[dens] * n))
    rng = substream(f"ei|{sid}")
    chosen = set(rng.sample(range(n), target))
    tasks = []
    for i, t in enumerate(inst.tasks):
        res = list(t.resources)
        res[0] = 1 if i in chosen else 0
        tasks.append(replace(t, resources=res))
    inst = Instance(capacities=inst.capacities, tasks=tasks, prices=[])

    # --- 3. horizon --------------------------------------------------------
    # NOTE ON THE 6.96% NON-COMPLETION RATE OF THE FIRST FULL RUN.
    #
    # It is tempting to blame this rule: the model forces the machine Off at
    # t=0 and t=h-1, the boundary transitions consume p^{off,proc}=2 and
    # p^{proc,off}=1 intervals, and this rule does not reserve them. But that
    # is NOT the cause, and padding the horizon here is the wrong fix -- it
    # changes h for 100% of instances, which changes due dates AND the drawn
    # price window, i.e. it invalidates the entire benchmark.
    #
    # The actual cause is in the solver: SolverH1::getReadyTasks throws when a
    # task cannot fit before the mandatory Off tail, and SolverGA/SolverGAP
    # call scheduleTasks() during initial-population seeding *outside* any
    # try/catch, unlike their fitness evaluators which catch and assign
    # -BIG_M. One unlucky seeding ordering therefore kills the whole run.
    # Hence H1/H1P/MILP never fail while GA/GAP fail together on every seed.
    # Guard the seeding calls and the horizon rule below is fine as it stands.
    lb = makespan_lower_bound(inst)
    days = max(design.MIN_HORIZON_DAYS,
               math.ceil(lb * design.HORIZON_SLACK / design.HOURS_PER_DAY))
    h = days * design.HOURS_PER_DAY

    # --- 4/5. release and due dates ---------------------------------------
    es = earliest_starts(inst)
    lo, hi = design.DUE_TIGHTNESS[tight]
    rng_due = substream(f"due|{sid}")
    rng_w = substream(f"w|{sid}")
    out = []
    for i, t in enumerate(inst.tasks):
        rel = es[i]
        latest = h - 1
        room = max(0, latest - rel - t.duration)
        rho = rng_due.uniform(lo, hi)
        due = min(latest, rel + t.duration + round(rho * room))
        due = max(due, rel + t.duration)          # never due before it can finish
        # --- 6. weight ----------------------------------------------------
        w = (ref_price / 10.0) * rng_w.uniform(0.5, 1.0) * lam
        out.append(replace(t, release=rel, due=due, weight=round(w, 4)))

    inst = Instance(capacities=inst.capacities, tasks=out, prices=[0.0] * h)
    # NOTE: keys here must not collide with rcpsp_io.descriptors(), which is
    # merged on top of them in instance_row(). The *level labels* live under
    # `_level` names; the realised numeric values come from descriptors().
    inst.meta = {"shop_id": sid, "size_class": p, "replicate": rep,
                "ei_density_level": dens, "due_tightness_level": tight,
                "lam": lam, "makespan_lb": lb, "horizon": h, "horizon_days": days}
    return inst


def e_day(inst: Instance) -> float:
    """Mean daily processing energy of the EI machine, in the solver's units.

    Battery capacities are expressed as multiples of this quantity so that a
    ratio of 1.0 means the same thing on a 32-task and a 512-task instance.
    """
    ei_dur = sum(inst.tasks[i].duration for i in inst.ei_ids)
    days = inst.horizon / design.HOURS_PER_DAY
    return E_PROC * ei_dur / days if days else 0.0


def battery_arg(inst: Instance, ratio: float) -> int:
    """Integer -b argument realising `ratio` x E_day (solver takes an int)."""
    return max(0, round(ratio * e_day(inst)))


def materialise(inst: Instance, series_values: list[float], out_path: Path) -> str:
    """Attach a price series to a shop and write the instance file."""
    if len(series_values) != inst.horizon:
        raise ValueError(f"{out_path}: series has {len(series_values)} hours, "
                         f"shop horizon is {inst.horizon}")
    concrete = Instance(capacities=inst.capacities, tasks=inst.tasks,
                        prices=list(series_values), meta=dict(inst.meta))
    return write_instance(out_path, concrete)


def instance_row(inst: Instance, price_name: str, price_regime: str,
                 price_desc: dict, path: Path, sha: str, subset: str) -> dict:
    """One manifest row: design factors + structural + price covariates."""
    row = dict(inst.meta)
    row.update(descriptors(inst))
    row.update(price_desc)
    row.update({
        "instance": path.stem,
        "subset": subset,
        "path": str(path),   # relative to the data root; resolve as DATA / path
        "price_name": price_name,
        "price_regime": price_regime,
        "e_day": round(e_day(inst), 4),
        "sha256": sha,
    })
    return row
