# Data Manifest

Snapshot `2026-09-16`. 84 nodes in `config/series_map.yaml`, 76 verified, 75
snapshotted to `data/processed/snapshot_2026-09-16/`.

Node names live in `graph_spec.yaml`; source IDs live in `series_map.yaml`.
The two join on node name and nothing else.

---

## Treatment

| Item               | Value                                                    |
| ------------------ | -------------------------------------------------------- |
| Instrument         | Three-Month CORRA Futures (CRA), Montreal Exchange       |
| Contract           | second quarterly, rolled unconditionally                 |
| Raw input          | `data/raw/mx/` (not redistributed)                     |
| Cleaned            | `data/interim/cleaned_mx_data.csv` (not redistributed) |
| Output             | `data/processed/cra_policy_surprises_alwaysroll.csv`   |
| Units              | basis points, sign-flipped to rate space                 |
| Sample             | 27 events, 2023-04-12 to 2026-07-15                      |
| Announcement dates | `config/boc_announcement_dates.csv`                    |
| FOMC overlap flag  | `data/interim/boc_announcements_with_decisions.csv`    |

---

## Selected nodes

**Layer 1**, shared: `goc_2y` (Valet BD.CDN.2YR.DQ.YLD) for central
governments, NFCs, hedge funds, banks; `goc_5y` (BD.CDN.5YR.DQ.YLD) for
households and general banking; `goc_10y` (BD.CDN.10YR.DQ.YLD) for central
banks and asset managers. All percent, daily.

| Chain               | L | Node                               | Source  | ID                                                                  | Units   | Freq           |
| ------------------- | - | ---------------------------------- | ------- | ------------------------------------------------------------------- | ------- | -------------- |
| households          | 2 | `effective_household_rate`       | Valet   | HHEFFRATE                                                           | percent | weekly         |
| households          | 3 | `mortgage_funds_advanced_growth` | derived | pct_change of V122667729 + V122667735                               | pp      | monthly        |
| general banking     | 2 | `gic_5y`                         | Valet   | V80691341                                                           | percent | weekly         |
| general banking     | 3 | `lending_deposit_spread`         | derived | `effective_household_rate` − `gic_1y`                          | percent | weekly         |
| central governments | 2 | `tbill_3m`                       | Valet   | TB.CDN.90D.MID                                                      | percent | daily          |
| central governments | 3 | `tbill_outstanding_growth`       | derived | pct_change of`data/interim/auc_tbill_3m.csv`, "Outstanding after" | pp      | biweekly       |
| central banks       | 2 | `corra_target_spread`            | derived | AVG.INTWO − V39079                                                 | percent | business daily |
| NFCs                | 2 | `effective_business_rate`        | Valet   | BUSEFFRATE                                                          | percent | weekly         |
| NFCs                | 3 | `nfc_nonmortgage_growth`         | derived | StatCan 36-10-0640, banks + non-banks                               | pp      | monthly        |
| NFCs                | 4 | `business_insolvencies_growth`   | derived | pct_change of OSB monthly                                           | pp      | monthly        |
| asset managers      | 2 | `bond_etf_volume_growth`         | derived | pct_change of ZAG.TO**Volume**                                | pp      | business daily |
| asset managers      | 3 | `zag_xic_return_spread`          | derived | ZAG.TO − XIC.TO return                                             | pp      | business daily |
| hedge funds         | 2 | `cra_oi_growth`                  | derived | pct_change of MX open interest, CRA                                 | pp      | business daily |
| hedge funds         | 3 | `cra_volume_growth`              | derived | pct_change of MX volume, CRA                                        | pp      | business daily |
| banks               | 2 | `gic_1y`                         | Valet   | V80691339                                                           | percent | weekly         |
| banks               | 3 | `lending_deposit_spread`         | derived | shared with general banking                                         | percent | weekly         |
| banks               | 4 | `bank_equity_return`             | derived | pct_change of XFN.TO, W-FRI                                         | pp      | weekly         |

Central banks has no layer 3; candidates are exhausted.

The `Volume` column on `bond_etf_volume_growth` matters. Reverting to the
default `Close` silently turns that node into a price return, reintroducing
the duration identity rejected at this layer.

---

## Controls

| Node                     | Source   | ID                           | Units          | Freq           | Range                    |
| ------------------------ | -------- | ---------------------------- | -------------- | -------------- | ------------------------ |
| `us_2y`                | FRED     | DGS2                         | percent        | daily          | 1976-06-01 → 2026-09-15 |
| `vix`                  | FRED     | VIXCLS                       | index          | business daily | 1990-01-02 → 2026-09-15 |
| `unemployment_rate_ca` | FRED     | LRUNTTTTCAM156S              | percent        | monthly        | 1955-01-01 → 2026-08-01 |
| `sp500_return_pct`     | yfinance | ^GSPC, pct_change            | pp             | business daily | 2019-01-03 → 2026-09-16 |
| `gdp_monthly`          | StatCan  | 36-10-0434, "All industries" | chained 2017 $ | monthly        | 2020-01-01 → 2026-06-01 |
| `gdp_growth`           | derived  | pct_change of`gdp_monthly` | pp             | monthly        | 2020-02-01 → 2026-06-01 |
| `quarter_end_dummy`    | calendar | internal, ±3 business days  | dummy          | business daily | 2019-01-01 → 2026-09-16 |
| `cra_roll_dummy`       | calendar | internal, ±3 business days  | dummy          | business daily | 2019-01-01 → 2026-09-16 |

`gdp_monthly` is chained 2017 dollars, seasonally adjusted at annual rates — a
volume measure. Current dollars were rejected because the inflation component
correlates with the treatment through a channel the control is not meant to
capture.

`cra_roll_dummy` marks the Friday preceding the third Wednesday of March,
June, September and December per MX specifications, or the prior business day.
No Canadian holiday calendar exists in this codebase, so "business day" means
weekday.

---

## Source directories

| Directory                                         | Contents                          | Tracked                    |
| ------------------------------------------------- | --------------------------------- | -------------------------- |
| `data/raw/mx/`                                  | MX downloads                      | no — licensing            |
| `data/raw/boc/`                                 | announcement and decision files   | yes                        |
| `data/raw/boc_valet/`, `fred/`, `yfinance/` | API response caches               | no — regenerated each run |
| `data/raw/statcan/`                             | StatCan table exports             | yes                        |
| `data/raw/osb/`                                 | OSB monthly insolvency statistics | yes                        |
| `data/raw/ific/`                                | SIMA/IFIC releases                | PDFs no, extracted CSV yes |
| `data/interim/`                                 | cleaned MX data, auction files    | partially                  |
| `data/processed/`                               | treatment file, snapshots         | see below                  |

---

## Licensing

**MX exchange data.** TMX terms restrict redistribution. `data/raw/mx/` and
`data/interim/cleaned_mx_data.csv` (41,265 rows) are excluded. The ingestion
code in `src/data.py` is included so the treatment can be rebuilt from a
licensed copy.

**SIMA / IFIC.** Release PDFs state that no reproduction in whole or in part
is permitted without permission. The 40 downloaded PDFs are excluded. What is
committed is `data/raw/ific/ific_bond_etf_monthly.csv` — extracted figures
with a source URL per row — plus the collector in `src/utils/ific.py`.

Releases are not served from a predictable URL: direct paths redirect to a
handler returning nothing for valid and invalid requests alike, never a 404,
so resolution requires crawling `sima-amvi.ca/en/stats/` for opaque attachment
IDs. The March 2024 release is absent from that archive and missing from the
series. Eighteen of forty months were revised between first and second
publication — 2025-04 went from 104 to 75, a 28% revision — so the series
carries measurement error attenuating any estimate built on it toward zero.

**Public sources.** BoC Valet, FRED, StatCan and OSB are redistributable under
their respective open terms.

---

## Reproducibility

The pipeline refetches live by default, so recorded numbers drift as vendors
revise and the sample grows. Three findings in this project became
unreproducible that way before the snapshot mechanism existed.

`build_snapshot()` resolves every node once and writes per-node CSVs to
`data/processed/snapshot_<date>/`. Run
`python src/run.py --snapshot snapshot_2026-09-16` to reproduce the published
figures.

**The snapshot is incomplete.** Five MX-derived nodes are excluded for
licensing reasons — `cra_open_interest_level`, `cra_open_interest_chg`,
`cra_volume`, `cra_oi_growth`, `cra_volume_growth` — so the hedge funds chain
cannot be reproduced from it. Rebuilding that chain requires a licensed MX
copy and `src/data.py`. All other chains reproduce exactly.

**Nodes not resolvable.** Eight remain `status: lookup` — catalogued with a
mechanism but no confirmed source: `boc_goc_holdings`,
`personal_term_deposits`, `repo_volume`, `bond_fund_net_flows`,
`bond_fund_aum`, `public_debt_charges`, `boc_net_income`, `issuance_volume`.
One verified node, `term_deposit_growth`, is absent because it derives from
`personal_term_deposits`.

---

## Security note

A FRED API key was committed during early development. It has been removed
from tracking but remains in git history and must be treated as compromised.
Set `FRED_API_KEY` in a local `.env`; see `.env.example`.
