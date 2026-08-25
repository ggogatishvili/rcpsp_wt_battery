#!/usr/bin/env python3
"""
Verify the seed-count derivation by simulation rather than by re-reading it.

docs/SEED_POWER.md derives

    Var(d_i) = sigma_effect^2 + (sigma_A^2 + sigma_B^2 - 2 rho sigma_A sigma_B)/k
    k        >= W / ( n (delta/z)^2 - sigma_effect^2 )

This script generates experiments from that model, runs the SAME paired t-test
the campaign will run, and measures the empirical rejection rate at the k the
formula prescribes. If the derivation is right the exact-t column lands on the
nominal power (0.80) to within Monte-Carlo error.

Why this exists as a committed script rather than a one-off check: the formula
sizes a 7,000 core-hour campaign, and a derivation that is only ever verified
by its author reading it again is not verified. Re-run it after any change to
design.required_seeds, paired_contrast_variance, MIN_SEEDS or MAX_SEEDS.

    python3 bin/08_verify_power.py            # the table in SEED_POWER.md
    python3 bin/08_verify_power.py --reps 100000 --tolerance 0.01

Exit code 4 if any row misses its nominal power by more than --tolerance,
ignoring rows where MIN_SEEDS binds (there the floor deliberately over-delivers).

scipy is used HERE for the critical value and is not a dependency of the
pipeline; without it the script falls back to a normal critical value and says
so, which weakens the check slightly but does not invalidate it.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import design                                    # noqa: E402

# (sigma_seed, sigma_effect, n_instances, rho). Chosen to span the campaign:
# M1's cube (n = 288), M4 (n = 108), M5 (n = 252), M3 (n = 900), plus small-n
# and high-noise cases where the approximations are worst.
CASES = [
    (1.0, 0.4, 36, 0.0),
    (2.0, 0.4, 36, 0.0),
    (2.0, 0.4, 36, 0.5),      # same as the row above, but with common random numbers
    (3.0, 1.0, 36, 0.0),      # the MAX_SEEDS case: finite k, absurd k
    (1.0, 0.2, 288, 0.0),
    (5.0, 1.0, 108, 0.0),
    (4.0, 2.0, 252, 0.0),
    (2.0, 0.4, 900, 0.0),
]


def critical_value(n: int, alpha: float) -> tuple[float, str]:
    try:
        from scipy import stats
        return float(stats.t.ppf(1 - alpha / 2, n - 1)), "t"
    except ImportError:
        # 1.96 at 5 %. Slightly liberal for small n, which makes the measured
        # power slightly optimistic -- stated rather than hidden.
        return 1.959963984540054, "normal (scipy absent)"


def empirical_power(sig_s: float, sig_eff: float, n: int, k: float,
                    delta: float, rho: float, reps: int, alpha: float,
                    seed: int) -> float:
    """Rejection rate of the paired t-test, under the model of SEED_POWER.md.

    Simulating d_i directly rather than simulating x_{A,i,s} and x_{B,i,s} and
    differencing them: the two are equivalent by construction (that IS the
    derivation), and going through the components would test the simulator
    rather than the formula.
    """
    rng = np.random.default_rng(seed)
    var = design.paired_contrast_variance(sig_s, sig_s, rho, sig_eff, k)
    D = rng.normal(delta, math.sqrt(var), size=(reps, n))
    se = D.std(axis=1, ddof=1) / math.sqrt(n)
    t = np.divide(D.mean(axis=1), se, out=np.zeros(reps), where=se > 0)
    crit, _ = critical_value(n, alpha)
    return float(np.mean(np.abs(t) > crit))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=20_000)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--power", type=float, default=0.80)
    ap.add_argument("--tolerance", type=float, default=0.02,
                    help="allowed shortfall below nominal power")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    _, kind = critical_value(36, args.alpha)
    delta = design.MDE_TARGET_PCT
    print(f"Verifying design.required_seeds against simulation.")
    print(f"  target effect  delta = {delta} % of the naive bill")
    print(f"  nominal power        = {args.power:.2f} at alpha = {args.alpha}")
    print(f"  replicates           = {args.reps:,}")
    print(f"  critical value       = {kind}")
    print(f"  MIN_SEEDS = {design.MIN_SEEDS}, MAX_SEEDS = {design.MAX_SEEDS}")
    print()
    hdr = (f"{'sig_seed':>9} {'sig_eff':>8} {'n':>5} {'rho':>5} "
           f"{'k normal':>9} {'k exact-t':>10} {'pow(k_z)':>9} {'pow(k_t)':>9} "
           f"{'note':<28}")
    print(hdr)
    print("-" * len(hdr))

    failures = []
    for sig_s, sig_eff, n, rho in CASES:
        kz = design.required_seeds(sig_s, sig_eff, n, rho=rho)
        kt = design.required_seeds_t(sig_s, sig_eff, n, rho=rho)
        if not math.isfinite(kz):
            mde = design.achievable_mde(sig_s, sig_eff, n, design.MAX_SEEDS, rho=rho)
            print(f"{sig_s:>9.1f} {sig_eff:>8.1f} {n:>5d} {rho:>5.1f} "
                  f"{'inf':>9} {'inf':>10} {'-':>9} {'-':>9} "
                  f"{'more instances; MDE@k=10 ' + format(mde, '.2f'):<28}")
            continue
        pz = empirical_power(sig_s, sig_eff, n, kz, delta, rho, args.reps,
                             args.alpha, args.seed)
        pt = empirical_power(sig_s, sig_eff, n, kt, delta, rho, args.reps,
                             args.alpha, args.seed + 1)
        note = ""
        floored = kz <= design.MIN_SEEDS + 1e-9
        if floored:
            note = "MIN_SEEDS binds (over-delivers)"
        elif kt > design.MAX_SEEDS:
            mde = design.achievable_mde(sig_s, sig_eff, n, design.MAX_SEEDS, rho=rho)
            note = f"exceeds MAX_SEEDS; MDE@k=10 {mde:.2f}"
        elif pt < args.power - args.tolerance:
            note = "UNDERPOWERED"
            failures.append((sig_s, sig_eff, n, rho, pt))
        print(f"{sig_s:>9.1f} {sig_eff:>8.1f} {n:>5d} {rho:>5.1f} "
              f"{kz:>9.2f} {kt:>10.2f} {pz:>9.3f} {pt:>9.3f} {note:<28}")

    print()
    print("Reading this table:")
    print("  * the exact-t column should land on the nominal power wherever no")
    print("    ceiling binds. That is the derivation being correct.")
    print("  * the normal column sits a little below it at small n -- the z-vs-t")
    print("    approximation, costing about 2 points of power at n = 36.")
    print("  * rows where MIN_SEEDS binds show power ABOVE target: the floor")
    print("    gives more seeds than the calculation asks for, which is the safe")
    print("    direction and is why they are not failures.")
    print("  * a row above MAX_SEEDS reports the effect size reachable at k = 10")
    print("    instead of a seed count nobody would run.")

    if failures:
        print(f"\nFATAL: {len(failures)} row(s) missed nominal power by more than "
              f"{args.tolerance}:", file=sys.stderr)
        for f in failures:
            print(f"  sigma_seed={f[0]} sigma_effect={f[1]} n={f[2]} rho={f[3]} "
                  f"-> power {f[4]:.3f}", file=sys.stderr)
        print("The derivation in docs/SEED_POWER.md and the implementation in "
              "config/design.py disagree. Fix before sizing anything.",
              file=sys.stderr)
        return 4
    print("\nOK: every row meets its nominal power. The derivation and the "
          "implementation agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
