"""
Renders the estimated graph as results/figures/dag.svg. Reads
graph_spec.yaml, series_map.yaml and results/tables/edges.csv; hardcodes no
number of its own. Produces one SVG via graphviz.
"""
import re
import yaml
import pandas as pd
import graphviz
from pathlib import Path

CONFIG = Path(__file__).parent.parent / 'config'
TABLES = Path(__file__).parent.parent / 'results' / 'tables'
FIGURES = Path(__file__).parent.parent / 'results' / 'figures'

TREATMENT = 'cra_policy_surprise'
SNAPSHOT = 'snapshot_2026-09-16'
ALPHA = 0.05

# one edge colour per sector; edges carry sector identity, not nodes, so a node's own owner: field never has to agree
SECTOR_COLOUR = {
    'central_governments': '#4C72B0',
    'banks':                '#DD8452',
    'households':           '#55A868',
    'nfcs':                 '#C44E52',
    'central_banks':        '#8172B2',
    'asset_managers':       '#937860',
    'hedge_funds':          '#CCB974',
    'general_banking':      '#64B5CD',
}

# amber, not red: a wrong-signed edge is a flagged finding, not a broken estimate; overrides the sector colour
WRONG_SIGN_COLOUR = '#B8860B'

# muted grey so confounder/control edges and nodes read as background, not competing with the chain edges
CONFOUNDER_COLOUR = '#B0B0B0'

NEUTRAL_FILL = '#EAEAEA'
NEUTRAL_BORDER = '#555555'

# the two edges graph_spec.yaml's cross_cutting_finding names as contradicting their mechanism, not inferred from betas
WRONG_SIGN_EDGES = {
    ('tbill_3m', 'tbill_outstanding_growth'),
    ('nfc_nonmortgage_growth', 'business_insolvencies_growth'),
}

# the two sectors whose controlled chain carries one of WRONG_SIGN_EDGES, flagged again on their legend effect
WRONG_SIGN_SECTORS = {'central_governments', 'nfcs'}

# a rejected candidate stale in us_2y's parent_of; dropped from rendering, reported rather than fixed in graph_spec.yaml
STALE_CONFOUNDER_TARGETS = {'bank_funding_spread'}


def load_spec():
    """graph_spec.yaml as a dict."""
    with open(CONFIG / 'graph_spec.yaml') as f:
        return yaml.safe_load(f)


def load_smap():
    """series_map.yaml as a dict."""
    with open(CONFIG / 'series_map.yaml') as f:
        return yaml.safe_load(f)


def load_edges():
    """results/tables/edges.csv, keyed by (parent, child) -> row."""
    df = pd.read_csv(TABLES / 'edges.csv')
    out = {}
    for _, row in df.iterrows():
        out[(row['parent'], row['child'])] = row
    return out


def load_chains():
    """results/tables/chains.csv, keyed by sector -> row."""
    df = pd.read_csv(TABLES / 'chains.csv')
    return {row['sector']: row for _, row in df.iterrows()}


def node_label(name, smap):
    """'name (unit)', unit from edge_unit's to_bp scaling (a percent-catalogued
    node reads bp). The treatment is not a catalogued node, so it is labelled
    by hand as bp."""
    if name == TREATMENT:
        return f'{name} (bp, treatment)'
    return f'{name} ({edge_unit(name, smap)})'


def edge_unit(name, smap):
    """A node's effective unit for an edge label: to_bp's own percent->bp
    scaling applies before estimation, so a percent-catalogued node reads bp here."""
    if name == TREATMENT:
        return 'bp'
    units = smap[name].get('units')
    assert units, f'{name}: no units recorded in series_map.yaml'
    return 'bp' if units == 'percent' else units


def node_layers(spec):
    """
    node -> layer number, read off every sector's layers list. Several nodes
    are `selected` by more than one sector; this asserts they agree on layer
    number rather than assuming it, so rank=same groups them correctly.
    """
    out = {}
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        for layer in block['layers']:
            node = layer.get('selected')
            if node is None:
                continue
            layer_num = layer['layer']
            if node in out:
                assert out[node] == layer_num, (
                    f'{node}: layer number disagrees across sectors, '
                    f'{out[node]} (seen first) vs {layer_num} ({sector})')
            else:
                out[node] = layer_num
    return out


def all_chain_edges(spec):
    """
    (parent, child) -> list of sectors selecting this edge, root edge
    included via a synthetic [cra_policy_surprise] + layers walk per sector.
    Only layer 1 is shared by more than one sector.
    """
    out = {}
    for sector, block in spec.items():
        if not isinstance(block, dict) or 'layers' not in block:
            continue
        chain = [{'selected': TREATMENT}] + block['layers']
        for prev, cur in zip(chain, chain[1:]):
            parent, child = prev.get('selected'), cur.get('selected')
            if parent is None or child is None:
                continue
            out.setdefault((parent, child), []).append(sector)
    return out


def edge_style(parent, child, row, sector_colour, unit_suffix):
    """
    (style, colour, label, note) for one edge, reading only the CONTROLLED
    columns of edges.csv. colour is the sector's colour, overridden to amber
    for the two wrong-signed edges regardless of significance.
    """
    wrong_sign = (parent, child) in WRONG_SIGN_EDGES
    colour = WRONG_SIGN_COLOUR if wrong_sign else sector_colour

    if row is None:
        return 'dashed', colour, '', 'no row in edges.csv'

    status = row['status']
    if status != 'ok':
        return 'dashed', colour, status, None

    beta, se, pval = row['beta'], row['se'], row['pval']
    if pd.isna(pval) or pval >= ALPHA:
        return 'dashed', colour, 'not_significant', None

    label = f'{beta:.3g} ({se:.3g}) {unit_suffix}'
    if wrong_sign:
        return 'solid', colour, f'{label} ⚩', 'wrong-signed vs mechanism'
    return 'solid', colour, label, None


def build():
    """Assembles the graphviz Digraph and renders it. Returns (out_path, report, dot)."""
    spec = load_spec()
    smap = load_smap()
    edges = load_edges()
    chains = load_chains()

    dot = graphviz.Digraph('dag', format='svg')
    dot.attr(rankdir='LR', fontsize='11', fontname='Helvetica')
    dot.attr('node', fontname='Helvetica', fontsize='10')
    dot.attr('edge', fontname='Helvetica', fontsize='9')

    added = set()
    node_rank = {}
    report = {'missing_rows': [], 'not_significant': [], 'wrong_signed': [],
              'stale_confounder_targets': [], 'n_nodes': 0, 'n_edges': 0}

    def add_node(name, rank, dashed=False):
        if name in added:
            return
        added.add(name)
        node_rank[name] = rank
        dot.node(name, label=node_label(name, smap), shape='box',
                 style='dashed' if dashed else 'filled,solid',
                 fillcolor='white' if dashed else NEUTRAL_FILL,
                 color=CONFOUNDER_COLOUR if dashed else NEUTRAL_BORDER,
                 penwidth='1.2')
        report['n_nodes'] += 1

    # root
    add_node(TREATMENT, 0)

    # chain edges, layer 1 (root) included
    layers = node_layers(spec)
    for (parent, child), sectors in all_chain_edges(spec).items():
        rank = 1 if parent == TREATMENT else layers[child]
        add_node(child, rank)
        # the parent already exists: see all_chain_edges' docstring
        row = edges.get((parent, child))
        unit_suffix = f'{edge_unit(parent, smap)}->{edge_unit(child, smap)}'
        for sector in sectors:
            colour = SECTOR_COLOUR[sector]
            style, edge_colour, label, note = edge_style(parent, child, row, colour, unit_suffix)
            dot.edge(parent, child, label=label, style=style,
                     color=edge_colour, fontcolor=edge_colour)
            report['n_edges'] += 1
        if row is None:
            report['missing_rows'].append((parent, child))
        elif style == 'dashed':
            report['not_significant'].append((parent, child, label))
        if note == 'wrong-signed vs mechanism':
            report['wrong_signed'].append((parent, child))

    # confounders, from exogenous_nodes
    # cra_policy_surprise is skipped here - it is already drawn as the root
    for name, entry in (spec.get('exogenous_nodes') or {}).items():
        if name == TREATMENT:
            continue
        add_node(name, None, dashed=True)
        for child in entry.get('parent_of') or []:
            if child in STALE_CONFOUNDER_TARGETS:
                report['stale_confounder_targets'].append((name, child))
                continue
            if child not in added:
                # a parent_of target outside the estimated chains, rendered since it is not known-stale
                add_node(child, None)
            # constraint=false: a confounder's own rank must not distort the chain layers it points into
            dot.edge(name, child, style='dashed', color=CONFOUNDER_COLOUR,
                     constraint='false')
            report['n_edges'] += 1

    # rank=same per layer
    by_rank = {}
    for name, r in node_rank.items():
        if r is None:
            continue
        by_rank.setdefault(r, []).append(name)
    for r, names in by_rank.items():
        with dot.subgraph() as s:
            s.attr(rank='same')
            for n in names:
                s.node(n)

    # legend: one row per sector, plus the edge styles
    used_sectors = sorted({s for sectors in all_chain_edges(spec).values() for s in sectors})
    with dot.subgraph(name='cluster_legend') as leg:
        leg.attr(label='Legend', fontsize='11', style='dashed', color='#999999', rankdir='TB')
        leg.attr('node', shape='point', width='0.01', style='invis')
        for sector in used_sectors:
            colour = SECTOR_COLOUR[sector]
            a, b = f'legend_{sector}_a', f'legend_{sector}_b'
            leg.node(a)
            leg.node(b)
            crow = chains.get(sector)
            flag = ' ⚩' if sector in WRONG_SIGN_SECTORS else ''
            if crow is None or crow['status'] != 'ok':
                status = crow['status'] if crow is not None else 'not estimated'
                effect = f'status: {status.split(":")[0]}'
            else:
                effect = (f"{crow['effect_1sd']:.3g} [{crow['ci_low']:.3g}, "
                          f"{crow['ci_high']:.3g}]{flag}")
            leg.edge(a, b, color=colour, fontcolor=colour, penwidth='2',
                     label=f'{sector}: {effect}')
        leg.node('legend_note', shape='plaintext', style='', label=(
            'edge colour: the selecting sector (see rows above)\\l'
            'edge style (controlled column of edges.csv):\\l'
            '  solid + "beta (se) unit->unit"  = estimated, significant at 5% -\\l'
            '                                    unit is bp for a percent-catalogued\\l'
            '                                    node (to_bp\'s scaling), else its own unit\\l'
            '  dashed + status text            = not significant / no_significant_horizon\\l'
            f'  amber + ⚩                      = significant but wrong-signed vs recorded\\l'
            '                                    mechanism - a flagged finding, not an error\\l'
            '  muted grey, dashed              = control / confounder (exogenous_nodes),\\l'
            '                                    constraint=false so it does not affect layer rank\\l'
            'edge labels are PER-EDGE beta (se), not cumulative; a chain\'s total effect is\\l'
            '  the PRODUCT of its edges - see results/tables/chains.csv, not this figure\\l'
            'sector row: name: controlled effect_1sd [ci_low, ci_high] from chains.csv -\\l'
            '  asset_managers is broken_link controlled, so its status is shown instead;\\l'
            f'  central_governments and nfcs repeat ⚩, since their chain contains a\\l'
            '  wrong-signed edge and their effect is not a clean transmission result\\l'
            f'estimates frozen against data/processed/{SNAPSHOT}\\l'
        ))

    FIGURES.mkdir(parents=True, exist_ok=True)
    out_path = dot.render(filename='dag', directory=str(FIGURES), cleanup=True)
    return out_path, report, dot


def svg_dimensions(path):
    """(width, height) in points, parsed from the rendered SVG's own header."""
    text = Path(path).read_text(encoding='utf-8')
    m = re.search(r'width="(\d+)pt" height="(\d+)pt"', text)
    assert m, f'{path}: no width/height found in SVG header'
    return int(m.group(1)), int(m.group(2))


def main():
    """CLI entry point: build the figure and print its counts and flagged edges."""
    out_path, report, _ = build()
    width, height = svg_dimensions(out_path)
    print(f'wrote {out_path}')
    print(f'dimensions: {width}pt x {height}pt')
    print(f"nodes: {report['n_nodes']}, edges: {report['n_edges']}")
    print(f"missing rows in edges.csv: {report['missing_rows'] or 'none'}")
    print(f"not significant (dashed): {report['not_significant']}")
    print(f"wrong-signed (amber): {report['wrong_signed']}")
    print(f"stale parent_of targets dropped: {report['stale_confounder_targets']}")


if __name__ == '__main__':
    main()
