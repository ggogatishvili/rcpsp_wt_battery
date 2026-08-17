"""
Analyses E1-E5.

Deliberately numpy-only. statsmodels/scipy are convenient but they are one more
thing that must be installed identically on the compute server for results to
be reproducible, and everything needed here (OLS, cluster-robust standard
errors, paired bootstrap) is a few lines of linear algebra. If you prefer
statsmodels, the point estimates below will agree; the standard errors are
cluster-robust by instance, which lme4/statsmodels default output is not.

Statistical conventions used throughout:
  * The unit of analysis is the *instance*, not the run. Seeds are averaged
    within instance-configuration first, so seed noise does not inflate n.
  * All comparisons are PAIRED on the instance. Configurations are evaluated on
    identical instances by construction, so paired differences remove between-
    instance variance, which dominates.
  * Confidence intervals are bootstrap percentile intervals resampling
    instances (not runs), 10,000 replicates, seeded.
  * Multiple comparisons across the reported family are controlled with
    Holm-Bonferroni.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

BOOT = 10_000
RNG_SEED = 20260801


# ---------------------------------------------------------------------------
# io helpers
# ---------------------------------------------------------------------------

DROPPED_NONFINITE = 0


def load_results(path: Path) -> list[dict]:
    """Load successful runs, discarding those with a non-finite objective.

    A run can exit cleanly and still carry NaN: the solver serialises a
    non-finite double as JSON null (an infeasible fallback, or a chromosome
    scored -BIG_M reaching extraction), and 04_collect turns that back into
    NaN. Such a row is not an observation.

    Keeping them is worse than dropping them, because NaN propagates through
    np.mean and poisons the whole aggregate: a single bad run turned an entire
    regime of E2, half of E4 and the whole of E5 into "nan" while every other
    cell looked healthy. The count is exported so the rate is visible rather
    than silent -- if it is more than a fraction of a percent, the solver is
    reporting failures as successes and that is a finding in its own right.
    """
    global DROPPED_NONFINITE
    rows = list(csv.DictReader(Path(path).open()))
    out, dropped = [], 0
    for r in rows:
        if r.get("status") != "ok":
            continue
        try:
            r["objective"] = float(r["objective"])
            r["energy_cost"] = float(r["energy_cost"])
            r["tardiness_cost"] = float(r["tardiness_cost"])
            r["battery_ratio"] = float(r["battery_ratio"])
            r["wall_seconds"] = float(r["wall_seconds"])
        except (KeyError, ValueError):
            continue
        if not (math.isfinite(r["objective"]) and math.isfinite(r["energy_cost"])
                and math.isfinite(r["tardiness_cost"])):
            dropped += 1
            continue
        out.append(r)
    DROPPED_NONFINITE = dropped
    if dropped:
        print(f"WARNING: dropped {dropped} run(s) with status=ok but a "
              f"non-finite objective ({100*dropped/max(1,dropped+len(out)):.2f} % "
              f"of otherwise-successful runs)")
    return out


def norm_scale(row: dict) -> float:
    """A strictly positive, treatment-invariant scale for one instance.

    WHY THIS EXISTS. Savings and gaps are reported as a fraction of the
    baseline cost, and with 64 % of the price series containing negative hours
    the baseline can approach or cross zero. The ratio is then unbounded: it is
    what produces the >100 % savings in E2 and the divergent relative gaps in
    E0. Normalising by a quantity that does not depend on the configuration
    fixes that, at the cost of no longer reading as "percent of cost".

    The scale is the energy bill of running the EI machine flat out at the mean
    price of the attached tariff, with no optimisation at all:

        E_ref = e_day  x  horizon_days  x  mean_price
              = e_proc x  sum(EI durations)  x  mean_price

    It is instance-level, identical across every configuration compared, and
    strictly positive for every series in the library (the lowest regime mean
    is ~82 EUR/MWh). A normalised difference of 0.10 therefore means "one tenth
    of the naive energy bill", and is comparable across regimes and sizes in a
    way that a percentage of the realised cost is not.
    """
    try:
        e_day = float(row.get("inst_e_day", 0) or 0)
        days = float(row.get("inst_horizon_days", 0) or 0)
        price = float(row.get("inst_price_mean", 0) or 0)
    except (TypeError, ValueError):
        return float("nan")
    s = e_day * days * abs(price)
    return s if s > 1e-9 else float("nan")


def collapse_seeds(rows: list[dict], keys: tuple[str, ...],
                   value: str = "objective", how: str = "best") -> dict:
    """Average or take the best over seeds within each configuration cell.

    `how="best"` reports the best-of-run value the paper uses for GA results;
    `how="mean"` is the honest choice when comparing configurations, because
    best-of-k is biased upward in k and k differs between experiments. Both are
    computed and E1 reports mean with best-of-run as a robustness check.
    """
    buckets = defaultdict(list)
    for r in rows:
        buckets[tuple(r[k] for k in keys)].append(float(r[value]))
    agg = {}
    for k, v in buckets.items():
        agg[k] = min(v) if how == "best" else float(np.mean(v))
    return agg


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def boot_ci(x: np.ndarray, alpha: float = 0.05, n: int = BOOT) -> tuple[float, float]:
    if len(x) == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    means = x[idx].mean(axis=1)
    return (float(np.percentile(means, 100 * alpha / 2)),
            float(np.percentile(means, 100 * (1 - alpha / 2))))


def paired_summary(d: np.ndarray, label: str) -> dict:
    lo, hi = boot_ci(d)
    sd = float(np.std(d, ddof=1)) if len(d) > 1 else float("nan")
    se = sd / math.sqrt(len(d)) if len(d) > 1 else float("nan")
    t = float(np.mean(d)) / se if se and se > 0 else float("nan")
    return dict(effect=label, n=len(d), mean=float(np.mean(d)),
                sd=sd, ci_lo=lo, ci_hi=hi, t=t,
                cohens_dz=float(np.mean(d)) / sd if sd else float("nan"))


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, prev = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, max(prev, (m - i) * p))
        out[k] = adj
        prev = adj
    return out


def two_sided_p_from_t(t: float, df: int) -> float:
    """Normal approximation; df here is always in the hundreds."""
    if not math.isfinite(t):
        return float("nan")
    return math.erfc(abs(t) / math.sqrt(2))


def ols_cluster(X: np.ndarray, y: np.ndarray, clusters: np.ndarray):
    """OLS with cluster-robust (CR0) covariance. Returns beta, se, r2."""
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    meat = np.zeros((X.shape[1], X.shape[1]))
    for c in np.unique(clusters):
        m = clusters == c
        u = X[m].T @ resid[m]
        meat += np.outer(u, u)
    G = len(np.unique(clusters))
    k = X.shape[1]
    scale = (G / max(1, G - 1)) * ((len(y) - 1) / max(1, len(y) - k))
    cov = XtX_inv @ meat @ XtX_inv * scale
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return beta, se, (1 - ss_res / ss_tot if ss_tot else float("nan"))


# ---------------------------------------------------------------------------
# E1 — value decomposition
# ---------------------------------------------------------------------------

def e1(rows: list[dict], out: Path, how: str = "mean") -> str:
    """Main effects and the sigma x beta interaction, paired by instance.

    Cost is decomposed as
        Z0 - Z(pi, sigma, beta) = V_pi + V_sigma + V_beta + interactions
    but only the cells the solver can currently produce are estimated. With the
    state dimension blocked (item C1) this reduces to the pi x beta plane, and
    the sigma terms are reported as NOT ESTIMABLE rather than silently omitted.
    """
    rows = [r for r in rows if r["experiment"] == "E1"]
    lines = ["E1 - decomposition of the value of flexibility", "=" * 62]
    if not rows:
        return "E1: no data\n"

    states = sorted({r["state_policy"] for r in rows})
    lines.append(f"state policies present: {', '.join(states)}")

    # ---- PRIMARY: the sigma x beta decomposition (RQ1) --------------------
    # This is the question the paper poses: are machine-state flexibility and
    # storage substitutes or complements? The baseline is the status quo cell
    # -- always hot, no battery -- and everything is measured against it:
    #
    #   V_sigma = Z(s1,0) - Z(s3,0)      state flexibility alone
    #   V_beta  = Z(s1,0) - Z(s1,b)      storage alone
    #   V_joint = Z(s1,0) - Z(s3,b)      both
    #   I_sigma_beta = V_joint - V_sigma - V_beta
    #
    # A negative interaction means the levers compete for the same arbitrage
    # (substitutes) and a plant that has one should discount the other.
    if len({"sigma1", "sigma3"} & set(states)) == 2:
        ladder_pol = sorted({r["policy"] for r in rows
                             if r["state_policy"] != "sigma3"}) or ["edd"]
        pol0 = ladder_pol[0]
        lines += ["", "=" * 62,
                  f"PRIMARY (RQ1): machine-state x storage, policy = {pol0}",
                  "=" * 62]
        for regime in sorted({r["price_regime"] for r in rows}):
            sub = [r for r in rows
                   if r["price_regime"] == regime and r["policy"] == pol0]
            cells = collapse_seeds(
                sub, ("instance", "state_policy", "battery_ratio"), how=how)
            insts = sorted({k[0] for k in cells})

            def g(i, st, b):
                return cells.get((i, st, b))

            keep = [i for i in insts
                    if all(g(i, st, b) is not None
                           for st in ("sigma1", "sigma3") for b in (0.0, 1.0))]
            if not keep:
                lines.append(f"  {regime}: no complete sigma1/sigma3 cells")
                continue
            Z00 = np.array([g(i, "sigma1", 0.0) for i in keep])   # status quo
            Z10 = np.array([g(i, "sigma3", 0.0) for i in keep])   # states only
            Z01 = np.array([g(i, "sigma1", 1.0) for i in keep])   # storage only
            Z11 = np.array([g(i, "sigma3", 1.0) for i in keep])   # both

            V_s = (Z00 - Z10) / Z00 * 100
            V_b = (Z00 - Z01) / Z00 * 100
            V_j = (Z00 - Z11) / Z00 * 100
            I_sb = V_j - V_s - V_b

            st4 = [paired_summary(V_s, "V_sigma  (state flexibility only)"),
                   paired_summary(V_b, "V_beta   (storage only)"),
                   paired_summary(V_j, "V_joint  (both)"),
                   paired_summary(I_sb, "I_sigma_beta (interaction)")]
            ps = {x["effect"]: two_sided_p_from_t(x["t"], x["n"] - 1) for x in st4}
            padj = holm(ps)
            lines.append(f"\n--- {regime}  n={len(keep)} "
                         f"(% of the always-hot, no-battery baseline) ---")
            lines.append(f"    {'effect':36s} {'mean':>8s} {'95% CI':>20s} "
                         f"{'dz':>7s} {'p(holm)':>9s}")
            for x in st4:
                lines.append(f"    {x['effect']:36s} {x['mean']:8.3f} "
                             f"[{x['ci_lo']:8.3f},{x['ci_hi']:8.3f}] "
                             f"{x['cohens_dz']:7.3f} {padj[x['effect']]:9.4f}")
            mn = min(abs(float(np.mean(V_s))), abs(float(np.mean(V_b))))
            si = -float(np.mean(I_sb)) / mn if mn > 1e-12 else float("nan")
            verdict = ("SUBSTITUTES" if si > 0.05 else
                       "COMPLEMENTS" if si < -0.05 else "approximately ADDITIVE")
            lines.append(f"    substitution index SI = {si:.3f}  -> {verdict}")

            # the intermediate rung, for the ladder table in the paper
            if "sigma2" in states:
                mid = [i for i in keep if g(i, "sigma2", 0.0) is not None]
                if mid:
                    Zm = np.array([g(i, "sigma2", 0.0) for i in mid])
                    Zs = np.array([g(i, "sigma1", 0.0) for i in mid])
                    v = (Zs - Zm) / Zs * 100
                    lo, hi = boot_ci(v)
                    lines.append(f"    rung sigma1->sigma2 (idling only): "
                                 f"{v.mean():.3f} % [{lo:.3f},{hi:.3f}]  n={len(mid)}")
        lines += ["", "=" * 62,
                  "SECONDARY: scheduling policy x storage, within each state",
                  "=" * 62]
    if states == ["sigma3"]:
        lines += ["",
                  "  NOT ESTIMABLE: V_sigma and I_sigma_beta.",
                  "  The state-policy dimension requires solver item C1 (--states).",
                  "  Only the policy x battery plane is estimated below.", ""]

    for regime in sorted({r["price_regime"] for r in rows}):
        sub = [r for r in rows if r["price_regime"] == regime]
        cells = collapse_seeds(
            sub, ("instance", "policy", "state_policy", "battery_ratio"), how=how)
        insts = sorted({k[0] for k in cells})
        lines += [f"\n--- tariff regime: {regime}  ({len(insts)} instances) ---"]

        for st in states:
            def get(i, pol, b):
                return cells.get((i, pol, st, b))

            keep = [i for i in insts
                    if all(get(i, p, b) is not None
                           for p in ("edd", "price_aware") for b in (0.0, 1.0))]
            if not keep:
                lines.append(f"  {st}: no complete cells")
                continue
            Z00 = np.array([get(i, "edd", 0.0) for i in keep])          # baseline
            Z10 = np.array([get(i, "price_aware", 0.0) for i in keep])  # policy only
            Z01 = np.array([get(i, "edd", 1.0) for i in keep])          # battery only
            Z11 = np.array([get(i, "price_aware", 1.0) for i in keep])  # both

            V_pi = (Z00 - Z10) / Z00 * 100
            V_b = (Z00 - Z01) / Z00 * 100
            V_both = (Z00 - Z11) / Z00 * 100
            inter = V_both - V_pi - V_b

            stats = [paired_summary(V_pi, "V_policy (price-aware only)"),
                     paired_summary(V_b, "V_battery (battery only)"),
                     paired_summary(V_both, "V_joint (both)"),
                     paired_summary(inter, "I_policy_battery (interaction)")]
            ps = {s["effect"]: two_sided_p_from_t(s["t"], s["n"] - 1) for s in stats}
            padj = holm(ps)

            lines.append(f"  state={st}  n={len(keep)}  (all values are % of baseline cost)")
            lines.append(f"    {'effect':34s} {'mean':>8s} {'95% CI':>20s} "
                         f"{'dz':>7s} {'p(holm)':>9s}")
            for s in stats:
                lines.append(
                    f"    {s['effect']:34s} {s['mean']:8.3f} "
                    f"[{s['ci_lo']:8.3f},{s['ci_hi']:8.3f}] "
                    f"{s['cohens_dz']:7.3f} {padj[s['effect']]:9.4f}")

            mn = min(abs(float(np.mean(V_pi))), abs(float(np.mean(V_b))))
            si = -float(np.mean(inter)) / mn if mn > 1e-12 else float("nan")
            verdict = ("SUBSTITUTES" if si > 0.05 else
                       "COMPLEMENTS" if si < -0.05 else "approximately ADDITIVE")
            lines.append(f"    substitution index SI = {si:.3f}  -> {verdict}")

    txt = "\n".join(lines) + "\n"
    (out / "e1_decomposition.txt").write_text(txt)
    return txt


# ---------------------------------------------------------------------------
# E2 — sizing and investment appraisal
# ---------------------------------------------------------------------------

def e2(rows: list[dict], out: Path, econ: dict) -> str:
    """Savings curve, marginal value of storage, and investment appraisal.

    UNITS. The solver is dimensionless: the machine draws `Proc.cost = 4`
    energy units per interval and prices are EUR/MWh, so the objective has
    units of (energy unit) x (EUR/MWh). Everything below assumes

        1 solver energy unit = 1 MWh,  1 interval = 1 hour

    i.e. the energy-intensive machine is a 4 MW load and `-b 16` is a 16 MWh
    battery. That is a defensible reading of the model as published, but it is
    an INTERPRETATION, not something the solver states. Every currency figure
    below inherits it. If the paper adopts a different scale, change
    MWH_PER_ENERGY_UNIT and re-run; nothing else needs touching.
    """
    MWH_PER_ENERGY_UNIT = 1.0

    rows = [r for r in rows if r["experiment"] == "E2"]
    lines = ["E2 - storage sizing and investment appraisal", "=" * 62,
             "  UNITS: 1 solver energy unit == "
             f"{MWH_PER_ENERGY_UNIT:g} MWh, 1 interval == 1 h (see docstring).",
             "  All EUR figures inherit this interpretation."]
    if not rows:
        return "E2: no data\n"

    cells = collapse_seeds(rows, ("instance", "price_regime", "battery_ratio"), how="mean")
    # per-instance attributes, taken once
    attrs = {}
    for r in rows:
        attrs.setdefault(r["instance"], dict(
            horizon=float(r.get("inst_horizon", 0) or 0),
            battery_arg=float(r.get("battery_arg", 0) or 0)))

    hours_per_year = econ["operating_weeks"] * 7 * 24

    for regime in sorted({k[1] for k in cells}):
        insts = sorted({k[0] for k in cells if k[1] == regime})
        ratios = sorted({k[2] for k in cells if k[1] == regime})
        base = {i: cells.get((i, regime, 0.0)) for i in insts}
        keep = [i for i in insts if base.get(i)]
        lines += [f"\n--- regime {regime}  ({len(keep)} instances) ---",
                  f"  {'B/E_day':>8s} {'saving %':>9s} {'95% CI':>19s} "
                  f"{'norm':>8s} {'MVS %/unit':>11s} {'cap MWh':>9s} "
                  f"{'NPV kEUR':>10s} {'payback y':>10s} {'NPV>0':>6s}"]
        prev_mean = 0.0
        for b in ratios:
            present = [i for i in keep if (i, regime, b) in cells]
            if not present:
                continue
            rel = np.array([100 * (base[i] - cells[(i, regime, b)]) / base[i]
                            for i in present])
            lo, hi = boot_ci(rel)
            # Same difference, normalised by a positive configuration-invariant
            # scale instead of the (possibly near-zero) baseline. Where the two
            # disagree badly, trust this one.
            nrm = np.array([(base[i] - cells[(i, regime, b)]) / _scale_of(rows, i)
                            for i in present
                            if math.isfinite(_scale_of(rows, i))])

            # Per-instance annualisation, then aggregate. Averaging horizons
            # first would bias the annual figure towards long-horizon instances.
            npvs, pays, caps = [], [], []
            for i in present:
                h = attrs[i]["horizon"]
                if h <= 0:
                    continue
                saving_h = base[i] - cells[(i, regime, b)]
                annual = saving_h * hours_per_year / h
                # battery_arg is per-instance; recover it from the ratio and E_day
                cap = b * _e_day_of(rows, i) * MWH_PER_ENERGY_UNIT
                if cap <= 0:
                    continue
                caps.append(cap)
                npvs.append(_npv(annual, cap, econ))
                pays.append(_payback(annual, cap, econ))
            mvs = ((float(np.mean(rel)) - prev_mean) / b) if b else float("nan")
            med_npv = float(np.median(npvs)) / 1000.0 if npvs else float("nan")
            med_pay = float(np.median(pays)) if pays else float("nan")
            frac_pos = (100 * sum(1 for v in npvs if v > 0) / len(npvs)) if npvs else float("nan")
            lines.append(f"  {b:8.2f} {float(np.mean(rel)):9.3f} "
                         f"[{lo:8.3f},{hi:8.3f}] "
                         f"{(float(np.mean(nrm)) if len(nrm) else float('nan')):8.3f} "
                         f"{mvs:11.3f} "
                         f"{(float(np.mean(caps)) if caps else float('nan')):9.2f} "
                         f"{med_npv:10.1f} {med_pay:10.2f} {frac_pos:5.0f}%")
            prev_mean = float(np.mean(rel))

        lines += [
            "  MVS   marginal saving (percentage points) per unit of B/E_day.",
            "  NPV   median across instances, thousand EUR, config/economics.py.",
            "  norm  same saving divided by e_day x horizon_days x mean_price,",
            "        a positive configuration-invariant scale. Percentages are",
            "        unbounded here because negative prices can drive the",
            "        baseline cost towards zero; this column is not.",
            "  NPV>0 share of instances where the investment is worth making.",
            "  Saturation is where MVS approaches zero; the NPV-optimal size is",
            "  normally well below it, and that gap is the managerial point."]

    lines += ["", "  Sensitivity: re-run with config.economics.LOW_COST and",
              "  HIGH_COST to bound the investment conclusion. Do not report a",
              "  point payback figure without that band."]
    txt = "\n".join(lines) + "\n"
    (out / "e2_sizing.txt").write_text(txt)
    return txt


_EDAY_CACHE: dict[str, float] = {}
_SCALE_CACHE: dict[str, float] = {}


def _scale_of(rows: list[dict], inst: str) -> float:
    if inst not in _SCALE_CACHE:
        _SCALE_CACHE[inst] = float("nan")
        for r in rows:
            if r["instance"] == inst:
                _SCALE_CACHE[inst] = norm_scale(r)
                break
    return _SCALE_CACHE[inst]


def _e_day_of(rows: list[dict], inst: str) -> float:
    if inst not in _EDAY_CACHE:
        for r in rows:
            if r["instance"] == inst:
                _EDAY_CACHE[inst] = float(r.get("inst_e_day", 0) or 0)
                break
        else:
            _EDAY_CACHE[inst] = 0.0
    return _EDAY_CACHE[inst]


def _npv(annual_saving: float, capacity_mwh: float, e: dict) -> float:
    capex = e["capex_eur_per_kwh"] * 1000.0 * capacity_mwh
    r, L, om = e["wacc"], e["life_years"], e["om_share"]
    net = annual_saving - om * capex
    return sum(net / (1 + r) ** y for y in range(1, L + 1)) - capex


def _payback(annual_saving: float, capacity_mwh: float, e: dict) -> float:
    capex = e["capex_eur_per_kwh"] * 1000.0 * capacity_mwh
    r, om = e["wacc"], e["om_share"]
    net = annual_saving - om * capex
    if net <= 0 or capex <= 0:
        return float("inf")
    acc, y = 0.0, 0
    while acc < capex and y < 100:
        y += 1
        acc += net / (1 + r) ** y
    return y if y < 100 else float("inf")


# ---------------------------------------------------------------------------
# E3 — tariff regimes and the screening rule
# ---------------------------------------------------------------------------

def e3(rows: list[dict], out: Path) -> str:
    rows = [r for r in rows if r["experiment"] == "E3"]
    lines = ["E3 - tariff regimes: when does storage pay?", "=" * 62]
    if not rows:
        return "E3: no data\n"

    cells = collapse_seeds(rows, ("instance", "battery_ratio"), how="mean")
    meta = {r["instance"]: r for r in rows}
    recs = []
    for (inst, b), v in cells.items():
        if b == 0.0:
            continue
        base = cells.get((inst, 0.0))
        if not base:
            continue
        m = meta[inst]
        try:
            recs.append(dict(
                inst=inst,
                shop=m["inst_shop_id"],
                saving=100 * (base - v) / base,
                spread=float(m["inst_spread_intraday"]),
                cv=float(m["inst_price_cv"]),
                neg=float(m["inst_neg_share"]),
                mean=float(m["inst_price_mean"]),
                regime=m["price_regime"]))
        except (KeyError, ValueError):
            continue
    if not recs:
        return "E3: no paired battery/no-battery cells\n"

    # ---- lead with the non-parametric result -----------------------------
    # The regime means need no functional form, no covariate support and no
    # identification assumption. They are the finding; the regression below is
    # a description of it, and is reported second for that reason.
    lines.append("\n  PRIMARY: mean saving by tariff regime (non-parametric)")
    byreg = defaultdict(list)
    for r in recs:
        byreg[r["regime"]].append(r["saving"])
    for reg, v in sorted(byreg.items(), key=lambda kv: float(np.mean(kv[1]))):
        a = np.array(v)
        lo, hi = boot_ci(a)
        lines.append(f"    {reg:14s} n={len(a):5d}  mean {a.mean():7.3f} % "
                     f"[{lo:7.3f},{hi:7.3f}]")

    y = np.array([r["saving"] for r in recs])
    X = np.column_stack([np.ones(len(recs)),
                         [r["spread"] for r in recs],
                         [r["cv"] for r in recs],
                         [r["neg"] for r in recs],
                         [r["mean"] for r in recs]])
    clusters = np.array([r["shop"] for r in recs])
    beta, se, r2 = ols_cluster(X, y, clusters)
    names = ["intercept", "spread_intraday", "price_cv", "neg_share", "price_mean"]
    lines += ["", f"  SECONDARY: descriptive fit, n = {len(recs)} instance-tariff "
                  f"pairs, {len(set(clusters))} shop clusters, R2 = {r2:.4f}",
              "  saving% ~ spread + cv + neg_share + mean "
              "(cluster-robust SE by shop)",
              f"    {'term':18s} {'coef':>10s} {'se':>10s} {'t':>8s}"]
    for nm, b_, s_ in zip(names, beta, se):
        lines.append(f"    {nm:18s} {b_:10.4f} {s_:10.4f} "
                     f"{(b_/s_ if s_ else float('nan')):8.2f}")

    # ---- diagnostic 1: multicollinearity ---------------------------------
    # spread and cv both measure dispersion and are near-collinear by
    # construction. Individual coefficients are then not separately
    # identified, and a sign flip on the weaker one is an artefact, not a
    # finding. Report VIF so nobody ranks the predictors without seeing it.
    lines.append("\n  DIAGNOSTIC - variance inflation (VIF > 5 caution, > 10 severe)")
    Zc = X[:, 1:]
    Zs = (Zc - Zc.mean(0)) / np.where(Zc.std(0) == 0, 1.0, Zc.std(0))
    worst = 0.0
    for i, nm in enumerate(names[1:]):
        others = np.delete(Zs, i, 1)
        coef, *_ = np.linalg.lstsq(others, Zs[:, i], rcond=None)
        resid = Zs[:, i] - others @ coef
        ss = float(((Zs[:, i] - Zs[:, i].mean()) ** 2).sum())
        r2i = 1 - float(resid @ resid) / ss if ss else 0.0
        vif = 1 / (1 - r2i) if r2i < 1 else float("inf")
        worst = max(worst, vif)
        flag = "SEVERE" if vif > 10 else ("caution" if vif > 5 else "")
        lines.append(f"    {nm:18s} VIF {vif:8.2f}  {flag}")
    if worst > 5:
        lines.append("    -> individual coefficients are NOT separately identified;")
        lines.append("       do not rank predictors or interpret a sign flip.")

    # ---- diagnostic 2: support, and the screening rule -------------------
    # Inverting the fit for a target saving is only meaningful where the data
    # actually lie. The spread covariate here is close to bimodal (a flat
    # control at 0, then a gap, then the real tariffs), so a naive inversion
    # lands in a region with no observations.
    sp = X[:, 1]
    nz = sp[sp > 0]
    lines.append("\n  DIAGNOSTIC - support of spread_intraday (EUR/MWh)")
    lines.append(f"    zero (flat control): {int((sp == 0).sum())} obs;  "
                 f"non-zero min {nz.min():.1f}" if len(nz) else "    no non-zero spreads")
    lines.append("    percentiles: " + "  ".join(
        f"p{q}={np.percentile(sp, q):.1f}" for q in (1, 5, 25, 50, 95, 100)))

    lines.append("\n  screening rule (only where the data support it)")
    for target in (1.0, 2.0, 5.0):
        if beta[1] <= 1e-9:
            lines.append(f"    {target:.0f}%: spread coefficient not positive; "
                         f"no usable rule")
            continue
        x = (target - beta[0] - beta[2] * np.mean(X[:, 2])
             - beta[3] * np.mean(X[:, 3]) - beta[4] * np.mean(X[:, 4])) / beta[1]
        near = int(((sp >= 0.5 * x) & (sp <= 1.5 * x)).sum())
        if near >= max(30, 0.01 * len(sp)):
            lines.append(f"    {target:.0f}% saving at spread ~{x:.1f} "
                         f"({near} obs within +/-50% of it)")
        else:
            lines.append(f"    {target:.0f}%: implied spread {x:.1f} is NOT "
                         f"IDENTIFIABLE - only {near} obs within +/-50% of it; "
                         f"this is extrapolation outside the design, not a rule")

    # ---- diagnostic 3: real vs synthetic ---------------------------------
    # A third of the sample is a generated sinusoid. If the slope differs
    # between families, the pooled coefficient is partly an artefact of the
    # generator rather than a property of real tariffs.
    lines.append("\n  DIAGNOSTIC - fit separately by price family")
    for label, mask in (("real (spot+contractual)",
                         np.array([r["regime"] != "synthetic" for r in recs])),
                        ("synthetic", np.array([r["regime"] == "synthetic"
                                                for r in recs]))):
        if mask.sum() < 50:
            continue
        b2, s2, r22 = ols_cluster(X[mask], y[mask], clusters[mask])
        lines.append(f"    {label:24s} n={int(mask.sum()):5d}  R2={r22:6.4f}  "
                     f"spread coef {b2[1]:8.4f} (se {s2[1]:.4f})  "
                     f"range {sp[mask].min():.1f}-{sp[mask].max():.1f}")

    lines.append("\n  by regime (detail):")
    for reg, v in sorted(byreg.items()):
        a = np.array(v)
        lo, hi = boot_ci(a)
        lines.append(f"    {reg:14s} n={len(a):5d}  mean {a.mean():7.3f} % "
                     f"[{lo:7.3f},{hi:7.3f}]")

    txt = "\n".join(lines) + "\n"
    (out / "e3_tariff.txt").write_text(txt)
    return txt


# ---------------------------------------------------------------------------
# E4 — service/energy frontier
# ---------------------------------------------------------------------------

def e4(rows: list[dict], out: Path) -> str:
    rows = [r for r in rows if r["experiment"] == "E4"]
    lines = ["E4 - the service-energy frontier", "=" * 62]
    if not rows:
        return "E4: no data\n"

    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        key = (float(r["lam"]), float(r["battery_ratio"]))
        agg[key]["energy"].append(r["energy_cost"])
        agg[key]["tard"].append(r["tardiness_cost"])
        agg[key]["obj"].append(r["objective"])

    lines.append(f"  {'lambda':>8s} {'B/E_day':>8s} {'energy':>12s} "
                 f"{'tardiness':>12s} {'n':>6s}")
    pts = defaultdict(list)
    for (lam, b), d in sorted(agg.items()):
        e_, t_ = float(np.mean(d["energy"])), float(np.mean(d["tard"]))
        lines.append(f"  {lam:8.2f} {b:8.2f} {e_:12.2f} {t_:12.2f} {len(d['energy']):6d}")
        pts[b].append((t_, e_, lam))

    lines.append("\n  energy-service exchange rate  dEnergy/dTardiness")
    lines.append("  (negative = buying energy savings with delivery performance)")
    for b, seq in sorted(pts.items()):
        seq.sort()
        rates = []
        for (t0, e0, _), (t1, e1_, _) in zip(seq, seq[1:]):
            if abs(t1 - t0) > 1e-9:
                rates.append((e1_ - e0) / (t1 - t0))
        if rates:
            lines.append(f"    B/E_day={b:4.2f}  median {np.median(rates):10.4f}  "
                         f"range [{min(rates):.4f}, {max(rates):.4f}]")

    if 0.0 in pts and 1.0 in pts:
        lines.append("\n  frontier shift (battery vs none), matched on lambda:")
        d0 = {p[2]: p for p in pts[0.0]}
        d1 = {p[2]: p for p in pts[1.0]}
        for lam in sorted(set(d0) & set(d1)):
            t0, e0, _ = d0[lam]
            t1, e1_, _ = d1[lam]
            lines.append(f"    lambda={lam:6.2f}  dEnergy {e1_-e0:+10.2f}  "
                         f"dTardiness {t1-t0:+10.2f}")
        lines.append("  The paper's hypothesis is that the battery moves the frontier")
        lines.append("  inward (dEnergy < 0) without a tardiness penalty (dTardiness ~ 0).")

    txt = "\n".join(lines) + "\n"
    (out / "e4_frontier.txt").write_text(txt)
    return txt


# ---------------------------------------------------------------------------
# E5 — which plants benefit
# ---------------------------------------------------------------------------

def e5(rows: list[dict], out: Path) -> str:
    lines = ["E5 - which plants benefit (structural regression)", "=" * 62]
    rows = [r for r in rows if r["experiment"] in ("E1", "E2")]
    if not rows:
        return "E5: no data\n"

    cells = collapse_seeds(rows, ("instance", "battery_ratio"), how="mean")
    meta = {r["instance"]: r for r in rows}
    recs = []
    for (inst, b), v in cells.items():
        if b == 0.0:
            continue
        base = cells.get((inst, 0.0))
        if not base:
            continue
        m = meta[inst]
        try:
            recs.append(dict(
                shop=m["inst_shop_id"],
                y=100 * (base - v) / base,
                ei_density=float(m["inst_ei_density"]),
                ei_dur_share=float(m["inst_ei_duration_share"]),
                order_strength=float(m["inst_order_strength"]),
                resource_strength=float(m["inst_resource_strength"]),
                log_n=math.log(float(m["inst_n"])),
                horizon_days=float(m["inst_horizon_days"]),
                due_slack=float(m["inst_mean_due_slack"])))
        except (KeyError, ValueError):
            continue
    if len(recs) < 20:
        return "E5: too few complete records\n"

    names = ["ei_density", "ei_dur_share", "order_strength", "resource_strength",
             "log_n", "horizon_days", "due_slack"]
    y = np.array([r["y"] for r in recs])
    raw = np.column_stack([[r[k] for r in recs] for k in names])
    mu, sd = raw.mean(axis=0), raw.std(axis=0, ddof=1)
    sd[sd == 0] = 1.0
    Z = (raw - mu) / sd                       # standardised -> comparable effects
    X = np.column_stack([np.ones(len(recs)), Z])
    beta, se, r2 = ols_cluster(X, y, np.array([r["shop"] for r in recs]))

    lines += [f"  n = {len(recs)}, R2 = {r2:.4f}",
              "  standardised coefficients: % points of extra saving per 1 SD",
              f"    {'covariate':20s} {'beta':>9s} {'se':>9s} {'t':>7s}"]
    order = np.argsort(-np.abs(beta[1:]))
    for i in order:
        lines.append(f"    {names[i]:20s} {beta[i+1]:9.4f} {se[i+1]:9.4f} "
                     f"{(beta[i+1]/se[i+1] if se[i+1] else float('nan')):7.2f}")
    lines.append(f"    {'(intercept)':20s} {beta[0]:9.4f} {se[0]:9.4f}")
    txt = "\n".join(lines) + "\n"
    (out / "e5_characteristics.txt").write_text(txt)
    return txt


# ---------------------------------------------------------------------------
# E6 — machine profile (C2), battery efficiency (C3), C-rate (C4)
# ---------------------------------------------------------------------------

# Named archetypes matching EXPERIMENTAL_PLAN.md table 3.3, for the grid
# cells that land on one (see design.py §7). Unlabelled cells are just
# unnamed points in the (rho, restart) plane.
_E6_ARCHETYPES = {
    (0.0, "low"):   "A5 ideal (approx -- restart cost/duration still > 0, see design.py)",
    (0.25, "low"):  "A1 fast electric",
    (0.5, "med"):   "A2 default (grid reference cell)",
    (0.5, "high"):  "A3 industrial oven",
    (0.75, "high"): "A4 continuous process (approx)",
}


def _e6_paired_pct(cells: dict, group_keys, key_hi, key_lo,
                   scales: dict | None = None) -> np.ndarray:
    """Paired change (hi vs lo) over every group key present at both.

    DENOMINATOR. Dividing by the low-configuration cost is unstable here for
    the same reason it is everywhere else in this study: negative prices can
    drive an energy cost towards zero, and the ratio then explodes. It did:
    the first E6 tornado reported a policy effect of +233 % with a 95 % CI of
    [2, 691], and a restart effect whose sign was not even determined. When
    `scales` is supplied (instance -> norm_scale) the difference is divided by
    that positive, configuration-invariant quantity instead, which keeps the
    "% of the naive energy bill" reading and cannot degenerate.
    """
    out = []
    for gk in group_keys:
        lo = cells.get(gk + key_lo)
        hi = cells.get(gk + key_hi)
        if lo is None or hi is None:
            continue
        sc = scales.get(gk[0]) if scales else None
        if sc is not None and math.isfinite(sc) and sc > 0:
            out.append(100 * (hi - lo) / sc)
        elif lo:
            out.append(100 * (hi - lo) / lo)
    return np.array(out)


def e6(rows: list[dict], out: Path) -> str:
    """Machine substitution surface (C2) and battery efficiency/C-rate
    retention (C3/C4).

    CALIBRATION WARNING: the rho/restart/efficiency/C-rate levels driving
    this report are the same placeholders EXPERIMENTAL_PLAN.md §3.3/§3.4 and
    design.py §7 flag as "invented, needs a citable source" -- every number
    below illustrates the METHOD, not a result to publish before recalibrating
    design.RHO_LEVELS / RESTART_LEVELS / ROUNDTRIP_EFFICIENCY_LEVELS /
    C_RATE_LEVELS against real machine and storage data.

    BASELINE WARNING: E6 has no zero-battery counterpart for its grid cells
    (crossing it would have pushed the run count well past the plan's
    ~10 800 budget -- see design.py §7), so every percentage below is
    relative to the A2 grid cell (rho=0.5/restart=med) or to C-rate=infinity,
    NOT relative to no storage. This is a narrower claim than E1/E2's
    vs-no-storage savings figures and must not be conflated with them.
    """
    rows = [r for r in rows if r["experiment"] == "E6"]
    lines = ["E6 - machine profile (C2) and battery efficiency / C-rate (C3/C4)",
             "=" * 62]
    if not rows:
        return "E6: no data\n"

    lines += [
        "  CALIBRATION WARNING: rho/restart/efficiency/C-rate levels are",
        "  placeholders (EXPERIMENTAL_PLAN.md §3.3/§3.4, design.py §7), not",
        "  measured machine or storage data -- recalibrate before quoting.",
        "  BASELINE WARNING: figures below are relative to the A2 grid cell",
        "  or to C-rate=infinity, NOT relative to no storage (E6 has no",
        "  zero-battery counterpart) -- do not conflate with E1/E2 savings.",
    ]

    # instance -> positive normalising scale, shared by every tornado row
    e6_scales: dict[str, float] = {}
    for r in rows:
        e6_scales.setdefault(r["instance"], norm_scale(r))

    a = [r for r in rows if r.get("e6_subdesign") == "machine"]
    b = [r for r in rows if r.get("e6_subdesign") == "battery"]
    cells_a = collapse_seeds(a, ("instance", "price_regime", "policy", "rho", "restart_level"),
                             value="energy_cost", how="mean") if a else {}
    cells_b = collapse_seeds(b, ("instance", "price_regime", "roundtrip_eff", "c_rate"),
                             value="energy_cost", how="mean") if b else {}

    # ---- E6a: machine substitution surface --------------------------------
    if a:
        policies = sorted({r["policy"] for r in a})
        rhos = sorted({r["rho"] for r in a}, key=float)
        restarts = [x for x in ("low", "med", "high") if x in {r["restart_level"] for r in a}]
        groups_by_pol = defaultdict(set)
        for r in a:
            groups_by_pol[r["policy"]].add((r["instance"], r["price_regime"]))

        n_inst = len({r["instance"] for r in a})
        n_reg = len({r["price_regime"] for r in a})
        lines.append(f"\n--- E6a: machine substitution surface "
                     f"({n_inst} instances x {n_reg} regimes) ---")
        lines.append("  cell = mean % energy-cost change vs the A2 archetype "
                     "(rho=0.5, restart=med), paired by instance x regime")

        for policy in policies:
            base = {gk: cells_a.get(gk + (policy, "0.5", "med")) for gk in groups_by_pol[policy]}
            lines.append(f"\n  heat map -- policy = {policy}  "
                         "(rows = restart penalty, cols = rho = e_idle/e_proc)")
            lines.append("    restart " + "".join(f"{'rho='+rs:>10s}" for rs in rhos))
            for restart in restarts:
                rowvals = []
                for rs in rhos:
                    vals = [100 * (base[gk] - cells_a[gk + (policy, rs, restart)]) / base[gk]
                           for gk in groups_by_pol[policy]
                           if base.get(gk) and gk + (policy, rs, restart) in cells_a]
                    rowvals.append(float(np.mean(vals)) if vals else float("nan"))
                cell_txt = "".join(f"{v:10.2f}" if math.isfinite(v) else f"{'.':>10s}"
                                   for v in rowvals)
                lines.append(f"    {restart:7s}{cell_txt}")
        lines.append("  positive = cheaper than A2 at that policy; negative = more expensive.")

        lines.append("\n  substitution map with archetypes (grid cell -> nearest named archetype):")
        for rs in rhos:
            for restart in restarts:
                lab = _E6_ARCHETYPES.get((float(rs), restart))
                if lab:
                    lines.append(f"    rho={rs:<5s} restart={restart:<5s} -> {lab}")
    else:
        lines.append("\n--- E6a: no machine-grid data ---")

    # ---- E6b: C-rate retention --------------------------------------------
    if b:
        groups_b = {(r["instance"], r["price_regime"]) for r in b}
        effs = sorted({r["roundtrip_eff"] for r in b}, key=float)
        crates = sorted((c for c in {r["c_rate"] for r in b} if c != "inf"), key=float)
        if any(r["c_rate"] == "inf" for r in b):
            crates.append("inf")

        lines.append(f"\n--- E6b: C-rate retention "
                     f"({len({g[0] for g in groups_b})} instances x "
                     f"{len({g[1] for g in groups_b})} regimes, policy=price_aware) ---")
        lines.append("  cell = % of the C-rate=infinity energy cost retained at that C-rate")
        lines.append("  (100% = capping the rate changed nothing; <100% = the cap cost money)")
        lines.append("    eta_rt   " + "".join(f"{('C='+c):>10s}" for c in crates))
        for eff in effs:
            base = {gk: cells_b.get(gk + (eff, "inf")) for gk in groups_b}
            rowvals = []
            for c in crates:
                vals = [100 * cells_b[gk + (eff, c)] / base[gk]
                       for gk in groups_b
                       if base.get(gk) and gk + (eff, c) in cells_b]
                rowvals.append(float(np.mean(vals)) if vals else float("nan"))
            row_txt = "".join(f"{v:10.2f}" if math.isfinite(v) else f"{'.':>10s}"
                              for v in rowvals)
            lines.append(f"    {eff:<9s}{row_txt}")
        lines.append("  eta_rt = round-trip efficiency (eta_c x eta_d); "
                     "C = C-rate (max charge/discharge power / capacity).")
    else:
        lines.append("\n--- E6b: no battery-grid data ---")

    # ---- tornado: which factor moves energy cost most ---------------------
    lines.append("\n--- tornado: energy-cost effect of moving one factor from its "
                 "lowest to its highest level, holding the rest at the A2 / "
                 "uncapped reference (paired by instance x regime) ---")
    tornado: list[dict] = []
    if a:
        rho_lo, rho_hi = min(rhos, key=float), max(rhos, key=float)
        gk_edd = sorted(groups_by_pol.get("edd", set()))
        tornado.append(paired_summary(
            _e6_paired_pct(cells_a, gk_edd, ("edd", rho_hi, "med"), ("edd", "0.5", "med"), e6_scales),
            f"rho: {rho_lo}->{rho_hi} (restart=med, policy=edd)"))
        if "low" in restarts and "high" in restarts:
            tornado.append(paired_summary(
                _e6_paired_pct(cells_a, gk_edd, ("edd", "0.5", "high"), ("edd", "0.5", "low"), e6_scales),
                "restart: low->high (rho=0.5, policy=edd)"))
        if "price_aware" in groups_by_pol:
            gk_ref = sorted(groups_by_pol["edd"] & groups_by_pol["price_aware"])
            tornado.append(paired_summary(
                _e6_paired_pct(cells_a, gk_ref, ("price_aware", "0.5", "med"), ("edd", "0.5", "med"), e6_scales),
                "policy: edd->price_aware (rho=0.5, restart=med)"))
    if b:
        eff_lo, eff_hi = min(effs, key=float), max(effs, key=float)
        gk_b = sorted(groups_b)
        tornado.append(paired_summary(
            _e6_paired_pct(cells_b, gk_b, (eff_hi, "inf"), (eff_lo, "inf"), e6_scales),
            f"round-trip efficiency: {eff_lo}->{eff_hi} (C-rate=infinity)"))
        eff_ref = min(effs, key=lambda e: abs(float(e) - 0.95))
        crate_lo = min((c for c in crates if c != "inf"), key=float, default=None)
        if crate_lo:
            tornado.append(paired_summary(
                _e6_paired_pct(cells_b, gk_b, (eff_ref, crate_lo), (eff_ref, "inf"), e6_scales),
                f"C-rate: infinity->{crate_lo} (round-trip eff={eff_ref})"))

    tornado = [t for t in tornado if t["n"] > 0]
    tornado.sort(key=lambda t: -abs(t["mean"]))
    if tornado:
        lines.append(f"    {'factor':52s} {'mean %':>8s} {'95% CI':>20s} {'n':>5s}")
        for t in tornado:
            lines.append(f"    {t['effect']:52s} {t['mean']:8.3f} "
                         f"[{t['ci_lo']:8.3f},{t['ci_hi']:8.3f}] {t['n']:5d}")
    else:
        lines.append("    no comparable pairs found")

    txt = "\n".join(lines) + "\n"
    (out / "e6_machine_battery.txt").write_text(txt)
    return txt


# ---------------------------------------------------------------------------
# E0 — validation
# ---------------------------------------------------------------------------

def e0(rows: list[dict], out: Path) -> str:
    rows = [r for r in rows if r["experiment"] == "E0"]
    lines = ["E0 - solver validation", "=" * 62]
    if not rows:
        return "E0: no data\n"

    # ---- anytime profile -------------------------------------------------
    # The metaheuristic parameters were tuned by irace at --tl 600 for GA only
    # (tuning/target-runner), while the main design runs at 60 s. Ranking GA
    # against GAP at a single budget therefore confounds the methods with a
    # parameter setting calibrated elsewhere. Reporting the ranking as a
    # function of the budget is the honest form of the comparison, and it is
    # a more informative result than a single row.
    budgets = sorted({int(r["time_limit"]) for r in rows
                      if r["method"] in ("GA", "GAP")})
    if len(budgets) > 1:
        lines += ["", "anytime profile: mean objective by budget "
                      "(paired on instance x battery, lower is better)"]
        paired = defaultdict(dict)
        for r in rows:
            if r["method"] not in ("GA", "GAP"):
                continue
            k = (r["instance"], r["battery_ratio"], int(r["time_limit"]))
            paired[k].setdefault(r["method"], []).append(r["objective"])
        rowsby = defaultdict(lambda: {"GA": [], "GAP": [], "n": 0})
        for (inst, b, tl), d in paired.items():
            if "GA" in d and "GAP" in d:
                rowsby[tl]["GA"].append(float(np.mean(d["GA"])))
                rowsby[tl]["GAP"].append(float(np.mean(d["GAP"])))
                rowsby[tl]["n"] += 1
        lines.append(f"  {'budget':>8s} {'n':>6s} {'GA':>14s} {'GAP':>14s} "
                     f"{'GAP-GA %':>10s} {'95% CI':>20s}")
        for tl in sorted(rowsby):
            ga = np.array(rowsby[tl]["GA"])
            gp = np.array(rowsby[tl]["GAP"])
            if not len(ga):
                continue
            rel = 100 * (gp - ga) / np.abs(ga)
            lo, hi = boot_ci(rel)
            lines.append(f"  {tl:8d} {rowsby[tl]['n']:6d} {ga.mean():14.2f} "
                         f"{gp.mean():14.2f} {rel.mean():10.3f} "
                         f"[{lo:8.3f},{hi:8.3f}]")
        lines += ["  Positive GAP-GA % means GAP is worse. A sign change across",
                  "  budgets means the ranking is a budget artefact, not a",
                  "  property of price-aware scheduling.", ""]

    # ---- gaps at the reference budget ------------------------------------
    ref = 60 if 60 in budgets else (budgets[0] if budgets else None)
    if ref is not None:
        rows = [r for r in rows if r["method"] not in ("GA", "GAP")
                or int(r["time_limit"]) == ref]
        lines.append(f"gaps below are at the reference budget tl={ref}s")

    best = defaultdict(lambda: float("inf"))
    for r in rows:
        k = (r["instance"], r["battery_ratio"])
        best[k] = min(best[k], r["objective"])

    lines.append(f"  {'method':6s} {'class':>6s} {'n':>6s} {'gap% mean':>10s} "
                 f"{'gap% p90':>10s} {'norm gap':>9s} {'time s':>9s}")
    lines.append("  gap% diverges where the best-known objective approaches zero "
                 "(negative prices);")
    lines.append("  'norm gap' divides the same difference by a positive "
                 "instance scale instead.")
    agg = defaultdict(list)
    for r in rows:
        b = best[(r["instance"], r["battery_ratio"])]
        gap = 100 * (r["objective"] - b) / abs(b) if b else float("nan")
        sc_ = norm_scale(r)
        ngap = ((r["objective"] - b) / sc_) if math.isfinite(sc_) else float("nan")
        agg[(r["method"], r["size_class"])].append((gap, r["wall_seconds"], ngap))
    for (meth, sc), v in sorted(agg.items()):
        g = np.array([x[0] for x in v])
        t = np.array([x[1] for x in v])
        ng = np.array([x[2] for x in v])
        ng = ng[np.isfinite(ng)]
        lines.append(f"  {meth:6s} {sc:>6s} {len(v):6d} {np.nanmean(g):10.3f} "
                     f"{np.nanpercentile(g,90) if np.isfinite(g).any() else float('nan'):10.3f} "
                     f"{(ng.mean() if len(ng) else float('nan')):9.4f} {t.mean():9.2f}")

    lines.append("\n  gap stability across battery levels (the property that")
    lines.append("  licenses using the solver as a measurement device):")
    for meth in sorted({r["method"] for r in rows}):
        per_b = defaultdict(list)
        for r in rows:
            if r["method"] != meth:
                continue
            b = best[(r["instance"], r["battery_ratio"])]
            if b:
                per_b[r["battery_ratio"]].append(100 * (r["objective"] - b) / abs(b))
        if len(per_b) >= 2:
            means = {k: float(np.mean(v)) for k, v in sorted(per_b.items())}
            spread = max(means.values()) - min(means.values())
            flag = "OK" if spread < 1.0 else "WARNING"
            lines.append(f"    {meth:6s} " +
                         "  ".join(f"b={k:g}:{v:.3f}%" for k, v in means.items()) +
                         f"   spread {spread:.3f}pp  {flag}")
    # ---- gap to a single fixed reference method (MILP where it returns a
    # solution, GA elsewhere) -----------------------------------------------
    # "best solution found by any method" above answers a different question
    # in every cell (sometimes MILP, sometimes whichever metaheuristic did
    # best on that seed); anchoring on one named method instead makes the
    # comparison paper-reportable (Section~\ref{sec:exp-validation}).
    mean_obj = defaultdict(list)
    for r in rows:
        mean_obj[(r["instance"], r["battery_ratio"], r["method"])].append(r["objective"])
    mean_obj = {k: float(np.mean(v)) for k, v in mean_obj.items()}

    anchor = {}
    for (inst, brat, meth), v in mean_obj.items():
        if meth == "MILP":
            anchor[(inst, brat)] = v
    for (inst, brat, meth), v in mean_obj.items():
        if meth == "GA" and (inst, brat) not in anchor:
            anchor[(inst, brat)] = v

    lines.append("\n  gap to a fixed reference method (MILP where it returns")
    lines.append("  a solution, GA elsewhere; 'method' is the reference in")
    lines.append("  cells where it equals the row's own method):")
    lines.append(f"  {'method':6s} {'class':>6s} {'n':>6s} {'norm gap':>9s}")
    agg2 = defaultdict(list)
    for r in rows:
        a = anchor.get((r["instance"], r["battery_ratio"]))
        if a is None:
            continue
        sc_ = norm_scale(r)
        ngap = ((r["objective"] - a) / sc_) if math.isfinite(sc_) else float("nan")
        agg2[(r["method"], r["size_class"])].append(ngap)
    for (meth, sc), v in sorted(agg2.items()):
        arr = np.array([x for x in v if math.isfinite(x)])
        lines.append(f"  {meth:6s} {sc:>6s} {len(v):6d} "
                     f"{(arr.mean() if len(arr) else float('nan')):9.4f}")

    txt = "\n".join(lines) + "\n"
    (out / "e0_validation.txt").write_text(txt)
    return txt


# ---------------------------------------------------------------------------
# E8 / E9 - decomposition
# ---------------------------------------------------------------------------
#
# These two answer, in order:
#
#   Q1  How far from the compact ILP is the decomposition, at equal wall clock?
#   Q2  How much of that distance does the battery post-processing recover?
#   Q3  Does folding the battery LP into the master beat post-processing it?
#
# Everything below is paired on (instance, battery_ratio, time_limit). The
# methods are deterministic and every one of them sees the identical instance
# at the identical budget, so a paired difference is a difference between
# methods and nothing else. There is no seed dimension to collapse.

DECOMP_METHODS = ("MILP", "LBBD", "StateLBBD", "Benders")


def dnum(row: dict, key: str) -> float:
    """A diag_* column as a float; nan when the method did not export it."""
    v = row.get(f"diag_{key}", "")
    if v is None or v == "":
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _decomp_key(r: dict) -> tuple:
    return (r["instance"], r["battery_ratio"], int(r["time_limit"]))


def _by_cell(rows: list[dict]) -> dict:
    """(instance, battery, budget) -> {method: row}. Later rows win, but the
    runlist is unique per (cell, method) so there are none."""
    cells: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        cells[_decomp_key(r)][r["method"]] = r
    return cells


def _paired_norm_diff(cells: dict, hi: str, lo: str, budget: int,
                      value=lambda r: r["objective"], value_lo=None) -> np.ndarray:
    """(value[hi] - value_lo[lo]) / instance scale, over cells holding both.

    Normalised rather than relative because 64 % of the price series contain
    negative hours, so an objective can approach zero and a percentage of it is
    unbounded -- the same reason norm_scale() exists for E0-E4. A value of 0.01
    means "one hundredth of the naive energy bill".

    `value_lo` defaults to `value`, and exists for the one comparison where the
    two sides are not measured the same way: the decomposition's objective
    *before* battery post-processing against the compact ILP's objective, which
    has no such notion and must stay as it is.
    """
    value_lo = value_lo or value
    out = []
    for (_inst, _b, tl), d in cells.items():
        if tl != budget or hi not in d or lo not in d:
            continue
        s = norm_scale(d[lo])
        if not math.isfinite(s):
            continue
        a, b = value(d[hi]), value_lo(d[lo])
        if math.isfinite(a) and math.isfinite(b):
            out.append((a - b) / s)
    return np.array(out)


def _fmt_effect(lines: list[str], label: str, d: np.ndarray, note: str = "") -> None:
    if not len(d):
        lines.append(f"  {label:34s} {'no paired data':>44s}")
        return
    lo, hi = boot_ci(d)
    wins = int(np.sum(d < -1e-12))
    ties = int(np.sum(np.abs(d) <= 1e-12))
    lines.append(f"  {label:34s} n={len(d):4d}  mean={d.mean():+9.5f}  "
                 f"[{lo:+8.5f},{hi:+8.5f}]  better/tie/worse={wins}/{ties}/{len(d)-wins-ties}"
                 + (f"  {note}" if note else ""))


def e8(rows: list[dict], out: Path) -> str:
    rows = [r for r in rows if r["experiment"] == "E8"]
    lines = ["E8 - decomposition vs the compact ILP, at equal wall clock", "=" * 74]
    if not rows:
        return "E8: no data\n"

    cells = _by_cell(rows)
    budgets = sorted({int(r["time_limit"]) for r in rows})
    present = [m for m in DECOMP_METHODS if any(r["method"] == m for r in rows)]
    lines += [f"  budgets: {budgets}", f"  methods present: {present}", ""]

    # ---- 0. is the reference actually a reference? ------------------------
    # "Distance to the compact ILP" only means "distance to the optimum" where
    # the MILP proved optimality. Where it did not, every gap below is a
    # distance to an incumbent and can legitimately be negative.
    lines += ["0. Is the compact ILP a valid reference?", "-" * 74]
    for tl in budgets:
        milp = [r for r in rows if r["method"] == "MILP" and int(r["time_limit"]) == tl]
        if not milp:
            continue
        gaps = np.array([float(r.get("gap") or "nan") for r in milp])
        gaps = gaps[np.isfinite(gaps)]
        proven = int(np.sum(gaps <= 1e-6))
        lines.append(f"  tl={tl:4d}s  n={len(milp):4d}  proved optimal: {proven:4d} "
                     f"({100*proven/max(1,len(milp)):5.1f} %)  mean MIP gap={np.mean(gaps) if len(gaps) else float('nan'):8.4f}")
    lines += ["  Where this is far below 100 %, read the tables below as "
              "'distance to the best", "  the compact ILP managed in the same "
              "time', which is the fair comparison", "  but NOT a distance to "
              "the optimum.", ""]

    # ---- 1. Q1: distance to the compact ILP -------------------------------
    lines += ["1. Distance to the compact ILP (normalised; negative = decomposition wins)",
              "-" * 74]
    for tl in budgets:
        lines.append(f"  budget {tl} s")
        for m in present:
            if m == "MILP":
                continue
            _fmt_effect(lines, f"    {m} - MILP", _paired_norm_diff(cells, m, "MILP", tl))
        lines.append("")

    # ---- 2. Q2: what the battery post-processing recovers ------------------
    # The decomposition's master ignores storage, so "with" and "without"
    # post-processing are the SAME schedule priced two ways -- which is why one
    # run yields both numbers. objective_no_post reconstructs the objective the
    # method would have reported had it stopped before the battery LP.
    def obj_no_post(r: dict) -> float:
        e = dnum(r, "energy_cost_no_battery")
        return e + r["tardiness_cost"] if math.isfinite(e) else float("nan")

    lines += ["2. Value recovered by the battery post-processing", "-" * 74,
              "   Same schedule, priced with and without storage. The master "
              "never saw the",
              "   battery in either case, so this is the ceiling on what "
              "post-processing alone",
              "   can do -- and the benchmark the Benders arm has to beat.", ""]
    for tl in budgets:
        lines.append(f"  budget {tl} s")
        for m in present:
            if m == "MILP":
                continue
            # saving = objective_no_post - objective, per run, normalised
            vals = []
            for (_i, b, t), cell in cells.items():
                if t != tl or m not in cell:
                    continue
                r = cell[m]
                s = norm_scale(r)
                no_post = obj_no_post(r)
                if math.isfinite(s) and math.isfinite(no_post):
                    vals.append((no_post - r["objective"]) / s)
            arr = np.array(vals)
            if len(arr):
                lo, hi = boot_ci(arr)
                lines.append(f"    {m:12s} n={len(arr):4d}  recovered={arr.mean():+9.5f}  "
                             f"[{lo:+8.5f},{hi:+8.5f}]")
        # And the same comparison against the ILP, before post-processing:
        for m in present:
            if m == "MILP":
                continue
            _fmt_effect(lines, f"    {m} (no post) - MILP",
                        _paired_norm_diff(cells, m, "MILP", tl, value=obj_no_post,
                                          value_lo=lambda r: r["objective"]))
        lines.append("")

    # ---- 3. Q3: is the battery better inside the master? -------------------
    lines += ["3. Battery inside the master vs post-processed", "-" * 74,
              "   Benders - StateLBBD isolates the battery coordination: same "
              "explicit-state",
              "   master, storage cut in vs storage post-processed.",
              "   StateLBBD - LBBD isolates the price of dropping the SPACES "
              "switching",
              "   pre-processing, which the Benders cut cannot coexist with.",
              "   Benders - LBBD is the end-to-end question, and is the SUM of "
              "those two",
              "   effects -- which is exactly why it must not be reported "
              "alone.", ""]
    for tl in budgets:
        lines.append(f"  budget {tl} s")
        _fmt_effect(lines, "    Benders - StateLBBD  (battery)",
                    _paired_norm_diff(cells, "Benders", "StateLBBD", tl))
        _fmt_effect(lines, "    StateLBBD - LBBD     (lost SPACES)",
                    _paired_norm_diff(cells, "StateLBBD", "LBBD", tl))
        _fmt_effect(lines, "    Benders - LBBD       (end to end)",
                    _paired_norm_diff(cells, "Benders", "LBBD", tl))
        lines.append("")

    # ---- 4. bound validity -------------------------------------------------
    lines += ["4. Optimality gaps that can actually be quoted", "-" * 74,
              "   Only a bound flagged battery-aware bounds the problem being "
              "solved. LBBD",
              "   and StateLBBD price energy at the raw tariff, so their "
              "master",
              "   bound is an upper bound on the battery-aware cost, not a "
              "lower one, and no",
              "   gap can be computed from it. Benders' theta is a genuine "
              "lower bound.", ""]
    for m in present:
        if m == "MILP":
            lines.append(f"  {m:12s} gap from Gurobi, valid by construction")
            continue
        sub = [r for r in rows if r["method"] == m]
        aware = np.array([dnum(r, "bound_is_battery_aware") for r in sub])
        aware = aware[np.isfinite(aware)]
        if len(aware) and np.all(aware > 0.5):
            g = []
            for r in sub:
                b = dnum(r, "bound")
                s = norm_scale(r)
                if math.isfinite(b) and math.isfinite(s):
                    g.append((r["objective"] - b) / s)
            g = np.array(g)
            lines.append(f"  {m:12s} battery-aware bound; normalised gap "
                         f"mean={g.mean() if len(g) else float('nan'):+9.5f} over n={len(g)}")
        else:
            lines.append(f"  {m:12s} bound NOT battery-aware -- no gap reported (by design)")
    lines.append("")

    # ---- 5. cut economy ----------------------------------------------------
    lines += ["5. Cut economy",
              "-" * 74,
              f"  {'method':12s} {'budget':>7s} {'n':>5s} {'subprob':>9s} {'feas':>8s} "
              f"{'opt':>8s} {'batt':>8s} {'MIS':>7s} {'incon':>7s}"]
    for m in present:
        if m == "MILP":
            continue
        for tl in budgets:
            sub = [r for r in rows if r["method"] == m and int(r["time_limit"]) == tl]
            if not sub:
                continue
            def mean_of(key: str) -> float:
                a = np.array([dnum(r, key) for r in sub])
                a = a[np.isfinite(a)]
                return a.mean() if len(a) else float("nan")
            mis = mean_of("cumul_mifs")
            feas = mean_of("feasibility_cuts")
            lines.append(f"  {m:12s} {tl:7d} {len(sub):5d} {mean_of('subproblems'):9.1f} "
                         f"{feas:8.1f} {mean_of('optimality_cuts'):8.1f} "
                         f"{mean_of('battery_cuts'):8.1f} "
                         f"{(mis/feas if feas else float('nan')):7.2f} "
                         f"{mean_of('inconclusive'):7.2f}")
    lines += ["  'MIS' is the mean size of an infeasibility set: well below the "
              "full EI",
              "  assignment means the conflict refiner is doing useful work.",
              "  'incon' > 0 means subproblems hit their limit and that run's "
              "gap no longer",
              "  certifies anything.", ""]

    # ---- 6. falsification control -----------------------------------------
    # Under a flat tariff there is no arbitrage, so the battery cannot create
    # value and Benders cannot differ from StateLBBD. Whatever difference does
    # appear is the resolution floor of this experiment, and any effect above
    # that is not reportable.
    flat_cells = {k: v for k, v in cells.items()
                  if any(r.get("price_regime") == "flat" for r in v.values())}
    floor = 0.0
    for tl in budgets:
        d = _paired_norm_diff(flat_cells, "Benders", "StateLBBD", tl)
        if len(d):
            floor = max(floor, float(np.max(np.abs(d))))
    lines += ["6. Flat-tariff falsification control", "-" * 74,
              f"  max |Benders - StateLBBD| under a constant price: {floor:.3e}",
              "  This is E8's RESOLUTION FLOOR. Any effect in sections 1-3 "
              "smaller than it",
              "  is indistinguishable from solver noise and must not be "
              "reported as a finding.",
              ""]

    txt = "\n".join(lines) + "\n"
    (out / "e8_decomposition.txt").write_text(txt)
    return txt


def e9(rows: list[dict], out: Path) -> str:
    rows = [r for r in rows if r["experiment"] == "E9"]
    lines = ["E9 - how far the decomposition scales", "=" * 74]
    if not rows:
        return "E9: no data\n"

    budgets = sorted({int(r["time_limit"]) for r in rows})
    present = sorted({r["method"] for r in rows})
    lines += ["  No compact ILP here: it does not survive these sizes, so the",
              "  reference is the best incumbent any decomposition found on the",
              "  same instance at the same budget. The question is therefore",
              "  'which method degrades first', not 'how far from optimal'.",
              f"  budgets: {budgets}   methods: {present}", ""]

    best: dict[tuple, float] = defaultdict(lambda: float("inf"))
    for r in rows:
        best[_decomp_key(r)] = min(best[_decomp_key(r)], r["objective"])

    lines += ["1. Distance to the best known incumbent, by size class", "-" * 74,
              f"  {'class':>6s} {'method':12s} {'budget':>7s} {'n':>5s} "
              f"{'norm gap':>10s} {'p90':>10s} {'wall s':>9s} {'incon %':>8s}"]
    agg = defaultdict(list)
    for r in rows:
        b = best[_decomp_key(r)]
        s = norm_scale(r)
        ngap = (r["objective"] - b) / s if math.isfinite(s) else float("nan")
        inc = dnum(r, "inconclusive")
        agg[(r["size_class"], r["method"], int(r["time_limit"]))].append(
            (ngap, r["wall_seconds"], 1.0 if (math.isfinite(inc) and inc > 0) else 0.0))
    for (sc, m, tl), v in sorted(agg.items(), key=lambda kv: (int(kv[0][0]), kv[0][1], kv[0][2])):
        g = np.array([x[0] for x in v])
        g = g[np.isfinite(g)]
        t = np.array([x[1] for x in v])
        inc = np.array([x[2] for x in v])
        lines.append(f"  {sc:>6s} {m:12s} {tl:7d} {len(v):5d} "
                     f"{(g.mean() if len(g) else float('nan')):10.5f} "
                     f"{(np.percentile(g, 90) if len(g) else float('nan')):10.5f} "
                     f"{t.mean():9.1f} {100*inc.mean():8.1f}")
    lines.append("")

    # ---- 2. where each method stops proving anything ----------------------
    lines += ["2. Frontier: largest size class where the method still closes",
              "-" * 74,
              "   'closes' = returned a solution with a reported gap <= 1e-6 and "
              "no inconclusive",
              "   subproblem. For the arms whose bound is not battery-aware this "
              "is optimality",
              "   for the battery-free problem only -- see E8 section 4.", ""]
    closed = defaultdict(lambda: [0, 0])
    for r in rows:
        gap = float(r.get("gap") or "nan")
        inc = dnum(r, "inconclusive")
        ok = math.isfinite(gap) and gap <= 1e-6 and (not math.isfinite(inc) or inc == 0)
        cell = closed[(r["method"], int(r["time_limit"]), int(r["size_class"]))]
        cell[0] += int(ok)
        cell[1] += 1
    for m in present:
        for tl in budgets:
            frontier = None
            detail = []
            for sc in sorted({int(r["size_class"]) for r in rows}):
                k = (m, tl, sc)
                if k not in closed:
                    continue
                ok, n = closed[k]
                detail.append(f"{sc}:{ok}/{n}")
                if n and ok / n >= 0.5:
                    frontier = sc
            lines.append(f"  {m:12s} tl={tl:4d}s  frontier class={frontier if frontier else '-':>4}"
                         f"   [{' '.join(detail)}]")
    lines += ["", "   'frontier' is the largest class closed on at least half the "
              "instances. A method",
              "   whose frontier does not move as the budget grows is limited by "
              "its formulation,",
              "   not by time -- which is the finding that decides whether the "
              "decomposition was",
              "   worth building.", ""]

    # ---- 3. failure to produce anything -----------------------------------
    lines += ["3. Runs that produced no solution at all", "-" * 74]
    lines.append("   (load_results already drops non-finite objectives; this "
                 "counts what survived)")
    for m in present:
        sub = [r for r in rows if r["method"] == m]
        lines.append(f"  {m:12s} usable runs: {len(sub)}")
    lines.append("")

    txt = "\n".join(lines) + "\n"
    (out / "e9_scaling.txt").write_text(txt)
    return txt
