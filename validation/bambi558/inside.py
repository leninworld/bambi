"""Faithful Bambi558 candidate validation. Original G0 attempts remain closed."""
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

P = Path('/packet')
W = Path('/work')
E = W / 'evidence'
E.mkdir(exist_ok=True)
SRC = W / 'src'
PIXI = W / 'tools/pixi'
ENV = os.environ['G0_ENV']
ROLE = os.environ['G0_ROLE']
LINKER = os.environ['G0_LINKER']
PLAN = json.loads((P/'plan.json').read_text())
SPEC = PLAN['lanes'][os.environ['G0_LANE']]
assert ENV in ('py312','py313','dev') and ROLE in ('test','docs') and LINKER in ('cvm','numba')


def save(name, value):
    (E / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def metrics():
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
    seconds = min(remaining, SPEC.get('pytest_seconds',1800) if label == 'pytest' else 850 if label == 'pixi-install'
                  else 600 if label == 'full-docs-render' else 300 if label in ('quartodoc', 'hook-install') else 180)
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
    return call(label, [str(PIXI), 'run', '--frozen', '--environment', ENV, *args], accepted, cwd)


def verify_rows(root, rows):
    for r in rows:
        f = root / r['path']
        b = os.readlink(f).encode() if r['mode'] == '120000' else f.read_bytes()
        assert len(b) == r['bytes'] and hashlib.sha256(b).hexdigest() == r['sha256'], r['path']
        assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest() == r['git_blob']
        if r['mode'] != '120000':
            assert bool(f.stat().st_mode & 0o111) == (r['mode'] == '100755'), r['path']


def verify_source():
    rows = json.loads((P / ('candidate-source-manifest.json' if (W/'candidate-applied').exists() else 'source-manifest.json')).read_text())
    assert len(rows) == 239
    verify_rows(SRC, rows)


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
    assert (fixture and url.startswith('http://127.0.0.1:')) or url.startswith((
        'https://github.com/prefix-dev/pixi/releases/download/v0.62.2/',
        'https://github.com/quarto-dev/quarto-cli/releases/download/v1.8.26/'))
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
    # Exact source verifier's real file, mode, size, SHA256 and Git-blob branches.
    root = W/'source-fixture'; root.mkdir()
    (root/'value').write_bytes(b'abc'); (root/'value').chmod(0o644)
    row = {'path':'value','mode':'100644','bytes':3,'sha256':hashlib.sha256(b'abc').hexdigest(),'git_blob':hashlib.sha1(b'blob 3\0abc').hexdigest()}
    verify_rows(root, [row])
    for field, bad in [('bytes',4),('sha256','0'*64),('git_blob','0'*40),('mode','100755')]:
        try:
            verify_rows(root, [{**row,field:bad}])
            raise RuntimeError('source identity failure accepted: '+field)
        except AssertionError:
            pass
    overlay = PLAN['overlay'][0]
    for suffix, corrupt in (('valid', False), ('wrong-original', True)):
        root = W/('overlay-fixture-'+suffix)
        target = root/overlay['path']; target.parent.mkdir(parents=True)
        # helper-fixture has no source mount: the packet carries the exact original bytes.
        original_blob = (P/'original-test_model_construction.py').read_bytes()
        target.write_bytes(original_blob + (b'corrupt' if corrupt else b''))
        rejected=False
        try:
            copy_overlay(root, [overlay])
        except AssertionError:
            rejected=True
        assert rejected==corrupt
        if not corrupt:
            verify_rows(root,[overlay['candidate']])

    from bounded import ByteBudgetWriter, stage_upload
    sink = io.BytesIO(); writer = ByteBudgetWriter(sink,3)
    assert writer.write(b'abc') == 3
    try:
        writer.write(b'x'); raise RuntimeError('archive cap accepted overflow')
    except OverflowError:
        pass
    assert sink.getvalue() == b'abc'
    evidence = W/'upload-fixture'; evidence.mkdir()
    (evidence/'summary.json').write_text('{}')
    ok = stage_upload(evidence, W/'upload-valid', 4096)
    assert not ok['cap_hit']
    (evidence/'too-large.log').write_bytes(b'x'*5000)
    bad = stage_upload(evidence, W/'upload-capped', 4096)
    assert bad['cap_hit'] and not (W/'upload-capped/too-large.log').exists()
    assert (evidence/'too-large.log').stat().st_size == 5000
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
        if ROLE != 'docs' and spec['name'].startswith('quarto-'):
            continue
        archive = tools / spec['name']; download(spec, archive)
        dest = tools / ('unpacked-'+spec['tag']); extract(archive, dest)
        if spec['name'].startswith('pixi-'):
            candidates = list(dest.rglob('pixi')); assert len(candidates) == 1
            shutil.copy2(candidates[0], PIXI)
        else:
            candidates = list(dest.rglob('bin/quarto')); assert len(candidates) == 1
            (tools / 'quarto-path.txt').write_text(str(candidates[0]))
    call('pixi-version', [str(PIXI), '--version'])
    if ROLE == 'docs':
        call('quarto-version', [(tools / 'quarto-path.txt').read_text(), '--version'])
    for _, row in json.loads((P / 'datasets.json').read_text()).items():
        b = (P / 'datasets' / row['filename']).read_bytes()
        assert hashlib.sha256(b).hexdigest() == row['checksum']
        folder = Path(os.environ['BAMBI_DATA']); folder.mkdir(exist_ok=True)
        (folder / row['filename']).write_bytes(b)
    # Reuse the complete same-source lock; no environment may solve or rewrite it.
    shutil.copy2(P / 'canonical-pixi.lock', SRC / 'pixi.lock')
    verify_rows(SRC, [PLAN['canonical_lock']])
    call('pixi-install', [str(PIXI), 'install', '--frozen', '--environment', ENV])
    verify_rows(SRC, [PLAN['canonical_lock']])
    shutil.copy2(SRC / 'pixi.lock', E / 'pixi.lock')
    call('pixi-list', [str(PIXI), 'list', '--environment', ENV, '--json'])
    try:
        list_is_json = isinstance(json.loads((E/'pixi-list.log').read_text()), list)
    except (ValueError, TypeError):
        list_is_json = False
    save('pixi-list-json-status.json', {'pixi_list_json': list_is_json,
                                      'record_only': True, 'raw_log': 'pixi-list.log'})
    # Pixi0.62.2 JSON shape is unverified: raw output and actual lock are retained; format is record-only.
    shutil.copy2(E / 'pixi-list.log', E / 'pixi-list.json')
    save('lock-identity.json', {'sha256': hashlib.sha256((SRC/'pixi.lock').read_bytes()).hexdigest(),
         'environment_list_sha256': hashlib.sha256((E/'pixi-list.json').read_bytes()).hexdigest(),
         'environment': ENV, 'source': plan['source']})
    verify_source()
    apply_candidate()



def copy_overlay(root, overlays):
    for overlay in overlays:
        target = root / overlay['path']
        assert not target.is_symlink()
        verify_rows(root, [overlay['original']])
        blob = (P / overlay['packet_file']).read_bytes()
        candidate = overlay['candidate']
        assert len(blob) == candidate['bytes']
        assert hashlib.sha256(blob).hexdigest() == candidate['sha256']
        target.write_bytes(blob)
        verify_rows(root, [candidate])


def apply_candidate():
    assert not (W/'candidate-applied').exists()
    verify_source()
    copy_overlay(SRC, PLAN['overlay'])
    (W/'candidate-applied').write_text(PLAN['candidate_patch_sha256'])
    verify_source()
    actual = subprocess.check_output(['git', 'diff', '--binary', '--full-index', '--no-color', '--no-ext-diff', '--', 'tests/test_model_construction.py'], cwd=SRC)
    assert actual == (P/'candidate.patch').read_bytes()
    save('applied-candidate.json', {'patch_sha256': hashlib.sha256(actual).hexdigest(),
         'source': PLAN['source'], 'production_change': False, 'files': PLAN['overlay']})


def focused(label, expected=(0,)):
    os.environ['M0_COLLECTION_OUTPUT'] = str(E/(label+'-collection.json'))
    os.environ['M0_REPORT_OUTPUT'] = str(E/(label+'-reports.jsonl'))
    result = pixi(label, ['pytest', '-p', 'collection_plugin', '-vv',
          '--junitxml='+str(E/(label+'.xml')), 'tests/test_model_construction.py', '-k', 'init_fallback'], accepted=expected)
    ids = json.loads((E/(label+'-collection.json')).read_text())
    reports = [json.loads(v) for v in (E/(label+'-reports.jsonl')).read_text().splitlines()]
    assert len(ids) == 11 and len(ids) == len(set(ids))
    assert {x['nodeid'] for x in reports if x['when']=='teardown'} == set(ids)
    assert not any(x['outcome']=='skipped' for x in reports)
    cases = ET.parse(E/(label+'.xml')).findall('.//testcase')
    assert len(cases)==11 and not any(c.find('error') is not None for c in cases)
    failures = [c.attrib['name'] for c in cases if c.find('failure') is not None]
    assert bool(failures) == (result==1)
    save(label+'-result.json', {'exit':result,'collected':ids,'failures':failures,
         'scope':'controlled dependency-raiser / real small-fit tests, not numerical reproduction or parallel recovery'})
    return failures


def candidate_early():
    verify_source()
    focused('early-fallback')
    if os.environ['G0_LANE'] != PLAN['mutation_lane']:
        return
    backend = SRC/'bambi/backend/pymc.py'
    original = backend.read_text()
    guard = 'if "ValueError: Mass matrix contains" in traceback.format_exc() and init == "auto":'
    assert original.count(guard)==1
    retry = 'init="adapt_diag",'
    assert original.count(retry)==1
    retry_start = original.index(retry)
    seed_position = original.index('random_seed=random_seed,', retry_start)
    mcmc_scope = original[original.index('    def _run_mcmc('):original.index('    def _clean_results(')]
    assert mcmc_scope.count('random_seed=random_seed,')==2
    mutants = {
      'disabled': original.replace(guard, 'if False:', 1),
      'wrong-init': original.replace(retry, 'init="auto",', 1),
      'lost-seed': original[:seed_position]+original[seed_position:].replace('random_seed=random_seed,','random_seed=1235,',1),
      'unrelated-retry': original.replace(guard, 'if init == "auto":', 1),
      'explicit-retry': original.replace(guard, 'if "ValueError: Mass matrix contains" in traceback.format_exc():', 1),
      'duplicate-log': original.replace(guard, guard+'\n                    _log.info("The default initialization duplicate")', 1),
    }
    required_failure_prefix = {
      'disabled': 'test_init_fallback[', 'wrong-init': 'test_init_fallback[',
      'lost-seed': 'test_init_fallback[', 'unrelated-retry': 'test_init_fallback_unrelated_error[',
      'explicit-retry': 'test_init_fallback_explicit_init[', 'duplicate-log': 'test_init_fallback[',
    }
    mutations=[]
    for name, changed in mutants.items():
        assert changed != original
        try:
            backend.write_text(changed)
            failures=focused('mutation-'+name, (1,))
            assert any(v.startswith(required_failure_prefix[name]) for v in failures), 'WRONG_MUTATION_FAILURE'
            mutations.append({'name':name, 'failures':failures,
                'production_sha256':hashlib.sha256(changed.encode()).hexdigest()})
        finally:
            backend.write_text(original)
            verify_source()
    save('mutation-sensitivity.json', {'all_detected':True,'mutants':mutations,
           'never_committed':True,'all_source_bytes_restored':True})


def prepare():
    pixi('imports', ['python', '/packet/smoke.py', 'imports'])
    pixi('dependency-diagnostic', ['python', '/packet/diagnostic.py'])
    if ROLE == 'test':
        pixi('hook-install', ['pre-commit', 'install-hooks'])
    if ROLE == 'test':
        pixi('candidate-black', ['pre-commit', 'run', 'black', '--files', 'tests/test_model_construction.py'])
        verify_source()
        pixi('early-precommit', ['pre-commit', 'run', '--all'])
        verify_source()
        pixi('early-pylint', ['pylint', 'bambi'])
    # Actual installed pytest exercises the changed result observer, including failure/skip.
    tiny = W/'observer-fixture'; tiny.mkdir()
    (tiny/'test_synthetic.py').write_text('import pytest\n'
        'def test_pass(): assert True\n'
        'def test_fail(): assert False\n'
        '@pytest.mark.skip(reason="synthetic skip")\n'
        'def test_skip(): pass\n')
    os.environ['M0_COLLECTION_OUTPUT'] = str(E/'fixture-collected.json')
    os.environ['M0_REPORT_OUTPUT'] = str(E/'fixture-reports.jsonl')
    assert pixi('observer-fixture', ['pytest', '-p', 'collection_plugin', '-q', str(tiny)],
                accepted=(1,)) == 1
    ids = json.loads((E/'fixture-collected.json').read_text())
    reports = [json.loads(s) for s in (E/'fixture-reports.jsonl').read_text().splitlines()]
    assert len(ids) == 3 and {r['nodeid'] for r in reports if r['when'] == 'teardown'} == set(ids)
    assert {r['outcome'] for r in reports} == {'passed', 'failed', 'skipped'}
    if ROLE == 'test':
        candidate_early()
    # Every candidate tracked byte must remain exact, including after controlled mutations.
    call('prepare-tracked-diff', ['git', 'diff', '--stat'])
    verify_source()


def baseline():
    verify_rows(SRC, [PLAN['canonical_lock']])
    pixi('offline-imports', ['python', '/packet/smoke.py', 'offline-imports'])
    if ROLE == 'docs':
        docs()
        return
    code = lint = 0  # Required early lint passed in prepare; no second unchanged lint run.
    diff = subprocess.run(['git', 'diff', '--exit-code'], cwd=SRC, capture_output=True)
    assert diff.returncode == 1, 'CANDIDATE_DIFF_MISSING'
    verify_source()
    os.environ['M0_COLLECTION_OUTPUT'] = '/work/evidence/collected-nodeids.json'
    pixi('collection', ['pytest', '-p', 'collection_plugin', '--collect-only', '-q', 'tests'])
    ids = json.loads((E/'collected-nodeids.json').read_text()); assert ids and len(ids) == len(set(ids))
    assert float(os.environ['M0_TARGET_EPOCH'])-time.time() >= SPEC['pytest_entry_floor_seconds'], 'INSUFFICIENT_PYTEST_TIME'
    os.environ['M0_COLLECTION_OUTPUT'] = '/work/evidence/executed-collection.json'
    os.environ['M0_REPORT_OUTPUT'] = '/work/evidence/test-reports.jsonl'
    test_code = pixi('pytest', ['pytest', '-p', 'collection_plugin', '-vv', '--junitxml=/work/evidence/candidate.xml',
                              '--durations=50', '--cov=bambi', '--cov-report=term', '--cov-report=xml', 'tests'], accepted=(0, 1))
    assert json.loads((E/'executed-collection.json').read_text()) == ids
    assert len(ids) == PLAN['candidate_expected_collected_count'], 'FULL_ORIGINAL_TEST_INVENTORY_CHANGED'
    original_ids = set(json.loads((P/'baseline-nodeids.json').read_text()))
    old_skip = 'tests/test_model_construction.py::test_init_fallback'
    assert old_skip in original_ids
    assert set(ids).issuperset(original_ids - {old_skip}), 'ORIGINAL_TEST_DROPPED'
    assert old_skip not in ids
    reports = [json.loads(line) for line in (E/'test-reports.jsonl').read_text().splitlines()]
    finished = {r['nodeid'] for r in reports if r['when'] == 'teardown'}
    assert finished == set(ids), 'INCOMPLETE_TEST_RESULTS'
    doc = ET.parse(E/'candidate.xml')
    cases = doc.findall('.//testcase'); assert cases
    rows = [{'name': c.attrib.get('name'), 'classname': c.attrib.get('classname'),
             'time': c.attrib.get('time'), 'status': 'failure' if c.find('failure') is not None
             else 'error' if c.find('error') is not None else 'skip' if c.find('skipped') is not None else 'pass'} for c in cases]
    save('candidate-test-results.json', {'pytest_exit': test_code, 'precommit_exit': code,
         'pylint_exit': lint, 'tracked_autofixes': False, 'cases': rows,
         'collected_count': len(ids), 'completed_count': len(finished),
         'classification': 'CANDIDATE_FULL_RESULTS_REVIEW_REQUIRED'})
    assert len(rows) == len(ids) and all(row['status'] == 'pass' for row in rows), 'CANDIDATE_NONPASS_OR_MISSING_CASE'
    pixi('coverage-report', ['coverage', 'report', '-m'])
    assert code == lint == test_code == 0 and diff.returncode == 1, 'NONZERO_CANDIDATE_NOT_PASS'
    verify_source()


def docs():
    quarto = (W/'tools/quarto-path.txt').read_text()
    os.environ['PATH'] = str(Path(quarto).parent)+os.pathsep+os.environ['PATH']
    pixi('quartodoc', ['python', '-m', 'quartodoc', 'build', '--verbose'], cwd=SRC/'docs')
    pixi('interlinks', ['python', '-m', 'quartodoc', 'interlinks'], cwd=SRC/'docs')
    pixi('full-docs-render', [quarto, 'render'], cwd=SRC/'docs')
    site = SRC/'docs/_site'; assert site.is_dir()
    (site/'.nojekyll').touch()
    pixi('objects-inventory', ['python','-m','sphobjinv','convert','-o','zlib','objects.txt','objects.inv'],cwd=SRC/'docs')
    shutil.move(SRC/'docs/objects.inv',site/'objects.inv')
    assert (site/'objects.inv').stat().st_size > 0
    save('docs-results.json', {'status':'COMPLETE_BUILD_NO_PUBLISH','pages':[x.relative_to(site).as_posix() for x in site.rglob('*.html')], 'notebook_execution':'Quarto workflow defaults only, no claim all fits ran'})


def freeze():
    verify_rows(SRC, [PLAN['canonical_lock']])
    # Explicit allowlist; no whole HOME, environment, Git config or data-history upload.
    for path in ('/work/src/pixi.lock', '/work/evidence/pixi-list.json'):
        assert Path(path).is_file()
    save('final-kernel-metrics.json', metrics())
    save('cache-paths.json', {'compiledir': '/work/pytensor-cache',
                            'pre_commit_home': os.environ['PRE_COMMIT_HOME'],
                            'full_environment_archived': False})
    # Preserve only named scientific/tool caches. Git configs are never uploaded.
    # Missing hook configs make this a partial cache, requiring ordinary rehydration later.
    total = 0
    remaining = max(0, PLAN['artifact_cap']//2 - sum(f.stat().st_size for f in E.rglob('*') if f.is_file()) - 1024**2)
    if remaining < 1024**2:
        save('cache-truncated.json', {'partial':True,'archive_complete':False,'reason':'no evidence budget'})
        return
    from bounded import ByteBudgetWriter
    try:
        with (E/'bounded-cache.tar.gz').open('xb') as raw:
            writer = ByteBudgetWriter(raw, remaining)
            with tarfile.open(fileobj=writer,mode='w|gz',compresslevel=1) as tar:
                for name in ('pytensor-cache', 'precommit-cache'):
                    root = W/name
                    if not root.exists():
                        continue
                    for path in sorted(root.rglob('*')):
                        if path.is_symlink() or not path.is_file():
                            continue
                        relative = path.relative_to(root)
                        if '.git' in relative.parts or path.name in ('config', 'config.json'):
                            continue
                        if total+path.stat().st_size > 40*1024**2:
                            save('cache-truncated.json', {'partial': True, 'reason': '40MiB cache cap'})
                            return
                        tar.add(path, arcname=name+'/'+relative.as_posix(), recursive=False)
                        total += path.stat().st_size

    except OverflowError:
        save('cache-truncated.json', {'partial':True,'archive_complete':False,'reason':'compressed evidence byte cap; archive unusable'})


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


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'metrics':
        print(json.dumps(metrics()))
    elif mode == 'fixture':
        fixture(sys.argv[2])
    else:
        funcs = {'helper-fixture': helper_fixture, 'install': install,
                 'prepare': prepare, 'baseline': baseline, 'freeze': freeze}
        row = {'phase': mode, 'started': time.time(), 'success': False}
        try:
            funcs[mode](); row['success'] = True
        except BaseException as exc:
            row['first_failure'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            row['elapsed'] = time.time()-row['started']
            row['metrics'] = metrics()
            save(mode+'-receipt.json', row)
