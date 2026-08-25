# Pre-registration — campaign v2 (IJPR managerial experiments)

Written **before** the run. The point is to fix what counts as a finding in
advance, so the analysis cannot drift toward whatever the data happens to show.
If anything here changes after results are seen, record the change and the date
in §9 and label the affected analysis exploratory.

Design frozen at: `config/design.py`, `MASTER_SEED = 20260824`,
`PROFILE = "full"`, `TL_GA = 300`.
Seed counts are the ONE design constant deliberately left open, and §6 fixes
in advance the rule that closes it.
Machine archetypes frozen at: `config/machines.py`.
Economics frozen at: `config/economics.py`.

The v1 pre-registration (E0–E9, `MASTER_SEED = 20260801`) is superseded and
lives in git history. v1 results are not pooled with v2 results anywhere.

---

## 1. Hypotheses

**H0a (instrument precision).** The GA's run-to-run standard deviation
σ_seed, measured at the campaign's time budget, is small enough that k seeds
resolve the target effect: with the pre-registered minimum detectable effect
δ = 0.5 % of the naive energy bill, `design.required_seeds` returns a finite k
no larger than 5 for every experiment.
Test: MR §1 and §4.
**If it returns ∞ for an experiment**, seed replication is not the constraint —
that experiment's instance count is — and the paper states the effect size that
experiment can resolve instead of reporting effects below it.

**H0b (homoscedasticity across treatments).** σ_seed does not depend on the
battery level, on the machine archetype, or on the tariff regime.
Test: MR §2, Brown–Forsythe on median-centred residuals, plus the max/min σ
ratio. Predicted: ratio < 1.5 and p > 0.05.
**This is a gate on interpretation, not on execution.** If it fails, a
difference of means across that factor is partly a difference of dispersions:
effects across it are reported against the LARGER σ_seed rather than the pooled
one, the violation is named in Threats to Validity, and no paired p-value across
that factor is quoted without it.

**H0c (instrument validity).** The GA's optimality gap is small on instances
where the compact MILP proves optimality, and — the operative part — it is
*invariant to the battery level*.
Test: M0 §2 and §3. Predicted: mean normalised gap < 2 % of the naive energy
bill, and the range of that gap across battery levels below 0.5 pp.
**H0c is a gate, not a finding.** If the invariance part fails, H1–H4 are still
estimated but every storage effect is reported against the measured range and
any effect below it is declared unresolvable.

H0a–H0c together characterise the instrument: H0c its accuracy, H0a its
precision, H0b whether that precision is the same everywhere. All three are
read before any managerial number.

**H1 (the return depends on all three levers jointly).** The relative saving
from storage is not additive in (capacity, tariff shape, machine transition
graph): at least one two-way interaction is distinguishable from zero and
larger than the flat-tariff resolution floor.
Test: M1 §2, three-factor variance decomposition, shop-clustered SEs.
Predicted: the tariff × machine interaction is the largest of the three, and
the return to storage falls as the machine becomes cheaper to switch.

**H2 (sizing).** The NPV-maximising capacity is strictly below the capacity at
which the marginal value of storage reaches zero.
Test: M1 §3. Predicted β* < β_sat, with β* at most half of β_sat, in every
(archetype, tariff) cell where β* > 0.

**H3 (fluctuation height).** The relative saving increases in the realised
intra-day spread, and there is a spread below which the investment is
NPV-negative.
Test: M2, coefficient on `spread_intraday`, positive and significant with
shop-clustered SEs, **estimated separately on real and on synthetic tariffs**.
Predicted: positive in both. **A disagreement in sign or significance between
the two families falsifies the external reading of H3**, and the finding then
becomes "the relationship is identifiable only under a controlled design",
which is reported as such and not as a screening rule.

**H4 (frontier).** Storage shifts the service–energy frontier inward and
flattens it: at matched λ, adding a battery reduces energy cost without a
proportionate increase in tardiness.
Test: M5 §3. Predicted dEnergy < 0 and dTardiness indistinguishable from zero
at every λ.

**H5 (substitution).** Machine-state management and storage are partial
substitutes: the joint reduction is strictly smaller than the sum of the
individual ones.
Test: M4, interaction I and substitution index SI = −I / min(V_σ, V_β).
Predicted I < 0 and SI > 0.05, **at every capacity in
`design.M4_BATTERY_RATIOS`**, not only at the saturating one.

**H6 (scale).** The relative saving per horizon day does not decline in the
number of tasks once the horizon is controlled for.
Test: M3 §1, slope of saving on log n with horizon days as a covariate.
Predicted: slope indistinguishable from zero. A significant decline is a real
finding (storage value dilutes in large programmes) and is reported as one.

---

## 2. Outcome hierarchy — what is confirmatory and what is not

Fixed here, before any data, because a campaign with this many outputs
otherwise reads as a hypothesis factory. Only the primary tier carries
confirmatory weight; the rest is reported with its status attached, and a
secondary or exploratory result is never promoted to a headline after the fact.

**Primary (confirmatory).** Four numbers, one per hypothesis the paper's
argument actually needs:

| outcome | experiment | hypothesis |
|---|---|---|
| NPV-optimal capacity, and its ratio to the saturating capacity | M1 §3 | H2 |
| relative saving from storage, by tariff shape and machine archetype | M1 §1 | — |
| the three-way interaction in the ROI cube | M1 §2 | H1 |
| the service–energy trade-off at matched λ | M5 §3 | H4 |

**Secondary (supporting, reported in full, not headline-bearing).** The
volatility regression and its synthetic-versus-real diagnostic (M2, H3); the
substitution decomposition and index (M4, H5); the scaling slope (M3, H6).
These answer real questions and several are among the paper's more interesting
results — M4 in particular — but the paper's central claim does not depend on
them, and treating them as confirmatory would multiply the confirmatory family
by three for no gain in what is being argued.

**Diagnostics (instrument, not findings).** σ_seed and its homogeneity across
treatments (MR); required seed counts (MR §4); the GA's gap to the MILP and its
stability across battery levels (M0); anytime profiles (M0 §5); VIFs and
collinearity (M2); the flat-tariff placebo (M4) and the flat-tariff resolution
floor (integrity C5). These are read *before* the outcomes they license and are
never reported as results about storage.

**Exploratory (labelled as such wherever they appear).** Anything not listed
above: the (ρ, restart) response surface beyond its two main effects, per-regime
breakdowns not pre-specified here, the EFC/degradation figures, and any
subgroup analysis suggested by the data.

Holm–Bonferroni is applied within each family in the primary tier, and within
each table in the secondary tier. It is not applied across tiers: correcting a
confirmatory test for the existence of a diagnostic would be nonsense.

## 3. Primary outcome definitions

| | |
|---|---|
| primary | relative saving in **total cost** (energy + tardiness), normalised by `norm_scale` |
| secondary | the energy component; the tardiness component; equivalent full cycles per day |
| M1 only | NPV, discounted payback and NPV-positive share under all three `config/economics.py` scenarios |
| M0 only | gap to the MILP, its stability across battery levels, and seed dispersion |
| MR only | σ_seed (pooled, per cell, and per treatment level); the share of each paired contrast's variance that is seed noise; the required seed count per experiment |

**Why `norm_scale` and not percent-of-baseline.** 64 % of v1's price series
contained negative hours, which drives the baseline cost toward zero and makes
a percentage unbounded — that is what produced v1's >100 % savings. The
denominator used throughout is instead the energy bill of running the EI
machine flat out at the tariff's mean price, which is strictly positive,
identical across every configuration compared, and independent of the
treatment. A normalised saving of 0.10 means "one tenth of the naive energy
bill". Percent-of-baseline is still printed where a referee would expect it,
and flagged where the two disagree.

**Unit of analysis: the instance.** Seeds are averaged within
instance × configuration *before* any comparison, so seed replication does not
inflate the sample size.

**Sign convention, fixed here.** In M1, M2 and M4 a positive number is a
saving on total cost. In M5 the two components are reported as signed deltas
(negative = improvement) because the whole point is their direction relative to
one another.

## 4. Statistical analysis, fixed in advance

* All comparisons **paired on the instance**; configurations are evaluated on
  identical instances by construction, and the completeness audit in
  `data/balance_report.txt` verifies that every sub-design block is a complete
  factorial before the campaign runs.
* 95 % confidence intervals by **bootstrap percentile, 10,000 replicates,
  resampling instances** (shops, for regressions), seeded at `MASTER_SEED`.
* Regression standard errors are **cluster-robust by shop**: a shop
  contributes many instances through the tariff crossing, and treating them as
  independent would overstate precision. Fits with fewer than 5 clusters report
  point estimates only, with standard errors suppressed.
* **Holm–Bonferroni within each reported family of effects**, where a family is
  one factor in one table — never the union of every test in a report.
* Effect sizes as Cohen's *d_z* alongside p-values. Degenerate cases
  (zero within-instance variance) report the effect and suppress d_z and p
  rather than printing an astronomical statistic.
* **Seeds are averaged within instance × configuration before any comparison**
  (`how="mean"`, never best-of-k: best-of-k is biased in k, and a plant does not
  re-run its scheduler five times and keep the luckiest answer). Every report
  carries a seed-noise footnote stating, from MR's σ_seed and its own seed
  count, the standard error that seed noise alone contributes to any paired
  mean difference in it. An effect near that size is reported as unresolved by
  that experiment.
* An effect is reported as a finding only if it is **both** statistically
  distinguishable from zero **and** larger than the resolution floor (§5).

## 5. Exclusion rules

Fixed in advance:

1. Runs with `status != "ok"` are excluded and their rate is reported. Above
   **2 %**, the cause is investigated before any analysis is interpreted.
2. Instance–configuration cells not complete across the compared
   configurations are dropped **listwise**, so every paired comparison uses a
   balanced set. The retained *n* is reported for each comparison.
3. No instance is excluded on the basis of its result. There is no outlier
   rule; if the distribution is heavy-tailed, medians are reported alongside
   means rather than trimming.
4. v1 results are never pooled with v2 results. Different master seed,
   different time limit, different solver revision.

## 6. The falsification check comes first

Under the **flat tariff** no configuration can create arbitrage value. Check C5
in `integrity_report.txt` measures the distribution of the relative
energy-cost difference across battery levels under a constant price, holding
every other factor of the configuration fixed.

**That distribution is the resolution floor of the campaign.** It is read and
recorded *before* looking at M1–M5. Any effect smaller than it is reported as
"below the resolution of the experiment", never as a small effect.

Two statistics, for two different questions, and they are not
interchangeable — v1 conflated them and had to log a deviation:

* the **maximum** over groups bounds claims about a *single instance*;
* the **flat-tariff placebo cell of M4** — the same estimator applied where no
  interaction can exist — bounds claims about a *mean over paired instances*,
  which is what every effect in M1–M5 actually is.

If the placebo cell returns an interaction distinguishable from zero, the
estimator is biased and no M4 result is interpretable until that is fixed.

## 7. The one design constant set after seeing data, and the rule that sets it

Seed counts are chosen from MR's output rather than fixed here. That is a
departure from "no configuration is added after seeing results", so the rule is
written out in full **before** MR runs, leaves no discretion, and is applied
once:

1. MR runs first, alone, at the campaign's time budget.
2. σ_seed is the pooled within-cell standard deviation over every MR cell, in
   percent of `norm_scale`. σ_effect is the largest residual effect SD across
   the measured paired contrasts (the conservative choice).
3. For each experiment, k is `ceil(design.required_seeds(σ_seed, σ_effect, n))`
   with n that experiment's instance count, δ = `MDE_TARGET_PCT` = 0.5 %,
   two-sided α = 5 %, power = 80 %, floored at `MIN_SEEDS` = 3.
   The floor is fixed here, before any data, for two reasons that are not
   statistical: single-run metaheuristic results are not a publishable protocol
   whatever the arithmetic says, and k = 1 leaves no within-cell variance, so
   the campaign could not check that its own noise level matches what MR found.
   The report prints the unfloored k alongside, and where the two differ the
   paper says the extra seeds bought reporting credibility rather than
   precision — which is a different claim from having needed them.
4. Those k values are written into `design.SEEDS_PER_EXP`, the runlist is
   regenerated, the budget is re-checked, and the campaign runs. The MR report
   and the resulting runlist are both archived.
5. **No k is chosen by looking at an M1–M5 result.** If the budget cannot
   accommodate the required k, the response is to reduce the instance pool or
   to raise the declared MDE and say so — never to lower k below what the
   formula returns and report the effects anyway.

Nothing else in this document is revised after data are seen.

## 8. Stopping and re-running rules

* The runlist is generated once and executed to completion. Execution order is
  deterministically shuffled, so an early stop yields a representative subset
  of the whole design rather than all of M0 and none of M5.
* **No configuration is added after seeing results** without labelling it
  exploratory in §7.
* Reruns of failed runs use the identical command line and seed.
* **Capacity levels are not revised after the fact.** v1 pre-registered its
  non-sweep experiments at 1.0 E_day and then found that capacity NPV-negative
  everywhere, which left it choosing between an un-run protocol and a caveat.
  v2 avoids the dilemma by carrying both 0.1 and 1.0 in every non-sweep
  experiment from the start, so whichever the sizing answer turns out to be,
  the corresponding cells already exist.
* If the real market-year files arrive after the campaign starts, M2 is
  re-run in full at the enlarged tariff library. Partial tariff coverage is
  not merged with full coverage in one regression.

## 9. Deviations log

Record every departure, with date and reason. An empty section here at
submission time is itself a claim, so keep it honest.

| date | deviation | reason |
|---|---|---|
| | | |

## 10. What would falsify the paper's framing

* **H0b fails (σ_seed is heteroscedastic).** Paired comparisons across that
  factor mix a difference of means with a difference of dispersions. Report
  against the larger σ, name the violation, and do not quote a paired p-value
  across that factor without it.
* **H0a returns ∞.** Seeds are not the constraint; instances are. State what
  the experiment can resolve rather than reporting effects below it.
* **H0c's invariance fails.** The measured storage benefit is partly the
  solver's. Everything downstream is reported against that floor; effects below
  it are not findings. This does not invalidate rankings, only magnitudes.
* **H1 fails (interaction ≈ 0).** The levers are additive. Publishable, but the
  narrative becomes "evaluate the three separately", and the cube was more
  design than the question needed. Do not describe an additive result as an
  interaction.
* **H2 gives β\* ≈ 0 everywhere.** Storage is never worth buying at current
  costs for this class of plant. A legitimate and useful negative result; it
  must be the headline, not buried.
* **H3's two families disagree.** The spread relationship is a property of the
  generator. Report the diagnostic prominently, demote the screening rule to a
  design-experiment result, and state what data would settle it.
* **H4 fails.** Storage buys energy savings with delivery performance like any
  other lever, and the service-level-protection framing collapses back to a
  conventional arbitrage argument.
* **H5 gives I ≈ 0.** The two levers are independent, so investment sequencing
  does not matter and they can be appraised separately. Still a result; a
  different one.
* **The resolution floor is large.** The campaign cannot resolve what it set
  out to measure. The correct response is to raise the time limit or the seed
  replication and re-run — not to report the effects anyway.
