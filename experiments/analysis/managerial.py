"""
Managerial analyses M0-M5 -- campaign v2.

WHAT THIS FILE IS. analyses.py holds the v1 campaign (E0-E9). v2 replaces the
sequential experiments with six crossed managerial ones (design.py, ENABLED),
and this module is their analysis. It deliberately IMPORTS analyses.py rather
than forking it: load_results, norm_scale, collapse_seeds, boot_ci,
paired_summary, holm, ols_cluster, _npv and _payback are the campaign's
statistical contract, and two copies of a contract is one copy too many. If a
helper here looks like it belongs in analyses.py, move it there rather than
duplicating it.

STATISTICAL CONVENTIONS (identical to analyses.py, restated because every
number below depends on them):

  * The unit of analysis is the INSTANCE. Seeds are collapsed inside
    instance x configuration BEFORE any comparison (A.collapse_seeds, how="mean").
    Seed replication is measurement noise, not sample size; averaging first is
    what stops five seeds from quintupling the apparent n.
  * Every comparison is PAIRED on the instance, with LIST-WISE DELETION of
    incomplete cells, and the retained n is printed for each comparison. A cell
    that is missing because the campaign is still running must shrink the
    sample, never silently shift the estimate.
  * Confidence intervals are bootstrap PERCENTILE intervals, BOOT replicates,
    RESAMPLING INSTANCES (clusters), seeded from design.MASTER_SEED. Where a
    record is an instance x configuration pair rather than an instance, the
    bootstrap resamples instances and carries all of that instance's records,
    i.e. a cluster bootstrap -- otherwise the CI is too narrow by the
    within-instance correlation.
  * Multiple comparisons are controlled with Holm-Bonferroni INSIDE each
    reported family of effects. Families are named in the report so a reader
    can see what was corrected against what.
  * SIGN CONVENTION, stated once and repeated in every docstring that uses it.
    M1, M2 and M4 report SAVINGS on the TOTAL OBJECTIVE (energy + weighted
    tardiness): a POSITIVE number is an ECONOMY, a NEGATIVE number is a
    SURCHARGE. The total and not energy alone, because those three reports feed
    an INVESTMENT decision and a plant pays for late orders too. M5 is the
    exception and deliberately so: it reports signed DELTAS of the two halves
    SEPARATELY (dEnergy, dTard), where NEGATIVE = improvement. The two are
    linked -- M4's V_beta is approximately -(dEnergy + dTard) on a comparable
    arm -- so a negative V_beta under a volatile tariff is a REAL trade-off
    (the battery bought energy and paid for it in tardiness), not a sign bug.
    Nothing in this module takes an absolute value of a saving or flips a sign
    to make a table read better.

  * ZERO VARIANCE IS A CASE, NOT A NUMBER. Whenever an effect is identical in
    every instance -- which happens when it is a fixed fraction of an
    instance-invariant scale -- the paired sd is zero and t, dz and p are
    undefined. They are reported as undefined. Dividing by that sd printed a
    dz of 56077 and, through Holm, a p of 0; likewise a cluster-robust SE
    computed on a single cluster is exactly zero and produced t = 1e15. See
    DEGENERATE_REL and MIN_CLUSTERS.

  * DENOMINATORS. Nothing here is divided by a realised cost. Around two
    thirds of the price series contain negative hours, so a realised cost can
    approach zero and any ratio to it is unbounded -- this produced >100 %
    "savings" and undetermined effect signs in v1. Everything is divided by
    A.norm_scale(row) = e_day x horizon_days x |mean price|, a strictly
    positive, treatment-invariant instance scale. Read "0.10" as "one tenth of
    the naive energy bill of running the EI machine flat out".

ROBUSTNESS CONTRACT. No function in this module raises on missing or partial
data. The campaign is analysed while it is still running, so an absent cell,
an absent column, an absent experiment or an absent price family produces a
report that SAYS SO and continues with what is there. Every public entry point
is wrapped in _guard, which converts an unexpected exception into a report
rather than a traceback that kills the whole analysis stage.

DEPENDENCIES. numpy only, plus an optional scipy import used for exact rank
correlations and t/normal tails; every scipy use has a hand-rolled fallback
because requirements.txt pins numpy alone. statsmodels is never imported: OLS
with cluster-robust standard errors is A.ols_cluster, ten lines of algebra.
"""

from __future__ import annotations

import csv
import functools
import math
import sys
import traceback
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import numpy as np

# The module is imported both as `analysis.managerial` (from bin/05_analyse.py,
# which puts the experiments root on sys.path) and run directly as a script for
# the self-test. Make the package root importable in both cases.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis import analyses as A          # noqa: E402  (see _ROOT bootstrap)
from config import design, economics, machines   # noqa: E402

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

BOOT = 10_000
# The seed for every bootstrap here is the campaign master seed, so that a
# reported interval is reproducible from the one number that reproduces the
# instances and the runlist. analyses.py uses v1's seed; see _master_seed().
SEED = design.MASTER_SEED

W = 78                      # report width; all rules and tables obey it
ALPHA = 0.05

# gap <= this counts as "the MILP proved optimality". 1e-4 rather than 0 because
# Gurobi reports a relative MIP gap and prints a residual on closed models.
PROVEN_GAP = 1e-4

# UNITS. The solver is dimensionless. Every euro figure in this module inherits
# the same interpretation analyses.e2 adopts, restated in each report that uses
# it: 1 solver energy unit == 1 MWh and 1 interval == 1 hour, so the EI machine
# (Proc.cost = 4) is a 4 MW load and "-b 16" is a 16 MWh battery. This is an
# INTERPRETATION of the model as published, not something the solver states.
MWH_PER_ENERGY_UNIT = 1.0

UNITS_NOTE = (
    "  UNITS: 1 solver energy unit == %g MWh, 1 interval == 1 h. The EI machine\n"
    "  is therefore a %g MW load and a battery ratio of 1.0 E_day is that many\n"
    "  MWh. This is an interpretation of the model, not a solver output, and\n"
    "  every EUR figure below inherits it (same convention as analyses.e2)."
    % (MWH_PER_ENERGY_UNIT, machines.E_PROC * MWH_PER_ENERGY_UNIT)
)

# Columns the v2 runlist adds. Absent columns must not raise: a partial CSV
# collected mid-campaign, or a re-analysis of v1 data, simply has fewer factors.
_OPTIONAL_COLS = {
    "machine_profile": "", "rho": "", "restart_level": "", "m1_subdesign": "",
    "tariff_family": "", "price_market": "", "price_year": "", "price_label": "",
    "synth_spread": "", "synth_noise": "", "synth_neg": "",
    "state_policy": "", "price_regime": "", "price_name": "",
    "size_class": "", "ei_density_level": "", "due_tightness_level": "",
    "lam": "", "method": "", "time_limit": "", "gap": "", "battery_arg": "",
    "experiment": "", "seed": "",
}

# Which tariff families count as "a real market price". The spot windows are
# drawn from the reference market year, so they ARE real data even though the
# regime label describes their volatility tercile; only the contractual pair is
# constructed. M2's headline diagnostic contrasts synthetic against these.
REAL_FAMILIES = ("real", "spot")


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def _num(row: dict, key: str, default: float = float("nan")) -> float:
    """A column as a float; `default` when absent, empty or unparseable."""
    v = row.get(key, "")
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _nanmean(values) -> float:
    """np.nanmean without the all-NaN warning: an empty or entirely missing
    covariate is a normal state of a campaign that has not finished."""
    a = np.array([v for v in values if isinstance(v, (int, float))
                  and math.isfinite(v)], dtype=float)
    return float(a.mean()) if len(a) else float("nan")


def _lvlkey(x):
    """Sort key that orders numeric-looking labels numerically, others by text."""
    try:
        return (0, float(x), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(x))


def _g(x, fmt="{:g}"):
    return fmt.format(x)


@contextmanager
def _master_seed():
    """Run A.boot_ci / A.paired_summary under THIS campaign's seed.

    analyses.RNG_SEED is v1's master seed and must keep reproducing v1's
    intervals when 05_analyse runs E0-E9 in the same process. Rather than
    copying boot_ci (the instruction is to reuse, not fork), the seed is
    swapped for the duration of the call and restored. Single-threaded by
    construction; nothing in this package runs analyses concurrently.
    """
    old = A.RNG_SEED
    A.RNG_SEED = SEED
    try:
        yield
    finally:
        A.RNG_SEED = old


def boot_ci(x, alpha: float = ALPHA) -> tuple[float, float]:
    x = np.asarray([v for v in np.asarray(x, dtype=float) if math.isfinite(v)],
                   dtype=float)
    if len(x) == 0:
        return (float("nan"), float("nan"))
    with _master_seed():
        return A.boot_ci(x, alpha=alpha, n=BOOT)


def _p_from_t(t: float, df: float) -> float:
    """Two-sided p from a Student t with df degrees of freedom.

    A.two_sided_p_from_t is a NORMAL approximation and its docstring says "df
    here is always in the hundreds", which is true of v1 but false here: the
    df in this module is the number of paired INSTANCES or the number of SHOP
    CLUSTERS minus one, and mid-campaign that is single digits. On the smoke
    data the normal tail turned a t of 25 on n = 9 instances into p = 3e-115, a
    number no design with nine clusters can support. scipy when available; the
    fallback is the regularised incomplete beta, which is the exact tail.
    """
    if not (isinstance(t, (int, float)) and math.isfinite(t)):
        return float("nan")
    if not (isinstance(df, (int, float)) and math.isfinite(df)) or df < 1:
        return float("nan")
    try:                                    # local import: scipy is optional
        from scipy import stats as _st
        return float(2.0 * _st.t.sf(abs(float(t)), df))
    except Exception:
        return _betainc_reg(df / 2.0, 0.5, df / (df + float(t) ** 2))


def _betainc_reg(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b), by the Lentz continued fraction.

    Only used when scipy is absent; requirements.txt pins numpy alone, so that
    is the case the campaign actually ships.
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    front = math.exp(a * math.log(x) + b * math.log(1 - x) - lbeta)
    if x > (a + 1) / (a + b + 2):
        return 1.0 - _betainc_reg(b, a, 1 - x)
    tiny = 1e-30
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        if abs(d) < tiny:
            d = tiny
        d = 1.0 / d
        c = 1.0 + num / c
        if abs(c) < tiny:
            c = tiny
        f *= c * d
        if abs(1.0 - c * d) < 1e-12:
            break
    return min(1.0, max(0.0, front * (f - 1.0) / a))


# A standard error, a standard deviation or a bootstrap width at or below this
# multiple of the estimate is numerically zero, not small: it means the model
# fits exactly, every paired difference is identical, or there is a single
# cluster. Dividing by it manufactures the t = 1e17 and dz = 56077 that the
# smoke run printed. The estimate is then DETERMINISTIC and has no sampling
# distribution, which is a statement, not a missing number.
DEGENERATE_REL = 1e-9

# An effect whose spread across instances is below this FRACTION of its own
# mean is constant for every practical purpose: it varies by less than 0.1 %
# from one instance to the next. A t-test on it is not asking "is this effect
# real in the population", it is asking "is this constant different from zero",
# and it answers yes with an effect size of five figures. M4's V_sigma is
# exactly that: it is a fixed fraction of a treatment-invariant scale, so every
# instance returns the same number and dz came out at 56077 with p = 0. Past
# this bound the estimate is reported as DETERMINISTIC and carries no dz, no t
# and no p -- which is a stronger statement than any p-value, not a weaker one.
DEGENERATE_CV = 1e-3


def _degenerate(spread: float, level: float) -> bool:
    """Is `spread` (an sd or an se) numerically zero next to `level` (a mean)?

    Two ways of being zero, and the campaign produces both. ABSOLUTE: a single
    cluster, or a model that fits exactly, gives an se of ~1e-18 -- that is
    round-off, and it turned a coefficient of 0.0122 into a t of 2.5e15.
    RELATIVE: an effect identical in every instance to within a thousandth
    gives an sd that is real but carries no information about sampling.
    """
    if not math.isfinite(spread) or spread <= 0.0:
        return True
    if spread <= DEGENERATE_REL * max(abs(level), 1.0):
        return True
    return math.isfinite(level) and spread <= DEGENERATE_CV * abs(level)


def _ratio(num: float, den: float, level: float | None = None) -> float:
    """num/den, but NaN when den is numerically zero (see DEGENERATE_REL)."""
    if not (math.isfinite(num) and math.isfinite(den)):
        return float("nan")
    return float("nan") if _degenerate(den, num if level is None else level) \
        else num / den


def paired_summary(d, label: str) -> dict:
    """A.paired_summary with ZERO VARIANCE treated as a case, not a number.

    When every paired difference is identical -- which happens whenever the
    planted effect is a fixed fraction of an instance-invariant scale, and
    which is exactly what M4's V_sigma is -- the sample sd is 0 (or a few
    float ulps). A.paired_summary then divides by it and returns dz = 56077 and
    t = 1e5, and the Holm block below turns those into p = 0. Here t and dz
    become NaN instead, and `degenerate` records why, so the report can say
    "no dispersion" rather than print a spuriously enormous effect size.
    """
    d = np.asarray([v for v in np.asarray(d, dtype=float) if math.isfinite(v)],
                   dtype=float)
    if len(d) == 0:
        return dict(effect=label, n=0, mean=float("nan"), sd=float("nan"),
                    ci_lo=float("nan"), ci_hi=float("nan"), t=float("nan"),
                    cohens_dz=float("nan"), degenerate=True)
    with _master_seed():
        st = A.paired_summary(d, label)
    st["degenerate"] = bool(len(d) < 2 or _degenerate(st["sd"], st["mean"]))
    if st["degenerate"]:
        st["t"] = float("nan")
        st["cohens_dz"] = float("nan")
    return st


def _holm(pvals: dict[str, float]) -> dict[str, float]:
    """A.holm, but non-finite p-values are excluded instead of becoming zero.

    A.holm computes max(prev, (m-i)*p); with p = nan that comparison is False
    in Python, so prev is kept and a NaN p-value silently emerges as an
    adjusted p of 0 -- i.e. an effect with zero variance (every paired
    difference identical, so t is undefined) is reported as maximally
    significant. The self-test hit exactly that on M5's tardiness shift. Here a
    non-estimable p stays non-estimable, and it is also excluded from the
    family size so it does not deflate everyone else's correction.
    """
    finite = {k: v for k, v in pvals.items() if isinstance(v, float) and math.isfinite(v)}
    adj = A.holm(finite) if finite else {}
    for k in pvals:
        adj.setdefault(k, float("nan"))
    return adj


# A cluster-robust standard error is asymptotic IN THE NUMBER OF CLUSTERS. With
# a handful of shops it is not merely imprecise, it is biased towards zero, and
# with a single cluster the meat matrix is rank one and the SE collapses to
# machine epsilon. Below this many clusters the coefficients are still printed
# (they are the point estimates the campaign is accumulating) but no t and no
# p is offered, because there is no sampling distribution to quote.
MIN_CLUSTERS = 5


def ols_cluster(X, y, clusters):
    """A.ols_cluster plus the degeneracies a mid-campaign table actually hits.

    Returns (beta, se, r2, n_clusters, usable).  `usable` is False when there
    are fewer than MIN_CLUSTERS clusters; `se` entries that are numerically
    zero -- a single cluster, or a design the model fits exactly, both of which
    the smoke run produced -- are returned as NaN rather than as 1e-18, which
    is what turned a coefficient of 0.0122 into a t of 2.5e15.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    clusters = np.asarray(clusters)
    G = int(len(np.unique(clusters))) if len(clusters) else 0
    beta, se, r2 = A.ols_cluster(X, y, clusters)
    se = np.asarray(se, dtype=float).copy()
    for i in range(len(se)):
        if _degenerate(se[i], beta[i]):
            se[i] = float("nan")
    return beta, se, r2, G, G >= MIN_CLUSTERS


def _cluster_note(G: int, usable: bool, what: str = "shop") -> list[str]:
    """The line that must accompany any table fitted on too few clusters."""
    if usable:
        return []
    return [f"  ONLY {G} {what} CLUSTER(S). Cluster-robust standard errors are",
            f"  asymptotic in the number of clusters; below {MIN_CLUSTERS} they",
            "  are biased towards zero and at one they are exactly zero. The",
            "  coefficients below are point estimates only: t and p are NOT",
            "  reported rather than reported as certainties."]


def _coef_t(beta: float, se: float, usable: bool = True) -> float:
    """t for one cluster-robust coefficient, NaN when there is no distribution.

    Printed alongside the coefficient, so it must obey the same rule as the
    p-value: a table that says "t and p are NOT reported" and then prints
    t = -62 is worse than either choice on its own.
    """
    return _ratio(beta, se, beta) if usable else float("nan")


def _coef_p(beta: float, se: float, df: float, usable: bool = True) -> float:
    """Two-sided p for one cluster-robust coefficient, NaN when not estimable."""
    if not usable:
        return float("nan")
    return _p_from_t(_ratio(beta, se, beta), df)


def _cluster_boot(stat, n_clusters: int, n: int = BOOT, chunk: int = 500):
    """Cluster bootstrap driver.

    `stat(idx)` receives an integer array of shape (k, n_clusters) of resampled
    CLUSTER indices and returns an array of shape (k,) or (k, m). Chunked so
    that BOOT x n_clusters index matrices never materialise in full: at 10,000
    replicates and a few thousand instances that is otherwise hundreds of MB
    for a confidence interval.
    """
    if n_clusters <= 0:
        return np.zeros((0,))
    rng = np.random.default_rng(SEED)
    parts, done = [], 0
    while done < n:
        k = min(chunk, n - done)
        idx = rng.integers(0, n_clusters, size=(k, n_clusters))
        parts.append(np.asarray(stat(idx), dtype=float))
        done += k
    return np.concatenate(parts, axis=0)


def _cluster_ci_mean(vals, cluster_ids, alpha: float = ALPHA):
    """Mean and percentile CI of a mean, resampling CLUSTERS (instances).

    Records are instance x configuration, so several records share an instance
    and are correlated. Resampling records would understate the interval; this
    resamples instances and carries every record of a drawn instance.
    """
    vals = np.asarray(vals, dtype=float)
    ok = np.isfinite(vals)
    vals, cluster_ids = vals[ok], np.asarray(cluster_ids)[ok]
    if len(vals) == 0:
        return float("nan"), float("nan"), float("nan"), 0, 0
    uniq, inv = np.unique(cluster_ids, return_inverse=True)
    s = np.bincount(inv, weights=vals, minlength=len(uniq))
    c = np.bincount(inv, minlength=len(uniq)).astype(float)

    def stat(idx):
        return s[idx].sum(axis=1) / np.maximum(c[idx].sum(axis=1), 1e-12)

    draws = _cluster_boot(stat, len(uniq))
    lo = float(np.percentile(draws, 100 * alpha / 2)) if len(draws) else float("nan")
    hi = float(np.percentile(draws, 100 * (1 - alpha / 2))) if len(draws) else float("nan")
    return float(vals.mean()), lo, hi, len(vals), len(uniq)


def _matrix_boot(M, fn, alpha: float = ALPHA):
    """Percentile CI for fn(column means) of a complete instances x levels matrix.

    Used for statistics that are not a mean -- the range of the GA gap across
    battery levels (M0), the substitution index (M4). The matrix must already
    be list-wise complete, so a bootstrap draw of rows is a draw of instances.
    """
    M = np.asarray(M, dtype=float)
    if M.ndim != 2 or M.shape[0] == 0:
        return float("nan"), float("nan"), float("nan")
    point = float(fn(M.mean(axis=0)))

    def stat(idx):
        return np.array([fn(M[row].mean(axis=0)) for row in idx])

    draws = _cluster_boot(stat, M.shape[0])
    draws = draws[np.isfinite(draws)]
    if not len(draws):
        return point, float("nan"), float("nan")
    return (point, float(np.percentile(draws, 100 * alpha / 2)),
            float(np.percentile(draws, 100 * (1 - alpha / 2))))


def _spearman(x, y) -> tuple[float, float]:
    """Spearman rho and a two-sided p. scipy when available, else a t approx."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 4:
        return float("nan"), float("nan")
    try:                                    # local import: scipy is optional
        from scipy import stats as _st
        r = _st.spearmanr(x, y)
        return float(r.statistic), float(r.pvalue)
    except Exception:
        rx = np.argsort(np.argsort(x)).astype(float)
        ry = np.argsort(np.argsort(y)).astype(float)
        rho = float(np.corrcoef(rx, ry)[0, 1])
        if not math.isfinite(rho) or abs(rho) >= 1:
            return rho, 0.0
        t = rho * math.sqrt((len(x) - 2) / (1 - rho ** 2))
        return rho, A.two_sided_p_from_t(t, len(x) - 2)


def _pearson(x, y) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or np.std(x[ok]) == 0 or np.std(y[ok]) == 0:
        return float("nan")
    return float(np.corrcoef(x[ok], y[ok])[0, 1])


def _vif(Z, names) -> list[tuple[str, float]]:
    """Variance inflation factors of a standardised covariate block.

    Kept visible rather than buried: v1's price regression carried VIFs near
    9.5, which means the individual coefficients were not separately identified
    and their ranking (and any sign flip on the weaker one) was an artefact.
    """
    Z = np.asarray(Z, dtype=float)
    out = []
    if Z.shape[0] < Z.shape[1] + 2:
        return [(nm, float("nan")) for nm in names]
    sd = Z.std(axis=0)
    sd[sd == 0] = 1.0
    Zs = (Z - Z.mean(axis=0)) / sd
    for i, nm in enumerate(names):
        others = np.delete(Zs, i, axis=1)
        if others.shape[1] == 0:
            out.append((nm, 1.0))
            continue
        coef, *_ = np.linalg.lstsq(others, Zs[:, i], rcond=None)
        resid = Zs[:, i] - others @ coef
        ss = float(((Zs[:, i] - Zs[:, i].mean()) ** 2).sum())
        r2 = 1 - float(resid @ resid) / ss if ss else 0.0
        out.append((nm, 1 / (1 - r2) if r2 < 1 else float("inf")))
    return out


def _sum_to_zero(labels):
    """Sum-to-zero (effect) coding of a categorical factor.

    Effect coding rather than dummy coding so that the sequential sums of
    squares below are the usual balanced-design main effects, and so that a
    main effect is not silently defined "relative to whichever level happened
    to sort first".
    """
    levels = sorted(set(labels), key=_lvlkey)
    k = len(levels)
    if k < 2:
        return np.zeros((len(labels), 0)), levels
    pos = {lv: i for i, lv in enumerate(levels)}
    X = np.zeros((len(labels), k - 1))
    for r, lab in enumerate(labels):
        j = pos[lab]
        if j < k - 1:
            X[r, j] = 1.0
        else:
            X[r, :] = -1.0
    return X, levels


def _inter(Xa, Xb):
    if Xa.shape[1] == 0 or Xb.shape[1] == 0:
        return np.zeros((Xa.shape[0], 0))
    return np.column_stack([Xa[:, i] * Xb[:, j]
                            for i in range(Xa.shape[1])
                            for j in range(Xb.shape[1])])


def _ss_resid(X, y) -> float:
    if X.shape[1] == 0:
        return float(((y - y.mean()) ** 2).sum())
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ b
    return float(r @ r)


def _variance_decomposition(y, blocks) -> list[tuple[str, float, int]]:
    """Sequential (type I) sums of squares over effect-coded blocks.

    The design is crossed but not necessarily balanced once incomplete cells
    are deleted, so the shares depend on the ENTRY ORDER, which is why the
    order is fixed and printed (main effects, then two-way, then three-way).
    Reported as shares of total SS, which is what a manager reads as "how much
    of the variation in the return is explained by the tariff".
    """
    y = np.asarray(y, dtype=float)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    X = np.ones((len(y), 1))
    prev = _ss_resid(X, y)
    out = []
    for name, B in blocks:
        if B.shape[1] == 0:
            out.append((name, float("nan"), 0))
            continue
        X = np.hstack([X, B])
        cur = _ss_resid(X, y)
        out.append((name, (prev - cur) / ss_tot if ss_tot else float("nan"),
                    B.shape[1]))
        prev = cur
    out.append(("residual", prev / ss_tot if ss_tot else float("nan"),
                len(y) - X.shape[1]))
    return out


# ---------------------------------------------------------------------------
# report / io plumbing
# ---------------------------------------------------------------------------

def _rule(ch: str = "=") -> str:
    return ch * W


def _hdr(title: str) -> list[str]:
    return [title, _rule("=")]


def _sec(title: str) -> list[str]:
    return ["", _rule("-"), title, _rule("-")]


def _ci(lo: float, hi: float, w: int = 9, p: int = 4) -> str:
    return f"[{lo:{w}.{p}f},{hi:{w}.{p}f}]"


def _write_csv(out: Path, name: str, rows: list[dict]) -> None:
    """Write an intermediate table. Never fatal: a read-only output directory
    must not lose the report that is already computed."""
    try:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        if not rows:
            (out / name).write_text("")
            return
        cols: list[str] = []
        for r in rows:
            for c in r:
                if c not in cols:
                    cols.append(c)
        with (out / name).open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    except OSError as exc:                      # pragma: no cover - io only
        print(f"WARNING: could not write {name}: {exc}", file=sys.stderr)


def _emit(out: Path, name: str, lines: list[str]) -> str:
    txt = "\n".join(lines) + "\n"
    try:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / name).write_text(txt)
    except OSError as exc:                      # pragma: no cover - io only
        print(f"WARNING: could not write {name}: {exc}", file=sys.stderr)
    return txt


def _guard(fn):
    """Turn any unexpected exception into a report.

    The campaign is analysed while it is still running, so the analysis stage
    meets shapes of data nobody anticipated. A traceback that aborts
    05_analyse.py loses the five reports that WOULD have been produced; a
    report that says "this one failed, here is where" loses nothing.
    """
    @functools.wraps(fn)
    def wrapper(rows, out, **kw):
        try:
            return fn(rows, out, **kw)
        except Exception:                        # noqa: BLE001 - deliberate
            tb = traceback.format_exc(limit=8)
            lines = _hdr(f"{fn.__name__.upper()} - FAILED (analysis bug, not a result)")
            lines += ["", "  This function raised. The failure is reported instead of",
                      "  propagating, so the remaining analyses still run. Fix and",
                      "  re-run; nothing below should be quoted.", ""]
            lines += ["  " + ln for ln in tb.strip().splitlines()]
            return _emit(out, f"{fn.__name__}_FAILED.txt", lines)
    return wrapper


# ---------------------------------------------------------------------------
# data preparation
# ---------------------------------------------------------------------------

def _family_from_regime(regime: str, price_name: str = "") -> str:
    """Fallback tariff family when the runlist did not record one."""
    r = (regime or "").lower()
    p = (price_name or "").lower()
    if r in ("flat", "tou2") or r == "contractual":
        return "contractual"
    if r.startswith("spot"):
        return "spot"
    if "synth" in r or "synth" in p:
        return "synthetic"
    if r:
        return "real"
    return ""


def _prep(rows: list[dict], experiments: tuple[str, ...]) -> list[dict]:
    """Filter to the wanted experiments and fill in optional v2 columns.

    Copies are made so that a missing column can be defaulted without mutating
    the caller's rows (05_analyse passes the SAME list to every analysis).
    """
    keep = []
    for r in rows:
        if r.get("experiment") not in experiments:
            continue
        d = dict(r)
        for k, v in _OPTIONAL_COLS.items():
            if d.get(k) in (None, ""):
                d[k] = v
        if not d.get("tariff_family"):
            d["tariff_family"] = _family_from_regime(d.get("price_regime", ""),
                                                     d.get("price_name", ""))
        if not d.get("price_regime"):
            d["price_regime"] = d.get("price_name", "") or "?"
        if not d.get("size_class"):
            d["size_class"] = str(r.get("inst_size_class", "") or "")
        d["battery_ratio"] = _num(d, "battery_ratio", 0.0)
        keep.append(d)
    return keep


def _meta(R: list[dict]) -> dict[str, dict]:
    """instance -> its treatment-invariant attributes, taken once."""
    m: dict[str, dict] = {}
    for r in R:
        i = r.get("instance", "")
        if not i or i in m:
            continue
        shop = r.get("inst_shop_id") or i
        m[i] = dict(
            shop=shop,
            # lambda is baked into the shop id, so stripping it gives a key
            # that identifies the same physical structure across M5's lambda
            # ladder -- the only way to pair a frontier point to point.
            struct=str(shop).rsplit("_lam", 1)[0],
            scale=A.norm_scale(r),
            e_day=_num(r, "inst_e_day"),
            horizon=_num(r, "inst_horizon"),
            horizon_days=_num(r, "inst_horizon_days"),
            n=_num(r, "inst_n"),
            spread=_num(r, "inst_spread_intraday"),
            cv=_num(r, "inst_price_cv"),
            neg=_num(r, "inst_neg_share"),
            pmean=_num(r, "inst_price_mean"),
            ei_density=_num(r, "inst_ei_density"),
            due_slack=_num(r, "inst_mean_due_slack"),
            size_class=str(r.get("size_class") or r.get("inst_size_class") or ""),
            ei_level=str(r.get("ei_density_level")
                         or r.get("inst_ei_density_level") or ""),
            tight_level=str(r.get("due_tightness_level")
                            or r.get("inst_due_tightness_level") or ""),
            regime=str(r.get("price_regime") or ""),
            family=str(r.get("tariff_family") or ""),
            label=str(r.get("price_label") or ""),
            market=str(r.get("price_market") or ""),
            year=str(r.get("price_year") or ""),
            lam=_num(r, "lam", _num(r, "inst_lam")),
        )
    return m


def _inventory(R: list[dict], meta: dict) -> list[str]:
    """A short 'what is actually in this slice' block, printed by every report.

    Reading a table without knowing that half the design has not finished
    running is how a partial campaign gets quoted as a result.
    """
    def uniq(key):
        return sorted({str(r.get(key, "")) for r in R if r.get(key, "") != ""},
                      key=_lvlkey)
    return [
        f"  runs {len(R)}   instances {len({r['instance'] for r in R})}   "
        f"shops {len({meta[i]['shop'] for i in {r['instance'] for r in R}})}",
        f"  methods       {', '.join(uniq('method')) or '-'}",
        f"  size classes  {', '.join(uniq('size_class')) or '-'}",
        f"  tariffs       {', '.join(uniq('price_regime')) or '-'}",
        f"  battery       {', '.join(_g(b) for b in sorted({r['battery_ratio'] for r in R}))}",
        f"  seeds         {', '.join(uniq('seed')) or '-'}",
    ]


def _cells(R: list[dict], keys: tuple[str, ...], value: str = "objective") -> dict:
    """A.collapse_seeds with how='mean', guarded against a missing column.

    how='mean' and not 'best': best-of-k is biased upward in k, and k differs
    between M0's anytime sub-cell and the rest of the campaign. Comparing
    configurations on best-of-k would compare sampling budgets.
    """
    usable = [r for r in R if all(k in r for k in keys) and value in r]
    if not usable:
        return {}
    return A.collapse_seeds(usable, keys, value=value, how="mean")


# ---------------------------------------------------------------------------
# M0 -- validation
# ---------------------------------------------------------------------------

@_guard
def m0(rows: list[dict], out: Path, **kw) -> str:
    """Is the GA close enough to the optimum to be used as a measuring device?

    Everything downstream is a DIFFERENCE between two GA runs. That is only a
    difference between two configurations if the GA's error is (a) small and
    (b) THE SAME in both configurations. (b) is the load-bearing half and it is
    the one nobody checks: an error that grows with battery capacity turns part
    of every measured storage benefit into an algorithmic artefact. The battery
    stability section is therefore the decisive test of this whole campaign,
    not the headline mean gap.
    """
    R = _prep(rows, ("M0",))
    L = _hdr("M0 - validation: GA against the compact MILP")
    if not R:
        return _emit(out, "m0_validation.txt",
                     L + ["", "  NO M0 DATA. Nothing downstream is licensed yet.",
                          "  (Either the M0 cells have not run, or `experiment` "
                          "is not 'M0'.)"])
    meta = _meta(R)
    L += _inventory(R, meta)

    # Which GA budget is "the campaign". M0 also carries the 30 s / 900 s
    # anytime sub-cell (design.TL_PROFILE_EXTRA); mixing those into the headline
    # gap would report an average over budgets nobody runs at.
    tls = [int(_num(r, "time_limit", 0)) for r in R if r.get("method") == "GA"]
    tl_ref = design.TL_GA if design.TL_GA in tls else (
        max(set(tls), key=tls.count) if tls else 0)
    L += [f"  GA reference budget: {tl_ref} s "
          f"(design.TL_GA = {design.TL_GA}); other budgets are the anytime cell."]

    main = [r for r in R if r.get("method") != "GA"
            or int(_num(r, "time_limit", tl_ref)) == tl_ref]
    cells = _cells(main, ("instance", "battery_ratio", "method"))
    methods = sorted({r["method"] for r in main if r.get("method")})
    ratios = sorted({r["battery_ratio"] for r in main})

    # ---- 1. is the reference a reference? ---------------------------------
    # "Gap to the MILP" is only "gap to the optimum" where the MILP CLOSED. The
    # two populations behave differently and are never pooled below.
    L += _sec("1. Does the MILP prove optimality? (the two populations differ)")
    proven: dict[tuple, bool] = {}
    milp_gaps = defaultdict(list)
    for r in main:
        if r.get("method") != "MILP":
            continue
        g = _num(r, "gap")
        key = (r["instance"], r["battery_ratio"])
        milp_gaps[meta[r["instance"]]["size_class"]].append(g)
        proven[key] = bool(math.isfinite(g) and g <= PROVEN_GAP)
    if not milp_gaps:
        L += ["  No MILP rows. Every 'gap' below is a gap to the best method",
              "  present, which is NOT a distance to the optimum."]
    else:
        L.append(f"  {'class':>7s} {'n':>6s} {'proved opt':>12s} {'share':>8s} "
                 f"{'mean MIP gap':>13s}")
        prov_rows = []
        for sc in sorted(milp_gaps, key=_lvlkey):
            g = np.array([x for x in milp_gaps[sc] if math.isfinite(x)])
            k = int((g <= PROVEN_GAP).sum()) if len(g) else 0
            share = 100.0 * k / len(g) if len(g) else float("nan")
            L.append(f"  {sc:>7s} {len(milp_gaps[sc]):6d} {k:12d} {share:7.1f}% "
                     f"{(g.mean() if len(g) else float('nan')):13.5f}")
            prov_rows.append(dict(size_class=sc, n=len(milp_gaps[sc]),
                                  proved=k, share_pct=share,
                                  mean_mip_gap=float(g.mean()) if len(g) else ""))
        _write_csv(out, "m0_milp_optimality.csv", prov_rows)
        L += ["  Where this share is below 100 %, the tables below measure a",
              "  distance to the best the MILP managed in 1800 s, not to the",
              "  optimum. The two are reported separately and never averaged."]

    # ---- 2. relative gap by size class and tariff regime ------------------
    L += _sec("2. GA gap to the MILP, paired on the instance")
    L += ["  rel %   100 x (GA - MILP) / |MILP|. Diverges when the MILP cost",
          "          approaches zero under negative prices; kept because it is",
          "          the quantity a referee expects, flagged when unstable.",
          "  norm %  the same difference over A.norm_scale (positive, treatment-",
          "          invariant). Where the two disagree, this one is the truth.",
          "  CI      95 % percentile bootstrap over INSTANCES (clusters)."]

    recs = []
    for (inst, brat, meth), v in cells.items():
        if meth == "MILP":
            continue
        ref = cells.get((inst, brat, "MILP"))
        if ref is None:
            continue
        sc = meta[inst]["scale"]
        recs.append(dict(
            instance=inst, shop=meta[inst]["shop"], method=meth,
            battery_ratio=brat, size_class=meta[inst]["size_class"],
            regime=meta[inst]["regime"],
            proven=proven.get((inst, brat), False),
            rel=100.0 * (v - ref) / abs(ref) if ref else float("nan"),
            norm=100.0 * (v - ref) / sc if math.isfinite(sc) and sc > 0 else float("nan")))
    _write_csv(out, "m0_gaps.csv", recs)

    if not recs:
        L += ["", "  NO PAIRED GA/MILP CELLS. The validation is not available:",
              "  either the MILP has not returned on any instance the GA also",
              "  ran, or the two arms did not share a battery level."]
    else:
        fam_p: dict[str, float] = {}
        for popname, sel in (("proven optimal", lambda x: x["proven"]),
                             ("MILP not closed", lambda x: not x["proven"])):
            sub = [x for x in recs if sel(x)]
            if not sub:
                L += ["", f"  population '{popname}': empty"]
                continue
            L += ["", f"  population: {popname}  (n = {len(sub)} instance x "
                      f"battery cells)"]
            for grouper, title in (("size_class", "by size class"),
                                   ("regime", "by tariff regime")):
                L.append(f"    {title}")
                L.append(f"      {'method':6s} {'level':>12s} {'n':>6s} {'inst':>6s} "
                         f"{'rel %':>9s} {'norm %':>9s} {'95% CI (norm)':>22s}")
                for meth in methods:
                    if meth == "MILP":
                        continue
                    lv = sorted({x[grouper] for x in sub}, key=_lvlkey)
                    for level in lv:
                        s = [x for x in sub if x["method"] == meth
                             and x[grouper] == level]
                        if not s:
                            continue
                        mn, lo, hi, n, ninst = _cluster_ci_mean(
                            [x["norm"] for x in s], [x["instance"] for x in s])
                        rel = np.array([x["rel"] for x in s], dtype=float)
                        rel = rel[np.isfinite(rel)]
                        L.append(f"      {meth:6s} {str(level):>12s} {n:6d} {ninst:6d} "
                                 f"{(rel.mean() if len(rel) else float('nan')):9.3f} "
                                 f"{mn:9.4f} {_ci(lo, hi)}")
                        if meth == "GA" and grouper == "size_class":
                            # The test is on the INSTANCE, so the records are
                            # averaged within instance first. Testing the
                            # instance x battery records directly would count
                            # every battery level as an independent
                            # observation and inflate t by sqrt(levels), while
                            # the df below already says "instances".
                            per_i = defaultdict(list)
                            for x in s:
                                if math.isfinite(x["norm"]):
                                    per_i[x["instance"]].append(x["norm"])
                            di = [float(np.mean(v)) for v in per_i.values()]
                            sti = paired_summary(di, "x")
                            fam_p[f"{popname}|class {level}"] = _p_from_t(
                                sti["t"], max(1, sti["n"] - 1))
        if fam_p:
            adj = _holm(fam_p)
            L += ["", "    Holm-adjusted p (family: GA gap != 0, one test per "
                      "size class x population)"]
            for k in sorted(adj, key=lambda x: adj[x]):
                L.append(f"      {k:44s} p_holm = {adj[k]:.4g}")

    # ---- 3. gap stability across battery levels ---------------------------
    # THE DECISIVE TEST. If the GA's error is larger with storage than without,
    # part of every "value of storage" number in M1-M5 is the GA's own
    # degradation. The quantity reported is the RANGE of the mean gap across
    # battery levels, with a CI, because a range is what "the error is the same
    # everywhere" actually claims.
    L += _sec("3. DECISIVE: is the GA's error the same at every battery level?")
    L += ["  If the gap is larger with storage than without, a share of every",
          "  measured storage benefit downstream is an algorithmic artefact and",
          "  not a property of the battery. Range = max - min of the mean gap",
          "  over battery levels, paired within instance (list-wise complete)."]
    stab_rows = []
    if len(ratios) < 2:
        L.append("  Only one battery level present; not testable.")
    else:
        for meth in methods:
            if meth == "MILP":
                continue
            per = defaultdict(dict)
            for x in recs:
                if x["method"] == meth:
                    per[x["instance"]][x["battery_ratio"]] = x["norm"]
            complete = [i for i, d in per.items()
                        if all(b in d and math.isfinite(d[b]) for b in ratios)]
            if len(complete) < 3:
                L.append(f"  {meth:6s} only {len(complete)} instances complete "
                         f"across all battery levels; not testable.")
                continue
            M = np.array([[per[i][b] for b in ratios] for i in complete])
            means = M.mean(axis=0)
            rng_pt, rng_lo, rng_hi = _matrix_boot(M, lambda v: float(v.max() - v.min()))
            L.append(f"  {meth:6s} n={len(complete):4d} instances   " +
                     "  ".join(f"b={_g(b)}:{m:.4f}%" for b, m in zip(ratios, means)))
            # The verdict threshold is one tenth of the smallest effect the
            # campaign intends to report. A range at or above the effects in
            # M1 would make those effects unreadable.
            # A non-finite upper bound is NOT an unstable gap: it is a gap
            # whose interval could not be computed. Falling through the
            # comparisons would have printed the worst verdict on the thinnest
            # evidence, which is the opposite of what this section is for.
            verdict = ("NOT TESTABLE" if not math.isfinite(rng_hi) else
                       "STABLE" if rng_hi < 0.05 else
                       "MARGINAL" if rng_hi < 0.20 else "UNSTABLE -- see note")
            L.append(f"         range {rng_pt:.4f}% of the naive bill  "
                     f"{_ci(rng_lo, rng_hi)}   {verdict}")
            stab_rows.append(dict(method=meth, n_instances=len(complete),
                                  range_norm_pct=rng_pt, ci_lo=rng_lo, ci_hi=rng_hi,
                                  **{f"mean_b{_g(b)}": float(m)
                                     for b, m in zip(ratios, means)}))
        L += ["  UNSTABLE means the storage effects reported in M1/M4/M5 must be",
              "  compared against this range before they are called findings."]
    _write_csv(out, "m0_battery_stability.csv", stab_rows)

    # ---- 4. seed dispersion ------------------------------------------------
    # Reported SEPARATELY from the configuration effect, because they answer
    # different questions: seed dispersion is the precision of one measurement,
    # configuration dispersion is the signal the campaign is trying to see.
    L += _sec("4. Seed dispersion vs configuration dispersion (GA only)")
    by_cell = defaultdict(list)
    for r in main:
        if r.get("method") != "GA":
            continue
        by_cell[(r["instance"], r["battery_ratio"], int(_num(r, "time_limit", 0)))
                ].append(float(r["objective"]))
    within, cellmeans = [], defaultdict(list)
    for (inst, b, tl), v in by_cell.items():
        sc = meta[inst]["scale"]
        if len(v) > 1 and math.isfinite(sc) and sc > 0:
            within.append(100.0 * float(np.std(v, ddof=1)) / sc)
        cellmeans[inst].append(float(np.mean(v)) / sc if math.isfinite(sc) and sc > 0
                               else float("nan"))
    between = [100.0 * float(np.std(v, ddof=1)) for v in cellmeans.values()
               if len(v) > 1 and all(math.isfinite(x) for x in v)]
    if within:
        w = np.array(within)
        L.append(f"  within instance x configuration (across seeds): n={len(w)} cells "
                 f"mean {w.mean():.4f}%  median {np.median(w):.4f}%  "
                 f"p90 {np.percentile(w, 90):.4f}%")
    else:
        L.append("  within-cell seed dispersion: not computable (one seed per cell)")
    if between:
        b_ = np.array(between)
        L.append(f"  across configurations within an instance:      n={len(b_)} inst  "
                 f"mean {b_.mean():.4f}%  median {np.median(b_):.4f}%  "
                 f"p90 {np.percentile(b_, 90):.4f}%")
        if within:
            L.append(f"  ratio configuration / seed dispersion: "
                     f"{(b_.mean() / max(np.array(within).mean(), 1e-12)):.2f}  "
                     "(<1 means seed noise dominates the signal)")
    _write_csv(out, "m0_dispersion.csv",
               [dict(kind="within_seed", value=v) for v in within] +
               [dict(kind="between_config", value=v) for v in between])

    # ---- 5. anytime profile ------------------------------------------------
    L += _sec("5. Anytime profile: what the time budget buys")
    budgets = sorted({int(_num(r, "time_limit", 0)) for r in R
                      if r.get("method") == "GA"})
    if len(budgets) < 2:
        L.append(f"  Only one GA budget present ({budgets}); "
                 f"design.TL_PROFILE_EXTRA = {design.TL_PROFILE_EXTRA} has not run.")
    else:
        ga = _cells([r for r in R if r.get("method") == "GA"],
                    ("instance", "battery_ratio", "time_limit"))
        ref_cells = _cells([r for r in R if r.get("method") == "MILP"],
                           ("instance", "battery_ratio"))
        anchor_name = "MILP" if ref_cells else "the best GA budget"
        if not ref_cells:
            # No MILP on the anytime sub-cell: anchor on the best value any
            # budget reached, which measures marginal improvement rather than
            # distance to the optimum, and the report says which it is.
            best = defaultdict(lambda: float("inf"))
            for (inst, b, tl), v in ga.items():
                best[(inst, b)] = min(best[(inst, b)], v)
            ref_cells = dict(best)
        keys = sorted({(i, b) for (i, b, tl) in ga})
        complete = [k for k in keys
                    if all((k[0], k[1], str(tl)) in ga or (k[0], k[1], tl) in ga
                           for tl in budgets) and k in ref_cells]
        L.append(f"  anchor: {anchor_name};  paired cells complete at every "
                 f"budget: {len(complete)}")
        prev = None
        any_rows = []
        L.append(f"  {'budget s':>9s} {'n':>6s} {'norm gap %':>11s} "
                 f"{'95% CI':>22s} {'marginal':>10s}")
        for tl in budgets:
            vals, ids = [], []
            for (i, b) in complete:
                v = ga.get((i, b, str(tl)), ga.get((i, b, tl)))
                a = ref_cells.get((i, b))
                sc = meta[i]["scale"]
                if v is None or a is None or not (math.isfinite(sc) and sc > 0):
                    continue
                vals.append(100.0 * (v - a) / sc)
                ids.append(i)
            if not vals:
                continue
            mn, lo, hi, n, _ = _cluster_ci_mean(vals, ids)
            marg = (prev - mn) if prev is not None else float("nan")
            L.append(f"  {tl:9d} {n:6d} {mn:11.4f} {_ci(lo, hi)} {marg:10.4f}")
            any_rows.append(dict(budget_s=tl, n=n, norm_gap_pct=mn,
                                 ci_lo=lo, ci_hi=hi, marginal_gain=marg))
            prev = mn
        _write_csv(out, "m0_anytime.csv", any_rows)
        L += ["  'marginal' is the gap reduction bought by moving up one budget.",
              "  A marginal gain near zero from the campaign budget upward is the",
              "  argument that TL_GA is not the binding constraint (design.py §5)."]

    # ---- 6. run time -------------------------------------------------------
    L += _sec("6. Solve time by method and size class (median and p90)")
    L.append(f"  {'method':8s} {'class':>7s} {'n':>6s} {'median s':>10s} "
             f"{'p90 s':>10s} {'max s':>10s}")
    trow = []
    tagg = defaultdict(list)
    for r in main:
        tagg[(r.get("method", "?"), meta[r["instance"]]["size_class"])].append(
            _num(r, "wall_seconds"))
    for (meth, sc), v in sorted(tagg.items(), key=lambda kv: (kv[0][0], _lvlkey(kv[0][1]))):
        a = np.array([x for x in v if math.isfinite(x)])
        if not len(a):
            continue
        L.append(f"  {meth:8s} {sc:>7s} {len(a):6d} {np.median(a):10.2f} "
                 f"{np.percentile(a, 90):10.2f} {a.max():10.2f}")
        trow.append(dict(method=meth, size_class=sc, n=len(a),
                         median_s=float(np.median(a)),
                         p90_s=float(np.percentile(a, 90)), max_s=float(a.max())))
    _write_csv(out, "m0_runtime.csv", trow)

    L += ["", _rule("="),
          "READ THIS BEFORE USING ANY OTHER REPORT",
          "  M0 licenses the campaign in three steps, in this order:",
          "    (a) the MILP closes often enough that a gap to it means something;",
          "    (b) the GA's gap is small on the closed population;",
          "    (c) the gap does NOT move with the battery level (section 3).",
          "  (c) failing does not invalidate M1-M5's rankings, but it does put a",
          "  floor under them: no storage effect smaller than the range in",
          "  section 3 can be attributed to storage rather than to the solver.",
          _rule("=")]
    return _emit(out, "m0_validation.txt", L)


# ---------------------------------------------------------------------------
# M1 -- the ROI cube
# ---------------------------------------------------------------------------

def _saving_records(R: list[dict], meta: dict, config_keys: tuple[str, ...],
                    value: str = "objective") -> list[dict]:
    """Paired savings against the b = 0 cell of the SAME configuration.

    SIGN CONVENTION (see the module header; every caller inherits it):
        saving      = 100 x (cost at b = 0  -  cost at b) / A.norm_scale
        saving_abs  =        cost at b = 0  -  cost at b
    so a POSITIVE number is an ECONOMY (the battery lowers the cost) and a
    NEGATIVE number is an EXTRA COST (the battery raises it). There is no
    absolute value and no sign flip anywhere in this module: a negative saving
    is a result, not a formatting accident.

    WHICH METRIC. `value` defaults to "objective", i.e. the saving is on the
    TOTAL objective, energy + weighted tardiness. Pass value="energy_cost" for
    the energy-only saving. The two can disagree in sign, and legitimately so:
    shifting production into cheap hours buys energy at the price of delivery
    performance, so an instance can show an energy economy and a total-cost
    surcharge at the same time. That is the trade-off M5 measures directly.
    Everything that feeds an INVESTMENT figure (M1's NPV, M2's NPV) must stay
    on the objective, because a plant pays for both halves of it.

    The pairing key is (instance, everything except the battery ratio). A cell
    without its own zero-battery twin is deleted list-wise rather than compared
    against some other configuration's baseline, which would confound the
    machine or tariff factor with the storage factor.
    """
    cells = _cells(R, ("instance",) + config_keys + ("battery_ratio",), value=value)
    recs = []
    for key, v in cells.items():
        inst, cfg, b = key[0], key[1:-1], key[-1]
        if b == 0.0:
            continue
        base = cells.get((inst,) + cfg + (0.0,))
        if base is None:
            continue
        sc = meta[inst]["scale"]
        if not (math.isfinite(sc) and sc > 0):
            continue
        rec = dict(instance=inst, shop=meta[inst]["shop"], battery_ratio=b,
                   base=base, value=v,
                   saving_abs=base - v, saving=100.0 * (base - v) / sc)
        rec.update({k: cfg[i] for i, k in enumerate(config_keys)})
        recs.append(rec)
    return recs


def _npv_block(recs: list[dict], meta: dict, econ: dict) -> None:
    """Attach NPV / payback / capacity to saving records, in place.

    Annualisation is PER INSTANCE and then aggregated. Averaging horizons first
    and annualising the average would weight long-horizon instances twice: once
    through their larger absolute saving and again through the shared divisor.
    """
    hours_per_year = econ["operating_weeks"] * 7 * 24
    for r in recs:
        m = meta[r["instance"]]
        h = m["horizon"]
        cap = r["battery_ratio"] * m["e_day"] * MWH_PER_ENERGY_UNIT
        if not (math.isfinite(h) and h > 0 and math.isfinite(cap) and cap > 0):
            r["capacity_mwh"] = float("nan")
            r["npv"] = float("nan")
            r["payback"] = float("inf")
            continue
        annual = r["saving_abs"] * hours_per_year / h
        r["capacity_mwh"] = cap
        r["annual_saving_eur"] = annual
        r["npv"] = A._npv(annual, cap, econ)
        r["payback"] = A._payback(annual, cap, econ)


@_guard
def m1(rows: list[dict], out: Path, econ: dict | None = None, **kw) -> str:
    """The ROI cube: capacity x tariff x machine, plus the (rho, restart) surface.

    The paper's claim is about a return on investment that depends on all three
    levers at once, which is a statement about an INTERACTION. v1 could not make
    it because it measured the three factors in three separate experiments at
    one another's baselines; the cube is what makes the claim estimable.

    SIGN CONVENTION. Every "saving %" in this report is

        100 x (objective at b = 0  -  objective at b) / A.norm_scale

    on the TOTAL OBJECTIVE (energy + weighted tardiness), so:

        saving > 0  ==  ECONOMY: the battery lowers the total cost
        saving < 0  ==  SURCHARGE: the battery raises it

    The total objective and not energy alone, because M1 is the investment
    report: NPV, payback and b* are what a plant is being advised on, and a
    plant pays for late orders as well as for electricity. A configuration
    whose battery buys energy with delivery performance therefore shows a
    NEGATIVE saving here, correctly, even though its energy bill fell. This is
    the SAME convention as M4's V_beta (which is also on the objective) and the
    OPPOSITE of M5's dEnergy/dTard, which are signed DELTAS of the two halves
    taken separately -- see m5's docstring.

    A negative saving under a volatile tariff is a finding about the plant, not
    a bug in this function: the falsification block below fixes the resolution
    floor, and section 1 prints the sign as measured.
    """
    econ = econ or economics.CENTRAL
    R = _prep(rows, ("M1", "M1b"))
    L = _hdr("M1 - the ROI cube: capacity x tariff x machine")
    if not R:
        return _emit(out, "m1_roi_cube.txt",
                     L + ["", "  NO M1 DATA."])
    meta = _meta(R)
    L += _inventory(R, meta)
    L += ["", UNITS_NOTE]

    # Sub-designs. If the runlist did not label them, a row carrying a restart
    # level but no archetype is a grid cell by construction.
    cube = [r for r in R if (r.get("m1_subdesign") or
                             ("grid" if r.get("restart_level") else "cube")) == "cube"]
    grid = [r for r in R if (r.get("m1_subdesign") or
                             ("grid" if r.get("restart_level") else "cube")) == "grid"]

    # ---- 0. FALSIFICATION CHECK, first, before any result -----------------
    # Under a constant price there is no arbitrage, so NO configuration can
    # create value through storage. Whatever saving is measured under flat is
    # therefore pure solver noise, and it is the resolution floor of the entire
    # experiment. Placing it at the top is deliberate: a reader must know the
    # floor before reading the first effect.
    L += _sec("0. FALSIFICATION CHECK (read before anything else)")
    flat_rows = [r for r in R if r.get("price_regime") == "flat"
                 or r.get("price_name") == "flat"]
    floor = float("nan")
    if not flat_rows:
        L += ["  NO FLAT-TARIFF CELLS. The resolution floor of this experiment is",
              "  UNKNOWN, and no effect below can be declared larger than noise.",
              "  design.M1_TARIFFS includes 'flat'; if it has not run, run it first."]
    else:
        fs = _saving_records(flat_rows, meta,
                             ("machine_profile", "rho", "restart_level"))
        if not fs:
            L += ["  Flat cells present but no zero-battery twin: floor not "
                  "computable yet."]
        else:
            a = np.abs(np.array([r["saving"] for r in fs], dtype=float))
            a = a[np.isfinite(a)]
            floor = float(a.max()) if len(a) else float("nan")
            L += [f"  n = {len(a)} flat-tariff instance x configuration cells",
                  f"  measured 'saving' under a constant price (must be ~0):",
                  f"    max    {a.max():.5f} % of the naive bill   <-- RESOLUTION FLOOR",
                  f"    p95    {np.percentile(a, 95):.5f} %",
                  f"    median {np.median(a):.5f} %",
                  f"    mean   {a.mean():.5f} %",
                  "  Any effect below smaller than the FLOOR is marked",
                  "  'BELOW RESOLUTION' and must be reported as such, not as a",
                  "  small effect."]
            _write_csv(out, "m1_falsification_flat.csv", fs)

    def flag(x: float) -> str:
        return ("  BELOW RESOLUTION" if math.isfinite(floor) and math.isfinite(x)
                and abs(x) <= floor else "")

    # ---- 1. main table: saving by (archetype, tariff, capacity) -----------
    L += _sec("1. Relative saving by archetype x tariff x capacity")
    L += ["  saving % = 100 x (cost at b=0 - cost at b) / (e_day x horizon_days x",
          "  |mean price|). NOT a percentage of the realised bill: with negative",
          "  hours that denominator can cross zero and the ratio diverges.",
          "  CI: 95 % percentile bootstrap over instances."]
    cube_recs = _saving_records(cube, meta, ("machine_profile",)) if cube else []
    for r in cube_recs:
        r["regime"] = meta[r["instance"]]["regime"]
    _write_csv(out, "m1_cube_savings.csv", cube_recs)
    if not cube_recs:
        L.append("  No paired cube cells yet.")
    else:
        archs = sorted({r["machine_profile"] for r in cube_recs}, key=_lvlkey)
        tars = sorted({r["regime"] for r in cube_recs}, key=_lvlkey)
        brs = sorted({r["battery_ratio"] for r in cube_recs})
        L.append(f"  {'archetype':18s} {'tariff':>13s} " +
                 "".join(f"{('b=' + _g(b)):>11s}" for b in brs) + f" {'n':>6s}")
        for arch in archs:
            for tar in tars:
                sub = [r for r in cube_recs
                       if r["machine_profile"] == arch and r["regime"] == tar]
                if not sub:
                    continue
                cellstr = ""
                for b in brs:
                    v = [r["saving"] for r in sub if r["battery_ratio"] == b]
                    cellstr += (f"{np.mean(v):11.4f}" if v else f"{'.':>11s}")
                L.append(f"  {arch[:18]:18s} {tar[:13]:>13s}{cellstr} "
                         f"{len({r['instance'] for r in sub}):6d}")
        # One CI table at the capacity a plant would actually install, rather
        # than a CI in every cell of a 6 x 5 x 7 grid nobody can read.
        b_ref = (design.BATTERY_ON_RATIO if design.BATTERY_ON_RATIO in brs
                 else (brs[0] if brs else None))
        if b_ref is not None:
            L += ["", f"  detail at the installed capacity b = {_g(b_ref)} E_day "
                      f"(design.BATTERY_ON_RATIO)",
                  f"  {'archetype':18s} {'tariff':>13s} {'n':>5s} {'saving %':>10s} "
                  f"{'95% CI':>22s}"]
            for arch in archs:
                for tar in tars:
                    sub = [r for r in cube_recs if r["machine_profile"] == arch
                           and r["regime"] == tar and r["battery_ratio"] == b_ref]
                    if not sub:
                        continue
                    mn, lo, hi, n, _ = _cluster_ci_mean(
                        [r["saving"] for r in sub], [r["instance"] for r in sub])
                    L.append(f"  {arch[:18]:18s} {tar[:13]:>13s} {n:5d} {mn:10.4f} "
                             f"{_ci(lo, hi)}{flag(mn)}")

    # ---- 2. three-factor variance decomposition ---------------------------
    L += _sec("2. Where the variation in the return comes from (3-factor ANOVA)")
    L += ["  Shares of the total sum of squares of the saving, sequential",
          "  (type I) SS in the printed order, effect coding. The design is",
          "  crossed but list-wise deletion can unbalance it, so the order",
          "  matters and is fixed: main effects, two-way, three-way.",
          "  A large three-way share is the paper's thesis: the return on",
          "  storage is not a sum of three separate stories."]
    if len(cube_recs) < 30:
        L.append("  Too few paired cube cells for a variance decomposition.")
    else:
        y = np.array([r["saving"] for r in cube_recs], dtype=float)
        Xa, la = _sum_to_zero([r["machine_profile"] for r in cube_recs])
        Xb, lb = _sum_to_zero([r["regime"] for r in cube_recs])
        Xc, lc = _sum_to_zero([_g(r["battery_ratio"]) for r in cube_recs])
        blocks = [("machine (A)", Xa), ("tariff (B)", Xb), ("capacity (C)", Xc),
                  ("A x B", _inter(Xa, Xb)), ("A x C", _inter(Xa, Xc)),
                  ("B x C", _inter(Xb, Xc)),
                  ("A x B x C", _inter(_inter(Xa, Xb), Xc))]
        dec = _variance_decomposition(y, blocks)
        L.append(f"  {'effect':14s} {'df':>5s} {'share of SS':>13s}")
        for name, share, df in dec:
            L.append(f"  {name:14s} {df:5d} {100 * share:12.2f}%")
        _write_csv(out, "m1_anova.csv",
                   [dict(effect=n, df=d, share_ss=s) for n, s, d in dec])
        L += [f"  levels: machine {la}", f"          tariff  {lb}",
              f"          capacity {lc}"]

        # Cluster-robust main effects. One SHOP contributes several instances
        # (the same structure paired with every tariff), so residuals are
        # correlated within shop and an i.i.d. standard error is too small.
        X = np.column_stack([np.ones(len(y)), Xa, Xb, Xc])
        names = (["intercept"] + [f"machine={l}" for l in la[:-1]] +
                 [f"tariff={l}" for l in lb[:-1]] + [f"capacity={l}" for l in lc[:-1]])
        clusters = np.array([r["shop"] for r in cube_recs])
        beta, se, r2, G, usable = ols_cluster(X, y, clusters)
        L += ["", f"  main effects with SE clustered by shop "
                  f"({G} shops, R2 = {r2:.4f})",
              "  (effect coding: a coefficient is the deviation of that level "
              "from the grand mean;",
              "   the omitted level is minus the sum of the others)"]
        L += _cluster_note(G, usable)
        L += ["  Holm families: the machine levels, the tariff levels and the",
              "  capacity levels are corrected SEPARATELY -- they answer three",
              "  different questions, and pooling them into one family of 15",
              "  would penalise each for the others' tests.",
              f"  {'term':28s} {'coef':>10s} {'se':>10s} {'t':>8s} {'p(holm)':>9s}"]
        # one family per factor, not one family over the union
        fams: dict[str, dict[str, float]] = defaultdict(dict)
        df_cl = max(1, G - 1)
        for nm, b_, s_ in zip(names, beta, se):
            if nm == "intercept":
                continue
            fams[nm.split("=")[0]][nm] = _coef_p(b_, s_, df_cl, usable)
        padj: dict[str, float] = {}
        for f_ in fams.values():
            padj.update(_holm(f_))
        for nm, b_, s_ in zip(names, beta, se):
            t = _coef_t(b_, s_, usable)
            L.append(f"  {nm:28s} {b_:10.4f} {s_:10.4f} {t:8.2f} "
                     f"{padj.get(nm, float('nan')):9.4f}")

    # ---- 3. ROI surface ----------------------------------------------------
    L += _sec("3. ROI surface: NPV-optimal capacity by archetype x tariff")
    L += ["  Per-instance annualisation then aggregation (analyses.e2's",
          "  convention). NPV median over instances, thousand EUR. b* is the",
          "  capacity maximising the MEDIAN NPV among the capacities actually",
          "  run -- it is a grid argmax, not an optimum of a fitted curve, and",
          "  cannot be finer than design.BATTERY_RATIOS."]
    scen = kw.get("scenarios") or economics.SENSITIVITY
    roi_rows = []
    if not cube_recs:
        L.append("  No cube data; ROI surface unavailable.")
    else:
        archs = sorted({r["machine_profile"] for r in cube_recs}, key=_lvlkey)
        tars = sorted({r["regime"] for r in cube_recs}, key=_lvlkey)
        for sname, sec_ in scen.items():
            recs_s = [dict(r) for r in cube_recs]
            _npv_block(recs_s, meta, sec_)
            for arch in archs:
                for tar in tars:
                    sub = [r for r in recs_s if r["machine_profile"] == arch
                           and r["regime"] == tar]
                    if not sub:
                        continue
                    by_b = defaultdict(list)
                    for r in sub:
                        by_b[r["battery_ratio"]].append(r)
                    stats = {}
                    for b, v in by_b.items():
                        npvs = np.array([x["npv"] for x in v], dtype=float)
                        npvs = npvs[np.isfinite(npvs)]
                        if not len(npvs):
                            continue
                        pays = [x["payback"] for x in v if math.isfinite(x["payback"])]
                        stats[b] = dict(
                            med_npv=float(np.median(npvs)),
                            frac_pos=100.0 * float((npvs > 0).mean()),
                            payback=float(np.median(pays)) if pays else float("inf"),
                            mean_saving=float(np.mean([x["saving"] for x in v])),
                            n=len(v))
                    if not stats:
                        continue
                    b_star = max(stats, key=lambda b: stats[b]["med_npv"])
                    # Saturation: the smallest capacity at which the marginal
                    # saving per unit of ratio has fallen to 5 % of the first
                    # step's. This is the engineer's "the battery is full
                    # enough" point, and the paper's comparison is how far
                    # BELOW it the money-optimal size sits.
                    bs = sorted(stats)
                    marg, prev_b, prev_s = [], 0.0, 0.0
                    for b in bs:
                        db = b - prev_b
                        marg.append((b, (stats[b]["mean_saving"] - prev_s) / db
                                     if db > 0 else float("nan")))
                        prev_b, prev_s = b, stats[b]["mean_saving"]
                    m0_ = marg[0][1] if marg and math.isfinite(marg[0][1]) else float("nan")
                    b_sat = float("nan")
                    if math.isfinite(m0_) and m0_ > 0:
                        for b, mm in marg:
                            if math.isfinite(mm) and mm <= 0.05 * m0_:
                                b_sat = b
                                break
                        else:
                            b_sat = bs[-1]      # never saturated in the design
                    roi_rows.append(dict(
                        scenario=sname, archetype=arch, tariff=tar,
                        b_star=b_star, npv_kEUR=stats[b_star]["med_npv"] / 1000.0,
                        payback_y=stats[b_star]["payback"],
                        frac_npv_pos=stats[b_star]["frac_pos"],
                        n=stats[b_star]["n"], b_saturation=b_sat,
                        ratio_bstar_over_bsat=(b_star / b_sat
                                               if math.isfinite(b_sat) and b_sat > 0
                                               else float("nan")),
                        saving_at_bstar=stats[b_star]["mean_saving"]))
        _write_csv(out, "m1_roi_surface.csv", roi_rows)

        central_key = "central" if "central" in scen else next(iter(scen))
        L += ["", f"  scenario = {central_key}  (economics.SENSITIVITY)",
              f"  {'archetype':18s} {'tariff':>13s} {'n':>5s} {'b*':>6s} "
              f"{'NPV kEUR':>10s} {'payback y':>10s} {'NPV>0':>7s} "
              f"{'b_sat':>7s} {'b*/b_sat':>9s}"]
        for r in [x for x in roi_rows if x["scenario"] == central_key]:
            pay = r["payback_y"]
            L.append(f"  {r['archetype'][:18]:18s} {r['tariff'][:13]:>13s} "
                     f"{r['n']:5d} {r['b_star']:6.2f} {r['npv_kEUR']:10.1f} "
                     f"{(pay if math.isfinite(pay) else float('nan')):10.2f} "
                     f"{r['frac_npv_pos']:6.0f}% {r['b_saturation']:7.2f} "
                     f"{r['ratio_bstar_over_bsat']:9.3f}")
        # The other scenarios compactly: the cost assumption, not the physics,
        # decides the sign of the investment answer, and a point payback figure
        # quoted without this band is not defensible.
        L += ["", "  cost-assumption band (the sign of the answer moves with it)",
              f"  {'scenario':10s} {'cells':>6s} {'median b*':>10s} "
              f"{'median NPV kEUR':>16s} {'cells with NPV>0 majority':>27s}"]
        for sname in scen:
            sub = [x for x in roi_rows if x["scenario"] == sname]
            if not sub:
                continue
            L.append(f"  {sname:10s} {len(sub):6d} "
                     f"{np.median([x['b_star'] for x in sub]):10.2f} "
                     f"{np.median([x['npv_kEUR'] for x in sub]):16.1f} "
                     f"{100 * np.mean([x['frac_npv_pos'] > 50 for x in sub]):26.0f}%")
        L += ["", "  THE COMPARISON THAT MAKES THE PAPER: b* / b_sat above. A ratio",
              "  well below 1 says the money-optimal battery is a small fraction",
              "  of the technically saturating one, i.e. the engineering",
              "  intuition ('size it until the marginal kWh stops helping')",
              "  systematically over-invests. A ratio of 1 with b_sat at the top",
              "  of the ladder means the design never reached saturation and the",
              "  comparison is not identified -- check b_sat against",
              f"  max(design.BATTERY_RATIOS) = {max(design.BATTERY_RATIOS)}."]

    # ---- 4. M1b orthogonal (rho, restart) surface -------------------------
    L += _sec("4. M1b: the orthogonal (rho, restart) surface")
    L += ["  The archetypes are recognisable but not orthogonal; this grid is",
          "  orthogonal but not recognisable. Main effects and their interaction",
          "  are estimable here and not in section 1."]
    grid_recs = _saving_records(grid, meta, ("rho", "restart_level")) if grid else []
    _write_csv(out, "m1b_grid_savings.csv", grid_recs)
    if not grid_recs:
        L.append("  No M1b grid cells with a zero-battery twin yet.")
    else:
        rhos = sorted({r["rho"] for r in grid_recs}, key=_lvlkey)
        rests = [x for x in ("low", "med", "prohibitive")
                 if x in {r["restart_level"] for r in grid_recs}]
        rests += [x for x in sorted({r["restart_level"] for r in grid_recs})
                  if x not in rests]
        brs = sorted({r["battery_ratio"] for r in grid_recs})
        for b in brs:
            L.append(f"\n  saving surface at b = {_g(b)} E_day "
                     f"(rows = restart penalty, cols = rho = e_idle/e_proc)")
            L.append("    restart      " + "".join(f"{('rho=' + str(x)):>12s}"
                                                   for x in rhos))
            for rest in rests:
                cells_txt = ""
                for rh in rhos:
                    v = [r["saving"] for r in grid_recs
                         if r["battery_ratio"] == b and r["rho"] == rh
                         and r["restart_level"] == rest]
                    cells_txt += (f"{np.mean(v):12.4f}" if v else f"{'.':>12s}")
                L.append(f"    {rest:12s}{cells_txt}")

        y = np.array([r["saving"] for r in grid_recs], dtype=float)
        Xr, lr = _sum_to_zero([r["rho"] for r in grid_recs])
        Xs, ls = _sum_to_zero([r["restart_level"] for r in grid_recs])
        Xb, lbb = _sum_to_zero([_g(r["battery_ratio"]) for r in grid_recs])
        dec = _variance_decomposition(
            y, [("rho", Xr), ("restart", Xs), ("capacity", Xb),
                ("rho x restart", _inter(Xr, Xs)),
                ("rho x capacity", _inter(Xr, Xb)),
                ("restart x capacity", _inter(Xs, Xb))])
        L += ["", "  variance shares on the grid",
              f"  {'effect':20s} {'df':>5s} {'share of SS':>13s}"]
        for name, share, df in dec:
            L.append(f"  {name:20s} {df:5d} {100 * share:12.2f}%")

        # The managerial sentence, stated explicitly and with an interval:
        # what happens to the RETURN ON STORAGE when restarting stops being an
        # option. This is the reading a plant engineer can act on.
        if len(rests) >= 2:
            lo_lv, hi_lv = rests[0], rests[-1]
            L += ["", f"  explicit reading: restart '{lo_lv}' -> '{hi_lv}'"]
            fam = {}
            for b in brs:
                per = defaultdict(dict)
                for r in grid_recs:
                    if r["battery_ratio"] == b:
                        per[(r["instance"], r["rho"])][r["restart_level"]] = r["saving"]
                # The unit of analysis is the INSTANCE, so the rho ladder is
                # averaged within instance before the pairing. Keeping one
                # record per (instance, rho) would count each instance three
                # times and shrink the interval by sqrt(3) on data that
                # contains no extra instances.
                per_inst = defaultdict(list)
                for (i_, _rh), d_ in per.items():
                    if lo_lv in d_ and hi_lv in d_:
                        per_inst[i_].append((d_[lo_lv], d_[hi_lv]))
                pairs = [(float(np.mean([p[0] for p in v])),
                          float(np.mean([p[1] for p in v])))
                         for v in per_inst.values()]
                if len(pairs) < 3:
                    L.append(f"    b={_g(b)}: only {len(pairs)} paired cells; skipped")
                    continue
                d = np.array([hi - lo for lo, hi in pairs])
                base = float(np.mean([lo for lo, _ in pairs]))
                st = paired_summary(d, f"b={_g(b)}")
                rel = 100.0 * st["mean"] / base if abs(base) > 1e-12 else float("nan")
                L.append(f"    b={_g(b):>5s} n={st['n']:4d}  storage return "
                         f"{base:.4f}% -> {base + st['mean']:.4f}%  "
                         f"change {st['mean']:+.4f} pp {_ci(st['ci_lo'], st['ci_hi'])} "
                         f"= {rel:+.1f} %{flag(st['mean'])}")
                fam[f"b={_g(b)}"] = _p_from_t(st["t"], max(1, st["n"] - 1))
            if fam:
                adj = _holm(fam)
                L.append("    Holm-adjusted p (family: restart effect on the "
                         "storage return, one test per capacity)")
                for k in sorted(adj, key=lambda x: adj[x]):
                    L.append(f"      {k:12s} p_holm = {adj[k]:.4g}")
            L += ["    Read as: when restarting becomes prohibitive, the machine",
                  "    can no longer shut down to dodge a price peak, so the",
                  "    arbitrage has to be done by the battery instead. A POSITIVE",
                  "    change means storage is worth MORE to an inflexible plant."]

    return _emit(out, "m1_roi_cube.txt", L)


# ---------------------------------------------------------------------------
# M2 -- price volatility
# ---------------------------------------------------------------------------

@_guard
def m2(rows: list[dict], out: Path, econ: dict | None = None, **kw) -> str:
    """Does the value of storage follow price volatility -- in real markets?

    v1's answer was an artefact and this function exists to make that
    impossible to miss again: the pooled spread coefficient was +0.554
    (se 0.026) on the synthetic family and -0.061 (se 0.108) on real tariffs.
    The relationship the paper wanted to report was a property of the sinusoid
    generator, not of electricity markets. The split regression is therefore
    the FIRST table here, not a robustness note at the end.

    SIGN CONVENTION. The regressand is _saving_records's `saving`: a POSITIVE
    coefficient means more of that price descriptor buys a LARGER economy on
    the TOTAL objective (energy + weighted tardiness), the same convention as
    M1 and M4. A negative spread coefficient therefore says storage costs more
    where the price moves more -- readable, and not a sign error here.

    CLUSTERING. Every standard error in this report is clustered on the SHOP,
    not on the run and not on the instance: one shop supplies the same physical
    structure to every tariff in the library, so its records share a structural
    residual. With fewer than MIN_CLUSTERS shops no t and no p is quoted at
    all; the cluster-robust variance is asymptotic in the number of clusters
    and at G = 1 it is identically zero.
    """
    econ = econ or economics.CENTRAL
    R = _prep(rows, ("M2",))
    L = _hdr("M2 - price volatility and the value of storage")
    if not R:
        return _emit(out, "m2_volatility.txt", L + ["", "  NO M2 DATA."])
    meta = _meta(R)
    L += _inventory(R, meta)
    fams = sorted({meta[i]["family"] for i in {r["instance"] for r in R}})
    L.append(f"  tariff families: {', '.join(f for f in fams if f) or '-'}")

    recs = _saving_records(R, meta, ())
    for r in recs:
        m = meta[r["instance"]]
        r.update(spread=m["spread"], cv=m["cv"], neg=m["neg"], pmean=m["pmean"],
                 family=m["family"], label=m["label"], market=m["market"],
                 year=m["year"], regime=m["regime"])
    _npv_block(recs, meta, econ)
    _write_csv(out, "m2_records.csv", recs)

    ok = [r for r in recs
          if all(math.isfinite(r[k]) for k in ("saving", "spread", "cv", "neg", "pmean"))]
    if len(ok) < 20:
        return _emit(out, "m2_volatility.txt",
                     L + ["", f"  only {len(ok)} complete records (need >= 20); "
                              "price descriptors or paired baselines are missing."])

    names = ["spread_intraday", "price_cv", "neg_share", "price_mean"]

    def fit(sub):
        y = np.array([r["saving"] for r in sub], dtype=float)
        X = np.column_stack([np.ones(len(sub))] +
                            [[r[k] for r in sub] for k in ("spread", "cv", "neg", "pmean")])
        cl = np.array([r["shop"] for r in sub])
        beta, se, r2, G, usable = ols_cluster(X, y, cl)
        return beta, se, r2, X, y, cl, G, usable

    # ---- 1. THE HEADLINE: synthetic vs real, side by side ------------------
    L += _sec("1. HEADLINE: the same regression on synthetic and on real tariffs")
    L += ["  If these two columns disagree, the pooled coefficient in section 2",
          "  is a property of the price GENERATOR and not of electricity markets,",
          "  and no screening rule may be derived from it.",
          "  v1 for reference: synthetic +0.554 (se 0.026), real -0.061 (se 0.108).",
          ""]
    groups = {
        "synthetic": [r for r in ok if r["family"] == "synthetic"],
        "real market": [r for r in ok if r["family"] in REAL_FAMILIES],
        "contractual": [r for r in ok if r["family"] == "contractual"],
    }
    L.append(f"  {'family':14s} {'n':>6s} {'shops':>6s} {'R2':>7s} " +
             "".join(f"{nm[:12]:>14s}" for nm in names))
    fitres = {}
    for gname, sub in groups.items():
        if len(sub) < 15:
            L.append(f"  {gname:14s} {len(sub):6d}   (too few records to fit)")
            continue
        beta, se, r2, _X, _y, _cl, G, usable = fit(sub)
        fitres[gname] = (beta, se, len(sub), G, usable,
                         {r["shop"] for r in sub})
        L.append(f"  {gname:14s} {len(sub):6d} {G:6d} "
                 f"{r2:7.4f} " +
                 "".join(f"{b:14.4f}" for b in beta[1:]))
        L.append(f"  {'':14s} {'(se)':>6s} {'':6s} {'':7s} " +
                 "".join(f"{s:14.4f}" for s in se[1:]))
        if not usable:
            L += _cluster_note(G, usable)
    if "synthetic" in fitres and "real market" in fitres:
        bs, ss, _, Gs, us, shops_s = fitres["synthetic"]
        br, sr, _, Gr, ur, shops_r = fitres["real market"]
        d = bs[1] - br[1]
        shared = shops_s & shops_r
        sed = math.sqrt(ss[1] ** 2 + sr[1] ** 2)
        # df is the SMALLER of the two cluster counts, not a hard-coded 100:
        # a difference of two shop-clustered slopes cannot be more precise than
        # the thinner of the two shop panels behind it.
        df = max(1, min(Gs, Gr) - 1)
        t = _ratio(d, sed, d) if (us and ur) else float("nan")
        p = _p_from_t(t, df)
        L += ["", f"  difference in the SPREAD coefficient (synthetic - real): "
                  f"{d:+.4f} (se {sed:.4f}, t {t:.2f}, p {p:.4g}, df {df})"]
        if shared:
            L += [f"  CAUTION: the two fits SHARE {len(shared)} shop(s), so they are",
                  "  not independent samples and adding the SEs in quadrature is",
                  "  only an approximation (conservative if the two slopes are",
                  "  positively correlated within shop, which is the usual case).",
                  "  Quote the two columns, not this difference, where it matters."]
        else:
            L += ["  Independent-sample difference (the two fits share no shop),",
                  "  so the SEs simply add in quadrature."]
        L += [""]
        if not (us and ur):
            L += ["  VERDICT: NOT AVAILABLE -- one of the two fits has too few "
                  "shop clusters", "  for its slope to have a sampling distribution."]
        else:
            verdict = ("ARTEFACT: the spread response exists only in the generated "
                       "family." if (abs(t) > 2 and abs(br[1]) < 2 * sr[1]) else
                       "The spread response survives on real tariffs." if abs(br[1]) > 2 * sr[1]
                       else "INCONCLUSIVE: neither family identifies the spread slope.")
            L.append(f"  VERDICT: {verdict}")

    # ---- 2. pooled regression + collinearity ------------------------------
    L += _sec("2. Pooled regression (reported second, for description only)")
    beta, se, r2, X, y, cl, G, usable = fit(ok)
    L += [f"  n = {len(ok)} instance x tariff records, "
          f"{G} shop clusters, R2 = {r2:.4f}",
          "  saving% ~ spread + cv + neg_share + mean, SE clustered by shop"]
    L += _cluster_note(G, usable)
    L += [f"  {'term':18s} {'coef':>10s} {'se':>10s} {'t':>8s} {'p(holm)':>9s}"]
    praw = {nm: _coef_p(b_, s_, max(1, G - 1), usable)
            for nm, b_, s_ in zip(names, beta[1:], se[1:])}
    padj = _holm(praw)
    L.append(f"  {'intercept':18s} {beta[0]:10.4f} {se[0]:10.4f}")
    for nm, b_, s_ in zip(names, beta[1:], se[1:]):
        L.append(f"  {nm:18s} {b_:10.4f} {s_:10.4f} "
                 f"{_coef_t(b_, s_, usable):8.2f} {padj[nm]:9.4f}")

    # VIF is computed on the CENTRED AND SCALED covariate block (_vif
    # standardises internally) and WITHOUT the intercept column, which is the
    # only way the numbers mean "how much of this covariate the others already
    # explain" rather than "how far the design sits from the origin".
    L += ["", "  variance inflation (VIF > 5 caution, > 10 severe;",
          "   computed on the centred and scaled covariates, intercept excluded)"]
    vifs = _vif(X[:, 1:], names)
    worst = max((v for _, v in vifs if math.isfinite(v)), default=0.0)
    for nm, v in vifs:
        tag = "SEVERE" if v > 10 else ("caution" if v > 5 else "")
        L.append(f"    {nm:18s} VIF {v:8.2f}  {tag}")
    if worst > 5:
        L += ["    -> spread and cv both measure dispersion and are near-collinear",
              "       by construction. The individual coefficients are NOT",
              "       separately identified: do not rank the predictors and do not",
              "       interpret a sign flip on the weaker one.",
              "       (v1 carried VIFs around 9.5 here.)"]

    # ---- 3. screening threshold, with its support -------------------------
    L += _sec("3. Screening rule: the spread below which storage stops paying")
    L += ["  Inverting a fit is only meaningful where the data actually lie. The",
          "  support count is the number of observations within +/-50 % of the",
          "  implied spread; below that, the number is extrapolation into an",
          "  empty region of the design and is refused rather than printed."]
    thr_rows = []
    for gname in ("real market", "synthetic"):
        sub = groups.get(gname, [])
        if gname not in fitres:
            L.append(f"  {gname}: not fitted")
            continue
        b_, s_, n_, G_, usable_, _shops = fitres[gname]
        sp = np.array([r["spread"] for r in sub], dtype=float)
        L.append(f"\n  {gname}: spread support "
                 f"min {sp.min():.1f}  " +
                 "  ".join(f"p{q}={np.percentile(sp, q):.1f}" for q in (5, 25, 50, 75, 95)) +
                 f"  max {sp.max():.1f}")
        if b_[1] <= 1e-9:
            L.append("    spread coefficient is not positive: no usable rule "
                     "(this is itself the finding).")
            continue
        mx = [float(np.mean([r[k] for r in sub])) for k in ("cv", "neg", "pmean")]
        for target in (0.5, 1.0, 2.0):
            x = (target - b_[0] - b_[2] * mx[0] - b_[3] * mx[1] - b_[4] * mx[2]) / b_[1]
            near = int(((sp >= 0.5 * x) & (sp <= 1.5 * x)).sum())
            enough = near >= max(20, 0.02 * len(sp))
            thr_rows.append(dict(family=gname, target_saving_pct=target,
                                 implied_spread=x, support_pm50=near,
                                 usable=bool(enough)))
            if enough:
                L.append(f"    saving {target:.1f}% of the naive bill at spread "
                         f"~{x:7.1f} EUR/MWh  ({near} obs within +/-50 %)")
            else:
                L.append(f"    saving {target:.1f}%: implied spread {x:7.1f} is NOT "
                         f"IDENTIFIABLE ({near} obs within +/-50 %) -- REFUSED")
    _write_csv(out, "m2_screening.csv", thr_rows)

    # A non-parametric reading of the same question, which needs no functional
    # form and no extrapolation: profitability by spread decile.
    L += ["", "  non-parametric: profitability by spread decile (real markets)",
          f"  {'decile':>7s} {'spread lo':>10s} {'spread hi':>10s} {'n':>6s} "
          f"{'saving %':>10s} {'median NPV kEUR':>16s} {'NPV>0':>7s}"]
    realsub = groups.get("real market", [])
    if len(realsub) >= 30:
        # Rank-based deciles, not value-based edges. The tariff library has a
        # handful of distinct spreads repeated across shops, so percentile
        # edges collide and a value-based bucket can come out empty while the
        # observations sit on its boundary -- which loses a whole decile from
        # the table without saying so.
        order = sorted(realsub, key=lambda r: r["spread"])
        bounds = [round(k * len(order) / 10) for k in range(11)]
        dec_rows = []
        for k in range(10):
            sel = order[bounds[k]:bounds[k + 1]]
            if not sel:
                continue
            lo_e = sel[0]["spread"]
            hi_e = sel[-1]["spread"]
            npvs = np.array([r["npv"] for r in sel], dtype=float)
            npvs = npvs[np.isfinite(npvs)]
            L.append(f"  {k + 1:7d} {lo_e:10.1f} {hi_e:10.1f} {len(sel):6d} "
                     f"{np.mean([r['saving'] for r in sel]):10.4f} "
                     f"{(np.median(npvs) / 1000 if len(npvs) else float('nan')):16.1f} "
                     f"{(100 * (npvs > 0).mean() if len(npvs) else float('nan')):6.0f}%")
            dec_rows.append(dict(decile=k + 1, spread_lo=lo_e, spread_hi=hi_e,
                                 n=len(sel),
                                 saving=float(np.mean([r["saving"] for r in sel])),
                                 median_npv=float(np.median(npvs)) if len(npvs) else "",
                                 frac_pos=float((npvs > 0).mean()) if len(npvs) else ""))
        _write_csv(out, "m2_spread_deciles.csv", dec_rows)
        L += ["  The screening threshold a manager can act on is the decile where",
              "  'NPV>0' crosses 50 %, read off this table. Do not interpolate",
              "  inside a decile and do not extrapolate below the first one."]
    else:
        L.append("  (too few real-market records for deciles)")

    # ---- 4. real market-years ---------------------------------------------
    L += _sec("4. Real market-years: does the conclusion survive the regime?")
    L += ["  A calm year, a crisis year, a high-renewable year and a second",
          "  bidding zone. If the sign of the investment answer moves across",
          "  these rows, the paper's recommendation is conditional on the market",
          "  regime and must say so."]
    L.append(f"  {'market':>8s} {'year':>6s} {'label':>16s} {'n':>6s} "
             f"{'spread':>9s} {'saving %':>10s} {'95% CI':>22s} "
             f"{'NPV kEUR':>10s} {'NPV>0':>7s}")
    yr_rows = []
    realish = [r for r in ok if r["family"] in REAL_FAMILIES]
    keys = sorted({(r["market"], r["year"], r["label"]) for r in realish})
    if not keys or keys == [("", "", "")]:
        L.append("  No market/year labels recorded (design.REAL_MARKET_YEARS "
                 "files absent?). The real-tariff arm is NOT interpretable.")
    for (mk, yr, lab) in keys:
        sub = [r for r in realish
               if (r["market"], r["year"], r["label"]) == (mk, yr, lab)]
        if not sub:
            continue
        mn, lo, hi, n, _ = _cluster_ci_mean([r["saving"] for r in sub],
                                            [r["instance"] for r in sub])
        npvs = np.array([r["npv"] for r in sub], dtype=float)
        npvs = npvs[np.isfinite(npvs)]
        L.append(f"  {mk or '-':>8s} {yr or '-':>6s} {lab or '-':>16s} {n:6d} "
                 f"{np.mean([r['spread'] for r in sub]):9.1f} {mn:10.4f} "
                 f"{_ci(lo, hi)} "
                 f"{(np.median(npvs) / 1000 if len(npvs) else float('nan')):10.1f} "
                 f"{(100 * (npvs > 0).mean() if len(npvs) else float('nan')):6.0f}%")
        yr_rows.append(dict(market=mk, year=yr, label=lab, n=n, saving=mn,
                            ci_lo=lo, ci_hi=hi,
                            median_npv=float(np.median(npvs)) if len(npvs) else "",
                            frac_pos=float((npvs > 0).mean()) if len(npvs) else ""))
    _write_csv(out, "m2_market_years.csv", yr_rows)
    L += ["", f"  (NPV under economics = the scenario passed to m2; "
              f"capex {econ['capex_eur_per_kwh']:.0f} EUR/kWh, "
              f"wacc {econ['wacc']:.0%}, life {econ['life_years']} y)"]
    return _emit(out, "m2_volatility.txt", L)


# ---------------------------------------------------------------------------
# M3 -- scaling
# ---------------------------------------------------------------------------

@_guard
def m3(rows: list[dict], out: Path, **kw) -> str:
    """How the value of storage and the solver behave as the shop grows.

    METHODOLOGICAL WARNING, HANDLED EXPLICITLY BELOW. In this generator the
    horizon h is derived from a makespan lower bound (lib/generate.py step 3),
    so n and h move TOGETHER. A raw cost comparison across size classes
    therefore confounds "more tasks" with "more daily price cycles to
    arbitrage across" -- and the second alone would produce a rising saving
    with no scheduling content whatever. Everything here is reported per
    horizon day AND normalised by A.norm_scale, and the realised correlation
    between n and horizon days is printed so the reader can see how bad the
    confound is rather than take our word for it.
    """
    R = _prep(rows, ("M3",))
    L = _hdr("M3 - scaling in the number of tasks")
    if not R:
        return _emit(out, "m3_scaling.txt", L + ["", "  NO M3 DATA."])
    meta = _meta(R)
    L += _inventory(R, meta)

    # ---- 0. the confound, quantified --------------------------------------
    L += _sec("0. THE CONFOUND: n and the horizon move together by construction")
    insts = sorted({r["instance"] for r in R})
    nn = np.array([meta[i]["n"] for i in insts], dtype=float)
    hd = np.array([meta[i]["horizon_days"] for i in insts], dtype=float)
    pr = _pearson(nn, hd)
    sp, sp_p = _spearman(nn, hd)
    L += [f"  instances {len(insts)}",
          f"  corr(n, horizon_days): Pearson {pr:.4f}   Spearman {sp:.4f} "
          f"(p {sp_p:.3g})",
          "  A correlation near 1 means a size class IS a horizon length here.",
          "  Consequence: any raw cost or raw saving compared across classes is",
          "  partly a comparison of how many price cycles the schedule spans.",
          "  Mitigation used below: every quantity is (a) divided by",
          "  A.norm_scale, which already contains horizon_days, and (b) also",
          "  reported per horizon day. Where the two orderings disagree, the",
          "  size effect is not identified and the report says so.",
          "",
          f"  {'class':>7s} {'inst':>6s} {'mean n':>9s} {'mean days':>10s} "
          f"{'mean e_day':>11s} {'mean scale':>12s}"]
    for sc in sorted({meta[i]["size_class"] for i in insts}, key=_lvlkey):
        sub = [i for i in insts if meta[i]["size_class"] == sc]
        L.append(f"  {sc:>7s} {len(sub):6d} "
                 f"{_nanmean([meta[i]['n'] for i in sub]):9.1f} "
                 f"{_nanmean([meta[i]['horizon_days'] for i in sub]):10.2f} "
                 f"{_nanmean([meta[i]['e_day'] for i in sub]):11.2f} "
                 f"{_nanmean([meta[i]['scale'] for i in sub]):12.1f}")

    # ---- 1. storage saving vs n -------------------------------------------
    recs = _saving_records(R, meta, ())
    for r in recs:
        m = meta[r["instance"]]
        r.update(size_class=m["size_class"], n=m["n"], days=m["horizon_days"],
                 regime=m["regime"], ei=m["ei_density"], slack=m["due_slack"],
                 ei_level=m["ei_level"], tight_level=m["tight_level"],
                 saving_per_day=(r["saving_abs"] / m["horizon_days"]
                                 if m["horizon_days"] else float("nan")))
    _write_csv(out, "m3_savings.csv", recs)
    L += _sec("1. Storage saving as the shop grows")
    if not recs:
        L.append("  No paired battery/no-battery cells.")
    else:
        brs = sorted({r["battery_ratio"] for r in recs})
        for b in brs:
            L.append(f"\n  b = {_g(b)} E_day")
            L.append(f"  {'class':>7s} {'n obs':>6s} {'inst':>6s} {'saving %':>10s} "
                     f"{'95% CI':>22s} {'abs/day':>11s}")
            for sc in sorted({r["size_class"] for r in recs}, key=_lvlkey):
                sub = [r for r in recs if r["battery_ratio"] == b
                       and r["size_class"] == sc]
                if not sub:
                    continue
                mn, lo, hi, n, ni = _cluster_ci_mean(
                    [r["saving"] for r in sub], [r["instance"] for r in sub])
                perday = _nanmean([r["saving_per_day"] for r in sub])
                L.append(f"  {sc:>7s} {n:6d} {ni:6d} {mn:10.4f} {_ci(lo, hi)} "
                         f"{perday:11.3f}")
            # trend test: rank correlation between n and the saving, which
            # needs no functional form, plus a cluster-robust log-n slope.
            sub = [r for r in recs if r["battery_ratio"] == b
                   and math.isfinite(r["n"]) and r["n"] > 0]
            if len(sub) >= 10:
                rho, p = _spearman([r["n"] for r in sub], [r["saving"] for r in sub])
                y = np.array([r["saving"] for r in sub])
                X = np.column_stack([np.ones(len(sub)),
                                     np.log([r["n"] for r in sub])])
                beta, se, _r2, G, usable = ols_cluster(
                    X, y, np.array([r["shop"] for r in sub]))
                L.append(f"    trend: Spearman rho(n, saving) = {rho:+.4f} "
                         f"(p {p:.3g});  d(saving)/d(log n) = {beta[1]:+.4f} "
                         f"(se {se[1]:.4f}, clustered by shop, {G} shops)")
                if not usable:
                    L.append(f"           (SE not interpretable: only {G} shop "
                             f"cluster(s); see MIN_CLUSTERS)")

    # ---- 2. GA quality and run time vs n ----------------------------------
    L += _sec("2. Solver behaviour as n grows")
    L += ["  Where the MILP still returns, the gap is to the MILP; where it does",
          "  not, the reference is the best objective ANY method reached on the",
          "  same instance x battery cell, which is a distance to an incumbent",
          "  and not to the optimum. The reference used is printed per class."]
    cells = _cells(R, ("instance", "battery_ratio", "method"))
    best = defaultdict(lambda: float("inf"))
    has_milp = defaultdict(bool)
    for (inst, b, meth), v in cells.items():
        best[(inst, b)] = min(best[(inst, b)], v)
        if meth == "MILP":
            has_milp[(inst, b)] = True
    qrows = []
    L.append(f"  {'class':>7s} {'method':8s} {'n obs':>6s} {'ref':>10s} "
             f"{'norm gap %':>11s} {'p90':>9s} {'median s':>10s} {'p90 s':>9s}")
    # The GAP is computed on the SEED-COLLAPSED cell (`cells` is
    # A.collapse_seeds with how='mean'), not on the individual run: a cell
    # measured with five seeds must not enter the average five times, and its
    # reference is already a seed mean, so comparing a raw run against it mixes
    # one draw with a mean of five. Wall time stays per RUN, because a run time
    # is a property of a run and not of a configuration.
    agg = defaultdict(list)         # gaps, one per instance x battery x method
    tagg3 = defaultdict(list)       # wall seconds, one per run
    flags = defaultdict(list)
    for (i, b, meth), v in cells.items():
        ref = cells.get((i, b, "MILP")) if has_milp[(i, b)] else best[(i, b)]
        sc = meta[i]["scale"]
        gap = (100.0 * (v - ref) / sc
               if ref is not None and math.isfinite(sc) and sc > 0 else float("nan"))
        agg[(meta[i]["size_class"], meth)].append(gap)
        flags[(meta[i]["size_class"], meth)].append(has_milp[(i, b)])
    for r in R:
        tagg3[(meta[r["instance"]]["size_class"], r.get("method", "?"))].append(
            _num(r, "wall_seconds"))
    for (sc, meth), v in sorted(agg.items(), key=lambda kv: (_lvlkey(kv[0][0]), kv[0][1])):
        g = np.array(v, dtype=float); g = g[np.isfinite(g)]
        t = np.array(tagg3.get((sc, meth), []), dtype=float)
        t = t[np.isfinite(t)] if len(t) else t
        fl = flags[(sc, meth)]
        refname = "MILP" if all(fl) else ("mixed" if any(fl) else "best-known")
        L.append(f"  {sc:>7s} {meth:8s} {len(v):6d} {refname:>10s} "
                 f"{(g.mean() if len(g) else float('nan')):11.4f} "
                 f"{(np.percentile(g, 90) if len(g) else float('nan')):9.4f} "
                 f"{(np.median(t) if len(t) else float('nan')):10.2f} "
                 f"{(np.percentile(t, 90) if len(t) else float('nan')):9.2f}")
        qrows.append(dict(size_class=sc, method=meth, n=len(v), reference=refname,
                          mean_norm_gap=float(g.mean()) if len(g) else "",
                          p90_norm_gap=float(np.percentile(g, 90)) if len(g) else "",
                          median_s=float(np.median(t)) if len(t) else "",
                          p90_s=float(np.percentile(t, 90)) if len(t) else ""))
    _write_csv(out, "m3_solver.csv", qrows)
    # Empirical scaling exponent of the run time. A slope near 0 means the time
    # limit binds, not the problem size -- which is a statement about the
    # budget, not about the algorithm.
    for meth in sorted({r.get("method", "?") for r in R}):
        pts = [(meta[r["instance"]]["n"], _num(r, "wall_seconds"))
               for r in R if r.get("method") == meth]
        pts = [(a, b) for a, b in pts if a > 0 and math.isfinite(b) and b > 0]
        if len(pts) >= 10:
            X = np.column_stack([np.ones(len(pts)), np.log([a for a, _ in pts])])
            beta, *_ = np.linalg.lstsq(X, np.log([b for _, b in pts]), rcond=None)
            L.append(f"    {meth:8s} empirical time exponent d(log t)/d(log n) = "
                     f"{beta[1]:+.3f}")

    # ---- 3. size vs structure ---------------------------------------------
    L += _sec("3. Is it size, or is it structure?")
    L += ["  EI density and due-date tightness enter as covariates. If the log-n",
          "  coefficient collapses once they are in, what looked like a size",
          "  effect was a structural one. horizon_days is included ON PURPOSE",
          "  even though it is collinear with n: the VIF that results is the",
          "  honest measure of how little the two can be separated here."]
    sub = [r for r in recs if math.isfinite(r["n"]) and r["n"] > 0
           and math.isfinite(r["ei"]) and math.isfinite(r["slack"])
           and math.isfinite(r["days"])]
    if len(sub) < 20:
        L.append("  Too few complete records for the covariate model.")
    else:
        cov = ["log_n", "ei_density", "due_slack", "horizon_days"]
        raw = np.column_stack([np.log([r["n"] for r in sub]),
                               [r["ei"] for r in sub],
                               [r["slack"] for r in sub],
                               [r["days"] for r in sub]])
        y = np.array([r["saving"] for r in sub], dtype=float)
        cl = np.array([r["shop"] for r in sub])
        # standardised so the coefficients are comparable "per 1 SD" effects
        mu, sd = raw.mean(axis=0), raw.std(axis=0, ddof=1)
        sd[sd == 0] = 1.0
        Z = (raw - mu) / sd
        b1, s1, r21, G, usable = ols_cluster(
            np.column_stack([np.ones(len(sub)), Z[:, :1]]), y, cl)
        b2, s2, r22, _G2, _u2 = ols_cluster(
            np.column_stack([np.ones(len(sub)), Z]), y, cl)
        L += [f"  n = {len(sub)},  {G} shop clusters"]
        L += _cluster_note(G, usable)
        L += [f"  log_n alone:        beta = {b1[1]:+.4f} (se {s1[1]:.4f}), "
              f"R2 = {r21:.4f}",
              f"  with covariates:    R2 = {r22:.4f}",
              f"  {'covariate':16s} {'beta/SD':>10s} {'se':>10s} {'t':>8s} "
              f"{'p(holm)':>9s} {'VIF':>8s}"]
        praw = {nm: _coef_p(b2[i + 1], s2[i + 1], max(1, G - 1), usable)
                for i, nm in enumerate(cov)}
        padj = _holm(praw)
        # VIF on the CENTRED AND SCALED block Z (and without the intercept):
        # that is what makes "how much of log_n do the others already explain"
        # the question being answered.
        vifs = dict(_vif(Z, cov))
        for i, nm in enumerate(cov):
            L.append(f"  {nm:16s} {b2[i + 1]:+10.4f} {s2[i + 1]:10.4f} "
                     f"{_coef_t(b2[i + 1], s2[i + 1], usable):8.2f} "
                     f"{padj[nm]:9.4f} {vifs.get(nm, float('nan')):8.2f}")
        shrink = (1 - abs(b2[1]) / abs(b1[1])) * 100 if abs(b1[1]) > 1e-12 else float("nan")
        L.append(f"  the log-n coefficient moves by {shrink:.0f} % once structure "
                 f"is controlled for.")
        # max() over an empty generator raises; an all-NaN VIF column is the
        # normal state of a slice with fewer rows than covariates.
        finite_vifs = [v for v in vifs.values() if math.isfinite(v)]
        if finite_vifs and max(finite_vifs) > 5:
            L.append("  VIF above 5: log_n and horizon_days are not separately "
                     "identified. Report the pair as one 'scale' factor.")
        elif not finite_vifs:
            L.append("  VIF not computable on this slice (too few rows for the "
                     "covariate block).")
    return _emit(out, "m3_scaling.txt", L)


# ---------------------------------------------------------------------------
# M4 -- state x storage substitution
# ---------------------------------------------------------------------------

@_guard
def m4(rows: list[dict], out: Path, **kw) -> str:
    """Are machine-state flexibility and storage substitutes or complements?

    The decomposition is against the STATUS QUO cell -- always hot, no battery
    -- because that is the plant that has neither lever and is the one being
    advised:

        V_sigma = Z(s1,0) - Z(s3,0)        state flexibility alone
        V_beta  = Z(s1,0) - Z(s1,b)        storage alone
        V_joint = Z(s1,0) - Z(s3,b)        both
        I       = V_joint - V_sigma - V_beta
        SI      = -I / min(V_sigma, V_beta)

    SI > 0 means the levers compete for the same arbitrage and a plant that
    already has one should discount the other. The estimator is CALIBRATED on a
    flat-tariff placebo cell, where no interaction can exist by construction:
    an SI materially different from zero there is a property of the estimator,
    not of the plant.

    SIGN CONVENTION -- READ THIS BEFORE QUOTING V_beta.

      * Z is the TOTAL OBJECTIVE of the cell (energy + weighted tardiness),
        the SAME metric M1 measures its savings on, not energy alone. Each V
        is a difference "status quo minus the lever", divided by
        A.norm_scale and multiplied by 100, so it is in % of the naive
        energy bill of the instance but computed on the total objective.

      * A POSITIVE V is an ECONOMY: that lever lowers the total cost. A
        NEGATIVE V is a SURCHARGE: it raises it. V_beta < 0 therefore reads
        "installing the battery costs this plant money", and it is a
        legitimate outcome, not a sign error: storage buys cheap energy by
        shifting production, and shifting production is paid for in
        tardiness. Whenever V_beta is negative here, M5's dTard for the same
        arm is positive and pays for M5's negative dEnergy -- the two reports
        are two views of one trade-off, and if they ever disagree in sign
        about the same arm THAT is the bug worth chasing.

      * Both halves are kept in Z on purpose. M4 advises a plant on whether
        to buy a battery, and a plant pays for late orders as well as for
        electricity, so netting the tardiness out would advertise an economy
        the plant never banks. The energy-only view is M5's, deliberately
        reported separately rather than folded in here.

      * SI = -I / min(|V_sigma|, |V_beta|) keeps that sign: SI > 0 means
        substitutes (the second lever adds less than it would alone), SI < 0
        complements. Because SI divides by a main effect, it is REFUSED
        rather than printed whenever that denominator is at or below the
        flat-tariff noise floor measured below.
    """
    R = _prep(rows, ("M4",))
    L = _hdr("M4 - machine states x storage: substitutes or complements?")
    if not R:
        return _emit(out, "m4_substitution.txt", L + ["", "  NO M4 DATA."])
    meta = _meta(R)
    L += _inventory(R, meta)
    states = sorted({r["state_policy"] for r in R if r.get("state_policy")})
    L.append(f"  state policies: {', '.join(states) or '-'} "
             f"(design.STATE_POLICIES, GA-only by construction)")
    if not ({"sigma1", "sigma3"} <= set(states)):
        return _emit(out, "m4_substitution.txt",
                     L + ["", "  The decomposition needs BOTH sigma1 (always hot,",
                          "  the status quo) and sigma3 (full model). Missing one",
                          "  of them, so V_sigma and the interaction are NOT",
                          "  ESTIMABLE. Nothing is reported rather than reporting",
                          "  a policy effect labelled as a state effect."])

    cells = _cells(R, ("instance", "state_policy", "battery_ratio"))
    ratios = [b for b in sorted({r["battery_ratio"] for r in R}) if b > 0]
    regimes = sorted({meta[i]["regime"] for i in {r["instance"] for r in R}})

    def decomp(insts, b):
        """Paired arrays for one (regime, capacity) cell; list-wise complete."""
        keep = [i for i in insts
                if all((i, s, bb) in cells
                       for s, bb in (("sigma1", 0.0), ("sigma3", 0.0),
                                     ("sigma1", b), ("sigma3", b)))
                and math.isfinite(meta[i]["scale"]) and meta[i]["scale"] > 0]
        if not keep:
            return None
        sc = np.array([meta[i]["scale"] for i in keep])
        Z00 = np.array([cells[(i, "sigma1", 0.0)] for i in keep])
        Z10 = np.array([cells[(i, "sigma3", 0.0)] for i in keep])
        Z01 = np.array([cells[(i, "sigma1", b)] for i in keep])
        Z11 = np.array([cells[(i, "sigma3", b)] for i in keep])
        Vs = 100.0 * (Z00 - Z10) / sc
        Vb = 100.0 * (Z00 - Z01) / sc
        Vj = 100.0 * (Z00 - Z11) / sc
        return keep, Vs, Vb, Vj, Vj - Vs - Vb

    # ---- the resolution floor of this experiment, measured first ----------
    # Under a constant price storage cannot create value, so V_beta measured on
    # the flat cell is pure solver noise. That number is what makes SI
    # reportable at all: SI divides by min(|V_sigma|, |V_beta|), and when the
    # smaller of the two is itself noise the ratio is unbounded. The self-test
    # found exactly this -- an SI of 28 in a cell where the true value is zero.
    # SI is therefore REFUSED, not printed, when its denominator is below the
    # floor. A refused SI is a statement about identification, not a missing
    # number.
    m4_floor = 0.0
    floor_measured = False          # a MEASURED floor of 0.0 is not a missing one
    flat_insts = [i for i in {r["instance"] for r in R}
                  if meta[i]["regime"] == "flat"]
    for b in ratios:
        d = None
        if flat_insts:
            keep = [i for i in flat_insts
                    if (i, "sigma1", 0.0) in cells and (i, "sigma1", b) in cells
                    and math.isfinite(meta[i]["scale"]) and meta[i]["scale"] > 0]
            if keep:
                d = np.array([100.0 * (cells[(i, "sigma1", 0.0)]
                                       - cells[(i, "sigma1", b)]) / meta[i]["scale"]
                              for i in keep])
        if d is not None and len(d):
            floor_measured = True
            m4_floor = max(m4_floor, float(np.abs(np.mean(d))),
                           float(np.abs(d).mean()))
    # The floor being EXACTLY zero is the strongest possible calibration -- the
    # estimator returned nothing where nothing exists -- so it must not be
    # reported as "no flat cell". Testing the value instead of the presence of
    # the cell printed "NO FLAT CELL: uncalibrated" on the smoke run, which does
    # contain flat cells and does measure a floor of 0.
    L += ["", f"  RESOLUTION FLOOR (|V_beta| measured under the flat tariff, where",
          f"  it must be zero): {m4_floor:.5f} % of the naive bill."
          if floor_measured else
          "  NO FLAT CELL: the floor is unknown and every SI below is uncalibrated."]
    if floor_measured:
        L += ["  SI is refused wherever min(|V_sigma|, |V_beta|) falls below it."
              if m4_floor > 0 else
              "  The floor is exactly zero: the estimator returned no value where "
              "none exists,",
              "  which is the strongest calibration available. SI is then refused "
              "only on a", "  denominator that is itself exactly zero."]

    def si_of(v):
        """SI from a vector of (mean Vs, mean Vb, mean I) -- bootstrap-friendly.

        Returns nan when the denominator is at or below the measured noise
        floor: dividing a noise-sized interaction by a noise-sized main effect
        produces a number with no content and an enormous magnitude.
        """
        mn = min(abs(v[0]), abs(v[1]))
        return -v[2] / mn if mn > max(m4_floor, 1e-12) else float("nan")

    out_rows = []
    for regime in regimes:
        insts = sorted({r["instance"] for r in R
                        if meta[r["instance"]]["regime"] == regime})
        placebo = (regime == "flat")
        L += _sec(f"{'PLACEBO ' if placebo else ''}tariff regime: {regime}"
                  f"{'  (no arbitrage is possible here)' if placebo else ''}")
        if placebo:
            L += ["  CALIBRATION CELL -- and it calibrates THREE things that must",
                  "  not be confused with one another. Under a constant price:",
                  "",
                  "    V_beta MUST be ~0. Storage can only move energy through",
                  "      time, and with round-trip efficiency below 1 moving it",
                  "      strictly loses energy, so the optimum is not to cycle at",
                  "      all. Anything non-zero here is solver noise.",
                  "",
                  "    I MUST be ~0. An interaction needs the two levers to",
                  "      compete for the same arbitrage opportunity, and there is",
                  "      none. This is the quantity the placebo is read on.",
                  "",
                  "    V_sigma MUST NOT be 0, and this is the part that is easy",
                  "      to get wrong. Machine states change how much energy is",
                  "      CONSUMED (e_idle < e_proc, e_off = 0, plus transition",
                  "      costs), not merely when it is bought. Shutting down",
                  "      between jobs saves money at a constant price exactly as",
                  "      it does at a volatile one. A flat-tariff V_sigma of zero",
                  "      is therefore NOT a clean placebo -- it is evidence that",
                  "      --states never reached the model, and every M4 number",
                  "      would then be measuring nothing. See the verdict below."]
        # TWO PASSES, because there is ONE family per regime and it has to be
        # complete before any p in it can be adjusted. The previous version
        # corrected the printed column against the four effects of one capacity
        # and the trailing block against all four at every capacity, so the same
        # test carried two different adjusted p-values in the same section.
        # V_sigma is also entered ONCE per regime and not once per capacity: it
        # does not depend on b (it is Z(s1,0) - Z(s3,0)), so counting it at
        # every capacity inflates the family with copies of one test.
        blocks, fam = [], {}
        for b in ratios:
            d = decomp(insts, b)
            if d is None:
                blocks.append((b, None))
                continue
            keep, Vs, Vb, Vj, I = d
            stats = [paired_summary(Vs, "V_sigma  (states only)"),
                     paired_summary(Vb, "V_beta   (storage only)"),
                     paired_summary(Vj, "V_joint  (both)"),
                     paired_summary(I, "I        (interaction)")]
            M = np.column_stack([Vs, Vb, I])
            si, si_lo, si_hi = _matrix_boot(M, si_of)
            keys = {}
            for s in stats:
                short = s["effect"][:7].strip()
                key = (f"{regime}|{short}" if short == "V_sigma"
                       else f"{regime}|b={_g(b)}|{short}")
                keys[s["effect"]] = key
                fam[key] = _p_from_t(s["t"], max(1, s["n"] - 1))
            blocks.append((b, (keep, Vs, Vb, Vj, I, stats, keys,
                               si, si_lo, si_hi)))
        padj = _holm(fam) if fam else {}

        for b, blk in blocks:
            if blk is None:
                L.append(f"  b = {_g(b)}: no complete sigma1/sigma3 x 0/b cells")
                continue
            keep, Vs, Vb, Vj, I, stats, keys, si, si_lo, si_hi = blk
            L += [f"\n  b = {_g(b)} E_day   n = {len(keep)} instances "
                  f"(% of the naive bill, on the TOTAL objective, vs the "
                  f"always-hot no-battery cell;",
                  f"   positive = economy, negative = surcharge -- see the "
                  f"docstring)",
                  f"    {'effect':26s} {'mean':>9s} {'95% CI':>22s} {'dz':>7s} "
                  f"{'p(holm)':>9s}"]
            for s in stats:
                # A degenerate effect (every instance identical, so sd = 0) has
                # no dz and no p. Printing mean/sd there gave dz = 56077 and,
                # through Holm, p = 0: a deterministic quantity reported as the
                # most significant finding in the table.
                dz = ("      ." if s.get("degenerate")
                      else f"{s['cohens_dz']:7.3f}")
                L.append(f"    {s['effect']:26s} {s['mean']:9.4f} "
                         f"{_ci(s['ci_lo'], s['ci_hi'])} {dz} "
                         f"{padj.get(keys[s['effect']], float('nan')):9.4f}")
            if any(s.get("degenerate") for s in stats):
                L.append("      ('.' in dz: no dispersion across instances -- the "
                         "effect is identical")
                L.append("       in every instance, so it is deterministic and has "
                         "no t, dz or p.)")
            if math.isfinite(si):
                verdict = ("SUBSTITUTES" if si > 0.05 else
                           "COMPLEMENTS" if si < -0.05 else "approximately ADDITIVE")
                L.append(f"    substitution index SI = {si:+.4f} "
                         f"{_ci(si_lo, si_hi)}  -> {verdict}")
            else:
                L.append(f"    substitution index SI = NOT IDENTIFIED: "
                         f"min(|V_sigma|,|V_beta|) = "
                         f"{min(abs(Vs.mean()), abs(Vb.mean())):.5f} % is at or "
                         f"below the {m4_floor:.5f} % noise floor.")
                L.append("      (a ratio to a noise-sized denominator is "
                         "unbounded; the interaction I above is still readable)")
            out_rows.append(dict(regime=regime, battery_ratio=b, n=len(keep),
                                 V_sigma=float(Vs.mean()), V_beta=float(Vb.mean()),
                                 V_joint=float(Vj.mean()), I=float(I.mean()),
                                 SI=si, SI_lo=si_lo, SI_hi=si_hi,
                                 placebo=placebo))
        if fam:
            L += ["", f"  Holm-adjusted p within regime '{regime}' "
                      f"(ONE family: the four effects at every capacity,",
                  f"   V_sigma entered once because it does not depend on b) -- "
                  f"{len(fam)} tests"]
            for k in sorted(padj, key=lambda x: (math.isnan(padj[x]), padj[x]))[:12]:
                L.append(f"    {k:40s} p_holm = {padj[k]:.4g}")
    _write_csv(out, "m4_decomposition.csv", out_rows)

    # The capacity-dependence statement: substitution measured only at a
    # saturating battery describes an asset nobody buys (v1's E1 measured
    # everything at 1.0 E_day and then E2 found 1.0 NPV-negative everywhere).
    L += _sec("Does the substitution survive at a capacity a plant would buy?")
    L += [f"  design.M4_BATTERY_RATIOS = {design.M4_BATTERY_RATIOS}; the capacity",
          f"  a plant would actually install is around "
          f"{design.BATTERY_ON_RATIO} E_day (design.py §4).",
          f"  {'regime':14s} " + "".join(f"{('SI at b=' + _g(b)):>16s}"
                                         for b in ratios)]
    for regime in regimes:
        cells_txt = ""
        for b in ratios:
            m = [r for r in out_rows if r["regime"] == regime
                 and r["battery_ratio"] == b]
            cells_txt += (f"{m[0]['SI']:16.4f}" if m else f"{'.':>16s}")
        L.append(f"  {regime[:14]:14s}{cells_txt}")
    plac = [r for r in out_rows if r["placebo"]]
    if plac:
        # The placebo is read on the INTERACTION, not on SI: under a flat
        # tariff V_beta is zero by construction, so SI's denominator is zero
        # and the ratio is undefined rather than merely large. I is the
        # quantity the estimator must return as zero, and it does have a scale.
        worst_i = max(abs(r["I"]) for r in plac if math.isfinite(r["I"]))
        refused = sum(1 for r in plac if not math.isfinite(r["SI"]))
        L += ["", f"  PLACEBO CALIBRATION (flat tariff):",
              f"    max |I| = {worst_i:.5f} % of the naive bill over "
              f"{len(plac)} cells   <- must be ~0",
              f"    SI refused as not identified in {refused} of {len(plac)} "
              f"flat cells (denominator below the noise floor) -- which is the",
              "    correct behaviour, not a gap in the table.",
              "  Any interaction in the other regimes smaller than max |I| here",
              "  is the estimator talking, not the plant."]
        # The positive half of the placebo: V_sigma must be non-zero under a
        # flat tariff, because machine states change CONSUMPTION and not only
        # its timing. A zero here does not calibrate the estimator, it condemns
        # the experiment -- so it is checked rather than assumed, and the
        # verdict is printed next to the negative half so the two cannot be
        # read as one.
        vs_flat = [r["V_sigma"] for r in plac if math.isfinite(r.get("V_sigma", float("nan")))]
        if vs_flat:
            vmax = max(abs(v) for v in vs_flat)
            floor = max(worst_i, 1e-9)
            if vmax > 5.0 * floor:
                verdict = ("OK -- state management has real value at a constant "
                           "price, as it must")
            else:
                verdict = ("FAILED -- V_sigma is indistinguishable from the "
                           "interaction noise floor.\n"
                           "               Machine states appear not to reach "
                           "the model. Check --states\n"
                           "               with RUNBOOK_SERVER.md step 3(b) "
                           "before reading ANY M4 result.")
            L += ["",
                  f"    max |V_sigma| = {vmax:.5f} % over the same cells   "
                  f"<- must be clearly NON-zero",
                  f"    verdict: {verdict}",
                  "    (V_sigma under a flat tariff is the pure consumption",
                  "     saving of switching the machine off: no arbitrage, just",
                  "     less energy. It is a result in its own right and worth",
                  "     quoting -- it is the part of state management that does",
                  "     not depend on the tariff at all.)"]
    else:
        L += ["", "  NO FLAT-TARIFF CELL: the estimator is UNCALIBRATED and no SI",
              "  above should be treated as an established magnitude."]
    return _emit(out, "m4_substitution.txt", L)


# ---------------------------------------------------------------------------
# M5 -- the service/energy frontier
# ---------------------------------------------------------------------------

@_guard
def m5(rows: list[dict], out: Path, **kw) -> str:
    """The service-energy frontier, traced by lambda, with and without storage.

    THE CLAIM UNDER TEST: storage moves the frontier INWARD ALMOST VERTICALLY
    -- it buys energy savings without paying for them in delivery performance.
    That is the managerially interesting version of the result, and it is
    falsifiable: if the tardiness shift is significantly positive at the same
    lambda, storage is simply buying the same trade-off the scheduler could
    have bought by relaxing due dates, and the paper must say so.

    Lambda is baked into the instance file (task weights), so two lambdas are
    different instances. Pairing across lambda uses the SHOP STRUCTURE key
    (the shop id with its lambda suffix stripped) crossed with the price
    series; pairing across battery levels is exact, on the instance.

    SIGN CONVENTION -- DELIBERATELY THE OPPOSITE OF M1's AND M4's.

      * M1 and M4 report SAVINGS on the TOTAL objective: positive = economy.
      * M5 reports signed DELTAS of the two halves taken SEPARATELY:

            dEnergy = 100 x (energy_cost at b_hi - energy_cost at b_lo) / scale
            dTard   = 100 x (tard_cost   at b_hi - tard_cost   at b_lo) / scale

        so here NEGATIVE = improvement (the battery lowered that half) and
        POSITIVE = deterioration. dEnergy < 0 is the battery doing its job;
        dTard > 0 is the plant paying for it in delivery performance.

      This is not an inconsistency, it is the point of the report: M5 exists to
      show WHERE the money in M1/M4 comes from and what it costs. The two are
      tied by an identity -- up to the seed means, M4's V_beta on a comparable
      arm is approximately -(dEnergy + dTard) -- so a NEGATIVE V_beta in M4
      must correspond to dTard > |dEnergy| here. Reading a negative V_beta as a
      sign bug without checking that correspondence is how a real trade-off
      gets refactored away.

      Concretely: "traded off" in the verdict column below means dTard is
      significantly positive, i.e. the total-objective saving in M1/M4 is being
      eaten (and can go negative) even though the energy bill genuinely fell.
    """
    R = _prep(rows, ("M5",))
    L = _hdr("M5 - the service-energy frontier")
    if not R:
        return _emit(out, "m5_frontier.txt", L + ["", "  NO M5 DATA."])
    meta = _meta(R)
    L += _inventory(R, meta)
    lams = sorted({meta[r["instance"]]["lam"] for r in R
                   if math.isfinite(meta[r["instance"]]["lam"])})
    ratios = sorted({r["battery_ratio"] for r in R})
    L += [f"  lambda levels: {[_g(x) for x in lams]}",
          f"  battery levels: {[_g(b) for b in ratios]}",
          "  energy and tardiness are both divided by A.norm_scale, so the",
          "  exchange rate below is dimensionless and comparable across sizes."]
    if len(lams) < 2:
        L.append("  Fewer than two lambda levels: no frontier to trace.")
    if len(ratios) < 2:
        L.append("  Only one battery level: the frontier SHIFT is not estimable.")

    ecells = _cells(R, ("instance", "battery_ratio"), value="energy_cost")
    tcells = _cells(R, ("instance", "battery_ratio"), value="tardiness_cost")

    # ---- 1. the frontier ---------------------------------------------------
    L += _sec("1. Pareto frontier (energy, tardiness) by lambda and capacity")
    L.append(f"  {'lambda':>8s} {'b':>6s} {'inst':>6s} {'energy (norm)':>15s} "
             f"{'tardiness (norm)':>18s} {'raw energy':>13s} {'raw tard':>12s}")
    front = {}
    frows = []
    for lam in lams:
        for b in ratios:
            sel = [i for i in {r["instance"] for r in R}
                   if meta[i]["lam"] == lam and (i, b) in ecells
                   and math.isfinite(meta[i]["scale"]) and meta[i]["scale"] > 0]
            if not sel:
                continue
            e = np.array([ecells[(i, b)] / meta[i]["scale"] for i in sel])
            t = np.array([tcells.get((i, b), float("nan")) / meta[i]["scale"]
                          for i in sel])
            re_ = np.array([ecells[(i, b)] for i in sel])
            rt = np.array([tcells.get((i, b), float("nan")) for i in sel])
            front[(lam, b)] = (float(e.mean()), _nanmean(t), sel)
            L.append(f"  {lam:8.2f} {b:6.2f} {len(sel):6d} {e.mean():15.5f} "
                     f"{_nanmean(t):18.5f} {re_.mean():13.2f} "
                     f"{_nanmean(rt):12.2f}")
            frows.append(dict(lam=lam, battery_ratio=b, n=len(sel),
                              energy_norm=float(e.mean()),
                              tard_norm=_nanmean(t),
                              energy_raw=float(re_.mean()),
                              tard_raw=_nanmean(rt)))
    _write_csv(out, "m5_frontier.csv", frows)

    # ---- 2. exchange rate --------------------------------------------------
    # Paired on the STRUCTURE across adjacent lambdas: the same shop structure
    # and the same price series appear at every lambda, so a per-structure
    # slope is a within-plant trade-off rather than a difference of two group
    # means over different plants.
    L += _sec("2. Exchange rate along the frontier: dEnergy / dTardiness")
    L += ["  Per structure, between adjacent lambda levels, then aggregated.",
          "  Negative = extra tardiness buys energy savings, which is what a",
          "  frontier is. A rate near zero means the lever has stopped working."]
    by_struct = defaultdict(dict)
    for i in {r["instance"] for r in R}:
        m = meta[i]
        if not (math.isfinite(m["scale"]) and m["scale"] > 0):
            continue
        for b in ratios:
            if (i, b) in ecells:
                by_struct[(m["struct"], m["regime"], b)][m["lam"]] = (
                    ecells[(i, b)] / m["scale"],
                    tcells.get((i, b), float("nan")) / m["scale"])
    xrows = []
    L.append(f"  {'b':>6s} {'lambda step':>18s} {'pairs':>6s} {'median dE/dT':>14s} "
             f"{'mean':>10s} {'95% CI':>22s}")
    for b in ratios:
        for lo_l, hi_l in zip(lams, lams[1:]):
            rates = []
            for key, d in by_struct.items():
                if key[2] != b or lo_l not in d or hi_l not in d:
                    continue
                (e0, t0), (e1, t1) = d[lo_l], d[hi_l]
                if not all(math.isfinite(x) for x in (e0, t0, e1, t1)):
                    continue
                if abs(t1 - t0) > 1e-12:
                    rates.append((e1 - e0) / (t1 - t0))
            if len(rates) < 3:
                continue
            a = np.array(rates)
            lo, hi = boot_ci(a)
            L.append(f"  {b:6.2f} {f'{lo_l:g} -> {hi_l:g}':>18s} {len(a):6d} "
                     f"{np.median(a):14.4f} {a.mean():10.4f} {_ci(lo, hi)}")
            xrows.append(dict(battery_ratio=b, lam_from=lo_l, lam_to=hi_l,
                              pairs=len(a), median=float(np.median(a)),
                              mean=float(a.mean()), ci_lo=lo, ci_hi=hi))
    if not xrows:
        L.append("  No structure paired across adjacent lambda levels.")
    _write_csv(out, "m5_exchange_rate.csv", xrows)

    # ---- 3. the claim: is the shift vertical? -----------------------------
    L += _sec("3. THE CLAIM: does storage move the frontier inward, vertically?")
    L += ["  Paired on the instance at a fixed lambda, so nothing but the",
          "  battery changes. dEnergy < 0 with dTardiness indistinguishable from",
          "  zero is the claim; dTardiness significantly > 0 falsifies it and",
          "  makes storage just another way to buy the same trade-off."]
    if len(ratios) < 2:
        L.append("  Not estimable with one battery level.")
    else:
        b0, b1 = ratios[0], ratios[-1]
        L.append(f"  comparing b = {_g(b1)} against b = {_g(b0)}")
        L.append(f"  {'lambda':>8s} {'n':>5s} {'dEnergy':>10s} {'95% CI':>22s} "
                 f"{'dTard':>10s} {'95% CI':>22s} {'|dT/dE|':>9s} {'verdict':>12s}")
        # TWO families, not one union. "did the battery move energy" and "did
        # the battery cost service" are two different questions asked over the
        # same lambda ladder; correcting the 2 x len(lams) tests together makes
        # each question pay for the other's power, and the verdict column reads
        # them separately anyway.
        fam_e, fam_t, srows = {}, {}, []
        for lam in lams:
            sel = [i for i in {r["instance"] for r in R}
                   if meta[i]["lam"] == lam and (i, b0) in ecells and (i, b1) in ecells
                   and math.isfinite(meta[i]["scale"]) and meta[i]["scale"] > 0]
            if len(sel) < 3:
                continue
            dE = np.array([100.0 * (ecells[(i, b1)] - ecells[(i, b0)]) / meta[i]["scale"]
                           for i in sel])
            dT = np.array([100.0 * (tcells.get((i, b1), float("nan"))
                                    - tcells.get((i, b0), float("nan"))) / meta[i]["scale"]
                           for i in sel])
            se_ = paired_summary(dE, "dE")
            st_ = paired_summary(dT, "dT")
            ratio = (abs(st_["mean"]) / abs(se_["mean"])
                     if abs(se_["mean"]) > 1e-12 else float("nan"))
            vertical = (se_["ci_hi"] < 0 and st_["ci_lo"] <= 0 <= st_["ci_hi"])
            verdict = ("VERTICAL" if vertical else
                       "traded off" if st_["ci_lo"] > 0 else
                       "no shift" if se_["ci_lo"] <= 0 <= se_["ci_hi"] else "both improve")
            L.append(f"  {lam:8.2f} {len(sel):5d} {se_['mean']:10.4f} "
                     f"{_ci(se_['ci_lo'], se_['ci_hi'])} {st_['mean']:10.4f} "
                     f"{_ci(st_['ci_lo'], st_['ci_hi'])} {ratio:9.3f} {verdict:>12s}")
            fam_e[f"dEnergy@lam={_g(lam)}"] = _p_from_t(
                se_["t"], max(1, se_["n"] - 1))
            fam_t[f"dTard@lam={_g(lam)}"] = _p_from_t(
                st_["t"], max(1, st_["n"] - 1))
            srows.append(dict(lam=lam, n=len(sel), dEnergy=se_["mean"],
                              dE_lo=se_["ci_lo"], dE_hi=se_["ci_hi"],
                              dTard=st_["mean"], dT_lo=st_["ci_lo"],
                              dT_hi=st_["ci_hi"], ratio=ratio, verdict=verdict))
        _write_csv(out, "m5_shift.csv", srows)
        if fam_e or fam_t:
            adj = dict(_holm(fam_e))
            adj.update(_holm(fam_t))
            L += ["", "  Holm-adjusted p (TWO families, corrected separately:",
                  "   the energy shift across lambda, and the tardiness shift "
                  "across lambda)"]
            for k in sorted(adj, key=lambda x: (math.isnan(adj[x]), adj[x])):
                L.append(f"    {k:28s} p_holm = {adj[k]:.4g}")
        nvert = sum(1 for r in srows if r["verdict"] == "VERTICAL")
        L += ["", f"  {nvert} of {len(srows)} lambda levels show an inward,",
              "  vertical shift. Where the verdict is 'traded off', the battery",
              "  bought its energy saving with delivery performance and the",
              "  headline claim does not hold at that service level."]
    return _emit(out, "m5_frontier.txt", L)


# ---------------------------------------------------------------------------
# registry, so 05_analyse.py can dispatch by name
# ---------------------------------------------------------------------------

REPORTS = {"M0": m0, "M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5}


def run_all(rows: list[dict], out: Path, econ: dict | None = None,
            only: tuple[str, ...] = ()) -> str:
    """Run every managerial analysis and return the combined report."""
    parts = []
    for name, fn in REPORTS.items():
        if only and name not in only:
            continue
        parts.append(fn(rows, out, econ=econ) if name in ("M1", "M2")
                     else fn(rows, out))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------
#
# A synthetic results.csv with KNOWN effects planted in it, used to check that
# every function runs on partial data and recovers what was planted. This is
# not a unit test of the statistics; it is a smoke test with a ground truth, so
# that a refactor that silently inverts a sign is caught before the campaign
# data arrives. Run:  python3 analysis/managerial.py --selftest

_TRUTH = dict(
    ga_gap=0.02,             # GA is 2 % above the MILP, at EVERY battery level
    si=0.40,                 # planted substitution index in M4
    synth_spread_coef=0.02,  # saving (% of naive bill) per EUR/MWh of spread
    real_spread_coef=0.0,    # ... and none at all on real tariffs
    restart_effect=0.30,     # storage is worth 30 % more when restart is costly
)


def _selftest(tmp: Path) -> int:                 # pragma: no cover - dev tool
    import random
    import shutil

    rng = random.Random(12345)
    rows: list[dict] = []

    def sat(b):
        """Saturating capacity response; saturates around b ~ 1."""
        return 1.0 - math.exp(-b / 0.4)

    def base_row(**kw):
        r = dict(run_id=f"r{len(rows):06d}", status="ok", policy="edd",
                 state_policy="sigma3", method="GA", seed="1", time_limit="300",
                 gap="", solver_seconds="1", wall_seconds="10.0",
                 machine_profile="", rho="", restart_level="", m1_subdesign="",
                 tariff_family="", price_market="", price_year="",
                 price_label="", synth_spread="", synth_noise="", synth_neg="")
        r.update(kw)
        return r

    TARIFFS = {  # name -> (regime, family, arbitrage strength, spread, market info)
        "flat":        ("flat", "contractual", 0.0, 0.0, ("", "", "")),
        "tou2":        ("tou2", "contractual", 0.6, 40.0, ("", "", "")),
        "spot_lowvol": ("spot_lowvol", "spot", 0.4, 30.0, ("CZ", "2025", "recent")),
        "spot_midvol": ("spot_midvol", "spot", 0.8, 60.0, ("CZ", "2025", "recent")),
        "spot_highvol": ("spot_highvol", "spot", 1.4, 110.0, ("CZ", "2025", "recent")),
    }
    MACH = {"T1_ideal": 0.6, "T2_fast_electric": 0.8, "T3_baseline": 1.0,
            "T4_thermal_oven": 1.2, "T5_continuous": 1.4, "T6_cheap_idle": 0.9}

    def inst_cols(shop, pname, sc, n, days, spread, pmean=90.0, cv=0.35,
                  neg=0.05, market=("", "", "")):
        e_day = 4.0 * n * 0.35
        return dict(
            instance=f"{shop}__{pname}", inst_shop_id=shop, inst_n=n,
            inst_size_class=sc, size_class=str(sc),
            inst_horizon=days * 24, inst_horizon_days=days,
            inst_e_day=round(e_day, 4), inst_price_mean=pmean,
            inst_price_cv=cv, inst_spread_intraday=spread, inst_neg_share=neg,
            inst_ei_density=0.25, inst_mean_due_slack=12.0,
            ei_density_level="d25", due_tightness_level="med", lam="1.0",
            price_name=pname, price_market=market[0], price_year=market[1],
            price_label=market[2])

    def emit(exp, cols, b, obj_true, **extra):
        """One run row; GA rows carry the planted 2 % gap and seed noise."""
        for seed in (1, 2):
            # solver-like noise: small relative to the planted effects, which
            # is what lets the falsification cells act as a resolution floor
            noise = rng.gauss(0, 0.0002) * abs(obj_true)
            rows.append(base_row(
                experiment=exp, battery_ratio=str(b),
                battery_arg=str(int(b * cols["inst_e_day"])), seed=str(seed),
                objective=obj_true * (1 + _TRUTH["ga_gap"]) + noise,
                energy_cost=obj_true * (1 + _TRUTH["ga_gap"]) + noise - 100.0,
                tardiness_cost=100.0,
                price_regime=extra.pop("regime", "spot_midvol"),
                **cols, **extra))

    # ---- M0: MILP + GA + H1, gap constant across battery levels -----------
    for sc, n in ((1, 32), (2, 64), (4, 128)):
        for rep in range(6):
            shop = f"p{sc:02d}_r{rep:02d}_d25_med_lam0100"
            for pname in ("flat", "tou2", "spot_midvol"):
                reg, fam, arb, spread, mk = TARIFFS[pname]
                days = max(3, n // 8)
                c = inst_cols(shop, pname, sc, n, days, spread, market=mk)
                scale = c["inst_e_day"] * days * 90.0
                for b in (0.0, 0.1):
                    opt = scale * (0.5 - 0.02 * arb * sat(b)) + rng.gauss(0, 1)
                    rows.append(base_row(
                        experiment="M0", method="MILP", seed="0",
                        time_limit="1800", wall_seconds=str(50.0 * sc),
                        gap=("0.0" if sc <= 2 else "0.031"),
                        battery_ratio=str(b), objective=opt,
                        energy_cost=opt - 100.0, tardiness_cost=100.0,
                        price_regime=reg, tariff_family=fam, **c))
                    for seed in (1, 2, 3):
                        rows.append(base_row(
                            experiment="M0", method="GA", seed=str(seed),
                            time_limit="300", wall_seconds="290.0",
                            battery_ratio=str(b),
                            objective=opt * (1 + _TRUTH["ga_gap"])
                            + rng.gauss(0, 0.0008) * opt,
                            energy_cost=opt * 1.02 - 100.0, tardiness_cost=100.0,
                            price_regime=reg, tariff_family=fam, **c))
                    rows.append(base_row(
                        experiment="M0", method="H1", seed="0",
                        time_limit="120", wall_seconds="0.4",
                        battery_ratio=str(b), objective=opt * 1.11,
                        energy_cost=opt * 1.11 - 100.0, tardiness_cost=100.0,
                        price_regime=reg, tariff_family=fam, **c))
                    # anytime sub-cell, only on spot_midvol
                    if pname == "spot_midvol":
                        for tl, extra_gap in ((30, 0.06), (900, 0.015)):
                            rows.append(base_row(
                                experiment="M0", method="GA", seed="1",
                                time_limit=str(tl), wall_seconds=str(tl),
                                battery_ratio=str(b),
                                objective=opt * (1 + extra_gap),
                                energy_cost=opt * (1 + extra_gap) - 100.0,
                                tardiness_cost=100.0, price_regime=reg,
                                tariff_family=fam, **c))

    # ---- M1 cube + M1b grid ------------------------------------------------
    for sc, n in ((2, 64), (4, 128)):
        for rep in range(4):
            shop = f"p{sc:02d}_r{rep:02d}_d25_med_lam0100"
            days = max(3, n // 8)
            for pname, (reg, fam, arb, spread, mk) in TARIFFS.items():
                c = inst_cols(shop, pname, sc, n, days, spread, market=mk)
                scale = c["inst_e_day"] * days * 90.0
                for arch, mult in MACH.items():
                    for b in design.BATTERY_RATIOS:
                        frac = 0.03 * arb * mult * sat(b)
                        obj = scale * (0.5 - frac) + rng.gauss(0, 0.02)
                        emit("M1", c, b, obj, regime=reg, tariff_family=fam,
                             machine_profile=arch, m1_subdesign="cube")
                if pname == design.M1B_TARIFF:
                    for rho in machines.RHO_LEVELS:
                        for rest in machines.RESTART_LEVELS:
                            # planted: prohibitive restart raises the storage
                            # return by _TRUTH["restart_effect"]
                            k = {"low": 1.0, "med": 1.0 + _TRUTH["restart_effect"] / 2,
                                 "prohibitive": 1.0 + _TRUTH["restart_effect"]}[rest]
                            for b in design.M1B_BATTERY_RATIOS:
                                frac = 0.03 * arb * k * (1 + 0.1 * rho) * sat(b)
                                obj = scale * (0.5 - frac) + rng.gauss(0, 0.02)
                                emit("M1", c, b, obj, regime=reg,
                                     tariff_family=fam, rho=str(rho),
                                     restart_level=rest, m1_subdesign="grid")

    # ---- M2: synthetic responds to spread, real does not -------------------
    for rep in range(10):
        shop = f"p02_r{rep:02d}_d25_med_lam0100"
        days = 8
        series = ([("synth_s%g_n%g" % (s, nz), "synthetic", s, ("", "", ""))
                   for s in design.SYNTH_SPREADS for nz in (0.10,)] +
                  [("cz2019_w%d" % w, "real", 25.0 + 5 * w, ("CZ", "2019", "calm"))
                   for w in range(3)] +
                  [("cz2022_w%d" % w, "real", 180.0 + 20 * w, ("CZ", "2022", "crisis"))
                   for w in range(3)] +
                  [("de2025_w%d" % w, "real", 90.0 + 10 * w,
                    ("DE", "2025", "high-renewable")) for w in range(3)] +
                  [("flat", "contractual", 0.0, ("", "", ""))])
        for pname, fam, spread, mk in series:
            c = inst_cols(shop, pname, 2, 64, days, spread, market=mk)
            scale = c["inst_e_day"] * days * 90.0
            for b in design.BATTERY_LADDER:
                if fam == "synthetic":
                    frac = _TRUTH["synth_spread_coef"] / 100.0 * spread * sat(b)
                elif fam == "real":
                    frac = 0.012 * sat(b) + rng.gauss(0, 0.0008)
                else:
                    frac = 0.0
                obj = scale * (0.5 - frac) + rng.gauss(0, 0.02)
                emit("M2", c, b, obj, regime=("synthetic" if fam == "synthetic"
                                              else "flat" if fam == "contractual"
                                              else pname.split("_")[0]),
                     tariff_family=fam)

    # ---- M3: saving flat in n, n and horizon strongly correlated -----------
    for sc, n in ((1, 32), (2, 64), (4, 128), (8, 256)):
        for rep in range(4):
            shop = f"p{sc:02d}_r{rep:02d}_d25_med_lam0100"
            days = max(3, n // 8)          # n and days move together on purpose
            for pname in ("flat", "tou2", "spot_midvol"):
                reg, fam, arb, spread, mk = TARIFFS[pname]
                c = inst_cols(shop, pname, sc, n, days, spread, market=mk)
                scale = c["inst_e_day"] * days * 90.0
                for b in design.BATTERY_LADDER:
                    frac = 0.03 * arb * sat(b)
                    obj = scale * (0.5 - frac) + rng.gauss(0, 0.02)
                    emit("M3", c, b, obj, regime=reg, tariff_family=fam)

    # ---- M4: planted substitution index ------------------------------------
    for rep in range(8):
        shop = f"p02_r{rep:02d}_d25_med_lam0100"
        days = 8
        for pname in design.M4_TARIFFS:
            reg, fam, arb, spread, mk = TARIFFS[pname]
            c = inst_cols(shop, pname, 2, 64, days, spread, market=mk)
            scale = c["inst_e_day"] * days * 90.0
            for st in ("sigma1", "sigma2", "sigma3"):
                for b in design.M4_BATTERY_RATIOS:
                    v_s = {"sigma1": 0.0, "sigma2": 0.010, "sigma3": 0.020}[st]
                    v_b = 0.03 * arb * sat(b)
                    val = v_s + v_b - _TRUTH["si"] * min(v_s, v_b)
                    obj = scale * (0.5 - val) + rng.gauss(0, 0.004)
                    emit("M4", c, b, obj, regime=reg, tariff_family=fam,
                         state_policy=st)

    # ---- M5: battery cuts energy, leaves tardiness alone -------------------
    for rep in range(6):
        for lam in design.LAMBDA_LEVELS:
            shop = f"p02_r{rep:02d}_d25_med_lam{int(lam * 100):04d}"
            days = 8
            c = inst_cols(shop, "spot_midvol", 2, 64, days, 60.0,
                          market=("CZ", "2025", "recent"))
            c["lam"] = str(lam)
            scale = c["inst_e_day"] * days * 90.0
            for b in (0.0, design.BATTERY_ON_RATIO):
                # higher lambda -> less tardiness, more energy cost (frontier)
                energy = scale * (0.40 + 0.03 * math.log(1 + lam)) * (1 - 0.02 * sat(b))
                tard = scale * 0.05 / (1 + lam)
                for seed in (1, 2):
                    rows.append(base_row(
                        experiment="M5", battery_ratio=str(b), seed=str(seed),
                        objective=energy + tard + rng.gauss(0, 0.5),
                        energy_cost=energy + rng.gauss(0, 0.5),
                        tardiness_cost=tard, price_regime="spot_midvol",
                        tariff_family="spot", **c))

    # ---- write, load through the real loader, run every analysis ----------
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "results.csv"
    cols: list[str] = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    loaded = A.load_results(path)
    print(f"selftest: {len(rows)} synthetic runs, {len(loaded)} loaded")

    outdir = tmp / "analysis"
    outdir.mkdir(parents=True, exist_ok=True)
    reports = {}
    for name, fn in REPORTS.items():
        reports[name] = (fn(loaded, outdir, econ=economics.CENTRAL)
                         if name in ("M1", "M2") else fn(loaded, outdir))
        print(f"  {name}: {len(reports[name].splitlines())} lines")

    # ---- checks on the planted effects ------------------------------------
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
        ok = ok and bool(cond)

    for name, txt in reports.items():
        check(f"{name} did not crash", "FAILED (analysis bug" not in txt)

    # M0: the planted GA gap is 2 % of the objective; the objective is about
    # half the naive bill, so the normalised gap should be about 1 %.
    g = [r for r in csv.DictReader((outdir / "m0_gaps.csv").open())
         if r["method"] == "GA"]
    mean_norm = float(np.mean([float(r["norm"]) for r in g]))
    check("M0 recovers the planted GA gap", abs(mean_norm - 1.0) < 0.25,
          f"(norm gap {mean_norm:.3f} %, expected ~1.0)")
    stab = list(csv.DictReader((outdir / "m0_battery_stability.csv").open()))
    ga_stab = [r for r in stab if r["method"] == "GA"]
    check("M0 finds the gap stable across battery levels",
          ga_stab and float(ga_stab[0]["range_norm_pct"]) < 0.1,
          f"(range {ga_stab[0]['range_norm_pct'] if ga_stab else 'n/a'})")

    # M1: flat tariff must show ~zero saving (the falsification floor), and the
    # ordering over archetypes must follow the planted multipliers.
    check("M1 flat-tariff floor is reported", "RESOLUTION FLOOR" in reports["M1"])
    cs = list(csv.DictReader((outdir / "m1_cube_savings.csv").open()))
    flat_max = max(abs(float(r["saving"])) for r in cs if r["regime"] == "flat")
    real_eff = np.mean([float(r["saving"]) for r in cs
                        if r["regime"] == "spot_highvol"
                        and float(r["battery_ratio"]) == 1.0])
    # the floor must sit an order of magnitude below the effects it licenses
    check("M1 flat floor is far below the measured effects",
          flat_max < 0.1 * real_eff,
          f"(floor {flat_max:.5f} % vs effect {real_eff:.3f} %)")
    hi = np.mean([float(r["saving"]) for r in cs
                  if r["machine_profile"] == "T5_continuous"
                  and r["regime"] == "spot_highvol" and float(r["battery_ratio"]) == 1.0])
    lo = np.mean([float(r["saving"]) for r in cs
                  if r["machine_profile"] == "T1_ideal"
                  and r["regime"] == "spot_highvol" and float(r["battery_ratio"]) == 1.0])
    check("M1 recovers the planted machine ordering", hi > lo,
          f"(T5 {hi:.3f} % > T1 {lo:.3f} %)")
    check("M1 restart effect is recovered with the right sign",
          "storage return" in reports["M1"])

    # M2: the whole point -- synthetic slope positive, real slope ~0.
    m2txt = reports["M2"]
    check("M2 leads with the synthetic/real split",
          m2txt.index("HEADLINE") < m2txt.index("Pooled regression"))
    check("M2 flags the artefact", "ARTEFACT" in m2txt or "INCONCLUSIVE" in m2txt)

    # M3: the confound must be visible, and the trend must be ~flat.
    check("M3 prints the n/horizon correlation", "corr(n, horizon_days)" in reports["M3"])
    check("M3 reports a near-perfect confound",
          "Pearson 0.9" in reports["M3"] or "Pearson 1.0" in reports["M3"])

    # M4: SI planted at 0.40 in the non-flat regimes, 0 in the placebo.
    d4 = list(csv.DictReader((outdir / "m4_decomposition.csv").open()))
    non_flat = [float(r["SI"]) for r in d4 if r["regime"] != "flat"
                and float(r["battery_ratio"]) >= 0.25]
    non_flat = [v for v in non_flat if math.isfinite(v)]
    flat_i = [abs(float(r["I"])) for r in d4 if r["regime"] == "flat"]
    flat_si_ok = all(not math.isfinite(float(r["SI"])) for r in d4
                     if r["regime"] == "flat")
    check("M4 recovers the planted substitution index",
          non_flat and abs(np.mean(non_flat) - _TRUTH["si"]) < 0.08,
          f"(SI {np.mean(non_flat) if non_flat else float('nan'):.3f}, "
          f"planted {_TRUTH['si']})")
    check("M4 placebo interaction is ~0", (not flat_i) or max(flat_i) < 0.02,
          f"(max |I| under flat {max(flat_i) if flat_i else 0:.5f} %)")
    check("M4 refuses SI where its denominator is noise", flat_si_ok,
          "(flat cells report NOT IDENTIFIED)")

    # M5: energy down, tardiness unchanged -> "VERTICAL".
    s5 = list(csv.DictReader((outdir / "m5_shift.csv").open()))
    check("M5 finds an inward shift", s5 and all(float(r["dEnergy"]) < 0 for r in s5))
    check("M5 calls the shift vertical",
          s5 and sum(1 for r in s5 if r["verdict"] == "VERTICAL") >= len(s5) - 1,
          f"({sum(1 for r in s5 if r['verdict'] == 'VERTICAL')}/{len(s5)} levels)")

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":                       # pragma: no cover - dev tool
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true",
                    help="generate synthetic results with known effects and "
                         "check that M0-M5 recover them")
    ap.add_argument("--results", default="", help="path to a results.csv to analyse")
    ap.add_argument("--out", default="", help="output directory for the reports")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_selftest(Path(tempfile.mkdtemp(prefix="managerial_"))))
    if args.results:
        _out = Path(args.out or ".")
        print(run_all(A.load_results(Path(args.results)), _out))
        raise SystemExit(0)
    ap.print_help()
