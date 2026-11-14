"""Record actual baseline feature absence, without inventing a product defect."""
import importlib.util
import json
from pathlib import Path
import bambi

assert Path(bambi.__file__).resolve().is_relative_to(Path('/work/src'))
assert importlib.util.find_spec('bambi.causal') is None, 'BASELINE_FEATURE_ALREADY_PRESENT_REASSESS_SCOPE'
Path('/work/evidence/baseline-feature-absence.json').write_text(json.dumps({
    'source':'babde99afb10a2b542e6c993074313ac2139f476',
    'actualBaselineModuleAbsent':True,
    'parentImportPath':bambi.__file__,
    'notMathematicalBugReproduction':True,
    'noCandidateOrFullBaselinePassClaim':True,
},indent=2)+'\n')
