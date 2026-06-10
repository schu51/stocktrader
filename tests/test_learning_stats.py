import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np


def test_ols_recovers_known_slope():
    # y = 2*x1 + 0*x2 + noise; expect coef[x1] strong, coef[x2] ~ 0
    from learning_stats import ols_fit
    rng = np.random.default_rng(0)
    n = 200
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = 2.0 * x1 + rng.normal(0, 0.5, n)
    X = np.column_stack([x1, x2])
    result = ols_fit(X, y)
    assert result["coef"][0] > 1.5
    assert abs(result["coef"][1]) < 0.5
    assert abs(result["t"][0]) > 2.05
    assert abs(result["t"][1]) < 2.05


def test_ols_handles_intercept():
    from learning_stats import ols_fit
    rng = np.random.default_rng(1)
    n = 100
    x = rng.normal(0, 1, n)
    y = 5.0 + 3.0 * x + rng.normal(0, 0.3, n)
    X = x.reshape(-1, 1)
    result = ols_fit(X, y)
    assert result["coef"][0] > 2.5
    assert result["intercept"] > 4.0


def test_zscore_standardizes():
    from learning_stats import zscore
    x = np.array([10.0, 20.0, 30.0, 40.0])
    z = zscore(x)
    assert abs(z.mean()) < 1e-9
    assert abs(z.std() - 1.0) < 1e-9


def test_zscore_constant_column():
    from learning_stats import zscore
    x = np.array([5.0, 5.0, 5.0])
    z = zscore(x)
    assert np.all(z == 0.0)


def test_derive_weights_both_significant():
    from learning_stats import derive_weights
    w_rs, w_thesis = derive_weights(coef_rs=0.8, t_rs=3.0, coef_thesis=0.4, t_thesis=2.5)
    assert abs(w_rs - 0.667) < 0.01
    assert abs(w_thesis - 0.333) < 0.01
    assert abs((w_rs + w_thesis) - 1.0) < 1e-9


def test_derive_weights_one_significant_floors_other():
    from learning_stats import derive_weights
    w_rs, w_thesis = derive_weights(coef_rs=0.9, t_rs=4.0, coef_thesis=0.5, t_thesis=1.0)
    assert abs(w_thesis - 0.10) < 1e-9
    assert abs(w_rs - 0.90) < 1e-9


def test_derive_weights_neither_significant_returns_none():
    from learning_stats import derive_weights
    result = derive_weights(coef_rs=0.2, t_rs=1.0, coef_thesis=0.1, t_thesis=0.5)
    assert result is None
