"""Proposed real-PyMC prior checks; no assumptions about RandomVariable input layout."""
import numpy as np
import pymc as pm


def assert_scalar_prior(model, name, distribution, parameters):
    variable = model.named_vars[name]
    assert type(variable.owner.op).__name__.lower().startswith(distribution)
    assert tuple(variable.eval().shape) == ()
    if distribution == 'normal':
        assert set(parameters) == {'mu', 'sigma'} and parameters['sigma'] > 0
        values = np.array([-2.0, 0.0, 3.0])
        mu, sigma = parameters['mu'], parameters['sigma']
        expected = -0.5 * np.log(2 * np.pi) - np.log(sigma) - 0.5 * ((values - mu) / sigma)**2
    elif distribution == 'halfcauchy':
        assert set(parameters) == {'beta'} and parameters['beta'] > 0
        values = np.array([0.2, 1.0, 3.0])
        beta = parameters['beta']
        expected = np.log(2) - np.log(np.pi) - np.log(beta) - np.log1p((values / beta)**2)
        assert np.isneginf(np.asarray(pm.logp(variable, -1.0).eval())).all()
    else:
        raise AssertionError('Unsupported prior check: ' + distribution)
    actual = np.asarray(pm.logp(variable, values).eval())
    assert actual.shape == expected.shape and np.isfinite(actual).all()
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)


def prior_reader_controls():
    with pm.Model() as control:
        pm.Normal('normal_ok', mu=0, sigma=10)
        pm.HalfCauchy('halfcauchy_ok', beta=1)
        pm.Normal('wrong_mean', mu=2, sigma=10)
        pm.Normal('wrong_sigma', mu=0, sigma=5)
        pm.HalfCauchy('wrong_beta', beta=2)
        pm.Normal('wrong_family', mu=0, sigma=1)
        pm.Normal('wrong_shape', mu=0, sigma=10, shape=2)
    assert_scalar_prior(control, 'normal_ok', 'normal', {'mu': 0, 'sigma': 10})
    assert_scalar_prior(control, 'halfcauchy_ok', 'halfcauchy', {'beta': 1})
    rejected = []
    for name, distribution, parameters in [
        ('wrong_mean', 'normal', {'mu': 0, 'sigma': 10}),
        ('wrong_sigma', 'normal', {'mu': 0, 'sigma': 10}),
        ('wrong_beta', 'halfcauchy', {'beta': 1}),
        ('wrong_family', 'halfcauchy', {'beta': 1}),
        ('wrong_shape', 'normal', {'mu': 0, 'sigma': 10}),
    ]:
        try:
            assert_scalar_prior(control, name, distribution, parameters)
        except AssertionError:
            rejected.append(name)
        else:
            raise AssertionError('Prior control unexpectedly accepted: ' + name)
    return {'status': 'PASS', 'pymcVersion': pm.__version__,
            'accepted': ['normal_ok', 'halfcauchy_ok'], 'rejected': rejected,
            'method': 'actual pm.logp against analytic normalized densities and support',
            'operatorInputSlicing': False, 'mockedDependency': False}
