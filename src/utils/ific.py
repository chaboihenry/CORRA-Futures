import csv
import re
import time
import calendar
import requests
import pdfplumber
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
RAW_DIR = ROOT / 'data' / 'raw' / 'ific'
OUTPUT_CSV = RAW_DIR / 'ific_bond_etf_monthly.csv'

USER_AGENT = 'Gold-DAG-Research/1.0 (henry.vianna123@gmail.com; academic data collection)'
REQUEST_TIMEOUT = 30
SLEEP_SECONDS = 1.0

# The brief's URL pattern (uploads/{pub}/{prefix}-Monthly-Investment-Fund-
# Statistics-{Month}-{Year}.pdf) 301-redirects to a blank downloads_new.php
# regardless of prefix or whether the file exists - SIMA gates every direct
# PDF request behind downloads_new.php?id=<opaque WordPress attachment id>,
# and that id cannot be derived from the date or prefix. The id is only
# exposed on the site's own paginated release archive, so Stage 1 resolves
# real download links from there instead of guessing the upload path.
STATS_INDEX_URL = 'https://www.sima-amvi.ca/en/stats/'
STATS_PAGE_URL = 'https://www.sima-amvi.ca/en/stats/page/{page}/'
RELEASE_LINK = re.compile(
    r'href="(https://www\.sima-amvi\.ca/wp-content/uploads/(\d{4})/(\d{2})/'
    r'(SIMA|IFIC)-Monthly-Investment-Fund-Statistics-([A-Za-z]+)-(\d{4})\.pdf'
    r'\?id=(\d+)[^"]*)"'
)
MAX_ARCHIVE_PAGES = 20

# SIMA rebranded from IFIC starting with the March 2025 data month. Kept only
# as a cross-check against the prefix actually found on the archive listing,
# per the brief's warning not to trust this boundary outright.
PREFIX_SWITCH = (2025, 3)

MONTH_NAMES = list(calendar.month_name)


def data_month_label(data_year, data_month):
    """'YYYY-MM' label used for on-disk filenames and CSV rows."""
    return f'{data_year:04d}-{data_month:02d}'


def month_range(start, end):
    """Yield (year, month) tuples from start to end inclusive, 'YYYY-MM' strings."""
    year, month = (int(part) for part in start.split('-'))
    end_year, end_month = (int(part) for part in end.split('-'))
    while (year, month) <= (end_year, end_month):
        yield year, month
        month += 1
        if month == 13:
            month = 1
            year += 1


def prior_month(data_year, data_month):
    """(year, month) of the month immediately before the given one."""
    if data_month == 1:
        return data_year - 1, 12
    return data_year, data_month - 1


def expected_prefix(data_year, data_month):
    """Prefix the brief's boundary predicts; a cross-check, never a fetch gate."""
    return 'SIMA' if (data_year, data_month) >= PREFIX_SWITCH else 'IFIC'


def crawl_release_index(start, end, max_pages=MAX_ARCHIVE_PAGES):
    """
    Walk the paginated SIMA stats archive and return
    {(data_year, data_month): {'url', 'id', 'prefix'}} for releases in
    [start, end]. Stops once every month in range has been found or the
    archive runs out of pages.
    """
    needed_start = tuple(int(part) for part in start.split('-'))
    needed_end = tuple(int(part) for part in end.split('-'))
    total_needed = (needed_end[0] * 12 + needed_end[1]) - (needed_start[0] * 12 + needed_start[1]) + 1

    index = {}
    for page in range(1, max_pages + 1):
        url = STATS_INDEX_URL if page == 1 else STATS_PAGE_URL.format(page=page)
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=REQUEST_TIMEOUT)
        time.sleep(SLEEP_SECONDS)
        assert response.status_code == 200, f'{url}: unexpected status {response.status_code}'

        matches = list(RELEASE_LINK.finditer(response.text))
        for full_url, _pub_year, _pub_month, prefix, month_name, data_year, attachment_id in (m.groups() for m in matches):
            data_month = MONTH_NAMES.index(month_name) if month_name in MONTH_NAMES else None
            if data_month is None:
                continue
            key = (int(data_year), data_month)
            if needed_start <= key <= needed_end and key not in index:
                index[key] = {'url': full_url, 'id': attachment_id, 'prefix': prefix}

        in_range_found = sum(1 for key in index if needed_start <= key <= needed_end)
        if in_range_found >= total_needed or not matches:
            break

    return index


def download_release(data_year, data_month, index, dest_dir=RAW_DIR):
    """
    Download one data month's release PDF using its resolved archive index
    entry. Skips the request entirely if the file is already on disk.
    """
    label = data_month_label(data_year, data_month)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f'{label}.pdf'
    if dest.exists():
        return {'month': label, 'status': 'skipped_exists', 'prefix_used': None, 'source_url': None}

    entry = index.get((data_year, data_month))
    if entry is None:
        return {'month': label, 'status': 'not_found_in_archive', 'prefix_used': None, 'source_url': None}

    response = requests.get(entry['url'], headers={'User-Agent': USER_AGENT},
                            timeout=REQUEST_TIMEOUT, allow_redirects=True)
    time.sleep(SLEEP_SECONDS)

    is_pdf = response.status_code == 200 and response.content[:4] == b'%PDF'
    if not is_pdf:
        return {'month': label, 'status': f'http_{response.status_code}',
                'prefix_used': entry['prefix'], 'source_url': entry['url']}

    dest.write_bytes(response.content)
    expected = expected_prefix(data_year, data_month)
    if expected != entry['prefix']:
        print(f'{label}: prefix boundary mismatch - expected {expected}, archive listed {entry["prefix"]}')

    return {'month': label, 'status': 'ok', 'prefix_used': entry['prefix'], 'source_url': entry['url']}


def download_range(start, end, dest_dir=RAW_DIR, index=None):
    """
    Download every monthly release for data months in [start, end]
    ('YYYY-MM', inclusive). Returns one result dict per month.
    """
    index = index if index is not None else crawl_release_index(start, end)
    results = []
    for data_year, data_month in month_range(start, end):
        result = download_release(data_year, data_month, index, dest_dir)
        print(f"{result['month']}: {result['status']} (prefix={result['prefix_used']})")
        results.append(result)
    return results


def clean_row(row):
    """Drop the empty merged-cell padding pdfplumber's grid extraction leaves in SIMA's tables."""
    return [cell.strip() for cell in row if cell not in (None, '')]


def classify_tables(pdf):
    """
    {'etf_sales': table, 'etf_assets': table}, both as pdfplumber's raw
    row lists, identified by the caption line immediately above each table
    (e.g. 'ETF net sales/net redemptions ($ millions)*'). Mutual fund tables
    share the same row labels but are captioned 'Mutual fund ...' instead, so
    a caption-text match is what tells the two apart - table position alone
    is not trusted, since the count of preceding tables is not part of the
    contract.
    """
    found = {}
    for page in pdf.pages:
        tables = page.find_tables()
        prev_bottom = 0
        for table in tables:
            _left, top, _right, bottom = table.bbox
            caption_area = page.within_bbox((0, prev_bottom, page.width, top))
            caption_text = caption_area.extract_text() or ''
            prev_bottom = bottom

            last_line = caption_text.strip().splitlines()[-1] if caption_text.strip() else ''
            lowered = last_line.lower()
            if 'etf' not in lowered:
                continue
            if 'net sales' in lowered or 'net redemption' in lowered:
                found['etf_sales'] = table.extract()
            elif 'net asset' in lowered:
                found['etf_assets'] = table.extract()

    return found


def extract_bond_row(table):
    """The cleaned 'Bond' asset-class row from an extracted table, or None."""
    for row in table:
        cleaned = clean_row(row)
        if cleaned and cleaned[0] == 'Bond':
            return cleaned
    return None


def parse_amount(cell):
    """A SIMA table cell as a float; parenthesised values are negative."""
    cell = cell.strip()
    negative = cell.startswith('(') and cell.endswith(')')
    digits = cell.strip('()').replace(',', '')
    assert re.match(r'^\d+(\.\d+)?$', digits), f'unexpected table cell: {cell!r}'
    value = float(digits)
    return -value if negative else value


def parse_release(pdf_path):
    """
    Bond-row figures from one release's ETF tables: the reported data month
    (column 1) and the prior month as restated in this same release
    (column 2).
    """
    with pdfplumber.open(pdf_path) as pdf:
        tables = classify_tables(pdf)

    assert 'etf_sales' in tables, f'{pdf_path}: ETF net sales/net redemptions table not found'
    assert 'etf_assets' in tables, f'{pdf_path}: ETF net assets table not found'

    sales_row = extract_bond_row(tables['etf_sales'])
    assets_row = extract_bond_row(tables['etf_assets'])
    assert sales_row, f'{pdf_path}: Bond row not found in ETF net sales table'
    assert assets_row, f'{pdf_path}: Bond row not found in ETF net assets table'

    return {
        'sales_current': parse_amount(sales_row[1]),
        'sales_prior_restated': parse_amount(sales_row[2]),
        'assets_current': parse_amount(assets_row[1]),
        'assets_prior_restated': parse_amount(assets_row[2]),
    }


def values_close(a, b, tol=0.05):
    """True if two figures agree within SIMA's own rounding (nearest $M / $0.1B)."""
    return abs(a - b) <= tol


def parse_range(start, end, raw_dir=RAW_DIR, index=None):
    """
    Parse every downloaded release for [start, end] and cross-check each
    release's restated prior-month figures against that prior month's own
    release. Returns (rows keyed by month label, list of disagreements).
    Disagreements are reported, never silently reconciled.
    """
    parsed = {}
    for data_year, data_month in month_range(start, end):
        label = data_month_label(data_year, data_month)
        path = raw_dir / f'{label}.pdf'
        if not path.exists():
            print(f'{label}: no PDF on disk, skipping parse')
            continue

        try:
            values = parse_release(path)
        except AssertionError as error:
            print(f'{label}: parse failed - {error}')
            continue

        entry = (index or {}).get((data_year, data_month), {})
        parsed[label] = {
            'month': label,
            'bond_etf_net_sales_millions': values['sales_current'],
            'bond_etf_net_assets_billions': values['assets_current'],
            'source_url': entry.get('url', ''),
            'prefix_used': entry.get('prefix', ''),
            'sales_prior_restated': values['sales_prior_restated'],
            'assets_prior_restated': values['assets_prior_restated'],
        }

    disagreements = []
    for label, row in parsed.items():
        data_year, data_month = (int(part) for part in label.split('-'))
        prior_label = data_month_label(*prior_month(data_year, data_month))
        prior_row = parsed.get(prior_label)
        if prior_row is None:
            continue
        if not values_close(prior_row['bond_etf_net_sales_millions'], row['sales_prior_restated']):
            disagreements.append((prior_label, 'net_sales_millions',
                                  prior_row['bond_etf_net_sales_millions'], row['sales_prior_restated'], label))
        if not values_close(prior_row['bond_etf_net_assets_billions'], row['assets_prior_restated']):
            disagreements.append((prior_label, 'net_assets_billions',
                                  prior_row['bond_etf_net_assets_billions'], row['assets_prior_restated'], label))

    return parsed, disagreements


CSV_COLUMNS = ['month', 'bond_etf_net_sales_millions', 'bond_etf_net_assets_billions',
              'source_url', 'prefix_used']


def write_csv(parsed, output_csv=OUTPUT_CSV):
    """One row per data month, ascending, columns per CSV_COLUMNS."""
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        for label in sorted(parsed):
            writer.writerow(parsed[label])


if __name__ == '__main__':
    START, END = '2023-03', '2026-07'
    index = crawl_release_index(START, END)
    download_range(START, END, index=index)
    parsed, disagreements = parse_range(START, END, index=index)
    write_csv(parsed)
    if disagreements:
        print(f'{len(disagreements)} prior-month cross-check disagreement(s):')
        for prior_label, field, prior_value, restated_value, restated_in in disagreements:
            print(f'  {prior_label} {field}: reported {prior_value}, restated as {restated_value} in {restated_in}')
