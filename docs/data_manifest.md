# Data Manifest

Everything is free and public. Nothing in `data/raw/` is written by code.

| Source                        | What                                                              | How                |
| ----------------------------- | ----------------------------------------------------------------- | ------------------ |
| MX historical daily summaries | COA, CRA, BAX, CGZ settlements                                    | Manual, 52 files   |
| BoC Valet API                 | CORRA, policy rate, GoC yields, effective rates, mortgage volumes | REST, free, no key |
| BoC Valet group`AUC_TBILL`  | Treasury bill auction results                                     | Manual CSV         |
| BoC press releases            | Announcement dates, times, MPR flags                              | Manual, 58 rows    |
| Federal Reserve               | FOMC statement dates                                              | Manual, 48 rows    |
| StatCan 36-10-0639-01         | Household credit aggregates                                       | Manual CSV         |
| StatCan 36-10-0640-01         | NFC credit aggregates                                             | Manual CSV         |
| StatCan 10-10-0144-01         | T-bill auction yields, weekly                                     | Manual CSV         |
| OSB                           | Monthly insolvency filings                                        | Manual XLSX → CSV |
| FRED                          | US 2Y and 10Y Treasury yields                                     | REST, free key     |
| yfinance                      | Bond and equity ETFs                                              | Python package     |

---

## Traps

### MX

**The download silently truncates.** Ask for more than ~180 days and you get the first 180 with no error. Always compare what you asked for against what the file contains — this cost a month of data before it was caught.

**Settlement prices are published for contracts that never traded.** That number is the exchange's mark. Every observation must have `Volume > 0` on both the announcement day and the day before.

**Open interest is lagged one day.** Stated by MX, not adjusted for.

**Summing open interest then differencing** makes every quarterly expiry look like a 250,000-contract position change — 12 dates carried 75% of the series variance. Differencing *within* contract then summing removes it. SD falls 20,399 → 11,252.

### Valet

**Over half the catalogue is chart extracts.** Prefixes `SAN_`, `MPR_`, `FSR_`, `WM_`, `SPEECH_` are data behind figures in publications, frozen at press time.

**Several series have a lookalike whose *label* is another series' ID.** `CBC20210` is labelled "V39079"; `V121764` is labelled "V80691335". Use the one whose label is descriptive text.

**The credit aggregates are dead.** `RMCRED_*`, `BUSCRED_*`, `HOUSECRED_*` and the SA `V12xxxx` stocks all stop in September 2020 — the data moved to StatCan. A good start date does not mean a series is live, hence `verified_end`.

**`V80691339` (1-year GIC) has three implausible consecutive moves in mid-2023** — +142, −120, +185 bp. Two thirds of the series' weekly variance sits in those three points.

**Group exports are shaped differently from series exports.** `AUC_TBILL` has a 46-row metadata preamble above an `OBSERVATIONS` marker, and the columns are series IDs (`AUC_TBILL_COVERAGE`) rather than labels.

### StatCan

Exports are **transposed** — dates across the header row, series down the side. Values are strings with thousands separators.

**The header row is not always the same.** Monthly credit tables put it at row 10; 10-10-0144 puts it at row 9. Detect it by scanning for the first row whose second cell parses as a date.

**`..` means missing**, not zero. In 10-10-0144, 159 of 348 weeks are `..` because the auction is biweekly while the table is stamped weekly — 189 usable observations.

**Labels collide on prefixes.** `Treasury bill auction - average yields` matches both the 3-month and 6-month rows. Use the full label.

In the household table, "Residential mortgages" appears twice with identical values; the reader takes the first and asserts they agree.

### OSB

One XLSX covers 1987 to present. Month labels are **bilingual and mixed-type** — Excel converted some to datetimes and left others as strings like "Feb/fév 2020". Both forms need handling or a third of the months vanish silently.

Filings are **not seasonally adjusted** and peak in March, so any edge using them needs month dummies.

### BoC auction results

**3-month bills are issued at a 98-day term, not 91.** An 85–95 day filter keeps zero auctions. Use 96–100, which also excludes cash management bills automatically.

**Do not filter on `Status == 'Results'`.** BoC only populates that field from 2022-02; the 624 older rows carry valid coverage with a blank status. Filtering truncates 745 auctions to 121.

`Outstanding after` is only populated on the Results rows, so coverage runs 1998→2026 while outstanding starts 2022-02.

### yfinance

**Use `auto_adjust=True`.** Over 2019–2026 ZAG.TO returns +11.78% adjusted against −12.85% unadjusted — the coupon is more than the entire return, and without adjustment the node would have the wrong sign.

The index comes back timezone-aware; strip it or it will not join with Valet.

---

## Two systematic bugs found by inspection

**Averaging daily returns to monthly** shrinks the SD by √21 and makes a good series look unusable. A monthly return is the *sum* of daily returns.

**Scaling percent to bp but leaving pp alone** made the variance screen read pp children as 100× smoother than they are. Four edges were false negatives — all four estimate significantly.

Neither was caught by an automated check.

---

## Config

Hand-curated. Code reads, never writes.

| File                           | Contents                                                           |
| ------------------------------ | ------------------------------------------------------------------ |
| `series_map.yaml`            | Every node: source, ID, frequency, units, verified range           |
| `graph_spec.yaml`            | Every chain: candidates, mechanisms, controls, estimates, findings |
| `boc_announcement_dates.csv` | 58 rows. Announcement time moves 10:00 → 09:45 in 2024            |
| `fomc_dates.csv`             | 48 rows, statement dates only                                      |
| `mx_holidays.csv`            | 92 rows, including early-close days that settle at 13:00           |

---

## Outputs

| File                                              | Contents                                        |
| ------------------------------------------------- | ----------------------------------------------- |
| `interim/cleaned_mx_data.csv`                   | All futures, deduplicated, 41,265 rows          |
| `interim/auc_tbill_3m.csv`                      | Auction results reshaped, human labels          |
| `processed/cra_policy_surprises_alwaysroll.csv` | **The treatment.** 36 passing, 27 primary |
| `results/tables/variance_screen.csv`            | Which edges can be estimated                    |
| `results/tables/candidate_screen.csv`           | Every candidate, not just selected              |
| `results/tables/edges.csv`                      | Every edge: β, SE, p, n                        |
| `results/tables/chains.csv`                     | Total effect per stakeholder with CI            |
| `results/tables/control_audit.csv`              | Declared vs derived adjustment sets             |
