"""Execute all actual candidate cells once; retain output/thumbnail identities and failures."""
import json
from pathlib import Path
import time

import nbformat
from nbclient import NotebookClient
import candidate_docs
from visual_outputs import candidate_visual_contract

E = Path('/work/evidence')
path = candidate_docs.NOTEBOOK
nb = nbformat.read(path, as_version=4)
assert len(nb.cells) == 26
candidate_docs.verify_candidate()
actual_cells = len(nb.cells)
probe = nbformat.v4.new_code_cell('''
import json
from pathlib import Path
evidence = Path('/work/evidence')
assert set(fits) == {'reference joint', 'reference total', 'Bambi mediator', 'Bambi outcome', 'Bambi total'}
assert len(diagnostics) == 5 and len(comparison) == 6
with np.load(evidence/'same-arrays.npz', allow_pickle=False) as validated:
    for label, actual in [('x', x), ('m', m), ('y', y)]:
        np.testing.assert_array_equal(validated[label], actual)
assert json.loads((evidence/'same-arrays.json').read_text()) == array_identity
(evidence/'science-full-results.json').write_text(json.dumps({
    'status': 'PASS', 'fiveFullFits': True, 'chains': 4, 'draws': 1000, 'tune': 4000,
    'targetAccept': .9, 'allFiveDiagnostics': diagnostics, 'comparisons': comparison,
    'sameArrayManifest': array_identity, 'zeroPointMassClaim': False,
    'actualContributionNotebookExecuted': True, 'fullProjectValidation': False}, indent=2)+'\\n')
for label, posterior in posteriors.items():
    (evidence/(label.replace(' ', '-')+'-diagnostics.json')).write_text(json.dumps({
        'rhat': azs.rhat(posterior).to_dict(),
        'essBulk': azs.ess(posterior, method='bulk').to_dict(),
        'essTail': azs.ess(posterior, method='tail').to_dict(),
        'hdi95': azs.hdi(posterior, prob=.95).to_dict()}, indent=2)+'\\n')
''')
nb.cells.append(probe)
started = time.monotonic()
first = None
try:
    NotebookClient(nb, timeout=600, kernel_name='bambi717-baseline',
                   resources={'metadata': {'path': str(path.parent)}}).execute()
    assert all(c.execution_count is not None for c in nb.cells if c.cell_type == 'code')
    assert not any(o.output_type == 'error' for c in nb.cells if c.cell_type == 'code' for o in c.outputs)
    assert nb.cells[-1].source == probe.source
    nb.cells.pop()
    assert len(nb.cells) == actual_cells
    visual = candidate_visual_contract(nb)
    (E/'candidate-visual-output-proof.json').write_text(json.dumps(visual, indent=2)+'\n')
    candidate_docs.source_contract(json.loads((Path('/packet')/'candidate-mediation_analysis.ipynb').read_text()), nb)
    thumbnail = candidate_docs.THUMBNAIL
    assert 0 < thumbnail.stat().st_size <= 2 * 1024**2
    assert thumbnail.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    nbformat.write(nb, path)
    assert path.stat().st_size <= 10 * 1024**2, 'UPSTREAM_NOTEBOOK_SIZE_LIMIT'
    (E/'executed-candidate.ipynb').write_bytes(path.read_bytes())
    (E/'candidate-thumbnail.png').write_bytes(thumbnail.read_bytes())
    (E/'candidate-notebook-identity.json').write_text(json.dumps({
        'notebookSHA256': candidate_docs.sha(path), 'thumbnailSHA256': candidate_docs.sha(thumbnail),
        'notebookBytes': path.stat().st_size, 'thumbnailBytes': thumbnail.stat().st_size,
        'actualCandidateCells': actual_cells, 'validationProbePublished': False,
        'allCandidateCodeCellsExecuted': True, 'candidateNotebookSourceASTAndMarkdownVerified': True,
        'seconds': time.monotonic()-started, 'wholeSiteOrFullTestsPassed': False}, indent=2)+'\n')
except BaseException as exc:
    first = {'type': type(exc).__name__, 'message': str(exc)}
    raise
finally:
    nbformat.write(nb, E/'candidate-execution-retained.ipynb')
    (E/'candidate-execution-receipt.json').write_text(json.dumps({
        'firstFailure': first, 'seconds': time.monotonic()-started,
        'codeCellsExecuted': sum(c.get('execution_count') is not None for c in nb.cells if c.cell_type=='code'),
        'expectedCandidateCells': actual_cells, 'validationProbeWasExtraPrivateCell': True,
        'automaticRetry': False}, indent=2)+'\n')
