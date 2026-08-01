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

def load_results(path: Path) -> list[dict]:
    rows = list(csv.DictReader(Path(path).open()))
    out = []
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
        out.append(r)
    return out


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
                  f"{'MVS %/unit':>11s} {'cap MWh':>9s} {'NPV kEUR':>10s} "
                  f"{'payback y':>10s} {'NPV>0':>6s}"]
        prev_mean = 0.0
        for b in ratios:
            present = [i for i in keep if (i, regime, b) in cells]
            if not present:
                continue
            rel = np.array([100 * (base[i] - cells[(i, regime, b)]) / base[i]
                            for i in present])
            lo, hi = boot_ci(rel)

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
                         f"[{lo:8.3f},{hi:8.3f}] {mvs:11.3f} "
                         f"{(float(np.mean(caps)) if caps else float('nan')):9.2f} "
                         f"{med_npv:10.1f} {med_pay:10.2f} {frac_pos:5.0f}%")
            prev_mean = float(np.mean(rel))

        lines += [
            "  MVS   marginal saving (percentage points) per unit of B/E_day.",
            "  NPV   median across instances, thousand EUR, config/economics.py.",
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

    y = np.array([r["saving"] for r in recs])
    X = np.column_stack([np.ones(len(recs)),
                         [r["spread"] for r in recs],
                         [r["cv"] for r in recs],
                         [r["neg"] for r in recs],
                         [r["mean"] for r in recs]])
    clusters = np.array([r["shop"] for r in recs])
    beta, se, r2 = ols_cluster(X, y, clusters)
    names = ["intercept", "spread_intraday", "price_cv", "neg_share", "price_mean"]
    lines += [f"  n = {len(recs)} instance-tariff pairs, "
              f"{len(set(clusters))} shop clusters, R2 = {r2:.4f}",
              "  saving% ~ spread + cv + neg_share + mean "
              "(cluster-robust SE by shop)",
              f"    {'term':18s} {'coef':>10s} {'se':>10s} {'t':>8s}"]
    for nm, b_, s_ in zip(names, beta, se):
        lines.append(f"    {nm:18s} {b_:10.4f} {s_:10.4f} "
                     f"{(b_/s_ if s_ else float('nan')):8.2f}")

    # screening rule: spread at which saving crosses a threshold
    lines.append("")
    for target in (1.0, 2.0, 5.0):
        if beta[1] > 1e-9:
            x = (target - beta[0] - beta[2] * np.mean(X[:, 2])
                 - beta[3] * np.mean(X[:, 3]) - beta[4] * np.mean(X[:, 4])) / beta[1]
            lines.append(f"  screening rule: {target:.0f}% saving requires an "
                         f"intra-day spread of ~{x:.1f} EUR/MWh")
        else:
            lines.append(f"  screening rule for {target:.0f}%: spread coefficient "
                         f"not positive; no usable rule")

    lines.append("\n  by regime:")
    byreg = defaultdict(list)
    for r in recs:
        byreg[r["regime"]].append(r["saving"])
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
# E0 — validation
# ---------------------------------------------------------------------------

def e0(rows: list[dict], out: Path) -> str:
    rows = [r for r in rows if r["experiment"] == "E0"]
    lines = ["E0 - solver validation", "=" * 62]
    if not rows:
        return "E0: no data\n"

    best = defaultdict(lambda: float("inf"))
    for r in rows:
        k = (r["instance"], r["battery_ratio"])
        best[k] = min(best[k], r["objective"])

    lines.append(f"  {'method':6s} {'class':>6s} {'n':>6s} {'gap% mean':>10s} "
                 f"{'gap% p90':>10s} {'time s':>9s}")
    agg = defaultdict(list)
    for r in rows:
        b = best[(r["instance"], r["battery_ratio"])]
        gap = 100 * (r["objective"] - b) / abs(b) if b else float("nan")
        agg[(r["method"], r["size_class"])].append((gap, r["wall_seconds"]))
    for (meth, sc), v in sorted(agg.items()):
        g = np.array([x[0] for x in v])
        t = np.array([x[1] for x in v])
        lines.append(f"  {meth:6s} {sc:>6s} {len(v):6d} {g.mean():10.3f} "
                     f"{np.percentile(g,90):10.3f} {t.mean():9.2f}")

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
    txt = "\n".join(lines) + "\n"
    (out / "e0_validation.txt").write_text(txt)
    return txt
