#!/usr/bin/env python3
"""
Stage 1 — build the price library and every instance file for campaign v2.

Deterministic: running this twice from a clean tree produces byte-identical
files and identical sha256 manifests. Re-running over an existing tree
regenerates only what is missing unless --force is given.

WHAT CHANGED FROM v1. v1 built instances per experiment (subsets "core", "e3",
"e4") and each experiment then filtered the manifest by hand. v2 inverts that:
config/design.py declares POOLS (which shops exist) and per-experiment tariff
sets (which series each experiment needs), and this script materialises exactly
the union. The consequence worth having is that shops are SHARED across
experiments by construction, so M1, M2 and M4 are paired on identical shop
structures and a difference between two experiments is a difference between
their treatments rather than between their samples.

Outputs
    data/prices/manifest_prices.csv     one row per price series, per horizon
    data/instances/<pool>/*.txt         solver-ready instances
    data/manifest_instances.csv         one row per instance, all covariates
    data/generation_report.txt          human-readable summary + warnings
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design                                    # noqa: E402
from lib import prices as P                                  # noqa: E402
from lib import generate as G                                # noqa: E402
from lib.rcpsp_io import Instance                            # noqa: E402
from lib.rng import substream                                # noqa: E402

REPO = ROOT.parent
ORIG = REPO / "instance_generator" / "instances_original"
RAW = REPO / "instance_generator"
DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))


# ---------------------------------------------------------------------------
# shops
# ---------------------------------------------------------------------------

def build_pool(name: str, spec: dict, ref_price: float,
               warnings: list) -> dict[str, Instance]:
    """Full factorial over (size class, replicate, density, tightness, lambda)."""
    shops: dict[str, Instance] = {}
    for p in spec["size_classes"]:
        for rep in range(1, spec["reps"] + 1):
            src = ORIG / f"{p}_{rep}.txt"
            if not src.exists():
                warnings.append(f"WARNING: missing source structure {src.name}, "
                                f"pool {name} cell skipped")
                continue
            for dens in design.EI_DENSITY:
                for tight in design.DUE_TIGHTNESS:
                    for lam in spec["lambdas"]:
                        inst = G.build_shop(src, p, rep, dens, tight, lam, ref_price)
                        shops[inst.meta["shop_id"]] = inst
    return shops


def stratified_subset(shops: dict[str, Instance], k: int, key: str) -> list[str]:
    """Pick k shop ids spread evenly over the (size, density, tightness) grid.

    Round-robin over the strata rather than a plain random sample: with k = 24
    over 18 strata a random sample can easily leave a whole (density,
    tightness) combination empty, and M2's regression uses those as controls.
    """
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for sid, inst in shops.items():
        buckets[(inst.meta["size_class"], inst.meta["ei_density_level"],
                 inst.meta["due_tightness_level"])].append(sid)
    keys = sorted(buckets)
    rng = substream(f"subset|{key}")
    for kk in keys:
        buckets[kk].sort()
        rng.shuffle(buckets[kk])
    out, i = [], 0
    while len(out) < k and any(buckets[kk] for kk in keys):
        kk = keys[i % len(keys)]
        if buckets[kk]:
            out.append(buckets[kk].pop())
        i += 1
    return sorted(out)


# ---------------------------------------------------------------------------
# price library
# ---------------------------------------------------------------------------

def load_years(warnings: list) -> dict[str, tuple[list[float], dict]]:
    """Load every market-year declared in design.REAL_MARKET_YEARS.

    A missing file is a warning, not an error: the campaign must still run on
    the reference year alone. But the analysis is then not entitled to the
    between-market claim, so the omission is recorded loudly here, repeated in
    generation_report.txt, and re-checked by the preflight.
    """
    years: dict[str, tuple[list[float], dict]] = {}
    for key, spec in design.REAL_MARKET_YEARS.items():
        path = RAW / spec["file"]
        if not path.exists():
            warnings.append(
                f"WARNING: market-year '{key}' ({spec['file']}) not found - the "
                f"real-tariff arm of M2 will be missing this regime. Run "
                f"bin/00b_fetch_prices.py to build it.")
            continue
        try:
            years[key] = (P.load_year_csv(path), spec)
        except (OSError, ValueError) as exc:
            warnings.append(f"WARNING: market-year '{key}' unreadable ({exc})")
    return years


def build_library(horizon: int, ref_year: list[float],
                  years: dict[str, tuple[list[float], dict]]) -> dict[str, P.Series]:
    """Every series the campaign can attach at this horizon, keyed by name."""
    lib: dict[str, P.Series] = {}

    for name, spec in design.CONTRACTUAL.items():
        s = (P.flat_series(ref_year, horizon) if spec["kind"] == "flat"
             else P.two_block_series(ref_year, horizon, spec["peak_hours"],
                                     spec["peak_ratio"]))
        lib[s.name] = s

    for regime in design.SPOT_REGIMES:
        for s in P.spot_windows(ref_year, horizon, regime,
                                design.SPOT_WINDOWS_PER_REGIME, "v2"):
            lib[s.name] = s

    for key, (vals, spec) in years.items():
        for s in P.real_windows(vals, key, spec["market"], spec["year"],
                                spec["label"], horizon,
                                design.REAL_WINDOWS_PER_YEAR, "v2"):
            lib[s.name] = s

    for sp in design.SYNTH_SPREADS:
        for nz in design.SYNTH_NOISE:
            for ng in design.SYNTH_NEG_SHARE:
                for d in range(design.SYNTH_DRAWS):
                    s = P.synthetic_series(horizon, design.SYNTH_MEAN, sp, nz,
                                           ng, d, "v2")
                    lib[s.name] = s
    return lib


def resolve(lib: dict[str, P.Series], wanted: list[str]) -> list[P.Series]:
    """Expand a design tariff list into concrete series.

    An entry matches either a series name exactly ("flat", "tou2") or a regime
    ("spot_midvol", "real_cz2022"), in which case every window of that regime
    is returned. Unmatched entries are silently empty here and reported by the
    caller, because whether a missing regime is fatal depends on which one it
    is: a missing market-year degrades M2, a missing spot regime breaks M1.
    """
    out: list[P.Series] = []
    for w in wanted:
        if w in lib:
            out.append(lib[w])
            continue
        out += [s for s in lib.values() if s.regime == w]
    return sorted(out, key=lambda s: s.name)


def family_members(lib: dict[str, P.Series], families: list[str]) -> list[P.Series]:
    return sorted((s for s in lib.values() if s.family in families),
                  key=lambda s: s.name)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate existing files")
    args = ap.parse_args()

    warnings: list[str] = []
    DATA.mkdir(parents=True, exist_ok=True)

    ref_path = RAW / design.REFERENCE_YEAR_CSV
    if not ref_path.exists():
        print(f"FATAL: reference price year {ref_path} not found", file=sys.stderr)
        return 1
    ref_year = P.load_year_csv(ref_path)
    ref_price = sum(ref_year) / len(ref_year)
    print(f"reference year: {ref_path.name}  {len(ref_year)} h  "
          f"mean {ref_price:.2f} EUR/MWh", flush=True)

    years = load_years(warnings)
    print(f"market-years available: {sorted(years) or 'none beyond the reference'}")

    # ---- shops ------------------------------------------------------------
    pools = {name: build_pool(name, spec, ref_price, warnings)
             for name, spec in design.POOLS.items()}
    for name, shops in pools.items():
        print(f"pool {name:<7} {len(shops):5d} shops")

    all_shops: dict[str, Instance] = {}
    for shops in pools.values():
        all_shops.update(shops)
    horizons = sorted({s.horizon for s in all_shops.values()})
    print(f"distinct horizons: {len(horizons)}  "
          f"{min(horizons)}h ({min(horizons)//24}d) .. "
          f"{max(horizons)}h ({max(horizons)//24}d)")
    if max(horizons) > 0.5 * len(ref_year):
        warnings.append(
            f"WARNING: longest horizon {max(horizons)}h exceeds half the source "
            f"year; drawn windows overlap heavily and are not independent draws")

    # ---- price library, per horizon ---------------------------------------
    # Series are horizon-specific (a window is a slice of a year), so the whole
    # library is rebuilt per distinct horizon. That is why the design keeps the
    # number of distinct horizons small: it multiplies this cost directly.
    lib: dict[int, dict[str, P.Series]] = {
        h: build_library(h, ref_year, years) for h in horizons}
    print(f"price series per horizon: {len(lib[horizons[0]])}")

    # ---- what each experiment needs ---------------------------------------
    # (pool name, tariff selector). The runlist generator re-derives exactly the
    # same sets from the same design constants; if the two ever disagree, the
    # runlist references an instance that was never written and 02 fails loudly
    # rather than the campaign quietly losing cells.
    m2_ids = set(stratified_subset(pools["core"], design.M2_SHOPS, "m2"))

    def wanted_series(pool: str, sid: str, h: int) -> set[str]:
        L = lib[h]
        out: set[str] = set()
        if pool == "valid" and design.ENABLED["M0"]:
            out |= {s.name for s in resolve(L, design.M0_TARIFFS)}
        if pool == "core":
            if design.ENABLED["M1"]:
                out |= {s.name for s in resolve(L, design.M1_TARIFFS)}
                out |= {s.name for s in resolve(L, [design.M1B_TARIFF])}
            if design.ENABLED["M4"]:
                out |= {s.name for s in resolve(L, design.M4_TARIFFS)}
            if design.ENABLED["M2"] and sid in m2_ids:
                out |= {s.name for s in family_members(L, design.M2_TARIFF_FAMILIES)}
        if pool == "scale" and design.ENABLED["M3"]:
            out |= {s.name for s in resolve(L, design.M3_TARIFFS)}
        if pool == "lambda" and design.ENABLED["M5"]:
            out |= {s.name for s in resolve(L, design.M5_TARIFFS)}
        return out

    # ---- write instances --------------------------------------------------
    rows: list[dict] = []
    emitted: dict[tuple[str, str], dict] = {}
    n_written = n_skipped = n_shared = 0

    def emit(inst: Instance, series: P.Series, pool: str) -> None:
        nonlocal n_written, n_skipped, n_shared
        key = (inst.meta["shop_id"], series.name)
        if key in emitted:
            # The same (shop, series) pair reachable from two pools is written
            # once and tagged with both. Writing it twice would give duplicate
            # manifest keys and double-counted runs.
            row = emitted[key]
            row["subset"] = ",".join(sorted(set(row["subset"].split(",")) | {pool}))
            n_shared += 1
            return
        out = DATA / "instances" / pool / f"{inst.meta['shop_id']}__{series.name}.txt"
        if out.exists() and not args.force:
            n_skipped += 1
        else:
            G.materialise(inst, series.values, out)
            n_written += 1
        try:
            sha = hashlib.sha256(out.read_bytes()).hexdigest()
        except OSError:
            sha = ""
        row = G.instance_row(inst, series, out.relative_to(DATA), sha, pool)
        # M2 runs on a stratified subset of the core pool, and it must run on
        # the SAME subset for every tariff family. Without this flag the runlist
        # would pick up contractual instances for all core shops (they are
        # materialised for M1 anyway) but synthetic ones only for the subset,
        # and M2's headline comparison -- the same regression estimated on
        # synthetic and on real tariffs -- would be estimated on two different
        # samples of shops.
        row["m2_shop"] = "1" if inst.meta["shop_id"] in m2_ids else "0"
        emitted[key] = row
        rows.append(row)

    for pool in ("valid", "core", "scale", "lambda"):
        for sid, inst in sorted(pools[pool].items()):
            L = lib[inst.horizon]
            for name in sorted(wanted_series(pool, sid, inst.horizon)):
                emit(inst, L[name], pool)
        print(f"pool {pool:<7} materialised, running total {len(rows)} instances",
              flush=True)

    # ---- manifests --------------------------------------------------------
    _write_csv(DATA / "manifest_instances.csv", rows)
    _write_csv(DATA / "prices" / "manifest_prices.csv",
               [dict(horizon=h, name=s.name, regime=s.regime, family=s.family,
                     market=s.meta.get("market", ""), year=s.meta.get("year", ""),
                     label=s.meta.get("label", ""), **s.descriptors())
                for h, d in sorted(lib.items()) for s in sorted(d.values(),
                                                                key=lambda x: x.name)])

    # ---- coverage checks --------------------------------------------------
    # Cheap, and they catch the failure that costs the most: a design cell that
    # silently has no instances, discovered four days into the run.
    fam = defaultdict(int)
    for r in rows:
        fam[r["tariff_family"]] += 1
    for f in ("contractual", "spot", "synthetic"):
        if not fam.get(f):
            warnings.append(f"WARNING: no instances with tariff family '{f}'")
    if design.ENABLED["M2"] and not fam.get("real"):
        warnings.append("WARNING: M2 is enabled but no REAL market-year instances "
                        "exist. Its between-market arm cannot be estimated and its "
                        "synthetic-vs-real diagnostic - the campaign's answer to "
                        "v1's biggest weakness - will be vacuous.")

    realised = sorted(float(r["spread_intraday"]) for r in rows)
    if realised:
        lo_support = sum(1 for v in realised if v < 10.0)
        if lo_support < 0.02 * len(realised):
            warnings.append(
                f"WARNING: only {lo_support} of {len(realised)} instances have a "
                f"realised intra-day spread below 10 EUR/MWh. Any screening "
                f"threshold in that region is extrapolation - lower "
                f"design.SYNTH_NOISE before trusting it.")

    rep = [
        "instance generation report (campaign v2)",
        f"  profile              {design.PROFILE}",
        f"  master seed          {design.MASTER_SEED}",
        f"  reference year       {ref_path.name} ({len(ref_year)} h, mean {ref_price:.2f})",
        f"  market-years         {', '.join(sorted(years)) or 'none'}",
        *[f"  pool {n:<15s} {len(s):6d} shops" for n, s in pools.items()],
        f"  series per horizon   {len(lib[horizons[0]])}",
        f"  instances written    {n_written}",
        f"  instances reused     {n_skipped}",
        f"  shared across pools  {n_shared}",
        f"  total manifest rows  {len(rows)}",
        f"  distinct horizons    {len(horizons)} ({min(horizons)}..{max(horizons)} h)",
        f"  disk                 {_du(DATA / 'instances')/1e6:.1f} MB",
        "",
        "  instances by tariff family:",
        *[f"    {k:<14s} {v:7d}" for k, v in sorted(fam.items())],
        "",
        "warnings:",
    ] + ([f"  {w}" for w in warnings] or ["  none"])
    (DATA / "generation_report.txt").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))
    return 0


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
        w = csv.DictWriter(fh, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path.name}  ({len(rows)} rows)")


def _du(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


if __name__ == "__main__":
    raise SystemExit(main())
