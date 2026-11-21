"""Private science probe for Bambi #1097 attempt 9 (never published in the notebook).

Runs in the notebook kernel after the last public cell, against the real objects:
``run(globals())``. It recomputes every causal estimand shown in the notebook from
direct ``Model.predict(kind="response_params")`` draws, checks the hand-worked identities
that the public notebook now states only in prose, checks numeric convergence and the
posterior structure of both fits, and writes /work/evidence/science-probe.json.
Any failed check raises, which fails the probe cell and therefore the attempt.
"""
import ast
import json
import math
import os
import sys
from pathlib import Path

if sys.flags.optimize:
    raise SystemExit('A9_REFUSED_PYTHON_O')
sys.dont_write_bytecode = True

import arviz as az
import numpy as np
import xarray as xr

from bambi.causal import g_computation

P = Path(os.environ.get('A9_PACKET', '/packet'))
E = Path(os.environ.get('A9_WORK', '/work')) / 'evidence'
RTOL = 1e-10
ATOL = 1e-12
RHAT_MAX = 1.01
ESS_MIN = 400
REQUIRED = ('model', 'idata', 'data', 'causal_inputs', 'standardized', 'ate', 'att', 'atu', 'cate',
            'gate', 'dose_response', 'binary_model', 'binary_idata', 'binary_data', 'binary_inputs',
            'results')


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def fit_contract():
    """Read literal draw/chain counts from the two public notebook fit calls."""
    notebook = json.loads((P / 'candidate-causal_effects.ipynb').read_text())
    contract = {}
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code':
            continue
        tree = ast.parse(''.join(cell['source']))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if not isinstance(call.func, ast.Attribute) or call.func.attr != 'fit':
                continue
            check(len(node.targets) == 1 and isinstance(node.targets[0], ast.Name), 'fit assignment')
            values = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords
                      if kw.arg in ('chains', 'draws')}
            check(set(values) == {'chains', 'draws'}, 'fit must explicitly set chains and draws')
            check(values['chains'] == 4, 'fit must use four chains')
            contract[node.targets[0].id] = values
    check(set(contract) == {'idata', 'binary_idata'}, 'exactly two fitted inference objects')
    return contract


def close(actual, expected, message):
    actual = np.asarray(actual, dtype=float)
    expected = np.asarray(expected, dtype=float)
    check(actual.shape == expected.shape, '%s: shape %s != %s' % (message, actual.shape, expected.shape))
    check(np.isfinite(actual).all() and np.isfinite(expected).all(), message + ': nonfinite')
    check(np.allclose(actual, expected, rtol=RTOL, atol=ATOL), message + ': values differ')
    return float(np.max(np.abs(actual - expected))) if actual.size else 0.0


def effect_draws(result):
    draws = result.draws['effects'].ds['estimate']
    check(draws.dims == ('chain', 'draw', '__obs__'), 'effect draw dims')
    check(draws.sizes['__obs__'] == len(result.summary), 'effect rows')
    return draws.values


def posterior_structure(name, idata, parent_names, sampling):
    chains, draws = sampling['chains'], sampling['draws']
    post = idata['posterior'].ds
    stats = idata['sample_stats'].ds
    check(post.sizes.get('chain') == chains and post.sizes.get('draw') == draws, name + ': chain/draw sizes')
    check(list(post['chain'].values) == list(range(chains)), name + ': chain coords')
    check(list(post['draw'].values) == list(range(draws)), name + ': draw coords')
    check('diverging' in stats, name + ': no diverging stat')
    div = stats['diverging']
    check(div.dims[:2] == ('chain', 'draw') and div.shape == (chains, draws), name + ': diverging shape')
    for var in parent_names:
        check(var in post, name + ': missing posterior variable ' + var)
    for var, arr in post.data_vars.items():
        check(arr.dims[:2] == ('chain', 'draw'), name + ': dims of ' + var)
        check(np.isfinite(arr.values).all(), name + ': nonfinite ' + var)
    return {'chains': chains, 'draws': draws, 'variables': sorted(post.data_vars),
            'divergences': int(div.values.sum())}


def convergence(name, idata):
    table = az.summary(idata)
    for col in ('r_hat', 'ess_bulk', 'ess_tail'):
        check(col in table.columns, '%s: az.summary has no %s column' % (name, col))
    check(len(table) > 0, name + ': empty summary')
    rhat = float(table['r_hat'].max())
    bulk = float(table['ess_bulk'].min())
    tail = float(table['ess_tail'].min())
    div = int(idata['sample_stats'].ds['diverging'].values.sum())
    row = {'rows': len(table), 'max_r_hat': rhat, 'min_ess_bulk': bulk, 'min_ess_tail': tail,
           'divergences': div}
    check(math.isfinite(rhat) and rhat <= RHAT_MAX, '%s: r_hat %.4f > %.2f' % (name, rhat, RHAT_MAX))
    check(bulk >= ESS_MIN and tail >= ESS_MIN, '%s: ESS bulk %.0f / tail %.0f < %d' % (name, bulk, tail, ESS_MIN))
    check(div == 0, '%s: %d divergences' % (name, div))
    return row


def predicted_parent(model, idata, frame, parent):
    out = model.predict(idata, data=frame, kind='response_params', inplace=False,
                        include_group_specific=True)
    arr = out['posterior'].ds[parent].transpose('chain', 'draw', '__obs__')
    check(arr.sizes['__obs__'] == len(frame), 'prediction rows')
    return arr.values


def run(ns):
    missing = [k for k in REQUIRED if k not in ns]
    check(not missing, 'notebook objects missing: %s' % missing)
    rep = {'status': 'FAIL', 'checks': {}}
    c = rep['checks']
    model, idata, data = ns['model'], ns['idata'], ns['data']
    inputs = ns['causal_inputs']
    reference = inputs['reference_population']
    before = idata['posterior'].ds.copy(deep=True)
    bin_before = ns['binary_idata']['posterior'].ds.copy(deep=True)

    # Posterior structure and numeric convergence of both fits.
    sampling = fit_contract()
    c['sampling_contract'] = sampling
    c['gaussian_structure'] = posterior_structure(
        'idata', idata, ['dose', 'z', 'dose:z', 'x', 'sigma'], sampling['idata']
    )
    c['bernoulli_structure'] = posterior_structure(
        'binary_idata', ns['binary_idata'], ['exposure', 'x', 'z'], sampling['binary_idata']
    )
    c['gaussian_convergence'] = convergence('idata', idata)
    c['bernoulli_convergence'] = convergence('binary_idata', ns['binary_idata'])

    # Gaussian estimands against direct prediction (same reference rows, held fixed).
    check(model.family.likelihood.parent == 'mu', 'gaussian parent')
    means = {v: predicted_parent(model, idata, reference.assign(dose=v), 'mu') for v in (0.0, 0.5, 1.0)}
    diff = means[1.0] - means[0.0]
    errs = {}
    errs['standardized'] = close(effect_draws(ns['standardized']),
                                 np.stack([means[v].mean(-1) for v in ns['standardized'].summary['value']], -1),
                                 'standardized means')
    errs['ate'] = close(effect_draws(ns['ate'])[..., 0], diff.mean(-1), 'ATE')
    treated = reference['dose'].to_numpy() == 1.0
    untreated = reference['dose'].to_numpy() == 0.0
    check(treated.any() and untreated.any(), 'membership masks empty')
    errs['att'] = close(effect_draws(ns['att'])[..., 0], diff[..., treated].mean(-1), 'ATT')
    errs['atu'] = close(effect_draws(ns['atu'])[..., 0], diff[..., untreated].mean(-1), 'ATU')
    cate = ns['cate']
    check(list(cate.summary['z']) == [0, 1], 'CATE strata order')
    errs['cate'] = close(effect_draws(cate), np.stack(
        [diff[..., reference['z'].to_numpy() == z].mean(-1) for z in cate.summary['z']], -1), 'CATE')
    gate = ns['gate']
    observed = set(zip(reference['z'].to_numpy().tolist(), reference['site'].astype(str).tolist()))
    check(len(gate.summary) == len(observed), 'GATE groups')
    gate_expected = []
    for z, site in zip(gate.summary['z'], gate.summary['site']):
        rows = (reference['z'].to_numpy() == z) & (reference['site'].astype(str).to_numpy() == str(site))
        check(rows.any(), 'GATE group empty')
        gate_expected.append(diff[..., rows].mean(-1))
    errs['gate'] = close(effect_draws(gate), np.stack(gate_expected, -1), 'GATE')
    dr = ns['dose_response']
    check(list(dr.summary['value']) == [0.0, 0.5, 1.0], 'dose-response grid')
    errs['dose_response'] = close(effect_draws(dr), np.stack([means[v].mean(-1) for v in (0.0, 0.5, 1.0)], -1),
                                  'dose-response means')
    weights = np.where(reference['z'].to_numpy() == 0, 3.0, 1.0)
    weighted = g_computation(model, idata, estimand='ate', values=[0.0, 1.0], weights=weights, **inputs)
    errs['weighted_ate'] = close(effect_draws(weighted)[..., 0], (diff * (weights / weights.sum())).sum(-1),
                                 'weighted ATE')

    # Bernoulli: risk difference, risk ratio, odds ratio and the opposite event.
    bmodel, bidata = ns['binary_model'], ns['binary_idata']
    binputs = ns['binary_inputs']
    check(bmodel.response_term.success == 'converted', 'modeled success event')
    check(bmodel.family.likelihood.parent == 'p', 'bernoulli parent')
    bref = binputs['reference_population']
    m0 = predicted_parent(bmodel, bidata, bref.assign(exposure=0), 'p').mean(-1)
    m1 = predicted_parent(bmodel, bidata, bref.assign(exposure=1), 'p').mean(-1)
    rd, rr, odds = (effect_draws(r)[..., 0] for r in ns['results'])
    check([r.metadata['comparison'] for r in ns['results']] == ['difference', 'risk_ratio', 'odds_ratio'],
          'binary comparisons')
    errs['risk_difference'] = close(rd, m1 - m0, 'risk difference')
    errs['risk_ratio'] = close(rr, m1 / m0, 'risk ratio')
    errs['odds_ratio'] = close(odds, (m1 / (1 - m1)) / (m0 / (1 - m0)), 'odds ratio')
    opposite = g_computation(bmodel, bidata, estimand='ate', **{**binputs, 'event': 'not_converted'})
    errs['opposite_event_rd'] = close(effect_draws(opposite)[..., 0], -rd, 'opposite-event risk difference')
    c['direct_prediction_max_abs_error'] = errs

    # Hand-worked identities that the notebook now states in prose (removed cells 17, 23, 25).
    z_math = np.array([0.0, 1.0])

    def means_math(a):
        return 0.5 + (1 + 3 * z_math) * a + 2 * z_math
    unit = means_math(1) - means_math(0)
    check(np.isclose(unit.mean(), 2.5) and np.isclose(unit[1], 4.0) and np.isclose(unit[0], 1.0), 'ATE/ATT/ATU')
    check(np.isclose(np.average(unit, weights=[3, 1]), 1.75), 'weighted ATE 1.75')
    check(np.allclose([means_math(a).mean() for a in (0, 1, 3)], [1.5, 4, 9]), 'standardized means 1.5, 4, 9')
    p0 = np.array([1 / 10, 4 / 5])
    p1 = np.array([2 / 11, 8 / 9])
    unit_or = (p1 / (1 - p1)) / (p0 / (1 - p0))
    check(np.allclose(unit_or, [2, 2]), 'both unit odds ratios equal 2')
    mrr = p1.mean() / p0.mean()
    mor = (p1.mean() / (1 - p1.mean())) / (p0.mean() / (1 - p0.mean()))
    check(np.isclose(mrr, 1060 / 891) and np.isclose(mor, 583 / 414), 'marginal RR 1060/891 and OR 583/414')
    check(not np.isclose(mor, 2), 'marginal odds ratio differs from 2')
    mu = np.array([0.0, 0.4])
    sigma = np.array([0.3, 0.8])
    lognormal = np.exp(mu + sigma ** 2 / 2)
    check(np.allclose(lognormal, [math.exp(0.045), math.exp(0.72)]), 'LogNormal means exp(mu + sigma^2/2)')
    check(not np.allclose(lognormal, np.exp(mu)), 'LogNormal mean differs from exp(mu)')
    c['hand_worked'] = {'ate_att_atu': [2.5, 4.0, 1.0], 'weighted_ate': 1.75,
                        'standardized_means': [1.5, 4, 9], 'unit_odds_ratios': [2.0, 2.0],
                        'marginal_rr': mrr, 'marginal_or': mor, 'lognormal_means': lognormal.tolist()}

    # The probe itself must not mutate the fitted posteriors.
    xr.testing.assert_identical(idata['posterior'].ds, before)
    xr.testing.assert_identical(bidata['posterior'].ds, bin_before)
    rep['status'] = 'PASS'
    text = json.dumps(rep, indent=1, sort_keys=True, allow_nan=False) + '\n'
    tmp = E / '.science-probe.json.tmp'
    tmp.write_text(text)
    tmp.replace(E / 'science-probe.json')
    return rep
