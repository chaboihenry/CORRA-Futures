# Methodology

## The question

When the Bank of Canada announces a rate decision, how far does the news travel, and how much of it reaches each sector?

## The idea

Markets guess the decision in advance, so only the part they got wrong is news. CORRA futures price that guess. The move in the futures price across an announcement is the size of the surprise. Follow it through the economy one step at a time and multiply the steps.

---

## Step 1 — measure the surprise

$$
\text{surprise}_t = -(f_t - f_{t-1}) \times 100
$$

$f$ is the settlement price of the three-month CORRA futures contract.

- The **minus sign** flips price into rate. Futures quote as $100 - R$, so a falling rate raises the price.
- The **×100** converts to basis points.

**Which contract.** Always the *second* quarterly, never the front. The front contract's reference quarter is partly over, so a surprise moves only the remaining days — a correction is needed and it amplifies noise. Tested both ways:

|                              | β on 2Y yield  | R²            |
| ---------------------------- | --------------- | -------------- |
| Front contract, corrected    | 0.746           | 0.18           |
| Second contract, uncorrected | **1.386** | **0.61** |

**Which market.** COA (one-month) is cleaner in theory but trades on 5 of 29 announcement days. CRA (three-month) trades on 36 of 49.

**Sample.** 27 announcements, 2023-04-12 to 2026-07-15. Mean absolute surprise 2.96 bp, SD 4.56 bp.

**Validation.** If the measure were picking up the decision rather than the surprise, the two would match. The surprise averages 15% of the realised move. On 2022-10-26 the Bank raised 50 bp while the surprise was *negative* — markets had priced 75, so a hike was dovish news. A measure keyed to the decision could not produce that.

---

## Step 2 — build the chain

Four nodes, three hops:

```
surprise → government bond yield → a rate banks set → a quantity that responds → an outcome
```

Every node is chosen for a stated mechanism. Three candidates per layer; one selected, others rejected with evidence.

**Households:**

| Node                             | Owner        | Why                                                             |
| -------------------------------- | ------------ | --------------------------------------------------------------- |
| 5Y GoC yield                     | central govt | Canadian mortgages are 5-year fixed                             |
| Effective household lending rate | banks        | Banks fund off the yield, add a spread                          |
| Mortgage origination growth      | households   | Higher rates raise payments, shrink qualifying amounts          |
| Consumer insolvency growth       | households   | Less new credit means fewer households refinance out of trouble |

---

## Step 3 — screen before estimating

An outcome that barely moves cannot show a response:

$$
\frac{\mathrm{SD}(\text{outcome})}{\mathrm{SD}(\Delta \text{parent})}
$$

| Outcome                | SD      | Ratio | Result                 |
| ---------------------- | ------- | ----- | ---------------------- |
| Origination growth     | 19.5 pp | 1.72  | β = −0.742, p<0.0001 |
| NFC credit growth      | 0.45 pp | 0.037 | null                   |
| Mortgage credit growth | 0.23 pp | 0.020 | null                   |

Credit *stocks* run in the trillions and grow 0.4% a month; credit *flows* swing tens of percent.

**Limitation.** A low ratio means the coefficient is *small*, not undetectable — detectability depends on R² and n, which the ratio does not see. Central banks L2 screens 0.398 and estimates at p=0.0002. The screen rejects confidently only at catastrophically low ratios; near the threshold it produces false negatives. Four were found and corrected when a units-scaling bug was fixed.

---

## Step 4 — estimate

**Same-day (layer 1).** Both the surprise and the yield move within the announcement day. One regression, 27 observations.

**Weekly rate on daily rate (layer 2).** Banks reprice over days to weeks, so all lags go into one regression and the coefficients are summed.

**Monthly outcome (layers 3–4).** One regression per horizon, 0 to 12 months. The coefficients trace an impulse response. Unadjusted series get month dummies.

Newey-West standard errors throughout.

**One trap worth naming.** When the child is already a difference or a return, differencing it again makes the lag polynomial sum to zero *by construction* — the response appears at lag 0 and reverses at lag 1. Asset managers layer 2 reported +0.0013 (p=0.44) this way; the true coefficient was −0.064 at t≈83, sitting at lag 0 in the same output. A `diff_y` flag now prevents it.

---

## Step 5 — controls

An edge's adjustment set is **derived from the graph**, not chosen. For edge A → B, condition on every observed node that is a parent of both A and B, and on nothing that is a descendant of A.

Four confounders are modelled, each with a cited mechanism:

| Confounder                    | Proxy                 | Source                                            |
| ----------------------------- | --------------------- | ------------------------------------------------- |
| Global rates                  | `us_2y`             | Rey (2013); Miranda-Agrippino & Rey (2020)        |
| Dealer balance-sheet capacity | `quarter_end_dummy` | Munyan (2015); Anbil & Senyuz (2018)              |
| Domestic demand               | `gdp_monthly`       | Bernanke, Gertler & Gilchrist (1999)              |
| Risk appetite                 | `equity_return_pct` | Sirri & Tufano (1998); Chevalier & Ellison (1997) |

`us_2y` is excluded at layer 1 for a window-specific reason: inside the announcement window the spillover runs Canada→US, making it a treatment descendant. At weekly and monthly frequency it is a sibling of the GoC yields under the global fork.

**The audit checks the graph against itself.** Facts outside the modelled edges stay outside it — `tsx_return` contains banks at ~20% weight, and `gdp_monthly` may be a mediator on a path the graph does not model. Neither is visible to any walk of the edge list.

---

## Step 6 — multiply

$$
\beta_{\text{total}} = \beta_1 \times \beta_2 \times \beta_3
$$

Uncertainty propagated by the delta method.

**Report per realistic shock.** The largest monthly move in the household lending rate is 30 bp, so quoting "per 100 bp" extrapolates nine standard deviations past the data. Everything is scaled to a one-SD surprise, 4.56 bp.

---

## Result

| Edge                                    | β      | SE    |
| --------------------------------------- | ------- | ----- |
| surprise → 5Y yield                    | 1.071   | 0.189 |
| 5Y yield → lending rate                | 0.576   | 0.082 |
| lending rate → origination growth      | −0.742 | 0.146 |
| origination growth → insolvency growth | −0.296 | 0.059 |

Product **0.135**. A 4.56 bp surprise raises consumer insolvency growth by **0.61 pp**, 95% CI **[0.18, 1.05]**.

**Is the last edge real?** Insolvencies might be caused by the rate directly — a fork, not a chain. Conditioning on the lending rate moves β from −0.29594 to −0.29464, keeping 99.6%. It is downstream of originations.

**Is the treatment a confounder?** Conditioning on the surprise raises β₃ from −0.742 to −0.975. An open backdoor would inflate and conditioning would shrink; the opposite happened, so the treatment was masking part of the effect rather than contaminating it. Tested on this edge only.

---

## What could not be estimated

|                                 | Why                                                                  |
| ------------------------------- | -------------------------------------------------------------------- |
| One-month CORRA futures         | 5 of 29 announcement days                                            |
| Posted mortgage rates           | No response at any lag, R² < 0.02                                   |
| Credit stocks                   | SD 0.2–0.5 pp, too smooth                                           |
| Quarterly series                | ~13 observations                                                     |
| `bank_prime_rate`             | Resets one-for-one with the target. An identity                      |
| `bank_funding_spread`         | 93% of the coefficient is the T-bill leg; the deposit leg is null    |
| `goc_2y_5y_slope`             | Contains this chain's layer 1; the`goc_2y` leg is 83% of it        |
| `zag_xic_return_spread` on L2 | Contains`bond_index_return_pct`; identity to 0.0e+00               |
| `tbill_auction_yield`         | β = 0.96 against`tbill_3m` — the same instrument cross-validated |
| `tbill_coverage`              | Null in both scalings, and the sign opposes the stated mechanism     |

The last four share a pattern: **a node that contains the thing it is regressed on measures the construction, not a mechanism.**

---

## Limitations

**Daily data, not intraday.** Kuttner's (2001) original method; the intraday refinement needs paid data.

**Eight of 27 announcements share a date with an FOMC statement**, all in 2025–26. CRA settles 15:00 ET, the Fed releases 14:00. Kept and flagged — excluding them would remove the easing cycle.

**A three-month contract spans two BoC meetings**, so the surprise is a shock to the near-term policy *path*.

**Small samples at the far end.** Layers 3–4 have ~33 monthly observations against up to 15 parameters. Only the peak horizon is reported.

---

## Sources

MX daily summaries · BoC Valet API · BoC press releases · Federal Reserve FOMC calendars · StatCan 36-10-0639-01, 36-10-0640-01, 10-10-0144-01 · OSB insolvency statistics · FRED · yfinance. No paid data.
