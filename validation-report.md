Bambi #558 — retained hosted Docker validation results

Source: f091d78049d91f566e420a26ffd3847568973b73
Patch SHA256: 69abdeb1115eca3519c40c29e2e4512890294ab592c4054b67623634223adaf7
Run: https://github.com/leninworld/bambi/actions/runs/42880468780

Python 3.12 / CVM:    400 passed, 0 skipped
Python 3.12 / Numba:  400 passed, 0 skipped
Python 3.13 / CVM:    400 passed, 0 skipped
Python 3.13 / Numba:  400 passed, 0 skipped
All 389 unchanged original tests retained; 11 fallback cases execute.
Six deliberate regressions (Python 3.12/CVM lane): all detected; source restored after each.
Black 25.9 hook, full pre-commit, pylint: passed. Coverage: 91%.
Full documentation: 64 pages; 19 warnings also present in baseline.
All 50 owned container phases stopped; no real OOM/PID-limit events.
Slowest lane: 937.4 seconds. Peak: 7,552,421,888 bytes.

CPython 3.12.16/3.13.18; PyMC 6.4.0; PyTensor 3.3.8.
Native AMD64 Linux Docker on agent-controlled GitHub-hosted runners.

Tests only: real dependency-raiser fault injection and genuine small fits.
No natural numerical-failure reproduction or live parallel recovery.
Full docs build does not claim every notebook model fit executed.
