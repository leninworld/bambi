"""PROPOSED Docker-only build/consumer checks; not an execution approval.

The parent controller supplies an owned evidence directory and deadline. No
download or package resolution occurs here. Each wheel is externally acquired,
hash checked, and inspected before uv sees it. The parent must exercise positive
and negative fixtures in approved Docker before this helper handles real inputs.
"""

import base64
import configparser
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import time
import zipfile

from bounded import command


def wheel_members(path, expected_sha256, expected_bytes):
    """Reject unsafe archive paths and script names before the older uv runs."""
    path = Path(path)
    assert path.stat().st_size == expected_bytes
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) and len(names) <= 20000
        assert sum(x.file_size for x in archive.infolist()) <= 150 * 1024**2
        for member in archive.infolist():
            name = member.filename
            parts = PurePosixPath(name).parts
            assert name and not name.startswith('/') and '\\' not in name
            assert '..' not in parts and '.' not in parts
            assert not re.match(r'^[A-Za-z]:', name)
            assert (member.external_attr >> 16) & 0o170000 != 0o120000
            if '/scripts/' in name:
                assert re.fullmatch(r'[A-Za-z0-9_.-]+', parts[-1])
            if name.endswith('.dist-info/entry_points.txt'):
                entries = configparser.ConfigParser(interpolation=None)
                entries.read_string(archive.read(name).decode('utf-8'))
                for group in ('console_scripts', 'gui_scripts'):
                    if entries.has_section(group):
                        for script in entries[group]:
                            assert re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', script)
        records = [name for name in names if name.endswith('.dist-info/RECORD')]
        assert len(records) == 1
        rows = list(csv.reader(io.StringIO(archive.read(records[0]).decode())))
        assert len({row[0] for row in rows}) == len(rows)
        for name, digest, size in rows:
            assert name in names
            data = archive.read(name)
            if digest:
                algorithm, encoded = digest.split('=', 1)
                assert algorithm == 'sha256'
                assert base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=') == encoded.encode()
                assert len(data) == int(size)
            else:
                assert name == records[0] and not size
        assert {row[0] for row in rows} == set(names)
        return names


def wheel_guard_fixtures(root, verified_row):
    """Exercise guards using a real downloaded wheel and bounded corrupt copies.

    Docker-only, before any install. These fixtures do not invoke uv or any
    distribution entry point and never extract an archive.
    """
    root = Path(root)
    root.mkdir(exist_ok=False)
    source = Path(verified_row['path'])
    wheel_members(source, verified_row['sha256'], verified_row['bytes'])
    assert source.stat().st_size <= 2*1024**2, 'Use a small verified backend wheel'
    with zipfile.ZipFile(source) as archive:
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]
    record = next(item.filename for item, _ in entries if item.filename.endswith('.dist-info/RECORD'))
    controls = []
    for kind in ('parent-path', 'absolute-path', 'backslash-path', 'duplicate',
                 'symlink', 'entrypoint-escape', 'unrecorded-member', 'record-hash'):
        output = root/(kind+'.whl')
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for info, data in entries:
                if kind == 'record-hash' and info.filename == record:
                    rows = list(csv.reader(io.StringIO(data.decode())))
                    for row in rows:
                        if row[1]:
                            row[1] = 'sha256=' + 'A'*43
                            break
                    stream = io.StringIO()
                    csv.writer(stream).writerows(rows)
                    data = stream.getvalue().encode()
                archive.writestr(info, data)
            if kind in ('parent-path', 'absolute-path', 'backslash-path'):
                name = {'parent-path': '../escape', 'absolute-path': '/escape',
                        'backslash-path': 'nested\\escape'}[kind]
                archive.writestr(name, b'synthetic')
            elif kind == 'duplicate':
                archive.writestr(entries[0][0], entries[0][1])
            elif kind == 'symlink':
                link = zipfile.ZipInfo('synthetic-link')
                link.create_system = 3
                link.external_attr = 0o120777 << 16
                archive.writestr(link, b'../escape')
            elif kind == 'entrypoint-escape':
                archive.writestr('synthetic.dist-info/entry_points.txt',
                                 b'[console_scripts]\n../escape = synthetic:main\n')
            elif kind == 'unrecorded-member':
                archive.writestr('synthetic-unrecorded', b'synthetic')
        data = output.read_bytes()
        try:
            wheel_members(output, hashlib.sha256(data).hexdigest(), len(data))
        except (AssertionError, ValueError, zipfile.BadZipFile):
            controls.append(dict(case=kind, rejected=True))
        else:
            raise AssertionError('Wheel guard accepted '+kind)
    receipt = dict(real_verified_wheel_accepted=True, negative_controls=controls,
                   no_extraction_or_install=True)
    (root/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


class BuildConsumer:
    """Finite child commands, owned outputs, and an offline-only uv environment."""

    def __init__(self, root, uv, source, source_commit, deadline):
        self.root = Path(root).resolve()
        self.source = Path(source).resolve()
        self.uv = Path(uv).resolve()
        self.source_commit = source_commit
        self.deadline = float(deadline)
        assert self.root.is_dir() and not self.root.is_relative_to(self.source)
        assert self.uv.is_relative_to(self.root)
        self.unset = ('PYTHONPATH', 'PYTHONHOME', 'SETUPTOOLS_SCM_PRETEND_VERSION',
                     'SETUPTOOLS_SCM_PRETEND_VERSION_FOR_BAMBI', 'UV_INDEX_URL',
                     'UV_EXTRA_INDEX_URL', 'UV_DEFAULT_INDEX', 'UV_INDEX')
        self.env = dict(UV_OFFLINE='1', UV_NO_CONFIG='1',
                        UV_PYTHON_DOWNLOADS='never', UV_CACHE_DIR=str(self.root/'uv-cache'),
                        PYTHONNOUSERSITE='1', PYTHONOPTIMIZE='0', GIT_CONFIG_COUNT='1',
                        GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=str(self.source))
        self.sequence = 0

    def call(self, name, arguments, cwd=None, seconds=120):
        self.sequence += 1
        remaining = self.deadline - time.time() - 180
        assert remaining > 0, 'No deadline or cleanup reserve remains'
        log = self.root / f'{self.sequence:02d}-{name}.log'
        start = time.monotonic()
        assert not log.exists()
        arguments = [str(x) for x in arguments]
        args = ['env', *[part for name in self.unset for part in ('-u', name)],
                *[f'{key}={value}' for key, value in self.env.items()], *arguments]
        result = {}
        error = None
        try:
            return command(args, seconds=min(seconds, remaining), cap=2*1024**2,
                           guard=self.guard, cwd=cwd or self.root, log=log,
                           result=result).decode('utf-8')
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            data = log.read_bytes() if log.exists() else b''
            receipt = dict(name=name, arguments=arguments, returncode=result.get('returncode'),
                           seconds=time.monotonic()-start, error=error,
                           log_bytes=len(data), log_sha256=hashlib.sha256(data).hexdigest())
            log.with_suffix('.json').write_text(json.dumps(receipt, indent=2)+'\n')

    def guard(self):
        assert time.time() < self.deadline-180, 'Cleanup reserve reached'

    def tools_and_git(self):
        version = self.call('uv-version', [self.uv, '--version'])
        assert version.split()[:2] == ['uv', '0.11.8']
        self.call('uv-help', [self.uv, '--help'])
        for command in ('venv', 'build'):
            self.call('uv-help-'+command, [self.uv, command, '--help'])
        self.call('uv-help-pip-install', [self.uv, 'pip', 'install', '--help'])
        self.call('uv-help-pip-uninstall', [self.uv, 'pip', 'uninstall', '--help'])
        self.call('git-version', ['git', '--version'])
        head = self.call('git-head', ['git', '-C', self.source, 'rev-parse', 'HEAD']).strip()
        assert head == self.source_commit
        assert self.call('git-worktree', ['git', '-C', self.source,
                                        'rev-parse', '--is-inside-work-tree']).strip() == 'true'

    def backend(self, python, wheels):
        """Four exact declared wheels only; no optional wheel until the hook asks."""
        assert len(wheels) == 4
        for row in wheels:
            wheel_members(row['path'], row['sha256'], row['bytes'])
        build = self.root/'build-env'
        self.call('build-venv', [self.uv, '--offline', 'venv', '--python', python, build])
        interpreter = build/'bin/python'
        self.call('build-backend-install', [self.uv, '--offline', 'pip', 'install',
                  '--python', interpreter, '--no-index', '--no-deps',
                  *[row['path'] for row in wheels]])
        probe = self.root/'backend_probe.py'
        probe.write_text('''import hashlib, importlib.metadata as m, json, pathlib, sys
import setuptools.build_meta as backend
dist=m.distribution("setuptools")
print(json.dumps({"backend_path":backend.__file__, "backend_sha256":hashlib.sha256(pathlib.Path(backend.__file__).read_bytes()).hexdigest(), "backend_source":pathlib.Path(backend.__file__).read_text(), "record":dist.read_text("RECORD")}))
sys.path.insert(0, sys.argv[1])
import os
os.chdir(sys.argv[1])
requirements=backend.get_requires_for_build_wheel()
pathlib.Path(sys.argv[2]).write_text(json.dumps(requirements))
''')
        requirements_path = self.root/'backend-requirements.json'
        self.call('backend-source-record-and-hook', [interpreter, '-I', probe,
                  self.source, requirements_path], cwd=self.source)
        requirements = json.loads(requirements_path.read_text())
        assert isinstance(requirements, list)
        # No automatic interpretation/resolution: the caller can install the
        # separately priced wheel only for these precisely accepted spellings.
        assert requirements in ([], ['wheel']), f'Unpriced dynamic build requirements: {requirements}'
        return interpreter, requirements

    def optional_wheel(self, interpreter, requirements, row):
        assert requirements == ['wheel']
        wheel_members(row['path'], row['sha256'], row['bytes'])
        self.call('optional-wheel-install', [self.uv, '--offline', 'pip', 'install',
                  '--python', interpreter, '--no-index', '--no-deps', row['path']])

    def build(self, interpreter):
        output = self.root/'wheel-output'
        self.call('candidate-wheel-build', [self.uv, '--offline', 'build', '--wheel',
                  '--no-build-isolation', '--python', interpreter,
                  '--out-dir', output, self.source], cwd=self.source)
        wheels = list(output.glob('*.whl'))
        assert len(wheels) == 1
        wheel = wheels[0]
        data = wheel.read_bytes()
        names = wheel_members(wheel, hashlib.sha256(data).hexdigest(), len(data))
        assert 'bambi/representation.py' in names and 'bambi/models.py' in names
        with zipfile.ZipFile(wheel) as archive:
            for name in names:
                if name.endswith('.dist-info/METADATA'):
                    (self.root/'candidate-METADATA.txt').write_bytes(archive.read(name))
        return wheel

    def consumer(self, science_prefix, wheel):
        """Copy a frozen environment; remove its editable Bambi before install.

        No existing environment is uninstalled from or modified. Relocation is
        tested, never assumed: a prefix/path failure holds this entire route.
        The parent admits disk space for the complete extra environment copy.
        """
        original = Path(science_prefix).resolve()
        consumer = self.root/'consumer-env'
        assert not consumer.exists() and not original.is_relative_to(self.root)
        shutil.copytree(original, consumer, symlinks=True)
        interpreter = consumer/'bin/python'
        assert interpreter.resolve().is_relative_to(consumer)
        for entry in consumer.rglob('*'):
            if entry.is_symlink() and entry.is_dir():
                assert entry.resolve().is_relative_to(consumer), 'External directory symlink in disposable copy'
        check = self.call('consumer-relocation', [interpreter, '-I', '-c',
                 'import json,sys,sysconfig;print(json.dumps(dict(prefix=sys.prefix,paths=sysconfig.get_paths())))'])
        relocation = json.loads(check)
        assert Path(relocation['prefix']).resolve() == consumer
        for key in ('purelib', 'platlib', 'scripts', 'data'):
            assert Path(relocation['paths'][key]).resolve().is_relative_to(consumer)
        uninstall_probe = self.root/'uninstall_targets.py'
        uninstall_probe.write_text('''import csv, importlib.metadata as m, io, json, pathlib, sys
root=pathlib.Path(sys.argv[1]).resolve()
dist=m.distribution("bambi")
record=dist.read_text("RECORD")
assert record is not None
targets=[]
for row in csv.reader(io.StringIO(record)):
 assert len(row)==3
 target=pathlib.Path(dist.locate_file(row[0]))
 assert target.resolve().is_relative_to(root)
 assert target.parent.resolve().is_relative_to(root)
 targets.append(str(target))
assert targets and any(x.endswith(".dist-info/RECORD") for x in targets)
print(json.dumps({"all_uninstall_targets_owned":True,"targets":targets,"record":record}))
''')
        self.call('consumer-uninstall-target-containment', [interpreter, '-I',
                  uninstall_probe, consumer])
        self.call('consumer-remove-editable', [self.uv, '--offline', 'pip',
                  'uninstall', '--python', interpreter, 'bambi'])
        assert not list(consumer.rglob('__editable__*bambi*'))
        self.call('consumer-install-wheel', [self.uv, '--offline', 'pip', 'install',
                  '--python', interpreter, '--no-index', '--no-deps', wheel])
        probe = self.root/'consumer_identity.py'
        probe.write_text('''import hashlib, importlib.metadata, json, pathlib, sys, zipfile
import bambi
import bambi.representation
source=pathlib.Path(sys.argv[1]).resolve()
consumer=pathlib.Path(sys.argv[2]).resolve()
assert pathlib.Path(sys.prefix).resolve()==consumer
assert not any(pathlib.Path(p).resolve().is_relative_to(source) for p in sys.path if p)
assert not any("__editable__" in repr(finder) for finder in sys.meta_path)
with zipfile.ZipFile(sys.argv[3]) as wheel:
 for member in wheel.namelist():
  if member.startswith("bambi/") and not member.endswith("/"):
   installed=pathlib.Path(importlib.metadata.distribution("bambi").locate_file(member)).resolve()
   assert installed.is_relative_to(consumer)
   assert installed.read_bytes()==wheel.read(member)
 for module in (bambi,bambi.representation):
  file=pathlib.Path(module.__file__).resolve()
  assert file.is_relative_to(consumer) and "site-packages" in file.parts
  member="bambi/__init__.py" if module is bambi else "bambi/representation.py"
  assert file.read_bytes()==wheel.read(member)
print(json.dumps({"prefix":sys.prefix,"bambi":bambi.__file__,"representation":bambi.representation.__file__,"version":importlib.metadata.version("bambi"),"sys_path":sys.path}))
''')
        self.call('consumer-identity', [interpreter, '-I', probe, self.source, consumer, wheel])
        return interpreter
