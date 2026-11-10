"""Non-gating real dependency probe; failure forbids later candidate authoring."""
import json
from pathlib import Path
import traceback
import importlib.metadata as md
E = Path('/work/evidence')
row = {'status':'UNVERIFIED', 'scope':'controlled potential fault injection, not numerical reproduction', 'source_checked_versions':['6.0.0','6.4.0']}
try:
    import numpy as np
    import pickle
    import inspect
    import pymc
    from pymc.step_methods.hmc.quadpotential import QuadPotentialDiagAdapt
    from pymc.sampling.parallel import ExceptionWithTraceback, ParallelSamplingError
    row['resolved_pymc'] = md.version('pymc')
    row['version_source_checked'] = row['resolved_pymc'] in row['source_checked_versions']
    row['actual_raise_source'] = inspect.getsource(QuadPotentialDiagAdapt.raise_ok)
    row['constructor_signature'] = str(inspect.signature(QuadPotentialDiagAdapt))
    potential = QuadPotentialDiagAdapt(1, np.zeros(1), initial_diag=np.ones(1))
    potential._stds[:] = 0
    try:
        potential.raise_ok([('synthetic', (), 1, np.dtype('float64'))])
        raise AssertionError('real PyMC raiser did not fail')
    except ValueError as exc:
        text = traceback.format_exc()
        row['observed_exception'] = type(exc).__name__
        row['traceback'] = text
        assert 'ValueError: Mass matrix contains' in text
        restored = pickle.loads(pickle.dumps(ExceptionWithTraceback(exc, exc.__traceback__)))
        assert isinstance(restored, ValueError) and 'Mass matrix contains' in str(restored)
        row['parallel_class_hierarchy_only'] = not issubclass(ParallelSamplingError, (ValueError, RuntimeError))
        row['parallel_live_recovery_tested'] = False
        row['status'] = 'DEPENDENCY_PROBE_PASS_NOT_PRODUCT_PASS'
except Exception as exc:
    row['status'] = 'DEPENDENCY_PROBE_FAILED_AUTHORING_HELD'
    row['failure_type'] = type(exc).__name__
    row['failure_message'] = str(exc)[:4000]
    row['failure_traceback'] = traceback.format_exc()[-16000:]
finally:
    (E/'dependency-diagnostic.json').write_text(json.dumps(row,indent=2)+'\n')
