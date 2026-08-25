"""
Price-series library.

Four families, all reduced to a common representation: a named series of hourly
EUR/MWh values, a family, a regime, and the descriptors used as covariates
throughout the analysis.

    contractual   flat, two-block TOU. Shape is imposed, not observed.
    spot          windows drawn from ONE reference year, stratified into
                  volatility terciles. Internally ordered, externally weak.
    real          windows drawn from SEVERAL market-years (a calm year, the
                  2022 crisis, a recent high-renewable year, a second bidding
                  zone). This is what carries external validity.
    synthetic     a controlled sinusoid family with orthogonal (spread, noise,
                  negative-hour share). This is what carries identification.

WHY BOTH `spot` AND `real`. They are not redundant and the distinction is the
main methodological repair of campaign v2. Terciles of a single year vary the
*window*, holding the market's price formation fixed; different market-years
vary the price formation itself. v1 had only the former, and the consequence
was measured: the spread coefficient was +0.554 (se 0.026) on synthetic
tariffs and -0.061 (se 0.108) on real ones. A screening rule fitted to that is
a description of the generator's sinusoid, not of a market. Keeping the two
families separately labelled is what lets the analysis estimate the same
regression on each and show the reader the difference rather than average over
it.

WHY SYNTHETIC SURVIVES ANYWAY. Real tariffs confound spread with mean, with
CV, and with negative-hour share (v1 measured VIF around 9.5). The synthetic
family is the only place where those three move independently, so it is the
only place the *shape* of the response can be identified. Its role is stated
accordingly: identification, never an external threshold on its own.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .rng import substream


@dataclass
class Series:
    name: str
    regime: str
    values: list[float]
    family: str = "spot"
    # Free-form provenance, written into the instance manifest so that every
    # analysis can group by market and year without re-parsing series names.
    meta: dict = field(default_factory=dict)

    def descriptors(self) -> dict:
        return price_descriptors(self.values)


# ---------------------------------------------------------------------------
# descriptors
# ---------------------------------------------------------------------------

def price_descriptors(v: list[float]) -> dict:
    n = len(v)
    mean = sum(v) / n
    var = sum((x - mean) ** 2 for x in v) / n
    sd = math.sqrt(var)
    days = n // 24
    spreads = [max(v[d * 24:(d + 1) * 24]) - min(v[d * 24:(d + 1) * 24])
               for d in range(days)] if days else [max(v) - min(v)]
    return dict(
        price_mean=round(mean, 4),
        price_sd=round(sd, 4),
        price_cv=round(sd / mean, 6) if mean else float("nan"),
        spread_intraday=round(sum(spreads) / len(spreads), 4),
        spread_max=round(max(spreads), 4),
        neg_share=round(sum(1 for x in v if x < 0) / n, 6),
        price_min=round(min(v), 4),
        price_max=round(max(v), 4),
    )


# ---------------------------------------------------------------------------
# raw market data
# ---------------------------------------------------------------------------

def load_year_csv(path: Path) -> list[float]:
    """Load an hourly EUR/MWh year file with columns day,hour,cost.

    Handles the DST artefacts the original InstanceGenerator.py handled (23- and
    25-hour days) and fills gaps from the same hour on the adjacent day.

    Files produced by bin/00b_fetch_prices.py are already clean (24 h per day,
    no gaps), so for those this is a straight read. The repair path is kept
    because the reference year that ships with the repository has not been
    through that script, and because a silently mis-shaped year would corrupt
    every window drawn from it in a way that is very hard to see downstream.
    """
    by_day: dict[datetime, list[float | None]] = {}
    order: list[datetime] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            d = datetime.strptime(row["day"].strip(), "%d/%m/%Y")
            c = row["cost"].strip()
            if d not in by_day:
                by_day[d] = []
                order.append(d)
            by_day[d].append(float(c) if c else None)

    out: list[float | None] = []
    for d in order:
        day = by_day[d]
        if len(day) == 23:                 # spring forward: duplicate hour 2
            day.insert(2, day[1])
        elif len(day) == 25:               # fall back: drop the repeated hour
            del day[2]
        if len(day) != 24:
            raise ValueError(f"{path}: {d:%Y-%m-%d} has {len(day)} hours after DST fix")
        out.extend(day)

    for i, x in enumerate(out):            # gap fill from +/- 24h
        if x is None:
            prev = out[i - 24] if i >= 24 else None
            nxt = out[i + 24] if i + 24 < len(out) else None
            cands = [y for y in (prev, nxt) if y is not None]
            if not cands:
                raise ValueError(f"{path}: unfillable gap at hour {i}")
            out[i] = sum(cands) / len(cands)
    return [float(x) for x in out]


def _midnight_windows(year: list[float], horizon: int) -> list[int]:
    """Candidate window start offsets, aligned to midnight."""
    return [s for s in range(0, len(year) - horizon + 1, 24)]


def spot_windows(year: list[float], horizon: int, regime: str,
                 n_draws: int, seed_key: str) -> list[Series]:
    """Draw `n_draws` midnight-aligned windows from the volatility tercile
    corresponding to `regime`.

    Windows are ranked by mean intra-day spread; terciles define low/mid/high
    volatility. Draws are without replacement and deterministic in the master
    seed, so the same regime always yields the same windows for a given
    horizon.

    A note on what this factor means. Because all three terciles come from one
    year, "high volatility" here is a volatile WEEK of an ordinary year, not a
    volatile YEAR. That is a legitimate factor — a plant does face volatile and
    calm weeks — but it is not the same experiment as comparing 2019 with 2022,
    and the two must not be conflated in the write-up. See real_windows().
    """
    starts = _midnight_windows(year, horizon)
    if not starts:
        raise ValueError(f"horizon {horizon}h exceeds the {len(year)}h source series")
    scored = []
    for s in starts:
        w = year[s:s + horizon]
        scored.append((price_descriptors(w)["spread_intraday"], s))
    scored.sort()
    k = len(scored)
    lo, hi = k // 3, 2 * k // 3
    band = {"spot_lowvol": scored[:lo],
            "spot_midvol": scored[lo:hi],
            "spot_highvol": scored[hi:]}[regime]
    if not band:
        raise ValueError(f"empty volatility band for {regime} at horizon {horizon}")

    rng = substream(f"spot|{regime}|{horizon}|{seed_key}")
    pool = [s for _, s in band]
    rng.shuffle(pool)
    picks = pool[:min(n_draws, len(pool))]
    return [Series(name=f"{regime}_w{i:02d}", regime=regime, family="spot",
                   values=year[s:s + horizon],
                   meta={"market": "", "year": "", "label": regime.replace("spot_", ""),
                         "window_start_hour": s})
            for i, s in enumerate(picks)]


def real_windows(year: list[float], key: str, market: str, year_label: int,
                 label: str, horizon: int, n_draws: int,
                 seed_key: str) -> list[Series]:
    """Draw `n_draws` midnight-aligned windows spread across a market-year.

    UNSTRATIFIED, ON PURPOSE. spot_windows() picks windows by their volatility
    so that the tercile factor is clean; doing the same here would defeat the
    point. The claim this family supports is "a plant facing the 2022 market
    saw a different return than one facing 2019", and that requires windows
    representative of each year as it actually was, tails included. Stratifying
    would replace the between-year contrast with a within-year one and quietly
    remove exactly the variation being paid for.

    Windows are spread evenly over the year rather than drawn at random, so
    that a small `n_draws` still covers all four seasons: with n_draws = 5 the
    picks land near the start, and at 1/5, 2/5, 3/5 and 4/5 of the year, each
    jittered within its block. Seasonality is a first-order driver of both
    price level and spread, and five random draws can easily miss winter.
    """
    starts = _midnight_windows(year, horizon)
    if not starts:
        raise ValueError(f"horizon {horizon}h exceeds the {len(year)}h series for {key}")
    n = min(n_draws, len(starts))
    rng = substream(f"real|{key}|{horizon}|{seed_key}")
    blocks = [starts[i * len(starts) // n:(i + 1) * len(starts) // n]
              for i in range(n)]
    picks = [rng.choice(b) for b in blocks if b]
    return [Series(name=f"real_{key}_w{i:02d}", regime=f"real_{key}", family="real",
                   values=year[s:s + horizon],
                   meta={"market": market, "year": year_label, "label": label,
                         "window_start_hour": s})
            for i, s in enumerate(picks)]


# ---------------------------------------------------------------------------
# contractual tariffs
# ---------------------------------------------------------------------------

def flat_series(year: list[float], horizon: int) -> Series:
    """Constant price at the annual mean. Falsification control.

    Under a constant price no schedule and no battery can create arbitrage
    value, so every saving measured here is solver noise. That number is the
    resolution floor of the whole campaign and is read before any other result.
    """
    mean = sum(year) / len(year)
    return Series("flat", "flat", [round(mean, 4)] * horizon, family="contractual",
                  meta={"market": "", "year": "", "label": "flat"})


def two_block_series(year: list[float], horizon: int,
                     peak_hours: tuple[int, int], peak_ratio: float) -> Series:
    """Regulated-style peak/off-peak tariff, mean-matched to the spot year.

    peak_ratio is the peak/off-peak price ratio; the two levels are chosen so
    that the time-weighted mean equals the annual spot mean, which keeps the
    contractual and spot regimes comparable in level and different only in
    shape.
    """
    mean = sum(year) / len(year)
    a, b = peak_hours
    peak_len = b - a
    off_len = 24 - peak_len
    # x*off_len + peak_ratio*x*peak_len = 24*mean
    off = 24 * mean / (off_len + peak_ratio * peak_len)
    peak = peak_ratio * off
    vals = [round(peak if a <= (h % 24) < b else off, 4) for h in range(horizon)]
    return Series("tou2", "tou2", vals, family="contractual",
                  meta={"market": "", "year": "", "label": "two-block"})


# ---------------------------------------------------------------------------
# synthetic controlled family
# ---------------------------------------------------------------------------

def synthetic_series(horizon: int, mean: float, spread: float, noise: float,
                     neg_share: float, draw: int, seed_key: str) -> Series:
    """Daily sinusoid with controlled spread, noise and negative-price share.

    The sinusoid trough is placed at 04:00 and the peak at 18:00, matching the
    shape of European day-ahead prices. `neg_share` is realised by subtracting
    a constant offset from the cheapest `neg_share` fraction of hours, which
    creates genuinely negative prices without distorting the spread.

    NOMINAL VS REALISED SPREAD. Noise inflates the realised intra-day spread,
    and at low nominal spreads it dominates it: on a 168 h series, nominal
    spread 1 realises 18.3 at noise 0.05 but 3.6 at noise 0.01. Any statement
    about "the spread below which storage stops paying" must therefore be read
    off the REALISED descriptor in the manifest, never off the nominal level
    requested here.
    """
    rng = substream(f"synth|{horizon}|{mean}|{spread}|{noise}|{neg_share}|{draw}|{seed_key}")
    vals = []
    for h in range(horizon):
        phase = 2 * math.pi * ((h % 24) - 4) / 24.0
        base = mean + (spread / 2.0) * (-math.cos(phase))
        vals.append(base + rng.gauss(0.0, noise * mean))

    if neg_share > 0:
        k = int(round(neg_share * horizon))
        if k:
            idx = sorted(range(horizon), key=lambda i: vals[i])[:k]
            shift = max(vals[i] for i in idx) + 5.0
            for i in idx:
                vals[i] -= shift

    name = (f"synth_s{int(spread)}_n{int(noise*100):02d}"
            f"_g{int(neg_share*100):02d}_d{draw}")
    return Series(name, "synthetic", [round(v, 4) for v in vals], family="synthetic",
                  meta={"market": "", "year": "", "label": "synthetic",
                        "synth_spread": spread, "synth_noise": noise,
                        "synth_neg": neg_share})
