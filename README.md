# Using DAGs to Highlight 4th-Order Causal Links in the CORRA Futures Market

Quantifying causal links between an unanticipated Bank of Canada policy move
and seven stakeholder sectors, using a theory-motivated DAG estimated edge by
edge.

The graph structure is an **input**, not an output. Nodes and arrows come from
published economics; the data supplies magnitudes. This is not causal
discovery.

All figures below are from snapshot `2026-09-16`.

---

## The roadmap, answered

**1. Stakeholder–product relationships, 3 per stakeholder.** Met. About 63
candidate nodes across eight chains, roughly eight per stakeholder, each either
selected or rejected with a stated arithmetic reason.

**2. Lead-lag relationships and causal power.** Met. 25 edges estimated in
`results/tables/edges.csv` with β, SE, p, n and peak horizon, in controlled
and uncontrolled specifications. Lead-lag is handled by the estimator:
`distributed_lag` for rate pass-through, `local_projection` for quantity
responses.

**3. Four nodes per chain.** Partially met, and this is the main result. Two
chains reach four edges, three reach three, one reaches two, and two reach
one. Chains were not padded. Every shortfall is a node tested and rejected on
arithmetic. A four-node chain with a dead or artifactual link measures
nothing; a three-node chain whose edges all estimate measures something.

---

## Results

Treatment: change in the second quarterly CRA contract across a BoC
announcement, in bp, sign-flipped so positive is hawkish. 27 announcements,
2023-04-12 to 2026-07-15, SD 4.56bp.

Effects are for a 1-SD (4.56bp) hawkish surprise, controlled, 95% CI.

| Stakeholder         | Edges | Effect                            | 95% CI             | Status                     |
| ------------------- | ----- | --------------------------------- | ------------------ | -------------------------- |
| Households          | 3     | −1.34 pp origination growth      | [−2.46, −0.22]   | **significant**      |
| Central governments | 3     | +0.77 pp T-bill stock growth      | [0.08, 1.47]       | significant, sign flagged  |
| NFCs                | 4     | −0.62 pp insolvency growth       | [−1.27, 0.02]     | crosses zero, sign flagged |
| Hedge funds         | 2     | +0.15 pp CRA open interest growth | [−0.004, 0.30]    | crosses zero               |
| General banking     | 3     | −0.25 bp lending-deposit spread  | [−3.09, 2.59]     | crosses zero               |
| Central banks       | 1     | +2.09 bp on the 10Y yield         | [−0.83, 5.02]     | not identified             |
| Banks               | 4     | −0.0008 pp bank equity return    | [−0.0098, 0.0083] | crosses zero               |
| Asset managers      | —    | no product                        | —                 | broken link at layer 2     |

One chain has a confidence interval excluding zero **and** a sign consistent
with its mechanism: households.

The rendered graph is at `results/figures/dag.svg`. Edge labels are per-edge
β (SE) with units, **not cumulative** — chain products are in
`results/tables/chains.csv`.

---

## How a surprise reaches each stakeholder

Each walkthrough traces the same 4.56bp hawkish surprise, edge by edge.

### Households — 3 edges, significant

The surprise raises the 5Y GoC yield by **3.16bp** (β = 0.693). Canadian
mortgages are 5-year fixed with renewal, so the 5Y is the pricing anchor.
Banks fund off that yield and pass roughly 57% into transaction rates, lifting
the effective household rate by **1.81bp** (β = 0.572). Higher effective rates
raise payments on new and renewing mortgages and shrink qualifying amounts
under the stress test, cutting origination growth by **1.34 pp** (β = −0.742,
p < 0.0001).

Every edge is significant with the expected sign. The pass-through contrast is
itself a finding: *posted* mortgage rates do not respond at all (R² < 0.02 at
every lag), while transaction rates do.

### Central governments — 3 edges, significant but wrong-signed

The surprise raises the 2Y GoC yield by **4.79bp** (β = 1.050), the
government's marginal short-term borrowing cost. That reaches the 3-month bill
yield within days, **+2.23bp** (β = 0.465, p < 2e-15) — clean pass-through to
where the shortest debt is priced.

The third edge inverts. Higher bill yields should slow issuance; instead
outstanding bill growth rises **0.78 pp** (β = +0.348, p = 0.010). The likely
explanation is reverse causality: bill supply is set by fiscal requirements,
and heavy issuance pushes yields up rather than yields governing issuance.
**This chain cannot be read as monetary transmission.**

### Central banks — 1 edge, not identified

The chain starts at the 10Y yield, the duration point of the QE-era GoC
portfolio. But the 10Y does not respond significantly once the same-day US 2Y
move is controlled for: **+2.09bp, p = 0.161**.

The intended second edge — 10Y to the CORRA-target spread — shows no
significant horizon at any lag, with or without controls. Layer 3 is moot:
nothing can hang beneath an edge that does not exist. Leave-one-out analysis
shows the layer-1 estimate itself falls to 0.158 when one announcement is
dropped. **This chain is not identified and no transmission claim is made.**

### NFCs — 4 edges, wrong-signed terminal

The surprise raises the 2Y yield by **4.79bp**; corporate floating and
short-term fixed borrowing is priced off the front of the curve. The effective
business rate follows, **+2.83bp** (β = 0.591), as new-lending rates track the
risk-free curve plus a credit spread. Costlier credit reduces net new
borrowing, cutting NFC non-mortgage credit growth by **0.080 pp** (β = −0.028,
p = 0.006).

The fourth edge inverts. Less credit growth should mean more distress; instead
it predicts *less* insolvency growth (β = +7.83, p = 0.0007), so the full
product implies a hawkish surprise **reduces** business insolvencies by
0.62 pp. See the cross-cutting finding.

### Asset managers — broken link

The surprise moves the 10Y yield by **+2.09bp**, where duration exposure
concentrates. Rebalancing should raise bond ETF turnover — but that edge is
not significant once controlled (uncontrolled: β = 0.115, p = 0.034). The
third edge, turnover to the bond-equity return spread, does estimate
(β = −0.00082), but sits past a gap.

`chain_all` refuses to multiply across a missing layer, so **no controlled
product is reported.** The original layer 2 — a bond index return on the 10Y
yield — was rejected as a bond-math identity: R² 0.914, implied duration 6.4
years against ZAG's actual ~7.

### Hedge funds — 2 edges, crosses zero

The surprise raises the 2Y yield by **4.79bp**, where levered rate traders
express path views. Surprises force repositioning, and aggregate CRA open
interest growth rises **0.15 pp** (β = 0.031, p = 0.036) over the following
days.

The intended third edge, open interest to trading volume, shows no significant
horizon. The chain's interval just straddles zero, [−0.004, 0.30] — and only
after a frequency-alignment bug was fixed. Before that fix the same chain
appeared significant on 21 observations rather than 836.

### Banks — 4 edges, dead link in the middle

The surprise raises the 2Y yield by **4.79bp**, the marginal wholesale funding
tenor. The 1-year GIC rate should follow — it does not: **β = −0.062,
p = 0.87**. Administered deposit rates do not track the curve here.

Downstream, both edges are strongly significant. The lending-deposit spread
responds to the GIC rate at β = −0.714 (p < 1e-13), and margin expansion
capitalises into bank equity at β = −0.0035 (p < 1e-5). But a null in the
middle means nothing transmits end to end: **−0.0008 pp, CI
[−0.0098, 0.0083]**.

Four nodes with a dead link is a weaker result than three that all estimate.

### General banking — 3 edges, crosses zero

The surprise raises the 5Y yield by **3.16bp**, the term funding tenor. Banks
compete for term retail funding against the risk-free alternative, and the 5Y
GIC rate follows at **+1.80bp** (β = 0.569, p = 0.036).

The third edge is null: the lending-deposit spread does not respond to the 5Y
GIC rate (β = −0.139, p = 0.86). The chain effect is −0.25 bp, CI
[−3.09, 2.59].

This chain shares `lending_deposit_spread` with the banks chain under a
different parent, so the two are not independent findings.

---

## Cross-cutting finding

Two chains reverse sign at their terminal layer — central governments L3 and
NFCs L4 — and households L4 produced a wrong-signed candidate among those
tested. These are not independent failures.

Insolvency counts and issuance volumes are jointly determined with the credit
conditions that supposedly drive them. Distressed firms borrow less *because*
they are distressed; governments issue more *because* they need funding, which
moves the yield. This is the Khwaja–Mian supply/demand identification problem,
and aggregate monthly series cannot resolve it — that requires firm- or
issuer-level panel data with time fixed effects.

The fourth hop is where the graph's directional assumption breaks down. That
is a result about the design, and the main reason chains stop at three edges.

---

## Running it

```
pip install -r requirements.txt        # graphviz also needs the system binary
python src/data.py                     # build the treatment from MX data
python src/run.py --snapshot snapshot_2026-09-16   # reproduce published numbers
python src/run.py                      # or run live against current data
python src/run.py --no-write           # run without writing results/tables/
python src/graph.py                    # render results/figures/dag.svg
```

Set `FRED_API_KEY` in `.env`; see `.env.example`.

A chain that stops because a node is missing from a snapshot reports
`data_unavailable` naming the node, not a truncated `ok`. That distinction
matters here: five MX-derived nodes are excluded from the published snapshot
for licensing reasons, so the hedge funds chain reports `data_unavailable`
when reproduced from it. Every other chain reproduces exactly.

## Layout

```
config/    graph_spec.yaml (structure, mechanisms, rejections)
           series_map.yaml (node -> source, id, units, range)
src/       run.py (entry point), data, nodes, estimators, effects,
           causal_estimates, diagnostics, graph, utils/
data/      raw/ interim/ processed/ (snapshots)
results/   tables/ figures/dag.svg
docs/      methodology.md, data_manifest.md
```

## Limitations

Full list in `docs/methodology.md`. The four that most constrain what can be
claimed:

- **27 announcements.** One event, the June 2023 hike at +15.5bp, has leverage
  0.43 against a 0.15 threshold. Dropping it moves controlled layer-1 betas by
  18% to 66%.
- **Daily windows.** MX settles at 15:00 ET, so US morning releases sit inside
  the treatment. The us_2y control is a second-best fix.
- **Aggregate data.** Credit supply cannot be separated from demand, so
  quantity edges are equilibrium responses, not supply effects.
- **Terminal-layer direction.** See the cross-cutting finding.

## Future work

Four chains have identified gaps where a better node might exist.

**Asset managers** needs a layer-2 node that is a genuine quantity, not a
revaluation. Anything built from ZAG's *price* inherits duration by
construction; only quantity nodes escape. ETF units outstanding is the right
measure, but no free daily history exists for Canadian ETFs. Premium/discount
to NAV and fund-level creations and redemptions are both behind paid vendors.

**Central banks** needs any layer-2 node that responds to the 10Y. Tested and
rejected: `boc_goc_holdings` (a stock, confounded by QT runoff),
`settlement_balances` (no clean series), `boc_net_income` (quarterly, ~13
observations), `repo_operation_volume` (reverse causality). Untried: the
rolling dispersion of the CORRA-target spread, on the theory that the level
does not move but its volatility might.

**Households layer 4** could use a construction-side terminal rather than a
distress one — CMHC monthly housing starts responds to originations on a
documented lag and avoids the joint-determination problem that killed all four
insolvency candidates.

**General banking and banks** both route through `lending_deposit_spread`, and
both have a null edge feeding it. A different margin measure might separate
them.

Sources and licensing: `docs/data_manifest.md`. MX and SIMA/IFIC data are not
redistributed; extraction code is included.
