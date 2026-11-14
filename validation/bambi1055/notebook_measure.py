"""Execute the complete causal tutorial, including two actual NUTS fits and all figures."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import struct
import nbformat
from nbclient import NotebookClient
import candidate_overlay as candidate

E=Path('/work/evidence');src=Path('/work/src/docs/notebooks/causal_effects.ipynb')
nb=nbformat.read(src,as_version=4);before=candidate.notebook_sources(src)
code=[c for c in nb.cells if c.cell_type=='code']
assert len(code)==13 and all(not c.get('outputs')and c.get('execution_count')is None for c in code)
fits=[n for c in code for n in ast.walk(ast.parse(c.source))if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr=='fit']
assert len(fits)==2,'TWO_REAL_FIT_CALLS_IN_REVIEWED_COMPLETE_NOTEBOOK_REQUIRED'
NotebookClient(nb,timeout=300,kernel_name='bambi1055-main',resources={'metadata':{'path':'/work/src'}}).execute()
code=[c for c in nb.cells if c.cell_type=='code']
assert all(c.execution_count is not None for c in code)
assert not any(o.output_type=='error'for c in code for o in c.outputs)
text='\n'.join(''.join(o.get('text',''))for c in code for o in c.outputs if o.output_type=='stream')
assert all(marker in text for marker in ('Gaussian posterior dimensions:','Bernoulli posterior dimensions:')),'BOTH_ACTUAL_FIT_DIMENSION_RECEIPTS_REQUIRED'
figures=[]
for i,c in enumerate(nb.cells):
    for j,o in enumerate(c.get('outputs',[])):
        data=o.get('data',{})
        if 'image/png'not in data:continue
        value=data['image/png'];value=''.join(value)if isinstance(value,list)else value
        raw=base64.b64decode(value,validate=True)
        assert 100<=len(raw)<=4*1024**2 and raw[:8]==b'\x89PNG\r\n\x1a\n'
        width,height=struct.unpack('>II',raw[16:24]);assert 200<=width<=5000 and 100<=height<=5000
        figures.append({'cell':i,'output':j,'mime':'image/png','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'width':width,'height':height})
assert len(figures)>=9,'ACTUAL_TRACE_AND_CAUSAL_MAIN_GROUP_PANEL_FIGURES_REQUIRED'
nb.metadata['kernelspec']={'display_name':'Python 3','language':'python','name':'python3'}
nbformat.write(nb,src);assert candidate.notebook_sources(src)==before
raw=src.read_bytes();assert b'/Users/'not in raw and b'ghp_'not in raw and b'github_pat_'not in raw,'PRIVATE_OUTPUT_SCAN_HOLD'
(E/'candidate-notebook-identity.json').write_text(json.dumps({'exactReviewedCellSourcesVerified':True,'codeCellsExecuted':len(code),'actualGenuineFitCallsCompleted':2,'actualFitDimensionChecksPassed':True,'fitSettings':{'chains':2,'draws':250,'tune':250},'actualPNGOutputs':len(figures),'figures':figures,'executedNotebookSHA256':hashlib.sha256(raw).hexdigest(),'outputPrivateDetailScanPassed':True,'actualFigurePixels':'PENDING_REQUIRED_VISUAL_INSPECTION'},indent=2)+'\n')
nbformat.write(nb,E/'causal-effects-executed.ipynb')
(E/'actual-causal-figure-outputs.json').write_text(json.dumps(figures,indent=2)+'\n')
candidate.verify_candidate()
