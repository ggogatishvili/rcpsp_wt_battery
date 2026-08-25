#!/usr/bin/env python3
"""
Stage 2b — the design contract: derive every count from the design constants,
INDEPENDENTLY of how the runlist was built, and refuse to agree with itself
unless the two match.

WHY THIS EXISTS.

`02_make_runlist.py` expands the design by looping over factors; this script
predicts the same counts by multiplying cardinalities. The two share nothing
but `config/design.py` and the instance manifest, so if a factor is silently
dropped, double-counted, or expanded over regimes where the prose says series,
the two numbers diverge and this script fails. One arithmetic path checking
another is worth more than either path being carefully reviewed.

The failure this is built to catch is a real one and it already happened. The
campaign document described MR as "12 shops x 2 tariffs x 3 capacities x
3 archetypes x 12 seeds" = 2,592 runs, while the runlist contained 3,888. Both
were right about different things: `MR_TARIFFS` lists two tariff *regimes*, and
`spot_midvol` resolves to `SPOT_WINDOWS_PER_REGIME = 2` distinct week-long
series, so the true tariff cardinality is three, not two. Nothing was wrong
with the campaign -- the runlist had it right -- but the document could not be
used to check the runlist, which is the whole point of writing the design down.

REGIME VERSUS SERIES is the distinction that caused it, and it is the one to
keep in mind when reading the tables below. A design constant like
`M1_TARIFFS = ["flat", "tou2", "spot_lowvol", "spot_midvol", "spot_highvol"]`
has five entries but resolves to eight series: two contractual tariffs plus
three regimes at two drawn windows each. Every table here prints the resolved
count, and names the selector that produced it.

Outputs
    data/DESIGN_CONTRACT.md    the generated design tables, safe to include
                               in the campaign document by reference
    exit code 3                any predicted count disagrees with the runlist
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design, machines                          # noqa: E402

DATA = Path(os.environ.get("RCPSP_EXP_DATA", ROOT / "data"))


# ---------------------------------------------------------------------------
# resolving selectors the way 01/02 do, but by a different route
# ---------------------------------------------------------------------------

def series_names(man: list[dict], selector: list[str]) -> set[str]:
    """Series a tariff selector resolves to.

    An entry matches a series name exactly ("flat") or a regime
    ("spot_midvol"), and a regime expands to every window drawn from it. This
    is the step the campaign prose kept collapsing.
    """
    want, out = set(selector), set()
    for r in man:
        if r["price_name"] in want or r["price_regime"] in want:
            out.add(r["price_name"])
    return out


def family_series(man: list[dict], families: list[str]) -> set[str]:
    f = set(families)
    return {r["price_name"] for r in man if r["tariff_family"] in f}


def shops_in(man: list[dict], pool: str, only_m2: bool = False) -> set[str]:
    return {r["shop_id"] for r in man
            if pool in r["subset"].split(",")
            and (not only_m2 or r.get("m2_shop") == "1")}


# ---------------------------------------------------------------------------
# predicted counts, one entry per runlist block
# ---------------------------------------------------------------------------

def predict(man: list[dict]) -> list[dict]:
    """One row per block: the factors, their cardinalities, and the product.

    Each row carries the factors as (name, level count, what produced it), so
    the generated table is self-explaining and a reader can recompute it.
    """
    P: list[dict] = []

    def add(block: str, factors: list[tuple[str, int, str]], note: str = "") -> None:
        n = 1
        for _, c, _ in factors:
            n *= c
        P.append({"block": block, "factors": factors, "predicted": n, "note": note})

    n_seeds = {e: len(design.seeds(e)) for e in
               ("MR", "M0", "M1", "M2", "M3", "M4", "M5")}

    # ---- MR -------------------------------------------------------------
    if design.ENABLED.get("MR"):
        mr_series = series_names(man, design.MR_TARIFFS)
        add("MR.dispersion", [
            ("shops", design.MR_SHOPS, "design.MR_SHOPS"),
            ("tariff series", len(mr_series),
             f"MR_TARIFFS={design.MR_TARIFFS} resolved "
             f"({design.SPOT_WINDOWS_PER_REGIME} window(s) per spot regime)"),
            ("capacities", len(design.MR_BATTERY_RATIOS), "MR_BATTERY_RATIOS"),
            ("archetypes", len(design.MR_ARCHETYPES), "MR_ARCHETYPES"),
            ("seeds", design.MR_SEEDS, "design.MR_SEEDS"),
        ], "runs first; sets the seed counts for everything below")

    # ---- M0 -------------------------------------------------------------
    if design.ENABLED.get("M0"):
        v_shops = len(shops_in(man, "valid"))
        m0_series = series_names(man, design.M0_TARIFFS)
        # The MILP is offered on two deliberately different populations (see
        # design.MILP_PROVE_CLASSES): every shop of the prove classes, and a
        # stratified subsample of the probe class. Neither is a clean product
        # over the whole pool, so both are counted from the manifest.
        prove_shops = {r["shop_id"] for r in man
                       if "valid" in r["subset"].split(",")
                       and int(r["size_class"]) in design.MILP_PROVE_CLASSES}
        probe_shops_all = sorted({r["shop_id"] for r in man
                                  if "valid" in r["subset"].split(",")
                                  and int(r["size_class"]) in design.MILP_PROBE_CLASSES})
        n_probe = min(design.MILP_PROBE_SHOPS, len(probe_shops_all))
        n_inst = v_shops * len(m0_series)
        nb = len(design.M0_BATTERY_RATIOS)
        for meth in design.M0_METHODS:
            if meth == "MILP":
                add("M0.main.MILP.prove", [
                    ("shops", len(prove_shops),
                     f"every shop of classes {design.MILP_PROVE_CLASSES}"),
                    ("tariff series", len(m0_series), "M0_TARIFFS resolved"),
                    ("capacities", nb, "M0_BATTERY_RATIOS"),
                    ("seeds", 1, "deterministic"),
                ], "the population where the MILP can close: 'distance to the "
                   "optimum' is meaningful only here")
                add("M0.main.MILP.probe", [
                    ("shops", n_probe,
                     f"stratified prefix of {len(probe_shops_all)} class-"
                     f"{design.MILP_PROBE_CLASSES} shops, "
                     f"design.MILP_PROBE_SHOPS={design.MILP_PROBE_SHOPS}"),
                    ("tariff series", len(m0_series), "M0_TARIFFS resolved"),
                    ("capacities", nb, "M0_BATTERY_RATIOS"),
                    ("seeds", 1, "deterministic"),
                ], "where the exact method stops being a reference; reported "
                   "separately and never averaged with the prove population")
                continue
            k = n_seeds["M0"] if meth not in design.DETERMINISTIC_METHODS else 1
            add(f"M0.main.{meth}", [
                ("shops", v_shops, "pool 'valid'"),
                ("tariff series", len(m0_series),
                 f"M0_TARIFFS={design.M0_TARIFFS} resolved"),
                ("capacities", nb, "M0_BATTERY_RATIOS"),
                ("seeds", k, "deterministic" if k == 1 else "design.seeds('M0')"),
            ])
        any_series = series_names(man, [design.M0_ANYTIME_TARIFF])
        add("M0.anytime", [
            ("shops", min(design.M0_ANYTIME_SHOPS, v_shops), "M0_ANYTIME_SHOPS"),
            ("tariff series", len(any_series),
             f"M0_ANYTIME_TARIFF='{design.M0_ANYTIME_TARIFF}' resolved"),
            ("capacities", nb, "M0_BATTERY_RATIOS"),
            ("extra budgets", len(design.TL_PROFILE_EXTRA), "TL_PROFILE_EXTRA"),
            ("seeds", min(design.M0_ANYTIME_SEEDS, n_seeds["M0"]), "M0_ANYTIME_SEEDS"),
        ], "the campaign budget itself is already in M0.main.GA")

    # ---- M1 -------------------------------------------------------------
    if design.ENABLED.get("M1"):
        c_shops = len(shops_in(man, "core"))
        cube_series = series_names(man, design.M1_TARIFFS)
        add("M1.cube", [
            ("shops", c_shops, "pool 'core'"),
            ("tariff series", len(cube_series),
             f"M1_TARIFFS={len(design.M1_TARIFFS)} selectors resolved to "
             f"{len(cube_series)} series"),
            ("archetypes", len(design.M1_ARCHETYPES), "M1_ARCHETYPES"),
            ("capacities", len(design.M1_BATTERY_RATIOS), "M1_BATTERY_RATIOS"),
            ("seeds", n_seeds["M1"], "design.seeds('M1')"),
        ], "the fully crossed cube")
        grid_series = series_names(man, [design.M1B_TARIFF])
        add("M1.grid", [
            ("shops", c_shops, "pool 'core'"),
            ("tariff series", len(grid_series),
             f"M1B_TARIFF='{design.M1B_TARIFF}' resolved"),
            ("grid cells", len(machines.RHO_LEVELS) * len(machines.RESTART_LEVELS),
             f"{len(machines.RHO_LEVELS)} rho x "
             f"{len(machines.RESTART_LEVELS)} restart"),
            ("capacities", len(design.M1B_BATTERY_RATIOS), "M1B_BATTERY_RATIOS"),
            ("seeds", n_seeds["M1"], "design.seeds('M1')"),
        ], "the orthogonal (rho, restart) surface")

    # ---- M2 -------------------------------------------------------------
    if design.ENABLED.get("M2"):
        m2_shops = len(shops_in(man, "core", only_m2=True))
        by_fam: dict[str, set] = defaultdict(set)
        for name in family_series(man, design.M2_TARIFF_FAMILIES):
            fam = next(r["tariff_family"] for r in man if r["price_name"] == name)
            by_fam[fam].add(name)
        for fam in sorted(by_fam):
            add(f"M2.{fam}", [
                ("shops", m2_shops, f"stratified subset, design.M2_SHOPS="
                                    f"{design.M2_SHOPS}"),
                ("tariff series", len(by_fam[fam]), f"family '{fam}'"),
                ("capacities", len(design.M2_BATTERY_RATIOS), "M2_BATTERY_RATIOS"),
                ("seeds", n_seeds["M2"], "design.seeds('M2')"),
            ])

    # ---- M3 -------------------------------------------------------------
    if design.ENABLED.get("M3"):
        s_shops = len(shops_in(man, "scale"))
        m3_series = series_names(man, design.M3_TARIFFS)
        add("M3.scale", [
            ("shops", s_shops, "pool 'scale'"),
            ("tariff series", len(m3_series), f"M3_TARIFFS resolved"),
            ("capacities", len(design.M3_BATTERY_RATIOS), "M3_BATTERY_RATIOS"),
            ("seeds", n_seeds["M3"], "design.seeds('M3')"),
        ])

    # ---- M4 -------------------------------------------------------------
    if design.ENABLED.get("M4"):
        c_shops = len(shops_in(man, "core"))
        m4_series = series_names(man, design.M4_TARIFFS)
        for meth in design.STATE_METHODS:
            add(f"M4.{meth}", [
                ("shops", c_shops, "pool 'core'"),
                ("tariff series", len(m4_series), "M4_TARIFFS resolved"),
                ("state levels", len(design.STATE_POLICIES), "STATE_POLICIES"),
                ("capacities", len(design.M4_BATTERY_RATIOS), "M4_BATTERY_RATIOS"),
                ("seeds", n_seeds["M4"], "design.seeds('M4')"),
            ])

    # ---- M5 -------------------------------------------------------------
    if design.ENABLED.get("M5"):
        l_shops = len(shops_in(man, "lambda"))
        m5_series = series_names(man, design.M5_TARIFFS)
        add("M5.frontier", [
            ("shops (lambda is in the shop id)", l_shops,
             f"pool 'lambda' = core structures x {len(design.LAMBDA_LEVELS)} "
             f"lambda levels"),
            ("tariff series", len(m5_series), "M5_TARIFFS resolved"),
            ("capacities", len(design.M5_BATTERY_RATIOS), "M5_BATTERY_RATIOS"),
            ("seeds", n_seeds["M5"], "design.seeds('M5')"),
        ], "lambda is baked into the instance file, so it multiplies the SHOP "
           "count rather than appearing as a separate factor")
    return P


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-verify", action="store_true",
                    help="write the contract without comparing to runlist.csv")
    ap.add_argument("--check-docs", action="store_true",
                    help="also scan the campaign documents for run counts that "
                         "no longer match")
    args = ap.parse_args()

    man_path = DATA / "manifest_instances.csv"
    if not man_path.exists():
        print("FATAL: run 01_build_instances.py first", file=sys.stderr)
        return 1
    man = list(csv.DictReader(man_path.open()))
    P = predict(man)

    actual: Counter = Counter()
    have_runlist = (DATA / "runlist.csv").exists() and not args.no_verify
    if have_runlist:
        for r in csv.DictReader((DATA / "runlist.csv").open()):
            actual[r["block"]] += 1

    L = ["# Design contract",
         "",
         "GENERATED by `bin/07_design_contract.py` from `config/design.py` and",
         "the instance manifest. Do not edit, and do not copy these numbers into",
         "prose by hand -- cite this file instead. Every count below is predicted",
         "by multiplying factor cardinalities, then checked against the runlist,",
         "which was built by a completely different code path.",
         "",
         "**Regime versus series.** A tariff selector names regimes, and a spot",
         f"regime resolves to `SPOT_WINDOWS_PER_REGIME = "
         f"{design.SPOT_WINDOWS_PER_REGIME}` distinct week-long series. Five",
         "selectors can therefore mean eight series. Every table states the",
         "resolved count and the selector that produced it.",
         "",
         f"Profile `{design.PROFILE}`, master seed {design.MASTER_SEED}, "
         f"GA budget {design.TL_GA} s.",
         "",
         "## Per-block factor decomposition",
         ""]

    total_pred = 0
    mismatches: list[str] = []
    for row in P:
        blk = row["block"]
        L += [f"### `{blk}`", ""]
        if row["note"]:
            L += [f"*{row['note']}*", ""]
        L += ["| factor | levels | source |", "|---|---:|---|"]
        for name, card, src in row["factors"]:
            L.append(f"| {name} | {card} | {src} |")
        expr = " x ".join(str(c) for _, c, _ in row["factors"])
        L += ["", f"**{expr} = {row['predicted']:,} runs**"]
        total_pred += row["predicted"]
        if have_runlist:
            got = actual.get(blk, 0)
            if got == row["predicted"]:
                L.append(f"Runlist agrees ({got:,}).")
            else:
                L.append(f"**MISMATCH: runlist has {got:,}, "
                         f"prediction says {row['predicted']:,}.**")
                mismatches.append(f"{blk}: predicted {row['predicted']}, "
                                  f"runlist {got}")
        L.append("")

    # ---- totals ----------------------------------------------------------
    frac = design.EST_TIME_FRACTION
    tl_of = {"MR": design.TL_GA, "M0": None, "M1": design.TL_GA,
             "M2": design.TL_GA, "M3": design.TL_GA, "M4": design.TL_GA,
             "M5": design.TL_GA}
    L += ["## Totals", "",
          "| | predicted | runlist |", "|---|---:|---:|",
          f"| runs | {total_pred:,} | "
          f"{sum(actual.values()):,} |" if have_runlist
          else f"| runs | {total_pred:,} | (no runlist) |"]
    if have_runlist:
        core_s = 0.0
        for r in csv.DictReader((DATA / "runlist.csv").open()):
            core_s += int(r["time_limit"]) * frac[r["method"]]
        L += [f"| core-hours | (see budget_report.txt) | {core_s/3600:,.1f} |",
              f"| wall days at {design.N_WORKERS} workers | | "
              f"{core_s/3600/design.N_WORKERS/24:.2f} |"]
    L += ["",
          "Core-hours are the runlist's own estimate: each run's time limit",
          "times `EST_TIME_FRACTION[method]`. They are a planning figure, not a",
          "measurement, and the fractions themselves are stated in `design.py`.",
          ""]

    # ---- seed counts, flagged as provisional -----------------------------
    L += ["## Seed counts", "",
          "| experiment | seeds | status |", "|---|---:|---|"]
    for e in ("MR", "M0", "M1", "M2", "M3", "M4", "M5"):
        k = len(design.seeds(e))
        st = ("fixed by design (needs df to estimate a variance)" if e == "MR"
              else "PROVISIONAL until MR reports -- see PREREGISTRATION.md section 6")
        L.append(f"| {e} | {k} | {st} |")
    L += ["",
          f"Floor: `MIN_SEEDS = {design.MIN_SEEDS}`. Target minimum detectable "
          f"effect: `MDE_TARGET_PCT = {design.MDE_TARGET_PCT}` % of the naive "
          f"energy bill.", ""]

    out = DATA / "DESIGN_CONTRACT.md"
    out.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nwritten to {out}")

    if mismatches:
        print("\nFATAL: the design contract disagrees with the runlist:",
              file=sys.stderr)
        for m in mismatches:
            print(f"  {m}", file=sys.stderr)
        print("\nOne of the two is wrong. Neither is allowed to win by default: "
              "find which factor was dropped or double-counted before running "
              "anything.", file=sys.stderr)
        return 3
    if args.check_docs:
        ch = {}
        if have_runlist:
            for r in csv.DictReader((DATA / "runlist.csv").open()):
                ch[r["experiment"]] = ch.get(r["experiment"], 0.0) + \
                    int(r["time_limit"]) * frac[r["method"]] / 3600.0
        n_inst = len({r["instance"] for r in man})
        stale, skipped = check_docs(P, actual, total_pred, ch, n_inst)
        if stale:
            print("\nFATAL: the campaign documents quote run counts that no "
                  "longer match the design:", file=sys.stderr)
            for line in stale:
                print(f"  {line}", file=sys.stderr)
            print("\nEither the design moved and the prose did not, or the prose "
                  "was always wrong. Regenerate the numbers from this contract "
                  "rather than editing them by hand.", file=sys.stderr)
            return 5
        print(f"\nOK: no stale run count in the campaign documents "
              f"({skipped} projection(s) skipped -- figures introduced by "
              f"'roughly', 'instead of', 'would' and the like, which describe a "
              f"design other than this one and cannot be checked here).")

    if have_runlist:
        print(f"\nOK: {len(P)} blocks, {total_pred:,} runs, "
              f"predicted counts match the runlist exactly.")
    return 0


# ---------------------------------------------------------------------------
# document scan
# ---------------------------------------------------------------------------

# Numbers that legitimately appear in the prose without being a run count:
# design constants, years, page-like figures. Anything else of four digits or
# more is treated as a claim about the campaign's size and has to match.
# Numbers that legitimately appear without being a count this script knows:
# design constants, years, and quantities from the previous campaign quoted for
# comparison.
_DOC_WHITELIST = {
    266150, 233750,          # v1's run and GAP counts, quoted for comparison
    20260824, 20260801,      # master seeds
    18413,                   # v1's seeding-failure count
    10000,                   # bootstrap replicates
    2025, 2022, 2019, 2026,
    8760, 8784,              # hours in a year
    1800, 3600, 2064,        # seconds and hours: time limits, longest horizon
}

# A number introduced by one of these is a PROJECTION -- what the campaign would
# cost under a change that has not been made -- not a claim about the design as
# it stands. Those cannot be checked against the contract by construction, so
# they are skipped and counted, and the count is printed so nobody can quietly
# turn a hard number into a projection to get past this check.
_PROJECTION_CUES = ("roughly", "about", "instead of", "becomes", "would",
                    "adds", "brings", "approximately", "~", "pilot")


def check_docs(P: list[dict], actual: Counter, total_pred: int,
               core_h: dict[str, float], n_instances: int) -> tuple[list[str], int]:
    """Flag run counts in the campaign docs that match nothing in the contract.

    SCOPE, deliberately narrow. Only lines that talk about runs, core-hours or
    instances are scanned, and only numbers of four digits or more. A wider net
    caught bootstrap replicate counts and every illustrative figure, which is
    how a check like this gets switched off within a week.

    It cannot stay silent about a hard count that has drifted, which is the one
    property that matters: the 2,592-versus-3,888 discrepancy this whole script
    exists for was exactly that.
    """
    import re
    known = {total_pred, sum(actual.values()), n_instances}
    for row in P:
        known.add(row["predicted"])
        for _, card, _ in row["factors"]:
            known.add(card)
    known |= set(actual.values())
    per_exp: Counter = Counter()
    for blk, n in actual.items():
        per_exp[blk.split(".")[0]] += n
    known |= set(per_exp.values())
    # Core-hours, at the roundings a document plausibly uses.
    for v in list(core_h.values()) + [sum(core_h.values())]:
        for r in (round(v), round(v, -1), round(v, -2)):
            known.add(int(r))

    docs = [ROOT / "CAMPAIGN_IJPR.md", ROOT / "RUNBOOK_SERVER.md",
            ROOT / "STATUS.md", ROOT / "PREREGISTRATION.md"]
    pat = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\w.%])")
    relevant = re.compile(r"\brun|core-h|instance", re.I)
    out, skipped = [], 0
    for d in docs:
        if not d.exists():
            continue
        # TABLE CONTEXT. A markdown table row is often just "| total | 85,476 |"
        # with the words "runs" and "core-h" living in the header two lines up,
        # so a per-line keyword filter skips exactly the rows most likely to
        # carry a stale total. (This was not hypothetical: the first version of
        # this check passed a planted 99,999 in the totals table.) Once a header
        # row mentions runs or core-hours, every row of that table is in scope
        # until the table ends.
        in_table = False
        for i, line in enumerate(d.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("|"):
                if relevant.search(line):
                    in_table = True
            else:
                in_table = False
            if not (relevant.search(line) or in_table):
                continue
            low = line.lower()
            projection = any(c in low for c in _PROJECTION_CUES)
            for m in pat.finditer(line):
                v = int(m.group(1).replace(",", ""))
                if v in _DOC_WHITELIST or v in known:
                    continue
                # A projection is allowed to differ; a bare assertion is not.
                if projection:
                    skipped += 1
                    continue
                out.append(f"{d.name}:{i}: {m.group(1)} matches no block, "
                           f"experiment, total or core-hour figure\n"
                           f"      | {line.strip()[:88]}")
    return out, skipped


if __name__ == "__main__":
    raise SystemExit(main())
