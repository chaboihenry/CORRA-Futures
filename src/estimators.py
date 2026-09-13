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
    Regress dy on dx at lags 0..max_lag jointly. One dict per lag plus the sum.

    Set diff_y=False when the child is already a difference or a return.
    Differencing it again makes the lag polynomial sum to zero by
    construction - the response appears at lag 0 and reverses at lag 1 - so
    the summed effect is null regardless of the true response, and headline()
    must not read the sum row for such an edge.
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

    resample_child is a resample alias ('W-FRI', ...) applied to y, and to the
    controls with it. The frame is joined on dates, so a parent stamped weekly
    against a child stamped daily keeps only the dates both carry; aligning the
    child onto the parent's grid is what makes such an edge estimable.
    """
    if resample_child:
        y = y.resample(resample_child).mean().dropna()
        assert not y.empty, f'child empty after resampling to {resample_child}'
    xa = x.resample('MS').mean() if aggregate_parent else x
    dx = xa.diff() if diff_x else xa

    ctrl = {}
    if controls is not None:
        for c in controls:
            if resample_child:
                # a control has to sit on the child's grid, or the join drops
                # every date where only one of the two is stamped
                cc = c.resample(resample_child).mean()
                cc = cc.diff() if diff_controls else cc
            else:
                cc = c.resample('MS').mean().diff() if diff_controls else c
            ctrl[f'ctl_{c.name}'] = cc
    ctrl_names = list(ctrl)

    out = []
    for h in range(max_horizon + 1):
        d = pd.DataFrame({'y': y.shift(-h), 'dx': dx, **ctrl}).dropna()
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