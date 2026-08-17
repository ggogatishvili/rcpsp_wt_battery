# Pre-registration

Written **before** the full run. The point is to fix what counts as a finding
in advance, so that the analysis cannot drift towards whatever the data happens
to show. If you change anything here after seeing results, record the change
and the date in §7 and label the affected analysis exploratory.

Design frozen at: `config/design.py`, `MASTER_SEED = 20260801`, `PROFILE = "full"`.

---

## 1. Hypotheses

**H1 (substitution).** Machine-state management and battery storage are
partial substitutes: the joint cost reduction is strictly smaller than the sum
of the individual reductions.
Test: interaction term `I` in the E1 decomposition; substitution index
`SI = −I / min(V_σ, V_β)`.
Predicted: `I < 0`, `SI > 0.05`.
*Currently only partially testable* — the state dimension is blocked (item C1).
The policy × battery interaction is testable now and is a weaker proxy.

**H2 (sizing).** The NPV-maximising battery capacity is strictly below the
capacity at which the marginal value of storage reaches zero.
Test: E2. Predicted `β* < β_sat`, with `β*` at most half of `β_sat`.

**H3 (spread threshold).** The relative saving from storage increases in the
mean intra-day price spread, and there is a spread below which the investment
is NPV-negative.
Test: E3 regression, coefficient on `spread_intraday` positive and significant
with cluster-robust standard errors.

**H4 (frontier).** Storage shifts the service–energy frontier inward and
flattens it: at matched λ, adding a battery reduces energy cost without a
proportionate increase in tardiness.
Test: E4. Predicted `dEnergy < 0` and `dTardiness ≈ 0` at every λ.
This is the paper's most distinctive claim.

**H5 (structure).** The return on both levers is driven primarily by the share
of load concentrated in the EI machine and by due-date looseness, and only
weakly by project size and precedence density.
Test: E5 standardised coefficients.

---

## 2. Primary outcomes

| | |
|---|---|
| primary | total cost `Z = energy + tardiness`, as % of the matched baseline |
| secondary | energy component; tardiness component; equivalent full cycles/day |
| E2 only | NPV and discounted payback under `config/economics.py` |

Unit of analysis: **the instance**. Seeds are averaged within
instance × configuration before any comparison, so seed replication does not
inflate the sample size.

---

## 3. Statistical analysis, fixed in advance

* All comparisons **paired on the instance**; configurations are evaluated on
  identical instances by construction.
* 95 % confidence intervals by **bootstrap percentile, 10,000 replicates,
  resampling instances**, seeded at 20260801.
* Standard errors in regressions are **cluster-robust by shop** (a shop
  contributes many instances through the tariff crossing; treating them as
  independent would overstate precision).
* **Holm–Bonferroni** correction within each reported family of effects.
* Effect sizes reported as Cohen's *d_z* alongside p-values. An effect is
  reported as a finding only if it is **both** statistically distinguishable
  from zero **and** larger than the C5 resolution floor (§5).

## 4. Exclusion rules

Fixed in advance:

1. Runs with `status != "ok"` are excluded and their rate is reported. If the
   failure rate exceeds **2 %**, the cause is investigated before any analysis
   is interpreted.
2. Instance–configuration cells that are not complete across the compared
   configurations are dropped **listwise**, so every paired comparison uses a
   balanced set. The retained *n* is reported for each comparison.
3. No instance is excluded on the basis of its result. There is no outlier
   rule; if the distribution is heavy-tailed, medians are reported alongside
   means rather than trimming.

## 5. The falsification check comes first

Under the **flat tariff** no configuration can create arbitrage value. Check C5
in `integrity_report.txt` measures the largest relative energy-cost difference
across battery levels under a constant price.

**That number is the resolution floor of the entire study.** It is read and
recorded *before* looking at E1–E4. Any effect smaller than it is reported as
"below the resolution of the experiment", never as a small effect.

If the floor exceeds **0.5 %**, the metaheuristic is too noisy at the
configured time limit to support the managerial claims, and the correct
response is to raise the time limit or increase seed replication — not to
report the effects anyway.

## 6. Stopping and re-running rules

* The runlist is generated once and executed to completion. Execution order is
  deterministically shuffled, so an early stop yields a representative subset.
* **No configuration is added after seeing results** without labelling it
  exploratory here.
* If `BATTERY_ON_RATIO` is revised downward after E2 (see README limitation 3),
  E1/E3/E4 are re-run **in full** at the new ratio. Mixing ratios across
  experiments is not permitted.
* Reruns of failed runs use the identical command line and seed.

## 7. Deviations log

Record every departure from the above, with date and reason. An empty section
here at submission time is itself a claim, so keep it honest.

| date | deviation | reason |
|---|---|---|
| 2026-08-17 | §5 fixed the resolution floor as the **largest** relative energy-cost difference across battery levels under the flat tariff, with a 0.5 % admissibility threshold. The paper instead reads the floor off the **flat-tariff placebo cell** of E1: the estimator applied where no storage×state interaction can exist returns $+0.02$ pp with a 95 % interval of $[-0.01, 0.06]$. | Specification error on our part, not a result we disliked. A maximum over ~9000 groups is a worst-case order statistic; it does not bound the standard error of a *mean over paired instances*, which is what every effect in E1–E4 is. Judged against it, the study would declare almost every effect unresolvable while the same estimator demonstrably detects a true zero to within 0.07 pp. The instance-level maximum (18.0 %) remains reported as the bound on **single-instance** claims, which is the question it does answer. |
| 2026-08-17 | The collector reports only the maximum of the flat-tariff difference distribution, not a high percentile of it. | The maximum is the statistic §5 asked for. Adding a percentile is a strict improvement in information and does not change any reported effect; it is recorded here because the pre-registered output set changed. Pending. |

**Open item, not yet a deviation.** §6 forbids mixing `BATTERY_ON_RATIO` across
experiments and requires E1/E3/E4 to be re-run in full if it is revised
downward after E2. It has not been revised: E1, E3 and E4 are all at
$B = E_{\mathrm{day}}$ as pre-registered. E2 subsequently showed that capacity
to be NPV-negative on every instance in every regime, which is a *finding*
about the asset rather than a departure from protocol. Resolving it by
annotating the discrepancy in the text is compliant; resolving it by reporting
E1/E3/E4 at a different ratio than pre-registered, without a full re-run, would
not be. Record the resolution here when taken.

---

## 8. What would falsify the paper's framing

Stated in advance so it cannot be explained away afterwards:

* **H1 fails** (interaction ≈ 0): the levers are additive. This is publishable
  but the narrative changes from "sequencing your investments matters" to
  "the two levers are independent, so evaluate them separately". Do not
  describe an additive result as substitution.
* **H4 fails** (battery reduces energy only by increasing tardiness): the
  reframing of storage as service-level protection collapses, and the paper
  falls back to a conventional arbitrage argument.
* **H2 gives β\* ≈ 0**: storage is never worth buying at current prices for
  this class of plant. That is a legitimate and useful negative result, and it
  must be reported as the headline rather than buried.
* **C5 floor is large**: the study cannot resolve the effects it set out to
  measure, and no amount of statistical treatment fixes it.
