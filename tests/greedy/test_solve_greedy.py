# -*- coding: utf-8 -*-
"""
Linear solve / likelihood tests.

"""
import starry
import numpy as np
from scipy.linalg import cho_solve
from scipy.stats import multivariate_normal
import pytest
import itertools

# Parameter combinations we'll test
vals = ["scalar", "vector", "matrix", "cholesky"]
woodbury = [False, True]
solve_inputs = itertools.product(vals, vals)
lnlike_inputs = itertools.product(vals, vals, woodbury)

# Instantiate a dipole map
map = starry.Map(ydeg=1, reflected=True)
inc_true = 60
y_true = [0.1, 0.2, 0.3]
map[1, :] = y_true
map.inc = inc_true

# Generate a synthetic light curve with just a little noise
theta = np.linspace(0, 360, 100)
phi = 3.5 * theta
xo = np.cos(phi * np.pi / 180)
yo = 0.1 * np.cos(phi * np.pi / 180)
zo = np.sin(phi * np.pi / 180)
kwargs = dict(theta=theta, xo=xo, yo=yo, zo=zo)
flux = map.flux(**kwargs)
sigma = 1e-5
np.random.seed(1)
flux += np.random.randn(len(theta)) * sigma


@pytest.mark.parametrize("L,C", solve_inputs)
def test_solve(L, C):
    # Place a generous prior on the map coefficients
    if L == "scalar":
        map.set_prior(L=1)
    elif L == "vector":
        map.set_prior(L=np.ones(map.Ny - 1))
    elif L == "matrix":
        map.set_prior(L=np.eye(map.Ny - 1))
    elif L == "cholesky":
        map.set_prior(cho_L=np.eye(map.Ny - 1))

    # Provide the dataset
    if C == "scalar":
        map.set_data(flux, C=sigma ** 2)
    elif C == "vector":
        map.set_data(flux, C=np.ones(len(flux)) * sigma ** 2)
    elif C == "matrix":
        map.set_data(flux, C=np.eye(len(flux)) * sigma ** 2)
    elif C == "cholesky":
        map.set_data(flux, cho_C=np.eye(len(flux)) * sigma)

    # Solve the linear problem
    map.inc = inc_true
    mu, cho_cov = map.solve(**kwargs)

    # Ensure the likelihood of the true value is close to that of
    # the MAP solution
    cov = cho_cov.dot(cho_cov.T)
    LnL0 = multivariate_normal.logpdf(mu, mean=mu, cov=cov)
    LnL = multivariate_normal.logpdf([0.1, 0.2, 0.3], mean=mu, cov=cov)
    assert LnL0 - LnL < 5.00


@pytest.mark.parametrize("L,C,woodbury", lnlike_inputs)
def test_lnlike(L, C, woodbury):
    """Test the log marginal likelihood method."""
    # Place a generous prior on the map coefficients
    if L == "scalar":
        map.set_prior(L=1)
    elif L == "vector":
        map.set_prior(L=np.ones(map.Ny - 1))
    elif L == "matrix":
        map.set_prior(L=np.eye(map.Ny - 1))
    elif L == "cholesky":
        map.set_prior(cho_L=np.eye(map.Ny - 1))

    # Provide the dataset
    if C == "scalar":
        map.set_data(flux, C=sigma ** 2)
    elif C == "vector":
        map.set_data(flux, C=np.ones(len(flux)) * sigma ** 2)
    elif C == "matrix":
        map.set_data(flux, C=np.eye(len(flux)) * sigma ** 2)
    elif C == "cholesky":
        map.set_data(flux, cho_C=np.eye(len(flux)) * sigma)

    # Compute the marginal log likelihood for different inclinations
    incs = [15, 30, 45, 60, 75, 90]
    ll = np.zeros_like(incs, dtype=float)
    for i, inc in enumerate(incs):
        map.inc = inc
        ll[i] = map.lnlike(woodbury=woodbury, **kwargs)

    # Verify that we get the correct inclination
    assert incs[np.argmax(ll)] == 60
    assert np.allclose(ll[np.argmax(ll)], 983.429)  # benchmarked
