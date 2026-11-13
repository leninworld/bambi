"""Native baseline prerequisite measurement. No contribution patch is applied."""
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
ENV = 'py312'


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
    seconds = min(remaining, 1800 if label == 'pytest' else 550 if label == 'pixi-install'
                  else 600 if label in ('full-site', 'science-notebook') else 300 if label in ('quartodoc', 'hook-install') else 180)
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
    assert len(rows) == 275
    for r in rows:
        f = SRC / r['path']
        b = os.readlink(f).encode() if r['mode'] == '120000' else f.read_bytes()
        assert len(b) == r['bytes'] and hashlib.sha256(b).hexdigest() == r['sha256'], r['path']
        assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest() == r['git_blob']
        if r['mode'] != '120000':
            assert bool(f.stat().st_mode & 0o111) == (r['mode'] == '100755'), r['path']


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
        'https://github.com/prefix-dev/pixi/releases/download/v0.76.2/',
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
    verify_source()


def prepare():
    pixi('imports', ['python', '/packet/smoke.py'])
    pixi('hook-install', ['pre-commit', 'install-hooks'])
    quarto = (W/'tools/quarto-path.txt').read_text()
    env = dict(os.environ)
    env['PATH'] = str(Path(quarto).parent)+os.pathsep+env['PATH']
    # Inherit no credentials; only the tool path is added for Quarto discovery.
    os.environ['PATH'] = env['PATH']
    pixi('quartodoc', ['python', '-m', 'quartodoc', 'build', '--verbose'], cwd=SRC/'docs')
    generated = SRC/'docs/api/Model.qmd'
    assert generated.is_file() and 'set_priors' in generated.read_text()
    pixi('interlinks', ['python', '-m', 'quartodoc', 'interlinks'], cwd=SRC/'docs')
    pixi('page-render', [quarto, 'render', 'changelog.qmd'], cwd=SRC/'docs')
    pixi('kernel-install', ['python', '-m', 'ipykernel', 'install', '--user', '--name', 'bambi-m0-py312'])
    pixi('notebook-fixture', ['python', '/packet/notebook_fixture.py'])
    pixi('kernel-717-install', ['python', '-m', 'ipykernel', 'install', '--user', '--name', 'bambi717-baseline'])
    pixi('quarto-real-semantics', ['python', '/packet/quarto_semantics.py'])
    pixi('science-early', ['python', '/packet/science.py', 'early'])
    # Known upstream baseline formatting outcomes are measured, not repaired or called a pass.
    early_lint = pixi('early-precommit', ['pre-commit', 'run', '--all-files'], accepted=(0,1))
    save('early-lint-baseline.json', {'exit':early_lint,'baselineOnly':True,'notCandidatePass':True})
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
    # Only the isolated, exact public baseline copy is restored; record any generators' changes.
    call('prepare-tracked-diff', ['git', 'diff', '--stat'])
    call('prepare-restore-tracked', ['git', 'checkout', '--', '.'])
    verify_source()


def baseline():
    pixi('offline-imports', ['python', '/packet/smoke.py'])
    code = pixi('precommit', ['pre-commit', 'run', '--all-files'], accepted=(0, 1))
    call('precommit-tracked-diff', ['git', 'diff', '--stat'])
    diff = subprocess.run(['git', 'diff', '--exit-code'], cwd=SRC, capture_output=True)
    assert len(diff.stdout) < 1024**2
    (E/'baseline-lint.diff').write_bytes(diff.stdout)
    call('baseline-restore-tracked', ['git', 'checkout', '--', '.'])
    verify_source()
    lint = pixi('pylint', ['pylint', 'bambi'], accepted=tuple(range(64)))
    os.environ['M0_COLLECTION_OUTPUT'] = '/work/evidence/collected-nodeids.json'
    pixi('collection', ['pytest', '-p', 'collection_plugin', '--collect-only', '-q', 'tests'])
    ids = json.loads((E/'collected-nodeids.json').read_text()); assert ids and len(ids) == len(set(ids))
    assert len(ids) == json.loads((P/'plan.json').read_text())['expected_collection_count'], 'SAME_SOURCE_COLLECTION_CHANGED'
    assert float(os.environ['M0_TARGET_EPOCH'])-time.time() >= 1845, 'INSUFFICIENT_FULL_TEST_PLUS_CLEANUP_TIME_AFTER_SITE'
    os.environ['M0_COLLECTION_OUTPUT'] = '/work/evidence/executed-collection.json'
    os.environ['M0_REPORT_OUTPUT'] = '/work/evidence/test-reports.jsonl'
    test_code = pixi('pytest', ['pytest', '-p', 'collection_plugin', '-vv', '--junitxml=/work/evidence/baseline.xml',
                              '--durations=50', 'tests'], accepted=(0, 1))
    assert json.loads((E/'executed-collection.json').read_text()) == ids
    reports = [json.loads(line) for line in (E/'test-reports.jsonl').read_text().splitlines()]
    finished = {r['nodeid'] for r in reports if r['when'] == 'teardown'}
    assert finished == set(ids), 'INCOMPLETE_TEST_RESULTS'
    doc = ET.parse(E/'baseline.xml')
    cases = doc.findall('.//testcase'); assert cases
    rows = [{'name': c.attrib.get('name'), 'classname': c.attrib.get('classname'),
             'time': c.attrib.get('time'), 'status': 'failure' if c.find('failure') is not None
             else 'error' if c.find('error') is not None else 'skip' if c.find('skipped') is not None else 'pass'} for c in cases]
    save('baseline-test-results.json', {'pytest_exit': test_code, 'precommit_exit': code,
         'pylint_exit': lint, 'tracked_autofixes': diff.returncode != 0, 'cases': rows,
         'collected_count': len(ids), 'completed_count': len(finished),
         'classification': 'MEASURED_BASELINE_REVIEW_REQUIRED_NOT_CANDIDATE_PASS'})
    verify_source()



def science_measurement():
    pixi('science-notebook', ['python','/packet/notebook_measure.py'])
    assert json.loads((E/'science-full-results.json').read_text())['fiveFullFits']
    verify_source()


def full_site():
    quarto=(W/'tools/quarto-path.txt').read_text()
    os.environ['PATH']=str(Path(quarto).parent)+os.pathsep+os.environ['PATH']
    pixi('full-quartodoc',['python','-m','quartodoc','build','--verbose'],cwd=SRC/'docs')
    pixi('full-interlinks',['python','-m','quartodoc','interlinks'],cwd=SRC/'docs')
    pixi('full-site',[quarto,'render'],cwd=SRC/'docs')
    pixi('full-inventory',['python','-m','sphobjinv','convert','-o','zlib','objects.txt','objects.inv'],cwd=SRC/'docs')
    site=SRC/'docs/_site';shutil.copy2(SRC/'docs/objects.inv',site/'objects.inv')
    assert (site/'index.html').is_file() and (site/'objects.inv').stat().st_size>0
    # Inspect all locally generated page/resource links. External URLs are reported, never silently fetched.
    from html.parser import HTMLParser
    from urllib.parse import urlsplit,unquote
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.links=[]
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src') and value:self.links.append(value)
    broken=[];external=set();data_uri_count=0;pages=list(site.rglob('*.html'));assert pages
    for page in pages:
        parser=Links();parser.feed(page.read_text(errors='strict'))
        for href in parser.links:
            u=urlsplit(href)
            if u.scheme=='data':data_uri_count+=1;continue
            if u.scheme or u.netloc:external.add(href);continue
            if not u.path:continue
            path=unquote(u.path)
            if path.startswith('/bambi/'):target=site/path[len('/bambi/'):]
            elif path.startswith('/'):target=site/path.lstrip('/')
            else:target=page.parent/path
            target=target.resolve()
            if not target.is_relative_to(site.resolve()) or not target.exists():
                broken.append({'page':str(page.relative_to(site)),'href':href})
    save('full-site-links.json',{'pages':len(pages),'brokenLocalResources':broken,'externalNotFetched':sorted(external),'dataURICount':data_uri_count,'anchorFragmentChecks':'NOT_CHECKED','baselineOnly':True})
    # Baseline link failures remain visible and cannot be called candidate success.
    save('full-site-summary.json',{'fullRenderCompleted':True,'pages':len(pages),'brokenLocalCount':len(broken),'explicitNotebookSemantics':json.loads((E/'quarto-actual-semantics.json').read_text()),'existingNotebookExecution':'UnmodifiedIPYNBstoredoutputs peractualbinary; QMDdefault execution. No existingnotebook source changed; affectedexistingnotebook execution set is empty for baseline.'})
    call('full-site-tracked-diff',['git','diff','--stat'])
    call('full-site-restore-tracked',['git','checkout','--','.'])
    verify_source()


def freeze():
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
    current = sum(p.stat().st_size for p in E.rglob('*') if p.is_file())
    cache_cap = max(0, min(40*1024**2, 64*1024**2-current-1024**2))
    if cache_cap == 0:
        save('cache-truncated.json', {'partial':True,'reason':'remaining evidence budget','cache_cap':0})
        return
    with tarfile.open(E/'bounded-cache.tar.gz', 'w:gz', compresslevel=1) as tar:
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
                if total+path.stat().st_size > cache_cap:
                    save('cache-truncated.json', {'partial': True, 'reason': 'dynamic remaining evidence cache cap','cache_cap':cache_cap,'current_evidence':current})
                    return
                tar.add(path, arcname=name+'/'+relative.as_posix(), recursive=False)
                total += path.stat().st_size


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
                 'prepare': prepare, 'science': science_measurement, 'baseline': baseline, 'full-site':full_site, 'freeze': freeze}
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
