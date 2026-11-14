"""PRIVATE DRAFT: complete MAIN1055 candidate validation; execution disabled."""

import hashlib

import http.server

import io

import json

import os

from pathlib import Path

import shutil

import subprocess

import sys

import tarfile

import time

import threading

import urllib.request

import urllib.error

import xml.etree.ElementTree as ET

from bounded import command

import candidate_overlay as candidate_docs
from timing_budget import full_test_budget,fixtures as timing_fixtures

P = Path('/packet')

W = Path('/work')

E = W / 'evidence'

E.mkdir(exist_ok=True)

SRC = W / 'src'

PIXI = W / 'tools/pixi'

ENV=os.environ['G0_ENV']
ROLE=os.environ['G0_ROLE']
LINKER=os.environ['G0_LINKER']
PLAN=json.loads((P/'plan.json').read_text())
SPEC=PLAN['lanes'][os.environ['G0_LANE']]
assert ENV in ('py312','py313','dev') and ROLE in ('test','docs') and LINKER in ('cvm','numba')

def save(name, value):
    (E / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

def export_shipping_diagnostics(root=W/'shipping'):
    if not root.exists():return []
    files=[p for p in root.iterdir()if p.is_file() and p.suffix in ('.json','.log','.txt','.py')]
    fixture_receipt=root/'wheel-guard-fixtures/receipt.json'
    if fixture_receipt.is_file():files.append(fixture_receipt)
    assert len(files)<=128 and sum(p.stat().st_size for p in files)<=24*1024**2,'SHIPPING_DIAGNOSTIC_BOUND'
    dest=E/'shipping';dest.mkdir(exist_ok=True)
    for f in files:
        assert not f.is_symlink()
        target=dest/('wheel-guard-receipt.json'if f==fixture_receipt else f.name)
        shutil.copyfile(f,target)
    return [f.name for f in files]

def metrics():
    export_shipping_diagnostics()
    root = Path('/sys/fs/cgroup')
    return {**{n: (root / n).read_text() for n in (
        'memory.current', 'memory.peak', 'memory.events', 'memory.max',
        'pids.current', 'pids.peak', 'pids.events', 'pids.max', 'cpu.stat')},
        'affinity': sorted(os.sched_getaffinity(0)),
        'shm': shutil.disk_usage('/dev/shm')._asdict()}

def call(label, args, accepted=(0,), cwd=SRC):
    print('COMMAND', label, json.dumps(args), flush=True)
    started = time.monotonic()
    used = sum(p.stat().st_size for p in E.rglob('*') if p.is_file())
    cap = min(16 * 1024**2, 64 * 1024**2-used)
    assert cap > 0, 'INNER_EVIDENCE_CAP'
    result = {}
    remaining = float(os.environ['M0_TARGET_EPOCH'])-time.time()
    seconds = min(remaining-180, SPEC.get('pytest_seconds',1800) if label == 'pytest' else 850 if label == 'pixi-install' else 600 if label=='causal-notebook' else 420 if label in ('feature-oracles','baseline-feature-oracles') else 300 if label=='shipping-early' else 300 if label in ('full-site','full-quartodoc','full-interlinks','hook-install') else 180)
    assert seconds > 0, 'ONE_M0_TARGET_STOP'
    command(args, seconds, cap, expected=accepted, cwd=cwd,
            log=E / (label+'.log'), result=result)
    code = result['returncode']
    size = (E / (label + '.log')).stat().st_size
    print('RESULT', label, code, 'seconds', time.monotonic()-started,
          'log_bytes', size, flush=True)
    assert size <= 16 * 1024**2, 'BOUNDED_PHASE_LOG'
    return code

def pixi(label, args, accepted=(0,), cwd=SRC):
    manifest = Path((W/'tools/manifest-path.txt').read_text())
    assert manifest.is_file() and manifest.parent == SRC
    return call(label, [str(PIXI), 'run', '--manifest-path', str(manifest), '--frozen', '--environment', ENV, *args], accepted, cwd)

def verify_source():
    rows = json.loads((P / 'source-manifest.json').read_text())
    assert len(rows) == 239
    for r in rows:
        if (W/'candidate-active.json').exists() and r['path'] in candidate_docs.OVERLAYS:
            continue
        f = SRC / r['path']
        b = os.readlink(f).encode() if r['mode'] == '120000' else f.read_bytes()
        assert len(b) == r['bytes'] and hashlib.sha256(b).hexdigest() == r['sha256'], r['path']
        assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest() == r['git_blob']
        if r['mode'] != '120000':
            assert bool(f.stat().st_mode & 0o111) == (r['mode'] == '100755'), r['path']
    if (W/'candidate-active.json').exists():
        candidate_docs.verify_candidate()

def extract(archive, destination):
    destination.mkdir(exist_ok=False)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        assert len(members) <= 12000 and sum(m.size for m in members) <= 2 * 1024**3
        for member in members:
            assert not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            assert member.isfile() or member.isdir() or member.issym() or member.islnk()
            # The actual standard-library filter is exercised before downloads.
            tarfile.data_filter(member, str(destination))
        tar.extractall(destination, members=members, filter='data')

def download(spec, destination, fixture=False):
    url = spec['browser_download_url']
    official=json.loads((P/'official-black.json').read_text())
    assert (fixture and url.startswith('http://127.0.0.1:')) or url==official['url'] or url.startswith((
        'https://github.com/prefix-dev/pixi/releases/download/v0.62.2/',
        'https://github.com/quarto-dev/quarto-cli/releases/download/v1.8.26/')) or (P/'shipping-assets.json').is_file() and url in {r['url']for r in json.loads((P/'shipping-assets.json').read_text())['fixed']+[json.loads((P/'shipping-assets.json').read_text())['optionalWheel']]}
    request = urllib.request.Request(url, headers={'User-Agent': 'bambi-native-baseline',
                                                 'Accept': 'application/octet-stream'})
    digest = hashlib.sha256()
    size = 0
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=30) as response, destination.open('xb') as out:
        assert response.status == 200
        while True:
            assert time.monotonic()-start < 180, 'TOOL_DOWNLOAD_TIME'
            b = response.read(65536)
            if not b:
                break
            size += len(b)
            assert size <= spec['size'], 'TOOL_DOWNLOAD_SIZE'
            digest.update(b)
            out.write(b)
    assert size == spec['size'] and 'sha256:'+digest.hexdigest() == spec['digest']
    save(destination.name+'.identity.json', {'bytes': size, 'sha256': digest.hexdigest(), 'url': url})

def helper_fixture():
    # Use this same production extractor against real tar structures.
    for name, member_name, target, must_fail in (
        ('valid', 'nested/value', None, False),
        ('traversal', '../escape', None, True),
        ('link-escape', 'escape', '../../outside', True),
    ):
        archive = W / (name+'.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            m = tarfile.TarInfo(member_name)
            if target is None:
                m.size = 3
                tar.addfile(m, io.BytesIO(b'abc'))
            else:
                m.type = tarfile.SYMTYPE
                m.linkname = target
                tar.addfile(m)
        failed = False
        try:
            extract(archive, W / ('fixture-'+name))
        except (tarfile.FilterError, AssertionError):
            failed = True
        assert failed == must_fail, name
    assert (W / 'fixture-valid/nested/value').read_bytes() == b'abc'
    # Exercise production HTTP request headers, exact byte bounds and digest checks.
    payload = b'public-synthetic-asset'
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            assert self.headers['User-Agent'] == 'bambi-native-baseline'
            assert self.headers['Accept'] == 'application/octet-stream'
            code = 404 if self.path == '/missing' else 200
            self.send_response(code)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for name, suffix, size, digest, fail in (
            ('valid', '/asset', len(payload), hashlib.sha256(payload).hexdigest(), False),
            ('too-big', '/asset', len(payload)-1, hashlib.sha256(payload).hexdigest(), True),
            ('bad-hash', '/asset', len(payload), '0'*64, True),
            ('missing', '/missing', len(payload), hashlib.sha256(payload).hexdigest(), True),
        ):
            spec = {'browser_download_url': 'http://127.0.0.1:'+str(server.server_port)+suffix,
                    'size': size, 'digest': 'sha256:'+digest}
            failed = False
            try:
                download(spec, W/('http-fixture-'+name), fixture=True)
            except (AssertionError, urllib.error.HTTPError):
                failed = True
            assert failed == fail, name
    finally:
        server.shutdown(); worker.join(timeout=2); server.server_close()
        assert not worker.is_alive()
    # Match the real dependency-tool execution location, not only image Python.
    native = W / 'fixture-native-true'
    native.write_bytes(Path('/bin/true').read_bytes())
    native.chmod(0o755)
    call('same-work-native-exec', [str(native)], cwd=W)
    assert call('helper-accepted-exit', [sys.executable, '-c', 'import sys;sys.exit(3)'],
                accepted=(0, 3), cwd=W) == 3
    try:
        call('helper-rejected-exit', [sys.executable, '-c', 'import sys;sys.exit(4)'], cwd=W)
        raise AssertionError('nonzero exit was accepted')
    except RuntimeError as exc:
        assert 'command exit 4,' in str(exc)
    for name in ('HOME', 'XDG_CACHE_HOME', 'MPLCONFIGDIR', 'NUMBA_CACHE_DIR',
                 'PRE_COMMIT_HOME', 'BAMBI_DATA', 'PIXI_CACHE_DIR'):
        path = Path(os.environ[name]); path.mkdir(parents=True, exist_ok=True)
        (path / 'fixture-writable').write_text('synthetic')
    for path in (Path('/work/pytensor-cache'), Path('/tmp')):
        path.mkdir(exist_ok=True)
        (path/'fixture-writable').write_text('synthetic')
    diagnostic_root=W/'shipping-export-fixture';diagnostic_root.mkdir()
    (diagnostic_root/'receipt.json').write_text('{"synthetic":true}')
    (diagnostic_root/'never-export.whl').write_bytes(b'not-a-real-wheel')
    assert export_shipping_diagnostics(diagnostic_root)==['receipt.json']
    assert (E/'shipping/receipt.json').is_file() and not (E/'shipping/never-export.whl').exists()
    save('full-test-timing-controls.json',timing_fixtures())
    save('helper-fixture.json', {'status': 'PASS', 'actual_tar_cases': 3, 'actual_http_cases': 4,
                               'same_work_native_exec': True, 'writable_paths': True})

def install():
    assert not SRC.exists()
    shutil.copytree('/source', SRC, symlinks=True)
    verify_source()
    plan = json.loads((P / 'plan.json').read_text())
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SRC).decode().strip() == plan['source']
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=SRC).decode().strip() == plan['source_tree']
    assert subprocess.check_output(['git', 'status', '--porcelain'], cwd=SRC) == b''
    # Never archive or emit Git configuration. Reject checkout credential material.
    config = (SRC / '.git/config').read_text().lower()
    assert 'extraheader' not in config and 'credential' not in config
    tools = W / 'tools'; tools.mkdir()
    for spec in json.loads((P / 'tools.json').read_text()):
        archive = tools / spec['name']; download(spec, archive)
        dest = tools / ('unpacked-'+spec['tag']); extract(archive, dest)
        if spec['name'].startswith('pixi-'):
            candidates = list(dest.rglob('pixi')); assert len(candidates) == 1
            shutil.copy2(candidates[0], PIXI)
        else:
            candidates = list(dest.rglob('bin/quarto')); assert len(candidates) == 1
            (tools / 'quarto-path.txt').write_text(str(candidates[0]))
    call('pixi-version', [str(PIXI), '--version'])
    call('quarto-version', [(tools / 'quarto-path.txt').read_text(), '--version'])
    for _, row in json.loads((P / 'datasets.json').read_text()).items():
        b = (P / 'datasets' / row['filename']).read_bytes()
        assert hashlib.sha256(b).hexdigest() == row['checksum']
        folder = Path(os.environ['BAMBI_DATA']); folder.mkdir(exist_ok=True)
        (folder / row['filename']).write_bytes(b)
    assert hashlib.sha256((P/'canonical-pixi.lock').read_bytes()).hexdigest() == plan['canonical_lock_sha256']
    shutil.copy2(P/'canonical-pixi.lock', SRC/'pixi.lock')
    manifest = SRC/'pixi.toml' if (SRC/'pixi.toml').is_file() else SRC/'pyproject.toml'
    assert manifest.is_file()
    (tools/'manifest-path.txt').write_text(str(manifest))
    call('pixi-install', [str(PIXI), 'install', '--manifest-path', str(manifest), '--frozen', '--environment', ENV])
    assert hashlib.sha256((SRC/'pixi.lock').read_bytes()).hexdigest() == plan['canonical_lock_sha256']
    assert (SRC / 'pixi.lock').is_file()
    shutil.copy2(SRC / 'pixi.lock', E / 'pixi.lock')
    call('pixi-list', [str(PIXI), 'list', '--manifest-path', str(manifest), '--environment', ENV, '--json'])
    try:
        list_is_json = isinstance(json.loads((E/'pixi-list.log').read_text()), list)
    except (ValueError, TypeError):
        list_is_json = False
    save('pixi-list-json-status.json', {'pixi_list_json': list_is_json,
                                      'record_only': True, 'raw_log': 'pixi-list.log'})
    shutil.copy2(E / 'pixi-list.log', E / 'pixi-list.json')
    save('lock-identity.json', {'sha256': hashlib.sha256((SRC/'pixi.lock').read_bytes()).hexdigest(),
         'environment_list_sha256': hashlib.sha256((E/'pixi-list.json').read_bytes()).hexdigest(),
         'environment': ENV, 'source': plan['source']})
    shipping=W/'shipping';shipping.mkdir(exist_ok=False)
    assets=json.loads((P/'shipping-assets.json').read_text())
    for item in assets['fixed']:
        download({'browser_download_url':item['url'],'size':item['bytes'],'digest':'sha256:'+item['sha256']},shipping/item['filename'])
    official=json.loads((P/'official-black.json').read_text())
    download({'browser_download_url':official['url'],'size':official['bytes'],'digest':'sha256:'+official['sha256']},shipping/official['filename'])
    verify_source()

def verify_junit_identity(cases, nodeids):
    expected = set()
    for node in nodeids:
        parts = node.split('::')
        assert parts[0].endswith('.py')
        classname = parts[0][:-3].replace('/', '.')
        if len(parts) > 2:
            classname += '.' + '.'.join(parts[1:-1])
        expected.add((classname, parts[-1]))
    actual = [(case.attrib.get('classname'), case.attrib.get('name')) for case in cases]
    assert len(expected) == len(nodeids) == len(actual) == len(set(actual))
    assert set(actual) == expected, 'JUNIT_NODE_IDENTITY_UNION_CHANGED'

def freeze():
    assert hashlib.sha256((SRC/'pixi.lock').read_bytes()).hexdigest()==PLAN['canonical_lock_sha256']
    assert (E/'pixi-list.json').is_file()
    save('final-kernel-metrics.json',metrics())
    save('cache-paths.json',{'full_environment_archived':False,'partialCacheExportSkipped':True,'scientificEvidencePriority':True})


def fixture(kind):
    if kind == 'success':
        time.sleep(20); print('F0_20_SECOND_SUCCESS', flush=True)
    elif kind == 'fail':
        print('F0_EXPECTED_EXIT_3', flush=True); sys.exit(3)
    elif kind == 'oom':
        block = bytearray(2 * 1024**3); time.sleep(20); assert block
    elif kind in ('timeout', 'deadline'):
        time.sleep(20)
    else:
        raise ValueError(kind)


def observer_controls():
    tiny = W/'observer-fixture'; tiny.mkdir()
    (tiny/'test_synthetic.py').write_text('import pytest\n'
        'def test_pass(): assert True\n'
        'def test_fail(): assert False\n'
        '@pytest.mark.skip(reason="synthetic skip")\n'
        'def test_skip(): pass\n')
    os.environ['M0_COLLECTION_OUTPUT'] = str(E/'fixture-collected.json')
    os.environ['M0_REPORT_OUTPUT'] = str(E/'fixture-reports.jsonl')
    os.environ['M0_TIMING_OUTPUT'] = str(E/'fixture-progress.jsonl')
    assert pixi('observer-fixture', ['pytest', '-p', 'collection_plugin', '-q',
        '--junitxml=/work/evidence/fixture-results.xml', str(tiny)],
                accepted=(1,)) == 1
    ids = json.loads((E/'fixture-collected.json').read_text())
    reports = [json.loads(s) for s in (E/'fixture-reports.jsonl').read_text().splitlines()]
    assert len(ids) == 3 and {r['nodeid'] for r in reports if r['when'] == 'teardown'} == set(ids)
    assert {r['outcome'] for r in reports} == {'passed', 'failed', 'skipped'}
    actual_cases = ET.parse(E/'fixture-results.xml').findall('.//testcase')
    verify_junit_identity(actual_cases, ids)
    mutations = [('missing', actual_cases[:-1]), ('duplicate', actual_cases+[actual_cases[0]])]
    for field in ('name', 'classname'):
        changed = [ET.fromstring(ET.tostring(case)) for case in actual_cases]
        changed[0].set(field, 'deliberately-wrong')
        mutations.append(('wrong-'+field, changed))
    rejected = []
    for label, changed in mutations:
        try:
            verify_junit_identity(changed, ids)
        except AssertionError:
            rejected.append(label)
        else:
            raise AssertionError('Invalid actual JUnit accepted: '+label)
    save('junit-identity-controls.json', {'status': 'PASS', 'actualPytestXMLUsed': True,
        'rejected': rejected, 'positiveRealPassFailSkipIdentityUnion': True})

    assert all(isinstance(r['duration'], (int, float)) and 0 <= r['duration'] < 300
               and r['wallTime'] > 0 and r['monotonic'] > 0 for r in reports)
    timing = [json.loads(line) for line in (E/'fixture-progress.jsonl').read_text().splitlines()]
    assert {r['nodeid'] for r in timing if r['event'] == 'start'} == set(ids)
    assert {r['nodeid'] for r in timing if r['event'] == 'finish'} == set(ids)
    # Real installed pytest timeout/stack evidence: setup and start retained, never a false completed case.
    (tiny/'test_slow.py').write_text('import time\ndef test_timed_wait(): time.sleep(20)\n')
    os.environ['M0_COLLECTION_OUTPUT'] = str(E/'fixture-timeout-collected.json')
    os.environ['M0_REPORT_OUTPUT'] = str(E/'fixture-timeout-reports.jsonl')
    os.environ['M0_TIMING_OUTPUT'] = str(E/'fixture-timeout-progress.jsonl')
    assert pixi('observer-timeout-fixture', ['timeout', '-k', '2', '8', 'pytest',
        '-p', 'collection_plugin', '-o', 'faulthandler_timeout=2', '-vv', str(tiny/'test_slow.py')],
        accepted=(124,)) == 124
    slow = [json.loads(line) for line in (E/'fixture-timeout-progress.jsonl').read_text().splitlines()]
    assert len([x for x in slow if x['event'] == 'start']) == 1
    assert not any(x['event'] == 'finish' for x in slow)
    timeout_reports = [json.loads(line) for line in (E/'fixture-timeout-reports.jsonl').read_text().splitlines()]
    assert any(x['when'] == 'setup' and x['outcome'] == 'passed' for x in timeout_reports)
    assert not any(x['when'] in ('call', 'teardown') for x in timeout_reports)
    failure_log = (E/'observer-timeout-fixture.log').read_text()
    assert 'Timeout (' in failure_log and 'test_timed_wait' in failure_log



def verify_changed_path_log(log, expected):
    assert log.is_file() and log.stat().st_size<=1024**2, 'DIFF_LOG_BOUND'
    changed=log.read_text(encoding='utf-8').splitlines()
    assert len(changed)==len(set(changed)) and set(changed)==set(expected), 'EXACT_CONTRIBUTION_PATH_UNION_CHANGED'
    return changed


def logged_git_diff_controls():
    call('prepared-three-diff',['git','diff','--name-only','HEAD'])
    path=E/'prepared-three-diff.log';actual=verify_changed_path_log(path,candidate_docs.PATHS)
    rejected=[]
    for name,lines in [('missing',actual[:-1]),('extra',actual+['unreviewed.py']),('duplicate',actual+[actual[0]])]:
        changed=E/('diff-log-control-'+name+'.txt');changed.write_text('\n'.join(lines)+'\n')
        try:verify_changed_path_log(changed,candidate_docs.PATHS)
        except AssertionError:rejected.append(name)
        else:raise AssertionError('Invalid actual logged Git path union accepted: '+name)
    save('logged-git-path-controls.json',{'status':'PASS','actualGitCommandAndLoggedOutputUsed':True,'positiveActualPaths':actual,'rejected':rejected,'callReturnContract':'integer exitcode; bytes read only from bounded persisted log','sourceNotMutatedByControls':True})


def restore_generated_tracked(witnesses, require_final_four, paths=('docs/changelog.qmd',), label='restore-only-generated-changelog'):
    # The generated rendered site stays fresh; only this one unrelated tracked input is restored.
    assert tuple(paths)==('docs/changelog.qmd',), 'ONLY_PINNED_TRACKED_CHANGELOG_RESTORE'
    source_rows={x['path']:x for x in json.loads((P/'source-manifest.json').read_text())}
    assert set(paths)<=source_rows.keys()
    candidates=[n for n in candidate_docs.PATHS if (SRC/n).is_file()]
    assert set(candidates)==set(candidate_docs.PATHS)
    before={n:candidate_docs.sha(SRC/n)for n in candidates}
    assert witnesses and all(p.is_file() and not p.is_symlink() for p in witnesses)
    witness_before={str(p):candidate_docs.sha(p)for p in witnesses}
    call(label,['git','restore','--source='+PLAN['source'],'--worktree','--',*paths])
    for n in paths:
        r=source_rows[n];raw=(SRC/n).read_bytes()
        assert len(raw)==r['bytes'] and hashlib.sha256(raw).hexdigest()==r['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==r['git_blob']
    assert before=={n:candidate_docs.sha(SRC/n)for n in candidates}, 'RESTORE_CHANGED_CANDIDATE_BYTES'
    assert witness_before=={str(p):candidate_docs.sha(p)for p in witnesses}, 'RESTORE_CHANGED_RENDERED_OUTPUT'
    return {'restoredExactPaths':list(paths),'immutableSource':PLAN['source'],'candidateHashesUnchanged':before,'renderedWitnessHashesUnchanged':witness_before}


def generated_restore_controls():
    target=SRC/'docs/changelog.qmd';original=target.read_bytes();target.write_bytes(original+b'\nSYNTHETIC_RENDER_MUTATION\n')
    mutated=candidate_docs.sha(target);witness=W/'generated-restoration-witness.html';witness.write_text('fresh synthetic render, must remain unchanged')
    rejected=[]
    for label,inputs,paths in [('missing-render-output',[W/'missing-render-output.html'],('docs/changelog.qmd',)),('wrong-restore-path',[witness],('bambi/causal/effects.py',))]:
        try:restore_generated_tracked(inputs,False,paths)
        except AssertionError:rejected.append(label)
        else:raise AssertionError('Invalid restoration control accepted: '+label)
        assert candidate_docs.sha(target)==mutated, 'REJECTED_RESTORE_MUTATED_SOURCE'
    positive=restore_generated_tracked([witness],False,label='fixture-restore-only-generated-changelog')
    assert target.read_bytes()==original and witness.read_text()=='fresh synthetic render, must remain unchanged'
    save('generated-tracked-restore-controls.json',{'status':'PASS','actualGitRestoreAndSourceCommitUsed':True,'positive':positive,'rejectedBeforeGitMutation':rejected,'isolatedDockerCopyOnly':True})


def prepare():
    save('candidate-source-contract-controls.json',candidate_docs.source_contract_controls())
    # Honest feature absence, not a fabricated failing mathematical assertion.
    os.environ.pop('M0_REPORT_OUTPUT',None);os.environ.pop('M0_TIMING_OUTPUT',None)
    os.environ['M0_COLLECTION_OUTPUT']=str(E/'original-collected-nodeids.json')
    pixi('original-collection',['pytest','-p','collection_plugin','--collect-only','-q','tests'])
    original=json.loads((E/'original-collected-nodeids.json').read_text());assert original and len(original)==len(set(original))
    pixi('baseline-causal-absence',['python','/packet/baseline_absence.py'])
    candidate_docs.apply_candidate()
    call('candidate-stage-nine-inputs',['git','add','--',*candidate_docs.PATHS])
    logged_git_diff_controls()
    if ROLE=='docs':generated_restore_controls()
    manifest=Path((W/'tools/manifest-path.txt').read_text())
    call('candidate-pixi-frozen-install',[str(PIXI),'install','--manifest-path',str(manifest),'--frozen','--environment',ENV])
    pixi('candidate-frozen-lock-compatibility',['python','/packet/lock_compatibility.py'])
    pixi('imports',['python','/packet/smoke.py','imports'])
    pixi('hook-install',['pre-commit','install-hooks'])
    pixi('format-candidate',['python','/packet/format_candidate.py'])
    pixi('record-actual-upstream-hook-version',['python','/packet/upstream_hook_identity.py'])
    verify_source()
    pixi('early-precommit',['pre-commit','run','--all'])
    pixi('early-pylint',['pylint','bambi'])
    observer_controls()
    os.environ['M0_COLLECTION_OUTPUT']=str(E/'reader-pass-collected.json')
    os.environ['M0_REPORT_OUTPUT']=str(E/'reader-pass-reports.jsonl')
    os.environ['M0_TIMING_OUTPUT']=str(E/'reader-pass-progress.jsonl')
    pixi('reader-real-pytest-positive',['pytest','-p','collection_plugin','-q','--junitxml=/work/evidence/reader-pass.xml',str(W/'observer-fixture/test_synthetic.py')+'::test_pass'])
    verify_junit_identity(ET.parse(E/'reader-pass.xml').findall('.//testcase'),json.loads((E/'reader-pass-collected.json').read_text()))
    if ROLE=='docs':
        quarto=(W/'tools/quarto-path.txt').read_text();os.environ['PATH']=str(Path(quarto).parent)+os.pathsep+os.environ['PATH']
        pixi('kernel-install-causal',['python','-m','ipykernel','install','--user','--name','bambi1055-main'])
        pixi('notebook-fixture',['python','/packet/notebook_fixture.py'])
        pixi('quarto-real-semantics',['python','/packet/quarto_semantics.py'])
    if ROLE=='docs':notebook_anchor_controls()
    pixi('result-reader-fixtures',['python','/packet/result_reader.py','fixtures'])
    verify_source()



def shipping_early():
    # Root owns this helper; a missing helper/input is an integration HOLD, never success.
    assert (P/'build_consumer.py').is_file() and (P/'shipping_stage.py').is_file()
    pixi('shipping-early',['python','/packet/shipping_stage.py'])
    receipt=json.loads((E/'shipping-early-results.json').read_text())
    assert receipt['candidateWheelBuilt'] and receipt['cleanConsumerIdentityPassed']
    assert receipt['focusedAPIAndIndependentOraclePassed'] and receipt['uvOfflineOnly']
    verify_source()

def feature():
    started=time.monotonic()
    os.environ['M0_COLLECTION_OUTPUT']=str(E/'feature-collected.json')
    os.environ['M0_REPORT_OUTPUT']=str(E/'feature-reports.jsonl')
    os.environ['M0_TIMING_OUTPUT']=str(E/'feature-progress.jsonl')
    pixi('feature-oracles',['pytest','-p','collection_plugin','-vv','--junitxml=/work/evidence/feature.xml','tests/test_causal.py'])
    ids=json.loads((E/'feature-collected.json').read_text());assert ids and len(ids)==len(set(ids))
    cases=ET.parse(E/'feature.xml').findall('.//testcase');verify_junit_identity(cases,ids)
    assert all(c.find('failure')is None and c.find('error')is None and c.find('skipped')is None for c in cases)
    save('feature-results.json',{'status':'PASS','elapsedSeconds':time.monotonic()-started,'completeNewTestFile':True,'collected':ids,'count':len(ids),'independentScientificOracleCoverageContract':json.loads((P/'feature-validation-contract.json').read_text())['coverage'],'allCasesExecuted':True})
    verify_source()



def baseline():
    assert ROLE=='test'
    actual=json.loads((E/'feature-results.json').read_text());budget=full_test_budget(SPEC['historical_full_test_seconds'],actual)
    SPEC['pytest_seconds']=budget['pytestSeconds'];SPEC['pytest_entry_floor_seconds']=budget['entryFloorSeconds']
    save('actual-full-test-budget.json',budget)
    pixi('offline-imports',['python','/packet/smoke.py','offline-imports'])
    verify_source()
    os.environ.pop('M0_REPORT_OUTPUT',None);os.environ.pop('M0_TIMING_OUTPUT',None)
    os.environ['M0_COLLECTION_OUTPUT']=str(E/'collected-nodeids.json')
    pixi('collection',['pytest','-p','collection_plugin','--collect-only','-q','tests'])
    ids=json.loads((E/'collected-nodeids.json').read_text());assert ids and len(ids)==len(set(ids))
    original=json.loads((E/'original-collected-nodeids.json').read_text());new=json.loads((E/'feature-results.json').read_text())['collected']
    assert set(original).issubset(ids) and set(ids)-set(original)==set(new),'ORIGINAL_CASE_LOST_OR_NEW_FEATURE_CASE_OMITTED'
    assert float(os.environ['M0_TARGET_EPOCH'])-time.time() >= SPEC['pytest_entry_floor_seconds']
    os.environ['M0_COLLECTION_OUTPUT']=str(E/'executed-collection.json')
    os.environ['M0_REPORT_OUTPUT']=str(E/'test-reports.jsonl')
    os.environ['M0_TIMING_OUTPUT']=str(E/'test-progress.jsonl')
    code=pixi('pytest',['pytest','-p','collection_plugin','-vv','--junitxml=/work/evidence/baseline.xml','--durations=0','-o','faulthandler_timeout=60','--cov=bambi','--cov-report=term','--cov-report=xml','tests'],accepted=(0,1))
    assert json.loads((E/'executed-collection.json').read_text())==ids
    reports=[json.loads(x)for x in(E/'test-reports.jsonl').read_text().splitlines()]
    completed={x['nodeid']for x in reports if x['when']=='teardown'};assert completed==set(ids)
    progress=[json.loads(x)for x in(E/'test-progress.jsonl').read_text().splitlines()]
    assert [x['nodeid']for x in progress if x['event']=='start']==ids
    assert [x['nodeid']for x in progress if x['event']=='finish']==ids
    cases=ET.parse(E/'baseline.xml').findall('.//testcase');verify_junit_identity(cases,ids)
    failures=[c.attrib for c in cases if c.find('failure')is not None or c.find('error')is not None]
    skips=[{'classname':c.attrib.get('classname'),'name':c.attrib.get('name'),'reason':c.find('skipped').attrib.get('message')}for c in cases if c.find('skipped')is not None]
    expected_skips={'tests.test_model_construction::test_init_fallback'}
    actual_skips={x['classname']+'::'+x['name']for x in skips}
    save('main-full-test-results.json',{'pytestExit':code,'collectedCount':len(ids),'completedCount':len(completed),'collectionSetSHA256':hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest(),'failures':failures,'skips':skips,'historicalSkipRule':'One original upstream unimplemented fallback skip; actual different skip findings FAIL for review','skipSetUnchanged':actual_skips==expected_skips,'precommit':0,'pylint':0,'source':PLAN['source'],'noHardcodedDevNodeCount':True,'originalCollectedNodeIDs':original,'newFeatureNodeIDs':new})
    assert not failures and code==0 and actual_skips==expected_skips,'CURRENT_MAIN_NONPASS_OR_SKIP_CHANGE'
    pixi('coverage-report',['coverage','report','-m'])
    assert (SRC/'coverage.xml').is_file();shutil.copy2(SRC/'coverage.xml',E/'coverage.xml')
    verify_source()


def example_measurement():
    assert ROLE=='docs' and json.loads((E/'feature-results.json').read_text())['status']=='PASS'
    pixi('causal-notebook',['python','/packet/notebook_measure.py'])
    call('candidate-stage-final-nine',['git','add','--',*candidate_docs.PATHS])
    before={n:candidate_docs.sha(SRC/n)for n in candidate_docs.PATHS}
    pixi('candidate-nine-path-hooks',['pre-commit','run','--files',*candidate_docs.PATHS])
    assert before=={n:candidate_docs.sha(SRC/n)for n in candidate_docs.PATHS},'FINAL_HOOK_MODIFIED_TESTED_BYTES'
    save('candidate-nine-path-hooks.json',{'exit':0,'allFinalBytesUnchanged':True,'filesSHA256':before})
    verify_source()



def has_notebook_anchor(page,site,expected):
    from html.parser import HTMLParser
    from urllib.parse import urlsplit,unquote
    assert page.is_file() and page.stat().st_size<=4*1024**2
    assert expected.is_file() and expected.resolve().is_relative_to(site.resolve())
    class Anchors(HTMLParser):
        def __init__(self):super().__init__();self.hrefs=[]
        def handle_starttag(self,tag,attrs):
            if tag=='a':self.hrefs.extend(v for k,v in attrs if k=='href'and v)
    parsed=Anchors();parsed.feed(page.read_text());matches=[]
    for href in parsed.hrefs:
        u=urlsplit(href)
        if u.scheme or u.netloc or not u.path:continue
        n=unquote(u.path)
        target=(site/n[len('/bambi/'):]if n.startswith('/bambi/')else site/n.lstrip('/')if n.startswith('/')else page.parent/n).resolve()
        if target==expected.resolve():matches.append(href)
    return matches

def notebook_anchor_controls():
    site=W/'anchor-parser-fixture';(site/'api').mkdir(parents=True);(site/'notebooks').mkdir()
    target=site/'notebooks/causal_effects.html';target.write_text('synthetic target')
    page=site/'api/causal.g_computation.html';page.write_text('<a href="../notebooks/causal_effects.html#example">example</a>')
    assert has_notebook_anchor(page,site,target)==['../notebooks/causal_effects.html#example']
    rejected=[]
    for name,text in [('text-only','causal_effects.html'),('image-only','<img src="../notebooks/causal_effects.html">'),('wrong-link','<a href="../notebooks/wrong.html">example</a>'),('unconverted-ipynb','<a href="../notebooks/causal_effects.ipynb">example</a>')]:
        page.write_text(text);assert not has_notebook_anchor(page,site,target);rejected.append(name)
    save('notebook-anchor-controls.json',{'status':'PASS','actualHTMLParserAndPathResolution':True,'rejected':rejected,'syntheticControlsNotActualSitePass':True})

def full_site():
    assert ROLE=='docs'
    quarto=(W/'tools/quarto-path.txt').read_text();os.environ['PATH']=str(Path(quarto).parent)+os.pathsep+os.environ['PATH']
    before={n:candidate_docs.sha(SRC/n)for n in candidate_docs.PATHS}
    pixi('full-quartodoc',['python','-m','quartodoc','build','--verbose'],cwd=SRC/'docs')
    pixi('full-interlinks',['python','-m','quartodoc','interlinks'],cwd=SRC/'docs')
    pixi('full-site',[quarto,'render'],cwd=SRC/'docs')
    site=SRC/'docs/_site';assert site.is_dir();(site/'.nojekyll').touch()
    pixi('full-inventory',['python','-m','sphobjinv','convert','-o','zlib','objects.txt','objects.inv'],cwd=SRC/'docs')
    shutil.move(SRC/'docs/objects.inv',site/'objects.inv');assert(site/'objects.inv').stat().st_size>0
    from html.parser import HTMLParser
    from urllib.parse import urlsplit,unquote
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.links=[]
        def handle_starttag(self,tag,attrs):
            for k,v in attrs:
                if k in ('src','href')and v:self.links.append(v)
    broken=[];external=set();pages=list(site.rglob('*.html'));assert pages
    for page in pages:
        parser=Links();parser.feed(page.read_text())
        for href in parser.links:
            u=urlsplit(href)
            if u.scheme or u.netloc:external.add(href);continue
            if not u.path:continue
            n=unquote(u.path);target=(site/n[len('/bambi/'):] if n.startswith('/bambi/')else site/n.lstrip('/')if n.startswith('/')else page.parent/n).resolve()
            if not target.is_relative_to(site.resolve())or not target.exists():broken.append({'page':str(page.relative_to(site)),'href':href})
    assert before=={n:candidate_docs.sha(SRC/n)for n in candidate_docs.PATHS}
    assert (site/'notebooks/causal_effects.html').is_file()
    example=site/'notebooks/causal_effects.html'
    rendered=example.read_text();assert 'Causal effects by posterior g-computation' in rendered and 'plot' in rendered.lower(),'REAL_CAUSAL_NOTEBOOK_OUTPUT_REQUIRED'
    api_pages=[p for p in pages if p.is_relative_to(site/'api') and 'g_computation' in p.read_text() or p.is_relative_to(site/'api') and 'CausalResult' in p.read_text() or p.is_relative_to(site/'api') and 'plot_effects' in p.read_text()]
    assert api_pages,'CAUSAL_API_NOT_EXPOSED_BY_FULL_QUARTODOC'
    api_text='\n'.join(p.read_text()for p in api_pages)
    assert all(n in api_text for n in ('g_computation','plot_effects','CausalResult')),'COMPLETE_CAUSAL_API_DOCS_REQUIRED'
    matched={str(page.relative_to(site)):has_notebook_anchor(page,site,example)for page in pages}
    assert any(matched.values()),'SIDEBAR_REAL_CAUSAL_NOTEBOOK_HTML_ANCHOR_MISSING'
    save('actual-api-notebook-anchor.json',{'actualParsedLinks':matched,'target':str(example.relative_to(site)),'realResolvedHTMLAnchor':True})
    shutil.copy2(example,E/'causal-effects-rendered.html')
    for i,page in enumerate(api_pages):shutil.copy2(page,E/('causal-api-rendered-'+str(i)+'.html'))
    save('full-site-links.json',{'pages':len(pages),'brokenLocalResources':broken,'externalNotFetched':sorted(external),'fragments':'NOT_CHECKED'})
    save('full-site-summary.json',{'fullRenderCompleted':True,'pages':len(pages),'brokenLocalCount':len(broken),'actualCandidateNotebookExecutedSeparately':True,'actualRichPlotPixels':'PENDING_ACTUAL_BROWSER_INSPECTION','unmodifiedNotebooksStoredOutputSemantics':json.loads((E/'quarto-actual-semantics.json').read_text()),'finalContributionHashes':before})
    assert not broken,'BROKEN_LOCAL_SITE_LINKS'
    witness=[site/'changelog.html',example,*api_pages]
    restoration=restore_generated_tracked(witness,True)
    save('tracked-generated-changelog-restoration.json',restoration)
    verify_source()
    call('final-nine-diff',['git','diff','--name-only','HEAD'])
    verify_changed_path_log(E/'final-nine-diff.log',candidate_docs.PATHS)
    call('final-nine-patch',['git','diff','--binary','HEAD','--',*candidate_docs.PATHS])
    shutil.copy2(E/'final-nine-patch.log',E/'tested-contribution.patch')
    verify_source()


if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='metrics':print(json.dumps(metrics()))
    elif mode=='fixture':fixture(sys.argv[2])
    else:
        funcs={'helper-fixture':helper_fixture,'install':install,'prepare':prepare,'shipping-early':shipping_early,'feature':feature,'example':example_measurement,'baseline':baseline,'full-site':full_site,'freeze':freeze}
        row={'phase':mode,'started':time.time(),'success':False}
        try:funcs[mode]();row['success']=True
        except BaseException as exc:row['first_failure']={'type':type(exc).__name__,'message':str(exc)};raise
        finally:row.update(elapsed=time.time()-row['started'],metrics=metrics());save(mode+'-receipt.json',row)
