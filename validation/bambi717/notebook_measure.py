"""Execute a complete diagnostic notebook; never a contribution-notebook pass."""
from pathlib import Path
import hashlib,json
import nbformat
from nbclient import NotebookClient

E=Path('/work/evidence')
nb=nbformat.v4.new_notebook(cells=[
 nbformat.v4.new_markdown_cell('# Mediation full native baseline measurement\nOriginal mathematical model: Benjamin T. Vincent, official pymc-examples 14a7fde2, MIT. Current PyMC API port is explicit. This is diagnostic code, not the proposed contribution notebook.\n\nIdentification requires consistency, positivity, treatment–outcome/treatment–mediator and mediator–outcome exchangeability, no exposure-induced mediator–outcome confounder, correct linear additive models and independent residuals. A fitted association alone does not prove causality. Total-only and mediation total comparisons retain different priors. Continuous priors give no posterior point-mass evidence for exactly zero.'),
 nbformat.v4.new_code_cell("from science import run\nrun()"),
 nbformat.v4.new_code_cell("from IPython.display import Image, display\nfrom pathlib import Path\nfor filename in ['simulation-pairplot.png','original-joint-graph.png','original-traces.png','original-posterior-pairs.png','effects-hdi95.png','total-model-prior-contrast.png']:\n    display(Image(filename=str(Path('/work/evidence')/filename)))"),
 nbformat.v4.new_code_cell("%load_ext watermark\n%watermark -n -u -v -iv -w -p bambi,pymc,pytensor,arviz_stats,xarray,numpy"),
])
nb.metadata['kernelspec']={'display_name':'Bambi717 baseline','language':'python','name':'bambi717-baseline'}
NotebookClient(nb,timeout=600,kernel_name='bambi717-baseline',resources={'metadata':{'path':'/work/src'}}).execute()
assert all(c.execution_count is not None for c in nb.cells if c.cell_type=='code')
assert not any(o.output_type=='error' for c in nb.cells if c.cell_type=='code' for o in c.outputs)
p=E/'full-scientific-baseline.ipynb';nbformat.write(nb,p)
assert json.loads((E/'science-full-results.json').read_text())['fiveFullFits']
(E/'scientific-notebook-identity.json').write_text(json.dumps({'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'allCellsExecuted':True,'contributionNotebookValidated':False},indent=2)+'\n')
