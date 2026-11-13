"""Exact docs-only candidate overlay in the isolated Docker source copy."""
import ast
import copy
import hashlib
import json
import shutil
from pathlib import Path

P = Path('/packet')
W = Path('/work')
SRC = W/'src'
E = W/'evidence'
NOTEBOOK = SRC/'docs/notebooks/mediation_analysis.ipynb'
THUMBNAIL = SRC/'docs/notebooks/thumbnails/mediation_analysis.png'
OVERLAYS = {'docs/_quarto.yml': 'candidate-_quarto.yml',
            'docs/notebooks/gallery.yml': 'candidate-gallery.yml'}
PATHS = [*OVERLAYS, 'docs/notebooks/mediation_analysis.ipynb',
         'docs/notebooks/thumbnails/mediation_analysis.png']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_contract(expected, actual):
    assert len(expected['cells']) == len(actual['cells']) == 26
    for before, after in zip(expected['cells'], actual['cells']):
        assert before['cell_type'] == after['cell_type']
        a, b = before['source'], after['source']
        a = ''.join(a) if isinstance(a, list) else a
        b = ''.join(b) if isinstance(b, list) else b
        if before['cell_type'] == 'markdown':
            assert a == b
        else:
            assert [s for s in a.splitlines() if s.startswith('%')] == [s for s in b.splitlines() if s.startswith('%')]
            clean = lambda s: '\n'.join(line for line in s.splitlines() if not line.startswith('%'))
            assert ast.dump(ast.parse(clean(a)), include_attributes=False) == ast.dump(ast.parse(clean(b)), include_attributes=False)


def apply_candidate():
    assert not (W/'candidate-active.json').exists() and not NOTEBOOK.exists() and not THUMBNAIL.exists()
    for path, packet in OVERLAYS.items():
        shutil.copy2(P/packet, SRC/path)
    shutil.copy2(P/'candidate-mediation_analysis.ipynb', NOTEBOOK)
    (W/'candidate-active.json').write_text(json.dumps({'paths': PATHS, 'source': '9552e1d0ece1ce3c209f9b17fa656c2e57924970'})+'\n')


def source_contract_controls():
    original = json.loads((P/'candidate-mediation_analysis.ipynb').read_text())
    source_contract(original, copy.deepcopy(original))
    whitespace = copy.deepcopy(original)
    code_index = next(i for i, c in enumerate(original['cells']) if c['cell_type'] == 'code')
    whitespace['cells'][code_index]['source'] += '\n'
    source_contract(original, whitespace)
    rejected = []
    for label in ('markdown', 'code-semantics', 'ipython-magic'):
        wrong = copy.deepcopy(original)
        if label == 'markdown':
            wrong['cells'][0]['source'] += 'unreviewed narrative'
        elif label == 'code-semantics':
            wrong['cells'][code_index]['source'] += '\nunreviewed_change = True\n'
        else:
            index = next(i for i, c in enumerate(wrong['cells']) if c['cell_type'] == 'code' and '%watermark' in c['source'])
            wrong['cells'][index]['source'] = wrong['cells'][index]['source'].replace('%watermark', '%unreviewed_magic')
        try:
            source_contract(original, wrong)
        except AssertionError:
            rejected.append(label)
        else:
            raise AssertionError('Candidate-source control accepted: ' + label)
    return {'status': 'PASS', 'accepted': ['exact-copy', 'code-whitespace'], 'rejected': rejected}


def verify_candidate():
    assert json.loads((W/'candidate-active.json').read_text())['paths'] == PATHS
    for path, packet in OVERLAYS.items():
        assert not (SRC/path).is_symlink() and not ((SRC/path).stat().st_mode & 0o111)
        assert sha(SRC/path) == sha(P/packet), path
    assert not NOTEBOOK.is_symlink() and not (NOTEBOOK.stat().st_mode & 0o111)
    expected = json.loads((P/'candidate-mediation_analysis.ipynb').read_text())
    actual = json.loads(NOTEBOOK.read_text())
    source_contract(expected, actual)
    completed = E/'candidate-notebook-identity.json'
    if completed.exists():
        proof = json.loads(completed.read_text())
        assert not THUMBNAIL.is_symlink() and not (THUMBNAIL.stat().st_mode & 0o111)
        assert sha(NOTEBOOK) == proof['notebookSHA256'] and sha(THUMBNAIL) == proof['thumbnailSHA256']
    else:
        assert all(c['execution_count'] is None and not c['outputs'] for c in actual['cells'] if c['cell_type'] == 'code')
        assert not THUMBNAIL.exists()
