"""Real prior-density checks with independently bounded mixed-precision normalization."""
import inspect
import json
from pathlib import Path

import numpy as np
import pymc as pm
import pytensor
import pytensor.scalar.basic
import pytensor.tensor as pt


def compare_density(actual, expected, log_normalizers):
    actual = np.asarray(actual)
    expected = np.asarray(expected, dtype=np.float64)
    assert actual.dtype == np.dtype('float64') and actual.shape == expected.shape
    assert actual.ndim == 1 and len(actual) >= 3
    assert np.isfinite(actual).all() and np.isfinite(expected).all()
    # At most 32 float64 elementary operations plus subtraction. This bound is
    # distinct from the few low-precision constant logs in normalized PyMC logp.
    eps = np.finfo(np.float64).eps
    gamma = 32 * eps / (1 - 32 * eps)
    arithmetic = gamma * max(1.0, float(np.max(np.abs(actual))), float(np.max(np.abs(expected))))
    # PyMC integer-constant logs may be float32: a four-ULP engineering allowance
    # applies only to constant normalization, never the strict float64 shape.
    normalizer = sum(4 * abs(float(np.spacing(np.float32(term))))
                     for term in log_normalizers)
    np.testing.assert_allclose(actual - actual[0], expected - expected[0],
                               rtol=0, atol=2 * arithmetic)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=normalizer + arithmetic)
    return {'actualDtype': str(actual.dtype), 'float64ArithmeticBound': arithmetic,
            'constantLogEngineeringAllowance': normalizer,
            'normalizerAllowanceIsNotAnAccuracyTheorem': True,
            'maximumAbsoluteError': float(np.max(np.abs(actual - expected))),
            'maximumCenteredShapeError': float(np.max(np.abs(
                (actual - actual[0]) - (expected - expected[0]))))}


def assert_scalar_prior(model, name, distribution, parameters):
    variable = model.named_vars[name]
    assert type(variable.owner.op).__name__.lower().startswith(distribution)
    assert tuple(variable.eval().shape) == ()
    if distribution == 'normal':
        assert set(parameters) == {'mu', 'sigma'} and parameters['sigma'] > 0
        values = np.array([-2.0, 0.0, 3.0], dtype=np.float64)
        mu, sigma = parameters['mu'], parameters['sigma']
        expected = -0.5 * np.log(2 * np.pi) - np.log(sigma) - 0.5 * ((values - mu) / sigma)**2
        normalizers = [np.log(np.sqrt(2 * np.pi)), np.log(sigma)]
    elif distribution == 'halfcauchy':
        assert set(parameters) == {'beta'} and parameters['beta'] > 0
        values = np.array([0.2, 1.0, 3.0], dtype=np.float64)
        beta = parameters['beta']
        expected = np.log(2) - np.log(np.pi) - np.log(beta) - np.log1p((values / beta)**2)
        normalizers = [np.log(2), np.log(np.pi), np.log(beta)]
        assert np.isneginf(np.asarray(pm.logp(variable, -1.0).eval())).all()
    else:
        raise AssertionError('Unsupported prior check: ' + distribution)
    return compare_density(np.asarray(pm.logp(variable, values).eval()), expected, normalizers)


def prior_reader_controls():
    # Persist real installed-backend provenance BEFORE any numerical assertion.
    source_rows = {}
    for label, function in [('Normal.dist', pm.Normal.dist), ('Normal.logp', pm.Normal.logp),
                            ('HalfCauchy.logp', pm.HalfCauchy.logp),
                            ('Log.impl', pytensor.scalar.basic.Log.impl),
                            ('upgrade_to_float', pytensor.scalar.basic.upgrade_to_float)]:
        try:
            source_rows[label] = inspect.getsource(function)
        except (OSError, TypeError) as exc:
            source_rows[label] = {'unavailable': type(exc).__name__}
    integer_log = pt.log(pt.as_tensor_variable(10))
    float64_log = pt.log(pt.as_tensor_variable(np.float64(10)))
    audit = {'pymcVersion': pm.__version__, 'pytensorVersion': pytensor.__version__,
             'floatX': pytensor.config.floatX, 'installedSource': source_rows,
             'integer10Dtype': pt.as_tensor_variable(10).dtype,
             'integerLogDtype': integer_log.dtype, 'integerLog10': float(integer_log.eval()),
             'float64LogDtype': float64_log.dtype, 'float64Log10': float(float64_log.eval()),
             'numpyFloat64Log10': float(np.log(np.float64(10)))}
    integer_log2 = pt.log(pt.as_tensor_variable(2))
    float64_log2 = pt.log(pt.as_tensor_variable(np.float64(2)))
    audit.update(integerLog10ErrorVsFloat64=audit['integerLog10'] - audit['float64Log10'],
                 integerLog2Dtype=integer_log2.dtype, integerLog2=float(integer_log2.eval()),
                 float64Log2Dtype=float64_log2.dtype, float64Log2=float(float64_log2.eval()))
    audit['integerLog2ErrorVsFloat64'] = audit['integerLog2'] - audit['float64Log2']
    Path('/work/evidence/prior-precision-before-controls.json').write_text(json.dumps(audit, indent=2)+'\n')
    assert pytensor.config.floatX == 'float64'
    with pm.Model() as control:
        pm.Normal('normal_integer', mu=0, sigma=10)
        pm.Normal('normal_float64', mu=np.float64(0), sigma=np.float64(10))
        pm.HalfCauchy('halfcauchy_integer', beta=1)
        pm.HalfCauchy('halfcauchy_float64', beta=np.float64(1))
        pm.Normal('wrong_mean', mu=2, sigma=10)
        pm.Normal('near_wrong_mean', mu=np.float64(1e-6), sigma=np.float64(10))
        pm.Normal('wrong_sigma', mu=0, sigma=5)
        pm.Normal('near_wrong_sigma', mu=np.float64(0), sigma=np.float64(10.00001))
        pm.HalfCauchy('wrong_beta', beta=2)
        pm.HalfCauchy('near_wrong_beta', beta=np.float64(1.00001))
        pm.Normal('wrong_family', mu=0, sigma=1)
        pm.Normal('wrong_shape', mu=0, sigma=10, shape=2)
    accepted = {}
    for name, family, params in [
        ('normal_integer', 'normal', {'mu': 0, 'sigma': 10}),
        ('normal_float64', 'normal', {'mu': 0, 'sigma': 10}),
        ('halfcauchy_integer', 'halfcauchy', {'beta': 1}),
        ('halfcauchy_float64', 'halfcauchy', {'beta': 1}),
    ]:
        accepted[name] = assert_scalar_prior(control, name, family, params)
    rejected = []
    for name, family, params in [
        ('wrong_mean', 'normal', {'mu': 0, 'sigma': 10}),
        ('near_wrong_mean', 'normal', {'mu': 0, 'sigma': 10}),
        ('wrong_sigma', 'normal', {'mu': 0, 'sigma': 10}),
        ('near_wrong_sigma', 'normal', {'mu': 0, 'sigma': 10}),
        ('wrong_beta', 'halfcauchy', {'beta': 1}),
        ('near_wrong_beta', 'halfcauchy', {'beta': 1}),
        ('wrong_family', 'halfcauchy', {'beta': 1}),
        ('wrong_shape', 'normal', {'mu': 0, 'sigma': 10}),
    ]:
        try:
            assert_scalar_prior(control, name, family, params)
        except AssertionError:
            rejected.append(name)
        else:
            raise AssertionError('Wrong prior accepted: ' + name)
    # Mutate actual real-backend output to exercise normalization and shape
    # failures separately. No fake PyMC/backend or sampler is substituted.
    values = np.array([-2.0, 0.0, 3.0], dtype=np.float64)
    expected = -0.5*np.log(2*np.pi) - np.log(10) - 0.5*(values/10)**2
    actual = np.asarray(pm.logp(control['normal_float64'], values).eval())
    compare_density(actual, expected, [np.log(np.sqrt(2*np.pi)), np.log(10)])
    for label, changed in [('wrong-normalization', actual + 1e-4),
                           ('wrong-shape', actual + np.array([0, 1e-8, 0])),
                           ('nonfinite', np.array([np.nan, actual[1], actual[2]]))]:
        try:
            compare_density(changed, expected, [np.log(np.sqrt(2*np.pi)), np.log(10)])
        except AssertionError:
            rejected.append(label)
        else:
            raise AssertionError('Wrong density accepted: ' + label)
    return {'status': 'PASS', 'pymcVersion': pm.__version__,
            'pytensorVersion': pytensor.__version__, 'floatX': pytensor.config.floatX,
            'accepted': accepted, 'rejected': rejected,
            'actualInstalledSource': source_rows, 'precisionAudit': audit,
            'operatorInputSlicing': False, 'mockedDependency': False,
            'method': 'independent analytic normalized density; float64 centered shape; ULP-derived constant-log normalization bound'}
