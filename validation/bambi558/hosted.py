"""One hosted M0 attempt. Reuse proven bounded commands; no periodic observer."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import time

from bounded import LimitAbort, command, limit, save, stage_upload

P = Path(__file__).resolve().parent
PLAN = json.loads((P / 'plan.json').read_text())
LANE = os.environ['G0_LANE']
SPEC = PLAN['lanes'][LANE]
assert os.environ.get('GITHUB_ACTIONS') == 'true'
assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
assert os.environ.get('GITHUB_REPOSITORY') == 'leninworld/bambi'
assert os.environ.get('GITHUB_REF') == 'refs/heads/' + PLAN['branch']
assert os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
assert os.environ.get('GITHUB_RUN_NUMBER') == '1', 'ONE_SHOT_WORKFLOW_RUN_NUMBER'
assert os.environ.get('GITHUB_EVENT_NAME') == 'push', 'PUSH_EVENT_ONLY'
assert platform.system() == 'Linux' and platform.machine() == 'x86_64'
WORKFLOW_RUN = os.environ['GITHUB_RUN_ID']
assert WORKFLOW_RUN.isdigit()
RUN = WORKFLOW_RUN + '-' + LANE
assert PLAN['execution_enabled']
START = float(os.environ['M0_STARTED'])
END = START + PLAN['absolute_seconds']
TARGET = START + PLAN['target_seconds']
ROOT = Path(os.environ['RUNNER_TEMP']) / ('bambi558-m0-' + RUN)
DEADLINE_FIXTURE = sys.argv[1:] == ['deadline-fixture']
assert not sys.argv[1:] or DEADLINE_FIXTURE
if DEADLINE_FIXTURE:
    assert ROOT.is_dir()
else:
    ROOT.mkdir(exist_ok=False)
E = ROOT / 'evidence' / 'deadline-child' if DEADLINE_FIXTURE else ROOT / 'evidence'
E.mkdir(exist_ok=False)
FIXTURE_END = time.time() + 5 if DEADLINE_FIXTURE else None
SOURCE = Path(os.environ['GITHUB_WORKSPACE']) / 'upstream'
IMAGE = 'bambi558-m0:' + RUN
VOLUME = 'bambi558-m0-' + RUN
DOCKER_ROOT = None
routes = [line.split()[0] for line in Path('/proc/net/route').read_text().splitlines()[1:]
          if line.split()[1] == '00000000']
assert len(set(routes)) == 1
RX = Path('/sys/class/net') / routes[0] / 'statistics/rx_bytes'
assert routes[0] == os.environ['M0_INTERFACE']
RX_START = int(os.environ['M0_RX_STARTED'])
OWNED = []


def guard():
    limit(time.time() < TARGET, 'ONE_M0_TARGET_STOP')
    limit(time.time() < END, 'ONE_M0_ABSOLUTE_DEADLINE')
    if FIXTURE_END is not None:
        limit(time.time() < FIXTURE_END, 'F0_COMPRESSED_GLOBAL_DEADLINE')
    available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
    limit(available > 1024**3, 'HOST_MEMORY_PRESSURE')
    for path in (ROOT, DOCKER_ROOT):
        if path:
            limit(shutil.disk_usage(path).free > 6 * 1024**3, 'HOST_DISK_PRESSURE')
    limit(int(RX.read_text()) - RX_START < PLAN['network_bytes_cap'], 'NETWORK_CAP')
    limit(sum(p.stat().st_size for p in E.rglob('*') if p.is_file()) < PLAN['artifact_cap'], 'EVIDENCE_CAP')
    image_cache = ROOT / 'image-cache'
    if image_cache.exists():
        limit(sum(p.stat().st_size for p in image_cache.iterdir() if p.is_file()) < 5 * 1024**3, 'IMAGE_CACHE_CAP')


def run(label, args, seconds=30, cap=2*1024**2, guarded=True):
    seconds = min(seconds, max(1, TARGET-time.time())) if guarded else seconds
    return command(args, seconds, cap, guard if guarded else lambda: None, log=E / (label + '.log'))


def stop_owned(name, receipt):
    """Stop only this run's labelled container, even after the global guard denies."""
    try:
        identity = json.loads(run(name + '-identity-before-stop',
            ['docker', 'inspect', '--format', '{{json .}}', name], guarded=False))
        assert identity['Config']['Labels'].get('bambi558.run') == RUN
        assert identity['Name'] == '/' + name
        receipt['stop_requested_at'] = time.time()
        try:
            receipt['stop_output'] = run(name + '-stop', ['docker', 'stop', '-t', '2', name],
                                         seconds=25, guarded=False).decode()
        except Exception as exc:
            receipt['stop_error'] = type(exc).__name__ + ': ' + str(exc)
        state = json.loads(run(name + '-state-after-stop',
            ['docker', 'inspect', '--format', '{{json .State}}', name], guarded=False))
        receipt['final_state'] = state
        receipt['confirmed_stopped'] = not state['Running'] and not state['Restarting']
    except Exception as exc:
        receipt['stop_inspection_error'] = type(exc).__name__ + ': ' + str(exc)
        receipt['confirmed_stopped'] = False


def phase(label, args, seconds, memory='6g', pids=2048, bridge=False, fixture=False):
    guard()
    name = 'bambi558-m0-' + RUN + '-' + label
    assert name not in OWNED and not (E / (label + '-phase.json')).exists()
    receipt = {'phase': label, 'started': time.time(), 'ok': False, 'command': args,
               'memory': memory, 'pids': pids, 'network': 'bridge' if bridge else 'none'}
    OWNED.append(name)  # Capture possible creation even if the CLI fails after creating it.
    first = None
    try:
        create = ['docker', 'create', '--name', name, '--label', 'bambi558.run=' + RUN,
            '--label', 'purpose=bambi558-m0', '--cpuset-cpus', '0-3', '--cpus', '4',
            '--memory', memory, '--memory-swap', memory, '--pids-limit', str(pids),
            '--shm-size', '4g', '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--log-driver', 'none',
            '--network', 'bridge' if bridge else 'none', '--workdir', '/work',
            '--mount', 'type=volume,src=' + VOLUME + ',dst=/work',
            '--mount', 'type=bind,src=' + str(P) + ',dst=/packet,readonly',
            '--tmpfs', '/tmp:rw,exec,nosuid,nodev,uid=1000,gid=1000,size=1g']
        if not fixture:
            create += ['--mount', 'type=bind,src=' + str(SOURCE) + ',dst=/source,readonly']
        settings = {'HOME': '/work/home', 'XDG_CACHE_HOME': '/work/cache',
                    'PIXI_CACHE_DIR': '/work/pixi-cache', 'MPLBACKEND': 'Agg',
                    'NUMBA_CACHE_DIR': '/work/numba-cache', 'MPLCONFIGDIR': '/work/mpl-cache',
                    'PRE_COMMIT_HOME': '/work/precommit-cache', 'BAMBI_DATA': '/work/datasets',
                    'PYTENSOR_FLAGS': 'linker='+SPEC['linker']+',base_compiledir=/work/pytensor-cache',
                    'G0_LANE': LANE, 'G0_ENV': SPEC['env'], 'G0_LINKER': SPEC['linker'], 'G0_ROLE': SPEC['role'],
                    'PYTHONPATH': '/packet', 'PYTHONUNBUFFERED': '1',
                    'M0_TARGET_EPOCH': str(TARGET),
                    'OPENBLAS_NUM_THREADS': '2', 'OMP_NUM_THREADS': '2',
                    'MKL_NUM_THREADS': '2', 'NUMBA_NUM_THREADS': '2'}
        for key, value in settings.items():
            create += ['--env', key + '=' + value]
        create += [IMAGE]
        cid = run(label + '-create', create).decode().strip()
        assert re.fullmatch('[a-f0-9]{64}', cid)
        receipt['container_id'] = cid
        run(label + '-start', ['docker', 'start', name])
        cap = min(seconds, max(1, TARGET - time.time() - 45))
        run(label + '-step', ['docker', 'exec', name, 'timeout', '-k', '30', str(cap)] + args,
            seconds=cap + 35, cap=8*1024**2)
        receipt['ok'] = True
    except BaseException as exc:
        first = exc
        receipt['first_failure'] = {'type': type(exc).__name__, 'message': str(exc), 'at': time.time()}
        save(E / (label + '-first-failure.json'), receipt['first_failure'])
    finally:
        # This is a single end-of-phase read, never a sampling loop.
        try:
            data = run(label + '-kernel-metrics',
                ['docker', 'exec', name, 'python', '/packet/inside.py', 'metrics'], guarded=False)
            receipt['metrics'] = json.loads(data)
        except Exception as exc:
            receipt['metrics_error'] = type(exc).__name__ + ': ' + str(exc)
        stop_owned(name, receipt)
        receipt['elapsed'] = time.time() - receipt['started']
        receipt['ok'] = receipt['ok'] and receipt.get('confirmed_stopped', False) and 'metrics' in receipt
        save(E / (label + '-phase.json'), receipt)
    if isinstance(first, LimitAbort):
        raise first
    return receipt


def events(row, name):
    return {k: int(v) for k, v in (line.split() for line in row['metrics'][name].splitlines())}


class PhaseFailure(Exception):
    """A retained ordinary phase failure that blocks dependent steps."""


def require_phase(*args, **kwargs):
    row = phase(*args, **kwargs)
    if not row['ok']:
        raise PhaseFailure(row['phase'])
    return row


def fixture_cases():
    base = ['python', '/packet/inside.py', 'fixture']
    success = phase('f0-success', base + ['success'], 35, memory='512m', pids=128, fixture=True)
    assert success['ok'] and success['elapsed'] >= 20
    assert b'F0_20_SECOND_SUCCESS' in (E / 'f0-success-step.log').read_bytes()
    try:
        require_phase('f0-fail', base + ['fail'], 15, memory='512m', pids=128, fixture=True)
        require_phase('f0-dependent', base + ['success'], 35, memory='512m', pids=128, fixture=True)
        raise AssertionError('Expected PhaseFailure did not occur')
    except PhaseFailure as exc:
        assert str(exc) == 'f0-fail'
    bad = json.loads((E / 'f0-fail-phase.json').read_text())
    assert not bad['ok'] and bad['confirmed_stopped']
    assert 'command exit 3,' in bad['first_failure']['message']
    assert b'F0_EXPECTED_EXIT_3' in (E / 'f0-fail-step.log').read_bytes()
    assert not (E / 'f0-dependent-phase.json').exists()
    assert not any(name.endswith('-f0-dependent') for name in OWNED)
    oom = phase('f0-oom', base + ['oom'], 25, memory='256m', pids=128, fixture=True)
    assert not oom['ok'] and oom['confirmed_stopped'] and events(oom, 'memory.events')['oom_kill'] >= 1
    row = phase('f0-timeout', base + ['timeout'], 3, memory='512m', pids=128, fixture=True)
    assert not row['ok'] and row['confirmed_stopped'] and 'command exit 124,' in row['first_failure']['message']
    # The same controller/guard/phase path in a fixture subprocess, under the unchanged outer deadline.
    # Its shorter surrogate deadline is deliberately expired; no actual M0 clock is reset.
    command([sys.executable, str(P / 'hosted.py'), 'deadline-fixture'],
            min(60, TARGET-time.time()), 1024**2, guard, expected=77, log=E / 'f0-global-deadline.log')
    deadline = json.loads((E / 'deadline-child/f0-global-deadline-phase.json').read_text())
    assert not deadline['ok'] and deadline['confirmed_stopped']
    assert deadline['first_failure']['message'] == 'F0_COMPRESSED_GLOBAL_DEADLINE'
    save(E / 'F0.json', {'status': 'PASS', 'real_cases': ['20s', 'exit3-blocks-next', 'oom', 'timeout', 'compressed-global-deadline'],
                         'global_deadline': 'Outer LimitAbort propagates. Only the nested fixture sentinel is expected; outer clock unchanged.'})


if DEADLINE_FIXTURE:
    try:
        phase('f0-global-deadline', ['python', '/packet/inside.py', 'fixture', 'deadline'],
              30, memory='512m', pids=128, fixture=True)
        raise AssertionError('Compressed guard did not expire')
    except LimitAbort as exc:
        if str(exc) != 'F0_COMPRESSED_GLOBAL_DEADLINE':
            raise
        sys.exit(77)


summary = {'status': 'ADMISSION_NOT_MET_NO_PROJECT_CODE_RUN', 'source': PLAN['source'],
           'started': START, 'full_product_validation': False, 'lane': LANE, 'environment': SPEC['env'], 'linker': SPEC['linker'], 'role': SPEC['role']} 
admitted = False
try:
    initial_available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
    save(E / 'pre-admission-observations.json', {'mem_available': initial_available,
         'affinity': sorted(os.sched_getaffinity(0)), 'runner_disk_free': shutil.disk_usage(ROOT).free,
         'docker_information': 'NOT_YET_READ'})
    guard()
    for name, digest in json.loads((P / 'file-hashes.json').read_text()).items():
        actual_path = Path(os.environ['GITHUB_WORKSPACE'])/'packet/.github/workflows/validate-bambi558-g0-i2.yml' if name == 'validate.yml' else P/name
        assert hashlib.sha256(actual_path.read_bytes()).hexdigest() == digest, name
    info = json.loads(run('docker-info', ['docker', 'info', '--format', '{{json .}}']))
    DOCKER_ROOT = info['DockerRootDir']
    available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
    admission = {'mem_available': available, 'mem_total': info['MemTotal'], 'docker_info': info,
                 'docker_root': DOCKER_ROOT, 'disk_free': None, 'cpus': info['NCPU'],
                 'cgroup_version': info['CgroupVersion'], 'affinity': sorted(os.sched_getaffinity(0)),
                 'no_mac_access': True}
    save(E / 'host-admission.json', admission)
    admission['disk_free'] = shutil.disk_usage(DOCKER_ROOT).free
    save(E / 'host-admission.json', admission)
    assert info['OSType'] == 'linux', 'HOST_OS_LINUX_REQUIRED'
    assert info['Architecture'] in ('x86_64', 'amd64'), 'HOST_NATIVE_AMD64_REQUIRED'
    assert info['CgroupVersion'] == '2', 'CGROUP_V2_REQUIRED'
    assert info['NCPU'] >= 4, 'HOST_AT_LEAST_4_CPUS_REQUIRED'
    assert info['MemTotal'] >= PLAN['host_total_memory_admission'], 'HOST_14_GIB_TOTAL_REQUIRED'
    assert {0, 1, 2, 3}.issubset(os.sched_getaffinity(0)), 'HOST_CPUSET_0_TO_3_REQUIRED'
    assert available >= PLAN['host_memory_admission'], 'HOST_13_GIB_AVAILABLE_REQUIRED'
    assert admission['disk_free'] >= PLAN['host_disk_admission'], 'HOST_24_GIB_DISK_FREE_REQUIRED'
    assert run('source-head', ['git', '-C', str(SOURCE), 'rev-parse', 'HEAD']).decode().strip() == PLAN['source'], 'SOURCE_PIN_MISMATCH'
    assert run('source-clean', ['git', '-C', str(SOURCE), 'status', '--porcelain']) == b'', 'SOURCE_NOT_CLEAN'
    admitted = True
    summary['status'] = 'FAILED'
    run('image-build', ['docker', 'build', '--network', 'default', '--label', 'bambi558.run=' + RUN,
                        '--tag', IMAGE, str(P)], 600, cap=8*1024**2)
    image = json.loads(run('image-inspect', ['docker', 'image', 'inspect', IMAGE]))[0]
    assert image['Architecture'] == 'amd64' and image['Os'] == 'linux'
    summary['image_id'] = image['Id']
    run('volume-create', ['docker', 'volume', 'create', '--label', 'bambi558.run=' + RUN, VOLUME])
    fixture_cases()
    rows = []
    phases = [('helper-fixture', 90, False, True), ('install', 900, True, False),
              ('prepare', 600, True, False), ('baseline', 2100, True, False),
              ('freeze', 120, False, False)]
    for name, seconds, bridge, fixture in phases:
        if name == 'baseline' and SPEC['role'] == 'test':
            limit(TARGET-time.time() >= SPEC['pytest_entry_floor_seconds'],
                  'INSUFFICIENT_FULL_SUITE_REMAINING_TIME')
        row = require_phase(name, ['python', '/packet/inside.py', name], seconds,
                            memory=PLAN['native_container_memory'],
                            pids=PLAN['native_container_pids'], bridge=bridge, fixture=fixture)
        rows.append(row)
        assert events(row, 'memory.events')['oom_kill'] == 0, name + ': OOM HOLD'
        assert events(row, 'pids.events')['max'] == 0, name + ': PID limit HOLD'
    summary['status'] = 'MEASUREMENT_FINISHED_REVIEW_REQUIRED'
    summary['phases'] = rows
except BaseException as exc:
    if not admitted:
        summary['status'] = 'ADMISSION_NOT_MET_NO_PROJECT_CODE_RUN'
    summary['first_failure'] = {'type': type(exc).__name__, 'message': str(exc), 'at': time.time()}
    save(E / 'first-failure.json', summary['first_failure'])
    raise
finally:
    # Copy only synthetic baseline evidence, never a whole venv, cache or source tree.
    # Stopped containers and the labelled volume are retained; no rm/prune/reset.
    if OWNED:
        try:
            run('copy-owned-evidence', ['docker', 'cp', OWNED[-1] + ':/work/evidence', str(E / 'inside')],
                seconds=30, cap=4096, guarded=False)
        except Exception as exc:
            summary['evidence_copy_error'] = type(exc).__name__ + ': ' + str(exc)
    summary.update(execution_target=TARGET, absolute_deadline=END, elapsed=time.time()-START, received_bytes=int(RX.read_text())-RX_START,
                   retained_containers=OWNED, retained_volume=VOLUME, image=IMAGE)
    save(E / 'summary.json', summary)
    upload = stage_upload(E, ROOT/('deadline-fixture-upload' if DEADLINE_FIXTURE else 'upload'), PLAN['artifact_cap'])
    if upload['cap_hit'] and sys.exc_info()[0] is None:
        raise RuntimeError('FINAL_EVIDENCE_CAP_HIT_ALLOWLIST_ONLY')
