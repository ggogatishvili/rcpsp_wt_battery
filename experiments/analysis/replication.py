"""
MR — seed replication: how noisy is the GA, and how many seeds does the
campaign need?

WHY THIS MODULE EXISTS

The GA is stochastic, so every cell of the design is measured with error. The
campaign handles that by averaging k seeds within each
instance x configuration cell before any comparison, which is the right thing
to do but is only half the job: it removes part of the noise and leaves the
rest, and the rest lands exactly where it hurts most.

Every managerial number in M1-M5 is a PAIRED difference -- storage against no
storage, one archetype against another, one tariff against another, all on the
same instance. Pairing is what makes those comparisons powerful: the enormous
instance-to-instance variability cancels. What does not cancel is the seed
noise, because the two cells being differenced were each measured with their
own independent draw:

    d_i    = mean_k(x_A,i) - mean_k(x_B,i)
    Var(d) = sigma_delta^2 + 2 sigma_seed^2 / k
             \\_________/    \\______________/
              the real         measurement
              variation        noise

With a large k the second term vanishes and the standard error of the mean
difference reflects genuine variation. With a small k it does not, and the
honest description of a reported effect changes from "storage saves X" to
"storage saves X, plus or minus an amount we did not measure".

So the two questions this module answers are:

  1. How large is sigma_seed, at the campaign's actual time budget and on the
     actual solver? It cannot be assumed, and it cannot be taken from v1: the
     time limit changed from 60 s to 300 s and the GA parameters were re-tuned,
     both of which move it.

  2. Is sigma_seed the SAME across treatments? This is the question a single
     dispersion figure cannot answer, and it is the more dangerous one. If the
     GA is noisier with a battery installed than without, then a difference of
     means between those two cells is partly a difference of dispersions, the
     paired test's assumptions do not hold, and part of the "storage effect" is
     the solver behaving differently rather than the plant behaving differently.
     It is the same failure mode that disqualified GAP in v1 -- there it was the
     mean gap that moved with the treatment; here it would be the variance.

Everything is expressed in percent of `norm_scale` (the naive energy bill of
the instance), the same denominator used everywhere else in the analysis, so
sigma_seed is directly comparable with the effects it has to be small against.

WHAT TO DO WITH THE OUTPUT. Section 4 prints a required-seed table, one row per
experiment, computed from the measured sigmas and that experiment's instance
count. Put those numbers in `design.SEEDS_PER_EXP`, regenerate the runlist,
and run the campaign. That is the whole point of running MR first.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from analysis import analyses as A
from config import design

# Percentile bootstrap settings, matched to the rest of the analysis so that a
# CI here is the same kind of object as a CI in managerial.py.
BOOT = 10_000
ALPHA = 0.05


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _rule(ch: str = "=") -> str:
    return ch * 78


def _sec(title: str) -> list[str]:
    return ["", _rule("-"), title, _rule("-")]


def _num(row: dict, key: str, default: float = float("nan")) -> float:
    try:
        v = row.get(key, "")
        return default if v == "" or v is None else float(v)
    except (TypeError, ValueError):
        return default


def _pooled_sd(groups: list[np.ndarray]) -> tuple[float, int]:
    """Pooled within-group standard deviation, and its degrees of freedom.

    Pooling rather than averaging the per-cell standard deviations: an SD
    estimated from k observations is itself noisy (with k = 3 it is good to
    within roughly a factor of two), so averaging point estimates would give a
    figure whose own precision is unknown. Pooling the sums of squares uses
    every observation once and produces one estimate with a stated df.
    """
    ss, df = 0.0, 0
    for g in groups:
        g = g[np.isfinite(g)]
        if len(g) > 1:
            ss += float(((g - g.mean()) ** 2).sum())
            df += len(g) - 1
    return (math.sqrt(ss / df) if df > 0 else float("nan")), df


def _boot_ci(x: np.ndarray, stat=np.mean) -> tuple[float, float]:
    x = np.asarray([v for v in x if np.isfinite(v)], dtype=float)
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(design.MASTER_SEED)
    idx = rng.integers(0, len(x), size=(BOOT, len(x)))
    vals = stat(x[idx], axis=1)
    return (float(np.percentile(vals, 100 * ALPHA / 2)),
            float(np.percentile(vals, 100 * (1 - ALPHA / 2))))


def _brown_forsythe(groups: list[np.ndarray]) -> tuple[float, float, float]:
    """Brown-Forsythe test for equal variances. Returns (F, df2, p).

    The median-centred variant of Levene's test, chosen because it does not
    assume normality -- objective values under negative prices are skewed, and
    the classical Levene test on means over-rejects badly there.
    """
    groups = [np.asarray(g, dtype=float) for g in groups]
    groups = [g[np.isfinite(g)] for g in groups]
    groups = [g for g in groups if len(g) > 1]
    k = len(groups)
    if k < 2:
        return (float("nan"),) * 3
    z = [np.abs(g - np.median(g)) for g in groups]
    n = sum(len(g) for g in z)
    zbar = np.concatenate(z).mean()
    num = sum(len(g) * (g.mean() - zbar) ** 2 for g in z) / (k - 1)
    den = sum(((g - g.mean()) ** 2).sum() for g in z) / (n - k)
    if den <= 0:
        return (float("nan"),) * 3
    F = num / den
    return F, float(n - k), _f_sf(F, k - 1, n - k)


def _f_sf(f: float, d1: int, d2: int) -> float:
    """P(F_{d1,d2} > f), exactly, without scipy.

    scipy is NOT a dependency of this package (see requirements.txt: the
    harness must run identically on the compute server without a
    package-resolution step that can silently differ), and the compute server
    does not have it. That makes the fallback path the REAL path, so it has to
    be exact rather than adequate.

    An earlier version of this function used a closed form valid only for
    d2 = 2 and returned p = 0.69 where the true value was 3e-81 -- which would
    have made the pre-registered homoscedasticity test (H0b) incapable of ever
    rejecting. The identity below is the standard one,

        P(F > f) = I_{d2/(d2 + d1 f)}(d2/2, d1/2),

    evaluated with the same Lentz continued fraction the rest of the analysis
    uses for t-tests.
    """
    if not (math.isfinite(f) and f > 0 and d1 > 0 and d2 > 0):
        return float("nan")
    x = d2 / (d2 + d1 * f)
    return _betainc_reg(d2 / 2.0, d1 / 2.0, x)


def _betainc_reg(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b), by the Lentz continued fraction.

    Same implementation as analysis/managerial.py::_betainc_reg -- duplicated
    rather than imported so that neither module depends on the other's internals
    for a pre-registered test statistic. Accurate to ~1e-12 over the range these
    tests use.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1 - x) - lbeta)
    # The continued fraction converges quickly only for x < (a+1)/(a+b+2);
    # otherwise use the symmetry I_x(a,b) = 1 - I_{1-x}(b,a).
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betainc_reg(b, a, 1.0 - x)
    tiny = 1e-30
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        # even step
        num = m * (b - m) * x / ((a + m2 - 1.0) * (a + m2))
        d = 1.0 + num * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + num / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        # odd step
        num = -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1.0))
        d = 1.0 + num * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + num / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return max(0.0, min(1.0, front * h / a))


def _scales(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        inst = r.get("instance", "")
        if inst not in out:
            out[inst] = A.norm_scale(r)
    return out


# ---------------------------------------------------------------------------
# the estimator
# ---------------------------------------------------------------------------

def sigma_seed(rows: list[dict], cell_keys=("instance", "battery_ratio",
                                            "machine_profile", "state_policy",
                                            "time_limit", "price_name"),
               value: str = "objective") -> tuple[float, int, list[float]]:
    """Pooled within-cell seed standard deviation, in percent of norm_scale.

    A "cell" is one instance under one fully-specified configuration; the only
    thing varying inside it is the seed. Anything else varying inside a cell
    would inflate this estimate and make every downstream seed count too large,
    which is why the key lists every factor the campaign moves.
    """
    sc = _scales(rows)
    buckets: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        s = sc.get(r.get("instance", ""), float("nan"))
        v = _num(r, value)
        if math.isfinite(s) and s > 0 and math.isfinite(v):
            buckets[tuple(str(r.get(k, "")) for k in cell_keys)].append(100.0 * v / s)
    groups = [np.asarray(v) for v in buckets.values() if len(v) > 1]
    sd, df = _pooled_sd(groups)
    per_cell = [float(np.std(g, ddof=1)) for g in groups]
    return sd, df, per_cell


def paired_effect_sd(rows: list[dict], factor: str, level_a, level_b,
                     k: int, sigma_s: float | None = None,
                     value: str = "objective") -> dict:
    """Decompose the spread of a paired contrast into signal and seed noise.

    Returns the observed SD of the paired difference, the seed-noise component
    2 sigma_s^2 / k that is baked into it, and what is left over -- the genuine
    instance-to-instance variability of the effect. That leftover is the
    quantity `required_seeds` needs, and it is the one nobody can observe
    directly: a paired difference always shows the two mixed together.

    SIGMA IS ESTIMATED FROM THE TWO CELLS BEING DIFFERENCED, not pooled over
    the whole experiment. That matters exactly when section 2 finds
    heteroscedasticity: if the GA is three times noisier at B = 1.0 than at
    B = 0, then the global pooled sigma is far too large for the
    (B = 0.1 vs B = 0) contrast and far too small for the (B = 1.0 vs B = 0)
    one. Using it would understate the precision of the first and overstate
    the precision of the second -- and the second is the one the paper leans
    on. `sigma_s` is accepted as an override only for callers that genuinely
    want the pooled figure.
    """
    sc = _scales(rows)
    other = [c for c in ("instance", "battery_ratio", "machine_profile",
                         "state_policy", "time_limit", "price_name")
             if c != factor]
    cells: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        s = sc.get(r.get("instance", ""), float("nan"))
        v = _num(r, value)
        if not (math.isfinite(s) and s > 0 and math.isfinite(v)):
            continue
        key = tuple(str(r.get(c, "")) for c in other)
        cells[key].setdefault(str(r.get(factor, "")), []).append(100.0 * v / s)

    # Seed-matched pairs, kept aligned so the common-random-numbers
    # correlation can be measured rather than assumed. `by_seed` is populated
    # only when both cells carry the same seed labels, which the runlist
    # guarantees by construction but which is worth verifying rather than
    # trusting.
    seed_of: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        s_ = _scales(rows).get(r.get("instance", ""), float("nan"))
        v = _num(r, value)
        if not (math.isfinite(s_) and s_ > 0 and math.isfinite(v)):
            continue
        key = tuple(str(r.get(c, "")) for c in other)
        seed_of[key].setdefault(str(r.get(factor, "")), {})[
            str(r.get("seed", ""))] = 100.0 * v / s_

    diffs, contrast_groups = [], []
    pa, pb = [], []
    for key, lv in cells.items():
        a, b = lv.get(str(level_a)), lv.get(str(level_b))
        if a and b:
            diffs.append(float(np.mean(a) - np.mean(b)))
            contrast_groups += [np.asarray(a), np.asarray(b)]
            sa_map = seed_of.get(key, {}).get(str(level_a), {})
            sb_map = seed_of.get(key, {}).get(str(level_b), {})
            common = sorted(set(sa_map) & set(sb_map))
            if len(common) >= 2:
                ma = float(np.mean([sa_map[s_] for s_ in common]))
                mb = float(np.mean([sb_map[s_] for s_ in common]))
                # Centre within the cell so the correlation measures the shared
                # seed effect, not the (huge) between-instance level.
                pa += [sa_map[s_] - ma for s_ in common]
                pb += [sb_map[s_] - mb for s_ in common]
    if len(diffs) < 3:
        return {"n": len(diffs), "sd_observed": float("nan"),
                "sd_noise": float("nan"), "sd_effect": float("nan"),
                "mean": float("nan"), "noise_share": float("nan"),
                "sigma_seed_local": float("nan"), "note": "too few paired cells"}

    sig_local, df_local = _pooled_sd(contrast_groups)
    # Common-random-numbers correlation between the two cells, seed by seed.
    # Positive rho shrinks Var(d) and therefore the required seed count; the
    # campaign runs the same seed labels in both cells on purpose, so this is
    # expected to be > 0 and is worth measuring rather than conservatively
    # assuming away.
    rho, rho_lo, rho_hi = float("nan"), float("nan"), float("nan")
    if len(pa) >= 4:
        A_, B_ = np.asarray(pa), np.asarray(pb)
        sda, sdb = A_.std(ddof=1), B_.std(ddof=1)
        if sda > 1e-12 and sdb > 1e-12:
            rho = float(np.corrcoef(A_, B_)[0, 1])
            # Bootstrap the pairs. Planning uses the LOWER bound, because
            # over-estimating rho under-estimates the seeds needed, and the
            # cost of that error is a campaign that cannot resolve its own
            # effects -- far worse than a few thousand wasted core-hours.
            rg = np.random.default_rng(design.MASTER_SEED)
            idx = rg.integers(0, len(A_), size=(2000, len(A_)))
            sa_ = A_[idx]; sb_ = B_[idx]
            ca = sa_ - sa_.mean(axis=1, keepdims=True)
            cb = sb_ - sb_.mean(axis=1, keepdims=True)
            num = (ca * cb).sum(axis=1)
            den = np.sqrt((ca ** 2).sum(axis=1) * (cb ** 2).sum(axis=1))
            with np.errstate(invalid="ignore", divide="ignore"):
                rs = np.where(den > 0, num / den, np.nan)
            rs = rs[np.isfinite(rs)]
            if len(rs) > 10:
                rho_lo = float(np.percentile(rs, 2.5))
                rho_hi = float(np.percentile(rs, 97.5))
    sig = sigma_s if sigma_s is not None else sig_local
    if not (math.isfinite(sig) and sig >= 0):
        sig = sig_local

    d = np.asarray(diffs)
    sd_obs = float(np.std(d, ddof=1))
    # (sigma_A^2 + sigma_B^2 - 2 rho sigma_A sigma_B) / k, with sigma_A =
    # sigma_B = sig here. rho unmeasurable -> fall back to the conservative
    # independent-seeds assumption.
    r_eff = 0.0
    if math.isfinite(rho):
        # Plan on the lower CI bound, capped. See design.RHO_PLANNING_CAP.
        r_eff = min(design.RHO_PLANNING_CAP,
                    max(0.0, rho_lo if math.isfinite(rho_lo) else rho))
    var_noise = 2.0 * sig ** 2 * (1.0 - r_eff) / max(1, k)
    var_effect = max(0.0, sd_obs ** 2 - var_noise)

    # The share is capped at 1 and the uncapped case is labelled rather than
    # printed as a percentage above 100, which is arithmetically impossible and
    # would read as a bug. It happens legitimately: both sd_obs and sig are
    # estimates, and when the true effect variability is near zero, sampling
    # error alone puts the noise estimate above the observed spread. The honest
    # reading is "this contrast has no detectable instance-to-instance
    # variability -- it is the same everywhere, to within the solver's noise",
    # which is a finding about the effect, not a defect in the arithmetic.
    note = ""
    if sd_obs > 0 and var_noise > sd_obs ** 2:
        note = ("seed noise >= observed spread: no detectable variation of this "
                "effect between instances")
    share = min(1.0, var_noise / sd_obs ** 2) if sd_obs > 0 else float("nan")
    return {"n": len(d), "mean": float(d.mean()), "sd_observed": sd_obs,
            "sd_noise": math.sqrt(var_noise), "sd_effect": math.sqrt(var_effect),
            "noise_share": share, "sigma_seed_local": sig_local,
            "df_local": df_local, "rho_crn": rho, "rho_lo": rho_lo,
            "rho_hi": rho_hi, "note": note}


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def mr(rows: list[dict], out: Path, **kw) -> str:
    R = [r for r in rows if r.get("experiment") == "MR"
         and r.get("status", "ok") == "ok" and r.get("method") == "GA"]
    L = [_rule(), "MR - seed replication: how noisy is the GA, and how many "
                  "seeds does the campaign need?", _rule()]
    if not R:
        L.append("  No MR runs in the results table. Until this experiment has "
                 "run, every seed count in design.SEEDS_PER_EXP is a guess and "
                 "the precision of every effect in M1-M5 is unknown.")
        return _emit(out, "mr_replication.txt", L)

    seeds = sorted({str(r.get("seed", "")) for r in R})
    insts = sorted({r["instance"] for r in R})
    L += [f"  runs {len(R)}   instances {len(insts)}   seeds per cell {len(seeds)}",
          f"  budget {sorted({int(_num(r,'time_limit',0)) for r in R})} s   "
          f"archetypes {sorted({r.get('machine_profile','') for r in R})}",
          "  All dispersions below are in PERCENT OF THE NAIVE ENERGY BILL",
          "  (norm_scale), the same units as every effect in M1-M5, so they can",
          "  be compared directly against the effects they have to be small",
          "  against."]

    # ---- 1. how big is sigma_seed -----------------------------------------
    L += _sec("1. sigma_seed: the GA's run-to-run spread")
    sd_all, df_all, per_cell = sigma_seed(R)
    L.append(f"  pooled over every cell:  sigma_seed = {sd_all:.4f} %   "
             f"(df = {df_all}, {len(per_cell)} cells)")
    if per_cell:
        pc = np.asarray(per_cell)
        L.append(f"  per-cell SD distribution: median {np.median(pc):.4f} %  "
                 f"p90 {np.percentile(pc, 90):.4f} %  max {pc.max():.4f} %")
        lo, hi = _boot_ci(pc, np.median)
        L.append(f"  95 % CI on the median per-cell SD: [{lo:.4f}, {hi:.4f}] %")

    # The flat tariff is the cleanest read: no arbitrage value can exist there,
    # so the whole spread is algorithmic.
    flat = [r for r in R if r.get("price_regime") == "flat"]
    if flat:
        sd_f, df_f, _ = sigma_seed(flat)
        L.append(f"  under the FLAT tariff (pure noise, no signal): "
                 f"sigma_seed = {sd_f:.4f} %  (df = {df_f})")
        L.append("    This is the cleanest estimate available: with a constant")
        L.append("    price no configuration can create value, so every bit of")
        L.append("    spread here is the algorithm rather than the problem.")

    L.append("")
    L.append(f"  For reference, the campaign's target minimum detectable effect")
    L.append(f"  is design.MDE_TARGET_PCT = {design.MDE_TARGET_PCT} %. A single "
             f"GA run carries")
    if math.isfinite(sd_all) and sd_all > 0:
        L.append(f"  {sd_all/design.MDE_TARGET_PCT:.2f} x that in noise, which is "
                 f"why seeds are averaged at all.")

    # ---- 2. is sigma_seed the same across treatments -----------------------
    L += _sec("2. DECISIVE: is the GA equally noisy under every treatment?")
    L += ["  If the GA is noisier with a battery than without, or noisier on a",
          "  machine that cannot switch off, then a difference of MEANS between",
          "  those cells is partly a difference of DISPERSIONS. The paired test",
          "  assumes it is not. This is the same failure that disqualified GAP",
          "  in v1, one moment earlier: there the mean gap moved with the",
          "  treatment; here it would be the variance.",
          ""]
    hom_rows = []
    for factor, label in (("battery_ratio", "battery level"),
                          ("machine_profile", "machine archetype"),
                          ("price_regime", "tariff regime")):
        levels = sorted({str(r.get(factor, "")) for r in R if str(r.get(factor, "")) != ""})
        if len(levels) < 2:
            continue
        L.append(f"  by {label}:")
        sds, resid_groups = {}, []
        for lv in levels:
            sub = [r for r in R if str(r.get(factor, "")) == lv]
            sd_lv, df_lv, cells_lv = sigma_seed(sub)
            sds[lv] = sd_lv
            # residuals about the cell median, for Brown-Forsythe
            sc = _scales(sub)
            buckets: dict[tuple, list[float]] = defaultdict(list)
            for r in sub:
                s = sc.get(r.get("instance", ""), float("nan"))
                v = _num(r, "objective")
                if math.isfinite(s) and s > 0 and math.isfinite(v):
                    buckets[(r["instance"], str(r.get("battery_ratio", "")),
                             str(r.get("machine_profile", "")),
                             str(r.get("price_name", "")))].append(100.0 * v / s)
            resid = []
            for g in buckets.values():
                if len(g) > 1:
                    m = float(np.median(g))
                    resid += [abs(x - m) for x in g]
            resid_groups.append(np.asarray(resid))
            L.append(f"    {lv:<20s} sigma_seed = {sd_lv:8.4f} %   "
                     f"(df {df_lv}, {len(cells_lv)} cells)")
        vals = [v for v in sds.values() if math.isfinite(v) and v > 0]
        if len(vals) >= 2:
            ratio = max(vals) / min(vals)
            F, df2, p = _brown_forsythe(resid_groups)
            verdict = ("HOMOGENEOUS" if (ratio < 1.5 and (not math.isfinite(p) or p > 0.05))
                       else "HETEROGENEOUS")
            L.append(f"    max/min ratio {ratio:5.2f}   Brown-Forsythe F = {F:.2f}, "
                     f"p = {p:.4g}   -> {verdict}")
            if verdict == "HETEROGENEOUS":
                L.append(f"    ACTION: report effects across {label} against the "
                         f"LARGER sigma_seed ({max(vals):.4f} %), not the pooled "
                         f"one, and say so in the paper.")
            hom_rows.append(dict(factor=factor, ratio=ratio, F=F, p=p,
                                 verdict=verdict,
                                 **{f"sd_{k}": v for k, v in sds.items()}))
        L.append("")

    # ---- 3. how much of a paired standard error is seed noise --------------
    L += _sec("3. What share of a paired comparison's error is seed noise?")
    L += ["  Var(d) = sigma_effect^2 + 2 sigma_seed^2 / k. The middle column is",
          "  the part that more seeds would remove; the last is the part only",
          "  more instances can.",
          ""]
    k_run = len(seeds)
    contrasts = []
    ratios = sorted({str(r.get("battery_ratio", "")) for r in R})
    if "0.0" in ratios or "0" in ratios:
        zero = "0.0" if "0.0" in ratios else "0"
        for lv in ratios:
            if lv == zero:
                continue
            contrasts.append(("battery_ratio", lv, zero,
                              f"storage b={lv} vs b=0"))
    archs = sorted({str(r.get("machine_profile", "")) for r in R if r.get("machine_profile")})
    if len(archs) >= 2:
        contrasts.append(("machine_profile", archs[-1], archs[0],
                          f"machine {archs[-1]} vs {archs[0]}"))

    L += ["  rho is the common-random-numbers correlation: the campaign runs the",
          "  SAME seed labels in both cells, so the two draws share the GA's",
          "  random stream. rho > 0 shrinks Var(d) and therefore the seeds",
          "  needed. Assuming rho = 0 is safe but can overstate k severalfold.",
          ""]
    L.append(f"  {'contrast':<28s} {'n':>4s} {'mean':>9s} {'sd(d)':>8s} "
             f"{'sig_seed':>9s} {'rho':>6s} {'sd noise':>9s} {'sd effect':>10s} "
             f"{'noise':>7s}")
    csv_rows = []
    for factor, a, b, label in contrasts:
        # sigma from the two cells being differenced, not the global pool --
        # see paired_effect_sd's docstring for why that matters here.
        st = paired_effect_sd(R, factor, a, b, k_run)
        if not math.isfinite(st["sd_observed"]):
            continue
        rho_txt = ("  n/a" if not math.isfinite(st.get("rho_crn", float("nan")))
                   else f"{st['rho_crn']:>6.3f}")
        if math.isfinite(st.get("rho_crn", float("nan"))) and \
                st["rho_crn"] > design.RHO_SUSPICIOUS:
            L.append(f"  WARNING: rho = {st['rho_crn']:.4f} on '{label}' is "
                     f"above {design.RHO_SUSPICIOUS}. Either the two cells are "
                     f"barely different,")
            L.append("           or -- far more likely -- the treatment is not "
                     "reaching the model.")
            L.append("           Check RUNBOOK_SERVER.md step 3 before trusting "
                     "any effect on this factor.")
        L.append(f"  {label:<28s} {st['n']:>4d} {st['mean']:>9.4f} "
                 f"{st['sd_observed']:>8.4f} {st['sigma_seed_local']:>9.4f} "
                 f"{rho_txt} {st['sd_noise']:>9.4f} {st['sd_effect']:>10.4f} "
                 f"{100*st['noise_share']:>6.1f} %")
        if st["note"]:
            L.append(f"      ^ {st['note']}")
        csv_rows.append(dict(contrast=label, k=k_run, **st))
    L += ["",
          f"  Read at the MR seed count (k = {k_run}). At the campaign's k the",
          "  noise column scales as 1/sqrt(k), so a contrast whose noise share",
          "  is already small here stays small, and one where it dominates will",
          "  still dominate at k = 3.",
          "",
          "  A noise share above ~50 % means most of what looks like",
          "  instance-to-instance variation in that effect is the solver, and",
          "  the effect's confidence interval is mostly measuring the GA."]

    # ---- 4. the required seed table ---------------------------------------
    L += _sec("4. REQUIRED SEEDS -- put these in design.SEEDS_PER_EXP")
    L += [f"  Solves  n (MDE/z)^2 >= sigma_effect^2 + W/k  with",
          f"  W = sigma_A^2 + sigma_B^2 - 2 rho sigma_A sigma_B,",
          f"  at MDE = {design.MDE_TARGET_PCT} % of the naive bill, two-sided "
          f"alpha = 5 %, power = 80 %.",
          "",
          "  'inf' is the answer that matters most: it means the genuine",
          "  instance-to-instance variability of the effect ALREADY exceeds the",
          "  budget on its own, so no amount of seed replication resolves it and",
          "  the fix is more instances, not more seeds.",
          ""]

    # instance counts per experiment, taken from the runlist where possible so
    # the table describes the campaign as configured rather than as imagined
    n_by_exp = {}
    for r in rows:
        e = r.get("experiment", "")
        if e and e != "MR":
            n_by_exp.setdefault(e, set()).add(r.get("instance", ""))
    if not n_by_exp:
        # MR normally runs alone and first, so fall back to the design's own
        # instance counts rather than printing an empty table.
        n_by_exp = {"M1": {f"i{i}" for i in range(288)},
                    "M2": {f"i{i}" for i in range(24 * 43)},
                    "M3": {f"i{i}" for i in range(675)},
                    "M4": {f"i{i}" for i in range(108)},
                    "M5": {f"i{i}" for i in range(252)}}
        L.append("  (MR ran alone, so instance counts come from the design, not "
                 "from the results table.)")

    # The effect SD to plan against: the largest across the contrasts measured,
    # which is the conservative choice.
    sd_eff = max([c["sd_effect"] for c in csv_rows
                  if math.isfinite(c["sd_effect"])] or [float("nan")])
    # Planning rho: the SMALLEST measured correlation across contrasts, so the
    # seed count is sized for the contrast that benefits least from common
    # random numbers rather than for the luckiest one.
    # Planning rho: the smallest LOWER CI BOUND across contrasts, capped. Two
    # layers of conservatism, both deliberate -- the seed count is sized for the
    # contrast that benefits least from common random numbers, at the pessimistic
    # end of that contrast's own uncertainty.
    rlos = [(c["rho_lo"] if math.isfinite(c.get("rho_lo", float("nan")))
             else c.get("rho_crn", float("nan"))) for c in csv_rows]
    rlos = [r for r in rlos if math.isfinite(r)]
    rho_plan = min(design.RHO_PLANNING_CAP, max(0.0, min(rlos))) if rlos else 0.0
    L.append(f"  planning sigma_seed  = {sd_all:.4f} %")
    pts = [c.get("rho_crn", float("nan")) for c in csv_rows]
    pts = [r for r in pts if math.isfinite(r)]
    L.append(f"  planning rho         = {rho_plan:.3f}  "
             + (f"(lower CI bound of the least-correlated of {len(rlos)} "
                f"contrasts, capped at {design.RHO_PLANNING_CAP}; point "
                f"estimates {min(pts):.3f}-{max(pts):.3f})" if pts
                else "(not measurable; assuming independent seeds)"))
    L.append(f"  planning sigma_effect = {sd_eff:.4f} %  "
             f"(largest measured, the conservative choice)")
    L.append("")
    L.append(f"  {'experiment':<10s} {'instances':>9s} {'k req':>10s} "
             f"{'k exact-t':>9s} {'k raw':>9s} {'k config':>12s} {'verdict':>14s}")
    req_rows = []
    for exp in sorted(n_by_exp):
        n_i = len(n_by_exp[exp])
        k_req = design.required_seeds(sd_all, sd_eff, n_i, rho=rho_plan)
        k_exact = design.required_seeds_t(sd_all, sd_eff, n_i, rho=rho_plan)
        k_cfg = len(design.seeds(exp))
        # The unfloored figure, so the reader can tell "we need 4 seeds" from
        # "we need 0.3 and are reporting 3 because one run per instance is not
        # a publishable protocol".
        room = n_i * (design.MDE_TARGET_PCT / design.POWER_Z) ** 2 - sd_eff ** 2
        k_raw = ((2.0 * sd_all ** 2 * (1.0 - rho_plan) / room)
                 if room > 0 else float("inf"))
        if not math.isfinite(k_req):
            verdict, shown, raw = "MORE INSTANCES", "inf", "inf"
        else:
            k_req = math.ceil(k_req)
            shown, raw = str(k_req), f"{k_raw:.2f}"
            verdict = "OK" if k_cfg >= k_req else "RAISE"
            if k_raw < design.MIN_SEEDS:
                verdict += " (floor)"
        kx = ("inf" if not math.isfinite(k_exact) else f"{math.ceil(k_exact):d}")
        L.append(f"  {exp:<10s} {n_i:>9d} {shown:>10s} {kx:>9s} {raw:>9s} "
                 f"{k_cfg:>12d} {verdict:>14s}")
        req_rows.append(dict(experiment=exp, instances=n_i, k_required=shown,
                             k_exact_t=kx, k_raw=raw, k_configured=k_cfg,
                             verdict=verdict, sigma_seed=sd_all,
                             sigma_effect=sd_eff, rho=rho_plan,
                             mde=design.MDE_TARGET_PCT, min_seeds=design.MIN_SEEDS))
    L += ["",
          "  RAISE means the configured seed count cannot resolve an effect of",
          f"  {design.MDE_TARGET_PCT} % on that experiment's instance count. Either "
          "raise the",
          "  seed count and re-price the campaign, or accept a larger MDE and",
          "  say in the paper what the experiment can and cannot resolve.",
          "",
          "  MORE INSTANCES means seeds are not the constraint at all.",
          "",
          f"  'k raw' is the unfloored power calculation; 'k required' applies",
          f"  design.MIN_SEEDS = {design.MIN_SEEDS}. Where the two differ, the "
          f"campaign is",
          "  comfortably powered and the extra seeds buy reporting credibility",
          "  and a within-campaign check on sigma_seed, not precision. That is",
          "  worth one sentence in the paper -- and it is a different sentence",
          "  from 'we needed five seeds'."]

    # ---- 5. the diminishing-returns curve ---------------------------------
    L += _sec("5. What each additional seed buys")
    L += ["  Standard error of a mean paired difference, as a function of k,",
          "  holding the instance count at the M1 cube's value. The curve",
          "  flattens once 2 sigma_seed^2 / k falls below sigma_effect^2 --",
          "  past that point seeds are being bought to shrink a term that is",
          "  already negligible.",
          ""]
    n_ref = len(n_by_exp.get("M1", [])) or 288
    if math.isfinite(sd_all) and math.isfinite(sd_eff):
        L.append(f"  {'k':>3s} {'SE(mean d)':>12s} {'vs k=1':>9s} "
                 f"{'noise share':>12s}")
        se1 = None
        for k in (1, 2, 3, 5, 8, 12, 20):
            var = sd_eff ** 2 + 2 * sd_all ** 2 / k
            se = math.sqrt(var / n_ref)
            se1 = se1 or se
            share = (2 * sd_all ** 2 / k) / var if var > 0 else float("nan")
            L.append(f"  {k:>3d} {se:>12.5f} {se/se1:>9.3f} {100*share:>11.1f} %")
        L.append("")
        L.append(f"  (n = {n_ref} instances, the M1 cube.)")

    _write_csv(out, "mr_sigma_by_cell.csv",
               [dict(cell_sd=v) for v in per_cell])
    _write_csv(out, "mr_homogeneity.csv", hom_rows)
    _write_csv(out, "mr_contrasts.csv", csv_rows)
    _write_csv(out, "mr_required_seeds.csv", req_rows)

    L += ["", _rule(),
          "READ THIS BEFORE FREEZING THE RUNLIST",
          "  1. Copy the 'k required' column into design.SEEDS_PER_EXP.",
          "  2. Re-run bin/02_make_runlist.py and check the budget still fits.",
          "  3. If section 2 says HETEROGENEOUS on any factor, the paper must",
          "     report effects across that factor against the larger sigma_seed,",
          "     and Threats to Validity must say so.",
          "  4. If any row says MORE INSTANCES, seeds will not fix it: either",
          "     widen that experiment's instance pool or state the effect size",
          "     it can resolve.",
          _rule()]
    return _emit(out, "mr_replication.txt", L)


# ---------------------------------------------------------------------------
# reusable: a seed-noise footnote for any experiment
# ---------------------------------------------------------------------------

def seed_noise_note(rows: list[dict], exp: str, sigma_s: float | None = None) -> list[str]:
    """Two lines any experiment's report can append: how much of its precision
    is seed noise at the seed count it actually ran.

    Kept here rather than in managerial.py so there is exactly one definition
    of sigma_seed in the codebase, and so an experiment cannot silently report
    a different one from MR.
    """
    R = [r for r in rows if r.get("experiment") == exp
         and r.get("status", "ok") == "ok" and r.get("method") == "GA"]
    if not R:
        return []
    k = len({str(r.get("seed", "")) for r in R})
    if sigma_s is None:
        mr_rows = [r for r in rows if r.get("experiment") == "MR"]
        if mr_rows:
            sigma_s, _, _ = sigma_seed([r for r in mr_rows if r.get("method") == "GA"])
        else:
            sigma_s, _, _ = sigma_seed(R)
    if not (math.isfinite(sigma_s) and sigma_s > 0):
        return []
    n_i = len({r["instance"] for r in R})
    noise_se = math.sqrt(2 * sigma_s ** 2 / max(1, k) / max(1, n_i))
    return ["",
            f"  SEED NOISE: k = {k} seeds, sigma_seed = {sigma_s:.4f} % of the "
            f"naive bill.",
            f"  Any paired mean difference in this report carries at least "
            f"{noise_se:.5f} %",
            f"  of standard error from seed noise alone ({n_i} instances). An "
            f"effect near that size is not resolved by this experiment."]


# ---------------------------------------------------------------------------
# io
# ---------------------------------------------------------------------------

def _write_csv(out: Path, name: str, rows: list[dict]) -> None:
    if not rows:
        return
    import csv
    out.mkdir(parents=True, exist_ok=True)
    cols: list[str] = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with (out / name).open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)


def _emit(out: Path, name: str, lines: list[str]) -> str:
    txt = "\n".join(lines) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(txt)
    return txt
