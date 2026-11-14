"""Real Jupyter PNG controls; synthetic fixtures do not replace the causal notebook."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import nbformat
from nbclient import NotebookClient
E=Path('/work/evidence')
def png_outputs(cell):
    found=[]
    for o in cell.get('outputs',[]):
        value=o.get('data',{}).get('image/png')
        if value is None:continue
        value=''.join(value)if isinstance(value,list)else value
        raw=base64.b64decode(value,validate=True)
        assert raw[:8]==b'\x89PNG\r\n\x1a\n'and 100<=len(raw)<=4*1024**2
        width,height=struct.unpack('>II',raw[16:24]);assert width>=200 and height>=100
        found.append({'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    assert found,'ACTUAL_PNG_OUTPUT_REQUIRED'
    return found
nb=nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('from IPython import get_ipython\nget_ipython().run_line_magic("matplotlib","inline")\nimport matplotlib.pyplot as plt\nplt.plot([0,1],[0,1]);plt.title("Synthetic kernel fixture");plt.show()'),nbformat.v4.new_code_cell('print("print only negative control")')])
NotebookClient(nb,timeout=60,kernel_name='bambi1055-main',resources={'metadata':{'path':'/work/src'}}).execute()
assert all(c.execution_count is not None for c in nb.cells)
assert not any(o.output_type=='error'for c in nb.cells for o in c.outputs)
actual=png_outputs(nb.cells[0]);rejected=[]
for name,cell in [('actual-print-only',nb.cells[1]),('missing-data',{'outputs':[]}),('wrong-signature',{'outputs':[{'data':{'image/png':base64.b64encode(b'not a PNG'*20).decode()}}]})]:
    try:png_outputs(cell)
    except AssertionError:rejected.append(name)
    else:raise AssertionError('Invalid rich output accepted: '+name)
nbformat.write(nb,E/'native-notebook-fixture.ipynb')
(E/'notebook-rich-PNG-controls.json').write_text(json.dumps({'status':'PASS','actualInstalledKernel':True,'actualPNG':actual,'rejected':rejected,'syntheticControlNotCausalFigureOrActualPixelReview':True})+'\n')
