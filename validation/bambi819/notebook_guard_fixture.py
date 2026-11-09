"""Actual nbformat structures exercise the production normalizer guard."""
import copy
import json
import runpy
from pathlib import Path

import nbformat

verify = runpy.run_path('/packet/inside.py')['verify_notebook_normalization']
old = nbformat.v4.new_notebook(cells=[
    nbformat.v4.new_code_cell('value=1'),
    nbformat.v4.new_markdown_cell('Synthetic documentation'),
])
new = copy.deepcopy(old)
new.cells[0].source = 'value = 1\n'
verify(old, new)
failures = 0
for mode in ('code', 'markdown', 'id', 'missing'):
    changed = copy.deepcopy(new)
    if mode == 'code':
        changed.cells[0].source = 'value = 2'
    elif mode == 'markdown':
        changed.cells[1].source = 'Changed documentation'
    elif mode == 'id':
        changed.cells[0].id = 'different'
    else:
        changed.cells.pop()
    try:
        verify(old, changed)
    except AssertionError:
        failures += 1
    else:
        raise AssertionError(mode + ' was accepted')
assert failures == 4
Path('/work/evidence/notebook-guard-fixture.json').write_text(json.dumps({
    'real_nbformat': nbformat.__version__, 'formatting_pass': True,
    'rejected_semantic_or_structure_changes': failures,
}) + '\n')
