"""Docker-only official wheel formatter; upstream hook and frozen Black remain unchanged."""
import ast
import difflib
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

from build_consumer import BuildConsumer, wheel_members, wheel_guard_fixtures
from inside import call, save, verify_changed_path_log
import candidate_overlay as candidate

P=Path('/packet');W=Path('/work');SRC=W/'src';E=W/'evidence'
PYTHON_PATHS=candidate.PATHS[:6]

def ast_identity(raw):
    return ast.dump(ast.parse(raw.decode('utf-8')),include_attributes=False)

def require_semantic_identity(before,after):
    assert ast_identity(before)==ast_identity(after),'FORMATTER_CHANGED_PYTHON_SEMANTICS'

def require_official_cli_output(text, probe_name):
    lines=text.splitlines()
    assert probe_name=='official_black_entry.py','UNEXPECTED_WRAPPER_PROGRAM_NAME'
    assert lines and lines[0]==probe_name+', 25.9.0 (compiled: no)','OFFICIAL_BLACK_RELEASE_VERSION_REQUIRED'
    return True

def actual_cli_output_controls(text, probe_name):
    assert require_official_cli_output(text,probe_name)
    rejected=[]
    for label,changed,name in (
        ('old-SCM-fallback-version',text.replace('25.9.0','0.1.dev1+gaf0ba72a7'),probe_name),
        ('wrong-frozen-base-version',text.replace('25.9.0','25.12.0'),probe_name),
        ('unexpected-output-basename',text.replace(probe_name,'unexpected_entry.py'),probe_name),
        ('unexpected-probe-name',text,'unexpected_entry.py'),
        ('malformed-version-line',text.replace(', 25.9.0 (compiled: no)',': 25.9.0'),probe_name),
        ('empty-output','',probe_name),
    ):
        try:require_official_cli_output(changed,name)
        except AssertionError:rejected.append(label)
        else:raise AssertionError('Invalid official CLI identity accepted: '+label)
    save('official-black-cli-output-controls.json',{'status':'PASS','positiveActualOfficialCLIOutput':text,'actualCLIOutputSHA256':hashlib.sha256(text.encode()).hexdigest(),'expectedWrapperBasename':probe_name,'rejected':rejected,'moduleDistributionPathWheelGuardsUnchanged':True,'argvNotNormalized':True})

def official_black():
    row=json.loads((P/'official-black.json').read_text());wheel=W/'shipping'/row['filename']
    wheel_members(wheel,row['sha256'],row['bytes'])
    root=W/'formatter';root.mkdir(exist_ok=False)
    assets=json.loads((P/'shipping-assets.json').read_text())
    backend=[r for r in assets['fixed']if r['name']!='uv'];assert len(backend)==4
    small=backend[-1]
    guard=wheel_guard_fixtures(root/'wheel-guard-fixtures',{'path':str(W/'shipping'/small['filename']),'sha256':small['sha256'],'bytes':small['bytes']})
    save('formatter-wheel-guard-fixtures.json',{'actualEightControlsBeforeFirstUVInstall':True,'verifiedBackendWheelSHA256':small['sha256'],'verifiedBackendWheelBytes':small['bytes'],**guard})
    uvrow=next(r for r in assets['fixed']if r['name']=='uv');uvwheel=W/'shipping'/uvrow['filename']
    members=wheel_members(uvwheel,uvrow['sha256'],uvrow['bytes'])
    with zipfile.ZipFile(uvwheel)as z:
        chosen=[n for n in members if n.endswith('/uv')and not n.endswith('/')];assert len(chosen)==1
        uv=root/'uv';uv.write_bytes(z.read(chosen[0]));uv.chmod(0o755)
    helper=BuildConsumer(root,uv,SRC,json.loads((P/'plan.json').read_text())['source'],os.environ['M0_TARGET_EPOCH'])
    helper.tools_and_git()
    base_version=metadata.version('black');assert base_version=='25.12.0','FROZEN_BASE_BLACK_IDENTITY_CHANGED'
    target=root/'isolated-target'
    helper.call('official-black-no-deps-install',[uv,'--offline','pip','install','--python',sys.executable,'--target',target,'--no-index','--no-deps',wheel])
    probe=root/'official_black_entry.py'
    probe.write_text('''import importlib.metadata as m,json,pathlib,sys
root=pathlib.Path(sys.argv.pop(1)).resolve();sys.path.insert(0,str(root))
import black
assert pathlib.Path(black.__file__).resolve().is_relative_to(root)
assert m.version("black")==black.__version__=="25.9.0"
black.patched_main()
''')
    command=[sys.executable,str(probe),str(target)]
    call('actual-official-black-version',command+['--version'])
    text=(E/'actual-official-black-version.log').read_text()
    actual_cli_output_controls(text,probe.name)
    assert metadata.version('black')==base_version,'ISOLATED_INSTALL_MODIFIED_FROZEN_BLACK'
    dest=E/'formatter';dest.mkdir()
    for f in root.iterdir():
        if f.is_file()and f.suffix in ('.log','.json','.py'):
            assert f.stat().st_size<=2*1024**2
            shutil.copyfile(f,dest/f.name)
    save('official-black-installed-identity.json',{'actualWheelSHA256':row['sha256'],'bytes':row['bytes'],'actualCLIOutput':text,'version':'25.9.0','isolatedModulePathCheckedOnEveryInvocation':True,'isolatedTarget':str(target),'baseVersionBeforeAndAfter':base_version,'noDepsOfflineInstall':True,'officialClosureProofSHA256':row['closureProofSHA256']})
    return command

def real_tool_controls(command):
    root=W/'actual-black-fixture';root.mkdir(exist_ok=False)
    sample=root/'positive.py';sample.write_text('value= (1+2)\n');before=sample.read_bytes()
    call('actual-black-positive',command+['--config',str(SRC/'pyproject.toml'),str(sample)])
    after=sample.read_bytes();assert after!=before;require_semantic_identity(before,after)
    changed=after.replace(b'1 + 2',b'1 + 3');assert changed!=after
    try:require_semantic_identity(before,changed)
    except AssertionError:negative=True
    else:raise AssertionError('Semantic mutation of actual formatter output accepted')
    save('actual-black-controls.json',{'status':'PASS','actualOfficialReleaseToolExecuted':True,'positiveASTIdentical':True,'semanticMutationRejected':negative,'productionSourceUntouchedByControls':True})

command=official_black();real_tool_controls(command)
rows=candidate.inputs();assert len(PYTHON_PATHS)==6 and all(n.endswith('.py')for n in PYTHON_PATHS)
files={n:SRC/n for n in PYTHON_PATHS}
for n,f in files.items():assert f.is_file()and not f.is_symlink()and candidate.sha(f)==rows[n]['sha256']
call('pre-format-contribution-diff',['git','diff','--name-only','HEAD'])
before_union=verify_changed_path_log(E/'pre-format-contribution-diff.log',candidate.PATHS)
before={n:f.read_bytes()for n,f in files.items()};authored=E/'authored-before-format';authored.mkdir()
for n,raw in before.items():
    dest=authored/n;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
call('format-six-candidate-python',command+['--config',str(SRC/'pyproject.toml'),*[str(files[n])for n in PYTHON_PATHS]])
after={n:f.read_bytes()for n,f in files.items()}
for n in PYTHON_PATHS:require_semantic_identity(before[n],after[n])
call('post-format-contribution-diff',['git','diff','--name-only','HEAD'])
after_union=verify_changed_path_log(E/'post-format-contribution-diff.log',candidate.PATHS)
assert set(before_union)==set(after_union)==set(candidate.PATHS)
assert all(candidate.sha(SRC/n)==rows[n]['sha256']for n in candidate.PATHS[6:]),'FORMATTER_CHANGED_NONPYTHON_CONTRIBUTION'
diff=''.join(''.join(difflib.unified_diff(before[n].decode().splitlines(True),after[n].decode().splitlines(True),fromfile='authored/'+n,tofile='formatted/'+n))for n in PYTHON_PATHS)
(E/'actual-formatting-only.diff').write_text(diff)
save('candidate-format-proof.json',{'status':'PASS','blackVersion':'25.9.0','officialWheelIdentityRequired':True,'configSHA256':candidate.sha(SRC/'pyproject.toml'),'pythonPaths':list(PYTHON_PATHS),'beforeHashes':{n:hashlib.sha256(before[n]).hexdigest()for n in PYTHON_PATHS},'afterHashes':{n:hashlib.sha256(after[n]).hexdigest()for n in PYTHON_PATHS},'normalizedASTSHA256':{n:hashlib.sha256(ast_identity(before[n]).encode()).hexdigest()for n in PYTHON_PATHS},'allPythonASTIdentical':True,'exactContributionPathUnionUnchanged':True,'notebookBytesUnchanged':True,'formattingDiffSHA256':hashlib.sha256(diff.encode()).hexdigest()})
candidate.verify_candidate()
