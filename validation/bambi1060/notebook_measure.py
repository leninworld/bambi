"""Execute the entire authored1060 example with the real selected installed kernel."""
from pathlib import Path
import hashlib,json
import nbformat
from nbclient import NotebookClient
import candidate_overlay as candidate
E=Path('/work/evidence');src=Path('/work/src')/candidate.PATHS[3]
nb=nbformat.read(src,as_version=4);before=candidate.notebook_sources(src)
assert all(not c.get('outputs') and c.get('execution_count')is None for c in nb.cells if c.cell_type=='code'),'AUTHORED_INPUT_MUST_BE_UNEXECUTED'
NotebookClient(nb,timeout=300,kernel_name='bambi1060-main',resources={'metadata':{'path':'/work/src'}}).execute()
code=[c for c in nb.cells if c.cell_type=='code'];assert code and all(c.execution_count is not None for c in code)
assert not any(o.output_type=='error'for c in code for o in c.outputs)
rich=[{'cell':i,'latex':o.data['text/latex']}for i,c in enumerate(nb.cells)if c.cell_type=='code'for o in c.outputs if 'text/latex' in o.get('data',{})]
assert len(rich)>=7,'SEVEN_MEANINGFUL_EXAMPLE_EQUATION_OUTPUTS_REQUIRED'
nb.metadata['kernelspec']={'display_name':'Python 3','language':'python','name':'python3'}
nbformat.write(nb,src);assert candidate.notebook_sources(src)==before
raw=src.read_bytes();assert b'/Users/'not in raw and b'ghp_'not in raw and b'github_pat_'not in raw,'PRIVATE_OUTPUT_SCAN_HOLD'
(E/'candidate-notebook-identity.json').write_text(json.dumps({'exactReviewedCellSourcesVerified':True,'codeCellsExecuted':len(code),'actualRichLatexOutputs':len(rich),'executedNotebookSHA256':hashlib.sha256(raw).hexdigest(),'outputPrivateDetailScanPassed':True,'noSamplingRequirementInvented':True},indent=2)+'\n')
nbformat.write(nb,E/'model-representation-executed.ipynb');(E/'actual-equation-outputs.json').write_text(json.dumps(rich,indent=2)+'\n')
candidate.verify_candidate()
