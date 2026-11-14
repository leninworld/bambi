"""A declarative mathematical description, independent of the inference backend.

The private records separate the model specification from its LaTeX serialization.
In particular, rendering never builds a model or evaluates a user-supplied callable.
"""

from dataclasses import dataclass
import math

import numpy as np

from bambi.defaults.families import BUILTIN_FAMILIES
from bambi.families.family import Family
from bambi.priors import Prior
from bambi.terms import CommonTerm, GroupSpecificTerm, HSGPTerm


@dataclass(frozen=True)
class _Named:
    """A user-defined function or an explicitly specified transform, not its execution."""

    name: str


@dataclass(frozen=True)
class _Prior:
    name: str
    distribution: str
    arguments: tuple
    dimensions: tuple = ()
    custom: bool = False
    noncentered: bool = False


@dataclass(frozen=True)
class _Term:
    kind: str
    name: str
    data: np.ndarray
    dimensions: tuple
    prior: _Prior | None = None
    group_index: np.ndarray | None = None
    labels: tuple = ()
    hsgp: tuple = ()


@dataclass(frozen=True)
class _Predictor:
    parameter: str
    name: str
    link: str
    terms: tuple
    centered: bool
    response_dimensions: tuple


@dataclass(frozen=True)
class _Expression:
    """A mathematical operation, shared by serialization and independent test evaluation."""

    operation: str
    operands: tuple


@dataclass(frozen=True)
class _Description:
    family: str
    distribution: str
    custom: bool
    response: str
    data: np.ndarray
    levels: tuple
    success: object
    predictors: tuple
    marginal: tuple
    response_kind: str
    potentials: tuple
    arguments: tuple


def _array(value):
    """Own the snapshot; never make a caller's array read-only or mutate it."""
    result = np.array(value, copy=True)
    result.setflags(write=False)
    return result


def _function_name(value):
    return getattr(value, "__qualname__", type(value).__name__)


def _value(value):
    if isinstance(value, np.ndarray):
        return _array(value)
    if isinstance(value, (str, int, float, bool, np.number)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_value(item) for item in value)
    if callable(value):
        return _Named(_function_name(value))
    # PyMC transforms are declarative prior arguments, not functions to evaluate.
    if type(value).__module__.startswith("pymc.") and ".transforms" in type(value).__module__:
        return _Named(getattr(value, "name", None) or type(value).__name__)
    raise ValueError(f"Cannot describe prior argument of type {type(value).__name__}")


def _prior(prior, name, dimensions=(), noncentered=False, aliases=None):
    if not isinstance(prior, Prior):
        raise ValueError(f"Cannot describe prior for {name!r}: expected a Prior")
    aliases = aliases or {}
    hyper_dimensions = tuple(dim for dim in dimensions if not dim[0].endswith("__factor_dim"))
    arguments = []
    for key, value in prior.args.items():
        if isinstance(value, Prior):
            label = name + "_" + aliases.get(key, key)
            value = _prior(value, label, hyper_dimensions, noncentered, aliases)
        else:
            value = _value(value)
        arguments.append((key, value))
    effective = prior.noncentered if prior.noncentered is not None else noncentered
    has_random_scale = any(key == "sigma" and isinstance(val, _Prior) for key, val in arguments)
    nc = bool(effective and has_random_scale)
    if nc and prior.name != "Normal":
        raise ValueError("Non-centered group priors are only defined for Normal distributions")
    return _Prior(
        name,
        _function_name(prior.dist) if prior.dist is not None else prior.name,
        tuple(arguments),
        tuple(dimensions),
        prior.dist is not None,
        nc,
    )


def _dimensions(coords):
    return tuple((name, tuple(values)) for name, values in coords.items())


def _term(term, response_dimensions, centered, noncentered):
    if not isinstance(term, (CommonTerm, GroupSpecificTerm, HSGPTerm)):
        raise ValueError(f"Cannot describe term of type {type(term).__name__}")
    name = term.alias or term.name
    if isinstance(term, HSGPTerm):
        if term.cov not in ("ExpQuad", "Matern32", "Matern52"):
            raise ValueError(f"Cannot describe HSGP covariance {term.cov!r}")
        dims = ((name + "_weights_dim", tuple(range(int(np.prod(term.m))))),)
        if term.by_levels is not None:
            dims += ((name + "_by", tuple(term.by_levels)),)
        dims += response_dimensions
        hyper_dims = ()
        if term.by_levels is not None and not term.share_cov:
            hyper_dims = ((name + "_by", tuple(term.by_levels)),)
        priors = []
        for param in ("sigma", "ell"):
            value = term.prior[param]
            param_dims = hyper_dims
            if param == "ell" and not term.iso and term.shape[1] > 1:
                param_dims = ((name + "_var", tuple(range(term.shape[1]))),) + param_dims
            priors.append(
                (param, _prior(value, name + "_" + param, param_dims))
                if isinstance(value, Prior)
                else (param, _value(value))
            )
        attrs = (
            ("m", tuple(int(x) for x in term.m)),
            ("L", _array(term.L)),
            ("mean", _array(term.mean)),
            ("scale", term.scale_predictors),
            ("maximum_distance", _value(term.maximum_distance)),
            ("drop_first", term.drop_first),
            ("centered", term.centered),
            ("share_cov", term.share_cov),
            ("iso", term.iso),
            ("cov", term.cov),
            ("priors", tuple(priors)),
        )
        return _Term(
            "hsgp", name, _array(term.data), dims,
            group_index=_array(term.by) if term.by is not None else None, hsgp=attrs
        )
    if isinstance(term, GroupSpecificTerm):
        coords = dict(term.coords)
        if term.alias:
            coords = {name + "__" + key.split("__", 1)[1]: val for key, val in coords.items()}
        dims = _dimensions(coords) + response_dimensions
        return _Term(
            "group", name, _array(term.predictor), dims,
            _prior(term.prior, name, dims, noncentered, term.hyperprior_alias),
            _array(term.group_index), tuple(str(x) for x in term.groups)
        )
    if not isinstance(term, CommonTerm):
        raise ValueError(f"Cannot describe term of type {type(term).__name__}")
    if term.kind == "offset":
        return _Term("offset", name, _array(term.data), ())
    dims = _dimensions(term.coords)
    if term.alias:
        dims = tuple((name + "_dim", values) for _, values in dims)
    dims += response_dimensions
    kind = "intercept" if term.kind == "intercept" else "common"
    matrix = _array(np.asarray(term.data).reshape(len(term.data), -1))
    # The centering applies only to common columns, never offsets or varying effects.
    if centered and kind == "common":
        matrix = _array(matrix - matrix.mean(axis=0))
    return _Term(kind, name, matrix, dims, _prior(term.prior, name, dims))


def _describe(model):
    """Snapshot the declarative public Model interface, without touching its backend."""
    family = model.family
    if not isinstance(family, Family):
        raise ValueError("Cannot describe an unrecognised model family")
    custom = type(family) is Family or family.likelihood.dist is not None
    if family.name not in BUILTIN_FAMILIES and not custom:
        raise ValueError(f"Cannot describe unrecognised family {family.name!r}")
    conditional = model.conditional_parameters
    marginal = model.marginal_parameters
    if set(model.parameters) != set(conditional) | set(marginal):
        raise ValueError("Cannot describe an unrecognised model parameter")
    response = model.response_term
    raw = _array(response.term.data)
    levels = tuple(response.levels or ()) if not custom else tuple(response.term.levels or ())
    success = response.success if not custom else None
    if not custom and family.name in ("categorical", "multinomial", "dirichlet_multinomial"):
        response_dimensions = _dimensions(response.coords)
    else:
        response_dimensions = ()
    predictors = []
    for parameter, component in conditional.items():
        required = ("terms", "intercept_term", "common_terms", "group_specific_terms", "hsgp_terms")
        if not all(hasattr(component, name) for name in required) or component.design is None:
            raise ValueError(f"Cannot describe model parameter {parameter!r}")
        centered = component.intercept_term is not None and model.center_predictors
        nc = model.noncentered
        if isinstance(nc, dict):
            nc = nc.get(parameter, True)
        terms = tuple(
            _term(term, response_dimensions, centered, nc) for term in component.terms.values()
        )
        link = family.link[parameter]
        predictors.append(
            _Predictor(parameter, component.alias or component.name, link.name, terms,
                       bool(centered), response_dimensions)
        )
    marginals = []
    for parameter, component in marginal.items():
        name = component.alias or component.name
        dims = ()
        if not custom and family.name in ("cumulative", "sratio") and parameter == "threshold":
            dims = ((name + "_dim", tuple(range(len(levels) - 1))),)
        value = component.prior
        if isinstance(value, Prior):
            value = _prior(value, name, dims)
        elif not isinstance(value, (int, float, np.number)):
            raise ValueError(f"Cannot describe marginal parameter {parameter!r}")
        marginals.append((parameter, name, value))
    kind = "ordinary"
    for candidate in ("censored", "truncated", "constrained", "weighted"):
        if getattr(response, "is_" + candidate):
            kind = candidate
    potentials = []
    for names, function in model.potentials or ():
        if isinstance(names, str):
            names = (names,)
        potentials.append((tuple(names), _function_name(function)))
    distribution = (
        _function_name(family.likelihood.dist)
        if family.likelihood.dist is not None else family.likelihood.name
    )
    return _Description(
        family.name, distribution, custom, response.alias or response.name, raw, levels,
        success, tuple(predictors), tuple(marginals), kind, tuple(potentials),
        _arguments(family, custom)
    )


def _arguments(family, custom):
    """The actual transformations between Bambi's and the likelihood's parameters."""
    def parameter(name):
        return _Expression("parameter", (name,))
    def operation(name, *operands):
        return _Expression(name, operands)
    args = {name: parameter(name) for name in family.likelihood.params}
    if custom:
        return tuple(args.items())
    name = family.name
    if name in ("beta", "beta_binomial"):
        mu, kappa = args.pop("mu"), args.pop("kappa")
        args["alpha"] = operation("multiply", mu, kappa)
        args["beta"] = operation("multiply", operation("subtract", 1, mu), kappa)
    elif name in ("gamma", "hurdle_gamma"):
        alpha = args.pop("alpha")
        args["sigma"] = operation("divide", args["mu"], operation("sqrt", alpha))
    elif name == "exponential":
        args["lam"] = operation("divide", 1, args.pop("mu"))
    elif name == "weibull":
        mu = args.pop("mu")
        args["beta"] = operation(
            "divide", mu, operation("gamma", operation("add", 1,
                                                       operation("divide", 1, args["alpha"])))
        )
    if name in ("binomial", "beta_binomial", "zero_inflated_binomial"):
        args["n"] = operation("data_column", 1)
    if name in ("multinomial", "dirichlet_multinomial"):
        args["n"] = operation("row_sum")
    if name in ("categorical", "multinomial"):
        args["p"] = operation("reference_softmax", operation("eta", "p"))
    if name in ("cumulative", "sratio"):
        args = {"p": operation("ordinal", name, family.link["p"].name,
                               operation("eta", "p"), parameter("threshold"))}
    return tuple(args.items())


def _escape(text):
    replacements = {
        "\\": r"\backslash{}", "{": r"\{", "}": r"\}", "_": r"\_",
        "%": r"\%", "&": r"\&", "#": r"\#", "$": r"\$", "^": r"\^{}",
        "~": r"\~{}",
    }
    return "".join(replacements.get(char, char) for char in str(text))


def _symbol(name):
    return r"\mathrm{" + _escape(name) + "}"


def _number(value):  # pylint: disable=too-many-return-statements
    if isinstance(value, _Named):
        return r"\operatorname{" + _escape(value.name) + "}"
    if isinstance(value, _Prior):
        return _symbol(value.name)
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return _number(value.item())
        if value.ndim > 2:
            return _number(tuple(_value(x) for x in value))
        if value.ndim == 1:
            if len(value) > 8:
                items = [_number(x) for x in value[:3]] + [r"\ldots", _number(value[-1])]
                return (r"\left[" + ", ".join(items) + r"\right]_{\mathrm{length}="
                        + str(len(value)) + "}")
            return r"\left[" + ", ".join(_number(x) for x in value) + r"\right]"
        return r"\begin{bmatrix}" + r" \\ ".join(
            " & ".join(_number(x) for x in row) for row in value
        ) + r"\end{bmatrix}"
    if isinstance(value, tuple):
        if len(value) > 8 and all(not isinstance(x, (tuple, np.ndarray)) for x in value):
            items = [_number(x) for x in value[:3]] + [r"\ldots", _number(value[-1])]
            return (r"\left[" + ", ".join(items) + r"\right]_{\mathrm{length}="
                    + str(len(value)) + "}")
        return r"\left[" + ", ".join(_number(x) for x in value) + r"\right]"
    if value is None:
        return r"\varnothing"
    if isinstance(value, (float, np.floating)):
        if math.isinf(value):
            return ("-" if value < 0 else "") + r"\infty"
        if math.isnan(value):
            return r"\operatorname{NaN}"
    if isinstance(value, (str, bool)):
        return _symbol(value)
    text = str(value)
    if isinstance(value, (float, np.floating)) and "e" in text.lower():
        mantissa, exponent = text.lower().split("e")
        return mantissa + r"\times10^{" + str(int(exponent)) + "}"
    return text


def _prior_lines(prior):
    lines = []
    for _, value in prior.arguments:
        if isinstance(value, _Prior):
            lines.extend(_prior_lines(value))
    label = _symbol(prior.name)
    if prior.distribution == "Horseshoe" and not prior.custom:
        args = dict(prior.arguments)
        lines.extend([
            label + r"_{\tau} &\sim \operatorname{HalfStudentT}(\nu="
            + _number(args.get("tau_nu", 3)) + ")",
            label + r"_{\lambda} &\sim \operatorname{HalfStudentT}(\nu="
            + _number(args.get("lam_nu", 1)) + ")",
            label + r"_{\mathrm{raw}} &\sim \mathcal N(0,1)",
            label + " &= " + label + r"_{\mathrm{raw}}" + label + r"_{\tau}"
            + label + r"_{\lambda}",
        ])
    elif prior.noncentered:
        sigma = dict(prior.arguments)["sigma"]
        lines.extend([
            label + r"_{\mathrm{offset}} &\sim \mathcal N(0,1)",
            label + " &= " + _number(sigma) + label + r"_{\mathrm{offset}}",
        ])
    else:
        args = ", ".join(_symbol(k) + "=" + _number(v) for k, v in prior.arguments
                         if k not in ("transform", "initval"))
        law = r"\operatorname{" + _escape(prior.distribution) + "}"
        if prior.custom:
            law += r"_{\mathrm{user\!\ defined}}"
        lines.append(label + r" &\sim " + law + r"\left(" + args + r"\right)")
        if prior.distribution in ("Flat", "HalfFlat") and not prior.custom:
            lines.append(r"p(" + label + r") &\propto "
                         + ("1" if prior.distribution == "Flat" else r"\mathbf1_{" + label + ">0}"))
    for key, value in prior.arguments:
        if key in ("transform", "initval"):
            lines.append(_symbol(key) + "(" + label + ") &= " + _number(value))
    for dim, values in prior.dimensions:
        lines.append(_symbol(dim) + r" &\in " + _number(tuple(str(x) for x in values)))
    return lines


def _hsgp_lines(term):
    a = dict(term.hsgp)
    label = _symbol(term.name)
    lines = []
    for param, value in a["priors"]:
        if isinstance(value, _Prior):
            lines.extend(_prior_lines(value))
        else:
            lines.append(_symbol(term.name + "_" + param) + " &= " + _number(value))
    group = r",g(i)" if term.group_index is not None else ""
    sigma = _symbol(term.name + "_sigma")
    ell = _symbol(term.name + "_ell")
    lines.extend([
        label + r"_{i} &= \sum_{j\in J}" + label + r"_{\phi,j}(\widetilde x_i)"
        + label + r"_{w,j" + group + "}",
        r"\overline x_g &= " + _number(a["mean"]),
        r"\widetilde x_i &= (x_i-\overline x_{g(i)})"
        + ("/" + _number(a["maximum_distance"]) if a["scale"] else ""),
        r"L &= " + _number(a["L"]),
        r"D &= " + str(term.data.shape[1]) + r",\quad m=" + _number(a["m"]),
        r"J &= \prod_{d=1}^D\{1,\ldots,m_d\}"
        + (r"\setminus\{(1,\ldots,1)\}" if a["drop_first"] else ""),
        label + r"_{\omega,j,d} &= \frac{\pi j_d}{2L_{g(i),d}}",
        label + r"_{\phi,j}(x) &= \prod_d L_{g(i),d}^{-1/2}"
        + r"\sin\left(\frac{\pi j_d(x_d+L_{g(i),d})}{2L_{g(i),d}}\right)",
    ])
    if a["cov"] == "ExpQuad":
        spectrum = sigma + r"^2(2\pi)^{D/2}\prod_d " + ell + r"_d"
        spectrum += r"\exp\left[-\tfrac12\sum_d(" + ell + r"_d\omega_d)^2\right]"
    else:
        nu = "3/2" if a["cov"] == "Matern32" else "5/2"
        spectrum = (
            sigma + r"^2\frac{2^D\pi^{D/2}\Gamma(\nu+D/2)(2\nu)^\nu}{\Gamma(\nu)}"
            + r"\prod_d " + ell + r"_d\left[2\nu+\sum_d("
            + ell + r"_d\omega_d)^2\right]^{-\nu-D/2},\quad\nu=" + nu
        )
    if term.group_index is not None and not a["share_cov"]:
        lines.append(r"(\sigma,\ell) &= (" + sigma + r"_{g(i)},"
                     + ell + r"_{g(i)})")
    else:
        lines.append(r"(\sigma,\ell) &= (" + sigma + "," + ell + ")")
    if a["iso"]:
        lines.append(r"\ell_d &= \ell\quad(d=1,\ldots,D)")
    spectrum = spectrum.replace(sigma, r"\sigma").replace(ell, r"\ell")
    lines.append(label + r"_S(\omega) &= " + spectrum)
    lines.append(r"\operatorname{weight\ index\ order} &= "
                 + _symbol(term.name + "_weights_dim")
                 + r"\text{ (backend coordinate order)}")
    if a["centered"]:
        lines.append(label + r"_{w,j" + group + r"} &\sim \mathcal N(\mu=0,\sigma=\sqrt{"
                     + label + r"_S(\omega_j)})")
    else:
        lines.extend([
            label + r"_{z,j" + group + r"} &\sim \mathcal N(0,1)",
            label + r"_{w,j" + group + "} &= " + label + r"_{z,j" + group
            + r"}\sqrt{" + label + r"_S(\omega_j)}",
        ])
    if term.group_index is not None:
        lines.append(r"g(i) &= " + _number(term.group_index))
        lines.append(r"\operatorname{shared\ covariance} &= " + _number(a["share_cov"]))
    else:
        lines.append(r"g(i) &= 0")
    lines.append(r"\operatorname{isotropic\ lengthscale} &= " + _number(a["iso"]))
    return lines


def _link_line(predictor):
    parameter = _symbol(predictor.name) + "_i"
    eta = r"\eta_{" + _escape(predictor.name) + ",i}"
    links = {
        "identity": eta,
        "log": r"\exp(" + eta + ")",
        "inverse": "1/(" + eta + ")",
        "inverse_squared": "1/\\sqrt{" + eta + "}",
        "logit": "1/(1+\\exp(-" + eta + "))",
        "probit": r"\Phi(" + eta + ")",
        "cloglog": r"1-\exp[-\exp(" + eta + ")]",
    }
    if predictor.link == "softmax":
        return None  # Reference-category padding is specified with the response law.
    expression = links.get(predictor.link)
    if expression is None:
        expression = _symbol(predictor.link) + r"^{-1}_{\mathrm{user\!\ defined}}(" + eta + ")"
    elif predictor.link in ("logit", "probit", "cloglog"):
        expression = r"\mathcal B\left(" + expression + r"\right)"
    return parameter + " &= " + expression


def _likelihood_lines(description):
    d = description
    names = {p.parameter: p.name for p in d.predictors}
    names.update({key: name for key, name, _ in d.marginal})
    def p(name):
        return _symbol(names.get(name, name)) + ("_i" if any(
            item.parameter == name for item in d.predictors
        ) else "")
    def expression(value):  # pylint: disable=too-many-return-statements
        if not isinstance(value, _Expression):
            return _number(value)
        op, operands = value.operation, value.operands
        if op == "parameter":
            return p(operands[0])
        if op == "eta":
            return r"\eta_{" + _escape(names[operands[0]]) + ",i}"
        if op in ("data_column", "row_sum"):
            return "n_i"
        if op in ("reference_softmax", "ordinal"):
            return r"\mathbf p_i"
        terms = [expression(item) for item in operands]
        if op == "add":
            return "(" + terms[0] + "+" + terms[1] + ")"
        if op == "subtract":
            return "(" + terms[0] + "-" + terms[1] + ")"
        if op == "multiply":
            return terms[0] + terms[1]
        if op == "divide":
            return r"\frac{" + terms[0] + "}{" + terms[1] + "}"
        if op == "sqrt":
            return r"\sqrt{" + terms[0] + "}"
        if op == "gamma":
            return r"\Gamma(" + terms[0] + ")"
        raise ValueError(f"Cannot describe mathematical operation {op!r}")
    args = {name: expression(value) for name, value in d.arguments}
    extra = []
    if not d.custom:
        if d.family in ("beta", "beta_binomial"):
            extra += [r"\alpha_i &= " + args["alpha"], r"\beta_i &= " + args["beta"]]
        elif d.family in ("gamma", "hurdle_gamma"):
            extra.append(r"\sigma_i &= " + args["sigma"])
        elif d.family == "exponential":
            extra.append(r"\lambda_i &= " + args["lam"])
        elif d.family == "weibull":
            extra.append(r"\beta_i &= " + args["beta"])
        if d.family in ("binomial", "beta_binomial", "zero_inflated_binomial"):
            args["n"] = "n_i"
            extra.append(r"n_i &= " + _number(d.data[:, 1]))
        if d.family in ("multinomial", "dirichlet_multinomial"):
            args["n"] = "n_i"
            extra.append(r"n_i &= " + _number(d.data.sum(axis=1)))
        if d.family in ("categorical", "multinomial"):
            eta = r"\eta_{" + _escape(names["p"]) + ",i,k}"
            eta_j = r"\eta_{" + _escape(names["p"]) + ",i,j}"
            eta_zero = r"\eta_{" + _escape(names["p"]) + ",i,0}"
            extra += [eta_zero + " &= 0", p("p") + r"_k &= \frac{\exp(" + eta
                      + r")}{1+\sum_{j=1}^{K-1}\exp(" + eta_j + ")}",
                      p("p") + r"_0 &= 1-\sum_{k=1}^{K-1}" + p("p") + "_k"]
        if d.family in ("cumulative", "sratio"):
            eta = r"\eta_{" + _escape(names["p"]) + ",i}"
            threshold = p("threshold")
            link = next(item.link for item in d.predictors if item.parameter == "p")
            cdf = {"logit": r"\operatorname{logistic}", "probit": r"\Phi",
                   "cloglog": r"\operatorname{invcloglog}"}.get(link, _symbol(link) + "^{-1}")
            if link in ("logit", "probit", "cloglog"):
                extra.append(r"F(t) &= \mathcal B(" + cdf + "(t))")
                cdf = "F"
            if d.family == "cumulative":
                equation = cdf + "(" + threshold + "_k-" + eta + ")-" + cdf
                equation += "(" + threshold + "_{k-1}-" + eta + ")"
                extra.append(threshold + r"_{-1} &=-\infty")
            else:
                equation = cdf + "(" + threshold + "_k-" + eta + r")\prod_{j<k}[1-"
                equation += cdf + "(" + threshold + "_j-" + eta + ")]"
            extra += [r"p_{i,k} &= " + equation,
                      r"p_{i,K-1} &= 1-\sum_{k=0}^{K-2}p_{i,k}"]
            args = {"p": r"p_i"}
        if d.family == "bernoulli":
            extra.append(r"\operatorname{success} &= " + _number(d.success))
    law_name = "Categorical" if d.family in ("cumulative", "sratio") and not d.custom else d.distribution
    law = r"\operatorname{" + _escape(law_name) + "}"
    if d.custom:
        law += r"_{\mathrm{user\!\ defined}}"
    law += r"\left(" + ", ".join(_symbol(k) + "=" + v for k, v in args.items()) + r"\right)"
    response = _symbol(d.response) + "_i"
    if d.response_kind == "weighted":
        lines = [r"\log L_i &= w_i\log p_0(" + response + r"\mid\theta)",
                 r"p_0 &:= " + law, r"w_i &= " + _number(d.data[:, 1])]
    elif d.response_kind in ("truncated", "constrained"):
        lines = [r"p_0 &:= " + law,
                 response + r" &\sim " + law + r"\mid\ell_i\leq Y_i\leq u_i",
                 r"p_i(y) &= \frac{p_0(y)}{\Pr_{p_0}(\ell_i\leq Y_i\leq u_i)}",
                 r"\ell_i &= " + _number(d.data[:, 1]), r"u_i &= " + _number(d.data[:, 2])]
    elif d.response_kind == "censored":
        lines = [r"p_0 &:= " + law,
                 r"L_i &= \begin{cases}F_0(y_i)&c_i=-1\\1-F_0(y_i^-)&c_i=1\\"
                 + r"p_0(y_i)&c_i=0\end{cases}",
                 r"F_0(y^-) &= \Pr_{p_0}(Y<y)", r"c_i &= " + _number(d.data[:, 1])]
    else:
        lines = [response + r" &\sim " + law]
    if not d.custom and (d.family.startswith("hurdle_") or d.family.startswith("zero_inflated_")):
        psi = p("psi")
        base = d.distribution.removeprefix("Hurdle").removeprefix("ZeroInflated")
        base_args = ", ".join(_symbol(k) + "=" + v for k, v in args.items() if k != "psi")
        extra.append(r"p_{\rm base} &:= \operatorname{" + base + "}(" + base_args + ")")
        if d.family.startswith("hurdle_"):
            extra += [r"\Pr(Y_i=0) &= 1-" + psi,
                      r"p(Y_i=y>0) &= " + psi + r"\frac{p_{\rm base}(y)}{\Pr_{\rm base}(Y>0)}"]
        else:
            extra += [r"\Pr(Y_i=0) &= 1-" + psi + "+" + psi + r"p_{\rm base}(0)",
                      r"p(Y_i=y>0) &= " + psi + r"p_{\rm base}(y)"]
    if d.levels:
        extra.append(r"\operatorname{response\ levels} &= " + _number(d.levels))
    return lines + extra


def _to_latex(description):
    lines = _likelihood_lines(description)
    if any(p.link in ("logit", "probit", "cloglog") for p in description.predictors):
        lines.extend([
            r"\mathcal B(q) &= \begin{cases}\epsilon&q=0\\1-\epsilon&q=1"
            r"\\q&\text{otherwise}\end{cases}",
            r"\epsilon &= " + _number(np.finfo(np.float64).eps),
        ])
    for predictor in description.predictors:
        summands = []
        for term in predictor.terms:
            label = _symbol(term.name)
            if term.kind == "intercept":
                summands.append(label)
            elif term.kind == "common":
                summands.append(r"\sum_j X^{(" + _escape(term.name) + r")}_{ij}" + label + "_j")
                lines.append("X^{(" + _escape(term.name) + r")} &\in\mathbb R^{"
                             + str(term.data.shape[0]) + r"\times " + str(term.data.shape[1]) + "}")
            elif term.kind == "group":
                summands.append(r"\sum_j Z^{(" + _escape(term.name) + r")}_{ij}"
                                + label + "_{g(i),j}")
                lines.append("g(i) &= " + _number(term.group_index))
                lines.append(r"\operatorname{groups} &= " + _number(term.labels))
            elif term.kind == "offset":
                summands.append(label + "_i")
                lines.append(label + "_i &= " + _number(term.data))
            elif term.kind == "hsgp":
                summands.append(label + "_i")
                lines.extend(_hsgp_lines(term))
            else:
                raise ValueError(f"Cannot describe term kind {term.kind!r}")
            if term.prior is not None:
                lines.extend(_prior_lines(term.prior))
        eta = r"\eta_{" + _escape(predictor.name) + ",i}"
        lines.append(eta + " &= " + (" + ".join(summands) or "0"))
        if predictor.centered and any(t.kind == "common" for t in predictor.terms):
            lines.append(r"X_{ij} &= X^{\rm original}_{ij}-\overline X^{\rm original}_{j}")
        link = _link_line(predictor)
        is_ordinal_parent = description.family in ("cumulative", "sratio") and predictor.parameter == "p"
        if link is not None and not is_ordinal_parent:
            lines.append(link)
    for _, name, value in description.marginal:
        if isinstance(value, _Prior):
            lines.extend(_prior_lines(value))
        else:
            lines.append(_symbol(name) + " &= " + _number(value))
    for names, function in description.potentials:
        args = ",".join(_symbol(name) for name in names)
        lines.append(r"\log\widetilde p &\mathrel{+}= " + _symbol(function)
                     + r"_{\rm user\!\ defined}(" + args + ")")
    return r"\begin{aligned}" + "\n" + " \\\\\n".join(lines) + "\n" + r"\end{aligned}"


def model_to_latex(model):
    """Render the complete current declarative model; inference state is not consulted."""
    return _to_latex(_describe(model))
