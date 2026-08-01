"""
Economic parameters for the E2 investment appraisal.

WARNING — THESE ARE PLACEHOLDERS. The central values are consistent with
publicly reported 2025 European commercial-and-industrial turnkey lithium-iron-
phosphate prices, but they are NOT yet backed by a citable source. Before any
NPV or payback number from E2 goes into the paper, replace them with figures
from a citable reference (an IEA, BNEF or Ember storage-cost report, or a
peer-reviewed storage-cost review) and record the citation here.

E2 reports NPV and payback for CENTRAL and for the LOW/HIGH sensitivity
corners, so a reviewer can see how much of the investment conclusion is driven
by the cost assumption rather than by the scheduling result.
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
