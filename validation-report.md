# Bambi #717 — validation results

Source: `bambinos/bambi@babde99afb10a2b542e6c993074313ac2139f476` (MAIN).
Actual run: [43533743624](https://github.com/leninworld/bambi/actions/runs/43533743624).

- **Four complete test lanes:** Python 3.12 and 3.13, each with PyTensor `cvm` and `numba`. Every lane collected the same 390 tests and completed **389 passed, 1 original upstream skip**; no failure or error. The retained skip is `test_init_fallback` (“this example no longer trigger the fallback to adapt_diag”).
- **Five full fits:** two PyMC reference models and three Bambi models, each with 4 chains, 4,000 tuning steps and 1,000 posterior draws per chain; `target_accept=0.9`.
- **Numerical checks:** zero divergences; maximum R-hat 1.00338909; minimum bulk ESS 1062.50, tail ESS 1423.28. All six like-for-like posterior comparisons passed their four-combined-MCSE criterion. Explicit prior-density and joint-factorization checks passed.
- **Sampler routing:** real PyMC NUTS/adaptation construction received 0.9 through both direct and Bambi routes. The misspelled-key control did not receive 0.9; the old top-level form also worked, so the explicit dictionary is a consistency choice.
- **Executed example:** all 26 notebook cells (13 code cells), 9 raster plots and 4 model graphs; fresh thumbnail. Code and Markdown sources exactly matched the reviewed inputs. Final four contribution files passed hooks without changes.
- **Documentation:** full Quarto 1.8.26 / quartodoc build, 65 HTML pages, zero broken local resources; mediation page and gallery tile verified.
- **Checks:** all-file pre-commit, pylint, complete pytest coverage commands, JUnit/collection identity and coverage reports passed. All 52 owned phase containers were confirmed stopped; the five deliberate OOM controls were isolated and there were no unexpected OOMs.

Validation used the retained synthetic dependency lock `fca13390…`, with PyMC 6.4.0, PyTensor 3.3.8, ArviZ 1.3.0 and arviz_stats 1.3.3. This lock is validation-only, is not an upstream tracked file, and does not claim to reproduce a fresh upstream dependency solve. Codecov upload, external-URL checks and HTML-fragment checks were not performed. The complete site build passed; the later retained local HTML preview lacks the full CSS/JavaScript bundle and other gallery thumbnails.

Final tested files (SHA-256):

- `docs/_quarto.yml` — `0ee03333fef671c4d0ed989c56dbfcbf9ba198856d7db4db2a8f8cf737632841`
- `docs/notebooks/gallery.yml` — `6a049aba9eb1a3597eb8c9fa478320f447065f1fd2ce9b23a2bcd691db3bb002`
- `docs/notebooks/mediation_analysis.ipynb` — `e1c7f47f5b32dcf61da25d0ea0efaa83a9a455e3ba3ae57d2aa76851ef436c76`
- `docs/notebooks/thumbnails/mediation_analysis.png` — `43ea4d24ec50d7b8bd4d21ef0caff8fdf1ebc54bdcbb9fb5dcc1d86c93f695bc`
