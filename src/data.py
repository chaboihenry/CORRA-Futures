import csv
import requests
import yaml
from pathlib import Path
import pandas as pd
import numpy as np
import calendar
import time
import datetime
import json

CONFIG = Path(__file__).parent.parent / 'config'
PROCESSED = Path(__file__).parent.parent / 'data' / 'processed'
INTERIM = Path(__file__).parent.parent / 'data' / 'interim'

MX = Path(__file__).parent.parent / 'data' / 'raw' / 'mx'
BOC = Path(__file__).parent.parent / 'data' / 'raw' / 'boc_valet'
BOC_RAW = Path(__file__).parent.parent / 'data' / 'raw' / 'boc'

CODES = 'FGHJKMNQUVXZ'
assert len(CODES) == 12

def ingest_mx(raw_dir=MX):
    """
    Combine BAX, CGZ, COA, CRA datasets from m-x.ca calendar downloads (they cap at 6 month windows)
    Dedupe since there is overlap in csvs for each respective security
    Assertions to determine there are no data quality issues with starting dates as expected, duplications, surviving etc.
    """
    frames = []
    paths = sorted(raw_dir.glob("*/*.csv"))
    assert paths, f"No CSVs found under {raw_dir}"
    for path in paths:
        df = pd.read_csv(path, parse_dates=['Date', 'Expiry Date'], na_values=['0000-00-00'])
        if df.empty:
            continue
        df['symbol'] = path.parent.name
        df['source_file'] = path.name
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)

    counts = combined.groupby(['Symbol', 'Date'])['Settlement Price'].nunique()
    n_bad = (counts > 1).sum()
    assert n_bad == 0, (
    f"{n_bad} (Date, Symbol) groups have conflicting settlement prices "
    f"across overlapping windows; e.g. {list(counts[counts > 1].index[:5])}"
    )

    clean = combined.drop_duplicates(subset=['Date', 'Symbol'], keep='first')

    expected_start = {
        'coa' : '2023-01-18',
        'cra' : '2020-06-09'
    }
    for sym, expected in expected_start.items():
        observed = clean.loc[clean['symbol'] == sym, 'Date'].min()
        assert observed == pd.Timestamp(expected), f'{sym} does not start at expected start date'

    clean.to_csv(INTERIM / 'cleaned_mx_data.csv', index=False)
    return clean

# The "3-month" Canadian bill is issued at a 98-day term, not 91. The bucket
# runs 96-100 days; 85-95 would keep nothing. The neighbouring buckets are
# ~28 days (cash management bills), ~168/182 (6-month, new and reopened) and
# ~350/364 (1-year, new and reopened).
TBILL_3M_TERM_DAYS = (96, 100)

# Column label per the BoC series preamble, keyed by the series id the group
# export actually uses for its column names.
AUC_TBILL_FIELDS = {
    'AUC_TBILL_AUCTION_DATE': 'date',
    'AUC_TBILL_COVERAGE': 'Coverage',
    'AUC_TBILL_OUTSTANDING_AFTER': 'Outstanding after',
    'AUC_TBILL_OUTSTANDING_PRIOR': 'Outstanding prior',
    'AUC_TBILL_AVG_YIELD': 'Avg yield',
    'AUC_TBILL_ALLOTMENT_RATIO': 'Allotment ratio',
    'AUC_TBILL_AMOUNT': 'Amount',
    'AUC_TBILL_TERM_DAYS': 'Term days',
    'AUC_TBILL_MATURITY_DATE': 'Maturity date',
    'AUC_TBILL_ISIN': 'ISIN',
    'AUC_TBILL_KEY': 'Auction key',
}
NUMERIC_AUC_FIELDS = ('Coverage', 'Outstanding after', 'Outstanding prior',
                      'Avg yield', 'Allotment ratio', 'Amount', 'Term days')


def ingest_auc_tbill(path=BOC_RAW / 'auc_tbill.csv',
                     out=INTERIM / 'auc_tbill_3m.csv'):
    """
    Regular 3-month bill auctions from the Valet AUC_TBILL group export.

    The group export is not shaped like a single-series export: it carries a
    metadata preamble (terms, name, description, link, then a SERIES block
    listing 31 ids) before an OBSERVATIONS block, and its column names are the
    series IDS - AUC_TBILL_COVERAGE - not the human labels the BoC page shows.
    This renames them to the labels and writes a flat file the csv source can
    read.

    Two filters make the rows comparable with tbill_3m. Term days selects the
    3-month tenor; the field is published, and it equals maturity minus issue
    date on every row that carries both, so there is nothing to derive. The
    status filter drops the one auction that has only been called for tender
    and has no result yet.

    Coverage runs the whole file. Outstanding after does NOT: the BoC only
    populates it on rows it marks 'Results', which start 2022-02-01, so the
    624 older auctions carry a coverage figure and a blank outstanding. The
    column is kept with that gap rather than truncating coverage to match.
    """
    rows = list(csv.reader(open(path, encoding='utf-8-sig')))
    head = next((i for i, r in enumerate(rows) if r and r[0] == 'OBSERVATIONS'),
                None)
    assert head is not None, f'{path}: no OBSERVATIONS block'
    df = pd.DataFrame(rows[head + 2:], columns=rows[head + 1])
    missing = [c for c in AUC_TBILL_FIELDS if c not in df.columns]
    assert not missing, f'{path}: export is missing {missing}'

    df = df[df['AUC_TBILL_AUCTION_DATE'] != ''].copy()
    df['term'] = pd.to_numeric(df['AUC_TBILL_TERM_DAYS'], errors='coerce')
    lo, hi = TBILL_3M_TERM_DAYS
    keep = df[df['term'].between(lo, hi)
              & (df['AUC_TBILL_STATUS'] != 'Preliminary CFT')].copy()
    assert not keep.empty, f'{path}: no {lo}-{hi} day auctions with results'

    out_df = keep[list(AUC_TBILL_FIELDS)].rename(columns=AUC_TBILL_FIELDS)
    out_df['date'] = pd.to_datetime(out_df['date'])
    out_df['Maturity date'] = pd.to_datetime(out_df['Maturity date'])
    for c in NUMERIC_AUC_FIELDS:
        out_df[c] = pd.to_numeric(out_df[c], errors='coerce')
    out_df = out_df.sort_values('date').reset_index(drop=True)

    assert out_df['date'].is_unique, 'more than one 3-month auction on a date'
    assert out_df['Coverage'].notna().all(), 'Coverage has gaps after filtering'
    # outstanding starts late, but must not be holed once it starts
    oa = out_df['Outstanding after']
    first = oa.first_valid_index()
    assert oa.loc[first:].notna().all(), 'Outstanding after has interior gaps'
    out_df.to_csv(out, index=False)
    return out_df


def select_coa_contract(announce_date, root='COA', threshold=7): 
    """
    Which COA contract, and what scale factor. Returns (symbol, scale).
    Kuttner (2001): surprise = Δf * m / (m - d), where Δf is the observed
    settlement change, m = days in month, d = day of month of announcement.
    Rolls to the next contract when < threshold days remain (scale = 1.0).
    """
    y, m, d = announce_date.year, announce_date.month, announce_date.day
    m_days = calendar.monthrange(y, m)[1]
    scale = 1.0
    if m_days - d <= threshold:
        if m == 12:
            m = 1
            y += 1
        else:
            m += 1
    else:
        scale = m_days / (m_days - d)
    code = CODES[m-1]
    symbol = f'{root}{code}{y % 100:02d}'
    return symbol, scale

def select_cra_contract(df, announce_date, root='CRA', threshold=21, always_roll=False):
    """
    Which CRA contract, and what scale factor. Returns (symbol, scale).
    CRA settles over a quarter on IMM dates, so the contract is looked up
    from the data (earliest Expiry Date after announce_date) rather than
    constructed. Scale applies Kuttner to the quarterly reference window.
    always_roll takes the next contract unconditionally: its window lies
    entirely ahead of the announcement, so no correction is needed.
    """
    live = df[(df['Date'] == announce_date) & (df['Expiry Date'] > announce_date)]
    if live.empty:
        return None, None
    row = live.sort_values(['Expiry Date']).iloc[0]
    symbol = row['Symbol']
    expiry = row['Expiry Date']
    all_expiries = pd.DatetimeIndex(sorted(df['Expiry Date'].dropna().unique()))
    i = all_expiries.searchsorted(expiry)
    if always_roll or (expiry - announce_date).days <= threshold:
        if i + 1 >= len(all_expiries):
            return None, None
        nxt = live[live['Expiry Date'] == all_expiries[i + 1]]
        if nxt.empty:
            return None, None
        return nxt.iloc[0]['Symbol'], 1.0
    if i == 0:
        return symbol, None
    start = all_expiries[i - 1]
    scale = (expiry - start).days / (expiry - announce_date).days
    return symbol, scale

# helper func
def lookup_field(df, date, sym, col):
    """Value of col for sym on date, or None if the contract wasn't listed."""
    row_t = df[(df['Date'] == date) & (df['Symbol'] == sym)]
    if row_t.empty:
        return None
    return row_t.iloc[0][col]

def volume_screen(mx, announcements, root='COA', always_roll=False):
    """
    Does this selected contract trade at t and t-1? One row per announcement. 
    """
    sub = mx[mx['symbol'] == root.lower()]
    dates = pd.DatetimeIndex(np.sort(sub['Date'].unique()))
    rows = []
    for ann_date in announcements['date']:
        if root.upper() == 'COA':
            sym, scale = select_coa_contract(ann_date, root=root)
        elif root.upper() == 'CRA':
            sym, scale = select_cra_contract(sub, ann_date, always_roll=always_roll)
        else:
            raise ValueError(f'Unsupported root: {root}')
        rec = {
            'date' : ann_date, 'symbol': sym, 'scale' : scale,
            'rolled' : scale == 1.0, 'prev_date' : None,
            'vol_t' : None, 'vol_prev' : None, 'status' : None
        }
        if sym is None:
            rec['status'] = 'no_live_contract'
        else:
            i = dates.searchsorted(ann_date)
            if i == len(dates) or i == 0:
                rec['status'] = 'out_of_range'
            elif dates[i] != ann_date:
                rec['status'] = 'no_data_on_date'
            else:
                prev = dates[i - 1]
                rec['prev_date'] = prev
                vol_t = lookup_field(sub, ann_date, sym, 'Volume')
                vol_prev = lookup_field(sub, prev, sym, 'Volume')
                rec['vol_t'], rec['vol_prev'] = vol_t, vol_prev
                if vol_t is None or vol_prev is None:
                    rec['status'] = 'not_listed'
                elif vol_t == 0 or vol_prev == 0:
                    rec['status'] = 'no_volume'
                else:
                    rec['status'] = 'ok'
        rows.append(rec)

    return pd.DataFrame(rows)

def compute_surprise(mx, screen, root='CRA'):
    """
    Kuttner policy surprise in bp, rate space (cut = negative)
    Returns screen with settle_t, settle_prev, delta_f, surprise added. 
    """
    out = []
    sub = mx[mx['symbol'] == root.lower()]
    for _, r in screen.iterrows():
        if r['status'] != 'ok':
            out.append({'settle_t' : None, 'settle_prev' : None,
                        'delta_f' : None, 'surprise' : None})
            continue
        sym = r['symbol']
        settle_t = lookup_field(sub, r['date'], sym, 'Settlement Price')
        settle_prev = lookup_field(sub, r['prev_date'], sym, 'Settlement Price')
        delta_f = settle_t - settle_prev
        surprise = -delta_f * r['scale'] * 100
        out.append({'settle_t' : settle_t, 'settle_prev' : settle_prev,
                    'delta_f' : delta_f, 'surprise' : surprise})

    return pd.concat([screen.reset_index(drop=True),
                    pd.DataFrame(out)], axis = 1)

def load_series_map():
    with open(CONFIG / 'series_map.yaml', 'r') as f:
        series_map = yaml.safe_load(f)
    return series_map

def fetch_valet(series_ids, start=None, end=None):
    """Fetch Valet observations. Returns a DataFrame indexed by date."""
    names = ','.join(series_ids)
    url = f'https://www.bankofcanada.ca/valet/observations/{names}/json'
    params = {}
    if start:
        params['start_date'] = start
    if end: 
        params['end_date'] = end
    r = requests.get(url, timeout=30, params=params)
    r.raise_for_status()
    payload = r.json()
    stamp = datetime.date.today().isoformat()
    safe = names.replace(',', '_').replace('.', '-')
    fname = f'valet_{safe}_{stamp}.json'
    with open(BOC / fname, 'w') as f:
        json.dump(payload, f, indent=2)
    obs = payload['observations']
    rows = []
    for o in obs:
        rec = {'date' : o['d']}
        for sid in series_ids:
            val = o.get(sid)
            if val and val['v'] != '':
                rec[sid] = float(val['v'])
            else:
                rec[sid] = None
        rows.append(rec)
    time.sleep(1)
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    return df

def add_decision_bps(announcements, boc):
    """Fill decision_bps from the target rate series. Returns a new frame"""
    ann = announcements.drop(columns=['decision_bps'], errors='ignore')
    dates = pd.DatetimeIndex(boc['date'])
    out = []
    for _, r in ann.iterrows():
        if r['date'].year < 2021:
            out.append({'decision_bps' : None, 'decision_note' : 'pre-2021 regime not implemented'})
            continue
        i = dates.searchsorted(r['date'])
        if i >= len(dates) or dates[i] != r['date']:
            out.append({'decision_bps' : None, 'decision_note' : 'not in series'})
            continue
        if i + 1 >= len(dates): 
            out.append({'decision_bps' : None, 'decision_note' : 'past available data'})
            continue
        before = boc['V39079'].iloc[i]
        after = boc['V39079'].iloc[i + 1]
        out.append({'decision_bps' : round((after-before) * 100, 2), 'decision_note' : None})
    return pd.concat([ann.reset_index(drop=True), 
                    pd.DataFrame(out)], axis=1)

def flag_fomc_overlap(announcements, fomc_dates):
    """Flag announcements sharing a date with an FOMC statement"""
    ann = announcements.copy()
    ann['fomc_same_day'] = ann['date'].isin(fomc_dates)
    return ann

def main():
    mx = ingest_mx()
    ann = pd.read_csv(CONFIG / 'boc_announcement_dates.csv', parse_dates=['date'])

    screen_a = volume_screen(mx, ann, root='CRA')
    screen_b = volume_screen(mx, ann, root='CRA', always_roll=True)
    print(screen_a['status'].value_counts())
    print(screen_b['status'].value_counts())

    surprises_a = compute_surprise(mx, screen_a)
    surprises_a.to_csv(PROCESSED / 'cra_policy_surprises.csv', index=False)
    surprises_b = compute_surprise(mx, screen_b)
    surprises_b.to_csv(PROCESSED / 'cra_policy_surprises_alwaysroll.csv', index=False)

    smap = load_series_map()
    ids = [v['valet_id'] for v in smap.values()
            if v.get('role') in ('underlying', 'policy')]
    assert len(ids) == 2, f'expected 2 series, got {len(ids)}: {ids}'
    boc = fetch_valet(ids)
    boc.to_csv(INTERIM / 'boc_rates_daily.csv', index=False)

    fomc = pd.read_csv(CONFIG / 'fomc_dates.csv', parse_dates=['date'])
    ann_full = add_decision_bps(ann, boc)
    ann_full = flag_fomc_overlap(ann_full, set(fomc['date']))
    ann_full.to_csv(INTERIM / 'boc_announcements_with_decisions.csv', index=False)

if __name__ == '__main__':
    main()






