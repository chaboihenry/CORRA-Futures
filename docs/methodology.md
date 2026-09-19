# Methodology

Snapshot `2026-09-16`, 75 nodes.

---

## 1. Treatment

Three-Month CORRA Futures (CRA) on the Montreal Exchange, **second quarterly
contract**, rolled unconditionally.

`surprise = -(settle_t - settle_prev) * 100`, in bp. The sign flip puts it in
rate space: positive means the market repriced the path upward.

27 announcements, 2023-04-12 to 2026-07-15. Mean absolute surprise 3.19bp,
SD 4.56bp.

**Why the second contract.** The front contract with Kuttner-style scaling
gives R² 0.18 against 0.61 uncorrected. The second contract prices months four
to six, the horizon a path surprise expresses. The one-month contract (COA)
trades on only 5 of 29 announcement days; CRA trades on 36 of 49.

Shorter-tenor layer-1 candidates fail on horizon mismatch: `tbill_3m` gives
β = 0.501 with the set's only significant intercept (−2.02, p = 0.0006),
because a 3-month bill covers months zero to three. `tbill_6m` gives 0.674,
`tbill_1y` 0.684.

**Validation.** The surprise averages ~15% of the realized decision.
2022-10-26 is the strongest check: a +50bp hike with a −17.1bp surprise, i.e.
dovish under-delivery. Opposite signs confirm the measure captures the
unanticipated component.

**Known contamination.** Eight of 27 announcements share a date with FOMC. CRA
settles 15:00 ET, FOMC releases 14:00 ET, so daily data cannot separate them.
Kept and flagged rather than dropped — excluding them removes the entire
2025–26 easing cycle. A 19-event non-FOMC subsample is reported where
relevant.

---

## 2. Estimators

Estimators know nothing about stakeholders: each takes two Series and a spec
dict and returns a result dict. That is what lets the orchestration layer loop
generically over the graph.

| Estimator            | Used for           | Reported row                         |
| -------------------- | ------------------ | ------------------------------------ |
| `event_study`      | layer 1            | the event coefficient                |
| `distributed_lag`  | rate pass-through  | the`sum` row                       |
| `local_projection` | quantity responses | largest-absolute significant horizon |

HC3 at layer 1, HAC elsewhere. `headline()` picks the reportable row and is
aware of whether the child was already differenced, so a `sum` is not taken
over a series that cancels to zero by construction.

---

## 3. Controls

Controls are derived from the graph, not chosen by hand. `adjustment_set()`
reads `exogenous_nodes` and returns the implied confounders;
`reconcile_controls()` diffs that against the hand-written `controls:` list
and writes `results/tables/control_audit.csv`.

**Classification.** *Derived* — covers both ends, a common cause, blocks a
backdoor, required. *Precision* — covers one end only; cannot open a backdoor
or bias the estimate, absorbs residual variance, admissible. *Unjustified* —
covers neither end, or is a descendant of the parent; excluded.

The precision category was added after a mistake: one-ended controls were
initially read as unjustified and stripped, which cost central banks L2 its
significance. No control currently reads unjustified and none is missing.

| Node                  | Proxy              | Role                          | Citation                                                                         |
| --------------------- | ------------------ | ----------------------------- | -------------------------------------------------------------------------------- |
| `us_2y`             | FRED DGS2          | global rate cycle             | Rey (2013); Miranda-Agrippino & Rey (2020),*ReStud* 87(6); Bruno & Shin (2015) |
| `gdp_growth`        | StatCan 36-10-0434 | domestic demand               | Bernanke, Gertler & Gilchrist (1999)                                             |
| `vix`               | FRED VIXCLS        | risk appetite / illiquidity   | Goldstein, Jiang & Ng (2017),*JFE* 126(3)                                      |
| `sp500_return_pct`  | ^GSPC              | market equity factor          | Bernanke & Kuttner (2005),*JF* 60(3)                                           |
| `quarter_end_dummy` | calendar           | dealer balance-sheet capacity | Munyan (2015), OFR WP 2015-22; Anbil & Senyuz (2018), FEDS                       |
| `cra_roll_dummy`    | calendar           | contract roll mechanics       | MX contract specifications                                                       |

The banks chain follows English, Van den Heuvel & Zakrajšek (2018), *JME* 98,
80–97, who estimate bank equity responses to rate shocks identified from
policy announcements.

### The layer-1 control decision

`us_2y` was originally excluded at layer 1 as a treatment descendant inside
the window. Tested and not supported as the dominant channel: regressing the
same-day `us_2y` change on the surprise gives 0.631 (p = 0.079, n = 27),
0.469 (p = 0.197, n = 19 excluding FOMC days), and 0.120 (p = 0.764, n = 17
also excluding two likely US CPI days). The estimate collapses as candidate
US-news days are removed — what a contaminated-window story predicts and a
spillover story does not.

**This is low-powered evidence of absence, not evidence of absence.** None of
the three estimates is individually significant.

| Node    | Uncontrolled β (se) | Controlled β (se) | R²          |
| ------- | -------------------- | ------------------ | ------------ |
| goc_2y  | 1.386 (0.176)        | 1.050 (0.222)      | 0.61 → 0.87 |
| goc_5y  | 1.071 (0.189)        | 0.693 (0.243)      | 0.44 → 0.84 |
| goc_10y | 0.815 (0.234)        | 0.459 (0.327)      | 0.32 → 0.75 |

Two consequences. The earlier hump-above-unity finding does not survive:
`goc_2y` rejected unity at p = 0.028 uncontrolled, p = 0.821 controlled. And
`goc_10y` becomes non-significant, removing layer-1 identification for central
banks and asset managers.

**Caveat against the control.** An, Dilts Stedman & Lusompa (2025, FRB Kansas
City RWP 25-03) compare daily and 30-minute intradaily surprises and find no
evidence that contemporaneous news biases daily measurement; Bernanke &
Kuttner (2005) argue similarly. Our position is that a full-day MX window is
weaker than a 30-minute one, but the literature does not settle in our favour.
Both columns are reported for that reason.

**Deliberately not used.** Conditioning on the same-day `goc_2y` move turns
the `us_2y` coefficient negative. This is not evidence: `goc_2y` is a common
child of the surprise and of `us_2y`, so this conditions on a collider, and
the sign flip appears under both hypotheses.

---

## 4. Screens and guards

**Variance screen.** Ratio = SD(child) / SD(Δparent), resampled to the coarser
frequency, with `screen_scale` correcting the percent/pp asymmetry. It is
**necessary, not sufficient**: a low ratio means a small coefficient, not an
undetectable one, since detectability depends on R² and n. Asset managers L3
screens at 0.007 and still clears significance at h = 7.

**Unit composition.** `UNIT_KIND` groups units into rate, quantity, index and
dummy. `chain_all` refuses to multiply across a boundary it cannot cancel,
returning `units_do_not_compose`. This blocked central governments while
`tbill_coverage`, a dimensionless ratio, sat at layer 3.

**Broken links.** A chain's edges must form an unbroken run. An `ok` layer past
a gap returns `broken_link` rather than being composed. Currently triggers on
asset managers, controlled column.

**Data unavailable.** A layer that stops because its node is missing from the
snapshot (e.g. the five MX-derived hedge_funds nodes, excluded from the
published snapshot for licensing) returns `data_unavailable`, naming the node
and snapshot, rather than being silently accepted as an `ok` chain truncated
one edge short. Currently triggers on hedge_funds against the published
snapshot.

**Minimum observations.** `MIN_OBS = 20`. *Known gap:* it counts raw n, not n
minus parameters, so high-parameter edges pass on little information — exactly
what happened with the households L4 candidates at n = 22 against 15
parameters.

**Construction overlap.** `shares_construction()` walks each derived node's
`inputs:` chain. It caught four nodes. It **cannot** detect an identity
between independently sourced series, which is how the asset managers
bond-math identity survived it.

---

## 5. Leverage at layer 1

Surprises in bp:

```
-7.5 -7.5 -3.5 -3.0 -2.0 -2.0 -1.5 -1.0 -1.0 -0.5 -0.0 -0.0
 0.5  0.5  0.5  1.0  1.0  2.5  2.5  3.0  3.0  3.5  4.5  5.0
 6.5  7.0 15.5
```

The +15.5bp event is 2023-06-07, the hike ending the pause. Genuine, not an
artifact: realized decision +25bp, volume 29,626 against 7,512 on the prior
settle, two weeks outside the roll window.

Leverage h = 0.425 uncontrolled (threshold 0.148), h = 0.443 controlled
(threshold 0.222). The controlled spec surfaces a second high-leverage point,
2024-04-10 at h = 0.316 — one of the two dates independently flagged as a
likely US CPI day.

Leave-one-out, dropping 2023-06-07:

| Node    | Controlled β, full → LOO | Uncontrolled β, full → LOO |
| ------- | -------------------------- | ---------------------------- |
| goc_2y  | 1.050 → 0.859 (−18%)     | 1.386 → 1.344 (−3%)        |
| goc_5y  | 0.693 → 0.473 (−32%)     | 1.071 → 1.020 (−5%)        |
| goc_10y | 0.459 → 0.158 (−66%)     | 0.815 → 0.683 (−16%)       |

**The instability is concentrated in the controlled column.** In the
uncontrolled `goc_2y` and `goc_5y` regressions this observation is not even
top-3 by DFBETA. Adding `us_2y_chg` spends a degree of freedom on 27 points
and concentrates leverage onto one day.

Stability is not unbiasedness, so this does not make the uncontrolled column
correct. It means neither is clean — a second reason both are reported.

The observation is retained. A large genuine repricing is what an event study
wants, and excluding an influential point because it is influential would be
trimming the data to the result.

---

## 6. Rejected nodes

**Construction artifacts.** `bank_prime_rate` resets one-for-one with the
target the next business day. `bank_funding_spread` decomposes exactly —
gic_1y −0.066 minus tbill_1y 0.867 equals the spread's −0.933, so 93% is the
T-bill leg. `goc_2y_5y_slope` splits to 5.8e-16, with the goc_2y leg 82.6% of
the total. `zag_xic_return_spread` against layer 2 reproduces 0.9773 against
0.9773. `bond_index_return_pct` on goc_10y gives R² 0.914, SE one-eightieth of
the coefficient, and an implied modified duration of 6.4 years against ZAG's
~7 — a bond-math identity between independently sourced series, invisible to
lineage checks.

**Stocks rather than flows.** Against a regressor SD of 11–12bp: origination
growth (flow, outcome SD 19.47pp) gives β = −0.742, p < 0.0001; NFC credit
growth (stock, 0.45pp) and mortgage credit growth (stock, 0.079pp) give no
significant horizon. A stock varies roughly 40× less. Also rejected on this
basis: `personal_term_deposits`, `boc_goc_holdings`, `bond_fund_aum`,
`bank_credit_growth`.

**Sample too thin.** `household_debt_service_ratio`, `boc_net_income`,
`statcan_real_estate_lending` — quarterly, ~13 observations.

**Households layer 4**, four candidates, chain capped at three:

| Candidate                        | Screen        | Estimate                                | Reason                                                                            |
| -------------------------------- | ------------- | --------------------------------------- | --------------------------------------------------------------------------------- |
| `consumer_insolvencies_growth` | —            | β = −0.296                            | withdrawn: double-differenced an already-differenced parent                       |
| `consumer_proposal_share`      | 0.052, fails  | −2.932 at h = 12, n = 22 vs 15 params  | significant horizons alternate sign: +1.48, −2.25, −1.78, +0.68, −1.40, −2.93 |
| `consumer_proposals_growth`    | 0.567, passes | −0.119, p = 0.004, n = 22 vs 15 params | worse sample-to-parameter ratio than the withdrawn estimate                       |
| `consumer_bankruptcies_growth` | 0.621, passes | +0.196 at h = 2, n = 32                 | healthier sample, wrong sign                                                      |

**Other.** `tbill_coverage`: null in level (+0.00211, p = 0.267) and growth
(+0.0170, p = 0.889), wrong sign in both, index-kind so it tripped the unit
guard. `tbill_auction_yield`: cross-validates against `tbill_3m` at β = 0.9607,
p < 1e-16 — the same instrument. `bond_etf_flow_rate`: screens 0.059, in the
credit-stock null range, and an industry aggregate standing in for a
fund-specific quantity. `nfc_credit_flow` and `cra_open_interest_chg`:
quantities against rate parents, rejected by the unit guard.
`housing_activity`: a mediator on the rate-to-originations edge.

---

## 7. Findings that did not survive

- **The hump-shaped term structure.** `goc_2y` at 1.386 rejected unity at
  p = 0.028, read as amplification implying a signalling channel. Controlled,
  it is 1.050 with p = 0.821. What remains is a monotonic decline across the
  curve, consistent with the expectations hypothesis. The hump was the global
  rate component loading on the short end.
- **Households L4 at β = −0.296.** Double-differenced specification.
  Withdrawn.
- **Central banks L2 at p = 0.0002.** In earlier notes but not reproducible
  under any specification against the current snapshot. It had been cited as
  evidence the variance screen is unreliable near 0.5; that claim now rests on
  asset managers L3 instead.

The third item is why the snapshot mechanism exists.

---

## 8. Bugs found and what they cost

- **`to_bp` percent/pp asymmetry.** Percent scales ×100 to bp, pp does not,
  though both are the same unit. Four false negatives in the variance screen
  before `screen_scale`.
- **`drop_missing`.** A series published on a finer grid than it is observed on
  carries empty stamps between observations. Kept, they annihilate every first
  difference, because each sits next to a gap.
- **Positional shift in `divide`/`lag_b`.** Shifting by position pairs a value
  with whatever row precedes it in the file, so a missing month closes the gap
  instead of producing one. The IFIC series is missing 2024-03, which would
  have silently given 2024-04 a two-month asset base.
- **`MIN_OBS` counts raw n**, not n minus parameters.
- **Double-differencing.** `diff_x` defaulted True, differencing
  already-differenced parents on four edges. Withdrew the households L4
  estimate.
- **`diff_controls` frequency collapse.** A control was resampled to monthly
  regardless of the edge's frequency, so a daily edge kept ~one observation per
  month. Asset managers L3 fell from n = 855 to 22 **and flipped sign**; hedge
  funds L2 from 836 to 21. Both looked significant. An assertion now fires when
  a controlled join drops below half the uncontrolled n.
- **`chain_all` composed across gaps**, taking a layer 1 × layer 3 product with
  layer 2 null and reporting `ok`.
- **Precision covariates read as unjustified**, costing central banks L2 its
  control on incorrect reasoning.
- **yfinance column override lost.** A stale-read edit reverted
  `fetch_yfinance_node` to a hardcoded `Close`, so `bond_etf_volume` returned
  ZAG's price — reintroducing the duration identity just rejected, under a node
  name that looked clean. Lineage checks cannot catch this; the tell was an SD
  of 0.096 against an expected 68.7.
- **SIMA download URLs.** Direct paths 301-redirect to a blank handler for
  every request, valid or not, never returning 404. Resolution requires
  crawling the paginated archive for opaque attachment IDs.

---

## 9. Limitations

1. **Sample size.** 27 announcements at layer 1; see section 5.
2. **Daily windows.** US morning releases fall inside the MX settlement window.
3. **Credit supply versus demand.** Khwaja & Mian (2008) and Jiménez, Ongena,
   Peydró & Saurina (2012) identify supply shocks with firm-time fixed effects
   on matched bank-firm loan data. Aggregate monthly series cannot, so every
   quantity edge is an equilibrium response.
4. **Terminal-layer direction.** See the cross-cutting finding in the README.
5. **Shared nodes.** `lending_deposit_spread` appears in both the general
   banking and banks chains under different parents. Those chains measure
   overlapping transmission and are not independent findings.
6. **`gdp_growth` role unresolved.** On NFCs L4, conditioning on it raises β
   from +7.011 to +7.826 — consistent with *both* a confounder (an open
   backdoor inflates a naive estimate) and a mediator (blocking a negatively
   signed indirect path raises the direct coefficient). Not diagnostic; the
   role is left open.
