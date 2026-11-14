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
        text=lambda value: ''.join(value) if isinstance(value,list) else value
        assert text(before['source']) == text(after['source']), 'REVIEWED_CELL_SOURCE_BYTES_CHANGED'


def apply_candidate():
    assert not (W/'candidate-active.json').exists() and not NOTEBOOK.exists() and not THUMBNAIL.exists()
    for path, packet in OVERLAYS.items():
        shutil.copy2(P/packet, SRC/path)
    shutil.copy2(P/'candidate-mediation_analysis.ipynb', NOTEBOOK)
    (W/'candidate-active.json').write_text(json.dumps({'paths': PATHS, 'source': 'babde99afb10a2b542e6c993074313ac2139f476'})+'\n')


def source_contract_controls():
    original=json.loads((P/'candidate-mediation_analysis.ipynb').read_text())
    source_contract(original,copy.deepcopy(original))
    rejected=[]
    for label,index,extra in [('markdown',0,'unreviewed narrative'),('code',1,'\nunreviewed=True'),('whitespace',1,'\n'),('magic',25,'\n%unreviewed')]:
        wrong=copy.deepcopy(original);value=wrong['cells'][index]['source']
        wrong['cells'][index]['source']=(''.join(value) if isinstance(value,list) else value)+extra
        try:source_contract(original,wrong)
        except AssertionError:rejected.append(label)
        else:raise AssertionError('Changed cell source accepted: '+label)
    return {'status':'PASS','exactCodeMarkdownSources':True,'rejected':rejected}


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
