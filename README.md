# CORRA Futures DAG

How far does a Bank of Canada rate surprise travel, and how much of it reaches each sector?

---

## The approach

Markets guess the Bank's decision in advance, so only the part they got wrong is news. CORRA futures price that guess, and the price move across an announcement measures the surprise.

That surprise is followed through the economy one step at a time — into bond yields, into the rates banks charge, into how much people borrow, into who fails to repay — and the steps are multiplied together.

```
surprise → government yield → bank rate → borrowing → outcome
```

Each stakeholder gets its own chain. Three candidates are named per layer; one is selected and the others are rejected with evidence.

**The graph is an input, not an output.** Structure comes from published economics; the data supplies magnitudes. 27 events cannot support learning the structure itself.

---

## Results

| Stakeholder         | Layers | Product  | 1-SD effect        | 95% CI               |
| ------------------- | ------ | -------- | ------------------ | -------------------- |
| Households          | 4      | 0.135    | **+0.61 pp** | [0.18, 1.05]         |
| Banks               | 4      | −0.0003 | −0.001            | [−0.013, 0.011]     |
| NFCs                | 3      | −0.023  | −0.106            | [−0.190, −0.023]   |
| General banking     | 3      | −0.083  | −0.377            | [−4.63, 3.88]       |
| Asset managers      | 2      | −0.052  | −0.239            | [−0.373, −0.104]   |
| Central banks       | 2      | −0.089  | −0.408            | [−0.721, −0.095]   |
| Hedge funds         | 2      | 0.043    | +0.198             | [0.006, 0.390]       |
| Central governments | 4      | —       | —                 | units do not compose |

A one-SD surprise is 4.56 bp. Four chains have intervals excluding zero.

**Households, the complete chain:**

| Edge                                    | β      | SE    |
| --------------------------------------- | ------- | ----- |
| surprise → 5Y GoC yield                | 1.071   | 0.189 |
| 5Y yield → household lending rate      | 0.576   | 0.082 |
| lending rate → origination growth      | −0.742 | 0.146 |
| origination growth → insolvency growth | −0.296 | 0.059 |

---

## What the nulls say

Roughly half this project is edges that could not be estimated, each with arithmetic:

- **One-month CORRA futures** trade on 5 of 29 announcement days
- **Posted mortgage rates** do not respond at any lag. Almost nobody pays them
- **Credit stocks** grow 0.4% a month with SD 0.2 pp — too smooth. Flows swing tens of percent and work
- **Quarterly series** give ~13 observations
- **Four nodes were rejected as construction artifacts** — each contained the thing it was regressed on

These are constraints on what any study of Canadian monthly data can do.

---

## Layout

```
CLAUDE.md    conventions, read by Claude Code each session
config/      hand-curated; code reads, never writes
  graph_spec.yaml    chains, candidates, mechanisms, controls, findings
  series_map.yaml    node -> source, id, frequency, units, verified range
  boc_announcement_dates.csv, fomc_dates.csv, mx_holidays.csv
src/
  data.py              build the treatment from MX downloads
  nodes.py             fetch any node from its catalogue entry
  estimators.py        three regressions, one per edge type
  effects.py           screen edges, multiply chains, propagate CIs
  causal_estimates.py  walk the spec, screen, estimate, audit controls
  diagnostics.py       six investigations, print-only, CLI subcommands
  graph.py             render the DAG
data/        raw (immutable) / interim / processed
results/     tables / figures
docs/        methodology.md, data_manifest.md
```

## Running it

```
python src/data.py              # build the treatment
python src/causal_estimates.py  # screen, estimate, chain, audit
python src/graph.py             # render
```

Diagnostics run individually:

```
python src/diagnostics.py mechanism_test --parent X --child Y
```

---

## Remaining

- [ ] Parent-side double-difference on four `local_projection` edges
- [ ] Drop two unjustified controls flagged by the audit
- [ ] Asset managers layers 3–4
- [ ] `graph.py` and the figure
- [ ] Final numbers into the docs

---

`docs/methodology.md` — the estimation procedure step by step
`docs/data_manifest.md` — every source and the traps in each
`config/graph_spec.yaml` — every candidate considered, with reasons

No paid data. Everything reproducible from public sources.
