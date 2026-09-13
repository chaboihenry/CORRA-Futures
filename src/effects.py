import pandas as pd
import numpy as np
import statsmodels.api as sm


def shock_scale(series, quantile=None):
    """Typical shock size: std of series, or |value| at a given quantile."""
    if quantile is None:
        return float(series.std())
    return float(abs(series.quantile(quantile)))

def chain_effect(betas, ses, shock):
    """Propagate a shock through a chain of edges via product of betas, with delta-method SE."""
    assert len(betas) == len(ses), f'betas/ses length mismatch: {len(betas)} vs {len(ses)}'
    for i, b in enumerate(betas):
        assert b != 0, f'beta at index {i} is zero: {b}'
    product = np.prod(betas)
    effect = shock * product
    se = abs(product) * np.sqrt(sum((se_i / b) ** 2 for b, se_i in zip(betas, ses))) * shock
    return {
        'effect':  effect,
        'se':      se,
        'ci_low':  effect - 1.96 * se,
        'ci_high': effect + 1.96 * se,
        'n_edges': len(betas),
    }

def variance_screen(y, x, aggregate='MS', threshold=0.5, scale_y=1.0):
    """
    Ratio of outcome SD to parent-change SD; below ~0.5 the outcome is too
    smooth to estimate. Calibration: flows give ~1.7, credit stocks ~0.02.

    y is never differenced - a child that is already a return or a growth rate
    enters as it stands, and only the parent is differenced. What does need
    care is the SCALE of the two sides. to_bp scales a node catalogued in
    percent into bp but leaves one catalogued in pp alone, so a pp child is
    compared against a bp parent change and reads 100x smoother than it is.
    scale_y is that missing factor; screen_scale in causal_estimates derives
    it from the two nodes' units.
    """
    yn = y.name if y.name is not None else 'y'
    xn = x.name if x.name is not None else 'x'
    assert not y.empty, f'{yn} is empty'
    assert not x.empty, f'{xn} is empty'
    dx = x.resample(aggregate).mean().diff().dropna()
    ya = y.dropna()
    assert not dx.empty, f'{xn} is empty after aggregating to {aggregate} and differencing'
    overlap = ya.index.intersection(dx.index)
    assert len(overlap) >= 12, f'{yn} and {xn} overlap on {len(overlap)} periods, need 12'
    sd_y = float(ya.std()) * scale_y
    sd_dx = float(dx.std())
    assert sd_dx > 0, f'{xn} has zero variance after differencing'
    ratio = sd_y / sd_dx
    return {
        'sd_y':  sd_y,
        'sd_dx': sd_dx,
        'ratio': ratio,
        'pass':  ratio >= threshold,
        'n_y':   int(len(ya)),
        'n_dx':  int(len(dx)),
    }
