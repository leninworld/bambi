"""Docker-only exact pinned hook formatter; semantic changes stop before expensive work."""
import ast,difflib,hashlib,json,os,re,sqlite3
from pathlib import Path
from inside import call,save,verify_changed_path_log
import candidate_overlay as candidate
P=Path('/packet');W=Path('/work');SRC=W/'src';E=W/'evidence'
PYTHON_PATHS=candidate.PATHS[:3]

def ast_identity(raw):
    return ast.dump(ast.parse(raw.decode('utf-8')),include_attributes=False)

def require_semantic_identity(before,after):
    assert ast_identity(before)==ast_identity(after),'FORMATTER_CHANGED_PYTHON_SEMANTICS'

def pinned_black():
    cache=Path(os.environ['PRE_COMMIT_HOME']).resolve();db=cache/'db.db'
    assert db.is_file() and not db.is_symlink()
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)as connection:
        rows=connection.execute('SELECT path FROM repos WHERE repo=? AND ref=?',('https://github.com/psf/black','25.9.0')).fetchall()
    assert len(rows)==1,'EXACT_PINNED_BLACK_CACHE_NOT_UNIQUE'
    repo=Path(rows[0][0]).resolve();assert repo.is_relative_to(cache)
    candidates=[p for p in repo.glob('py_env-*/bin/black')if p.is_file()]
    assert len(candidates)==1,'EXACT_HOOK_BLACK_ENVIRONMENT_NOT_UNIQUE'
    binary=candidates[0];call('actual-black-version',[str(binary),'--version'])
    output=(E/'actual-black-version.log').read_text();assert re.search(r'\bblack, 25\.9\.0\b',output),'PINNED_BLACK_VERSION_MISMATCH'
    return binary,repo

def real_tool_controls(binary):
    root=W/'actual-black-fixture';root.mkdir(exist_ok=False)
    sample=root/'positive.py';sample.write_text('value= (1+2)\n');before=sample.read_bytes()
    call('actual-black-positive',[str(binary),'--config',str(SRC/'pyproject.toml'),str(sample)])
    after=sample.read_bytes();assert after!=before;require_semantic_identity(before,after)
    changed=after.replace(b'1 + 2',b'1 + 3');assert changed!=after
    try:require_semantic_identity(before,changed)
    except AssertionError:negative=True
    else:raise AssertionError('Semantic mutation of actual formatter output accepted')
    save('actual-black-controls.json',{'status':'PASS','actualPinnedToolExecuted':True,'positiveASTIdentical':True,'semanticMutationRejected':negative,'productionSourceUntouchedByControls':True})

binary,repo=pinned_black();real_tool_controls(binary)
rows=candidate.inputs();assert PYTHON_PATHS==('bambi/representation.py','bambi/models.py','tests/test_representation.py')
files={n:SRC/n for n in PYTHON_PATHS}
for n,f in files.items():assert f.is_file() and not f.is_symlink() and candidate.sha(f)==rows[n]['sha256']
call('pre-format-four-diff',['git','diff','--name-only','HEAD']);before_union=verify_changed_path_log(E/'pre-format-four-diff.log',candidate.PATHS)
before={n:f.read_bytes()for n,f in files.items()};authored=E/'authored-before-format';authored.mkdir()
for n,raw in before.items():
    dest=authored/n;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
call('format-three-candidate-python',[str(binary),'--config',str(SRC/'pyproject.toml'),*[str(files[n])for n in PYTHON_PATHS]])
after={n:f.read_bytes()for n,f in files.items()}
for n in PYTHON_PATHS:require_semantic_identity(before[n],after[n])
call('post-format-four-diff',['git','diff','--name-only','HEAD']);after_union=verify_changed_path_log(E/'post-format-four-diff.log',candidate.PATHS)
assert set(before_union)==set(after_union)==set(candidate.PATHS)
assert candidate.sha(SRC/candidate.PATHS[3])==rows[candidate.PATHS[3]]['sha256'],'FORMATTER_CHANGED_NOTEBOOK'
diff=''.join(''.join(difflib.unified_diff(before[n].decode().splitlines(True),after[n].decode().splitlines(True),fromfile='authored/'+n,tofile='formatted/'+n))for n in PYTHON_PATHS)
(E/'actual-formatting-only.diff').write_text(diff)
save('candidate-format-proof.json',{'status':'PASS','blackVersion':'25.9.0','blackBinarySHA256':candidate.sha(binary),'exactCacheRepository':str(repo),'configSHA256':candidate.sha(SRC/'pyproject.toml'),'pythonPaths':list(PYTHON_PATHS),'beforeHashes':{n:hashlib.sha256(before[n]).hexdigest()for n in PYTHON_PATHS},'afterHashes':{n:hashlib.sha256(after[n]).hexdigest()for n in PYTHON_PATHS},'normalizedASTSHA256':{n:hashlib.sha256(ast_identity(before[n]).encode()).hexdigest()for n in PYTHON_PATHS},'allThreeASTIdentical':True,'exactFourPathUnionUnchanged':True,'notebookBytesUnchanged':True,'formattingDiffSHA256':hashlib.sha256(diff.encode()).hexdigest()})
candidate.verify_candidate()
