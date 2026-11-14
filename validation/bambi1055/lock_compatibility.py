"""Prove the package-list-only edit works with actual unchanged frozen environment."""
import hashlib
import json
from pathlib import Path
import tomllib
from inside import pixi, PLAN, SRC, E, ENV
import candidate_overlay as candidate

original = next(r for r in json.loads(Path('/packet/source-manifest.json').read_text()) if r['path']=='pyproject.toml')
assert original['sha256'] != candidate.sha(SRC/'pyproject.toml')
before_lock = candidate.sha(SRC/'pixi.lock')
assert before_lock == PLAN['canonical_lock_sha256']
pixi('actual-package-list-frozen-install', ['python','-c',
    'import bambi.causal; from bambi.causal import g_computation, plot_effects, CausalResult; print(bambi.causal.__file__)'])
assert candidate.sha(SRC/'pixi.lock') == before_lock
(E/'candidate-frozen-lock-compatibility.json').write_text(json.dumps({
    'actualPixiRunFrozenWithCandidateManifest':True,
    'actualCausalSubpackageImported':True,
    'lockUnchanged':True,'lockSHA256':before_lock,'environment':ENV,
    'packageListChangeNotInferredCompatible':True,
},indent=2)+'\n')
