# Experimental Plan — Managerial Repositioning (IJPR track)

Companion to `paper/paper_tex/main.tex`, Section 6 (`sec:experiments`).

**Repositioning in one sentence.** The paper's contribution moves from *"we built a
good solver for a new RCPSP variant"* to *"we measure how much of an industrial
electricity bill is removed by machine-state management versus on-site storage under
TOU tariffs, whether the two levers are substitutes, and what it costs in delivery
performance."* The solver becomes the measurement instrument, not the result.

---

## 1. What changes in the paper

| Section | Change |
|---|---|
| Abstract | Lead with the managerial finding, not the method. Method gets one sentence. |
| Introduction | Reframe contribution around the two flexibility levers and the investment question. Keep the modelling gap claim but subordinate it. |
| Literature review | Add a subsection positioning against the *energy-flexibility-in-manufacturing* management literature (industrial demand response, flexibility valuation, storage investment appraisal), not only the OR scheduling literature. Currently the review is almost entirely OR. **This is the main remaining literature gap for an IJPR submission.** |
| §3–5 (model, H1/H1P, GA/GAP) | Compress. The GA operator details (crossover/mutation weight tables) belong in the appendix for this venue. |
| §6 Experiments | Rewritten — see `main.tex`. |
| Appendix A | New: full algorithmic comparison, moved out of the body. |
| Conclusion | Rewrite around managerial implications + the limitations the experiments expose. |

---

## 2. Required model and code extensions

Ordered by whether they block an experiment. Nothing here has been implemented yet.

### 2.1 Blocking

| # | Extension | Blocks | Notes |
|---|---|---|---|
| C0 | **Make the Phase-3 LP unconditional** | everything | The paper now presents H1 Phase 3 *as* the LP; the greedy peak-shaver is gone from the text. In code this means `Config::phase3LP` stops being a flag (default `false` today) and `SolverH1` always calls `BatteryLp`. `SolverH1P` then differs from `SolverH1` only by `phase1PriceAware`. Decide whether to delete the greedy path in `SolverH1.cpp` or retain it as an untested fallback — if retained, it must not be reachable in any reported run, or the "H1 Phase 3 is optimal" claim is false. |
| C1 | **State-set restriction** `--states {proc \| proc,idle \| all}` to realise Σ₁ / Σ₂ / Σ₃ | E1, E6, E7 | Cheapest way: forbid the excluded states in MILP, and in H1 Phase 2 drop the corresponding SPACES graph nodes. Σ₁ also means "machine hot for the whole production window", which needs a definition of the window (first EI start → last EI end). |
| C2 | **Machine energy profile as instance data** | E6 | `e_proc=4, e_idle=2, e_off=0` and all four transition cost/duration pairs are hardcoded in `include/instance.h` (`Proc`, `Idle`, `Off`, `procOff`, `offProc`, `procIdle`, `idleProc`). Must move into the instance file or a machine-profile file. |
| C3 | **Battery parameters configurable** | E6 | `chargingEfficiency`/`dischargingEfficiency = 0.95` hardcoded in `instance.h`. Capacity is already `--batteryCapacity`. |
| C4 | **C-rate (power) limit** | E6, credibility | Not in the model at all — the battery can currently fully charge/discharge in one interval. Needs two bound changes in the MILP (§3) and the same two in the Phase-3 LP (§4.3); both stay linear, so nothing about the solution approach changes. Already flagged as future work in the conclusion; for a management venue it is a *reviewer-magnet omission*, since it directly inflates the measured storage benefit. |
| C5 | **Tardiness cost scale `--lambda`** | E4 | Multiplier applied to all `late_price` on load. Trivial. |
| C6 | **Prices decoupled from the instance file** | E3 | Prices are currently the last line of the instance `.txt` and are baked in by `InstanceGenerator.py`. E3 runs ~90 shop instances × ~90 price series; with the current format that means materialising ~8k duplicated instance files. Add `--prices <file>` overriding the embedded series. **Highest-leverage refactor in this list.** |
| C7 | **Fixed-schedule re-costing mode** `--evaluate <solution.json> --prices <realised.txt>` | E7a | Apply a committed schedule to a different price series and recompute cost. Needed for the forecast-vs-realised experiment. |
| C8 | **Baseline policies P1 and P2** | E7b | P1 = Phase 2 only on an EDD schedule, no battery. P2 = P1 + naive rule (charge the *k* cheapest hours per day, discharge the *k* most expensive, schedule-independent). Both are ~50 lines each and are what makes the "value of sophistication" claim credible. |

### 2.2 Non-blocking but needed for the metrics

| # | Extension | Used by |
|---|---|---|
| C9 | Export per-interval `grid_to_machine`, `grid_to_battery`, `battery_to_machine` in the solution JSON | E2, E6 — equivalent full cycles, depth of discharge, degradation cost. Only `battery_levels` is currently exported (`src/jsonHandler.cpp:44`), from which throughput can only be inferred approximately once efficiencies ≠ 1. |
| C10 | Record solver seed, wall-clock, and generation count in the JSON | E0, seed-dispersion reporting |
| C11 | Benchmark harness axes | `benchmarks/config_EXAMPLE.json` currently has `methods / instancePattern / batteryCapacities`. Add `stateSets`, `priceFiles`, `lambdas`, `machineProfiles`, `seeds`, and write one row per run to a tidy CSV. All analysis below assumes one tidy results table. |

### 2.3 Instance generator changes (`instance_generator/InstanceGenerator.py`)

| # | Change | Why |
|---|---|---|
| G1 | **Seed the RNG and record it** | `random.randint` / `random.uniform` are currently unseeded — the benchmark is not reproducible, which will not survive review. |
| G2 | **EI density parameter δ** | Currently the EI set is whatever had `resource_0 > 0` in the PSPLIB source. Needs to be a controlled factor at 10 / 25 / 50 %. |
| G3 | **Due-date tightness parameter τ** | Slack is hardcoded at `uniform(0.1, 0.2)` of the remaining horizon. Needs three levels (tight / medium / loose). |
| G4 | **Fixed weekly horizon h = 168** | Storage value depends on the number of price troughs in the horizon; horizon must be controlled, not inherited from the source file. |
| G5 | **Emit instance descriptors** (δ, τ, resource strength, order strength, EI energy share) as a sidecar CSV | E5 regression covariates. |
| G6 | **Synthetic price generator** | Daily sinusoid with parameters (mean c̄, intra-day spread Δ₂₄, noise σ, negative-price share). E3 needs orthogonal variation that real price data confounds. |
| G7 | **Price-series library builder** | Extract week-long blocks from OTE data for three years, plus a flat tariff and a two-block contractual TOU tariff. |

---

## 3. Instances required

### 3.1 Shop instances

| Set | Design | Count |
|---|---|---|
| `B_core` | n ∈ {32, 64, 128, 256, 512} × δ ∈ {10 %, 25 %, 50 %} × τ ∈ {tight, med, loose} × 10 replications | **450** |
| `B_scr` | stratified subset: n ∈ {64, 128, 256}, all δ, all τ, 2 replications | **90** (⊂ `B_core`) |

All at h = 168 (one week, hourly). Source: PSPLIB j30/j60/j90/j120 plus the existing
32–640 series in `instance_generator/instances_original/`.

> The existing 32→640-in-steps-of-32 series is kept only for E0 (scalability); the
> managerial experiments do not need 20 size categories, they need controlled δ and τ.

### 3.2 Price series

| Family | Design | Count |
|---|---|---|
| Contractual | flat + two-block peak/off-peak TOU | 2 |
| Spot | 3 years (low-volatility / 2022 crisis / recent high-renewable) × 20 week draws | 60 |
| Synthetic | Δ₂₄ ∈ 5 levels × σ ∈ 3 levels × negative-price share ∈ {0, ~8 %} | 30 |
| **Total** | | **92** |

Each series stored with its four descriptors (c̄, Δ₂₄, CV, negative-hour share) for
use as regression covariates.

### 3.3 Machine profiles

Five named archetypes spanning the (idle power ratio ρ, restart penalty) plane, plus
the full 4 × 3 grid for E6:

| Archetype | ρ = e_idle/e_proc | Off→Proc cost / duration | Rationale |
|---|---|---|---|
| A1 fast electric | 0.25 | low / 1 h | CNC, induction |
| A2 current paper default | 0.5 | 5 / 2 h | matches `instance.h` today |
| A3 industrial oven | 0.5 | high / 3 h | thermal mass |
| A4 continuous process | 0.75 | very high / 6 h | shutdown effectively infeasible |
| A5 ideal | 0.0 | 0 / 0 h | upper bound / falsification check |

**Calibration is a to-do**: these ratios should be sourced from published machine
energy-profile data rather than invented, or the E6 conclusions are unanchored.

### 3.4 Storage configurations

- Capacity B_max / E_day ∈ {0, 0.1, 0.25, 0.5, 1, 2, 4}, where E_day = mean daily EI
  machine energy demand on the instance (makes capacity comparable across sizes).
- Round-trip efficiency η_c·η_d ∈ {0.75, 0.85, 0.95, 1.0}.
- C-rate ∈ {0.25, 0.5, 1, ∞} — **requires C4**.

### 3.5 Economic parameters (for §6.2.4)

Placeholders currently in the paper, consistent with publicly reported 2025 European
C&I turnkey pricing; **each needs a citable source before submission** (IEA, BNEF,
Ember, or a peer-reviewed storage-cost review):

- CAPEX κ = 250 EUR/kWh (range 180–320)
- O&M ω = 2 %/yr of CAPEX (1–3)
- Calendar life L = 12 y (10–15); cycle life 6 000 EFC (4 000–8 000)
- Throughput degradation cost 10 EUR/MWh (5–15)
- WACC r = 8 % (6–10); 48 operating weeks/yr

---

## 4. Run budget

Assuming GAP at a 60 s planning-time budget per run, 1 seed except where noted.

| Exp. | Configurations | Instances | Price series | Runs | Notes |
|---|---|---|---|---|---|
| E0 | 5 methods × 2 β | 450 | 1 | ~4 500 | MILP restricted to n ≤ 64 (≈360 MILP runs at 600 s TL) |
| E1 | 2 π × 3 σ × 2 β = 12 | 450 | 3 | 16 200 | + 4 extra seeds on `B_scr` → +12 960 |
| E2 | 7 capacities | 450 | 3 | 9 450 | |
| E3 | 2 β | 90 | 92 | 16 560 | needs C6 |
| E4 | 7 λ × 2 β | 450 | 1 | 6 300 | needs C5 |
| E5 | — | — | — | 0 | reuses E1 + E2 output |
| E6 | 12 machine × 2 β, + 16 battery | 90 | 3 | ~10 800 | needs C2, C3, C4 |
| E7a | 2 treatments × 2 β + sizing sweep | 90 | 2 yrs | ~1 600 | needs C7 |
| E7b | 5 policies | 90 | 3 | 1 350 | needs C8 |
| | | | **Total** | **≈ 80 000 runs** | ≈ 1 300 core-hours at 60 s/run; ~2 days on 32 cores |

Reduce first by cutting E1 to 1 tariff regime (−10 800) and E3 synthetic profiles to
15 (−2 700) if compute is tight.

---

## 5. Analysis deliverables

| Exp. | Statistical output | Figures / tables |
|---|---|---|
| E0 | gap and runtime by method × size; **gap stability across β** | Appendix table |
| E1 | mixed-effects ANOVA on eq. (6.2); interaction I_σβ; substitution index SI | 3×2 saving table; stacked cost-decomposition bars; paired schedule visualisation |
| E2 | savings curve, MVS(β), saturation β_sat, NPV surface, β* | savings + marginal-value curves; NPV heat map (CAPEX × Δ₂₄) with break-even contour; EFC/day curve |
| E3 | mixed-effects regression eq. (6.3); inverted break-even spread | scatter + fitted line with threshold; regression table; contractual-vs-spot bars |
| E4 | Pareto frontiers; energy–service exchange rate ∂Z_el/∂tardiness | two frontiers on one axis; exchange-rate table; frontier by τ |
| E5 | mixed-effects regression + variable importance; standardised effects | coefficient plot; δ×τ heat map; diagnostic checklist box |
| E6 | savings surface over (ρ, restart) × β; C-rate retention | two heat maps; substitution map with archetypes; tornado chart |
| E7 | forecast loss (mean and 5 % tail); % of GAP benefit per policy rung | realised-vs-perfect-foresight bars; policy waterfall |

Cross-cutting: report bootstrap CIs over instances, seed dispersion separately from
configuration effects, and a **falsification check** — under the flat tariff no
configuration should reduce energy cost; any measured difference is solver noise and
bounds the resolution of every other claim.

---

## 6. Suggested execution order

0. **C0** (Phase-3 LP unconditional) — the paper now assumes it; every run below must
   use it, so land it before generating any results. Measure the LP's share of
   evaluation time immediately: if it turns out to dominate GAP's inner loop, the
   "just use the LP" decision needs a documented time-budget caveat rather than a
   silent slowdown.
1. **C6 + G1** (price decoupling, seeding) — unblocks reproducibility and E3, and is
   the change everything else is easiest to build on.
2. **G2–G5** → generate `B_core` and `B_scr`, plus the descriptor sidecar.
3. **G6, G7** → price library.
4. **C11** → tidy-CSV harness. Run **E0**. Freeze GAP's time budget from its results.
5. **C1, C5** → run **E1**, **E2**, **E4**. These three carry the paper; if the E1
   interaction result is null or the E4 frontier does not separate, the framing needs
   revisiting *before* investing in E6/E7.
6. **C2, C3, C4** → **E6**. C4 in particular may move the E2 sizing result, so expect
   to re-run E2 after it lands.
7. **C7, C8** → **E7**. **E5** from stored output.

---

## 7. Open risks

- **C4 (C-rate) will shrink the headline storage benefit.** Better to discover the
  magnitude early than to have a reviewer point out that a battery which fully cycles
  in one hour is not a product anyone sells.
- **The battery may cycle unsustainably often.** If the cost-optimal policy runs many
  equivalent full cycles per day, the degradation cost is not a footnote — it belongs
  in the objective. Check this at E2 before committing to the framing.
- **E1's interaction may be near zero**, in which case the honest finding is "the two
  levers are roughly additive", which is still publishable but changes the narrative.
- **Machine archetype calibration** (§3.3) is currently invented; without a source,
  E6 is the weakest experiment in the set.
- **Instance provenance**: PSPLIB projects are not energy-intensive manufacturing
  projects. A short justification, or one calibrated case instance from a real plant,
  would materially strengthen an IJPR submission.

---

## 8. Managerial content in the storage-scheduling literature

Purpose of this chapter: establish (a) whether comparable battery/storage scheduling
papers report managerial insight at all, (b) by what methodology they produce it, so
that our E1–E7 results can be positioned against something concrete rather than
asserted to be novel.

### 8.1 Verification status — read this before quoting anything below

Claims here are graded. **No full text was read for any paper in this chapter**;
publisher sites (Taylor & Francis, ScienceDirect, Wiley) are paywalled and could not
be retrieved. What was done:

| Grade | Meaning | Applies to |
|---|---|---|
| **A** | Existence and full bibliographic record confirmed against the Crossref API, *and* the publisher-deposited abstract was read in full | Hilbert et al. 2023 |
| **B** | Existence and full bibliographic record confirmed against the Crossref API; no abstract deposited, content from secondary sources | Karimi & Kwon 2021; Chen et al. 2025 (ESWA); Chen et al. 2025 (IJPR) |
| **C** | Bibliographic record confirmed by appearing in the Crossref-deposited reference list of a verified paper (Hilbert et al. 2023), with matching DOI, volume and page range | Mikhaylidi et al. 2015; Moon & Park 2014; Wichmann et al. 2019; Weitzel & Glock 2017, 2019; Zhang et al. 2018; Khalaf & Wang 2018; Dong & Ye 2022 |
| **D** | Existence confirmed only from publisher landing page / indexer metadata via search | Kim et al. 2022; Chen et al. 2024; the 2026 IJPR storage paper |
| **—** | **Not checked at all** | `kamjoo2016_hybrid_renewable`; `zhang2019_bilevel_fuzzy` |

Abstracts actually read in full: **Hilbert et al. 2023, Zhang et al. 2018,
Weitzel & Glock 2017**. Everything else is a search-engine summary and must be
re-checked against the real abstract before being written into the paper.

**Action required**: obtain full texts through the CTU library for the four papers
marked ★ in §8.2 before drafting the literature section. Two of them can invalidate
parts of our novelty claim (§8.5).

### 8.2 Papers with managerial content, and how they produce it

| Paper | Venue | Storage modelled | Managerial content? | Methodology used to produce it |
|---|---|---|---|---|
| ★ Karimi & Kwon (2021), *Int. J. Energy Research* 45(13), 18981–18998, DOI `10.1002/er.6999` — grade B | IJER | Battery + on-site PV | **Yes — comparative by design.** The stated aim is to analyse the effect of energy-aware scheduling, on-site solar and battery storage on energy cost and makespan "in various configurations" | Simulated 3-machine job shop; MILP with multi-objective function balancing makespan against energy cost; **configuration-by-configuration comparison** — structurally the same idea as our E1 |
| ★ Zhang, Islam, Sun et al. (2018), *IJPE* 206, 261–267, DOI `10.1016/j.ijpe.2018.10.011` — grade C, abstract read | IJPE | On-site generation (not battery) | **Yes — sizing and investment is the contribution.** Cost-effective sizing under a Critical Peak Pricing demand-response programme | MINLP for joint sizing + utilisation + production plan; linearisation and a metaheuristic for tractability; **real auto-component manufacturing case study** with an existing CPP tariff. Closest published template for our E2 |
| Hilbert, Dellnitz & Kleine (2023), *Annals of OR* 328(2), 1409–1436, DOI `10.1007/s10479-023-05338-x` — grade A | AOR | Redox-flow battery | **Yes — trade-off analysis is the framing.** Explicitly analyses the trade-off between electricity *cost* and electricity *consumption*, which most bicriteria papers skip, and evaluates green PPAs | Bicriteria MILP; energy-efficient allocation heuristic for the PPA case; fix-relax-and-optimise plus decomposition for RTP/TOU; **scenario analysis** as the vehicle for the managerial conclusions |
| Weitzel & Glock (2017), *EJOR* 264(2), 582–606, DOI `10.1016/j.ejor.2017.06.052` — grade C, abstract read | EJOR | Review of stationary EESS | **Yes — economic-viability framing.** States that EESS "entail high investment costs" and that optimal energy management is "an important precondition to ensure economic viability" | Systematic literature review + conceptual framework. **Cite this to justify that storage-investment framing is established in OR, not imported from energy economics** |
| Weitzel & Glock (2019), *IJPR* 57(1), 250–270, DOI `10.1080/00207543.2018.1475764` — grade C | **IJPR** | Storage-augmented production facility | Likely — incentive-based demand response | Not established from available sources. **Must be checked**: it is storage + production scheduling in the target journal |
| Wichmann, Johannes & Spengler (2019), *IJPE* 216, 204–214, DOI `10.1016/j.ijpe.2019.04.015` — grade C | IJPE | Electrical storage (Li-ion motivated) | **Partly** — reports cost-saving potential vs. classical planning; our §2 of the paper already cites ">20 % with large storages" | Energy-oriented GLSP (EOGLSP) extension; three-level time structure (macro-periods for demand, energy micro-periods, flexible production micro-periods); numerical study |
| Kim et al. (2022), *IJPR* 60(23), 7033–7052, DOI `10.1080/00207543.2021.2000655` — grade D | **IJPR** | DER + energy storage system | Unknown | Single machine, sequence-dependent setups; MILP + variable neighbourhood search |
| Moon & Park (2014), *IJPR* 52(13), 3922–3939, DOI `10.1080/00207543.2013.860251` — grade C | **IJPR** | DER + storage | Unknown | MIP and CP under time- and machine-dependent electricity cost |
| Mikhaylidi et al. (2015), *IJPR* 53(23), 7136–7157, DOI `10.1080/00207543.2015.1058981` — grade C | **IJPR** | Battery | Unknown | MILP with electricity and postponement costs, start-up costs |
| ★ Chen, Zhang, Chen & Demeulemeester (2025), *IJPR* 63(23), 9155–9180, DOI `10.1080/00207543.2025.2535516` — grade B | **IJPR** | Hybrid energy, dynamic prices | Unknown | **Not currently in our bibliography.** RCPSP + hybrid energy + dynamic energy prices, in the target journal — see §8.5 |
| ★ 2026 IJPR paper, DOI `10.1080/00207543.2026.2651395` — grade D | **IJPR** | *Inventory* storage energy, not battery | Unknown | MIP + two metaheuristics (MBAGA, MBALNS) under TOU with late-penalty costs |
| Chen et al. (2025), *ESWA* 270, 126412, DOI `10.1016/j.eswa.2025.126412` — grade B | ESWA | **Battery claim unverified** — title says renewable energy only | Unknown | ILP + two-stage heuristic (per our current lit table) |

Also surfaced, not yet in our bibliography, both grade C:
**Khalaf & Wang (2018)**, *IJER* 42(23), 3928–3942, DOI `10.1002/er.4130` — flow shop
with intermittent renewables, storage and real-time pricing; and **Dong & Ye (2022)**,
*Computers & Industrial Engineering* 169, 108146, DOI `10.1016/j.cie.2022.108146` —
distributed hybrid flow shop with DER and storage.

### 8.3 The pattern worth exploiting

Across the papers above, managerial content is produced by one of three methods:

1. **Configuration comparison** — solve the same instances with the lever switched on
   and off and tabulate the difference (Karimi & Kwon; Wichmann et al.).
2. **Scenario analysis** — sweep an exogenous parameter, usually the tariff, and report
   how the optimal plan and its cost respond (Hilbert et al.).
3. **Sizing / investment appraisal on a real case** — make the asset size a decision
   variable and evaluate it economically (Zhang et al.).

Two observations follow. First, **our E1 is method 1, E3 is method 2, E2 is method 3** —
none of the three is methodologically novel, so the contribution has to come from
*what* is being compared, not from how. Second, and more usefully: in most of these
papers the managerial numbers are a by-product reported in a results subsection, not
the object of a designed study. **No paper found here decomposes the joint effect of
two flexibility levers into main effects and an interaction.** That decomposition
(E1, eq. 6.2 and the substitution index) is the defensible novelty, and it should be
stated as such rather than resting on the five-element combination gap.

### 8.4 Benchmarks we can quote against

Numbers our results can be compared with, once verified from full text:

- Wichmann et al. (2019): >20 % total cost saving with large storage installations.
- Zhang et al. (2018): "significant" reduction in total electricity-related cost with
  a correctly sized on-site generation system under CPP — extract the actual figure
  and the payback treatment, since this is our E2 analogue.
- Karimi & Kwon (2021): per-configuration cost and makespan effects — extract the
  full configuration table; this is the direct comparator for E1.

### 8.5 Bibliography gaps found

Missing from `refs.bib` and, given the repositioning, hard to justify omitting:

1. **Chen et al. (2025), IJPR — RCPSP with hybrid energy and dynamic energy prices.**
   Same problem class, same target journal, published July 2025. Omitting this from an
   IJPR submission would be read as not knowing the venue's recent literature.
2. **Weitzel & Glock (2019), IJPR** — storage-augmented production under demand
   response.
3. **Weitzel & Glock (2017), EJOR** — the storage energy-management review; anchors
   the investment framing.
4. **Zhang et al. (2018), IJPE** — the sizing/investment analogue for E2.
5. **The 2026 IJPR TOU-with-storage paper** — demonstrates current venue appetite.
6. Optional: Khalaf & Wang (2018); Dong & Ye (2022); Bänsch et al. (2021), *CIE* 159,
   107456, "Energy-aware decision support models in production environments: a
   systematic literature review" (grade C) — the last is a natural anchor for the
   production-management framing the review currently lacks.

### 8.6 Novelty risks identified by this review

- **Karimi & Kwon (2021) is uncomfortably close to E1's framing.** They already
  compare energy-aware scheduling, renewable generation and battery storage in
  multiple configurations. Our differentiators are machine states as a third lever,
  per-task tardiness, the RCPSP structure, and the interaction decomposition — but
  this must be argued explicitly, and it cannot be argued until someone reads their
  configuration table. **Highest-priority read.**
- **Chen et al. (2025, IJPR) may already occupy the RCPSP-plus-energy-prices slot**
  in the target journal. Read before finalising the positioning section.
- **The `chen2025_engineer_to_order` entry in our lit table claims "Renewables;
  batteries"**, but the title and available metadata mention renewable energy only.
  Verify the battery claim or correct the table — an incorrect entry in a related-work
  table is exactly what a reviewer who knows the paper will catch.
- **`kamjoo2016_hybrid_renewable` and `zhang2019_bilevel_fuzzy` have not been checked
  at all** and should be verified before submission.
