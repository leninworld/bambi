"""Real Jupyter and rich LaTeX evidence controls, executed only in approved Docker."""
from pathlib import Path
import copy,json
import nbformat
from nbclient import NotebookClient
E=Path('/work/evidence')
def latex_outputs(cell):
 out=[v['data']['text/latex']for v in cell.get('outputs',[])if 'text/latex' in v.get('data',{})]
 assert out and all(isinstance(s,str)and s.strip()for s in out),'RICH_LATEX_OUTPUT_REQUIRED'
 return out
nb=nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('from IPython.display import display, Math\nfor i in range(7): display(Math(r"y = \\alpha + \\beta x"))'),nbformat.v4.new_code_cell('print("print only negative control")')])
NotebookClient(nb,timeout=60,kernel_name='bambi1060-main',resources={'metadata':{'path':'/work/src'}}).execute()
assert all(c.execution_count is not None for c in nb.cells)
assert not any(o.output_type=='error'for c in nb.cells for o in c.outputs)
assert any('alpha' in s and 'beta' in s for s in latex_outputs(nb.cells[0]))
rejected=[]
for name,cell in [('actual-print-only',nb.cells[1]),('missing-data',{'outputs':[]}),('empty-latex',{'outputs':[{'data':{'text/latex':''}}]})]:
 try:latex_outputs(cell)
 except AssertionError:rejected.append(name)
 else:raise AssertionError('Invalid rich output accepted: '+name)
nbformat.write(nb,E/'native-notebook-fixture.ipynb')
(E/'notebook-rich-latex-controls.json').write_text(json.dumps({'status':'PASS','actualInstalledKernel':True,'actualIPythonMathOutput':True,'rejected':rejected,'notProofOfMathJaxPixels':True})+'\n')
