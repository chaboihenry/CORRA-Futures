import inspect
import yaml
import pandas as pd
import statsmodels.api as sm
from pathlib import Path

from data import load_series_map
from nodes import fetch_node
from effects import variance_screen, chain_effect
from estimators import pass_through, distributed_lag, local_projection

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / 'config'
INTERIM = ROOT / 'data' / 'interim'
TABLES = ROOT / 'results' / 'tables'

COLUMNS = ['sector', 'layer', 'parent', 'child', 'sd_y', 'sd_dx', 'ratio', 'pass']
EDGE_COLUMNS = ['sector', 'layer', 'parent', 'child', 'estimator',
                'beta', 'se', 'pval', 'n', 'peak', 'status']
CHAIN_COLUMNS = ['sector', 'n_edges', 'product', 'effect_1sd', 'se',
                 'ci_low', 'ci_high', 'status']
CANDIDATE_COLUMNS = ['sector', 'layer', 'parent', 'candidate', 'is_selected',
                     'sd_y', 'sd_dx', 'ratio', 'pass', 'existing_status']
AUDIT_COLUMNS = ['sector', 'layer', 'parent', 'child', 'declared', 'derived',
                 'missing', 'unjustified', 'note']

ESTIMATORS = {
    'pass_through': pass_through,
    'distributed_lag': distributed_lag,
    'local_projection': local_projection,
}

# layer 1's parent is the policy surprise, measured in bp. It is the treatment
# rather than a catalogued node, so its kind is fixed here.
TREATMENT_KIND = 'rate'

# an edge estimated on fewer periods than this is not reported: HAC standard
# errors collapse once the residual degrees of freedom approach zero, which
# reads as a near-certain coefficient rather than as a failure.
MIN_OBS = 20

# resample alias and coarseness rank; an edge is screened at the coarser of its
# two nodes, so a daily return is not shrunk by averaging a month of returns.
FREQ_RULE = {
    'business_daily': ('B', 0),
    'weekly': ('W', 1),
    'weekly_auction': ('W', 1),
    'biweekly': ('2W', 1),
    'monthly': ('MS', 2),
    'quarterly': ('QS', 3),
}

# the ratio only has content when both sides measure the same kind of thing.
UNIT_KIND = {
    'percent': 'rate',
    'pp': 'rate',
    'dollars': 'quantity',
    'millions': 'quantity',
    'count': 'quantity',
    'contracts': 'quantity',
    'index': 'index',
    'dummy': 'dummy',
}


def load_graph_spec():
    """The graph spec as a dict."""
    with open(CONFIG / 'graph_spec.yaml') as f:
        spec = yaml.safe_load(f)
    assert 'meta' in spec, 'graph_spec.yaml has no meta block'
    return spec


def is_verified(name, smap):
    """True when the node is catalogued and its id is confirmed."""
    return name in smap and smap[name].get('status') == 'verified'


def to_bp(series, name, smap):
    """Rates and yields are catalogued in percent; estimation works in bp."""
    scale = 100 if smap[name].get('units') == 'percent' else 1
    return series * scale


def construction_inputs(name, smap, seen=None):
    """
    Every node a derived node is built from, transitively.

    A node built from its own parent cannot be estimated against it: the
    regression returns an arithmetic identity rather than a relationship.
    lending_deposit_spread on gic_1y returned d(ehh)/d(gic) - 1 that way, and
    zag_xic_return_spread on bond_index_return_pct returns
    1 - d(equity)/d(bond).
    """
    if seen is None:
        seen = set()
    for src in (smap.get(name) or {}).get('inputs') or []:
        if src in seen:
            continue
        seen.add(src)
        construction_inputs(src, smap, seen)
    return seen


def shares_construction(parent, child, smap):
    """True when the child is built from the parent, at any depth."""
    return parent in construction_inputs(child, smap)


def screen_scale(parent, child, smap):
    """
    Factor putting the child on the parent's scale for the variance ratio.

    percent and pp are the same unit of measure - both are percentage points -
    and UNIT_KIND already treats them as one kind. to_bp does not: it scales
    percent by 100 into bp and leaves pp alone, which is harmless inside an
    edge, where both sides of a regression carry their own scaling, but wrong
    across the two nodes of a ratio. Without this a pp child is divided by a
    bp parent change and reads 100x smoother than it is.
    """
    units = (smap[parent].get('units'), smap[child].get('units'))
    if units == ('percent', 'pp'):
        return 100.0
    if units == ('pp', 'percent'):
        return 0.01
    return 1.0


def coarser_frequency(parent, child, smap):
    """The coarser of the two nodes' frequencies, as a resample alias."""
    pick = None
    for name in (parent, child):
        freq = smap[name].get('frequency')
        assert freq in FREQ_RULE, f'{name}: unknown frequency {freq!r}'
        if pick is None or FREQ_RULE[freq][1] > FREQ_RULE[pick][1]:
            pick = freq
    return FREQ_RULE[pick][0]


def unit_kind(name, smap):
    """The kind of quantity a node measures: rate, quantity, index or dummy."""
    units = smap[name].get('units')
    assert units in UNIT_KIND, f'{name}: unknown units {units!r}'
    return UNIT_KIND[units]


def comparable(parent, child, smap):
    """True when both nodes measure the same kind of quantity."""
    return unit_kind(parent, smap) == unit_kind(child, smap)


def edge_pairs(spec):
    """
    Every parent-child pair in the graph: layer N-1 selected -> layer N selected,
    carrying the child layer's estimator settings.

    Layer 1 is skipped. Its parent is the policy surprise, which is the
    treatment rather than a catalogued node, so there is nothing to fetch.
    """
    out = []
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        layers = block['layers']
        for prev, cur in zip(layers, layers[1:]):
            parent = prev.get('selected')
            child = cur.get('selected')
            if parent is None or child is None:
                continue
            out.append({
                'sector': sector,
                'layer':  cur['layer'],
                'parent': parent,
                'child':  child,
                'estimator': cur.get('estimator'),
                'controls':  cur.get('controls') or [],
                'kwargs': {k: cur[k] for k in
                           ('max_lag', 'max_horizon', 'month_dummies',
                            'aggregate_parent', 'diff_x', 'diff_y',
                            'diff_controls', 'resample_child')
                           if k in cur},
            })
    return out


def fetch_edge_series(pair, smap, start, cache):
    """Parent, child and controls, filtered to the sample and scaled to bp."""
    for name in [pair['parent'], pair['child']] + list(pair['controls']):
        if name not in cache:
            s = fetch_node(name, smap)
            s = s[s.index >= start]
            cache[name] = to_bp(s, name, smap)
    return (cache[pair['parent']],
            cache[pair['child']],
            [cache[c] for c in pair['controls']])


def check_kwargs(estimator, fn, kwargs):
    """
    Every key the spec forwards must be a parameter of the chosen estimator.

    edge_pairs forwards whatever the layer entry sets, and the estimators do
    not share a signature: diff_x and aggregate_parent belong to
    local_projection, diff_y and max_lag to distributed_lag. Without this the
    mismatch surfaces as a bare TypeError naming no key.
    """
    allowed = set(inspect.signature(fn).parameters)
    bad = [k for k in kwargs if k not in allowed]
    if bad:
        raise TypeError(
            f'{estimator} does not accept {bad!r}; it takes '
            f'{sorted(allowed - {"y", "x", "controls"})}. Remove the key from '
            f'the layer entry or change the estimator')


def headline(result, estimator, diff_y=True):
    """
    The one row representing an edge: the summed effect for a distributed lag,
    the largest significant horizon for a local projection. None when a local
    projection has no horizon significant at 5%, and None when the selected
    row rests on fewer than MIN_OBS observations.
    """
    if estimator == 'distributed_lag' and diff_y:
        hits = [r for r in result if r.get('lag') == 'sum']
        assert hits, 'distributed_lag returned no sum row'
        row = hits[0]
        if row['n'] < MIN_OBS:
            return None
        return {'beta': row['beta'], 'se': row['se'], 'pval': row['pval'],
                'n': row['n'], 'peak': 'sum'}
    if estimator == 'distributed_lag':
        # child already a difference: the lag polynomial sums to zero by
        # construction, so read the peak lag the way a projection is read.
        # Control rows carry a string lag and the sum row is excluded.
        lags = [r for r in result if isinstance(r.get('lag'), int)]
        sig = [r for r in lags if r['pval'] < 0.05]
        if not sig:
            return None
        row = max(sig, key=lambda r: abs(r['beta']))
        if row['n'] < MIN_OBS:
            return None
        return {'beta': row['beta'], 'se': row['se'], 'pval': row['pval'],
                'n': row['n'], 'peak': row['lag']}
    sig = [r for r in result if r['pval'] < 0.05]
    if not sig:
        return None
    row = max(sig, key=lambda r: abs(r['beta']))
    if row['n'] < MIN_OBS:
        return None
    key = 'horizon' if 'horizon' in row else 'lag'
    return {'beta': row['beta'], 'se': row['se'], 'pval': row['pval'],
            'n': row['n'], 'peak': row[key]}


def layer1_nodes(spec):
    """Each sector's layer-1 node, deduplicated, in first-seen order."""
    out = []
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        node = block['layers'][0].get('selected')
        if node is not None and node not in out:
            out.append(node)
    assert out, 'no layer-1 node selected in any sector'
    return out


def load_surprises(spec, start=None):
    """
    Passing announcements in the sample, with the FOMC flag joined.

    The treatment is not a catalogued node, so it is read from the processed
    file the spec names rather than through series_map.
    """
    if start is None:
        start = spec['meta']['primary_sample']['start']
    start = pd.Timestamp(start)
    df = pd.read_csv(ROOT / spec['meta']['treatment_file'],
                     parse_dates=['date', 'prev_date'])
    keep = df[(df['status'] == 'ok') & (df['date'] >= start)]
    assert not keep.empty, f'no passing announcements after {start.date()}'

    ann = pd.read_csv(INTERIM / 'boc_announcements_with_decisions.csv',
                      parse_dates=['date'])
    cols = [c for c in ('date', 'fomc_same_day', 'decision_bps')
            if c in ann.columns]
    return keep.merge(ann[cols], on='date', how='left').reset_index(drop=True)


def event_changes(series, surprises, offset=0):
    """
    The series' change across each announcement window, one row per event.

    offset shifts which day counts as the event: 0 is the announcement day
    itself, 1 the day after. The series must already be in bp.
    """
    idx = series.index
    rows = []
    for _, r in surprises.iterrows():
        i = idx.searchsorted(r['date']) + offset
        if i <= 0 or i >= len(idx):
            continue
        now, prev = series.iloc[i], series.iloc[i - 1]
        if pd.isna(now) or pd.isna(prev):
            continue
        rows.append({'date': r['date'], 'dy': now - prev,
                     'surprise': r['surprise'],
                     'fomc': bool(r.get('fomc_same_day', False))})
    out = pd.DataFrame(rows)
    assert not out.empty, 'no usable announcement windows'
    return out


def regress_on_surprise(changes):
    """
    OLS of the event-window change on the surprise, HC3 errors.

    HC3 rather than HAC: these are discrete announcements, not a time series,
    so there is no autocorrelation to correct and what matters is the
    small-sample leverage correction.
    """
    X = sm.add_constant(changes['surprise'])
    fit = sm.OLS(changes['dy'], X).fit(cov_type='HC3')
    return {
        'beta': float(fit.params['surprise']),
        'se': float(fit.bse['surprise']),
        'pval': float(fit.pvalues['surprise']),
        'r2': float(fit.rsquared),
        'n': int(fit.nobs),
        'alpha': float(fit.params['const']),
        'alpha_p': float(fit.pvalues['const']),
        'p_vs_1': float(fit.t_test('surprise = 1').pvalue),
    }


def estimate_layer1(spec, smap, start=None, offset=0):
    """
    The treatment edge, one estimate per layer-1 node.

    Layer 1 is what edge_pairs skips: its parent is the policy surprise, which
    is the treatment rather than a catalogued node, so it cannot be walked as a
    parent-child pair. Returns {node: {beta, se, pval, r2, n, ...}}, which
    chain_all uses as the first factor of every chain.
    """
    if start is None:
        start = spec['meta']['primary_sample']['start']
    start = pd.Timestamp(start)
    surprises = load_surprises(spec, start)
    out = {}
    for node in layer1_nodes(spec):
        # the series is NOT trimmed to the sample: the window is a one-day
        # change, so the first announcement needs the trading day before it.
        # Trimming first puts that announcement at position 0 and silently
        # drops it. The sample is defined by which announcements are kept.
        series = to_bp(fetch_node(node, smap), node, smap)
        out[node] = regress_on_surprise(
            event_changes(series, surprises, offset=offset))
    return out


def child_map(spec):
    """Direct children of each node, taken from the selected edges."""
    out = {}
    for pair in edge_pairs(spec):
        out.setdefault(pair['parent'], set()).add(pair['child'])
    return out


def descendants_of(spec):
    """
    Every node downstream of each node, transitively, along the modelled
    chains. Built from edge_pairs, so layer 1 is not in it: the treatment is
    not an ancestor here. What the map answers is "does conditioning on this
    node sit on the path the edge is trying to measure".
    """
    direct = child_map(spec)
    out = {}
    for node in direct:
        seen, stack = set(), list(direct[node])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(direct.get(cur, ()))
        out[node] = seen
    return out


def observed_proxy(name, entry):
    """
    The node actually conditioned on.

    exogenous_nodes is keyed by the OBSERVED series. backdoor_blocked names
    the latent common cause it stands in for - 'X <- global_rates -> Y' - and
    that latent node is never itself a series anyone can condition on. An
    explicit `observed` field overrides the key if a future entry is ever
    keyed by the unobserved node instead.
    """
    return entry.get('observed', name)


def adjustment_set(parent, child, spec, descendants=None):
    """
    Observed controls implied by the backdoor criterion for one edge.

    A node in exogenous_nodes qualifies only when its parent_of list contains
    BOTH ends of the edge. That is what makes it a common cause, and a common
    cause is what opens a backdoor. A node that causes only the child is not a
    confounder of this edge: it adds noise, not bias, and conditioning on it
    buys nothing. A node that causes only the parent is likewise not on any
    backdoor path into the child.

    A qualifying node is still dropped when it is a descendant of the edge's
    parent, because conditioning on a mediator blocks part of the very path
    the edge measures. Those are returned with a reason rather than dropped
    silently.

    Returns {'controls': [...], 'excluded': [{'node', 'reason'}, ...]}.
    """
    if descendants is None:
        descendants = descendants_of(spec)
    downstream = descendants.get(parent, set())
    controls, excluded = [], []
    for name, entry in (spec.get('exogenous_nodes') or {}).items():
        covers = entry.get('parent_of') or []
        if parent not in covers or child not in covers:
            continue
        node = observed_proxy(name, entry)
        if node in downstream:
            excluded.append({
                'node': node,
                'reason': f'descendant of {parent}, so conditioning on it '
                          f'would block part of the path being measured'})
            continue
        controls.append(node)
    return {'controls': controls, 'excluded': excluded}


def reconcile_controls(spec, smap=None):
    """
    Compare each edge's hand-written controls list with the set the backdoor
    criterion implies, and write results/tables/control_audit.csv.

    missing     = derived but not declared: a backdoor left open.
    unjustified = declared but not derived: a control with no path to block.

    Layer 1 is not audited, for the same reason edge_pairs skips it: its
    parent is the treatment rather than a catalogued node, so there is no
    backdoor into it to reason about.

    Reports only. No controls list is modified.
    """
    if smap is None:
        smap = load_series_map()
    descendants = descendants_of(spec)
    exo = spec.get('exogenous_nodes') or {}
    rows = []
    for pair in edge_pairs(spec):
        declared = list(pair['controls'])
        found = adjustment_set(pair['parent'], pair['child'], spec, descendants)
        derived = found['controls']
        missing = [c for c in derived if c not in declared]
        unjustified = [c for c in declared if c not in derived]

        notes = [f"{e['node']}: {e['reason']}" for e in found['excluded']]
        for c in missing:
            blocked = exo.get(c, {}).get('backdoor_blocked')
            notes.append(f'{c} causes both ends'
                         + (f' [{blocked}]' if blocked else '')
                         + ' but is not declared')
        for c in unjustified:
            entry = exo.get(c)
            if entry is None:
                notes.append(f'{c} is declared but is not in exogenous_nodes, '
                             f'so it asserts no path at all')
                continue
            covers = entry.get('parent_of') or []
            side = ('the child only' if pair['child'] in covers else
                    'the parent only' if pair['parent'] in covers else
                    'neither end')
            note = f'{c} declared, but its parent_of covers {side}'
            if entry.get('warning'):
                note += f" - {entry['warning']}"
            notes.append(note)
        for c in declared:
            if c in smap and smap[c].get('status') != 'verified':
                notes.append(f'{c} is status {smap[c].get("status")!r} in '
                             f'series_map, so the edge cannot be estimated '
                             f'with it')

        rows.append({
            'sector': pair['sector'], 'layer': pair['layer'],
            'parent': pair['parent'], 'child': pair['child'],
            'declared': '; '.join(declared),
            'derived': '; '.join(derived),
            'missing': '; '.join(missing),
            'unjustified': '; '.join(unjustified),
            'note': ' | '.join(notes),
        })

    df = pd.DataFrame(rows, columns=AUDIT_COLUMNS)
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / 'control_audit.csv', index=False)
    return df


def screen_all(spec, smap, start=None, aggregate=None, threshold=0.5):
    """
    Run variance_screen on every verified edge, at the coarser of the two
    node frequencies. Unsourced pairs get 'unsourced', mismatched units get
    'incomparable'. Writes results/tables/variance_screen.csv.
    """
    if start is None:
        start = spec['meta']['primary_sample']['start']
    start = pd.Timestamp(start)
    pairs = edge_pairs(spec)
    assert pairs, 'no parent-child pairs found in the graph spec'

    cache = {}
    rows = []
    for pair in pairs:
        row = {k: pair[k] for k in ('sector', 'layer', 'parent', 'child')}
        row.update({'sd_y': None, 'sd_dx': None, 'ratio': None, 'pass': 'unsourced'})
        parent, child = pair['parent'], pair['child']
        if not (is_verified(parent, smap) and is_verified(child, smap)):
            rows.append(row)
            continue
        if not comparable(parent, child, smap):
            row['pass'] = 'incomparable'
            rows.append(row)
            continue
        try:
            for name in (parent, child):
                if name not in cache:
                    s = fetch_node(name, smap)
                    s = s[s.index >= start]
                    cache[name] = to_bp(s, name, smap)
        except (AssertionError, NotImplementedError, KeyError) as e:
            print(f'skipped {parent} -> {child}: {e}')
            rows.append(row)
            continue
        freq = aggregate if aggregate else coarser_frequency(parent, child, smap)
        y = cache[child].resample(freq).mean().dropna()
        res = variance_screen(y, cache[parent], aggregate=freq,
                              threshold=threshold,
                              scale_y=screen_scale(parent, child, smap))
        row['sd_y'] = res['sd_y']
        row['sd_dx'] = res['sd_dx']
        row['ratio'] = res['ratio']
        row['pass'] = res['pass']
        rows.append(row)

    df = pd.DataFrame(rows, columns=COLUMNS)
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / 'variance_screen.csv', index=False)
    return df


def candidate_pairs(spec):
    """
    Every (previous layer's selected node, candidate) pair in the graph.

    screen_all walks selected -> selected. This walks selected -> every
    candidate the layer lists, so a layer's rejected and untested products are
    screened on the same footing as the one that was picked.
    """
    out = []
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        layers = block['layers']
        for prev, cur in zip(layers, layers[1:]):
            parent = prev.get('selected')
            for cand in cur.get('candidates') or []:
                out.append({
                    'sector': sector,
                    'layer': cur['layer'],
                    'parent': parent,
                    'candidate': cand['node'],
                    'is_selected': cand['node'] == cur.get('selected'),
                    'existing_status': cand.get('status'),
                })
    return out


def screen_candidates(spec, smap, start=None, aggregate=None, threshold=0.5):
    """
    Run variance_screen on every candidate in every layer against the
    previous layer's selected node. Candidates missing from series_map or not
    yet verified get 'unsourced'; mismatched units get 'incomparable'. Writes
    results/tables/candidate_screen.csv.
    """
    if start is None:
        start = spec['meta']['primary_sample']['start']
    start = pd.Timestamp(start)
    pairs = candidate_pairs(spec)
    assert pairs, 'no candidates found in the graph spec'

    cache = {}
    rows = []
    for pair in pairs:
        row = dict(pair)
        row.update({'sd_y': None, 'sd_dx': None, 'ratio': None,
                    'pass': 'unsourced'})
        parent, child = pair['parent'], pair['candidate']
        if parent is None:
            rows.append(row)
            continue
        if not (is_verified(parent, smap) and is_verified(child, smap)):
            rows.append(row)
            continue
        if not comparable(parent, child, smap):
            row['pass'] = 'incomparable'
            rows.append(row)
            continue
        try:
            for name in (parent, child):
                if name not in cache:
                    s = fetch_node(name, smap)
                    s = s[s.index >= start]
                    cache[name] = to_bp(s, name, smap)
        except (AssertionError, NotImplementedError, KeyError) as e:
            print(f'skipped {parent} -> {child}: {e}')
            rows.append(row)
            continue
        freq = aggregate if aggregate else coarser_frequency(parent, child, smap)
        y = cache[child].resample(freq).mean().dropna()
        try:
            res = variance_screen(y, cache[parent], aggregate=freq,
                                  threshold=threshold,
                                  scale_y=screen_scale(parent, child, smap))
        except AssertionError as e:
            print(f'skipped {parent} -> {child}: {e}')
            rows.append(row)
            continue
        row['sd_y'] = res['sd_y']
        row['sd_dx'] = res['sd_dx']
        row['ratio'] = res['ratio']
        row['pass'] = res['pass']
        rows.append(row)

    df = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / 'candidate_screen.csv', index=False)
    return df


def layer1_rows(spec, layer1):
    """One edge row per sector for the treatment edge, so edges.csv starts at
    layer 1 rather than layer 2."""
    treatment = spec['meta']['treatment']
    rows = []
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        first = block['layers'][0]
        node = first.get('selected')
        row = {'sector': sector, 'layer': first['layer'], 'parent': treatment,
               'child': node, 'estimator': first.get('estimator'),
               'beta': None, 'se': None, 'pval': None, 'n': None,
               'peak': None, 'status': 'unsourced'}
        if node in layer1:
            row.update({k: layer1[node][k]
                        for k in ('beta', 'se', 'pval', 'n')})
            row['peak'] = 'event'
            row['status'] = 'ok'
        rows.append(row)
    return rows


def estimate_all(spec, smap, layer1, start=None):
    """
    Estimate every verified edge, dispatching on the spec's estimator.
    Layer-1 rows come from estimate_layer1. Failures are recorded as rows, not
    raised. Writes results/tables/edges.csv.
    """
    if start is None:
        start = spec['meta']['primary_sample']['start']
    start = pd.Timestamp(start)
    pairs = edge_pairs(spec)
    assert pairs, 'no parent-child pairs found in the graph spec'

    cache = {}
    rows = layer1_rows(spec, layer1)
    for pair in pairs:
        row = {k: pair[k] for k in
               ('sector', 'layer', 'parent', 'child', 'estimator')}
        row.update({'beta': None, 'se': None, 'pval': None,
                    'n': None, 'peak': None, 'status': 'unsourced'})

        if not (is_verified(pair['parent'], smap)
                and is_verified(pair['child'], smap)):
            rows.append(row)
            continue
        if pair['estimator'] not in ESTIMATORS:
            row['status'] = f'unknown estimator {pair["estimator"]!r}'
            rows.append(row)
            continue

        try:
            parent, child, ctrls = fetch_edge_series(pair, smap, start, cache)
            fn = ESTIMATORS[pair['estimator']]
            check_kwargs(pair['estimator'], fn, pair['kwargs'])
            result = fn(child, parent, controls=ctrls, **pair['kwargs'])
            head = headline(result, pair['estimator'],
                            diff_y=pair['kwargs'].get('diff_y', True))
            if head is None:
                ns = [r['n'] for r in result if 'n' in r]
                row['status'] = ('insufficient_n' if ns and max(ns) < MIN_OBS
                                 else 'no_significant_horizon')
            else:
                row.update(head)
                row['status'] = 'ok'
        except Exception as e:
            row['status'] = f'{type(e).__name__}: {e}'
        rows.append(row)

    df = pd.DataFrame(rows, columns=EDGE_COLUMNS)
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / 'edges.csv', index=False)
    return df


def chain_all(spec, edges, smap, layer1, shock=None):
    """
    Multiply each sector's estimated edges into a total path effect with a
    delta-method CI. Layer 1 comes from estimate_layer1. A product is only
    reported when
    consecutive edges compose: each edge's child must measure the same kind of
    quantity as the next edge's parent. Writes chains.csv.
    """
    if shock is None:
        shock = spec['meta'].get('shock_1sd_bp', 4.56)

    rows = []
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        row = {'sector': sector, 'n_edges': 0, 'product': None,
               'effect_1sd': None, 'se': None, 'ci_low': None,
               'ci_high': None, 'status': 'incomplete'}

        l1_node = block['layers'][0].get('selected')
        if l1_node not in layer1:
            row['status'] = f'layer 1 node {l1_node!r} not estimated'
            rows.append(row)
            continue

        # layer 1's parent is the policy surprise, which is not a catalogued
        # node; only its child takes part in the composition check.
        links = [{'parent': None, 'child': l1_node}]
        betas = [layer1[l1_node]['beta']]
        ses = [layer1[l1_node]['se']]
        # layer 1 is already the first factor and edges.csv now carries its row
        # as well, so it has to be filtered out or it would count twice
        sub = edges[(edges['sector'] == sector) & (edges['status'] == 'ok')
                    & (edges['layer'] > 1)]
        for _, e in sub.sort_values('layer').iterrows():
            betas.append(e['beta'])
            ses.append(e['se'])
            links.append({'parent': e['parent'], 'child': e['child']})

        row['n_edges'] = len(betas)

        # A product only means something when the units cancel, which takes
        # two things: each beta must relate two measures of the same kind, and
        # consecutive betas must meet on the same kind. The first is the test
        # variance_screen applies edge by edge; a chain may not quietly
        # multiply an edge the screen rejected.
        kinds = [(link,
                  TREATMENT_KIND if link['parent'] is None
                  else unit_kind(link['parent'], smap),
                  unit_kind(link['child'], smap))
                 for link in links]
        breaks = [f'{link["parent"] or "policy_surprise"} ({pk}) -> {link["child"]} ({ck})'
                  for link, pk, ck in kinds if pk != ck]
        breaks += [f'{a["child"]} ({ck}) meets {b["parent"]} ({pk})'
                   for (a, _, ck), (b, pk, _) in zip(kinds, kinds[1:]) if ck != pk]
        if breaks:
            print(f'{sector}: units do not compose at ' + '; '.join(breaks))
            row['status'] = 'units_do_not_compose'
            rows.append(row)
            continue

        try:
            res = chain_effect(betas, ses, shock)
            row.update({'product': res['effect'] / shock,
                        'effect_1sd': res['effect'],
                        'se': res['se'],
                        'ci_low': res['ci_low'],
                        'ci_high': res['ci_high'],
                        'status': 'ok'})
        except Exception as e:
            row['status'] = f'{type(e).__name__}: {e}'
        rows.append(row)

    df = pd.DataFrame(rows, columns=CHAIN_COLUMNS)
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / 'chains.csv', index=False)
    return df


def main():
    spec = load_graph_spec()
    smap = load_series_map()

    print('--- variance screen ---')
    print(screen_all(spec, smap).round(3).to_string(index=False))

    print('\n--- candidate screen ---')
    print(screen_candidates(spec, smap).round(3).to_string(index=False))

    print('\n--- control audit ---')
    audit = reconcile_controls(spec, smap)
    print(audit[['sector', 'layer', 'child', 'declared', 'derived',
                 'missing', 'unjustified']].to_string(index=False))
    for _, r in audit[audit['note'] != ''].iterrows():
        print(f"  {r['sector']} L{r['layer']}: {r['note']}")

    print('\n--- layer 1: the treatment edge ---')
    layer1 = estimate_layer1(spec, smap)
    print(pd.DataFrame(layer1).T[['beta', 'se', 'pval', 'r2', 'n']]
          .round(4).to_string())

    print('\n--- edges ---')
    edges = estimate_all(spec, smap, layer1)
    print(edges.round(4).to_string(index=False))

    print('\n--- chains ---')
    print(chain_all(spec, edges, smap, layer1).round(3).to_string(index=False))


if __name__ == '__main__':
    main()