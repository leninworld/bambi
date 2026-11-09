"""Full Bambi819 validation; reused M0 helpers and explicitly unrun I1 adaptations."""
import ast
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
PLAN = json.loads((P / 'plan.json').read_text())
JOB = os.environ['BAMBI_JOB']
assert JOB in PLAN['jobs']


def save(name, value):
    (E / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def metrics():
    root = Path('/sys/fs/cgroup')
    return {**{n: (root / n).read_text() for n in (
        'memory.current', 'memory.peak', 'memory.events', 'memory.max',
        'pids.current', 'pids.peak', 'pids.events', 'pids.max', 'cpu.stat')},
        'affinity': sorted(os.sched_getaffinity(0)),
        'shm': shutil.disk_usage('/dev/shm')._asdict()}


def package_list(raw):
    """Read complete pixi package JSON without treating diagnostic lines as packages."""
    values = []
    for i, char in enumerate(raw):
        if char != '[':
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(raw, i)
        except ValueError:
            continue
        if isinstance(value, list) and value and all(
                isinstance(row, dict) and 'name' in row and 'version' in row for row in value):
            values.append(value)
    assert len(values) == 1, 'PACKAGE_IDENTITY_NOT_UNIQUE'
    return values[0]


def call(label, args, accepted=(0,), cwd=SRC):
    print('COMMAND', label, json.dumps(args), flush=True)
    started = time.monotonic()
    used = sum(p.stat().st_size for p in E.rglob('*') if p.is_file())
    cap = min(16 * 1024**2, 64 * 1024**2-used)
    assert cap > 0, 'INNER_EVIDENCE_CAP'
    result = {}
    remaining = float(os.environ['M0_TARGET_EPOCH'])-time.time()
    seconds = min(remaining, 2100 if label in ('pytest', 'numba-tests') else 850 if label == 'pixi-install'
                  else 400 if label == 'focused'
                  else 300 if label in ('quartodoc', 'hook-install', 'unpatched') else 180)
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


def verify_source():
    manifest = E/'candidate-source-manifest.json'
    rows = json.loads(manifest.read_text()) if manifest.exists() else json.loads((P/'source-manifest.json').read_text())
    assert len(rows) in (275, 277)
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
    # Changed comparisons fail closed before downloading scientific dependencies.
    assert ast.dump(ast.parse('x=1')) == ast.dump(ast.parse('x = 1\n'))
    assert ast.dump(ast.parse('x=1')) != ast.dump(ast.parse('x=2'))
    nb = {'cells': [{'cell_type': 'code', 'id': 'fixture', 'source': ['x=1\n']}]}
    executed = json.loads(json.dumps(nb)); executed['cells'][0]['outputs'] = [{'text': '1'}]
    assert notebook_sources(nb) == notebook_sources(executed)
    executed['cells'][0]['source'] = 'x=2'
    assert notebook_sources(nb) != notebook_sources(executed)
    complete = [{'nodeid': 'synthetic::test_one', 'when': 'teardown'}]
    assert_complete_reports(['synthetic::test_one'], complete)
    for ids, rows in ((['synthetic::test_one'], []),
                      (['synthetic::test_one']*2, complete)):
        try:
            assert_complete_reports(ids, rows)
            raise RuntimeError('incomplete or duplicate evidence accepted')
        except AssertionError:
            pass
    packages = [{'name': 'synthetic', 'version': '1'}]
    raw = json.dumps(packages)
    assert package_list(raw) == packages
    assert package_list('[warning] synthetic diagnostic\n'+raw) == packages
    try:
        package_list(raw+'\n'+raw)
        raise RuntimeError('ambiguous package identity accepted')
    except AssertionError as exc:
        assert str(exc) == 'PACKAGE_IDENTITY_NOT_UNIQUE'
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
    shutil.copy2(P/'baseline-pixi.lock', SRC/'pixi.lock')
    assert hashlib.sha256((SRC/'pixi.lock').read_bytes()).hexdigest() == json.loads((P/'plan.json').read_text())['baseline_lock_sha256']
    call('pixi-install', [str(PIXI), 'install', '--frozen', '--environment', ENV])
    assert (SRC / 'pixi.lock').is_file()
    shutil.copy2(SRC / 'pixi.lock', E / 'pixi.lock')
    call('pixi-list', [str(PIXI), 'list', '--environment', ENV, '--json'])
    try:
        list_is_json = isinstance(json.loads((E/'pixi-list.log').read_text()), list)
    except (ValueError, TypeError):
        list_is_json = False
    save('pixi-list-json-status.json', {'pixi_list_json': list_is_json,
                                      'record_only': True, 'raw_log': 'pixi-list.log'})
    shutil.copy2(E / 'pixi-list.log', E / 'pixi-list.json')
    current = {r['name']: r for r in package_list((E/'pixi-list.log').read_text())
               if r['name'] != 'bambi'}
    original = {r['name']: r for r in package_list((P/'baseline-pixi-list.json').read_text())
                if r['name'] != 'bambi'}
    assert current == original, 'FROZEN_THIRD_PARTY_ENVIRONMENT_DRIFT'
    save('frozen-environment-comparison.json', {'all_third_party_records_equal': True,
         'packages': len(current), 'bambi_scm_metadata_reported_separately': True})
    save('lock-identity.json', {'sha256': hashlib.sha256((SRC/'pixi.lock').read_bytes()).hexdigest(),
         'environment_list_sha256': hashlib.sha256((E/'pixi-list.json').read_bytes()).hexdigest(),
         'environment': ENV, 'source': plan['source']})
    verify_source()


"""Candidate phases appended to the reused native controller; not executed locally."""


def apply_overlay(tests_only=False):
    rows = json.loads((P / 'overlay-manifest.json').read_text())
    assert {r['path'] for r in rows} == set(PLAN['overlay_paths'])
    for row in rows:
        if tests_only and row['path'] != 'tests/test_interpret_priors.py':
            continue
        data = (P / 'overlay' / row['path']).read_bytes()
        assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']
        target = SRC / row['path']
        assert target.resolve().is_relative_to(SRC) and not target.is_symlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)


def observer(label):
    os.environ['M0_COLLECTION_OUTPUT'] = str(E / (label + '-collection.json'))
    os.environ['M0_REPORT_OUTPUT'] = str(E / (label + '-reports.jsonl'))


def assert_complete_reports(ids, rows):
    assert len(ids) == len(set(ids))
    assert {r['nodeid'] for r in rows if r['when'] == 'teardown'} == set(ids)


def verify_complete_reports(label):
    ids = json.loads((E / (label + '-collection.json')).read_text())
    rows = [json.loads(s) for s in (E / (label + '-reports.jsonl')).read_text().splitlines()]
    assert_complete_reports(ids, rows)
    return ids


def prepare():
    pixi('imports', ['python', '/packet/smoke.py'])
    pixi('hook-install', ['pre-commit', 'install-hooks'])
    if JOB == 'docs':
        pixi('kernel-install', ['python', '-m', 'ipykernel', 'install', '--user',
                                '--name', 'bambi-candidate-py312'])
    tiny = W / 'observer-fixture'
    tiny.mkdir()
    (tiny / 'test_synthetic.py').write_text(
        'import pytest\ndef test_pass(): assert True\ndef test_fail(): assert False\n'
        '@pytest.mark.skip(reason="synthetic skip")\ndef test_skip(): pass\n'
    )
    observer('fixture')
    assert pixi('observer-fixture', ['pytest', '-p', 'collection_plugin', '-q', str(tiny)],
                accepted=(1,)) == 1
    assert len(verify_complete_reports('fixture')) == 3
    reports = [json.loads(s) for s in (E / 'fixture-reports.jsonl').read_text().splitlines()]
    calls = {r['nodeid'].split('::')[-1]: r['outcome'] for r in reports if r['when'] == 'call'}
    assert calls == {'test_pass': 'passed', 'test_fail': 'failed'}
    assert any(r['nodeid'].endswith('::test_skip') and r['when'] == 'setup'
               and r['outcome'] == 'skipped' for r in reports)
    # Exercise this same completion checker against genuinely incomplete reports.
    try:
        assert_complete_reports(['pass', 'missing'], [{'nodeid': 'pass', 'when': 'teardown'}])
    except AssertionError:
        pass
    else:
        raise AssertionError('Incomplete observer results were accepted')
    pixi('notebook-guard-fixture', ['python', '/packet/notebook_guard_fixture.py'])
    verify_source()


def regression():
    assert JOB == 'full'
    apply_overlay(tests_only=True)
    observer('unpatched')
    pixi('unpatched', ['pytest', '-p', 'collection_plugin', '-vv',
        '--junitxml=/work/evidence/unpatched.xml', 'tests/test_interpret_priors.py'], accepted=(1,))
    ids = verify_complete_reports('unpatched')
    assert ids and all(i.startswith('tests/test_interpret_priors.py::') for i in ids)
    cases = ET.parse(E / 'unpatched.xml').findall('.//testcase')
    controls = set(PLAN['preservation_controls'])
    assert len(cases) == len(ids) and controls
    failures = []
    passed = []
    for case in cases:
        assert case.find('error') is None and case.find('skipped') is None
        name = case.attrib['name']
        failure = case.find('failure')
        if name in controls:
            assert failure is None, name
            passed.append(name)
        else:
            assert failure is not None, name
            text = ET.tostring(failure, encoding='unicode')
            assert ('TypeError' in text and 'sample_group' in text) or (
                'KeyError' in text and 'posterior' in text
            ), text[-2000:]
            failures.append(name)
    assert set(passed) == controls and len(failures) == len(ids) - len(controls)
    save('regression-proof.json', {'missing_behavior_failures': failures,
         'preservation_controls_passed': passed, 'new_module_nodeids': ids})
    verify_source()


def cell_sources(nb):
    return [(c['cell_type'], ''.join(c['source']), c.get('id')) for c in nb['cells']]


def verify_notebook_normalization(before_nb, after_nb):
    assert len(before_nb['cells']) == len(after_nb['cells'])
    for old, new in zip(before_nb['cells'], after_nb['cells']):
        assert old['cell_type'] == new['cell_type'] and old.get('id') == new.get('id')
        if old['cell_type'] == 'code':
            assert ast.dump(ast.parse(''.join(old['source']))) == ast.dump(ast.parse(''.join(new['source'])))
        else:
            assert old == new


def source_manifest():
    rows = {r['path']: r for r in json.loads((P / 'source-manifest.json').read_text())}
    for item in json.loads((P / 'overlay-manifest.json').read_text()):
        path = item['path']
        data = (SRC / path).read_bytes()
        rows[path] = {'path': path, 'bytes': len(data), 'mode': '100644',
                     'sha256': hashlib.sha256(data).hexdigest(),
                     'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
        output = E / 'tested-files' / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    save('candidate-source-manifest.json', list(rows.values()))
    verify_source()


def candidate():
    apply_overlay()
    pyfiles = [p for p in PLAN['overlay_paths'] if p.endswith('.py')]
    before = {f: ast.dump(ast.parse((SRC / f).read_text()), include_attributes=False) for f in pyfiles}
    formatters = sorted(Path(os.environ['PRE_COMMIT_HOME']).glob('repo*/py_env-*/bin/black'))
    assert formatters
    for number, formatter in enumerate(formatters):
        call('hook-black-version-' + str(number), [str(formatter), '--version'])
        assert '26.5.1' in (E / ('hook-black-version-' + str(number) + '.log')).read_text()
    call('format-approved-python', [str(formatters[0]), *pyfiles])
    assert all(ast.dump(ast.parse((SRC / f).read_text()), include_attributes=False) == before[f]
               for f in pyfiles), 'FORMAT_CHANGED_SEMANTICS'
    # Normalize code-cell whitespace BEFORE execution; bind its AST and markdown.
    # This authoring transform needs explicit confirmation in the whole packet review.
    notebook = SRC / 'docs/notebooks/prior_predictive_interpret.ipynb'
    before_nb = json.loads(notebook.read_text())
    jupyter_black = []
    for number, formatter in enumerate(formatters):
        result = {}
        command([str(formatter), '--check', '--line-length=88', '--ipynb', str(notebook)], 30, 1024 ** 2,
                expected=(0, 1, 123), cwd=SRC, log=E / ('notebook-probe-' + str(number) + '.log'), result=result)
        text = (E / ('notebook-probe-' + str(number) + '.log')).read_text()
        if 'Skipping .ipynb' not in text and result['returncode'] in (0, 1):
            jupyter_black.append(formatter)
    assert jupyter_black, 'REAL_BLACK_JUPYTER_REQUIRED'
    call('format-approved-notebook', [str(jupyter_black[0]), '--line-length=88', '--ipynb', str(notebook)])
    after_nb = json.loads(notebook.read_text())
    verify_notebook_normalization(before_nb, after_nb)
    save('normalized-notebook-sources.json', cell_sources(after_nb))
    call('stage-new-and-changed-files', ['git', 'add', '--', *PLAN['overlay_paths']])
    pixi('syntax', ['python', '-m', 'py_compile', *pyfiles])
    pixi('precommit', ['pre-commit', 'run', '--all-files'])
    pixi('pylint', ['pylint', 'bambi'])
    assert cell_sources(json.loads(notebook.read_text())) == cell_sources(after_nb)
    if JOB != 'docs':
        observer('focused')
        args = ['pytest', '-p', 'collection_plugin', '-vv',
                '--junitxml=/work/evidence/focused.xml', 'tests/test_interpret_priors.py']
        if JOB == 'numba':
            args += ['-k', 'prior_native_prediction or prior_groups_and_omitted_offsets']
        pixi('focused', args)
        focused_ids = verify_complete_reports('focused')
        assert all(i.startswith('tests/test_interpret_priors.py::') for i in focused_ids)
        if JOB == 'full':
            assert set(focused_ids) == set(json.loads((E / 'regression-proof.json').read_text())['new_module_nodeids'])
    source_manifest()
    save('format-normalization.json', {'python_ast_preserved': True,
         'notebook_python_ast_and_markdown_preserved': True,
         'normalized_cell_sources_bound_before_execution': True})


def baseline():
    assert JOB == 'full'
    observer('full')
    pixi('collection', ['pytest', '-p', 'collection_plugin', '--collect-only', '-q', 'tests'])
    collected = json.loads((E / 'full-collection.json').read_text())
    old = set(json.loads((P / 'baseline-nodeids.json').read_text()))
    new = set(json.loads((E / 'regression-proof.json').read_text())['new_module_nodeids'])
    assert set(collected) == old | new and len(collected) == len(old) + len(new)
    assert float(os.environ['M0_TARGET_EPOCH']) - time.time() >= 2160
    os.environ['M0_COLLECTION_OUTPUT'] = str(E / 'full-executed-collection.json')
    pixi('pytest', ['pytest', '-p', 'collection_plugin', '-vv',
        '--junitxml=/work/evidence/candidate.xml', '--durations=50', 'tests'])
    ids = verify_complete_reports('full')
    assert json.loads((E / 'full-executed-collection.json').read_text()) == ids
    cases = ET.parse(E / 'candidate.xml').findall('.//testcase')
    assert len(cases) == len(collected)
    assert not any(c.find('failure') is not None or c.find('error') is not None for c in cases)
    def skips(path):
        return {(c.attrib['classname'], c.attrib['name']): c.find('skipped').attrib.get('message')
                for c in ET.parse(path).findall('.//testcase') if c.find('skipped') is not None}
    assert skips(E / 'candidate.xml') == skips(P / 'baseline-results.xml')
    verify_source()
    save('candidate-results.json', {'passed': len(cases) - len(skips(E / 'candidate.xml')),
         'baseline_nodeids_preserved': len(old), 'new_module_cases': len(new),
         'same_baseline_skips': list(skips(E / 'candidate.xml').values()),
         'all_nodeids_finished': len(ids), 'candidate_full_suite': True})


def numba_tests():
    assert JOB == 'numba'
    assert json.loads((E / 'smoke-prepare.json').read_text())['linker'] == 'numba'
    observer('numba')
    pixi('numba-tests', ['pytest', '-p', 'collection_plugin', '-vv',
        '--junitxml=/work/evidence/numba.xml', '--durations=30',
        'tests/test_interpret_priors.py', 'tests/test_interpret_plots.py'])
    ids = verify_complete_reports('numba')
    cases = ET.parse(E / 'numba.xml').findall('.//testcase')
    assert len(ids) == len(cases)
    assert not any(c.find('failure') is not None or c.find('error') is not None
                   or c.find('skipped') is not None for c in cases)
    old = [i for i in json.loads((P / 'baseline-nodeids.json').read_text())
           if i.startswith('tests/test_interpret_plots.py::')]
    assert set(old).issubset(ids)
    save('numba-results.json', {'linker': 'numba', 'all_completed': len(ids),
         'existing_interpret_nodes_preserved': len(old), 'new_prior_module': True})
    verify_source()


def docs():
    assert JOB == 'docs'
    notebook = SRC / 'docs/notebooks/prior_predictive_interpret.ipynb'
    frozen = json.loads((E / 'normalized-notebook-sources.json').read_text())
    pixi('execute-example', ['python', '/packet/execute_example.py'])
    assert json.loads(json.dumps(cell_sources(json.loads(notebook.read_text())))) == frozen
    call('stage-executed-notebook', ['git', 'add', '--', str(notebook.relative_to(SRC))])
    pixi('notebook-check', ['pre-commit', 'run', '--files', str(notebook.relative_to(SRC))])
    assert json.loads(notebook.read_text())['metadata']['kernelspec']['name'] == 'python3'
    assert json.loads(json.dumps(cell_sources(json.loads(notebook.read_text())))) == frozen
    quarto = (W / 'tools/quarto-path.txt').read_text()
    os.environ['PATH'] = str(Path(quarto).parent) + os.pathsep + os.environ['PATH']
    pixi('quartodoc', ['python', '-m', 'quartodoc', 'build', '--verbose'], cwd=SRC / 'docs')
    generated = list((SRC / 'docs/api').rglob('*.qmd'))
    model_pages = [page for page in generated
                   if page.stem == 'Model' and 'sample_group' in page.read_text()]
    assert len(model_pages) == 1, 'MODEL_API_PAGE_NOT_UNIQUE'
    pages = model_pages[:]
    names = ['predictions', 'comparisons', 'slopes', 'plot_predictions', 'plot_comparisons', 'plot_slopes']
    for name in names:
        matches = [page for page in generated
                   if page.stem.split('.')[-1] == name and 'sample_group' in page.read_text()]
        assert len(matches) == 1, name + ': GENERATED_API_PAGE_NOT_UNIQUE'
        pages.append(matches[0])
    pixi('interlinks', ['python', '-m', 'quartodoc', 'interlinks'], cwd=SRC / 'docs')
    for number, page in enumerate(pages):
        pixi('render-api-' + str(number), [quarto, 'render', str(page.relative_to(SRC / 'docs'))], cwd=SRC / 'docs')
    pixi('render-example', [quarto, 'render', str(notebook.relative_to(SRC / 'docs'))], cwd=SRC / 'docs')
    call('restore-generated-changelog', ['git', 'checkout', 'HEAD', '--', 'docs/changelog.qmd'])
    source_manifest()
    call('tested-patch', ['git', 'diff', '--binary', 'HEAD', '--', *PLAN['overlay_paths']])
    save('docs-results.json', {'api_pages': [str(x.relative_to(SRC)) for x in pages],
         'notebook_executed': True, 'normalized_cell_sources_exact': True})


def freeze():
    save('final-kernel-metrics.json', metrics())
    verify_source()
    source_manifest()


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
                 'prepare': prepare, 'regression': regression, 'candidate': candidate, 'docs': docs,
                 'baseline': baseline, 'numba-tests': numba_tests, 'freeze': freeze}
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
