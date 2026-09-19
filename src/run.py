"""
CLI entry point: runs screen_all, estimate_both and chain_both for the whole
graph and writes results/tables/. Reads config/*.yaml via causal_estimates.py;
produces variance_screen.csv, candidate_screen.csv, control_audit.csv,
edges.csv and chains.csv.
"""
import argparse
import shutil
import tempfile
from pathlib import Path

import causal_estimates as ce
from nodes import snapshot_dir

REAL_TABLES = ce.TABLES


def run_all(snapshot=None, write=True):
    """Runs the five pipeline stages, redirecting their output when write=False so results/tables/ stays untouched."""
    spec = ce.load_graph_spec()
    smap = ce.load_series_map()
    scratch = None if write else Path(tempfile.mkdtemp())
    ce.TABLES = REAL_TABLES if write else scratch
    try:
        screen = ce.screen_all(spec, smap, snapshot=snapshot)
        candidates = ce.screen_candidates(spec, smap, snapshot=snapshot)
        audit = ce.reconcile_controls(spec, smap)
        edges, layer1_c, layer1_u = ce.estimate_both(spec, smap, snapshot=snapshot, write=write)
        chains = ce.chain_both(spec, edges, smap, layer1_c, layer1_u, write=write)
    finally:
        ce.TABLES = REAL_TABLES
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    return screen, candidates, audit, edges, chains


def print_tables(screen, candidates, audit, edges, chains):
    """Mirrors causal_estimates.main()'s own print format for the five tables."""
    print('--- variance screen ---')
    print(screen.round(3).to_string(index=False))
    print('\n--- candidate screen ---')
    print(candidates.round(3).to_string(index=False))
    print('\n--- control audit ---')
    print(audit.to_string(index=False))
    print('\n--- edges ---')
    print(edges.round(4).to_string(index=False))
    print('\n--- chains ---')
    print(chains.round(3).to_string(index=False))


def build_parser():
    """The two flags this entry point takes: --snapshot and --no-write."""
    p = argparse.ArgumentParser(
        prog='run',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--snapshot', default=None,
                   help='reproduce a frozen data/processed/snapshot_<name>/ instead of fetching live')
    p.add_argument('--no-write', dest='write', action='store_false',
                   help='print the tables without touching results/tables/')
    return p


def bare_date(snapshot):
    """--snapshot may be given as the bare date or the full snapshot_<date> directory name."""
    return snapshot[len('snapshot_'):] if snapshot.startswith('snapshot_') else snapshot


def main(argv=None):
    """CLI entry point: validate a named snapshot up front, then run and print."""
    args = build_parser().parse_args(argv)
    snapshot = bare_date(args.snapshot) if args.snapshot else None
    if snapshot:
        path = snapshot_dir(snapshot)
        assert path.exists(), f'{path}: no such snapshot directory'
    tables = run_all(snapshot=snapshot, write=args.write)
    print_tables(*tables)


if __name__ == '__main__':
    main()
