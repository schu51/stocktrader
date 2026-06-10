"""
Learning Stats
==============
Pure functions for the learning agent: OLS regression, significance testing,
and weight derivation. No I/O, no state — fully unit-testable with synthetic data.

Uses numpy only (no scipy/statsmodels). The significance threshold is a
conservative t-critical of 2.05, valid for df >= 27 (guaranteed by the agent's
>= 30 sample-size gate).
"""

import numpy as np
from typing import Dict, Optional, Tuple

T_CRITICAL = 2.05   # two-sided p<0.05 for df>=27 (conservative)
WEIGHT_FLOOR = 0.10  # a feature is never weighted below this once in the model


def zscore(x: np.ndarray) -> np.ndarray:
    """Standardize to mean 0, std 1. Constant columns return all zeros."""
    x = np.asarray(x, dtype=float)
    sd = x.std()
    if sd == 0:
        return np.zeros_like(x)
    return (x - x.mean()) / sd


def ols_fit(X: np.ndarray, y: np.ndarray) -> Dict:
    """
    Ordinary least squares with an intercept.

    Args:
        X: (n, k) design matrix of predictors (no intercept column)
        y: (n,) outcome vector

    Returns:
        {
            "intercept": float,
            "coef": np.ndarray (k,),   # slope per predictor
            "se":   np.ndarray (k,),   # standard error per predictor
            "t":    np.ndarray (k,),   # t-statistic per predictor
            "n":    int,
        }
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)

    n, k = X.shape
    Xd = np.column_stack([np.ones(n), X])     # (n, k+1)

    XtX = Xd.T @ Xd
    XtX_inv = np.linalg.inv(XtX)
    beta = XtX_inv @ Xd.T @ y                  # (k+1,)

    residuals = y - Xd @ beta
    rss = float(residuals @ residuals)
    dof = n - (k + 1)
    sigma2 = rss / dof if dof > 0 else float("inf")

    var_beta = sigma2 * np.diag(XtX_inv)       # (k+1,)
    se = np.sqrt(np.maximum(var_beta, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, beta / se, 0.0)

    return {
        "intercept": float(beta[0]),
        "coef": beta[1:],
        "se":   se[1:],
        "t":    t[1:],
        "n":    n,
    }


def derive_weights(
    coef_rs: float, t_rs: float,
    coef_thesis: float, t_thesis: float,
) -> Optional[Tuple[float, float]]:
    """
    Convert regression coefficients into (w_rs, w_thesis) summing to 1.

    Significance is |t| > T_CRITICAL.
      - both significant → proportional to |coef|, normalized
      - one significant  → significant feature gets (1 - floor), other = floor
      - neither          → None (no change)
    """
    rs_sig     = abs(t_rs) > T_CRITICAL
    thesis_sig = abs(t_thesis) > T_CRITICAL

    if rs_sig and thesis_sig:
        a, b = abs(coef_rs), abs(coef_thesis)
        total = a + b
        if total == 0:
            return None
        return (a / total, b / total)
    elif rs_sig:
        return (1.0 - WEIGHT_FLOOR, WEIGHT_FLOOR)
    elif thesis_sig:
        return (WEIGHT_FLOOR, 1.0 - WEIGHT_FLOOR)
    else:
        return None
