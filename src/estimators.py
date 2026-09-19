"""
The three regression estimators causal_estimates.py dispatches edges to:
pass_through, distributed_lag, local_projection. Take two Series and a
spec, know nothing of graph_spec or stakeholders. Produce lists of per-lag/horizon result dicts.
"""
import pandas as pd
import numpy as np
import statsmodels.api as sm


def pass_through(y, x, max_lag=8):
    """Regress dy on dx at lags 0..max_lag. Returns one dict per lag."""
    xa = x.reindex(y.index, method='ffill')
    out = []
    for k in range(max_lag + 1):
        d = pd.DataFrame({'dy': y.diff().shift(-k), 'dx': xa.diff()}).dropna()
        X = sm.add_constant(d['dx'])
        fit = sm.OLS(d['dy'], X).fit(cov_type='HAC', cov_kwds={'maxlags': 4})
        out.append({
        'lag':  k,
        'beta': fit.params.iloc[1],
        'se':   fit.bse.iloc[1],
        'pval': fit.pvalues.iloc[1],
        'r2':   fit.rsquared,
        'n':    int(fit.nobs),
    })
    return out 

def distributed_lag(y, x, controls=None, max_lag=8, diff_y=True):
    """
    Regress dy on dx at lags 0..max_lag jointly. One dict per lag plus the
    sum. Set diff_y=False when the child is already a difference or a
    return - see docs/methodology.md for why double-differencing nulls the sum.
    """
    xa = x.reindex(y.index, method='ffill')
    dy = y.diff() if diff_y else y
    dx = xa.diff()
    cols = {'dy': dy}
    for k in range(max_lag + 1):
        cols[f'dx_{k}'] = dx.shift(k)
    ctrl_names = []
    if controls is not None:
        for c in controls:
            cn = f'ctl_{c.name}'
            cols[cn] = c.reindex(y.index, method='ffill').diff()
            ctrl_names.append(cn)
    d = pd.DataFrame(cols).dropna()
    names = [f'dx_{k}' for k in range(max_lag + 1)]
    X = sm.add_constant(d[names + ctrl_names])
    fit = sm.OLS(d['dy'], X).fit(cov_type='HAC', cov_kwds={'maxlags': max_lag})
    expr = ' + '.join(names) + ' = 0'
    t = fit.t_test(expr)
    out = []
    for k in range(max_lag + 1):
        name = f'dx_{k}'
        out.append({
            'lag':  k,
            'beta': fit.params[name],
            'se':   fit.bse[name],
            'pval': fit.pvalues[name],
            'r2':   fit.rsquared,
            'n':    int(fit.nobs),
        })
    for cn in ctrl_names:
        out.append({
            'lag':  cn,
            'beta': fit.params[cn],
            'se':   fit.bse[cn],
            'pval': fit.pvalues[cn],
            'r2':   fit.rsquared,
            'n':    int(fit.nobs),
        })
    out.append({
        'lag':  'sum',
        'beta': float(t.effect.item()),
        'se':   float(t.sd.item()),
        'pval': float(t.pvalue),
        'r2':   fit.rsquared,
        'n':    int(fit.nobs),
        'aic': fit.aic,
        'bic': fit.bic,
    })
    return out

def local_projection(y, x,
                    controls=None,
                    max_horizon=12,
                    month_dummies=False,
                    aggregate_parent=None,
                    diff_x=True,
                    diff_controls=True,
                    resample_child=None
                ):
    """
    Regress y at horizons 0..max_horizon on dx. One dict per horizon.
    resample_child is a resample alias ('W-FRI', ...) applied to y and its
    controls, aligning a daily child onto a weekly parent's grid so the join keeps their shared dates.
    """
    if resample_child:
        y = y.resample(resample_child).mean().dropna()
        assert not y.empty, f'child empty after resampling to {resample_child}'
    xa = x.resample('MS').mean() if aggregate_parent else x
    dx = xa.diff() if diff_x else xa

    ctrl = {}
    if controls is not None:
        for c in controls:
            # see docs/methodology.md, "Bugs found and what they cost"
            if resample_child:
                cc = c.resample(resample_child).mean()
            elif aggregate_parent:
                cc = c.resample('MS').mean()
            else:
                cc = c.reindex(dx.index, method='ffill')
            cc = cc.diff() if diff_controls else cc
            ctrl[f'ctl_{c.name}'] = cc
    ctrl_names = list(ctrl)

    out = []
    for h in range(max_horizon + 1):
        dy = y.shift(-h)
        if ctrl_names:
            n_unc = len(pd.DataFrame({'y': dy, 'dx': dx}).dropna())
        d = pd.DataFrame({'y': dy, 'dx': dx, **ctrl}).dropna()
        if ctrl_names:
            assert len(d) >= 0.5 * n_unc, (
                f'{x.name} -> {y.name}, h={h}: control(s) {ctrl_names} '
                f'collapsed the sample from {n_unc} (uncontrolled) to '
                f'{len(d)} (controlled) - likely a frequency mismatch '
                f'between a control and this edge')
        names = ['dx']
        if month_dummies:
            dm = pd.get_dummies(d.index.month, prefix='m',
                                drop_first=True).astype(float)
            dm.index = d.index
            d = pd.concat([d, dm], axis=1)
            names += list(dm.columns)
        X = sm.add_constant(d[names + ctrl_names])
        fit = sm.OLS(d['y'], X).fit(cov_type='HAC', cov_kwds={'maxlags': h + 1})
        out.append({
            'horizon': h,
            'beta': fit.params['dx'],
            'se':   fit.bse['dx'],
            'pval': fit.pvalues['dx'],
            'r2':   fit.rsquared,
            'n':    int(fit.nobs),
        })
    return out