"""Mathematical descriptions: semantic, numerical, and non-mutation contracts."""

import copy
from itertools import product
import random

import bambi as bmb
import numpy as np
import pandas as pd
import pymc as pm
import pytest
from scipy import special, stats

from bambi.defaults.families import BUILTIN_FAMILIES
try:
    from bambi.representation import _Expression, _Prior, _describe, _to_latex
except ModuleNotFoundError as error:
    # Only the three public semantic-contract cases run on the unchanged baseline.
    # Do not hide a broken transitive dependency of the candidate module.
    if error.name != "bambi.representation":
        raise
    _Expression = _Prior = _describe = _to_latex = None


def make_model(family="gaussian", formula=None, **kwargs):
    data = pd.DataFrame({"x": [-0.7, -0.4, -0.1, 0.2, 0.5, 0.8]})
    data["y"] = [0.2, 0.4, 1.1, 0.7, 1.5, 0.8]
    data["n"] = 5
    data["group"] = ["a", "a", "b", "b", "c", "c"]
    if family in ("beta",):
        data["y"] = [0.2, 0.4, 0.6, 0.7, 0.8, 0.3]
    if family in ("bernoulli",):
        data["y"] = [0, 1, 0, 1, 1, 0]
    if family in (
        "poisson", "negativebinomial", "hurdle_poisson", "hurdle_negativebinomial",
        "zero_inflated_poisson", "zero_inflated_negativebinomial", "binomial",
        "beta_binomial", "zero_inflated_binomial",
    ):
        data["y"] = [0, 1, 2, 3, 1, 2]
    if family in ("hurdle_gamma", "hurdle_lognormal"):
        data.loc[0, "y"] = 0
    if family in ("categorical", "cumulative", "sratio"):
        data["y"] = pd.Categorical(["a", "b", "c", "a", "c", "b"], ordered=True)
    response = "y"
    if family in ("binomial", "beta_binomial", "zero_inflated_binomial"):
        response = "p(y, n)"
    if family in ("multinomial", "dirichlet_multinomial"):
        data["y1"] = [1, 2, 3, 1, 0, 2]
        data["y2"] = [2, 1, 1, 3, 2, 1]
        data["y3"] = [3, 2, 0, 1, 3, 2]
        response = "c(y1, y2, y3)"
    if formula is None:
        formula = response + (" ~ 0 + x" if family in ("cumulative", "sratio") else " ~ x")
    parameters = BUILTIN_FAMILIES[family]["likelihood"]["params"]
    parent = BUILTIN_FAMILIES[family]["likelihood"]["parent"]
    fixed = {"sigma": 1.2, "alpha": 3.0, "kappa": 2.5, "b": 1.3,
             "nu": 4.0, "lam": 2.0, "psi": 0.65}
    priors = {name: fixed[name] for name in parameters if name != parent and name in fixed}
    if "threshold" in parameters:
        priors["threshold"] = bmb.Prior(
            "Normal", mu=[-1.0, 1.0], sigma=1,
            transform=pm.distributions.transforms.ordered, initval=[-1.0, 1.0]
        )
    priors.update(kwargs.pop("priors", {}))
    return bmb.Model(formula, data, family=family, priors=priors, **kwargs)


def prior_value(prior, point):
    """Independently invert the transforms used in these controlled test models."""
    if not isinstance(prior, _Prior):
        return prior
    if prior.distribution == "Horseshoe":
        return (point[prior.name + "_raw"] * np.exp(point[prior.name + "_tau_log__"])
                * np.exp(point[prior.name + "_lam_log__"]))
    if prior.noncentered:
        return prior_value(dict(prior.arguments)["sigma"], point) * point[prior.name + "_offset"]
    if prior.name in point:
        return point[prior.name]
    if prior.name + "_log__" in point:
        return np.exp(point[prior.name + "_log__"])
    ordered = prior.name + "_ordered__"
    if ordered in point:
        value = point[ordered]
        return np.concatenate([value[:1], value[0] + np.cumsum(np.exp(value[1:]))])
    raise AssertionError(f"Unrecognised controlled-test value transform: {prior.name}")


def inverse_link(name, eta):
    """Independent NumPy/SciPy functions; never call the renderer's or backend's link."""
    if name == "identity":
        return eta
    if name == "log":
        return np.exp(eta)
    if name == "inverse":
        return 1 / eta
    if name == "inverse_squared":
        return 1 / np.sqrt(eta)
    if name == "logit":
        result = special.expit(eta)
    elif name == "probit":
        result = special.ndtr(eta)
    elif name == "cloglog":
        result = -np.expm1(-np.exp(eta))
    else:
        result = None
    if result is not None:
        eps = np.finfo(np.float64).eps
        return np.where(result == 0, eps, np.where(result == 1, 1 - eps, result))
    if name == "softmax":
        return special.softmax(eta, axis=-1)
    raise AssertionError(f"Unrecognised controlled-test link: {name}")


def predictor_value(predictor, point):
    """Evaluate the stored design algebra independently of LaTeX serialization."""
    result = 0
    for term in predictor.terms:
        if term.kind == "offset":
            result = result + term.data.squeeze()
            continue
        if term.kind == "hsgp":
            raise AssertionError("HSGP uses its separate analytic basis oracle")
        coefficient = prior_value(term.prior, point)
        if term.kind == "intercept":
            result = result + coefficient
        elif term.kind == "common":
            coefficient = np.atleast_1d(coefficient)
            if predictor.response_dimensions and coefficient.ndim == 1:
                coefficient = coefficient[np.newaxis, :]
            result = result + term.data @ coefficient
        else:
            selected = coefficient[term.group_index]
            predictor_data = term.data
            if predictor.response_dimensions:
                predictor_data = predictor_data[..., np.newaxis]
            value = selected * predictor_data
            result = result + (value.sum(axis=1) if term.data.ndim > 1 else value)
    return np.asarray(result)


def evaluate(expression, parameters, etas, data):
    """An independent evaluator of the private mathematical expression records."""
    if not isinstance(expression, _Expression):
        return expression
    operation, operands = expression.operation, expression.operands
    if operation == "parameter":
        return parameters[operands[0]]
    if operation == "eta":
        return etas[operands[0]]
    if operation == "data_column":
        return data[:, operands[0]]
    if operation == "row_sum":
        return data.sum(axis=1)
    if operation == "reference_softmax":
        eta = np.asarray(evaluate(operands[0], parameters, etas, data))
        eta = np.broadcast_to(eta, (len(data), eta.shape[-1]))
        return special.softmax(np.column_stack([np.zeros(len(data)), eta]), axis=-1)
    if operation == "ordinal":
        family, link, eta, thresholds = operands
        eta = evaluate(eta, parameters, etas, data)
        thresholds = evaluate(thresholds, parameters, etas, data)
        cdf = inverse_link(link, thresholds - np.atleast_1d(eta)[:, None])
        if family == "cumulative":
            return np.diff(np.column_stack([np.zeros(len(data)), cdf, np.ones(len(data))]))
        survival = np.column_stack([np.ones(len(data)), np.cumprod(1 - cdf, axis=1)])
        return np.column_stack([cdf * survival[:, :-1], survival[:, -1]])
    values = [evaluate(item, parameters, etas, data) for item in operands]
    operations = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
        "sqrt": np.sqrt,
        "gamma": special.gamma,
    }
    return operations[operation](*values)


def mathematical_arguments(description, point):
    etas = {item.parameter: predictor_value(item, point) for item in description.predictors}
    parameters = {item.parameter: inverse_link(item.link, etas[item.parameter])
                  for item in description.predictors}
    parameters.update({name: prior_value(value, point) for name, _, value in description.marginal})
    return {name: evaluate(value, parameters, etas, description.data)
            for name, value in description.arguments}


def independent_logdensity(description, arguments):
    """Analytic/SciPy likelihoods, not PyMC distributions used a second time."""
    a = arguments
    family = description.family
    data = description.data
    if family in ("categorical", "cumulative", "sratio"):
        y = np.asarray(data).argmax(axis=1) if data.ndim == 2 else data.astype(int)
        return np.log(a["p"][np.arange(len(y)), y])
    if family in ("multinomial", "dirichlet_multinomial"):
        n = a["n"]
        combinatorial = special.gammaln(n + 1) - special.gammaln(data + 1).sum(axis=1)
        if family == "multinomial":
            return combinatorial + special.xlogy(data, a["p"]).sum(axis=1)
        alpha = np.broadcast_to(a["a"], data.shape)
        total = alpha.sum(axis=1)
        return (combinatorial + special.gammaln(total) - special.gammaln(total + n)
                + (special.gammaln(data + alpha) - special.gammaln(alpha)).sum(axis=1))
    if family in ("binomial", "beta_binomial", "zero_inflated_binomial"):
        y = data[:, 0]
    else:
        y = data.squeeze()
    base = family.removeprefix("hurdle_").removeprefix("zero_inflated_")
    if base == "asymmetriclaplace":
        difference = y - a["mu"]
        sign = np.where(difference >= 0, 1, -1)
        result = (np.log(a["b"] / (a["kappa"] + 1 / a["kappa"]))
                  - a["b"] * np.abs(difference) * a["kappa"] ** sign)
    elif base == "bernoulli":
        result = stats.bernoulli.logpmf(y, a["p"])
    elif base == "beta":
        result = stats.beta.logpdf(y, a["alpha"], a["beta"])
    elif base == "beta_binomial":
        result = stats.betabinom.logpmf(y, a["n"], a["alpha"], a["beta"])
    elif base == "binomial":
        result = stats.binom.logpmf(y, a["n"], a["p"])
    elif base == "exgaussian":
        result = stats.exponnorm.logpdf(y, a["nu"] / a["sigma"], a["mu"], a["sigma"])
    elif base == "exponential":
        result = stats.expon.logpdf(y, scale=1 / a["lam"])
    elif base == "gamma":
        alpha = (a["mu"] / a["sigma"]) ** 2
        result = stats.gamma.logpdf(y, alpha, scale=a["sigma"] ** 2 / a["mu"])
    elif base == "lognormal":
        result = stats.lognorm.logpdf(y, a["sigma"], scale=np.exp(a["mu"]))
    elif base == "gaussian":
        result = stats.norm.logpdf(y, a["mu"], a["sigma"])
    elif base == "negativebinomial":
        result = stats.nbinom.logpmf(y, a["alpha"], a["alpha"] / (a["alpha"] + a["mu"]))
    elif base == "laplace":
        result = stats.laplace.logpdf(y, a["mu"], a["b"])
    elif base == "poisson":
        result = stats.poisson.logpmf(y, a["mu"])
    elif base == "t":
        result = stats.t.logpdf(y, a["nu"], a["mu"], a["sigma"])
    elif base == "vonmises":
        result = stats.vonmises.logpdf(y, a["kappa"], loc=a["mu"])
    elif base == "wald":
        result = stats.invgauss.logpdf(y, a["mu"] / a["lam"], scale=a["lam"])
    elif base == "weibull":
        result = stats.weibull_min.logpdf(y, a["alpha"], scale=a["beta"])
    else:
        raise AssertionError(f"Missing independent density oracle: {family}")
    if family.startswith("hurdle_"):
        if base == "poisson":
            zero = stats.poisson.pmf(0, a["mu"])
        elif base == "negativebinomial":
            zero = stats.nbinom.pmf(0, a["alpha"], a["alpha"] / (a["alpha"] + a["mu"]))
        else:
            zero = 0
        return np.where(y == 0, np.log1p(-a["psi"]), np.log(a["psi"]) + result - np.log1p(-zero))
    if family.startswith("zero_inflated_"):
        return np.where(y == 0, np.logaddexp(np.log1p(-a["psi"]), np.log(a["psi"]) + result),
                        np.log(a["psi"]) + result)
    return result


def fixed_point(model):
    point = model.backend.model.initial_point()
    for name, value in point.items():
        if name.endswith("_ordered__"):
            point[name] = np.array([-1.0, np.log(2.0)])
        elif name.endswith("_log__"):
            point[name] = np.full_like(value, np.log(0.7))
        else:
            point[name] = np.full_like(value, 1.5 if "Intercept" in name else 0.2)
    return point


def observed_logdensity(model, point):
    backend = model.backend.model
    variables = [*backend.free_RVs, *backend.observed_RVs]
    parts = backend.compile_logp(vars=variables, sum=False)(point)
    return np.asarray(parts[len(backend.free_RVs)])


@pytest.mark.parametrize("family", list(BUILTIN_FAMILIES))
def test_every_native_family_has_mathematics_and_independent_density(family):
    model = make_model(family)
    description = _describe(model)
    latex = model.to_latex()
    assert latex.startswith(r"\begin{aligned}")
    assert r"\sim" in latex and r"\eta_{" in latex
    assert "user\\!\\ defined" not in latex
    assert not model.built
    model.build()
    point = fixed_point(model)
    arguments = mathematical_arguments(description, point)
    expected = independent_logdensity(description, arguments)
    actual = observed_logdensity(model, point)
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-7)


@pytest.mark.parametrize("link", ["identity", "logit", "probit", "cloglog"])
def test_supported_probability_links_have_numerical_meaning(link):
    model = make_model("bernoulli", link=link)
    model.build()
    point = fixed_point(model)
    point["Intercept"] = np.asarray(0.4)
    point["x"] = np.asarray(0.1)
    description = _describe(model)
    expected = independent_logdensity(description, mathematical_arguments(description, point))
    np.testing.assert_allclose(observed_logdensity(model, point), expected)


def test_centering_offsets_hierarchy_and_distributional_parameters():
    data = pd.DataFrame({"y": [0.2, 0.7, 1.1, 0.5, 1.4, 0.6], "x": [1., 2., 4., 5., 7., 8.],
                         "z": [0.2, 0.1, 0.3, -0.2, 0.4, -0.1], "g": ["a", "b"] * 3})
    model = bmb.Model(bmb.Formula("y ~ x + offset(z) + (1|g)", "sigma ~ z"), data)
    description = _describe(model)
    model.build()
    point = fixed_point(model)
    expected = independent_logdensity(description, mathematical_arguments(description, point))
    np.testing.assert_allclose(observed_logdensity(model, point), expected)
    latex = model.to_latex()
    assert r"\overline X" in latex and r"\mathrm{1|g}" in latex
    assert r"\eta_{sigma,i}" in latex
    group = next(t for p in description.predictors for t in p.terms if t.kind == "group")
    assert group.prior.noncentered
    hyperprior = dict(group.prior.arguments)["sigma"]
    assert all(not dim.endswith("__factor_dim") for dim, _ in hyperprior.dimensions)


def freeze(value, seen=None):
    """Deeply snapshot state, including all owned arrays, without invoking model methods."""
    seen = {} if seen is None else seen
    if isinstance(value, np.ndarray):
        return (str(value.dtype), value.shape, value.tobytes())
    if isinstance(value, (str, int, float, bool, type(None), np.number)):
        return value
    if id(value) in seen:
        return ("reference", seen[id(value)])
    seen[id(value)] = len(seen)
    if isinstance(value, dict):
        return tuple((freeze(k, seen), freeze(v, seen)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item, seen) for item in value)
    if callable(value):
        return ("callable", id(value))
    if hasattr(value, "__dict__"):
        return (type(value).__name__, freeze(vars(value), seen))
    return (type(value).__name__, repr(value))


@pytest.mark.parametrize("built", [False, True])
def test_rendering_does_not_change_model_or_rng(built):
    model = make_model()
    if built:
        model.build()
    before = freeze(vars(model))
    numpy_rng = copy.deepcopy(np.random.get_state())
    python_rng = random.getstate()
    text = model.to_latex()
    assert model.to_latex() == text
    assert freeze(vars(model)) == before
    assert freeze(np.random.get_state()) == freeze(numpy_rng)
    assert random.getstate() == python_rng


def test_escaped_aliases_numeric_precision_and_ordered_thresholds():
    model = make_model(priors={"x": bmb.Prior("Normal", mu=0.123456789123, sigma=1)})
    model.set_alias({"x": "slope_%{x}\\", "mu": "mean_y"})
    text = model.to_latex()
    assert r"slope\_\%\{x\}\backslash{}" in text
    assert "0.123456789123" in text
    assert r"\eta_{mean\_y,i}" in text
    ordinal = make_model("cumulative").to_latex()
    assert "ordered" in ordinal.lower()
    assert r"_{-1} &=-\infty" in ordinal


def test_user_functions_are_named_and_never_called():
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Rendering must not call a user's function")
    family = bmb.Family(
        "user_family", bmb.Likelihood("user_law", params=["mu", "sigma"], parent="mu", dist=forbidden),
        bmb.Link("user_link", link=forbidden, linkinv=forbidden, linkinv_backend=forbidden)
    )
    data = pd.DataFrame({"y": [1., 2., 3.], "x": [0., 1., 2.]})
    model = bmb.Model("y ~ x", data, family=family,
                      priors={"sigma": 1, "x": bmb.Prior("user_prior", dist=forbidden, scale=1)},
                      potentials=[("x", forbidden)], auto_scale=False)
    text = model.to_latex()
    assert "forbidden" in text and "user" in text
    assert calls == []


def test_unknown_structures_fail_instead_of_silently_disappearing():
    model = make_model()
    model.parameters["unknown"] = object()
    with pytest.raises(ValueError, match="unrecognised model parameter"):
        model.to_latex()
    del model.parameters["unknown"]
    model.parameters["mu"].terms["unknown"] = object()
    with pytest.raises(ValueError, match="Cannot describe term"):
        model.to_latex()


@pytest.mark.parametrize("cov", ["ExpQuad", "Matern32", "Matern52"])
@pytest.mark.parametrize("centered", [False, True])
def test_hsgp_exposes_basis_spectrum_and_parameterization(cov, centered):
    data = pd.DataFrame({"y": [0.2, 0.7, 1.1, 0.5, 1.4, 0.6], "x": np.linspace(-1, 1, 6)})
    formula = f"y ~ 0 + hsgp(x, m=4, c=1.5, cov='{cov}', centered={centered}, drop_first=True)"
    model = bmb.Model(formula, data, priors={"sigma": 0.9})
    term = next(iter(model.parameters["mu"].hsgp_terms.values()))
    model.set_priors({term.name: {"sigma": 1.2, "ell": 0.7}})
    description = _describe(model)
    term = description.predictors[0].terms[0]
    attributes = dict(term.hsgp)
    text = model.to_latex()
    assert attributes["cov"] == cov and attributes["drop_first"]
    assert r"\prod_d" in text and r"\omega" in text
    assert attributes["centered"] == centered
    assert not model.built


@pytest.mark.parametrize("kind", ["weighted", "censored", "truncated", "constrained"])
def test_response_wrapper_is_not_misrepresented_as_an_ordinary_law(kind):
    data = pd.DataFrame({"y": [0.4, 0.7, 1.0, 1.2], "x": [0., 1., 2., 3.],
                         "w": [1., 0.5, 2., 1.], "status": ["none", "left", "right", "none"]})
    response = {"weighted": "weighted(y, w)", "censored": "censored(y, status)",
                "truncated": "truncated(y, 0, 2)", "constrained": "constrained(y, 0, 2)"}[kind]
    model = bmb.Model(response + " ~ x", data)
    description = _describe(model)
    assert description.response_kind == kind
    text = model.to_latex()
    if kind == "weighted":
        assert r"w_i\log p_0" in text
    elif kind == "censored":
        assert "F_0(y_i)" in text and "c_i=-1" in text and "c_i=1" in text
    else:
        assert r"\Pr_{p_0}(\ell_i\leq Y_i\leq u_i)" in text


def test_serialization_consumes_the_structured_parameter_conversion():
    description = _describe(make_model("beta"))
    alpha = dict(description.arguments)["alpha"]
    assert alpha.operation == "multiply"
    assert [item.operands[0] for item in alpha.operands] == ["mu", "kappa"]
    assert _to_latex(description) == make_model("beta").to_latex()


@pytest.mark.parametrize("cov", ["ExpQuad", "Matern32", "Matern52"])
@pytest.mark.parametrize("centered", [False, True])
def test_hsgp_analytic_basis_density_matches_built_model(cov, centered):
    data = pd.DataFrame({"y": [0.2, 0.7, 1.1, 0.5, 1.4, 0.6], "x": np.linspace(-1, 1, 6)})
    formula = f"y ~ 0 + hsgp(x, m=4, c=1.5, cov='{cov}', centered={centered}, scale=False)"
    model = bmb.Model(formula, data, priors={"sigma": 0.9})
    original = next(iter(model.parameters["mu"].hsgp_terms.values()))
    model.set_priors({original.name: {"sigma": 1.2, "ell": 0.7}})
    model.set_alias({original.name: "smooth"})
    description = _describe(model)
    term = description.predictors[0].terms[0]
    attrs = dict(term.hsgp)
    model.build()
    point = model.backend.model.initial_point()
    label = "smooth_weights" + ("" if centered else "_raw")
    point[label] = np.linspace(-0.3, 0.3, 4)
    length = np.asarray(attrs["L"])[0, 0]
    omega = np.pi * np.arange(1, 5) / (2 * length)
    x = term.data[:, 0] - np.asarray(attrs["mean"]).squeeze()
    phi = np.sin((x[:, None] + length) * omega) / np.sqrt(length)
    if cov == "ExpQuad":
        spectrum = 1.2**2 * np.sqrt(2 * np.pi) * 0.7 * np.exp(-0.5 * (0.7 * omega) ** 2)
    else:
        nu = 1.5 if cov == "Matern32" else 2.5
        spectrum = (1.2**2 * 2 * np.sqrt(np.pi) * special.gamma(nu + 0.5)
                    * (2 * nu) ** nu * 0.7 / special.gamma(nu)
                    * (2 * nu + (0.7 * omega) ** 2) ** (-nu - 0.5))
    coefficient = point[label] if centered else point[label] * np.sqrt(spectrum)
    expected = stats.norm.logpdf(data.y, phi @ coefficient, 0.9)
    np.testing.assert_allclose(observed_logdensity(model, point), expected, rtol=1e-6)


@pytest.mark.parametrize("center", [False, True])
def test_vector_response_and_group_design_have_independent_numerics(center):
    model = make_model("categorical", formula="y ~ x + (1|group)", center_predictors=center)
    description = _describe(model)
    model.build()
    point = fixed_point(model)
    expected = independent_logdensity(description, mathematical_arguments(description, point))
    np.testing.assert_allclose(observed_logdensity(model, point), expected, rtol=1e-6)


@pytest.mark.parametrize("kind", ["weighted", "censored", "truncated", "constrained"])
def test_response_wrapper_density_is_correct(kind):
    data = pd.DataFrame({"y": [0.4, 0.7, 1.0, 1.2], "x": [0., 1., 2., 3.],
                         "w": [1., 0.5, 2., 1.], "status": ["none", "left", "right", "none"]})
    response = {"weighted": "weighted(y, w)", "censored": "censored(y, status)",
                "truncated": "truncated(y, 0, 2)", "constrained": "constrained(y, 0, 2)"}[kind]
    model = bmb.Model(response + " ~ x", data, priors={"sigma": 0.8})
    description = _describe(model)
    model.build()
    point = fixed_point(model)
    args = mathematical_arguments(description, point)
    mean, sigma = args["mu"], args["sigma"]
    observed = description.data[:, 0]
    expected = stats.norm.logpdf(observed, mean, sigma)
    if kind == "weighted":
        expected *= description.data[:, 1]
    elif kind == "censored":
        status = description.data[:, 1]
        expected = np.where(status == -1, stats.norm.logcdf(observed, mean, sigma), expected)
        expected = np.where(status == 1, stats.norm.logsf(observed, mean, sigma), expected)
    else:
        lower, upper = description.data[:, 1], description.data[:, 2]
        expected -= np.log(stats.norm.cdf(upper, mean, sigma) - stats.norm.cdf(lower, mean, sigma))
    np.testing.assert_allclose(observed_logdensity(model, point), expected, rtol=1e-6)


def test_horseshoe_parameterization_and_improper_priors():
    model = make_model(priors={"x": bmb.Prior("Horseshoe", tau_nu=3, lam_nu=1)}, auto_scale=False)
    description = _describe(model)
    text = model.to_latex()
    assert "HalfStudentT" in text and r"\mathrm{x}_{\tau}" in text
    model.build()
    point = fixed_point(model)
    expected = independent_logdensity(description, mathematical_arguments(description, point))
    np.testing.assert_allclose(observed_logdensity(model, point), expected)
    for name in ("Flat", "HalfFlat"):
        flat = make_model(priors={"x": bmb.Prior(name)}, auto_scale=False).to_latex()
        assert r"\propto" in flat


def test_original_design_can_disprove_an_incorrect_representation():
    """A semantic control independent of whether the new method happens to exist."""
    model = make_model()
    description = _describe(model)
    term = next(t for t in description.predictors[0].terms if t.kind == "common")
    original = model.parameters["mu"].common_terms["x"].data
    np.testing.assert_allclose(term.data[:, 0], original - original.mean())
    assert not np.array_equal(term.data[:, 0], original)


def public_description_or_legacy_text(model):
    """The old public description provides a substantive fail-before comparison."""
    method = getattr(model, "to_latex", None)
    return method() if method is not None else str(model)


def test_public_response_and_predictor_equations():
    model = make_model()
    text = public_description_or_legacy_text(model)
    assert r"\begin{aligned}" in text and r"\sim" in text
    assert r"\eta_{mu,i}" in text
    assert not model.built


def test_public_group_hierarchy_and_offset_equations():
    model = make_model(formula="y ~ x + offset(x) + (1|group)")
    text = public_description_or_legacy_text(model)
    assert r"\sum_j" in text and "g(i)" in text
    assert r"\mathrm{1|group}" in text and "offset" in text


def test_public_distributional_equations():
    data = pd.DataFrame({"y": [0.4, 0.7, 1.0, 1.2], "x": [0., 1., 2., 3.]})
    model = bmb.Model(bmb.Formula("y ~ x", "sigma ~ x"), data)
    text = public_description_or_legacy_text(model)
    assert r"\eta_{mu,i}" in text and r"\eta_{sigma,i}" in text
    assert r"\exp(" in text


@pytest.mark.parametrize("kind", ["censored", "truncated", "constrained"])
def test_discrete_response_wrapper_includes_boundary_mass(kind):
    data = pd.DataFrame({"y": [1, 2, 3, 2], "x": [0., 1., 2., 3.],
                         "status": ["left", "none", "right", "none"]})
    response = {"censored": "censored(y, status)", "truncated": "truncated(y, 1, 4)",
                "constrained": "constrained(y, 1, 4)"}[kind]
    model = bmb.Model(response + " ~ x", data, family="poisson")
    description = _describe(model)
    model.build()
    point = fixed_point(model)
    mu = mathematical_arguments(description, point)["mu"]
    observed = description.data[:, 0]
    expected = stats.poisson.logpmf(observed, mu)
    if kind == "censored":
        status = description.data[:, 1]
        expected = np.where(status == -1, stats.poisson.logcdf(observed, mu), expected)
        expected = np.where(status == 1, stats.poisson.logsf(observed - 1, mu), expected)
        assert "F_0(y_i^-)" in model.to_latex()
    else:
        lower, upper = description.data[:, 1], description.data[:, 2]
        expected -= np.log(stats.poisson.cdf(upper, mu) - stats.poisson.cdf(lower - 1, mu))
    np.testing.assert_allclose(observed_logdensity(model, point), expected, rtol=1e-6)


def test_reference_softmax_uses_the_aliased_predictor_in_every_category():
    model = make_model("categorical")
    model.set_alias({"p": "chance"})
    text = model.to_latex()
    assert r"\eta_{chance,i,k}" in text
    assert r"\eta_{chance,i,j}" in text
    assert r"\eta_{chance,i,0}" in text
    assert r"\eta_{i,j}" not in text


@pytest.mark.parametrize("kind", ["truncated", "constrained"])
def test_truncated_serialization_defines_its_normalized_base_law(kind):
    frame = pd.DataFrame({"y": [0.4, 0.7, 1.0, 1.2], "x": [0., 1., 2., 3.]})
    model = bmb.Model(f"{kind}(y, 0, 2) ~ x", frame, priors={"sigma": 0.8})
    text = model.to_latex()
    assert r"p_0 &:= \operatorname{Normal}" in text
    assert r"p_0(y)" in text and r"\Pr_{p_0}" in text


@pytest.mark.parametrize("family", ["gaussian", "categorical"])
def test_factor_interaction_and_vector_coefficients_have_independent_numerics(family):
    model = make_model(family, formula="y ~ x * group")
    description = _describe(model)
    model.build()
    point = fixed_point(model)
    # Distinct entries expose coding/order errors hidden by identical coefficients.
    for name, value in point.items():
        if value.size > 1:
            point[name] = np.linspace(0.1, 0.6, value.size).reshape(value.shape)
    expected = independent_logdensity(description, mathematical_arguments(description, point))
    np.testing.assert_allclose(observed_logdensity(model, point), expected, rtol=1e-6)
    common = [term for term in description.predictors[0].terms if term.kind == "common"]
    assert any(":" in term.name for term in common)
    assert any(term.dimensions for term in common)


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


@pytest.mark.parametrize("cov", ["ExpQuad", "Matern32", "Matern52"])
@pytest.mark.parametrize("share_cov", [False, True])
@pytest.mark.parametrize("centered", [False, True])
def test_grouped_anisotropic_scaled_hsgp_has_independent_density(cov, share_cov, centered):
    frame = pd.DataFrame({"y": [0.2, 0.7, 1.1, 0.5, 1.4, 0.6, 0.8, 0.3],
                          "x": [-1., -0.8, -0.3, -0.1, 0.2, 0.4, 0.8, 1.],
                          "z": [0.4, -0.4, 0.2, -0.1, 0.8, 0.1, -0.3, 0.6],
                          "group": ["a", "b"] * 4})
    formula = ("y ~ 0 + hsgp(x, z, by=group, m=[2, 3], c=1.5, "
               f"cov='{cov}', iso=False, scale=True, share_cov={share_cov}, centered={centered})")
    model = bmb.Model(formula, frame, priors={"sigma": 0.9})
    original = next(iter(model.parameters["mu"].hsgp_terms.values()))
    model.set_priors({original.name: {"sigma": bmb.Prior("HalfNormal", sigma=1),
                                    "ell": bmb.Prior("HalfNormal", sigma=1)}})
    model.set_alias({original.name: "surface"})
    term = _describe(model).predictors[0].terms[0]
    attrs = dict(term.hsgp)
    model.build()
    point = model.backend.model.initial_point()
    for parameter in ("sigma", "ell"):
        key = "surface_" + parameter + "_log__"
        point[key] = np.linspace(np.log(0.6), np.log(1.1), point[key].size).reshape(point[key].shape)
    key = "surface_weights" + ("" if centered else "_raw")
    # Constant across basis columns: independent of PyMC's multi-index ordering.
    # Different group values still expose accidental group mixing or unsorting.
    point[key] = np.broadcast_to(np.array([0.2, -0.3]), point[key].shape).copy()
    sigma = np.exp(point["surface_sigma_log__"])
    ell = np.exp(point["surface_ell_log__"])
    contribution = np.empty(len(frame))
    for group in range(2):
        select = term.group_index == group
        data = (term.data[select] - attrs["mean"][group]) / attrs["maximum_distance"]
        basis, spectrum = independent_hsgp_basis_and_spectrum(
            data, attrs["L"][group], attrs["m"], ell if share_cov else ell[:, group],
            sigma if share_cov else sigma[group], cov)
        weights = point[key][:, group]
        if not centered:
            weights = weights * np.sqrt(spectrum)
        contribution[select] = basis @ weights
    expected = stats.norm.logpdf(frame.y, contribution, 0.9)
    np.testing.assert_allclose(observed_logdensity(model, point), expected, rtol=1e-6)
    assert attrs["share_cov"] == share_cov and not attrs["iso"] and attrs["scale"]


@pytest.mark.parametrize("cov", ["ExpQuad", "Matern32", "Matern52"])
def test_dropped_hsgp_basis_and_spectrum_have_independent_numerics(cov):
    data = np.array([[-1., -0.5], [-0.3, 0.2], [0.3, -0.2], [1., 0.5]])
    lengths, m, ell, sigma = np.array([2., 1.5]), [2, 3], np.array([0.6, 0.9]), 1.2
    constructor = {"ExpQuad": pm.gp.cov.ExpQuad, "Matern32": pm.gp.cov.Matern32,
                   "Matern52": pm.gp.cov.Matern52}[cov]
    covariance = sigma**2 * constructor(2, ls=ell)
    approximation = pm.gp.HSGP(m=m, L=list(lengths), drop_first=True, cov_func=covariance)
    actual_basis, actual_sqrt = approximation.prior_linearized(data)
    basis, spectrum = independent_hsgp_basis_and_spectrum(data, lengths, m, ell, sigma, cov, True)
    actual_basis = actual_basis.eval()
    actual_spectrum = actual_sqrt.eval()**2
    np.testing.assert_allclose(actual_basis @ actual_basis.T, basis @ basis.T,
                               rtol=1e-6, atol=1e-8)
    np.testing.assert_allclose((actual_basis * actual_spectrum) @ actual_basis.T,
                               (basis * spectrum) @ basis.T, rtol=1e-6, atol=1e-8)
    np.testing.assert_allclose(np.sort(actual_spectrum), np.sort(spectrum), rtol=1e-6)
    assert basis.shape[1] == np.prod(m) - 1




def test_rendering_preserves_a_genuinely_fitted_model_and_rng():
    model = make_model()
    inference = model.fit(draws=10, tune=10, chains=1, cores=1, random_seed=457,
                          progressbar=False, compute_convergence_checks=False)
    before = freeze((vars(model), inference))
    numpy_rng = copy.deepcopy(np.random.get_state())
    python_rng = random.getstate()
    assert model.to_latex() == model.to_latex()
    assert freeze((vars(model), inference)) == before
    assert freeze(np.random.get_state()) == freeze(numpy_rng)
    assert random.getstate() == python_rng


def test_large_observation_literals_are_bounded_without_truncating_the_ir():
    count = 200
    frame = pd.DataFrame({"y": np.linspace(0.2, 1.5, count),
                          "x": np.linspace(-1, 1, count),
                          "w": np.linspace(0.5, 1.5, count),
                          "group": ["g_" + str(i) for i in range(count)]})
    model = bmb.Model("weighted(y, w) ~ x + offset(x) + (1|group)", frame)
    description = _describe(model)
    text = model.to_latex()
    assert r"\ldots" in text and r"\mathrm{length}=200" in text
    assert len(text) < 14000
    np.testing.assert_array_equal(description.data[:, 1], frame.w)
    group = next(t for t in description.predictors[0].terms if t.kind == "group")
    assert len(group.group_index) == count and len(group.labels) == count
    offset = next(t for t in description.predictors[0].terms if t.kind == "offset")
    np.testing.assert_array_equal(offset.data.squeeze(), frame.x)
