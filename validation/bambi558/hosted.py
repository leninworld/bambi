"""Bambi #558 hosted controller, candidate attempt 3 on 93bc3ad4 (G0 2/2 closed; candidate 1 failed, 2 passed).

One push-triggered run, five lanes, native AMD64 Docker. No periodic observer, no retry, no rm/prune.
Clock: origin START; new work stops at WORK_END (START+3000, the 50-minute target); every cleanup and
receipt step is clamped to CLEANUP_END (START+3300); the upload step (3 min) then ends before the
60-minute absolute ceiling (START+3600).
"""
import sys
if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script directory
from bounded import LimitAbort, TreeLimit, command, dumps_strict, limit, save, scan_tree, stage_upload, strict_loads

P = Path(__file__).resolve().parent
PLAN = strict_loads((P / 'plan.json').read_text())
LANE = os.environ['G0_LANE']
SPEC = PLAN['lanes'][LANE]
WORKFLOW = '.github/workflows/' + PLAN['workflow_file']


def identity_guard(environ):
    """Run/workflow identity. Returns a list of failed checks (empty means admitted)."""
    expected = {
        'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'GITHUB_REPOSITORY': 'leninworld/bambi',
        'GITHUB_REF': 'refs/heads/' + PLAN['branch'], 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_RUN_NUMBER': '1',
        'GITHUB_EVENT_NAME': 'push',
        'GITHUB_WORKFLOW_REF': 'leninworld/bambi/' + WORKFLOW + '@refs/heads/' + PLAN['branch'],
        'GITHUB_REPOSITORY_OWNER': 'leninworld', 'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64'}
    failed = [k for k, v in expected.items() if environ.get(k) != v]
    if not environ.get('GITHUB_RUN_ID', '').isdigit():
        failed.append('GITHUB_RUN_ID')
    if LANE not in PLAN['lanes']:
        failed.append('G0_LANE')
    return failed


if __name__ == '__main__':
    bad = identity_guard(os.environ)
    if bad:
        raise SystemExit('RUN_IDENTITY_GUARD ' + ','.join(bad))
    if not (platform.system() == 'Linux' and platform.machine() == 'x86_64'):
        raise SystemExit('HOST_NOT_LINUX_X86_64')
    if not PLAN['execution_enabled'] or PLAN['candidate_attempt'] != 3:
        raise SystemExit('PLAN_NOT_ENABLED_FOR_ATTEMPT_3')

WORKFLOW_RUN = os.environ.get('GITHUB_RUN_ID', '0')
RUN = WORKFLOW_RUN + '-' + LANE
START = float(os.environ.get('M0_STARTED', '0'))
WORK_END = START + PLAN['target_seconds']
CLEANUP_END = START + PLAN['cleanup_end_seconds']
END = START + PLAN['absolute_seconds']
ROOT = Path(os.environ.get('RUNNER_TEMP', '/nonexistent')) / ('bambi558-a3-' + RUN)
DEADLINE_FIXTURE = sys.argv[1:] == ['deadline-fixture']
FIXTURE_END = time.time() + 5 if DEADLINE_FIXTURE else None
E = ROOT / 'evidence' / 'deadline-child' if DEADLINE_FIXTURE else ROOT / 'evidence'
SOURCE = Path(os.environ.get('GITHUB_WORKSPACE', '/nonexistent')) / 'upstream'
PACKET_CHECKOUT = Path(os.environ.get('GITHUB_WORKSPACE', '/nonexistent')) / 'packet'
IMAGE = 'bambi558-a3:' + RUN
VOLUME = 'bambi558-a3-' + RUN
DOCKER_ROOT = None
OWNED = []
RX = None
RX_START = 0


def guard():
    limit(time.time() < WORK_END, 'A3_TARGET_STOP')
    limit(time.time() < END, 'A3_ABSOLUTE_DEADLINE')
    if FIXTURE_END is not None:
        limit(time.time() < FIXTURE_END, 'F0_COMPRESSED_GLOBAL_DEADLINE')
    available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
    limit(available > 1024**3, 'HOST_MEMORY_PRESSURE')
    for path in (ROOT, DOCKER_ROOT):
        if path:
            limit(shutil.disk_usage(path).free > 6 * 1024**3, 'HOST_DISK_PRESSURE')
    limit(int(RX.read_text()) - RX_START < PLAN['network_bytes_cap'], 'NETWORK_CAP')
    try:
        scan_tree(E, 4000, PLAN['artifact_cap'], WORK_END)
    except TreeLimit as exc:
        raise LimitAbort('EVIDENCE_CAP ' + str(exc)) from None


def run(label, args, seconds=30, cap=2*1024**2, guarded=True):
    return command(args, seconds, cap, guard if guarded else (lambda: None), log=E / (label + '.log'),
                   deadline=WORK_END if guarded else CLEANUP_END)


def stop_owned(name, receipt):
    """Stop only this run's labelled container, even after the global guard denies; clamped to CLEANUP_END."""
    try:
        identity = strict_loads(run(name + '-identity-before-stop',
                                  ['docker', 'inspect', '--format', '{{json .}}', name], guarded=False))
        if identity['Config']['Labels'].get('bambi558.run') != RUN or identity['Name'] != '/' + name:
            raise RuntimeError('NOT_OWNED_CONTAINER')
        receipt['stop_requested_at'] = time.time()
        try:
            receipt['stop_output'] = run(name + '-stop', ['docker', 'stop', '-t', '2', name], seconds=25,
                                         guarded=False).decode(errors='replace')
        except Exception as exc:
            receipt['stop_error'] = type(exc).__name__ + ': ' + str(exc)[:2000]
        state = strict_loads(run(name + '-state-after-stop',
                               ['docker', 'inspect', '--format', '{{json .State}}', name], guarded=False))
        receipt['final_state'] = state
        receipt['confirmed_stopped'] = state['Running'] is False and state['Restarting'] is False
        receipt['oom_killed'] = bool(state.get('OOMKilled'))
    except Exception as exc:
        receipt['stop_inspection_error'] = type(exc).__name__ + ': ' + str(exc)[:2000]
        receipt['confirmed_stopped'] = False


def phase(label, args, seconds, memory='6g', pids=2048, bridge=False, fixture=False):
    guard()
    name = 'bambi558-a3-' + RUN + '-' + label
    if name in OWNED or (E / (label + '-phase.json')).exists():
        raise LimitAbort('PHASE_REPLAY ' + label)
    receipt = {'phase': label, 'started': time.time(), 'ok': False, 'command': args,
               'memory': memory, 'pids': pids, 'network': 'bridge' if bridge else 'none'}
    OWNED.append(name)  # recorded before create, so a create that half-succeeds is still stopped
    first = None
    try:
        create = ['docker', 'create', '--init', '--name', name, '--label', 'bambi558.run=' + RUN,
                  '--label', 'purpose=bambi558-a3', '--cpuset-cpus', '0-3', '--cpus', '4',
                  '--memory', memory, '--memory-swap', memory, '--pids-limit', str(pids),
                  '--shm-size', '4g', '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--log-driver', 'none',
                  '--network', 'bridge' if bridge else 'none', '--workdir', '/work',
                  '--mount', 'type=volume,src=' + VOLUME + ',dst=/work',
                  '--mount', 'type=bind,src=' + str(P) + ',dst=/packet,readonly',
                  '--tmpfs', '/tmp:rw,exec,nosuid,nodev,uid=1000,gid=1000,size=1g']
        if not fixture:
            create += ['--mount', 'type=bind,src=' + str(SOURCE) + ',dst=/source,readonly']
        settings = {'HOME': '/work/home', 'XDG_CACHE_HOME': '/work/cache', 'PIXI_CACHE_DIR': '/work/pixi-cache',
                    'MPLBACKEND': 'Agg', 'NUMBA_CACHE_DIR': '/work/numba-cache', 'MPLCONFIGDIR': '/work/mpl-cache',
                    'PRE_COMMIT_HOME': '/work/precommit-cache', 'BAMBI_DATA': '/work/datasets',
                    'PYTENSOR_FLAGS': 'linker='+SPEC['linker']+',base_compiledir=/work/pytensor-cache',
                    'G0_LANE': LANE, 'G0_ENV': SPEC['env'], 'G0_LINKER': SPEC['linker'], 'G0_ROLE': SPEC['role'],
                    'PYTHONPATH': '/packet', 'PYTHONUNBUFFERED': '1', 'M0_TARGET_EPOCH': str(WORK_END),
                    'OPENBLAS_NUM_THREADS': '2', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2',
                    'NUMBA_NUM_THREADS': '2'}
        for key, value in settings.items():
            create += ['--env', key + '=' + value]
        create += [IMAGE]
        cid = run(label + '-create', create).decode().strip()
        if not re.fullmatch('[a-f0-9]{64}', cid):
            raise RuntimeError('CONTAINER_ID_FORMAT')
        receipt['container_id'] = cid
        run(label + '-start', ['docker', 'start', name])
        cap = min(seconds, max(1, WORK_END - time.time() - 45))
        run(label + '-step', ['docker', 'exec', name, 'timeout', '-k', '30', str(int(cap))] + args,
            seconds=cap + 35, cap=8*1024**2)
        receipt['ok'] = True
    except BaseException as exc:
        first = exc
        receipt['first_failure'] = {'type': type(exc).__name__, 'message': str(exc)[:4000], 'at': time.time()}
        save(E / (label + '-first-failure.json'), receipt['first_failure'])
    finally:
        try:
            data = run(label + '-kernel-metrics', ['docker', 'exec', name, 'python', '/packet/inside.py', 'metrics'],
                       guarded=False)
            receipt['metrics'] = strict_loads(data)
        except Exception as exc:
            receipt['metrics_error'] = type(exc).__name__ + ': ' + str(exc)[:2000]
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
    if not (success['ok'] and success['elapsed'] >= 20 and b'F0_20_SECOND_SUCCESS' in (E / 'f0-success-step.log').read_bytes()):
        raise PhaseFailure('F0_SUCCESS')
    try:
        require_phase('f0-fail', base + ['fail'], 15, memory='512m', pids=128, fixture=True)
        require_phase('f0-dependent', base + ['success'], 35, memory='512m', pids=128, fixture=True)
        raise PhaseFailure('F0_EXPECTED_FAILURE_MISSING')
    except PhaseFailure as exc:
        if str(exc) != 'f0-fail':
            raise
    bad = strict_loads((E / 'f0-fail-phase.json').read_text())
    if not (not bad['ok'] and bad['confirmed_stopped'] and 'command exit 3,' in bad['first_failure']['message']
            and not (E / 'f0-dependent-phase.json').exists()):
        raise PhaseFailure('F0_FAIL_SEMANTICS')
    oom = phase('f0-oom', base + ['oom'], 25, memory='256m', pids=128, fixture=True)
    if not (not oom['ok'] and oom['confirmed_stopped'] and events(oom, 'memory.events')['oom_kill'] >= 1):
        raise PhaseFailure('F0_OOM_SEMANTICS')
    row = phase('f0-timeout', base + ['timeout'], 3, memory='512m', pids=128, fixture=True)
    if not (not row['ok'] and row['confirmed_stopped'] and 'command exit 124,' in row['first_failure']['message']):
        raise PhaseFailure('F0_TIMEOUT_SEMANTICS')
    command([sys.executable, '-I', str(P / 'hosted.py'), 'deadline-fixture'], 60, 1024**2, guard, expected=77,
            log=E / 'f0-global-deadline.log', deadline=WORK_END)
    deadline = strict_loads((E / 'deadline-child/f0-global-deadline-phase.json').read_text())
    if not (not deadline['ok'] and deadline['confirmed_stopped']
            and deadline['first_failure']['message'] == 'F0_COMPRESSED_GLOBAL_DEADLINE'):
        raise PhaseFailure('F0_DEADLINE_SEMANTICS')
    save(E / 'F0.json', {'status': 'PASS', 'real_cases': ['20s', 'exit3-blocks-next', 'oom', 'timeout',
                                                           'compressed-global-deadline']})


def verify_packet():
    hashes = strict_loads((P / 'file-hashes.json').read_text())
    for name, digest in hashes.items():
        actual = PACKET_CHECKOUT / WORKFLOW if name == 'workflow' else P / name
        if hashlib.sha256(actual.read_bytes()).hexdigest() != digest:
            raise RuntimeError('PACKET_HASH ' + name)
    present = sorted(p.relative_to(PACKET_CHECKOUT).as_posix() for p in PACKET_CHECKOUT.rglob('*')
                     if p.is_file() and '.git' not in p.relative_to(PACKET_CHECKOUT).parts)
    expected = sorted(['validation/bambi558/' + n for n in hashes if n != 'workflow']
                      + ['validation/bambi558/file-hashes.json', WORKFLOW])
    if present != expected:
        raise RuntimeError('PACKET_PATH_SET ' + dumps_strict(sorted(set(present) ^ set(expected)))[:2000])


def main():
    global DOCKER_ROOT
    summary = {'status': 'ADMISSION_NOT_MET_NO_PROJECT_CODE_RUN', 'source': PLAN['source'], 'started': START,
               'candidate_attempt': 3, 'lane': LANE, 'environment': SPEC['env'], 'linker': SPEC['linker'],
               'role': SPEC['role'], 'run_id': WORKFLOW_RUN, 'head_sha': os.environ.get('GITHUB_SHA')}
    admitted = False
    try:
        save(E / 'pre-admission-observations.json', {'affinity': sorted(os.sched_getaffinity(0)),
             'runner_disk_free': shutil.disk_usage(ROOT).free})
        guard()
        verify_packet()
        info = strict_loads(run('docker-info', ['docker', 'info', '--format', '{{json .}}']))
        DOCKER_ROOT = info['DockerRootDir']
        available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
        admission = {'mem_available': available, 'mem_total': info['MemTotal'], 'docker_root': DOCKER_ROOT,
                     'disk_free': shutil.disk_usage(DOCKER_ROOT).free, 'cpus': info['NCPU'],
                     'cgroup_version': info['CgroupVersion'], 'architecture': info['Architecture'],
                     'os_type': info['OSType'], 'server_version': info.get('ServerVersion'),
                     'affinity': sorted(os.sched_getaffinity(0))}
        save(E / 'host-admission.json', admission)
        checks = [(info['OSType'] == 'linux', 'HOST_OS_LINUX_REQUIRED'),
                  (info['Architecture'] in ('x86_64', 'amd64'), 'HOST_NATIVE_AMD64_REQUIRED'),
                  (info['CgroupVersion'] == '2', 'CGROUP_V2_REQUIRED'), (info['NCPU'] >= 4, 'HOST_4_CPUS_REQUIRED'),
                  (info['MemTotal'] >= PLAN['host_total_memory_admission'], 'HOST_14_GIB_TOTAL_REQUIRED'),
                  ({0, 1, 2, 3}.issubset(os.sched_getaffinity(0)), 'HOST_CPUSET_0_TO_3_REQUIRED'),
                  (available >= PLAN['host_memory_admission'], 'HOST_13_GIB_AVAILABLE_REQUIRED'),
                  (admission['disk_free'] >= PLAN['host_disk_admission'], 'HOST_24_GIB_DISK_FREE_REQUIRED')]
        for ok, message in checks:
            if not ok:
                raise RuntimeError(message)
        if run('source-head', ['git', '-C', str(SOURCE), 'rev-parse', 'HEAD']).decode().strip() != PLAN['source']:
            raise RuntimeError('SOURCE_PIN_MISMATCH')
        if run('source-clean', ['git', '-C', str(SOURCE), 'status', '--porcelain']) != b'':
            raise RuntimeError('SOURCE_NOT_CLEAN')
        admitted = True
        summary['status'] = 'FAILED'
        run('image-build', ['docker', 'build', '--network', 'default', '--label', 'bambi558.run=' + RUN,
                            '--tag', IMAGE, str(P)], 600, cap=8*1024**2)
        image = strict_loads(run('image-inspect', ['docker', 'image', 'inspect', IMAGE]))[0]
        if not (image['Architecture'] == 'amd64' and image['Os'] == 'linux'):
            raise RuntimeError('IMAGE_NOT_LINUX_AMD64')
        summary['image_id'] = image['Id']
        run('volume-create', ['docker', 'volume', 'create', '--label', 'bambi558.run=' + RUN, VOLUME])
        fixture_cases()
        rows = []
        prepare = SPEC['prepare_seconds']
        phases = [('helper-fixture', 120, False, True), ('install', 1200 if SPEC['role'] == 'docs' else 900, True, False),
                  ('prepare', prepare, True, False), ('full', 2100, True, False), ('freeze', 120, False, False)]
        for name, seconds, bridge, fixture in phases:
            if name == 'full' and SPEC['role'] == 'test' and WORK_END - time.time() < SPEC['pytest_entry_floor_seconds']:
                raise LimitAbort('INSUFFICIENT_FULL_SUITE_REMAINING_TIME')
            row = require_phase(name, ['python', '/packet/inside.py', name], seconds,
                                memory=PLAN['native_container_memory'], pids=PLAN['native_container_pids'],
                                bridge=bridge, fixture=fixture)
            rows.append(row)
            if events(row, 'memory.events')['oom_kill'] != 0 or row.get('oom_killed'):
                raise RuntimeError(name + ': OOM HOLD')
            if events(row, 'pids.events')['max'] != 0:
                raise RuntimeError(name + ': PID limit HOLD')
        summary['status'] = 'MEASUREMENT_FINISHED_REVIEW_REQUIRED'
        summary['phases'] = [r['phase'] for r in rows]
    except BaseException as exc:
        if not admitted:
            summary['status'] = 'ADMISSION_NOT_MET_NO_PROJECT_CODE_RUN'
        summary['first_failure'] = {'type': type(exc).__name__, 'message': str(exc)[:4000], 'at': time.time()}
        save(E / 'first-failure.json', summary['first_failure'])
        raise
    finally:
        finalize(summary)


def finalize(summary):
    """Terminal path: every step clamped to CLEANUP_END; receipts always valid JSON; rechecked after writing."""
    if OWNED and time.time() < CLEANUP_END:
        try:
            run('copy-owned-evidence', ['docker', 'cp', OWNED[-1] + ':/work/evidence', str(E / 'inside')],
                seconds=60, cap=4096, guarded=False)
        except Exception as exc:
            summary['evidence_copy_error'] = type(exc).__name__ + ': ' + str(exc)[:2000]
    unstopped = []
    for name in OWNED:
        receipt_path = E / (name.removeprefix('bambi558-a3-' + RUN + '-') + '-phase.json')
        try:
            if not strict_loads(receipt_path.read_text()).get('confirmed_stopped'):
                unstopped.append(name)
        except (OSError, ValueError):
            unstopped.append(name)
    summary.update(work_end=WORK_END, cleanup_end=CLEANUP_END, absolute_deadline=END, elapsed=time.time()-START,
                   received_bytes=int(RX.read_text())-RX_START if RX else None, retained_containers=OWNED,
                   unconfirmed_stops=unstopped, retained_volume=VOLUME, image=IMAGE)
    if unstopped and summary['status'] == 'MEASUREMENT_FINISHED_REVIEW_REQUIRED':
        summary['status'] = 'INTEGRITY_FAILURE_UNCONFIRMED_STOP'
    save(E / 'summary.json', summary)
    upload_root = ROOT / ('deadline-fixture-upload' if DEADLINE_FIXTURE else 'upload')
    try:
        upload = stage_upload(E, upload_root, PLAN['artifact_cap'], deadline=CLEANUP_END)
    except Exception as exc:
        upload = {'cap_hit': True, 'stage_error': type(exc).__name__ + ': ' + str(exc)[:2000]}
    if time.time() > CLEANUP_END:
        upload_root.mkdir(exist_ok=True)
        save(upload_root / 'terminal-deadline-breach.json', {'status': 'INTEGRITY_FAILURE_CLEANUP_DEADLINE',
             'at': time.time(), 'cleanup_end': CLEANUP_END}, cap=4096)
        raise SystemExit('CLEANUP_DEADLINE_BREACH')
    if (upload.get('cap_hit') or unstopped) and sys.exc_info()[0] is None:
        raise RuntimeError('FINAL_EVIDENCE_OR_STOP_INTEGRITY')


if __name__ == '__main__':
    if DEADLINE_FIXTURE:
        if not ROOT.is_dir():
            raise SystemExit('FIXTURE_ROOT_MISSING')
    else:
        ROOT.mkdir(exist_ok=False)  # fresh attempt marker: one directory per run id and lane
    E.mkdir(exist_ok=False)
    routes = [line.split()[0] for line in Path('/proc/net/route').read_text().splitlines()[1:]
              if line.split()[1] == '00000000']
    if len(set(routes)) != 1 or routes[0] != os.environ['M0_INTERFACE']:
        raise SystemExit('ROUTE_IDENTITY')
    RX = Path('/sys/class/net') / routes[0] / 'statistics/rx_bytes'
    RX_START = int(os.environ['M0_RX_STARTED'])
    if DEADLINE_FIXTURE:
        try:
            phase('f0-global-deadline', ['python', '/packet/inside.py', 'fixture', 'deadline'], 30,
                  memory='512m', pids=128, fixture=True)
            raise SystemExit('COMPRESSED_GUARD_DID_NOT_EXPIRE')
        except LimitAbort as exc:
            if str(exc) != 'F0_COMPRESSED_GLOBAL_DEADLINE':
                raise
            sys.exit(77)
    if time.time() >= WORK_END:
        raise SystemExit('LANE_STARTED_AFTER_TARGET')
    main()
