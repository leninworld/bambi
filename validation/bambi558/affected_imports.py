"""Early check: every import the merged test file relies on resolves in this lane's environment."""
import sys
if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
import ast
import importlib.util
import json
from pathlib import Path

SRC = Path('/work/src')
TARGET = SRC / 'tests/test_model_construction.py'
tree = ast.parse(TARGET.read_text())
block = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
names = []
for node in block:
    for alias in node.names:
        names.append((node.module if isinstance(node, ast.ImportFrom) else None, alias.name))
counts = {}
for name in names:
    counts[name] = counts.get(name, 0) + 1
if any(v > 1 for v in counts.values()):
    raise SystemExit('DUPLICATE_IMPORT ' + repr({k: v for k, v in counts.items() if v > 1}))
sys.path.insert(0, str(SRC / 'tests'))
spec = importlib.util.spec_from_file_location('m558_affected', TARGET)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for module_name, attr in names:
    bound = attr.split('.')[0]
    if not hasattr(module, bound):
        raise SystemExit('UNBOUND_IMPORT ' + bound)
stdlib_union = ['logging', 'os', 'pathlib', 'subprocess', 'sys', 'textwrap']
first = [ast.unparse(n) for n in block[:7]]
if first != ['import ' + s for s in stdlib_union] + ['from copy import deepcopy']:
    raise SystemExit('IMPORT_UNION_ORDER ' + repr(first))
Path('/work/evidence/affected-imports.json').write_text(json.dumps(
    {'status': 'PASS', 'imports': [list(n) for n in names], 'module_import': True,
     'import_union': first}, indent=2, allow_nan=False) + '\n')
print('AFFECTED_IMPORTS_PASS', len(names))
