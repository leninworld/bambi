"""Early check: every import the merged test file relies on resolves in this lane's environment.

Each import's bound name comes from the AST exactly as Python binds it: the `as` name when present;
otherwise the top-level package for `import a.b` and the imported name for `from x import y`. (The attempt-4
helper used the imported name, so `import arviz as az` failed as UNBOUND_IMPORT arviz.) Static checks run
before the module import; the module is then imported for real and every bound name must exist on it.
Importing this file has no effect; the check runs only as a script.
"""
import sys
if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
sys.dont_write_bytecode = True
import ast
import importlib.util
import json
from pathlib import Path

STDLIB_UNION = ['logging', 'os', 'pathlib', 'subprocess', 'sys', 'textwrap']
UNION_HEAD = ['import ' + name for name in STDLIB_UNION] + ['from copy import deepcopy']


def bound_name(node, alias):
    """The name an import statement binds in the importing namespace."""
    if alias.asname:
        return alias.asname
    if isinstance(node, ast.Import):
        return alias.name.split('.')[0]
    return alias.name


def analyze(source):
    """Static, module-level import facts and failures (no import performed)."""
    tree = ast.parse(source)
    block = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    imports, failures = [], []
    for node in block:
        for alias in node.names:
            if alias.name == '*':
                failures.append('STAR_IMPORT ' + str(node.module))
                continue
            imports.append({'module': node.module if isinstance(node, ast.ImportFrom) else None,
                            'level': getattr(node, 'level', 0) or 0, 'name': alias.name,
                            'asname': alias.asname, 'bound': bound_name(node, alias)})
    seen, bound_seen = {}, {}
    for row in imports:
        key = (row['module'], row['name'])
        seen[key] = seen.get(key, 0) + 1
        bound_seen[row['bound']] = bound_seen.get(row['bound'], 0) + 1
    duplicates = sorted('%s:%s' % k for k, v in seen.items() if v > 1)
    if duplicates:
        failures.append('DUPLICATE_IMPORT ' + ' '.join(duplicates))
    rebound = sorted(k for k, v in bound_seen.items() if v > 1)
    if rebound:
        failures.append('DUPLICATE_BINDING ' + ' '.join(rebound))
    first = [ast.unparse(n) for n in block[:7]]
    if first != UNION_HEAD:
        failures.append('IMPORT_UNION_ORDER ' + repr(first))
    return {'imports': imports, 'import_union': first, 'failures': failures}


def unbound(module, imports):
    """Bound names missing from the imported module's namespace."""
    return sorted({row['bound'] for row in imports if not hasattr(module, row['bound'])})


def main(src=Path('/work/src'), evidence=Path('/work/evidence')):
    target = Path(src) / 'tests/test_model_construction.py'
    facts = analyze(target.read_text())
    if facts['failures']:
        raise SystemExit(facts['failures'][0])
    sys.path.insert(0, str(Path(src) / 'tests'))
    spec = importlib.util.spec_from_file_location('m558_affected', target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    missing = unbound(module, facts['imports'])
    if missing:
        raise SystemExit('UNBOUND_IMPORT ' + ' '.join(missing))
    (Path(evidence) / 'affected-imports.json').write_text(json.dumps(
        {'status': 'PASS', 'imports': facts['imports'], 'module_import': True,
         'bound_names': sorted({row['bound'] for row in facts['imports']}),
         'import_union': facts['import_union']}, indent=2, allow_nan=False) + '\n')
    print('AFFECTED_IMPORTS_PASS', len(facts['imports']))


if __name__ == '__main__':
    main()
