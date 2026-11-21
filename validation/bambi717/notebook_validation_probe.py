"""Private validation on the actual authored Bambi model and posterior objects."""
import hashlib
import json
from pathlib import Path

import arviz_stats as azs
import numpy as np
import pymc as pm
import xarray as xr

from science import SETTINGS, arrays, build, point, prior

E = Path('/work/evidence')


def run_probe(namespace):
    required = {'data', 'mediator_model', 'outcome_model', 'total_model',
                'mediator_fit', 'outcome_fit', 'total_fit'}
    assert required <= namespace.keys()
    x, m, y = arrays()
    for name, actual in zip(('x', 'm', 'y'), (x, m, y)):
        np.testing.assert_array_equal(namespace['data'][name].to_numpy(), actual)
    reference_joint, reference_total, _, _, _ = build()
    actual_models = [namespace[name].backend.model for name in ('mediator_model', 'outcome_model', 'total_model')]
    definitions = [(reference_joint, ['im', 'iy', 'a', 'b', 'cprime'], 10, ['sigma_m', 'sigma_y']),
                   (reference_total, ['iy', 'c'], 1, ['sigma_y']),
                   (actual_models[0], ['Intercept', 'x'], 10, ['sigma']),
                   (actual_models[1], ['Intercept', 'm', 'x'], 10, ['sigma']),
                   (actual_models[2], ['Intercept', 'x'], 1, ['sigma'])]
    for model, names, scale, sigmas in definitions:
        for name in names:
            prior(model, name, 'normal', (0, scale))
        for name in sigmas:
            prior(model, name, 'halfcauchy', (1,))
        assert {rv.name for rv in model.free_RVs} == set(names + sigmas)
    logps = [model.compile_logp() for model in (reference_joint, reference_total, *actual_models)]
    density = []
    for im, iy, a, b, cp, sm, sy in [(2, 0, .5, .6, .3, .5, .5),
                                    (1.2, -.8, -.2, 1.1, .9, .7, 1.2),
                                    (-1, 2, 1.5, -.7, -.2, 1.1, .3)]:
        joint = logps[0](point(reference_joint, dict(im=im, iy=iy, a=a, b=b, cprime=cp, sigma_m=sm, sigma_y=sy)))
        med = logps[2](point(actual_models[0], dict(Intercept=im, x=a, sigma=sm)))
        out = logps[3](point(actual_models[1], dict(Intercept=iy, m=b, x=cp, sigma=sy)))
        total = logps[1](point(reference_total, dict(iy=iy, c=cp, sigma_y=sy)))
        native_total = logps[4](point(actual_models[2], dict(Intercept=iy, x=cp, sigma=sy)))
        np.testing.assert_allclose(joint, med + out, rtol=1e-11, atol=1e-8)
        np.testing.assert_allclose(total, native_total, rtol=1e-11, atol=1e-8)
        density.append({'joint': float(joint), 'actualNativeSum': float(med + out),
                        'referenceTotal': float(total), 'actualNativeTotal': float(native_total)})
    with reference_joint:
        reference_joint_fit = pm.sample(**SETTINGS, nuts={'target_accept': .9}, random_seed=417)
    with reference_total:
        reference_total_fit = pm.sample(**SETTINGS, nuts={'target_accept': .9}, random_seed=419)
    fits = {'reference joint': reference_joint_fit, 'reference total': reference_total_fit,
            'Bambi mediator': namespace['mediator_fit'], 'Bambi outcome': namespace['outcome_fit'],
            'Bambi total': namespace['total_fit']}
    posteriors = {}
    diagnostics = []
    for label, fit in fits.items():
        posterior = fit['posterior'].ds
        stats = fit['sample_stats'].ds
        assert isinstance(posterior, xr.Dataset)
        assert posterior.sizes['chain'] == 4 and posterior.sizes['draw'] == 1000
        assert np.isfinite(posterior.to_array()).all()
        divergent = int(stats['diverging'].sum())
        rhat = float(azs.rhat(posterior).to_array().max())
        bulk = float(azs.ess(posterior, method='bulk').to_array().min())
        tail = float(azs.ess(posterior, method='tail').to_array().min())
        diagnostics.append(dict(model=label, divergences=divergent, max_rhat=rhat,
                                min_bulk_ess=bulk, min_tail_ess=tail))
        assert divergent == 0 and rhat < 1.01 and bulk >= 400 and tail >= 400
        posteriors[label] = posterior
        (E / (label.replace(' ', '-') + '-diagnostics.json')).write_text(json.dumps({
            'rhat': azs.rhat(posterior).to_dict(), 'essBulk': azs.ess(posterior, method='bulk').to_dict(),
            'essTail': azs.ess(posterior, method='tail').to_dict(),
            'hdi95': azs.hdi(posterior, prob=.95).to_dict()}, indent=2) + '\n')
    mediator = posteriors['Bambi mediator']
    outcome = posteriors['Bambi outcome']
    np.testing.assert_array_equal(outcome['direct'], outcome['x'])
    np.testing.assert_array_equal(outcome['indirect'], mediator['x'] * outcome['m'])
    np.testing.assert_array_equal(outcome['total'], outcome['x'] + mediator['x'] * outcome['m'])
    native = {'a': mediator['x'], 'b': outcome['m'], 'cprime': outcome['x'],
              'indirect effect': outcome['indirect'], 'total effect': outcome['total']}
    pairs = [(name, posteriors['reference joint'][name], actual) for name, actual in native.items()]
    pairs.append(('total-only slope', posteriors['reference total']['c'], posteriors['Bambi total']['x']))
    comparison = []
    for name, expected, actual in pairs:
        tolerance = 4 * np.sqrt(float(azs.mcse(expected, method='mean'))**2 +
                                float(azs.mcse(actual, method='mean'))**2)
        difference = abs(float(expected.mean()) - float(actual.mean()))
        row = dict(quantity=name, reference_mean=float(expected.mean()), bambi_mean=float(actual.mean()),
                   absolute_difference=difference, four_combined_mcse=tolerance)
        comparison.append(row)
        (E / 'actual-notebook-comparisons-progress.json').write_text(json.dumps(comparison, indent=2) + '\n')
        assert difference <= tolerance
    identity = {name: {'shape': list(a.shape), 'dtype': str(a.dtype),
                      'sha256': hashlib.sha256(a.tobytes()).hexdigest()} for name, a in zip(('x', 'm', 'y'), (x, m, y))}
    assert identity == json.loads((E / 'same-arrays.json').read_text())
    result = {'status': 'PASS', 'fiveFullFits': True, 'publicFits': 3, 'privateReferenceFits': 2,
              'chains': 4, 'draws': 1000, 'tune': 4000, 'targetAccept': .9,
              'allFiveDiagnostics': diagnostics, 'comparisons': comparison, 'sameArrayManifest': identity,
              'actualNotebookObjectsAndDrawsChecked': True, 'actualNativeDensityComparisons': density,
              'actualContributionNotebookExecuted': True, 'fullProjectValidation': False,
              'privateValidationProbeNotPublic': True, 'zeroPointMassClaim': False}
    (E / 'science-full-results.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
