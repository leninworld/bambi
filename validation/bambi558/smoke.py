"""Real import/compiler/construction prerequisite smoke, without getter logic."""
import importlib.metadata as md
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

import bambi as bmb
import jax
import numba
import nutpie
import preliz
import numpy as np
import pandas as pd
import pytensor
import pytensor.tensor as pt

E = Path('/work/evidence')
assert platform.machine() == 'x86_64' and platform.python_version_tuple()[:2] == ('3', {'py312':'12','py313':'13','dev':platform.python_version_tuple()[1]}[os.environ['G0_ENV']])
assert Path(bmb.__file__).resolve().is_relative_to(Path('/work/src'))
assert pytensor.config.linker == os.environ['G0_LINKER']
cxx = pytensor.config.cxx
assert cxx and shutil.which(cxx)
compiler = subprocess.check_output([cxx, '--version'], text=True)[:4000]
x = pt.dvector('x')
compiledir = Path(pytensor.config.base_compiledir)
before = {str(p) for p in compiledir.rglob('*.so')}
fn = pytensor.function([x], 2*x+1)
np.testing.assert_array_equal(fn(np.array([1., 2.])), [3., 5.])
after = {str(p) for p in compiledir.rglob('*.so')}
thunk_observation = any(getattr(t, 'cthunk', None) is not None
                        for t in getattr(fn.vm, 'thunks', ()))
# The first smoke compiles cold; the offline repeat intentionally reuses it.
first = not list(E.glob('smoke-*.json'))
if os.environ['G0_LINKER'] == 'cvm':
    assert (after-before) if first else after, 'NO_COMPILED_MODULE_EVIDENCE'
else:
    native = numba.njit(lambda a: a+1)
    assert native(2) == 3 and native.nopython_signatures
for name in ('blackjax', 'numpyro', 'arviz_plots', 'arviz_stats'):
    importlib.import_module(name)
df = pd.DataFrame({'y': [1., 2., 4., 3., 5., 7., 9., 8.],
                   'x': np.linspace(0, 1, 8), 'g': ['a']*4+['b']*4})
model = bmb.Model('y ~ x + (1|g)', df)
model.build()
versions = {n: md.version(n) for n in ('bambi', 'pymc', 'pytensor', 'numba', 'jax',
                                     'nutpie', 'preliz', 'formulae', 'arviz', 'blackjax',
                                     'numpyro', 'arviz_plots', 'arviz_stats')}
(E / ('smoke-'+str(os.getpid())+'.json')).write_text(json.dumps({
    'python': platform.python_version(), 'platform': platform.machine(), 'versions': versions,
    'linker': pytensor.config.linker, 'cxx': cxx, 'compiler': compiler,
    'private_thunk_observation_only': thunk_observation,
    'compiled_modules_new': sorted(after-before), 'compiled_modules_total': len(after),
    'real_native_model_build': True,
    'sampling': False, 'getter_logic': False}, indent=2)+'\n')

