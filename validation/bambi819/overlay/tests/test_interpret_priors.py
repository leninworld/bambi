"""Prior interpretation uses actual unfitted native prediction graphs."""

import matplotlib
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from matplotlib import pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg

import bambi as bmb
from bambi.interpret import (
    comparisons,
    plot_comparisons,
    plot_predictions,
    plot_slopes,
    predictions,
    slopes,
)

matplotlib.use("Agg")


def make_model(formula="y ~ x", family="gaussian", priors=None, **kwargs):
    data = pd.DataFrame(
        {
            "x": [-1.0, 0.0, 1.0, 2.0] * 2,
            "y": [0.2, 0.5, 1.4, 1.7, 0.5, 0.8, 1.1, 2.1],
            "group": ["a"] * 4 + ["b"] * 4,
            "n": [5] * 8,
            "k": [0, 1, 3, 5, 1, 2, 4, 5],
            "exposure": [1.0, 2.0, 3.0, 4.0] * 2,
            "category": pd.Categorical(["a", "b", "c", "a"] * 2),
        }
    )
    model = bmb.Model(
        formula,
        data,
        family=family,
        priors=priors,
        center_predictors=False,
        **kwargs,
    )
    model.build()
    return model


@pytest.fixture(scope="module")
def prior_case():
    model = make_model(
        priors={
            "Intercept": bmb.Prior("Normal", mu=0, sigma=1),
            "x": bmb.Prior("Normal", mu=0, sigma=0.5),
            "sigma": bmb.Prior("HalfNormal", sigma=0.5),
        },
        auto_scale=False,
    )
    return model, model.prior_predictive(draws=24, random_seed=819)


def latent_only(idata):
    return xr.DataTree.from_dict({"prior": idata["prior"].to_dataset().copy(deep=True)})


def observation_array(values):
    return xr.DataArray(
        np.asarray(values), dims="__obs__", coords={"__obs__": np.arange(len(values))}
    )


def linear_draws(idata, x):
    prior = idata["prior"].to_dataset()
    return prior.Intercept + prior.x * observation_array(x)


@pytest.mark.parametrize("kind", ["response_params", "response"])
@pytest.mark.parametrize("new_data", [False, True])
def test_prior_native_prediction(prior_case, kind, new_data):
    model, idata = prior_case
    source = latent_only(idata)
    original = source.copy(deep=True)
    data = pd.DataFrame({"x": [-0.5, 0.5]}) if new_data else None
    result = model.predict(
        source, kind=kind, data=data, sample_group="prior", inplace=False, random_seed=19
    )
    group = "prior_predictions" if new_data else "prior"
    expected = linear_draws(source, data.x if new_data else model.data.x)
    xr.testing.assert_allclose(result[group]["mu"], expected.transpose("chain", "draw", "__obs__"))
    if kind == "response":
        response_group = group if new_data else "prior_predictive"
        assert result[response_group]["y"].sizes["__obs__"] == len(expected["__obs__"])
    xr.testing.assert_identical(source, original)


@pytest.mark.parametrize("prob", [None, [0.5, 0.9]])
@pytest.mark.parametrize("function", [predictions, comparisons, slopes])
def test_prior_summaries(prior_case, function, prob):
    model, idata = prior_case
    kwargs = {"conditional": {"x": [0.0, 1.0]}}
    if function is comparisons:
        kwargs = {"contrast": {"x": [0.0, 1.0]}}
    elif function is slopes:
        kwargs = {"wrt": {"x": 0.0}}
    result = function(
        model,
        latent_only(idata),
        sample_group="prior",
        transforms={"y": lambda x: x + 2},
        average_by="x" if function is predictions else None,
        prob=prob,
        **kwargs,
    )
    assert not result.summary.empty
    assert "prior_predictions" in result.draws.children
    assert np.isfinite(result.summary.estimate).all()
    intervals = result.summary.filter(regex="^(lower|upper)")
    assert intervals.empty == (prob is None)
    if function is predictions:
        expected = linear_draws(idata, [0.0, 1.0]) + 2
        np.testing.assert_allclose(
            result.summary.estimate, expected.mean(("chain", "draw")), rtol=1e-10
        )
    else:
        np.testing.assert_allclose(
            result.summary.estimate,
            float(idata["prior"]["x"].mean()) + (2 if function is comparisons else 0),
            rtol=1e-5,
        )


@pytest.fixture(scope="module")
def plot_prior_case():
    model = make_model(
        "y ~ x + exposure",
        priors={
            "Intercept": bmb.Prior("Normal", mu=0, sigma=1),
            "x": bmb.Prior("Normal", mu=0, sigma=0.5),
            "exposure": bmb.Prior("Normal", mu=0, sigma=0.2),
            "sigma": bmb.Prior("HalfNormal", sigma=0.5),
        },
        auto_scale=False,
    )
    return model, model.prior_predictive(draws=24, random_seed=819)


@pytest.mark.parametrize("function", [plot_predictions, plot_comparisons, plot_slopes])
def test_prior_plots(plot_prior_case, function):
    model, idata = plot_prior_case
    kwargs = {"conditional": "x" if function is plot_predictions else "exposure"}
    if function is plot_comparisons:
        kwargs["contrast"] = "x"
    elif function is plot_slopes:
        kwargs["wrt"] = "x"
    figure = function(
        model, latent_only(idata), sample_group="prior", prob=[0.5, 0.9], **kwargs
    )
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    assert np.asarray(canvas.buffer_rgba()).size > 0
    plt.close(figure)


def test_prior_automatic_selection_and_no_mutation(prior_case):
    model, idata = prior_case
    source = latent_only(idata)
    before = source.copy(deep=True)
    model_data = model.data.copy(deep=True)
    named_vars = tuple(model.backend.model.named_vars)
    priors = repr(model)
    result = predictions(model, source, conditional={"x": [0.0, 1.0]})
    assert "prior_predictions" in result.draws.children
    xr.testing.assert_identical(source, before)
    pd.testing.assert_frame_equal(model.data, model_data)
    assert tuple(model.backend.model.named_vars) == named_vars
    assert repr(model) == priors


def test_source_preference_and_unrelated_groups(prior_case):
    model, idata = prior_case
    both = idata.copy(deep=True)
    # Synthetic posterior coefficients deliberately differ; this tests selection,
    # not posterior fitting (covered separately by a real fit control).
    both["posterior"] = idata["prior"].to_dataset().copy(deep=True)
    both["posterior"]["Intercept"] = both["posterior"]["Intercept"] + 3
    posterior_before = both["posterior"].to_dataset().copy(deep=True)
    kwargs = {"data": pd.DataFrame({"x": [0.0]}), "inplace": False}
    default = model.predict(both, **kwargs)
    explicit = model.predict(both, sample_group="posterior", **kwargs)
    prior = model.predict(both, sample_group="prior", **kwargs)
    xr.testing.assert_equal(default["predictions"], explicit["predictions"])
    np.testing.assert_allclose(
        default["predictions"]["mu"] - prior["prior_predictions"]["mu"], 3
    )
    xr.testing.assert_identical(prior["posterior"].to_dataset(), posterior_before)
    np.testing.assert_allclose(
        predictions(model, both, conditional={"x": [0.0]}).summary.estimate,
        predictions(model, both, conditional={"x": [0.0]}, sample_group="prior")
        .summary.estimate
        + 3,
    )


@pytest.mark.parametrize("selection", [{"draws": 7}, {"thin": 3}])
def test_prior_draw_selection(prior_case, selection):
    model, idata = prior_case
    both = idata.copy(deep=True)
    both["posterior"] = idata["prior"].to_dataset().copy(deep=True)
    before = both["posterior"].to_dataset().copy(deep=True)
    result = model.predict(both, sample_group="prior", inplace=False, **selection)
    assert result["prior"].sizes["draw"] < idata["prior"].sizes["draw"]
    assert result["prior_predictive"].sizes["draw"] == result["prior"].sizes["draw"]
    xr.testing.assert_identical(result["posterior"].to_dataset(), before)
    with pytest.raises(ValueError, match="larger than"):
        model.predict(idata, sample_group="prior", inplace=False, draws=25)


@pytest.mark.parametrize("source", [None, "prior", "posterior", "invalid"])
def test_prior_source_errors(prior_case, source):
    model, idata = prior_case
    response_only = xr.DataTree.from_dict(
        {"prior_predictive": idata["prior_predictive"].to_dataset()}
    )
    with pytest.raises(ValueError, match="(latent draws|sample_group)"):
        model.predict(response_only, sample_group=source, inplace=False)
    with pytest.raises(ValueError, match="(latent draws|sample_group)"):
        predictions(model, response_only, sample_group=source)


def test_prior_seed(prior_case):
    model, idata = prior_case
    kwargs = dict(sample_group="prior", kind="response", inplace=False, random_seed=1919)
    a = model.predict(idata, **kwargs)
    b = model.predict(idata, **kwargs)
    xr.testing.assert_equal(a["prior_predictive"], b["prior_predictive"])


@pytest.mark.parametrize("sparse", [False, True])
def test_prior_groups_and_omitted_offsets(sparse):
    previous = bmb.config["SPARSE_DOT"]
    try:
        bmb.config["SPARSE_DOT"] = sparse
        model = make_model("y ~ x + (1|group)")
        idata = model.prior_predictive(draws=12, omit_offsets=True, random_seed=819)
        assert not any(name.endswith("_offset") for name in idata["prior"].data_vars)
        source = latent_only(idata)
        before = source.copy(deep=True)
        for groups in (["a", "b"], ["new", "new"], [np.nan, "a"]):
            data = pd.DataFrame({"x": [0.0, 1.0], "group": groups})
            result = model.predict(
                source, data=data, sample_group="prior", inplace=False, random_seed=819
            )
            population = model.predict(
                source,
                data=data,
                sample_group="prior",
                include_group_specific=False,
                inplace=False,
            )
            expected = linear_draws(source, data.x).transpose("chain", "draw", "__obs__")
            xr.testing.assert_allclose(population["prior_predictions"]["mu"], expected)
            assert np.isfinite(result["prior_predictions"]["mu"]).all()
            if groups == ["a", "b"]:
                assert not np.allclose(result["prior_predictions"]["mu"], expected)
        effects = predictions(
            model, source, sample_group="prior", include_group_specific=False
        )
        assert np.isfinite(effects.summary.estimate).all()
        xr.testing.assert_identical(source, before)
    finally:
        bmb.config["SPARSE_DOT"] = previous


def test_prior_distributional_aliases():
    model = make_model(bmb.Formula("y ~ x", "sigma ~ x"))
    model.set_alias(
        {"mu": {"x": "slope"}, "sigma": {"Intercept": "log_sd", "x": "log_sd_x", "sigma": "sd"}}
    )
    model.build()
    idata = model.prior_predictive(draws=12, random_seed=819)
    result = predictions(model, latent_only(idata), target="sigma", sample_group="prior")
    expected = np.exp(
        idata["prior"]["log_sd"]
        + idata["prior"]["log_sd_x"] * observation_array(model.data.x.values)
    ).transpose("chain", "draw", "__obs__")
    xr.testing.assert_allclose(result.draws["prior_predictions"]["sd"], expected)


def test_prior_nonlinear_intermediate():
    model = make_model(
        bmb.Formula("y ~ a + b * exp(-x)", "a ~ 1 + x", nlpars=("a", "b")),
        priors={
            "a": {"Intercept": bmb.Prior("Normal", mu=0, sigma=1), "x": bmb.Prior("Normal", mu=0, sigma=1)},
            "b": bmb.Prior("Normal", mu=0, sigma=1),
        },
    )
    idata = model.prior_predictive(draws=12, random_seed=819)
    result = predictions(model, latent_only(idata), sample_group="prior")
    x = observation_array(model.data.x.values)
    expected = idata["prior"]["a_Intercept"] + idata["prior"]["a_x"] * x + idata["prior"]["b"] * np.exp(-x)
    xr.testing.assert_allclose(
        result.draws["prior_predictions"]["mu"], expected.transpose("chain", "draw", "__obs__")
    )


@pytest.mark.parametrize("trials", ["n", "5"])
def test_prior_binomial(trials):
    model = make_model(f"prop(k, {trials}) ~ x", family="binomial")
    idata = model.prior_predictive(draws=12, random_seed=819)
    result = predictions(
        model,
        latent_only(idata),
        target=model.response_term.label,
        sample_group="prior",
        random_seed=19,
    )
    samples = result.draws["prior_predictions"][model.response_term.label]
    assert np.isfinite(samples).all()
    assert float(samples.min()) >= 0
    assert float(samples.max()) <= (1 if trials == "n" else 5)


def test_prior_categorical():
    model = make_model("category ~ x", family="categorical")
    idata = model.prior_predictive(draws=12, random_seed=819)
    result = predictions(model, latent_only(idata), sample_group="prior")
    parent = result.draws["prior_predictions"][model.family.likelihood.parent]
    response_dims = [dim for dim in parent.dims if dim not in ("chain", "draw", "__obs__")]
    assert len(response_dims) == 1
    np.testing.assert_allclose(parent.sum(response_dims[0]), 1)
    assert len(result.summary) == len(model.data) * parent.sizes[response_dims[0]]


def test_prior_offset():
    model = make_model("y ~ x + offset(exposure)")
    idata = model.prior_predictive(draws=12, random_seed=819)
    result = predictions(model, latent_only(idata), sample_group="prior")
    expected = linear_draws(idata, model.data.x) + observation_array(model.data.exposure.values)
    xr.testing.assert_allclose(
        result.draws["prior_predictions"]["mu"], expected.transpose("chain", "draw", "__obs__")
    )


def test_preservation_real_posterior_fit():
    """Explicit unpatched control: this really runs MCMC, not mock_pymc_sample."""
    model = make_model()
    idata = model.fit(draws=50, tune=50, chains=1, cores=1, random_seed=819)
    source = idata.copy(deep=True)
    result = predictions(model, idata, conditional={"x": [0.0, 1.0]}, prob=None)
    assert "predictions" in result.draws.children
    xr.testing.assert_identical(idata, source)


def test_preservation_native_prior_output(prior_case):
    """Explicit unpatched control for the native unfitted sample transport."""
    model, idata = prior_case
    assert model.built
    assert "prior" in idata.children
    assert "prior_predictive" in idata.children
    assert "posterior" not in idata.children
