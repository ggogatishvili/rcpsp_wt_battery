#!/usr/bin/env python3
"""
Stage 1 — build the price library and every instance file.

Deterministic: running this twice from a clean tree produces byte-identical
files and identical sha256 manifests. Re-running over an existing tree
regenerates only what is missing unless --force is given.

Outputs
    data/prices/manifest_prices.csv     one row per price series
    data/instances/{core,e3,e4}/*.txt   solver-ready instances
    data/manifest_instances.csv         one row per instance, all covariates
    data/generation_report.txt          human-readable summary + warnings
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design                                    # noqa: E402
from lib import prices as P                                  # noqa: E402
from lib import generate as G                                # noqa: E402
from lib.rcpsp_io import Instance                            # noqa: E402

REPO = ROOT.parent
ORIG = REPO / "instance_generator" / "instances_original"
RAW = REPO / "instance_generator"
import os
DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))


def log(msg: str, warnings: list | None = None) -> None:
    print(msg, flush=True)
    if warnings is not None and msg.startswith("WARNING"):
        warnings.append(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate existing files")
    args = ap.parse_args()

    warnings: list[str] = []
    DATA.mkdir(exist_ok=True)

    # ---- source year -----------------------------------------------------
    year_files = [RAW / "electricity_cost_eur_mwh_2025.csv"] + \
                 [RAW / f for f in design.EXTRA_PRICE_CSVS]
    year_files = [f for f in year_files if f.exists()]
    if not year_files:
        print(f"FATAL: no price CSV found under {RAW}", file=sys.stderr)
        return 1
    year = P.load_year_csv(year_files[0])
    ref_price = sum(year) / len(year)
    log(f"source year: {year_files[0].name}  {len(year)} hours  "
        f"mean {ref_price:.2f} EUR/MWh")
    if len(year_files) > 1:
        log(f"NOTE: {len(year_files)-1} extra price file(s) present but only the "
            f"first is used for window drawing; extend build_price_library to use them.")

    # ---- shops -----------------------------------------------------------
    # Core shops: full factorial over size x density x tightness x replicate,
    # at lambda = base. E4 reuses these structures at other lambda levels.
    core_shops: dict[str, Instance] = {}
    for p in design.SIZE_CLASSES:
        for rep in range(1, design.REPLICATES + 1):
            src = ORIG / f"{p}_{rep}.txt"
            if not src.exists():
                log(f"WARNING: missing source structure {src.name}, cell skipped", warnings)
                continue
            for dens in design.EI_DENSITY:
                for tight in design.DUE_TIGHTNESS:
                    inst = G.build_shop(src, p, rep, dens, tight,
                                        design.LAMBDA_BASE, ref_price)
                    core_shops[inst.meta["shop_id"]] = inst
    log(f"core shops: {len(core_shops)}")

    horizons = sorted({s.horizon for s in core_shops.values()})
    log(f"distinct horizons: {len(horizons)}  "
        f"min {min(horizons)}h ({min(horizons)//24}d)  "
        f"max {max(horizons)}h ({max(horizons)//24}d)")
    if max(horizons) > 0.5 * len(year):
        log(f"WARNING: longest horizon {max(horizons)}h exceeds half the source "
            f"year; spot windows will overlap heavily and are not independent draws",
            warnings)

    # ---- price library, per horizon --------------------------------------
    # Series are horizon-specific, so they are built lazily per distinct horizon.
    lib: dict[int, dict[str, P.Series]] = defaultdict(dict)
    for h in horizons:
        for name, spec in design.CONTRACTUAL.items():
            if spec["kind"] == "flat":
                s = P.flat_series(year, h)
            else:
                s = P.two_block_series(year, h, spec["peak_hours"], spec["peak_ratio"])
            lib[h][s.name] = s
        for regime in design.SPOT_REGIMES:
            for s in P.spot_windows(year, h, regime,
                                    design.CORE_DRAWS_PER_REGIME, "core"):
                lib[h][s.name] = s
    log(f"price series per horizon: {len(next(iter(lib.values())))}")

    def e3_spot_for(h: int) -> list[P.Series]:
        out = []
        for regime in design.SPOT_REGIMES:
            out += P.spot_windows(year, h, regime, design.E3_DRAWS_PER_REGIME, "core")
        return out

    def synth_for(h: int) -> list[P.Series]:
        out = []
        for sp in design.SYNTH_SPREADS:
            for nz in design.SYNTH_NOISE:
                for ng in design.SYNTH_NEG_SHARE:
                    for d in range(design.SYNTH_DRAWS):
                        out.append(P.synthetic_series(h, design.SYNTH_MEAN, sp,
                                                      nz, ng, d, "e3"))
        return out

    # ---- write instances -------------------------------------------------
    rows: list[dict] = []
    n_written = n_skipped = n_shared = 0
    # A (shop, series) pair is materialised exactly once. E3 reuses the shops of
    # the core set, so without this the contractual and core spot series would
    # be written twice under two subset directories, giving duplicate manifest
    # keys and double-counted runs.
    emitted: dict[tuple[str, str], dict] = {}

    def emit(inst: Instance, series: P.Series, sub: str) -> None:
        nonlocal n_written, n_skipped, n_shared
        key = (inst.meta["shop_id"], series.name)
        if key in emitted:
            row = emitted[key]
            tags = set(row["subset"].split(",")) | {sub}
            row["subset"] = ",".join(sorted(tags))
            n_shared += 1
            return
        out = DATA / "instances" / sub / f"{inst.meta['shop_id']}__{series.name}.txt"
        if out.exists() and not args.force:
            n_skipped += 1
        else:
            G.materialise(inst, series.values, out)
            n_written += 1
        sha = ""
        try:
            import hashlib
            sha = hashlib.sha256(out.read_bytes()).hexdigest()
        except OSError:
            pass
        # Store the path relative to the experiments root so the manifest stays
        # valid after the tree is copied to the compute server.
        row = G.instance_row(inst, series.name, series.regime,
                             series.descriptors(), out.relative_to(DATA), sha, sub)
        emitted[key] = row
        rows.append(row)

    # E0 / E1 / E2 / E5 : core regimes, plus the extra regimes E2 sweeps over
    core_regimes = sorted(set(design.CORE_REGIMES) | set(design.E2_REGIMES))
    for sid, inst in sorted(core_shops.items()):
        for name, s in sorted(lib[inst.horizon].items()):
            if s.regime in core_regimes or s.name in core_regimes:
                emit(inst, s, "core")

    # E3 : a stratified subset of shops crossed with the whole tariff library
    if design.ENABLED["E3"]:
        subset = _stratified_subset(core_shops, design._PROFILES[design.PROFILE]["e3_shops"])
        log(f"E3 shop subset: {len(subset)}")
        for sid in subset:
            inst = core_shops[sid]
            contractual = [s for s in lib[inst.horizon].values()
                           if s.regime in design.CONTRACTUAL]
            for s in contractual + e3_spot_for(inst.horizon) + synth_for(inst.horizon):
                emit(inst, s, "e3")

    # E4 : lambda sweep on the core shops, single regime
    if design.ENABLED["E4"]:
        for p in design.SIZE_CLASSES:
            for rep in range(1, design.REPLICATES + 1):
                src = ORIG / f"{p}_{rep}.txt"
                if not src.exists():
                    continue
                for dens in design.EI_DENSITY:
                    for tight in design.DUE_TIGHTNESS:
                        for lam in design.LAMBDA_LEVELS:
                            if lam == design.LAMBDA_BASE:
                                continue          # already generated as a core shop
                            inst = G.build_shop(src, p, rep, dens, tight, lam, ref_price)
                            pick = sorted(
                                (s for s in lib[inst.horizon].values()
                                 if s.regime == design.E4_REGIME
                                 or s.name == design.E4_REGIME),
                                key=lambda s: s.name)[:1]
                            for s in pick:      # lambda is the treatment; hold price fixed
                                emit(inst, s, "e4")

    # ---- manifests -------------------------------------------------------
    _write_csv(DATA / "manifest_instances.csv", rows)

    prow = []
    for h, d in sorted(lib.items()):
        for s in d.values():
            prow.append(dict(horizon=h, name=s.name, regime=s.regime,
                             **s.descriptors()))
    _write_csv(DATA / "prices" / "manifest_prices.csv", prow)

    # ---- report ----------------------------------------------------------
    rep_lines = [
        "instance generation report",
        f"  profile              {design.PROFILE}",
        f"  master seed          {design.MASTER_SEED}",
        f"  source year          {year_files[0].name} ({len(year)} h, mean {ref_price:.2f})",
        f"  core shops           {len(core_shops)}",
        f"  instances written    {n_written}",
        f"  instances reused     {n_skipped}",
        f"  shared across subsets{n_shared:>6d}",
        f"  total manifest rows  {len(rows)}",
        f"  distinct horizons    {len(horizons)} ({min(horizons)}..{max(horizons)} h)",
        f"  disk                 {_du(DATA / 'instances')/1e6:.1f} MB",
        "",
        "warnings:",
    ] + ([f"  {w}" for w in warnings] or ["  none"])
    (DATA / "generation_report.txt").write_text("\n".join(rep_lines) + "\n")
    print("\n".join(rep_lines))
    return 0


def _stratified_subset(shops: dict, k: int) -> list[str]:
    """Pick k shop ids spread evenly over the size x density x tightness grid."""
    from lib.rng import substream
    buckets = defaultdict(list)
    for sid, inst in shops.items():
        buckets[(inst.meta["size_class"], inst.meta["ei_density_level"],
                 inst.meta["due_tightness_level"])].append(sid)
    keys = sorted(buckets)
    rng = substream("e3subset")
    for key in keys:
        buckets[key].sort()
        rng.shuffle(buckets[key])
    out, i = [], 0
    while len(out) < k and any(buckets[key] for key in keys):
        key = keys[i % len(keys)]
        if buckets[key]:
            out.append(buckets[key].pop())
        i += 1
    return sorted(out)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    cols: list[str] = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {path.name}  ({len(rows)} rows)")


def _du(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


if __name__ == "__main__":
    raise SystemExit(main())
