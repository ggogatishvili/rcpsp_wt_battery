"""
Price-series library.

Three families, all reduced to a common representation: a named series of
hourly EUR/MWh values plus four descriptors used as covariates throughout the
analysis (mean, mean intra-day spread, coefficient of variation, negative-hour
share).

DATA AVAILABILITY. Only Czech 2025 day-ahead data ships with the repository.
The multi-year design in EXPERIMENTAL_PLAN.md 3.2 is therefore approximated by
stratifying 2025 windows into volatility terciles. This is stated in
STATUS.md and must be stated in the paper: it is a real limitation, not a
detail. Additional years can be added through design.EXTRA_PRICE_CSVS without
touching this module.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .rng import substream


@dataclass
class Series:
    name: str
    regime: str
    values: list[float]

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
    return [Series(name=f"{regime}_w{i:02d}", regime=regime,
                   values=year[s:s + horizon])
            for i, s in enumerate(picks)]


# ---------------------------------------------------------------------------
# contractual tariffs
# ---------------------------------------------------------------------------

def flat_series(year: list[float], horizon: int) -> Series:
    """Constant price at the annual mean. Falsification control."""
    mean = sum(year) / len(year)
    return Series("flat", "flat", [round(mean, 4)] * horizon)


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
    return Series("tou2", "tou2", vals)


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
    return Series(name, "synthetic", [round(v, 4) for v in vals])
