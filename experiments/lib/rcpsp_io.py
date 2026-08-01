"""
Reading and writing the solver's instance format.

Format (matches src/instance.cpp Instance::from exactly):

    line 1        : n m
    line 2        : Q_0 Q_1 ... Q_{m-1}
    lines 3..n+2  : p_j  r_j^0 ... r_j^{m-1}  k_j  s_1 ... s_{k_j}  rel_j  due_j  w_j
    line n+3      : c_0 c_1 ... c_{h-1}

Successors are 0-indexed task ids. Resource 0 is the energy-intensive machine
(capacity 1 in every PSPLIB-derived structure, so EI tasks are serialised).

The writer is deliberately strict: it re-parses whatever it wrote and compares
against the in-memory object, so a malformed instance cannot reach the cluster.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path


@dataclass
class Task:
    duration: int
    resources: list[int]
    successors: list[int]
    release: int = 0
    due: int = 0
    weight: float = 0.0

    @property
    def is_ei(self) -> bool:
        return self.resources[0] > 0


@dataclass
class Instance:
    capacities: list[int]
    tasks: list[Task]
    prices: list[float]
    meta: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.tasks)

    @property
    def m(self) -> int:
        return len(self.capacities)

    @property
    def horizon(self) -> int:
        return len(self.prices)

    @property
    def ei_ids(self) -> list[int]:
        return [i for i, t in enumerate(self.tasks) if t.is_ei]


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def read_original(path: Path) -> Instance:
    """Read a raw PSPLIB-derived file (no release/due/weight columns)."""
    rows = [ln.split() for ln in Path(path).read_text().splitlines() if ln.strip()]
    n, m = int(rows[0][0]), int(rows[0][1])
    caps = [int(v) for v in rows[1][:m]]
    tasks = []
    for i in range(n):
        p = [int(v) for v in rows[i + 2]]
        dur = p[0]
        res = p[1:1 + m]
        k = p[1 + m]
        succ = p[2 + m:2 + m + k]
        tasks.append(Task(duration=dur, resources=res, successors=succ))
    prices = [float(v) for v in rows[2 + n]] if len(rows) > 2 + n else []
    _validate_structure(n, m, tasks)
    return Instance(capacities=caps, tasks=tasks, prices=prices)


def read_extended(path: Path) -> Instance:
    """Read a generated file (with release/due/weight columns)."""
    rows = [ln.split() for ln in Path(path).read_text().splitlines() if ln.strip()]
    n, m = int(rows[0][0]), int(rows[0][1])
    caps = [int(v) for v in rows[1][:m]]
    tasks = []
    for i in range(n):
        p = rows[i + 2]
        dur = int(p[0])
        res = [int(v) for v in p[1:1 + m]]
        k = int(p[1 + m])
        succ = [int(v) for v in p[2 + m:2 + m + k]]
        rest = p[2 + m + k:]
        if len(rest) != 3:
            raise ValueError(f"{path}: task {i} has {len(rest)} trailing fields, expected 3")
        tasks.append(Task(dur, res, succ, int(rest[0]), int(rest[1]), float(rest[2])))
    prices = [float(v) for v in rows[2 + n]]
    _validate_structure(n, m, tasks)
    return Instance(capacities=caps, tasks=tasks, prices=prices)


def _validate_structure(n: int, m: int, tasks: list[Task]) -> None:
    if len(tasks) != n:
        raise ValueError(f"declared {n} tasks, parsed {len(tasks)}")
    for i, t in enumerate(tasks):
        if len(t.resources) != m:
            raise ValueError(f"task {i}: {len(t.resources)} resource entries, expected {m}")
        if t.duration <= 0:
            raise ValueError(f"task {i}: non-positive duration {t.duration}")
        for s in t.successors:
            if not (0 <= s < n):
                raise ValueError(f"task {i}: successor {s} out of range [0,{n})")
            if s == i:
                raise ValueError(f"task {i}: self-loop")


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def _format(inst: Instance) -> str:
    out = [f"{inst.n} {inst.m}", " ".join(str(c) for c in inst.capacities)]
    for t in inst.tasks:
        parts = ([str(t.duration)] + [str(r) for r in t.resources]
                 + [str(len(t.successors))] + [str(s) for s in t.successors]
                 + [str(int(t.release)), str(int(t.due)), f"{t.weight:.4f}"])
        out.append(" ".join(parts))
    out.append(" ".join(f"{c:.4f}" for c in inst.prices))
    return "\n".join(out) + "\n"


def write_instance(path: Path, inst: Instance, verify: bool = True) -> str:
    """Write and (by default) re-parse to confirm round-trip fidelity.

    Returns the sha256 of the written bytes, for the manifest.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _format(inst)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)                      # atomic: no half-written instances

    if verify:
        back = read_extended(path)
        if back.n != inst.n or back.m != inst.m:
            raise AssertionError(f"{path}: shape round-trip failed")
        if back.capacities != inst.capacities:
            raise AssertionError(f"{path}: capacities round-trip failed")
        if back.horizon != inst.horizon:
            raise AssertionError(f"{path}: horizon round-trip failed")
        for i, (a, b) in enumerate(zip(inst.tasks, back.tasks)):
            if (a.duration, a.resources, a.successors, int(a.release), int(a.due)) != \
               (b.duration, b.resources, b.successors, int(b.release), int(b.due)):
                raise AssertionError(f"{path}: task {i} round-trip failed")
        if max(abs(x - y) for x, y in zip(inst.prices, back.prices)) > 1e-6:
            raise AssertionError(f"{path}: prices round-trip failed")

    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# structural analysis (used for horizon derivation and E5 covariates)
# ---------------------------------------------------------------------------

def topological_order(inst: Instance) -> list[int]:
    n = inst.n
    indeg = [0] * n
    for t in inst.tasks:
        for s in t.successors:
            indeg[s] += 1
    stack = [i for i in range(n) if indeg[i] == 0]
    order = []
    while stack:
        i = stack.pop()
        order.append(i)
        for s in inst.tasks[i].successors:
            indeg[s] -= 1
            if indeg[s] == 0:
                stack.append(s)
    if len(order) != n:
        raise ValueError("precedence graph contains a cycle")
    return order


def earliest_starts(inst: Instance) -> list[int]:
    """Longest-path earliest start times, ignoring resource constraints."""
    es = [0] * inst.n
    for i in topological_order(inst):
        for s in inst.tasks[i].successors:
            es[s] = max(es[s], es[i] + inst.tasks[i].duration)
    return es


def critical_path_length(inst: Instance) -> int:
    es = earliest_starts(inst)
    return max(es[i] + inst.tasks[i].duration for i in range(inst.n))


def makespan_lower_bound(inst: Instance) -> int:
    """max(critical path, EI serialisation bound, per-resource area bound).

    Resource 0 has capacity 1 in every PSPLIB structure used here, so every EI
    task is serialised and the sum of their durations is a valid lower bound.
    This is what makes the horizon safe once EI density is increased.
    """
    lb = critical_path_length(inst)
    lb = max(lb, sum(inst.tasks[i].duration for i in inst.ei_ids))
    for r in range(inst.m):
        cap = inst.capacities[r]
        if cap <= 0:
            continue
        area = sum(t.duration * t.resources[r] for t in inst.tasks)
        lb = max(lb, -(-area // cap))      # ceil division
    return int(lb)


def order_strength(inst: Instance) -> float:
    """Fraction of task pairs that are precedence-related (transitive closure).

    O(n*m/64) via bitsets; fine for n up to a few thousand.
    """
    n = inst.n
    reach = [0] * n
    for i in reversed(topological_order(inst)):
        acc = 0
        for s in inst.tasks[i].successors:
            acc |= reach[s] | (1 << s)
        reach[i] = acc
    total = sum(bin(r).count("1") for r in reach)
    denom = n * (n - 1) / 2
    return total / denom if denom else 0.0


def resource_strength(inst: Instance) -> float:
    """Mean over resources of (capacity / peak simultaneous demand at ES times).

    Lower means tighter. Uses the resource-unconstrained earliest-start
    schedule as the reference profile, following the usual RCPSP convention.
    """
    es = earliest_starts(inst)
    h = max(es[i] + inst.tasks[i].duration for i in range(inst.n))
    vals = []
    for r in range(inst.m):
        prof = [0] * (h + 1)
        for i, t in enumerate(inst.tasks):
            if t.resources[r]:
                for u in range(es[i], es[i] + t.duration):
                    prof[u] += t.resources[r]
        peak = max(prof) if prof else 0
        if peak > 0:
            vals.append(inst.capacities[r] / peak)
    return sum(vals) / len(vals) if vals else float("nan")


def descriptors(inst: Instance) -> dict:
    """Structural covariates recorded for every instance (E5 regression)."""
    ei = inst.ei_ids
    tot_dur = sum(t.duration for t in inst.tasks)
    return dict(
        n=inst.n,
        m=inst.m,
        horizon=inst.horizon,
        horizon_days=inst.horizon / 24.0,
        n_ei=len(ei),
        ei_density=len(ei) / inst.n,
        ei_duration_share=(sum(inst.tasks[i].duration for i in ei) / tot_dur) if tot_dur else 0.0,
        critical_path=critical_path_length(inst),
        makespan_lb=makespan_lower_bound(inst),
        order_strength=round(order_strength(inst), 6),
        resource_strength=round(resource_strength(inst), 6),
        mean_due_slack=round(
            sum(t.due - t.release - t.duration for t in inst.tasks) / inst.n, 4),
        mean_weight=round(sum(t.weight for t in inst.tasks) / inst.n, 6),
    )
