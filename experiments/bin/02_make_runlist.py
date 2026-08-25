#!/usr/bin/env python3
"""
Stage 2 — expand the design into an explicit runlist, and check the budget
BEFORE any compute is spent.

Every row of runlist.csv is one solver invocation, fully specified. Nothing
downstream reinterprets the design: the driver reads argv from this file and
executes it. That makes the campaign auditable — you can diff two runlists,
count cells, and verify the factorial is balanced without running anything.

SOLVER CAPABILITY PROBING. Some cells need solver features that may not exist
in the binary on the compute server (`--states`, the machine-profile flags, the
battery flags). This script probes `solver --help`, marks unsupported cells as
blocked, writes them to runlist_blocked.csv with a reason, and excludes them
from runlist.csv. When the feature lands, re-run this script and the cells
appear. The alternative — running anyway — would produce a full set of
beautiful numbers measured at the compiled-in defaults, which is the single
most expensive failure mode this pipeline has.

BALANCE CHECKING. v2 adds a post-expansion audit: for every experiment the
script counts cells per factor level and refuses to proceed if a factor is
unbalanced, because an unbalanced factorial silently turns a main effect into
a composition effect. Override with --allow-unbalanced only with a reason.

Outputs
    data/runlist.csv          runnable invocations
    data/runlist_blocked.csv  design cells awaiting solver features
    data/budget_report.txt    estimated core-hours vs available
    data/balance_report.txt   cell counts per factor level, per experiment
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design, machines                          # noqa: E402

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))
DEFAULT_SOLVER = ROOT.parent / "build" / "rcpsp_wt_battery"

# Columns every row carries, in a fixed order, so two runlists diff cleanly.
COLUMNS = [
    "run_id", "experiment", "block", "shop_id", "instance", "instance_path", "method",
    "state_policy", "battery_ratio", "battery_arg", "seed", "time_limit",
    "size_class", "ei_density_level", "due_tightness_level", "lam",
    "price_name", "price_regime", "tariff_family", "price_market", "price_year",
    "price_label", "synth_spread", "synth_noise", "synth_neg",
    "machine_profile", "rho", "restart_level", "m1_subdesign", "argv",
]


def probe(solver: Path) -> set[str]:
    """Return the set of long options the solver advertises in --help."""
    try:
        launcher = ([sys.executable, str(solver)] if str(solver).endswith(".py")
                    else [str(solver)])
        out = subprocess.run(launcher + ["--help"], capture_output=True,
                             text=True, timeout=60, check=False)
        text = (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"WARNING: could not probe solver ({exc}); assuming the full v2 "
              f"feature set. If that is wrong, every machine-profile cell will "
              f"run at the compiled-in default and look perfectly valid.")
        return set(machines.REQUIRED_FLAGS) | {"--states", "--lambda",
                                               "--charging-efficiency", "--c-rate"}
    flags = set()
    for tok in text.replace(",", " ").split():
        if tok.startswith("--"):
            flags.add(tok.split("=")[0].strip("[]"))
    return flags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", type=Path, default=DEFAULT_SOLVER)
    ap.add_argument("--allow-over-budget", action="store_true")
    ap.add_argument("--allow-unbalanced", action="store_true")
    ap.add_argument("--experiments", default="",
                    help="comma list, e.g. M0,M1; default = design.ENABLED")
    args = ap.parse_args()

    man_path = DATA / "manifest_instances.csv"
    if not man_path.exists():
        print("FATAL: run 01_build_instances.py first", file=sys.stderr)
        return 1
    man = list(csv.DictReader(man_path.open()))
    if not man:
        print("FATAL: empty instance manifest", file=sys.stderr)
        return 1

    only = {e.strip() for e in args.experiments.split(",") if e.strip()}
    enabled = {k: (v and (not only or k in only)) for k, v in design.ENABLED.items()}

    flags = probe(args.solver)
    print(f"solver flags detected: {len(flags)}")

    runs: list[dict] = []
    blocked: list[dict] = []
    seen_ids: set[str] = set()

    launcher = ([sys.executable, str(args.solver)] if str(args.solver).endswith(".py")
                else [str(args.solver)])

    def add(exp: str, row: dict, method: str, ratio: float, *, block: str,
            state: str = design.STATE_BASELINE, seed: int | None = None,
            extra: list[str] | None = None, tag: str = "",
            tl_override: int | None = None, cols: dict | None = None) -> None:
        """Expand one design cell into one solver invocation.

        `block` names the SUB-DESIGN this cell belongs to. It is what makes the
        completeness audit meaningful: an experiment is often a union of
        several factorials (M1 is a 6x5x7 archetype cube plus a 9x3 orthogonal
        grid), and auditing their union would report a wild imbalance that is
        entirely by construction. Auditing each block separately asks the
        question that actually matters -- is THIS factorial complete.
        """
        extra = list(extra or [])

        # --- capability gates ------------------------------------------------
        spec = design.STATE_POLICIES[state]
        if spec["flag"] and spec["flag"].split("=")[0] not in flags:
            blocked.append({"experiment": exp, "instance": row["instance"],
                            "method": method, "detail": state,
                            "reason": f"solver lacks {spec['flag'].split('=')[0]}"})
            return
        missing = {f for f in extra if f.startswith("--")} - flags
        if missing:
            blocked.append({"experiment": exp, "instance": row["instance"],
                            "method": method, "detail": tag,
                            "reason": f"solver lacks {sorted(missing)}"})
            return

        b = max(0, round(ratio * float(row["e_day"])))
        tl = design.TL[method] if tl_override is None else tl_override
        full_tag = tag
        if tl != design.TL[method]:
            full_tag = f"{tag}__tl{tl}" if tag else f"tl{tl}"

        argv = launcher + [
            "-i", str((DATA / row["path"]).resolve()),
            "-m", method,
            "-b", str(b),
            "--tl", str(tl),
            "--thl", str(design.THREADS_PER_RUN),
            "--ml", str(design.MEM_LIMIT_GB),
        ]
        if seed is not None:
            argv += ["-s", str(seed)]
        if spec["flag"]:
            argv.append(spec["flag"])
        argv += extra

        rid = "__".join([exp, row["instance"], method, state, f"b{ratio:g}",
                         f"s{seed if seed is not None else 0}"]
                        + ([full_tag] if full_tag else []))
        if rid in seen_ids:
            # Two design cells collapsing onto one run_id would silently halve a
            # factor. Loud, immediate, and always a design bug.
            raise SystemExit(f"FATAL: duplicate run_id {rid}. Two cells of the "
                             f"design differ only in something not encoded in "
                             f"the id; add it to the tag.")
        seen_ids.add(rid)

        run = {c: "" for c in COLUMNS}
        run.update({
            "run_id": rid, "experiment": exp, "instance": row["instance"],
            "instance_path": row["path"], "method": method, "state_policy": state,
            "battery_ratio": ratio, "battery_arg": b,
            "seed": seed if seed is not None else "", "time_limit": tl,
            "block": block, "shop_id": row.get("shop_id", ""),
            "argv": "\t".join(argv),
        })
        for c in ("size_class", "ei_density_level", "due_tightness_level", "lam",
                  "price_name", "price_regime", "tariff_family", "price_market",
                  "price_year", "price_label", "synth_spread", "synth_noise",
                  "synth_neg"):
            run[c] = row.get(c, "")
        run.update(cols or {})
        runs.append(run)

    # ---- helpers ---------------------------------------------------------
    def pool(name: str) -> list[dict]:
        return [r for r in man if name in r["subset"].split(",")]

    def by_tariff(rows: list[dict], wanted: list[str]) -> list[dict]:
        """Rows whose series name or regime is in `wanted`."""
        w = set(wanted)
        return [r for r in rows if r["price_name"] in w or r["price_regime"] in w]

    def by_family(rows: list[dict], families: list[str]) -> list[dict]:
        f = set(families)
        return [r for r in rows if r["tariff_family"] in f]

    def stratified_prefix(rows: list[dict], k: int,
                          strata: tuple[str, ...]) -> list[str]:
        """Deterministic, balanced subsample of shop ids.

        Round-robin over the strata rather than a sorted prefix or a random
        draw. A sorted prefix would take all of one density level before any of
        the next; a random draw would need a seed and could still leave a
        stratum empty. Round-robin over sorted buckets is reproducible, balanced
        on exactly the factors M0 breaks its results down by, and stable when
        the pool grows -- adding shops never reshuffles the ones already picked.
        """
        buckets: dict[tuple, list[str]] = defaultdict(list)
        for r in rows:
            buckets[tuple(r[f] for f in strata)].append(r["shop_id"])
        keys = sorted(buckets)
        for kk in keys:
            buckets[kk] = sorted(set(buckets[kk]))
        out, i = [], 0
        while len(out) < k and any(buckets[kk] for kk in keys):
            kk = keys[i % len(keys)]
            if buckets[kk]:
                out.append(buckets[kk].pop(0))
            i += 1
        return out

    def seeds_for(method: str, exp: str, k: int | None = None) -> list[int | None]:
        """Seeds for one (method, experiment) pair.

        Deterministic methods get a single unseeded run: passing them a seed
        would create identical runs under different ids and inflate every count
        they appear in.
        """
        if method in design.DETERMINISTIC_METHODS:
            return [None]
        pool = design.seeds(exp)
        return list(pool[:k] if k else pool)

    valid, core, scale, lam_pool = (pool("valid"), pool("core"),
                                    pool("scale"), pool("lambda"))
    print(f"manifest pools: valid={len(valid)} core={len(core)} "
          f"scale={len(scale)} lambda={len(lam_pool)}")

    baseline_machine = design.machine_args(machines.BASELINE_ARCHETYPE)
    baseline_cols = {"machine_profile": machines.BASELINE_ARCHETYPE}

    # =====================================================================
    # MR — how noisy is the GA, and how many seeds does the campaign need?
    # =====================================================================
    # Runs FIRST and gates the seed counts of everything below it. Two
    # questions, and the second is the one a dispersion number alone cannot
    # answer:
    #
    #   (1) How large is sigma_seed at the campaign's time budget? Every
    #       managerial number in M1-M5 is a paired difference, and in a paired
    #       difference the between-instance variability cancels while the seed
    #       noise does not: Var(d) = sigma_delta^2 + 2 sigma_seed^2 / k. With a
    #       small k that second term is not a correction to the standard error,
    #       it can be the standard error.
    #
    #   (2) Is sigma_seed the SAME across treatments? If the GA is noisier with
    #       a battery than without, or noisier on a machine that cannot switch
    #       off, a difference of means between those cells is partly a
    #       difference of dispersions. Hence the crossing with battery level and
    #       with the two archetype corners plus the anchor, rather than one cell
    #       repeated many times.
    #
    # The flat tariff is in here deliberately: under a constant price no
    # configuration can create value, so whatever spread the GA shows there is
    # pure algorithmic noise with no signal mixed in. It is the cleanest
    # estimate of sigma_seed the campaign can obtain.
    if enabled.get("MR"):
        mr_rows = by_tariff(core, design.MR_TARIFFS)
        mr_shops = set(sorted({r["shop_id"] for r in mr_rows})[:design.MR_SHOPS])
        for r in mr_rows:
            if r["shop_id"] not in mr_shops:
                continue
            for arch in design.MR_ARCHETYPES:
                margs = design.machine_args(arch)
                for ratio in design.MR_BATTERY_RATIOS:
                    for s in seeds_for("GA", "MR"):
                        add("MR", r, "GA", ratio, seed=s, extra=margs,
                            tag=f"m{arch}", block="MR.dispersion",
                            cols={"machine_profile": arch,
                                  "rho": machines.ARCHETYPES[arch]["rho"]})

    # =====================================================================
    # M0 — validation: is the GA close enough to the compact MILP to be used
    #      as the measurement device for everything else?
    # =====================================================================
    # Structure of the argument, in order:
    #   (a) GA vs MILP on sizes where the MILP can prove something;
    #   (b) the same comparison WITH and WITHOUT storage, because an accuracy
    #       that degrades when the battery is present would make every measured
    #       storage benefit partly an algorithmic artefact;
    #   (c) an anytime profile, so the conclusion is not an artefact of one
    #       arbitrary time budget.
    if enabled["M0"]:
        rows = by_tariff(valid, design.M0_TARIFFS)
        # Which shops the compact MILP is offered, decided here and once. The
        # prove classes take every shop; the probe class takes a deterministic
        # stratified prefix. See design.MILP_PROVE_CLASSES for why the two
        # populations are kept separate rather than pooled behind a size cap.
        milp_shops = {r["shop_id"] for r in rows
                      if int(r["size_class"]) in design.MILP_PROVE_CLASSES}
        probe_pool = [r for r in rows
                      if int(r["size_class"]) in design.MILP_PROBE_CLASSES]
        milp_shops |= set(stratified_prefix(
            probe_pool, design.MILP_PROBE_SHOPS,
            ("ei_density_level", "due_tightness_level")))
        for r in rows:
            for ratio in design.M0_BATTERY_RATIOS:
                for method in design.M0_METHODS:
                    if method == "MILP" and r["shop_id"] not in milp_shops:
                        continue
                    for s in seeds_for(method, "M0"):
                        blk = f"M0.main.{method}"
                        if method == "MILP":
                            blk += (".prove"
                                    if int(r["size_class"]) in design.MILP_PROVE_CLASSES
                                    else ".probe")
                        add("M0", r, method, ratio, seed=s, extra=baseline_machine,
                            cols=baseline_cols, block=blk)

        # (c) anytime, on a subset: the full pool at three budgets would cost
        # more than the rest of M0 put together and answer a narrower question.
        # Select by SHOP, not by instance. Selecting instances would take a
        # prefix of a name-sorted list that cuts across shops, leaving one shop
        # with fewer tariff windows than the others -- a hole the completeness
        # audit catches, and exactly the kind of ragged cell that makes a paired
        # anytime comparison silently drop runs.
        anytime_rows = by_tariff(rows, [design.M0_ANYTIME_TARIFF])
        keep_shops = set(sorted({r["shop_id"] for r in anytime_rows})
                         [:design.M0_ANYTIME_SHOPS])
        for r in anytime_rows:
            if r["shop_id"] not in keep_shops:
                continue
            for ratio in design.M0_BATTERY_RATIOS:
                for tl in design.TL_PROFILE_EXTRA:
                    for s in seeds_for("GA", "M0", design.M0_ANYTIME_SEEDS):
                        add("M0", r, "GA", ratio, seed=s, extra=baseline_machine,
                            tl_override=tl, cols=baseline_cols, block="M0.anytime")

    # =====================================================================
    # M1 — the ROI cube: capacity x tariff x machine, fully crossed.
    # =====================================================================
    # Fully crossed because the paper's claim is about a return that depends on
    # all three at once. Three separate one-factor sweeps can report three main
    # effects and no interaction, and "the battery pays off unless restarting is
    # cheap and the tariff is flat" is an interaction statement.
    if enabled["M1"]:
        rows = by_tariff(core, design.M1_TARIFFS)
        for r in rows:
            for arch in design.M1_ARCHETYPES:
                margs = design.machine_args(arch)
                for ratio in design.M1_BATTERY_RATIOS:
                    for s in seeds_for("GA", "M1"):
                        add("M1", r, "GA", ratio, seed=s, extra=margs,
                            tag=f"m{arch}", block="M1.cube",
                            cols={"machine_profile": arch,
                                  "rho": machines.ARCHETYPES[arch]["rho"],
                                  "m1_subdesign": "cube"})

        # M1b — the orthogonal (rho, restart) surface. The archetypes above are
        # recognisable but not orthogonal, so their main effects are not
        # separable; this grid is orthogonal but not recognisable. The paper
        # needs the surface to establish the mechanism and the archetypes to
        # name it, which is why both exist and neither replaces the other.
        grid_rows = by_tariff(core, [design.M1B_TARIFF])
        for r in grid_rows:
            for rho in machines.RHO_LEVELS:
                for restart in machines.RESTART_LEVELS:
                    margs = design.grid_machine_args(rho, restart)
                    for ratio in design.M1B_BATTERY_RATIOS:
                        for s in seeds_for("GA", "M1"):
                            add("M1", r, "GA", ratio, seed=s, extra=margs,
                                tag=f"g{rho:g}_{restart}", block="M1.grid",
                                cols={"machine_profile": f"grid_r{rho:g}_{restart}",
                                      "rho": rho, "restart_level": restart,
                                      "m1_subdesign": "grid"})

    # =====================================================================
    # M2 — price volatility: how much does the return depend on the shape of
    #      the tariff, and is that relationship real or an artefact?
    # =====================================================================
    # The synthetic family identifies the shape of the response (spread, noise
    # and negative-hour share move orthogonally, which they never do in real
    # data). The real market-years carry external validity. The analysis
    # estimates the SAME regression on each and reports both, because v1
    # measured +0.554 (se 0.026) synthetic against -0.061 (se 0.108) real and
    # reported only the pooled fit.
    if enabled["M2"]:
        rows = [r for r in by_family(core, design.M2_TARIFF_FAMILIES)
                if r.get("m2_shop") == "1"]
        for r in rows:
            for ratio in design.M2_BATTERY_RATIOS:
                for s in seeds_for("GA", "M2"):
                    add("M2", r, "GA", ratio, seed=s, extra=baseline_machine,
                        cols=baseline_cols,
                        block=f"M2.{r['tariff_family']}")

    # =====================================================================
    # M3 — scaling: what changes as the number of tasks grows?
    # =====================================================================
    # Two questions in one experiment, and they must not be conflated:
    #   does the SOLVER hold up (runtime, gap to best known), and does the
    #   MANAGERIAL conclusion hold up (is storage worth as much at n = 512 as
    #   at n = 32)? The second is confounded by construction — the horizon is
    #   derived from a makespan lower bound, so bigger instances also see more
    #   price cycles — which is why every M3 quantity is reported per horizon
    #   day and the analysis prints the realised n-vs-horizon correlation.
    if enabled["M3"]:
        rows = by_tariff(scale, design.M3_TARIFFS)
        for r in rows:
            for ratio in design.M3_BATTERY_RATIOS:
                for s in seeds_for("GA", "M3"):
                    add("M3", r, "GA", ratio, seed=s, extra=baseline_machine,
                        cols=baseline_cols, block="M3.scale")

    # =====================================================================
    # M4 — substitution: are machine states and storage substitutes?
    # =====================================================================
    # GA-only by construction: --states is honoured by the SPACES graph, and
    # config.cpp refuses the flag for the compact MILP rather than silently
    # ignoring it. The ladder is crossed with the full battery ladder rather
    # than one "installed" level, so the answer can be stated at the capacity a
    # plant would actually buy and not only at the saturating one — the exact
    # gap that forced v1 to caveat its own headline.
    if enabled["M4"]:
        rows = by_tariff(core, design.M4_TARIFFS)
        for r in rows:
            for state in design.STATE_POLICIES:
                for ratio in design.M4_BATTERY_RATIOS:
                    for method in design.STATE_METHODS:
                        for s in seeds_for(method, "M4"):
                            add("M4", r, method, ratio, state=state, seed=s,
                                extra=baseline_machine, cols=baseline_cols,
                                block=f"M4.{method}")

    # =====================================================================
    # M5 — the service-energy frontier.
    # =====================================================================
    # lambda is baked into the instance file (it scales every task weight at
    # generation time), so these are different shops rather than a solver flag.
    # That is why the lambda pool exists and why M5 does not reuse the core one.
    if enabled["M5"]:
        rows = by_tariff(lam_pool, design.M5_TARIFFS)
        for r in rows:
            for ratio in design.M5_BATTERY_RATIOS:
                for s in seeds_for("GA", "M5"):
                    add("M5", r, "GA", ratio, seed=s, extra=baseline_machine,
                        cols=baseline_cols, block="M5.frontier")

    if not runs:
        print("FATAL: the design expanded to zero runs. Check ENABLED, the "
              "profile, and that 01_build_instances.py wrote the pools you "
              "expect (data/generation_report.txt).", file=sys.stderr)
        return 1

    # ---- balance audit ----------------------------------------------------
    balance, imbalanced = _balance(runs)
    (DATA / "balance_report.txt").write_text(balance + "\n")
    print(balance)

    # ---- budget -----------------------------------------------------------
    def cost_of(rs) -> float:
        return sum(int(r["time_limit"]) * design.EST_TIME_FRACTION[r["method"]]
                   for r in rs) / 3600.0

    # A run that produced a solution has both {rid}.json and {rid}.meta.json; a
    # failed one has only the meta file. Completion is therefore readable from
    # filenames alone, without opening tens of thousands of files.
    done = {p.name[:-5] for p in (DATA / "results").glob("*.json")
            if not p.name.endswith(".meta.json")}
    todo = [r for r in runs if r["run_id"] not in done]

    core_h, todo_h = cost_of(runs), cost_of(todo)
    avail_h = design.WALL_CLOCK_BUDGET_H * design.N_WORKERS
    by_exp = Counter(r["experiment"] for r in runs)
    by_meth = Counter(r["method"] for r in runs)

    lines = [
        "budget report (campaign v2)",
        f"  profile            {design.PROFILE}",
        f"  runnable runs      {len(runs)}",
        f"  blocked cells      {len(blocked)}",
        f"  distinct instances {len({r['instance'] for r in runs})}",
        "",
        "  runs per experiment:",
        *[f"    {k:5s} {v:9d}   {cost_of([r for r in runs if r['experiment']==k]):9.1f} core-h"
          for k, v in sorted(by_exp.items())],
        "  runs per method:",
        *[f"    {k:5s} {v:9d}" for k, v in sorted(by_meth.items())],
        "",
        f"  whole campaign     {core_h:10.1f} core-h  "
        f"({core_h/design.N_WORKERS:7.1f} h wall, {core_h/design.N_WORKERS/24:5.2f} days)",
        f"  already complete   {len(done):10d} runs",
        f"  REMAINING          {len(todo):10d} runs = {todo_h:.1f} core-h  "
        f"({todo_h/design.N_WORKERS:.1f} h wall, {todo_h/design.N_WORKERS/24:.2f} days)",
        f"  available          {avail_h:10.1f} core-h  "
        f"({design.N_WORKERS} workers x {design.WALL_CLOCK_BUDGET_H} h)",
        f"  utilisation        {100*todo_h/avail_h:10.1f} %  of remaining work",
    ]
    if blocked:
        lines += ["", "  blocked cells by reason:"]
        for reason, k in Counter(b["reason"] for b in blocked).most_common():
            lines.append(f"    {k:8d}  {reason}")
    report = "\n".join(lines)
    (DATA / "budget_report.txt").write_text(report + "\n")
    print(report)

    if imbalanced and not args.allow_unbalanced:
        print("\nFATAL: at least one sub-design block is materially incomplete "
              "(see data/balance_report.txt). Holes make every paired comparison "
              "crossing them drop cells listwise, at a different sample size per "
              "contrast. Fix the design, or pass --allow-unbalanced and record "
              "the reason in PREREGISTRATION.md.", file=sys.stderr)
        return 3
    if todo_h > avail_h and not args.allow_over_budget:
        print("\nFATAL: remaining cost exceeds the budget. Lower PROFILE in "
              "config/design.py, trim a factor, or pass --allow-over-budget.",
              file=sys.stderr)
        return 2

    _write(DATA / "runlist.csv", runs)
    _write(DATA / "runlist_blocked.csv", blocked)
    return 0


def _balance(runs: list[dict]) -> tuple[str, bool]:
    """Completeness audit, per sub-design block.

    WHAT IT ASKS, AND WHY THAT QUESTION. The naive check is "does every level of
    every factor appear equally often". That question is wrong here, and
    confidently so: an experiment is usually a union of several factorials
    (M1 = a 6x5x7 archetype cube plus a 9x3 orthogonal grid; M2 = three tariff
    families of deliberately different sizes), and their union is unbalanced by
    construction. Auditing the union would fire on every campaign and be
    ignored within a week, which is worse than not auditing at all.

    The question that matters is whether each block is a COMPLETE factorial:
    for the factors that actually vary inside the block, does every combination
    exist for every shop? A missing combination is not a cosmetic imbalance --
    every paired comparison in the analysis drops incomplete cells listwise, so
    a hole silently shrinks the sample of whichever contrast crosses it, and
    does so differently for different contrasts.

    The unit crossed against the factors is the SHOP, not the instance. A shop
    paired with a tariff *is* an instance, so treating instances as the unit
    would make the tariff factor look perfectly confounded with the unit and
    the audit vacuous.
    """
    factors = ["battery_ratio", "price_name", "machine_profile", "state_policy",
               "time_limit", "method", "seed", "lam"]
    out = ["completeness audit (campaign v2)",
           "",
           "  A block is complete when runs == shops x prod(levels of every",
           "  varying factor). Anything below 100 % has holes, and the missing",
           "  combinations are listed so you can tell a design decision from a bug.",
           ""]
    bad = False
    for block in sorted({r["block"] for r in runs}):
        rs = [r for r in runs if r["block"] == block]
        shops = sorted({r["shop_id"] for r in rs})
        levels = {}
        for f in factors:
            vals = sorted({str(r[f]) for r in rs if str(r[f]) != ""})
            if len(vals) <= 1:
                continue
            # A factor that is CONSTANT within every shop is an attribute of the
            # shop, not a crossed factor -- lambda is the case that matters here,
            # since it is baked into the instance file at generation time and so
            # appears in the shop id. Counting it as crossed would multiply the
            # expected cell count by 7 and report a complete design as 14 % full.
            per_shop = defaultdict(set)
            for r in rs:
                per_shop[r["shop_id"]].add(str(r[f]))
            if all(len(v) <= 1 for v in per_shop.values()):
                continue
            levels[f] = vals
        expected = len(shops)
        for vals in levels.values():
            expected *= len(vals)
        pct = 100.0 * len(rs) / expected if expected else float("nan")
        tag = ""
        if expected and len(rs) != expected:
            tag = "   <-- INCOMPLETE"
            # Below 90 % something structural is missing rather than one
            # unlucky cell, and that is worth stopping for.
            if pct < 90.0:
                bad = True
        out.append(f"--- {block}")
        out.append(f"    shops {len(shops):5d}   runs {len(rs):7d}   "
                   f"expected {expected:7d}   complete {pct:6.1f} %{tag}")
        for f, vals in sorted(levels.items()):
            shown = ", ".join(vals[:6]) + (" ..." if len(vals) > 6 else "")
            out.append(f"      {f:<16s} {len(vals):3d} levels   {shown}")
        # A single factor whose levels are unevenly represented, WITHIN a block,
        # is the residual case the completeness ratio can hide (two holes that
        # happen to cancel). Cheap to check, so check it.
        for f, vals in sorted(levels.items()):
            c = Counter(str(r[f]) for r in rs)
            lo, hi = min(c.values()), max(c.values())
            if lo != hi:
                out.append(f"      {f:<16s} ragged: min {lo}, max {hi}")
                if hi > 2 * lo:
                    bad = True
        out.append("")
    if bad:
        out.append("At least one block is materially incomplete. See the FATAL "
                   "note below.")
    return "\n".join(out), bad


def _write(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        print(f"wrote {path.name} (empty)")
        return
    cols: list[str] = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path.name} ({len(rows)} rows)")


if __name__ == "__main__":
    raise SystemExit(main())
