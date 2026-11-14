# Bambi causal extension — retained validation results

Source: `babde99afb10a2b542e6c993074313ac2139f476`.

[Actual validation run 43631328928](https://github.com/leninworld/bambi/actions/runs/43631328928), November 14, 2026. This report summarizes retained artifacts from that run; it is not a new execution.

## Full test matrix

| Lane | Passed | Existing skip | Failures/errors | XML time |
| --- | ---: | ---: | ---: | ---: |
| Python 3.12 / CVM | 535 | 1 | 0 | 714.495 s |
| Python 3.12 / Numba | 535 | 1 | 0 | 861.078 s |
| Python 3.13 / CVM | 535 | 1 | 0 | 684.438 s |
| Python 3.13 / Numba | 535 | 1 | 0 | 663.198 s |

Every lane reconciled 536 exact collected/executed/JUnit identities: 390 original tests and 146 added causal cases. The original `tests.test_model_construction::test_init_fallback` skip remains; the new causal cases had no skips. Each of five earlier feature gates also passed all 146 causal cases. The artifact's `baseline.xml` label refers to the applied candidate full suite, not a separate unchanged-source full-suite run. The unchanged-source check establishes feature absence, not a fabricated failing mathematical regression.

**91.42% line coverage of the `bambi` package** (causal modules: `effects.py` 95.4%, `validate.py` 93.8%, `plots.py` 79.2%, `types.py`/`__init__.py` 100%); branch coverage was not measured. The package total was 3,866 / 4,229 lines. Full hooks and pylint passed. Four full-suite peak memory measurements were 4,118,446,080; 7,441,395,712; 4,130,750,464; and 7,613,210,624 bytes, within their 12 GiB container limits.

## Scientific and installed-package checks

The API covers g-computation, ATE, ATT, ATU, CATE, GATE, binary risk differences/ratios/odds ratios, continuous mean differences and continuous-treatment dose–response. Tests exercise independent known-answer calculations, reference/membership/weight handling, marginal binary contrasts, native response means, invalid domains and training-state transformations.

Twenty real model-fit executions completed: ten across five feature gates, eight across four full suites, and two in the executed notebook. Feature/full-suite fits used two chains with 60 tuning and 60 posterior draws; the notebook used two chains with 250 tuning and 250 posterior draws. These are integration/oracle checks, not convergence-quality certification. The notebook reports zero divergences in its two fits; that does not establish general inference quality. The software requires explicit causal inputs and cannot establish causal assumptions. Posterior summaries condition on the supplied fixed reference population.

Actual CVM compilation and Numba native-function prerequisites passed. The locked xarray 2026.9.0 control reproduced the DatasetView/Dataset type-only assertion failure, preserved equal materialized snapshots, and rejected value, coordinate and attribute mutations. Materialized no-op comparisons did not waive mutation checks.

Built-wheel checks used an isolated consumer with editable imports removed, source paths excluded and actual installed public API/oracle tests. Inert orphan bytecode caches were retained and shown non-importable. The wheel archive itself was not retained, so the evidence is the actual runtime wheel/member and installed-source identity checks, not a new offline whole-wheel rehash.

## Notebook and documentation

The new executed notebook has 27 source cells, 13 executed code cells, 42 output records and nine authentic PNG outputs. Its SHA256 is `c9775d81a3a1b8e310c85509277cfdb78c288f411722cc86ba83d33e23d350a9`. All output bytes remain in the contribution.

The complete site rendered 68 pages and had zero broken local links. Its 464 external links were not fetched. Eighteen raw-HTML-table warnings, a notebook-ID warning and fourteen pandas future warnings remain. Existing notebooks rendered their stored outputs; they are not claimed freshly refitted.

All validation containers were confirmed stopped after the run. Five deliberate out-of-memory control cases were expected; no ordinary test process ran out of memory.

## Exact tested contribution

Nine postimages match the actual tested patch by bytes, SHA256 and Git blob identity. Tested patch: `8536a6e37c13ebcbe62eaf5cacd08d0409a86e25c9faf30f09e9ab2189213431` (406,565 bytes). Black formatting preserved all six Python ASTs; the executed notebook is retained intact.

| File | SHA256 |
| --- | --- |
| `bambi/causal/__init__.py` | `1d15b72e13d462c1e3d1df277b10018be2a732534cd9f8791fa43f8680771a68` |
| `bambi/causal/effects.py` | `4fc18c5fa524c5f43307065be4adb5705ab8b7dd3e9ba21f7f86b1aa3255e23c` |
| `bambi/causal/plots.py` | `d1b81a8d76c43493c63f593831563d56824e915446b8993ffdb174c21a859bd4` |
| `bambi/causal/types.py` | `3a8865a8c870d6e750ce3424a152864da8785a8ace1ded41af2d74c893e67de5` |
| `bambi/causal/validate.py` | `3263ad857d92bc88bd9f38871691c816bf268746c052b514db305e1d1f4c807b` |
| `docs/_quarto.yml` | `aa4d4aa77c79f7162cc6d2326858423153d3741bd90ce32fc00d57777f71dce8` |
| `docs/notebooks/causal_effects.ipynb` | `c9775d81a3a1b8e310c85509277cfdb78c288f411722cc86ba83d33e23d350a9` |
| `pyproject.toml` | `2fb2ca23c8987f126a4a709c68922280054158c42f5928fe490e065581506c7c` |
| `tests/test_causal.py` | `5c8648d622fa96acd0a67f20c63bd2244b26d6ba1c01a558708c05e69876fba1` |

## Visual evidence status

All nine authentic notebook figures were individually inspected and found legible. A native full-page validation-report screenshot was captured on November 14, 2026, and inspected for legibility and privacy. The screenshot preserves the earlier pending-status paragraph as it appeared at capture time. The retained site HTML lacks complete styling assets; styled Quarto appearance is not certified by this evidence.
