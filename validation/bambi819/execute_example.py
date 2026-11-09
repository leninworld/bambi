"""Execute the approved notebook cell sources in the actual candidate environment."""
import json
from pathlib import Path

import nbformat
from nbclient import NotebookClient

path = Path('/work/src/docs/notebooks/prior_predictive_interpret.ipynb')
before = json.loads(path.read_text())
nb = nbformat.read(path, as_version=4)
NotebookClient(nb, timeout=180, kernel_name='bambi-candidate-py312',
               resources={'metadata': {'path': '/work/src'}}).execute()
assert all(c.execution_count is not None for c in nb.cells if c.cell_type == 'code')
assert not any(o.output_type == 'error' for c in nb.cells if c.cell_type == 'code'
               for o in c.outputs)
assert [(c['cell_type'], ''.join(c['source']), c.get('id')) for c in before['cells']] == [
    (c.cell_type, c.source, c.get('id')) for c in nb.cells]
# The temporary execution kernel must never leak into published notebook metadata.
nb.metadata['kernelspec'] = before['metadata']['kernelspec']
assert nb.metadata['kernelspec']['name'] == 'python3'
nbformat.write(nb, path)
Path('/work/evidence/notebook-execution.json').write_text(json.dumps({
    'executed_code_cells': sum(c.cell_type == 'code' for c in nb.cells),
    'approved_cell_sources_unchanged': True, 'random_seed': 819,
    'posterior_mcmc': False})+'\n')
