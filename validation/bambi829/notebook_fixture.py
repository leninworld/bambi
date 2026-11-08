"""Exercise the real selected environment's Jupyter execution path."""
from pathlib import Path
import nbformat
from nbclient import NotebookClient

nb = nbformat.v4.new_notebook(cells=[
    nbformat.v4.new_code_cell('import bambi as bmb\nprint(bmb.__version__)'),
    nbformat.v4.new_code_cell('import pandas as pd\nm = bmb.Model("y ~ x", '
                             'pd.DataFrame({"y": [1., 2., 4., 3.], "x": [0., 1., 2., 3.]}))\n'
                             'print(m.family.name)'),
])
NotebookClient(nb, timeout=120, kernel_name='bambi-m0-py312',
               resources={'metadata': {'path': '/work/src'}}).execute()
assert all(c.execution_count is not None for c in nb.cells)
assert not any(o.output_type == 'error' for c in nb.cells for o in c.outputs)
nbformat.write(nb, Path('/work/evidence/native-notebook-fixture.ipynb'))
