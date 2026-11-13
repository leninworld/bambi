"""Exercise the real selected environment's Jupyter execution path."""
from pathlib import Path
import base64, binascii, copy, json
import xml.etree.ElementTree as ET
from visual_outputs import require_images
import nbformat
from nbclient import NotebookClient

nb = nbformat.v4.new_notebook(cells=[
    nbformat.v4.new_code_cell('import bambi as bmb\nprint(bmb.__version__)'),
    nbformat.v4.new_code_cell('import pandas as pd\nm = bmb.Model("y ~ x", '
                             'pd.DataFrame({"y": [1., 2., 4., 3.], "x": [0., 1., 2., 3.]}))\n'
                             'print(m.family.name)'),
    nbformat.v4.new_code_cell("%matplotlib inline\nimport matplotlib\nimport matplotlib.pyplot as plt\n"
        "from IPython.display import display\nimport graphviz\n"
        "assert 'inline' in str(matplotlib.get_backend()).lower()\n"
        "plt.figure(); plt.plot([0, 1], [0, 1]); plt.show()\n"
        "g = graphviz.Digraph(); g.edge('x', 'y'); display(g)"),
    nbformat.v4.new_code_cell("print('actual executed print-only negative visual control')"),
])
NotebookClient(nb, timeout=120, kernel_name='bambi-m0-py312',
               resources={'metadata': {'path': '/work/src'}}).execute()
assert all(c.execution_count is not None for c in nb.cells)
assert not any(o.output_type == 'error' for c in nb.cells for o in c.outputs)
nbformat.write(nb, Path('/work/evidence/native-notebook-fixture.ipynb'))

positive = require_images(nb.cells[2], png=1, svg=1)
rejected = []
try:
    require_images(nb.cells[3], png=1)
except AssertionError:
    rejected.append('real-executed-print-only')
else:
    raise AssertionError('Print-only cell accepted as plot')
for label, mime, replacement in [
    ('invalid-base64', 'image/png', '!'),
    ('wrong-png-header', 'image/png', base64.b64encode(b'not a png payload at all....').decode()),
    ('wrong-svg-root', 'image/svg+xml', '<not_svg/>'),
    ('malformed-svg', 'image/svg+xml', '<svg'),
]:
    changed = copy.deepcopy(nb.cells[2])
    found = False
    for output in changed.outputs:
        if mime in output.get('data', {}):
            output.data[mime] = replacement
            found = True
    assert found
    try:
        require_images(changed, png=1, svg=1)
    except (AssertionError, binascii.Error, ET.ParseError):
        rejected.append(label)
    else:
        raise AssertionError('Malformed image accepted: ' + label)
assert len(rejected) == 5
Path('/work/evidence/real-notebook-visual-controls.json').write_text(json.dumps({
    'status': 'PASS', 'realSameInstalledKernelMatplotlibAndGraphviz': True,
    'positive': positive, 'rejected': rejected,
    'privateMockedBackend': False}, indent=2)+'\n')
