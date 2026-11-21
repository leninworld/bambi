"""Bambi #558 candidate attempt 4 on 93bc3ad4, inside one owned container phase.

Phases: helper-fixture (no source), install, prepare (pristine baseline probe, candidate overlay, early
checks, mutants), full (full suite or docs baseline/candidate comparison), freeze.
G0 is closed at 2/2; candidate attempts 1 (failed), 2 (passed) and 3 (harness bytecode defect, no project
code ran) are consumed; this is attempt 4.
Bytecode: this helper and every packet helper it starts run with `python -B` and PYTHONDONTWRITEBYTECODE=1
(/packet is also a read-only mount). Project commands (pytest, pylint, pre-commit, quartodoc, quarto) keep
the attempt-2 environment: the variable is removed here and set only around helper invocations.
"""
import sys
if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
sys.dont_write_bytecode = True
if __name__ == '__main__' and not sys.flags.dont_write_bytecode:
    raise SystemExit("BYTECODE_FLAG_REQUIRED")
import collections
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
import threading
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
HELPER_ENV_AT_START = os.environ.pop('PYTHONDONTWRITEBYTECODE', None)  # helpers only, see helper()
from bounded import (ByteBudgetWriter, command, dumps_strict, identity_report, save as _save, scan_tree,
                     stage_upload, strict_loads)

P = Path('/packet')
W = Path('/work')
E = W / 'evidence'
E.mkdir(exist_ok=True)
SRC = W / 'src'
SRC_BASE = W / 'src-base'
PIXI = W / 'tools/pixi'
ENV = os.environ['G0_ENV']
ROLE = os.environ['G0_ROLE']
LINKER = os.environ['G0_LINKER']
LANE = os.environ['G0_LANE']
PLAN = strict_loads((P/'plan.json').read_text())
SPEC = PLAN['lanes'][LANE]
WORK_END = float(os.environ['M0_TARGET_EPOCH'])
OLD_SKIP = 'tests/test_model_construction.py::test_init_fallback'
TEST_FILE = 'tests/test_model_construction.py'
if ENV not in ('py312', 'py313', 'dev') or ROLE not in ('test', 'docs') or LINKER not in ('cvm', 'numba'):
    raise SystemExit('LANE_SPEC_INVALID')


class CheckFailure(Exception):
    """An explicit gate (independent of asserts) failed."""


def need(condition, message):
    if not condition:
        raise CheckFailure(message)


def save(name, value):
    _save(E / name, value)


def metrics():
    root = Path('/sys/fs/cgroup')
    return {**{n: (root / n).read_text() for n in (
        'memory.current', 'memory.peak', 'memory.events', 'memory.max',
        'pids.current', 'pids.peak', 'pids.events', 'pids.max', 'cpu.stat')},
        'affinity': sorted(os.sched_getaffinity(0)),
        'shm': shutil.disk_usage('/dev/shm')._asdict()}


def call(label, args, accepted=(0,), cwd=SRC):
    print('COMMAND', label, dumps_strict(args), flush=True)
    need(not (E / (label + '.log')).exists(), 'LOG_LABEL_REUSED ' + label)
    started = time.monotonic()
    used = scan_tree(E, 4000, 64 * 1024**2, WORK_END)['bytes']
    cap = min(16 * 1024**2, 64 * 1024**2 - used)
    need(cap > 0, 'INNER_EVIDENCE_CAP')
    result = {}
    seconds = (SPEC.get('pytest_seconds', 1800) if label == 'pytest' else 850 if label.startswith('pixi-install')
               else 600 if label.endswith('docs-render') else 300 if label in ('quartodoc', 'hook-install', 'base-quartodoc')
               else 180)
    command(args, seconds, cap, expected=accepted, cwd=cwd, log=E / (label+'.log'), result=result,
            deadline=WORK_END)
    code = result['returncode']
    size = (E / (label + '.log')).stat().st_size
    print('RESULT', label, code, 'seconds', time.monotonic()-started, 'log_bytes', size, flush=True)
    need(size <= cap, 'BOUNDED_PHASE_LOG')
    scan_tree(E, 4000, 64 * 1024**2, WORK_END)  # post-exit recursive evidence bound
    return code


def pixi(label, args, accepted=(0,), cwd=SRC):
    return call(label, [str(PIXI), 'run', '--frozen', '--environment', ENV, *args], accepted, cwd)


def helper(label, script, *args):
    """A packet helper under the lane environment: `python -B` plus PYTHONDONTWRITEBYTECODE=1, for it alone."""
    need(re.fullmatch(r'[a-z_]+\.py', script) is not None, 'HELPER_NAME ' + script)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        return pixi(label, ['python', '-B', '/packet/' + script, *args])
    finally:
        os.environ.pop('PYTHONDONTWRITEBYTECODE', None)


def verify_rows(root, rows):
    for r in rows:
        f = root / r['path']
        b = os.readlink(f).encode() if r['mode'] == '120000' else f.read_bytes()
        need(len(b) == r['bytes'] and hashlib.sha256(b).hexdigest() == r['sha256'], 'SOURCE_BYTES ' + r['path'])
        need(hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest() == r['git_blob'], 'SOURCE_BLOB ' + r['path'])
        if r['mode'] != '120000':
            need(bool(f.stat().st_mode & 0o111) == (r['mode'] == '100755'), 'SOURCE_MODE ' + r['path'])


def verify_source(root=SRC, candidate=None):
    if candidate is None:
        candidate = root == SRC and (W / 'candidate-applied').exists()
    rows = strict_loads((P / ('candidate-source-manifest.json' if candidate else 'source-manifest.json')).read_text())
    need(len(rows) == PLAN['source_file_count'], 'SOURCE_COUNT')
    verify_rows(root, rows)


def extract(archive, destination):
    destination.mkdir(exist_ok=False)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        need(len(members) <= 12000 and sum(m.size for m in members) <= 2 * 1024**3, 'ARCHIVE_BOUNDS')
        for member in members:
            need(not Path(member.name).is_absolute() and '..' not in Path(member.name).parts, 'ARCHIVE_PATH')
            need(member.isfile() or member.isdir() or member.issym() or member.islnk(), 'ARCHIVE_TYPE')
            tarfile.data_filter(member, str(destination))
        tar.extractall(destination, members=members, filter='data')


def download(spec, destination, fixture=False):
    url = spec['browser_download_url']
    need((fixture and url.startswith('http://127.0.0.1:')) or url.startswith((
        'https://github.com/prefix-dev/pixi/releases/download/v0.62.2/',
        'https://github.com/quarto-dev/quarto-cli/releases/download/v1.8.26/')), 'TOOL_URL')
    request = urllib.request.Request(url, headers={'User-Agent': 'bambi-native-baseline',
                                                 'Accept': 'application/octet-stream'})
    digest = hashlib.sha256()
    size = 0
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=30) as response, destination.open('xb') as out:
        need(response.status == 200, 'TOOL_HTTP_STATUS')
        while True:
            need(time.monotonic()-start < 180 and time.time() < WORK_END, 'TOOL_DOWNLOAD_TIME')
            b = response.read(65536)
            if not b:
                break
            size += len(b)
            need(size <= spec['size'], 'TOOL_DOWNLOAD_SIZE')
            digest.update(b)
            out.write(b)
    need(size == spec['size'] and 'sha256:'+digest.hexdigest() == spec['digest'], 'TOOL_DIGEST')
    save(destination.name+'.identity.json', {'bytes': size, 'sha256': digest.hexdigest(), 'url': url})


def expect_failure(function, exceptions, label):
    try:
        function()
    except exceptions:
        return
    raise CheckFailure('FIXTURE_ACCEPTED_' + label)


def helper_fixture():
    # Source verifier: file, mode, size, SHA256 and Git-blob branches.
    root = W/'source-fixture'; root.mkdir()
    (root/'value').write_bytes(b'abc'); (root/'value').chmod(0o644)
    row = {'path': 'value', 'mode': '100644', 'bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest(),
           'git_blob': hashlib.sha1(b'blob 3\0abc').hexdigest()}
    verify_rows(root, [row])
    for field, bad in [('bytes', 4), ('sha256', '0'*64), ('git_blob', '0'*40), ('mode', '100755')]:
        expect_failure(lambda: verify_rows(root, [{**row, field: bad}]), CheckFailure, 'source-' + field)
    overlay = PLAN['overlay'][0]
    for suffix, corrupt in (('valid', False), ('wrong-original', True)):
        root = W/('overlay-fixture-'+suffix)
        target = root/overlay['path']; target.parent.mkdir(parents=True)
        target.write_bytes((P/'original-test_model_construction.py').read_bytes() + (b'corrupt' if corrupt else b''))
        rejected = False
        try:
            copy_overlay(root, [overlay])
        except CheckFailure:
            rejected = True
        need(rejected == corrupt, 'OVERLAY_FIXTURE_' + suffix)
        if not corrupt:
            verify_rows(root, [overlay['candidate']])
    sink = io.BytesIO(); writer = ByteBudgetWriter(sink, 3)
    need(writer.write(b'abc') == 3, 'BUDGET_WRITER')
    expect_failure(lambda: writer.write(b'x'), OverflowError, 'archive-cap')
    evidence = W/'upload-fixture'; (evidence/'nested').mkdir(parents=True)
    (evidence/'summary.json').write_text('{}')
    need(not stage_upload(evidence, W/'upload-valid', 128*1024)['cap_hit'], 'UPLOAD_VALID')
    (evidence/'nested/too-large.log').write_bytes(b'x'*200000)
    bad = stage_upload(evidence, W/'upload-capped', 128*1024)
    need(bad['cap_hit'] and not (W/'upload-capped/nested/too-large.log').exists(), 'UPLOAD_NESTED_CAP')
    # Multiset identities in the real image interpreter.
    rep = identity_report(['a', 'a', 'b'], [{'nodeid': 'a', 'when': 'teardown', 'outcome': 'passed'},
                                            {'nodeid': 'b', 'when': 'teardown', 'outcome': 'passed'}])
    need(rep['duplicates'] == {'a': 2} and not rep['complete'], 'IDENTITY_MULTISET')
    # Real tar structures through the production extractor.
    for name, member_name, target, must_fail in (('valid', 'nested/value', None, False),
                                                 ('traversal', '../escape', None, True),
                                                 ('link-escape', 'escape', '../../outside', True)):
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
        except (tarfile.FilterError, CheckFailure):
            failed = True
        need(failed == must_fail, 'TAR_FIXTURE_' + name)
    need((W / 'fixture-valid/nested/value').read_bytes() == b'abc', 'TAR_VALID_BYTES')
    payload = b'public-synthetic-asset'

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            ok = (self.headers['User-Agent'] == 'bambi-native-baseline'
                  and self.headers['Accept'] == 'application/octet-stream')
            code = 404 if self.path == '/missing' or not ok else 200
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
            ('missing', '/missing', len(payload), hashlib.sha256(payload).hexdigest(), True)):
            spec = {'browser_download_url': 'http://127.0.0.1:'+str(server.server_port)+suffix,
                    'size': size, 'digest': 'sha256:'+digest}
            failed = False
            try:
                download(spec, W/('http-fixture-'+name), fixture=True)
            except (CheckFailure, urllib.error.HTTPError):
                failed = True
            need(failed == fail, 'HTTP_FIXTURE_' + name)
    finally:
        server.shutdown(); worker.join(timeout=2); server.server_close()
    need(not worker.is_alive(), 'HTTP_FIXTURE_THREAD')
    native = W / 'fixture-native-true'
    native.write_bytes(Path('/bin/true').read_bytes()); native.chmod(0o755)
    call('same-work-native-exec', [str(native)], cwd=W)
    need(call('helper-accepted-exit', [sys.executable, '-c', 'import sys;sys.exit(3)'], accepted=(0, 3), cwd=W) == 3,
         'ACCEPTED_EXIT')
    try:
        call('helper-rejected-exit', [sys.executable, '-c', 'import sys;sys.exit(4)'], cwd=W)
        raise CheckFailure('NONZERO_EXIT_ACCEPTED')
    except RuntimeError as exc:
        need('command exit 4,' in str(exc), 'REJECTED_EXIT_MESSAGE')
    # Fast-exiting oversized producer and orphaned grandchild under the container's --init reaper.
    expect_failure(lambda: command([sys.executable, '-c', "print('y'*5000)"], 5, 100), OverflowError, 'fast-oversize')
    pidfile = W / 'grandchild.pid'
    expect_failure(lambda: command([sys.executable, '-c',
        "import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(60)"], 1.5, 1000), TimeoutError, 'timeout')
    time.sleep(.5)
    gone = False
    try:
        os.kill(int(pidfile.read_text()), 0)
    except ProcessLookupError:
        gone = True
    need(gone, 'GRANDCHILD_SURVIVED_OR_ZOMBIE')
    # The optimisation guard in the real image interpreter.
    probe = subprocess.run([sys.executable, '-O', '-B', str(P / 'inside.py'), 'metrics'], capture_output=True, timeout=20)
    need(probe.returncode == 1 and b'OPTIMIZE_FORBIDDEN' in probe.stderr, 'OPTIMIZE_GUARD_INSIDE')
    for name in ('HOME', 'XDG_CACHE_HOME', 'MPLCONFIGDIR', 'NUMBA_CACHE_DIR', 'PRE_COMMIT_HOME', 'BAMBI_DATA',
                 'PIXI_CACHE_DIR'):
        path = Path(os.environ[name]); path.mkdir(parents=True, exist_ok=True)
        (path / 'fixture-writable').write_text('synthetic')
    for path in (Path('/work/pytensor-cache'), Path('/tmp')):
        path.mkdir(exist_ok=True)
        (path/'fixture-writable').write_text('synthetic')
    save('helper-fixture.json', {'status': 'PASS', 'actual_tar_cases': 3, 'actual_http_cases': 4,
                                 'grandchild_reaped': True, 'fast_oversize_rejected': True,
                                 'optimize_refused': True, 'same_work_native_exec': True})


def install_tree(root, label):
    need(not root.exists(), 'INSTALL_TREE_EXISTS')
    shutil.copytree('/source', root, symlinks=True)
    verify_source(root, candidate=False)
    git = lambda *a: subprocess.check_output(['git', *a], cwd=root, timeout=30)
    need(git('rev-parse', 'HEAD').decode().strip() == PLAN['source'], 'SOURCE_HEAD')
    need(git('rev-parse', 'HEAD^{tree}').decode().strip() == PLAN['source_tree'], 'SOURCE_TREE')
    need(git('status', '--porcelain') == b'', 'SOURCE_DIRTY')
    config = (root / '.git/config').read_text().lower()
    need('extraheader' not in config and 'credential' not in config, 'GIT_CREDENTIAL_MATERIAL')
    for path, blob in PLAN['unchanged_inputs'].items():
        data = (root / path).read_bytes()
        need(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest() == blob, 'UNCHANGED_INPUT ' + path)
    shutil.copy2(P / 'canonical-pixi.lock', root / 'pixi.lock')
    verify_rows(root, [PLAN['canonical_lock']])
    call(label, [str(PIXI), 'install', '--frozen', '--environment', ENV], cwd=root)
    verify_rows(root, [PLAN['canonical_lock']])
    verify_source(root, candidate=False)


def install():
    tools = W / 'tools'; tools.mkdir()
    for spec in strict_loads((P / 'tools.json').read_text()):
        if ROLE != 'docs' and spec['name'].startswith('quarto-'):
            continue
        archive = tools / spec['name']; download(spec, archive)
        dest = tools / ('unpacked-'+spec['tag']); extract(archive, dest)
        if spec['name'].startswith('pixi-'):
            candidates = list(dest.rglob('pixi')); need(len(candidates) == 1, 'PIXI_BINARY')
            shutil.copy2(candidates[0], PIXI)
        else:
            candidates = list(dest.rglob('bin/quarto')); need(len(candidates) == 1, 'QUARTO_BINARY')
            (tools / 'quarto-path.txt').write_text(str(candidates[0]))
    call('pixi-version', [str(PIXI), '--version'], cwd=W)
    if ROLE == 'docs':
        call('quarto-version', [(tools / 'quarto-path.txt').read_text(), '--version'], cwd=W)
    folder = Path(os.environ['BAMBI_DATA']); folder.mkdir(exist_ok=True)
    for _, row in strict_loads((P / 'datasets.json').read_text()).items():
        b = (P / 'datasets' / row['filename']).read_bytes()
        need(hashlib.sha256(b).hexdigest() == row['checksum'], 'DATASET ' + row['filename'])
        (folder / row['filename']).write_bytes(b)
    install_tree(SRC, 'pixi-install')
    if ROLE == 'docs':
        install_tree(SRC_BASE, 'pixi-install-base')  # second, cache-backed install for the docs baseline
    shutil.copy2(SRC / 'pixi.lock', E / 'pixi.lock')
    call('pixi-list', [str(PIXI), 'list', '--environment', ENV, '--json'])
    shutil.copy2(E / 'pixi-list.log', E / 'pixi-list.json')
    save('lock-identity.json', {'sha256': hashlib.sha256((SRC/'pixi.lock').read_bytes()).hexdigest(),
                                'environment_list_sha256': hashlib.sha256((E/'pixi-list.json').read_bytes()).hexdigest(),
                                'environment': ENV, 'source': PLAN['source']})


def copy_overlay(root, overlays):
    for overlay in overlays:
        target = root / overlay['path']
        need(not target.is_symlink(), 'OVERLAY_SYMLINK')
        verify_rows(root, [overlay['original']])
        blob = (P / overlay['packet_file']).read_bytes()
        candidate = overlay['candidate']
        need(len(blob) == candidate['bytes'] and hashlib.sha256(blob).hexdigest() == candidate['sha256'], 'OVERLAY_BLOB')
        target.write_bytes(blob)
        verify_rows(root, [candidate])


def apply_candidate():
    need(not (W/'candidate-applied').exists(), 'CANDIDATE_ALREADY_APPLIED')
    verify_source(SRC, candidate=False)
    copy_overlay(SRC, PLAN['overlay'])
    (W/'candidate-applied').write_text(PLAN['candidate_patch_sha256'])
    verify_source()
    actual = subprocess.check_output(['git', 'diff', '--full-index', '--no-color', '--no-ext-diff', PLAN['source'], '--',
                                      TEST_FILE], cwd=SRC, timeout=30)
    need(actual == (P/'candidate.patch').read_bytes(), 'APPLIED_PATCH_MISMATCH')
    save('applied-candidate.json', {'patch_sha256': hashlib.sha256(actual).hexdigest(), 'source': PLAN['source'],
                                    'production_change': False, 'files': PLAN['overlay']})


def run_pytest(label, selection, expected=(0,), extra=()):
    os.environ['M0_COLLECTION_OUTPUT'] = str(E/(label+'-collection.json'))
    os.environ['M0_REPORT_OUTPUT'] = str(E/(label+'-reports.jsonl'))
    code = pixi(label, ['pytest', '-p', 'collection_plugin', '-vv', '--junitxml='+str(E/(label+'.xml')), *extra,
                        *selection], accepted=expected)
    ids = strict_loads((E/(label+'-collection.json')).read_text())
    reports = [strict_loads(v) for v in (E/(label+'-reports.jsonl')).read_text().splitlines()]
    report = identity_report(ids, reports)
    cases = ET.parse(E/(label+'.xml')).findall('.//testcase')
    junit = collections.Counter((c.attrib.get('classname'), c.attrib.get('name')) for c in cases)
    report['junit_cases'] = len(cases)
    report['junit_duplicates'] = sum(v - 1 for v in junit.values() if v > 1)
    report['junit_errors'] = sum(1 for c in cases if c.find('error') is not None)
    report['junit_failures'] = sorted(c.attrib['name'] for c in cases if c.find('failure') is not None)
    report['exit'] = code
    return ids, report


def focused(label, keyword, count, expected=(0,), file=TEST_FILE):
    ids, report = run_pytest(label, [file, '-k', keyword], expected)
    need(report['complete'] and report['collected_occurrences'] == count == report['junit_cases'], label + '_COUNT')
    need(not report['skipped'] and report['junit_duplicates'] == 0 and report['junit_errors'] == 0, label + '_SKIP_OR_ERROR')
    need(bool(report['junit_failures']) == (report['exit'] == 1), label + '_EXIT_CONSISTENCY')
    save(label+'-result.json', {**report, 'collected': ids,
         'scope': 'PyMC raise_ok fault + real small fits; not numerical reproduction or parallel recovery'})
    return report['junit_failures'], ids


def baseline_probe():
    """Pristine 93bc3ad4 inventory in this lane, observed before the overlay."""
    verify_source(SRC, candidate=False)
    os.environ['M0_COLLECTION_OUTPUT'] = str(E/'baseline-collection.json')
    os.environ.pop('M0_REPORT_OUTPUT', None)
    pixi('baseline-collection', ['pytest', '-p', 'collection_plugin', '--collect-only', '-q', 'tests'])
    ids = strict_loads((E/'baseline-collection.json').read_text())
    _, skip = run_pytest('baseline-old-skip', [OLD_SKIP])
    expected = strict_loads((P/'expected-baseline-nodeids.json').read_text())
    counts = collections.Counter(ids)
    row = {'source': PLAN['source'], 'collected_occurrences': len(ids), 'unique': len(counts),
           'duplicates': {k: v for k, v in counts.items() if v > 1}, 'old_skip_present': counts[OLD_SKIP] == 1,
           'old_skip_outcomes': skip['outcomes'], 'old_skip_skipped': skip['skipped'] == [OLD_SKIP],
           'expectation': {'expected_count': expected['expected_count'], 'matches': sorted(counts) == expected['nodeids'],
                           'missing_vs_expected': sorted(set(expected['nodeids']) - set(counts))[:50],
                           'extra_vs_expected': sorted(set(counts) - set(expected['nodeids']))[:50],
                           'gate': False}}
    save('baseline-collection-result.json', row)
    need(ids and not row['duplicates'] and row['old_skip_present'] and row['old_skip_skipped'], 'BASELINE_INVENTORY')
    verify_source(SRC, candidate=False)


def precommit_black_version():
    config = (SRC/'.pre-commit-config.yaml').read_text()
    need('https://github.com/psf/black\n    rev: 25.9.0\n' in config, 'BLACK_PIN_NOT_25_9_0')
    found = sorted(Path(os.environ['PRE_COMMIT_HOME']).glob('*/py_env-*/bin/black'))
    need(len(found) == 1, 'PRECOMMIT_BLACK_NOT_UNIQUE')
    call('precommit-black-version', [str(found[0]), '--version'], cwd=W)
    need(b'black, 25.9.0' in (E/'precommit-black-version.log').read_bytes(), 'BLACK_VERSION_NOT_25_9_0')


def mutants():
    backend = SRC/'bambi/backend/pymc.py'
    original = backend.read_text()
    guard = 'if "ValueError: Mass matrix contains" in traceback.format_exc() and init == "auto":'
    retry = 'init="adapt_diag",'
    need(original.count(guard) == 1 and original.count(retry) == 1, 'MUTANT_ANCHORS')
    retry_start = original.index(retry)
    seed_position = original.index('random_seed=random_seed,', retry_start)
    scope = original[original.index('    def _run_mcmc('):original.index('    def _clean_results(')]
    need(scope.count('random_seed=random_seed,') == 2, 'MUTANT_SEED_ANCHOR')
    variants = {
        'disabled': original.replace(guard, 'if False:', 1),
        'wrong-init': original.replace(retry, 'init="auto",', 1),
        'lost-seed': original[:seed_position]+original[seed_position:].replace('random_seed=random_seed,', 'random_seed=1235,', 1),
        'unrelated-retry': original.replace(guard, 'if init == "auto":', 1),
        'explicit-retry': original.replace(guard, 'if "ValueError: Mass matrix contains" in traceback.format_exc():', 1),
        'duplicate-log': original.replace(guard, guard+'\n                    _log.info("The default initialization duplicate")', 1),
    }
    prefix = {'disabled': 'test_init_fallback[', 'wrong-init': 'test_init_fallback[', 'lost-seed': 'test_init_fallback[',
              'unrelated-retry': 'test_init_fallback_unrelated_error[',
              'explicit-retry': 'test_init_fallback_explicit_init[', 'duplicate-log': 'test_init_fallback['}
    rows = []
    for name, changed in variants.items():
        need(changed != original, 'MUTANT_NOOP ' + name)
        row = {'name': name, 'production_sha256': hashlib.sha256(changed.encode()).hexdigest(), 'restored': False}
        rows.append(row)
        try:
            backend.write_text(changed)
            row['failures'], _ = focused('mutation-'+name, 'init_fallback', 11, (1,))
            row['detected'] = any(v.startswith(prefix[name]) for v in row['failures'])
        finally:
            backend.write_text(original)
            verify_source()  # every tracked byte restored after every mutant, before the next one
            row['restored'] = True
            save('mutation-progress.json', rows)
        need(row['detected'], 'WRONG_MUTATION_FAILURE ' + name)
    need(len(rows) == 6 and all(r['detected'] and r['restored'] for r in rows), 'MUTANTS_INCOMPLETE')
    save('mutation-sensitivity.json', {'all_detected': True, 'mutants': rows, 'never_committed': True,
                                       'all_source_bytes_restored': True})


def prepare():
    helper('imports', 'smoke.py', 'imports')
    helper('dependency-diagnostic', 'diagnostic.py')
    if ROLE == 'test':
        baseline_probe()
    apply_candidate()
    # Early affected-behaviour checks in every lane, before any full suite or docs build.
    focused('early-fallback', 'init_fallback', 11)
    focused('early-hashseed', 'auxiliary_parameter_order_is_independent_of_hash_seed', 2)
    helper('affected-imports', 'affected_imports.py')
    pixi('file-lint', ['pylint', '--rcfile=/packet/lint-file.rc', TEST_FILE])
    pixi('hook-install', ['pre-commit', 'install-hooks'])
    precommit_black_version()
    pixi('candidate-black', ['pre-commit', 'run', 'black', '--files', TEST_FILE])
    verify_source()
    if ROLE == 'test':
        pixi('early-precommit', ['pre-commit', 'run', '--all'])
        verify_source()
        pixi('early-pylint', ['pylint', 'bambi'])
    tiny = W/'observer-fixture'; tiny.mkdir()
    (tiny/'test_synthetic.py').write_text('import pytest\n'
        'def test_pass(): assert True\n'
        'def test_fail(): assert False\n'
        '@pytest.mark.skip(reason="synthetic skip")\n'
        'def test_skip(): pass\n')
    _, fixture = run_pytest('observer-fixture', [str(tiny)], expected=(1,))
    need(fixture['complete'] and fixture['collected_occurrences'] == 3 and fixture['skipped'] and fixture['failed'],
         'OBSERVER_FIXTURE')
    if LANE == PLAN['mutation_lane']:
        mutants()
    call('prepare-tracked-diff', ['git', 'diff', '--stat'])
    verify_source()


def full():
    verify_rows(SRC, [PLAN['canonical_lock']])
    helper('offline-imports', 'smoke.py', 'offline-imports')
    if ROLE == 'docs':
        docs()
        return
    verify_source()
    os.environ['M0_COLLECTION_OUTPUT'] = str(E/'collected-nodeids.json')
    os.environ.pop('M0_REPORT_OUTPUT', None)
    pixi('collection', ['pytest', '-p', 'collection_plugin', '--collect-only', '-q', 'tests'])
    ids = strict_loads((E/'collected-nodeids.json').read_text())
    base = collections.Counter(strict_loads((E/'baseline-collection.json').read_text()))
    new = collections.Counter(strict_loads((E/'early-fallback-collection.json').read_text()))
    expected = base - collections.Counter([OLD_SKIP]) + new
    need(collections.Counter(ids) == expected, 'CANDIDATE_INVENTORY_NOT_BASELINE_MINUS_SKIP_PLUS_11')
    need(WORK_END-time.time() >= SPEC['pytest_entry_floor_seconds'], 'INSUFFICIENT_PYTEST_TIME')
    executed, report = run_pytest('pytest', ['tests'], expected=(0, 1),
                                  extra=('--durations=50', '--cov=bambi', '--cov-report=term', '--cov-report=xml'))
    report.update(collected_count=len(ids), expected_count_observed=sum(expected.values()),
                  expectation_406_met=len(ids) == PLAN['candidate_expected_collected_count'],
                  classification='CANDIDATE_FULL_RESULTS_REVIEW_REQUIRED')
    save('identity-report.json', report)
    save('candidate-test-results.json', {'pytest_exit': report['exit'], 'collected': len(ids),
                                         'failures': report['junit_failures'], 'skipped': report['skipped']})
    need(collections.Counter(executed) == collections.Counter(ids), 'EXECUTED_NOT_COLLECTED')
    need(report['complete'] and report['junit_cases'] == len(ids) and report['junit_duplicates'] == 0, 'INCOMPLETE_RESULTS')
    need(report['exit'] == 0 and not report['junit_failures'] and not report['skipped'] and report['junit_errors'] == 0,
         'CANDIDATE_NONPASS')
    pixi('coverage-report', ['coverage', 'report', '-m'])
    verify_source()


def render(root, prefix):
    quarto = (W/'tools/quarto-path.txt').read_text()
    os.environ['PATH'] = str(Path(quarto).parent)+os.pathsep+os.environ['PATH']
    pixi(prefix+'quartodoc', ['python', '-m', 'quartodoc', 'build', '--verbose'], cwd=root/'docs')
    pixi(prefix+'interlinks', ['python', '-m', 'quartodoc', 'interlinks'], cwd=root/'docs')
    pixi(prefix+'full-docs-render', [quarto, 'render'], cwd=root/'docs')
    site = root/'docs/_site'; need(site.is_dir(), 'DOCS_SITE_MISSING')
    (site/'.nojekyll').touch()
    pixi(prefix+'objects-inventory', ['python', '-m', 'sphobjinv', 'convert', '-o', 'zlib', 'objects.txt', 'objects.inv'],
         cwd=root/'docs')
    shutil.move(root/'docs/objects.inv', site/'objects.inv')
    need((site/'objects.inv').stat().st_size > 0, 'OBJECTS_INV_EMPTY')
    log = (E/(prefix+'full-docs-render.log')).read_text(errors='replace').replace(str(root), '<ROOT>')
    warnings = [re.sub(r'\s+', ' ', l.strip()) for l in log.splitlines() if 'WARN' in l]
    # Tracked files a render rewrites (if any) are recorded, not hidden; both trees must show the same set.
    changed = subprocess.check_output(['git', 'diff', '--name-only'], cwd=root, timeout=60).decode().split()
    return {'pages': sorted(x.relative_to(site).as_posix() for x in site.rglob('*.html')), 'warnings': warnings,
            'tracked_changed': sorted(c for c in changed if c != TEST_FILE)}


def docs():
    verify_source(SRC_BASE, candidate=False)
    base = render(SRC_BASE, 'base-')  # pristine 93bc3ad4
    verify_source()  # candidate tree still exact before its own render
    cand = render(SRC, '')
    row = {'status': 'COMPLETE_BUILD_NO_PUBLISH', 'baseline_pages': len(base['pages']), 'candidate_pages': len(cand['pages']),
           'pages_identical': base['pages'] == cand['pages'],
           'baseline_warning_lines': len(base['warnings']), 'candidate_warning_lines': len(cand['warnings']),
           'warnings_identical_multiset': collections.Counter(base['warnings']) == collections.Counter(cand['warnings']),
           'page_diff': sorted(set(base['pages']) ^ set(cand['pages']))[:100],
           'tracked_changed_by_render': {'baseline': base['tracked_changed'], 'candidate': cand['tracked_changed']},
           'notebook_execution': 'Quarto workflow defaults only; no claim that every notebook fit ran'}
    save('docs-comparison.json', row)
    need(row['pages_identical'] and row['warnings_identical_multiset']
         and base['tracked_changed'] == cand['tracked_changed'], 'DOCS_BASELINE_CANDIDATE_DIFFER')


def freeze():
    verify_rows(SRC, [PLAN['canonical_lock']])
    save('final-kernel-metrics.json', metrics())
    remaining = max(0, PLAN['artifact_cap']//2 - scan_tree(E, 4000, 64*1024**2, WORK_END)['bytes'] - 1024**2)
    if remaining < 1024**2:
        save('cache-truncated.json', {'partial': True, 'archive_complete': False, 'reason': 'no evidence budget'})
        return
    total = 0
    try:
        with (E/'bounded-cache.tar.gz').open('xb') as raw:
            writer = ByteBudgetWriter(raw, remaining)
            with tarfile.open(fileobj=writer, mode='w|gz', compresslevel=1) as tar:
                for name in ('pytensor-cache',):
                    root = W/name
                    if not root.exists():
                        continue
                    for path in sorted(root.rglob('*')):
                        need(time.time() < WORK_END, 'FREEZE_DEADLINE')
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
        save('cache-truncated.json', {'partial': True, 'archive_complete': False, 'reason': 'compressed evidence cap'})


def fixture(kind):
    if kind == 'success':
        time.sleep(20); print('F0_20_SECOND_SUCCESS', flush=True)
    elif kind == 'fail':
        print('F0_EXPECTED_EXIT_3', flush=True); sys.exit(3)
    elif kind == 'oom':
        block = bytearray(2 * 1024**3); time.sleep(20); print(len(block))
    elif kind in ('timeout', 'deadline'):
        time.sleep(20)
    else:
        raise ValueError(kind)


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'metrics':
        print(dumps_strict(metrics()))
    elif mode == 'fixture':
        fixture(sys.argv[2])
    else:
        funcs = {'helper-fixture': helper_fixture, 'install': install, 'prepare': prepare, 'full': full, 'freeze': freeze}
        row = {'phase': mode, 'started': time.time(), 'success': False,
               'bytecode': {'dont_write_bytecode_flag': sys.flags.dont_write_bytecode,
                            'env_received': HELPER_ENV_AT_START, 'env_removed_for_project_commands': True}}
        try:
            funcs[mode](); row['success'] = True
        except BaseException as exc:
            row['first_failure'] = {'type': type(exc).__name__, 'message': str(exc)[:4000]}
            raise
        finally:
            row['elapsed'] = time.time()-row['started']
            try:
                row['metrics'] = metrics()
            except OSError as exc:
                row['metrics_error'] = str(exc)
            save(mode+'-receipt.json', row)
