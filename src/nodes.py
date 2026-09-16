import yaml, pandas as pd
import yfinance as yf
from pathlib import Path
from data import fetch_valet
from data import load_series_map
import csv
import requests
import json
import datetime
import os
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
CONFIG = Path(__file__).parent.parent / 'config'
FRED_RAW = Path(__file__).parent.parent / 'data' / 'raw' / 'fred'
STATCAN_RAW = Path(__file__).parent.parent / 'data' / 'raw' / 'statcan'
CSV_RAW = Path(__file__).parent.parent / 'data' / 'raw'
YF_RAW = Path(__file__).parent.parent / 'data' / 'raw' / 'yfinance'

# StatCan missing-data symbols; '..' is not available, '...' not applicable,
# 'x' suppressed for confidentiality, 'F' too unreliable to publish.
STATCAN_MISSING = {'..', '...', 'x', 'F', ''}
STATCAN_DATE_FORMATS = ('%B %Y', '%B %d, %Y')
STATCAN_HEADER_SEEN = set()

REQUIRED_KEY = {
    'valet' : 'valet_id',
    'fred' : 'fred_id',
    'derived' : 'inputs',
    'statcan_csv' : 'file',
    'csv' : 'file',
    'yfinance' : 'ticker',
    'internal' : 'file',
    'calendar' : 'rule'
}

# frequency label (as used in series_map) -> pandas offset alias, for building
# a complete gap-free DatetimeIndex before a positional shift
FREQ_ALIAS = {'monthly': 'MS'}

# CRA (3-month CORRA futures) settles on the IMM quarterly cycle
CRA_EXPIRY_MONTHS = (3, 6, 9, 12)

def resolve(name, smap):
    """Node spec from series_map. Raises if unknown or unresolved."""
    assert name in smap, f'Unknown node: {name}'
    spec = smap[name]
    if spec.get('status') in ('lookup', 'blocked_on_lookup'):
        raise NotImplementedError(f'{name}: {spec["status"]}') 
    source = spec.get('source')
    key = REQUIRED_KEY.get(source)
    assert source in REQUIRED_KEY, f'{name}: unknown source {source!r}, expected one of {list(REQUIRED_KEY)}'
    assert key in spec, f'{name}: source {source!r} requires {key!r}, not found in spec'
    return spec

def fetch_valet_node(spec):
    """One Valet series as a Series indexed by date."""
    df = fetch_valet([spec['valet_id']])
    out = df.set_index('date')[spec['valet_id']]
    assert not out.empty, f'no observation for {spec["valet_id"]}'
    return out

def fetch_yfinance_node(spec):
    """One yfinance ticker as a total-return close Series indexed by date."""
    ticker = spec['ticker']
    frame = yf.Ticker(ticker).history(start='2019-01-01', auto_adjust=True)
    assert not frame.empty, f'no observation for {ticker}'
    YF_RAW.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    safe = ticker.replace('^', '').replace('.', '-')
    frame.to_csv(YF_RAW / f'{safe}_{stamp}.csv')
    column = spec.get('column', 'Close')
    assert column in frame.columns, (
        f'{ticker}: no column {column!r}, found {list(frame.columns)}')
    out = frame[column]
    out.index = out.index.tz_localize(None)
    out.index.name = 'date'
    assert not out.empty, f'no {column} for {ticker}'
    return out

def fetch_fred_node(spec):
    """One FRED series as a Series indexed by date."""
    names = spec['fred_id']
    url = 'https://api.stlouisfed.org/fred/series/observations'
    params = {
        'series_id' : names,
        'api_key' : os.getenv('FRED_API_KEY'),
        'file_type' : 'json'
    }
    r = requests.get(url, timeout=30, params=params)
    r.raise_for_status()
    payload = r.json()
    stamp = datetime.date.today().isoformat()
    safe = names.replace(',', '_').replace('.', '-')
    fname = f'fred_{safe}_{stamp}.json'
    with open(FRED_RAW / fname, 'w') as f:
        json.dump(payload, f, indent=2)
    obs = payload['observations']
    rows = []
    for o in obs:
        v = o['value']
        rows.append({
            'date' : o['date'], 
            'value' : float(v) if v != '.' else None
        })
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    out = df.set_index('date')['value']
    assert not out.empty, f'no observation for {names}'
    return out

def statcan_header_row(rows, spec, fname):
    """
    Index of the row carrying the dates: the first row whose second cell parses
    as a date. spec['header_row'] overrides the scan.
    """
    if 'header_row' in spec:
        found = spec['header_row']
        how = 'header_row override'
        assert len(rows[found]) > 1, f'{fname}: row {found} has no date cells'
        assert pd.notna(pd.to_datetime(rows[found][1], errors='coerce')), (
            f'{fname}: row {found} is not a date header, second cell is '
            f'{rows[found][1]!r}. The export layout has changed')
    else:
        found, how = None, 'detected'
        for i, r in enumerate(rows):
            if len(r) > 1 and r[1].strip() and pd.notna(
                    pd.to_datetime(r[1], errors='coerce')):
                found = i
                break
        assert found is not None, (
            f'{fname}: no row has a date in its second cell')
    if fname not in STATCAN_HEADER_SEEN:
        STATCAN_HEADER_SEEN.add(fname)
        print(f'{fname}: dates on row {found} ({how})')
    return found


def statcan_dates(cells, fname):
    """
    The header's date cells as a DatetimeIndex. Monthly exports stamp
    'January 2020', weekly ones 'January 14, 2020'.
    """
    for fmt in STATCAN_DATE_FORMATS:
        try:
            return pd.to_datetime(cells, format=fmt)
        except ValueError:
            continue
    raise AssertionError(
        f'{fname}: date header matches no known format: {cells[0]!r}')


def statcan_value(cell):
    """One cell as a float, or None for a StatCan missing-data symbol."""
    c = cell.strip()
    if c in STATCAN_MISSING:
        return None
    return float(c.replace(',', ''))


def fetch_statcan_csv_node(spec):
    """One series from a StatCan CSV export, as a Series indexed by date."""
    fname = spec['file']
    rows = list(csv.reader(open(STATCAN_RAW / fname, encoding='utf-8-sig')))
    head = statcan_header_row(rows, spec, fname)
    dates = rows[head][1:]
    hits = [r for r in rows if r and r[0].startswith(spec['series'])]
    assert hits, f'{fname}: no row matching {spec["series"]!r}'
    if len(hits) > 1:
        # labels share prefixes - 'Treasury bill auction - average yields'
        # opens both the 3 month and the 6 month row
        firsts = {tuple(h[1:]) for h in hits}
        assert len(firsts) == 1, (
            f'{fname}: {len(hits)} rows match {spec["series"]!r} with differing '
            f'values: {[h[0] for h in hits]}. Extend the series label until it '
            f'picks out one of them')
    cells = hits[0][1:]
    assert len(cells) == len(dates), f'{len(cells)} values vs {len(dates)} dates'
    values = [statcan_value(c) for c in cells]
    out = pd.Series(values, index=statcan_dates(dates, fname), dtype='float64')
    out.index.name = 'date'
    if spec.get('drop_missing'):
        # a series published on a finer grid than it is observed on carries
        # empty stamps between observations. Kept, they annihilate every first
        # difference, because each one sits next to a gap.
        out = out.dropna()
        assert not out.empty, f'{fname}: {spec["series"]!r} is all missing'
    return out

def fetch_csv_node(spec):
    """One column from a plain CSV with a date column, as a Series indexed by date."""
    date_column = spec.get('date_column', 'date')
    # paths are relative to data/raw, which is immutable; a path written out
    # from the project root reaches a file the pipeline itself produced
    path = (ROOT / spec['file'] if spec['file'].startswith('data/')
            else CSV_RAW / spec['file'])
    df = pd.read_csv(path, parse_dates=[date_column])
    assert spec['series'] in df.columns, f'{spec["file"]}: no column {spec["series"]!r}, found {list(df.columns)}'
    out = df.set_index(date_column)[spec['series']]
    assert not out.empty, f'{spec["file"]}: no observation for {spec["series"]!r}'
    return out

def fetch_internal_node(spec):
    """
    One aggregated MX series as a Series indexed by date.

    MX lags Open Interest by one day; no adjustment is made.

    diff_then_sum differences within each contract before summing, so a
    contract leaving the board nets out. sum_then_diff does the reverse and is
    kept only for comparison: it turns every IMM expiry into a ~250k drop, and
    12 such dates carry 75% of the variance of the CRA series.
    """
    df = pd.read_csv(ROOT / spec['file'], parse_dates=['Date'])
    rows = df[df['symbol'].str.lower() == spec['filter_symbol']]
    assert not rows.empty, f'no rows with symbol {spec["filter_symbol"]!r}'
    assert spec['column'] in rows.columns, f'{spec["column"]!r} not in {spec["file"]}, found {list(rows.columns)}'
    if spec['op'] == 'diff_then_sum':
        rows = rows.sort_values(['Symbol', 'Date'])
        within = rows.groupby('Symbol')[spec['column']].diff()
        out = within.groupby(rows['Date']).sum().sort_index()
    else:
        out = rows.groupby('Date')[spec['column']].sum().sort_index()
        if spec['op'] == 'sum_then_diff':
            out = out.diff()
        elif spec['op'] != 'sum':
            raise NotImplementedError(f'op {spec["op"]!r} not implemented')
    out = out.dropna()
    assert not out.empty, f'no observation for {spec["filter_symbol"]} {spec["column"]!r}'
    out.index.name = 'date'
    return out

def reindex_complete(series, frequency, name):
    """series on a complete DatetimeIndex at the given frequency; gaps as NaN."""
    alias = FREQ_ALIAS.get(frequency)
    assert alias, f'{name}: no complete-index rule for frequency {frequency!r}'
    full = pd.date_range(series.index.min(), series.index.max(), freq=alias)
    return series.reindex(full)

def cra_roll_dates(probe):
    """
    Last trading day of each CRA contract in probe's span: the Friday before
    the third Wednesday of March/June/September/December, per MX contract
    specs (m-x.ca/en/markets/interest-rate-derivatives/cra), or the preceding
    business day if that Friday is not one.
    """
    expiries = []
    for year in sorted(set(probe.year)):
        for month in CRA_EXPIRY_MONTHS:
            wednesdays = pd.date_range(f'{year}-{month}-01', periods=3, freq='W-WED')
            friday = wednesdays[2] - pd.Timedelta(days=5)
            while friday.dayofweek >= 5:
                friday -= pd.Timedelta(days=1)
            expiries.append(friday)
    return pd.DatetimeIndex(sorted(expiries))

def fetch_calendar_node(spec):
    """Calendar-derived indicator over the sample. No upstream nodes."""
    rule = spec['rule']
    if rule not in ('quarter_end', 'contract_roll'):
        raise NotImplementedError(f'calendar rule {rule!r} not implemented')
    window = spec.get('window', 3)
    end = pd.Timestamp(datetime.date.today())
    idx = pd.bdate_range(spec.get('start', '2019-01-01'), end)
    # probe past today so a boundary still ahead of the sample end anchors
    # the business days leading up to it
    probe = pd.bdate_range(spec.get('start', '2019-01-01'), end + pd.Timedelta(days=120))
    flags = pd.Series(0.0, index=probe)
    n = len(probe)
    if rule == 'quarter_end':
        quarters = [(d.year, d.quarter) for d in probe]
        for i in range(n - 1):
            if quarters[i + 1] != quarters[i]:
                flags.iloc[max(0, i - window):min(n, i + window + 1)] = 1.0
    else:
        for pos in probe.get_indexer(cra_roll_dates(probe)):
            if pos == -1:
                continue
            flags.iloc[max(0, pos - window):min(n, pos + window + 1)] = 1.0
    out = flags.reindex(idx)
    assert not out.empty, f'calendar rule {rule!r} produced no dates'
    assert (out == 1.0).any(), f'calendar rule {rule!r} flagged no dates'
    out.index.name = 'date'
    return out

def fetch_node(name, smap):
    """Resolve a node name to pd.Series indexed by date."""
    spec = resolve(name, smap)
    if spec['source'] == 'valet':
        out = fetch_valet_node(spec)
    elif spec['source'] == 'fred':
        out = fetch_fred_node(spec)
    elif spec['source'] == 'derived':
        out = fetch_derived_node(spec, smap)
    elif spec['source'] == 'statcan_csv':
        out = fetch_statcan_csv_node(spec)
    elif spec['source'] == 'csv':
        out = fetch_csv_node(spec)
    elif spec['source'] == 'yfinance':
        out = fetch_yfinance_node(spec)
    elif spec['source'] == 'internal':
        out = fetch_internal_node(spec)
    elif spec['source'] == 'calendar':
        out = fetch_calendar_node(spec)
    else:
        raise NotImplementedError(f'{name}: source {spec["source"]!r} not implemented')
    out.name = name
    return out

def fetch_derived_node(spec, smap):
    """Compute a node from its inputs. Calls fetch_node recursively."""
    inputs = [fetch_node(s, smap) for s in spec['inputs']]
    op = spec['op']
    if op in ('add', 'subtract'):
        assert len(inputs) == 2, f'{op} needs 2 inputs, got {len(inputs)}'
        a, b = inputs
        # the two inputs may be stamped on different weekdays, so the natural
        # intersection can be empty; carry the second onto the first's index
        b = b.reindex(a.index, method='ffill')
        overlap = a.index.intersection(b.index)
        assert len(overlap) > 0, f'inputs {spec["inputs"]} share no dates'
        out = a + b if op == 'add' else a - b
    elif op == 'pct_change':
        assert len(inputs) == 1, f'{op} needs 1 input, got {len(inputs)}'
        out = inputs[0].pct_change() * 100
        # a zero in the level gives an infinite growth rate, which dropna does
        # not remove and OLS cannot take
        out = out.replace([float('inf'), float('-inf')], float('nan'))
    elif op == 'diff':
        assert len(inputs) == 1, f'{op} needs 1 input, got {len(inputs)}'
        out = inputs[0].diff()
    elif op == 'divide':
        assert len(inputs) == 2, f'{op} needs 2 inputs, got {len(inputs)}'
        a, b = inputs
        a_name, b_name = spec['inputs']
        lag_b = spec.get('lag_b', 0)
        scale = spec.get('scale', 1)
        if lag_b:
            # shifting by POSITION silently pairs a with whatever row precedes
            # it in the file, even if that row is not the prior period - a
            # missing month closes the gap instead of producing one. Reindex
            # onto a complete grid first so a hole in the source stays a hole
            # after the shift.
            b = reindex_complete(b, spec['frequency'], b_name).shift(lag_b)
        overlap = a.index.intersection(b.index)
        assert len(overlap) > 0, f'{a_name} and {b_name} share no dates after alignment'
        a, b = a.reindex(overlap), b.reindex(overlap)
        # a zero denominator would otherwise divide to +/-inf, not NaN
        out = (a / b.where(b != 0)) * scale
    else:
        raise NotImplementedError(f'op {op!r} not implemented')
    out = out.dropna()
    assert not out.empty, f'derived node empty: {spec["inputs"]}'
    return out


def main():
    smap = load_series_map()

    # goc5 = fetch_node('goc_5y', smap)
    # print(goc5.name, goc5.dtype, len(goc5), goc5.index.min().date(), goc5.index.max().date())
    # print(goc5.tail())

    # mtg = fetch_node('mortgage_5y_posted', smap)
    # print(mtg.name, len(mtg), mtg.index.min().date(), mtg.index.max().date())
    # print(mtg.index.to_series().diff().value_counts().head())
    # print(mtg.index.day_name().value_counts())

    # us2 = fetch_node('us_2y', smap)
    # print(us2.name, us2.dtype, len(us2), us2.index.min().date(), us2.index.max().date())
    # print(us2.isna().sum(), 'missing')

    # ins = fetch_node('insured_5yr_fixed', smap)
    # uni = fetch_node('uninsured_5yr_fixed', smap)
    # tot = fetch_node('mortgage_funds_advanced', smap)

    # print(len(ins), len(uni), len(tot))
    # print(tot.tail())
    # print('spot check:', ins.iloc[-1], '+', uni.iloc[-1], '=', tot.iloc[-1])

    # rmc = fetch_node('resi_mortgage_credit', smap)
    # print(len(rmc), rmc.index.min().date(), '->', rmc.index.max().date())
    # print(rmc.tail())

    g = fetch_node('resi_mortgage_credit_growth', smap)
    print(len(g), g.index.min().date(), '->', g.index.max().date())
    print(g.tail())
    print(g.describe().round(3))

if __name__ == '__main__':
    main()







