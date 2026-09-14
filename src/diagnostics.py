"""
Investigations that inform the spec but are not part of it.

Every function takes node names, so the same function serves any sector:
    python src/diagnostics.py alignment_check --node goc_2y
    python src/diagnostics.py term_structure
    python src/diagnostics.py passthrough_shape --parent goc_5y --child effective_household_rate
    python src/diagnostics.py direct_path_test --parent effective_household_rate --child mortgage_funds_advanced_growth
    python src/diagnostics.py mechanism_test --parent mortgage_funds_advanced_growth --child consumer_insolvencies_growth
    python src/diagnostics.py scale_invariance --parent effective_business_rate --child-a nfc_credit_flow --child-b nfc_credit_growth
"""
import argparse
import pandas as pd

from data import load_series_map
from nodes import fetch_node
from estimators import distributed_lag, local_projection
from causal_estimates import (ROOT, load_graph_spec, load_surprises,
                              event_changes, regress_on_surprise, to_bp)

# estimators return full precision; format at the presentation layer so a
# coefficient of 1e-4 stays legible next to one of 1e+2.
FMT = '%.5g'


def context(start=None):
    """The spec, the catalogue and the sample start, loaded once."""
    spec = load_graph_spec()
    smap = load_series_map()
    if start is None:
        start = spec['meta']['primary_sample']['start']
    return spec, smap, pd.Timestamp(start)


def series(name, smap, start, trim=True):
    """
    One node, rates in bp. trim=False keeps the lead-in before the sample,
    which an event-window change needs for its first event.
    """
    s = fetch_node(name, smap)
    if trim:
        s = s[s.index >= start]
        assert not s.empty, f'{name} is empty after {start.date()}'
    return to_bp(s, name, smap)


def grandparent_of(spec, node):
    """
    The node two layers above, read off the spec: the parent of whichever
    selected edge has `node` as its child.
    """
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        layers = block['layers']
        for i, cur in enumerate(layers):
            if cur.get('selected') == node and i > 0:
                gp = layers[i - 1].get('selected')
                if gp is not None:
                    return gp, sector
    return None, None


def show(rows, drop=()):
    """One estimator table, printed."""
    df = pd.DataFrame(rows)
    df = df.drop(columns=[c for c in drop if c in df.columns])
    print(df.to_string(index=False, float_format=FMT))


def at_horizon(rows, h):
    """The one row at horizon h."""
    hits = [r for r in rows if r.get('horizon') == h]
    assert hits, f'no row at horizon {h}'
    return hits[0]


def peak(rows):
    """The significant horizon with the largest absolute beta, or None."""
    sig = [r for r in rows if r['pval'] < 0.05]
    return max(sig, key=lambda r: abs(r['beta'])) if sig else None


# Does the yield move on the announcement day or the day after
def alignment_check(node='goc_2y', offsets=(0, 1), start=None):
    """
    Which day carries the response. offset 0 is the announcement day, 1 the
    day after. If offset 1 dominates, every layer-1 estimate is measured on
    the wrong window.
    """
    spec, smap, start = context(start)
    surprises = load_surprises(spec, start)
    s = series(node, smap, start, trim=False)
    rows = []
    for off in offsets:
        r = regress_on_surprise(event_changes(s, surprises, offset=off))
        rows.append({'node': node, 'offset': off, **r})
    print(f'--- alignment: {node}, n_events={len(surprises)} ---')
    show(rows)
    best = max(rows, key=lambda r: r['r2'])
    print(f"\nR2 is highest at offset {best['offset']} "
          f"({best['r2']:.3f} vs {min(r['r2'] for r in rows):.3f}). "
          f"The pipeline measures offset 0.")


# ---------------------------------------------------------------------------
# 2. beta across the curve
# ---------------------------------------------------------------------------
def term_structure(nodes=None, start=None):
    """
    The layer-1 coefficient at each tenor. A pure expectations model cannot
    produce amplification above 1, so a hump above unity is itself a finding.
    """
    spec, smap, start = context(start)
    if not nodes:
        nodes = [c['node'] for c in spec['layer1_candidates']['candidates']]
    surprises = load_surprises(spec, start)
    rows = []
    for node in nodes:
        if node not in smap or smap[node].get('status') != 'verified':
            print(f'skipped {node}: not verified in series_map')
            continue
        r = regress_on_surprise(
            event_changes(series(node, smap, start, trim=False), surprises))
        rows.append({'node': node, **r})
    print(f'--- term structure, n_events={len(surprises)} ---')
    show(rows)
    top = max(rows, key=lambda r: abs(r['beta']))
    print(f"\npeak |beta| at {top['node']}: {top['beta']:.3f}. "
          f"p vs 1 = {top['p_vs_1']:.4f} "
          f"({'rejects' if top['p_vs_1'] < 0.05 else 'cannot reject'} unity)")


# ---------------------------------------------------------------------------
# 3. lag profile of one pass-through edge
# ---------------------------------------------------------------------------
def passthrough_shape(parent, child, controls=(), max_lag=8,
                      lag_choices=(4, 6, 8, 12), start=None):
    """
    Where in the lag structure a pass-through edge lives, and whether the
    summed coefficient is stable in the lag length chosen for it.
    """
    spec, smap, start = context(start)
    y = series(child, smap, start)
    x = series(parent, smap, start)
    ctrls = [series(c, smap, start) for c in controls]

    print(f'--- lag profile: {parent} -> {child} '
          f'(controls {list(controls) or "none"}, max_lag {max_lag}) ---')
    rows = distributed_lag(y, x, controls=ctrls or None, max_lag=max_lag)
    show(rows, drop=('aic', 'bic'))

    print('\n--- is the sum stable in max_lag? ---')
    out = []
    for k in lag_choices:
        r = distributed_lag(y, x, controls=ctrls or None, max_lag=k)[-1]
        out.append({'max_lag': k, 'sum_beta': r['beta'], 'se': r['se'],
                    'pval': r['pval'], 'aic': r['aic'], 'bic': r['bic'],
                    'n': r['n']})
    show(out)
    best = min(out, key=lambda r: r['bic'])
    print(f"\nBIC prefers max_lag {best['max_lag']}. Spread of the sum across "
          f"choices: {max(r['sum_beta'] for r in out) - min(r['sum_beta'] for r in out):.4f}")


# ---------------------------------------------------------------------------
# 4. is the treatment an open backdoor on a downstream edge
# ---------------------------------------------------------------------------
def direct_path_test(parent, child, controls=('us_2y',), max_horizon=12,
                     daily=False, start=None):
    """
    The treatment is an ancestor of every node. If it also has a DIRECT edge
    to the child, then for edges below layer 1 the path child <- surprise ->
    outcome is an open backdoor and the edge is biased.

    Three specs: the edge as estimated, the surprise alone, and the edge
    conditioned on the surprise. If the first and third agree, the treatment
    is not acting as a confounder here.
    """
    spec, smap, start = context(start)
    y = series(child, smap, start)
    x = series(parent, smap, start)
    ctrls = [series(c, smap, start) for c in controls]

    surprises = load_surprises(spec, start)
    surp = (pd.Series(surprises['surprise'].values,
                      index=surprises['date']).resample('MS').sum()
            .rename('surprise'))
    print(f'--- direct path: {parent} -> {child} ---')
    print(f'{len(surp)} months, {int((surp == 0).sum())} with no announcement')

    agg = None if daily else 'mean_within_month'
    dummies = not daily
    kw = dict(max_horizon=max_horizon, month_dummies=dummies,
              aggregate_parent=agg)
    specs = [
        ('1: the edge as estimated',
         local_projection(y, x, controls=ctrls or None, **kw)),
        ('2: the surprise alone, total effect',
         local_projection(y, surp, diff_x=False, max_horizon=max_horizon,
                          month_dummies=dummies)),
        ('3: the edge conditioned on the surprise',
         local_projection(y, x, controls=(ctrls + [surp]) or None,
                          diff_controls=False, **kw)),
    ]
    results = {}
    for label, rows in specs:
        print(f'\n--- spec {label} ---')
        show(rows)
        results[label[0]] = rows

    a, c = peak(results['1']), peak(results['3'])
    print('\n--- reading ---')
    if a is None:
        print('spec 1 has no significant horizon; nothing to contaminate')
        return
    same = at_horizon(results['3'], a['horizon'])
    print(f"spec 1 peak h={a['horizon']}: beta {a['beta']:.5g} (p {a['pval']:.4f})")
    print(f"spec 3 at h={a['horizon']}: beta {same['beta']:.5g} (p {same['pval']:.4f})")
    print(f"retained: {same['beta'] / a['beta']:.3f} of spec 1. Near 1.0 means "
          f"the treatment is not an open backdoor on this edge.")


# ---------------------------------------------------------------------------
# 5. is the edge downstream of its parent, or a fork from its grandparent
# ---------------------------------------------------------------------------
def mechanism_test(parent, child, grandparent=None, controls=('us_2y',),
                   max_horizon=12, focus=1, daily=False, diff_x=True,
                   diff_grandparent=True, diff_controls=True, start=None):
    """
    Whether an edge is really downstream of its parent or a fork from the node
    above it. Three specs: the edge as estimated, the same edge conditioned on
    the grandparent, and the grandparent against the child directly.

    If B holds A's coefficient, the response is carried by the parent and the
    chain runs through it. If B collapses toward zero, parent and child are
    both responding to the grandparent and the chain ends one layer earlier.

    diff_x differences the parent in A and B; diff_grandparent the regressor
    in C, which can be a level where the parent is already a growth rate;
    diff_controls every control, the grandparent in B included. All default
    True, which reproduces the test as it ran before the options existed.
    """
    spec, smap, start = context(start)
    if grandparent is None:
        grandparent, sector = grandparent_of(spec, parent)
        assert grandparent, (f'no grandparent for {parent!r} in the spec; '
                             f'pass --grandparent')
        print(f'grandparent inferred from {sector}: {grandparent}')

    y = series(child, smap, start)
    x = series(parent, smap, start)
    gp = series(grandparent, smap, start)
    ctrls = [series(c, smap, start) for c in controls]

    agg = None if daily else 'mean_within_month'
    kw = dict(max_horizon=max_horizon, month_dummies=not daily,
              aggregate_parent=agg, diff_controls=diff_controls)
    print(f'diff_x={diff_x} diff_grandparent={diff_grandparent} '
          f'diff_controls={diff_controls} daily={daily}')
    runs = [
        ('A', f'{parent} -> {child}, controls {list(controls) or "none"}',
         local_projection(y, x, controls=ctrls or None, diff_x=diff_x, **kw)),
        ('B', f'same, plus {grandparent}',
         local_projection(y, x, controls=ctrls + [gp], diff_x=diff_x, **kw)),
        ('C', f'{grandparent} -> {child} directly',
         local_projection(y, gp, controls=ctrls or None,
                          diff_x=diff_grandparent, **kw)),
    ]
    results = {}
    for tag, label, rows in runs:
        results[tag] = rows
        print(f'\n--- {tag}: {label} ---')
        show(rows)

    a, b = at_horizon(results['A'], focus), at_horizon(results['B'], focus)
    retained = b['beta'] / a['beta'] if a['beta'] else float('nan')
    print(f'\n--- A vs B at h={focus} ---')
    print(f'{"":<28}{"beta":>11}{"se":>10}{"pval":>10}{"n":>5}')
    for tag, r in (('A', a), ('B', b)):
        print(f'{tag:<28}{r["beta"]:>11.5g}{r["se"]:>10.5g}'
              f'{r["pval"]:>10.4f}{r["n"]:>5}')
    print(f'\nB keeps {retained:.1%} of A; B significant at 5%: '
          f'{b["pval"] < 0.05}')
    for tag in ('A', 'B', 'C'):
        pk = peak(results[tag])
        print(f'  {tag} peak: ' + ('none significant at 5%' if pk is None else
              f'h={pk["horizon"]} beta={pk["beta"]:.5g} p={pk["pval"]:.4f} '
              f'n={pk["n"]}'))


# ---------------------------------------------------------------------------
# 6. does the result survive rescaling the child
# ---------------------------------------------------------------------------
def scale_invariance(parent, child_a, child_b, controls=('us_2y',),
                     max_horizon=12, daily=False, start=None):
    """
    The same edge with the child measured two ways - a flow against a growth
    rate, say. A pure rescaling leaves every p-value unchanged and moves only
    the coefficient. If the p-values move, the two are not the same test and
    the choice of scaling is a modelling decision, not a presentation one.
    """
    spec, smap, start = context(start)
    x = series(parent, smap, start)
    ctrls = [series(c, smap, start) for c in controls]
    agg = None if daily else 'mean_within_month'
    kw = dict(controls=ctrls or None, max_horizon=max_horizon,
              month_dummies=not daily, aggregate_parent=agg)

    results = {}
    for tag, child in (('A', child_a), ('B', child_b)):
        rows = local_projection(series(child, smap, start), x, **kw)
        results[tag] = rows
        print(f'\n--- {tag}: {parent} -> {child} ---')
        show(rows)

    print(f'\n--- A vs B across horizons ---')
    print(f'{"h":>3}{"beta_A":>13}{"p_A":>9}{"beta_B":>13}{"p_B":>9}'
          f'{"bA/bB":>11}{"|dp|":>9}')
    worst = 0.0
    for a, b in zip(results['A'], results['B']):
        gap = abs(a['pval'] - b['pval'])
        worst = max(worst, gap)
        ratio = a['beta'] / b['beta'] if b['beta'] else float('nan')
        print(f'{a["horizon"]:>3}{a["beta"]:>13.5g}{a["pval"]:>9.4f}'
              f'{b["beta"]:>13.5g}{b["pval"]:>9.4f}{ratio:>11.1f}{gap:>9.4f}')
    print(f'\nlargest p-value gap: {worst:.4f}. Below ~0.001 the two are the '
          f'same test rescaled; above it they are different specifications.')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    """One subcommand per investigation."""
    p = argparse.ArgumentParser(
        prog='diagnostics',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)

    def common(q, daily=False):
        q.add_argument('--controls', nargs='*', default=['us_2y'],
                       help='control node names (default: us_2y)')
        q.add_argument('--max-horizon', type=int, default=12)
        q.add_argument('--start', default=None,
                       help='override the sample start')
        if daily:
            q.add_argument('--daily', action='store_true',
                           help='leave the parent at its native frequency and '
                                'drop month dummies (default: aggregate to '
                                'monthly means with month dummies)')

    q = sub.add_parser('alignment_check', help='yields move at t or t+1')
    q.add_argument('--node', default='goc_2y')
    q.add_argument('--offsets', nargs='*', type=int, default=[0, 1])
    q.add_argument('--start', default=None)

    q = sub.add_parser('term_structure', help='beta across 3M to 10Y')
    q.add_argument('--nodes', nargs='*', default=None,
                   help='default: every layer-1 candidate in the spec')
    q.add_argument('--start', default=None)

    q = sub.add_parser('passthrough_shape', help='lag profile for one edge')
    q.add_argument('--parent', required=True)
    q.add_argument('--child', required=True)
    q.add_argument('--controls', nargs='*', default=[])
    q.add_argument('--max-lag', type=int, default=8)
    q.add_argument('--lag-choices', nargs='*', type=int, default=[4, 6, 8, 12])
    q.add_argument('--start', default=None)

    q = sub.add_parser('direct_path_test',
                       help='is the treatment a confounder of this edge')
    q.add_argument('--parent', required=True)
    q.add_argument('--child', required=True)
    common(q, daily=True)

    q = sub.add_parser('mechanism_test',
                       help='does the edge survive controlling for its grandparent')
    q.add_argument('--parent', required=True)
    q.add_argument('--child', required=True)
    q.add_argument('--grandparent', default=None,
                   help='default: read off graph_spec.yaml')
    q.add_argument('--focus', type=int, default=1,
                   help='horizon to compare A and B at (default 1)')
    q.add_argument('--no-diff-x', dest='diff_x', action='store_false',
                   help='the parent is already a difference; do not '
                        'difference it again in A and B')
    q.add_argument('--no-diff-grandparent', dest='diff_grandparent',
                   action='store_false',
                   help='the grandparent is already a difference')
    q.add_argument('--no-diff-controls', dest='diff_controls',
                   action='store_false',
                   help='the controls, grandparent included, are already '
                        'differences')
    common(q, daily=True)

    q = sub.add_parser('scale_invariance',
                       help='flow vs growth on the same edge')
    q.add_argument('--parent', required=True)
    q.add_argument('--child-a', required=True)
    q.add_argument('--child-b', required=True)
    common(q, daily=True)
    return p


COMMANDS = {
    'alignment_check': alignment_check,
    'term_structure': term_structure,
    'passthrough_shape': passthrough_shape,
    'direct_path_test': direct_path_test,
    'mechanism_test': mechanism_test,
    'scale_invariance': scale_invariance,
}


def main(argv=None):
    args = vars(build_parser().parse_args(argv))
    command = args.pop('command')
    if 'controls' in args:
        args['controls'] = tuple(args['controls'])
    if 'offsets' in args:
        args['offsets'] = tuple(args['offsets'])
    if 'lag_choices' in args:
        args['lag_choices'] = tuple(args['lag_choices'])
    COMMANDS[command](**args)


if __name__ == '__main__':
    main()
