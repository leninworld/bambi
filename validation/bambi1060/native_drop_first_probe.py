"""PACKET-ONLY baseline diagnostic: not part of the contribution test suite.

The actual full-model probe is preserved byte-for-byte. Its real baseline exit0/1
and XML must be retained; no xfail/skip, no claim that FAIL is PASS, no backend fix.
"""
from itertools import product
import bambi as bmb
import numpy as np
import pandas as pd
from scipy import special, stats

def observed_logdensity(model, point):
    backend = model.backend.model
    variables = [*backend.free_RVs, *backend.observed_RVs]
    parts = backend.compile_logp(vars=variables, sum=False)(point)
    return np.asarray(parts[len(backend.free_RVs)])


def independent_hsgp_basis_and_spectrum(data, lengths, m, ell, sigma, cov, drop_first=False):
    """Dirichlet eigenfunctions and analytic stationary PSD; no PyMC GP calls."""
    dimension = data.shape[1]
    indices = np.asarray(list(product(*(range(1, int(count) + 1) for count in m))))
    if drop_first:
        indices = indices[1:]
    omega = np.pi * indices / (2 * lengths)
    angles = (data[:, None, :] + lengths) * omega
    basis = (np.sin(angles) / np.sqrt(lengths)).prod(axis=-1)
    ell = np.broadcast_to(ell, (dimension,))
    squared = ((omega * ell) ** 2).sum(axis=-1)
    if cov == "ExpQuad":
        spectrum = sigma**2 * (2 * np.pi)**(dimension / 2) * ell.prod() * np.exp(-squared / 2)
    else:
        nu = 1.5 if cov == "Matern32" else 2.5
        spectrum = (sigma**2 * 2**dimension * np.pi**(dimension / 2)
                    * special.gamma(nu + dimension / 2) * (2 * nu)**nu
                    * ell.prod() / special.gamma(nu)
                    * (2 * nu + squared)**(-nu - dimension / 2))
    return basis, spectrum


def test_drop_first_full_model_preserves_actual_basis_weight_contract():
    # This genuine full-model probe must not be replaced by the lower-level basis test.
    # Any upstream dimension failure is retained for baseline/candidate adjudication.
    frame = pd.DataFrame({"y": [0.2, 0.7, 1.1, 0.5], "x": [-1., -0.2, 0.2, 1.]})
    model = bmb.Model("y ~ 0 + hsgp(x, m=4, c=1.5, drop_first=True, scale=False)",
                      frame, priors={"sigma": 0.9})
    original = next(iter(model.parameters["mu"].hsgp_terms.values()))
    model.set_priors({original.name: {"sigma": 1.2, "ell": 0.7}})
    model.set_alias({original.name: "dropped"})
    model.build()
    point = model.backend.model.initial_point()
    point["dropped_weights_raw"] = np.linspace(-0.3, 0.3, 3)
    lengths = original.L[0]
    data = original.data - original.mean
    basis, spectrum = independent_hsgp_basis_and_spectrum(
        data, lengths, original.m, np.array([0.7]), 1.2, "ExpQuad", True)
    expected = stats.norm.logpdf(frame.y, basis @ (point["dropped_weights_raw"] * np.sqrt(spectrum)), 0.9)
    np.testing.assert_allclose(observed_logdensity(model, point), expected, rtol=1e-6)
