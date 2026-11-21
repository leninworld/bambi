"""Execute the authored notebook and a removed private reference/verification cell."""
import base64
import json
from pathlib import Path
import time
import os
import socket

import nbformat
from nbclient import NotebookClient
import candidate_docs
from visual_outputs import candidate_visual_contract, image_payloads

E = Path('/work/evidence')
path = candidate_docs.NOTEBOOK
nb = nbformat.read(path, as_version=4)
assert len(nb.cells) == 22
candidate_docs.verify_candidate()
actual_cells = len(nb.cells)
probe = nbformat.v4.new_code_cell('from notebook_validation_probe import run_probe\nrun_probe(globals())')
nb.cells.append(probe)
started = time.monotonic()
first = None
try:
    NotebookClient(nb, timeout=1200, kernel_name='bambi717-baseline',
                   resources={'metadata': {'path': str(path.parent)}}).execute()
    assert all(c.execution_count is not None for c in nb.cells if c.cell_type == 'code')
    assert not any(o.output_type == 'error' for c in nb.cells if c.cell_type == 'code' for o in c.outputs)
    assert nb.cells[-1].source == probe.source
    nb.cells.pop()
    assert len(nb.cells) == actual_cells
    counts = [c.execution_count for c in nb.cells if c.cell_type == 'code']
    assert counts == list(range(1, len(counts) + 1)), 'PUBLIC_EXECUTION_COUNTS_NOT_CLEAN'
    visual = candidate_visual_contract(nb)
    (E / 'candidate-visual-output-proof.json').write_text(json.dumps(visual, indent=2) + '\n')
    candidate_docs.source_contract(json.loads((Path('/packet') / 'candidate-mediation_analysis.ipynb').read_text()), nb)
    # Extract a genuine authored plot. No savefig or hidden-data dependence in the public notebook.
    effects = [c for c in nb.cells if c.id == 'effects-plot']
    assert len(effects) == 1
    checked = [r for r in image_payloads(effects[0]) if r['mime'] == 'image/png']
    outputs = [o['data']['image/png'] for o in effects[0].outputs if 'image/png' in o.get('data', {})]
    assert len(checked) == len(outputs) == 1
    raw = base64.b64decode(outputs[0].encode().translate(None, b' \t\r\n'), validate=True)
    thumbnail = candidate_docs.THUMBNAIL
    assert thumbnail.parent.is_dir() and not thumbnail.exists()
    thumbnail.write_bytes(raw)
    assert candidate_docs.sha(thumbnail) == checked[0]['sha256']
    assert 0 < len(raw) <= 2 * 1024**2
    nb.metadata['kernelspec'] = {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'}
    serialized_outputs = json.dumps([{'metadata': c.get('metadata', {}), 'outputs': c.get('outputs', [])} for c in nb.cells])
    forbidden = ['/work/', '/home/runner/', '/runner/', socket.gethostname()]
    forbidden += [value for key, value in os.environ.items() if ('TOKEN' in key.upper() or 'SECRET' in key.upper()) and len(value) >= 8]
    assert not any(value and value in serialized_outputs for value in forbidden), 'PRIVATE_RUNTIME_DETAIL_IN_CONTRIBUTION_OUTPUT'
    assert all('notebook_validation_probe' not in c.source and 'run_probe' not in c.source for c in nb.cells)
    nbformat.write(nb, path)
    assert path.stat().st_size <= 10 * 1024**2
    (E / 'executed-candidate.ipynb').write_bytes(path.read_bytes())
    (E / 'candidate-thumbnail.png').write_bytes(thumbnail.read_bytes())
    (E / 'candidate-notebook-identity.json').write_text(json.dumps({
        'notebookSHA256': candidate_docs.sha(path), 'thumbnailSHA256': candidate_docs.sha(thumbnail),
        'notebookBytes': path.stat().st_size, 'thumbnailBytes': thumbnail.stat().st_size,
        'actualCandidateCells': actual_cells, 'validationProbePublished': False,
        'allCandidateCodeCellsExecuted': True, 'candidateNotebookSourceASTAndMarkdownVerified': True,
        'exactReviewedCellSourcesVerified': True, 'kernelspec': 'python3', 'outputPrivateDetailScanPassed': True,
        'cleanPublicExecutionCounts': counts, 'publicFits': 3, 'privateReferenceFits': 2,
        'thumbnailFromAuthoredEffectsPNG': checked[0],
        'seconds': time.monotonic() - started, 'wholeSiteOrFullTestsPassed': False}, indent=2) + '\n')
except BaseException as exc:
    first = {'type': type(exc).__name__, 'message': str(exc)}
    raise
finally:
    nbformat.write(nb, E / 'candidate-execution-retained.ipynb')
    (E / 'candidate-execution-receipt.json').write_text(json.dumps({
        'firstFailure': first, 'seconds': time.monotonic() - started,
        'codeCellsExecuted': sum(c.get('execution_count') is not None for c in nb.cells if c.cell_type == 'code'),
        'expectedCandidateCells': actual_cells, 'validationProbeWasExtraPrivateCell': True,
        'automaticRetry': False}, indent=2) + '\n')
