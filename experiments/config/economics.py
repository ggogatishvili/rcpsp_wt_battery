"""
Economic parameters for the E2 investment appraisal.

Source: BloombergNEF, *Energy Storage Systems Cost Survey 2025* (cited in the
paper as \\cite{bnef2025ess}). The central turnkey figure is the European
commercial-and-industrial lithium-iron-phosphate installed cost; the LOW and
HIGH corners bracket it together with the corresponding lifetime, O&M and
discount-rate assumptions, which move together in practice rather than
independently.

E2 reports NPV and payback for CENTRAL and for both corners, and that band is
not decoration: the share of instances worth investing in at the smallest
capacity under high volatility moves from 3 % to 55 % to 95 % across HIGH,
CENTRAL and LOW, on identical physical savings. The cost assumption decides
the sign of the investment answer, so no point payback figure from this file
should be quoted without the band.

Run all three with:
    python3 bin/05_analyse.py --only E2 --economics central,low,high
"""

CENTRAL = dict(
    capex_eur_per_kwh=250.0,   # turnkey, installed
    om_share=0.02,             # annual O&M as a share of CAPEX
    life_years=12,
    cycle_life_efc=6000,       # equivalent full cycles to end of first life
    degradation_eur_per_mwh=10.0,   # throughput cost, applied ex post
    wacc=0.08,
    operating_weeks=48,
)

LOW_COST = dict(CENTRAL, capex_eur_per_kwh=180.0, om_share=0.01,
                life_years=15, wacc=0.06)

HIGH_COST = dict(CENTRAL, capex_eur_per_kwh=320.0, om_share=0.03,
                 life_years=10, wacc=0.10)

SENSITIVITY = {"central": CENTRAL, "low": LOW_COST, "high": HIGH_COST}
