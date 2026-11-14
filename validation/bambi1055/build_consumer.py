"""PROPOSED Docker-only build/consumer checks; not an execution approval.

The parent controller supplies an owned evidence directory and deadline. No
download or package resolution occurs here. Each wheel is externally acquired,
hash checked, and inspected before uv sees it. The parent must exercise positive
and negative fixtures in approved Docker before this helper handles real inputs.
"""

import base64
import configparser
import csv
from email.parser import Parser
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import time
import zipfile

from bounded import command


def _wheel_identity(filename):
    """Read the escaped distribution/version from a wheel's pinned basename."""
    assert filename.endswith('.whl'), 'WHEEL_FILENAME_IDENTITY'
    fields = filename[:-4].split('-')
    assert len(fields) in (5, 6), 'WHEEL_FILENAME_IDENTITY'
    distribution, version = fields[:2]
    assert re.fullmatch(r'[A-Za-z0-9_.]+', distribution), 'WHEEL_FILENAME_IDENTITY'
    assert re.fullmatch(r'[A-Za-z0-9_.+!]+', version), 'WHEEL_FILENAME_IDENTITY'
    return distribution, version


def wheel_members(path, expected_sha256, expected_bytes, diagnostics=None):
    """Verify one root distribution RECORD, including every vendored member.

    When requested, save bounded identity/count diagnostics before an exception
    reaches the caller. Nested vendor RECORD files remain ordinary hashed files
    in the complete outer manifest; they are never ignored archive members.
    """
    path = Path(path)
    details = dict(filename=path.name, expected_sha256=expected_sha256,
                   expected_bytes=expected_bytes, actual_bytes=None, actual_sha256=None,
                   root_record_count=None, suffix_record_count=None, error=None)
    try:
        details['actual_bytes'] = path.stat().st_size
        assert details['actual_bytes'] == expected_bytes, 'WHEEL_OUTER_SIZE'
        details['actual_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert details['actual_sha256'] == expected_sha256, 'WHEEL_OUTER_SHA256'
        distribution, version = _wheel_identity(path.name)
        expected_root = distribution + '-' + version + '.dist-info'
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            suffix_records = [name for name in names if name.endswith('.dist-info/RECORD')]
            records = [name for name in suffix_records if len(PurePosixPath(name).parts) == 2]
            details.update(member_count=len(names), root_record_count=len(records),
                           suffix_record_count=len(suffix_records),
                           root_record_names=[name[:256] for name in records[:8]],
                           suffix_record_names=[name[:256] for name in suffix_records[:8]],
                           record_names_truncated=len(suffix_records) > 8 or len(records) > 8,
                           expected_root=expected_root)
            assert len(names) == len(set(names)), 'WHEEL_DUPLICATE_MEMBER'
            assert len(names) <= 20000, 'WHEEL_MEMBER_CAP'
            assert sum(x.file_size for x in archive.infolist()) <= 150 * 1024**2, 'WHEEL_EXPANSION_CAP'
            for member in archive.infolist():
                name = member.filename
                parts = PurePosixPath(name).parts
                assert name and not name.startswith('/') and '\\' not in name, 'WHEEL_UNSAFE_PATH'
                assert '..' not in parts and '.' not in parts, 'WHEEL_UNSAFE_PATH'
                assert not re.match(r'^[A-Za-z]:', name), 'WHEEL_UNSAFE_PATH'
                assert (member.external_attr >> 16) & 0o170000 != 0o120000, 'WHEEL_SYMLINK_MEMBER'
                if '/scripts/' in name:
                    assert re.fullmatch(r'[A-Za-z0-9_.-]+', parts[-1]), 'WHEEL_UNSAFE_SCRIPT'
                if name.endswith('.dist-info/entry_points.txt'):
                    entries = configparser.ConfigParser(interpolation=None)
                    entries.read_string(archive.read(name).decode('utf-8'))
                    for group in ('console_scripts', 'gui_scripts'):
                        if entries.has_section(group):
                            for script in entries[group]:
                                assert re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', script), 'WHEEL_UNSAFE_ENTRYPOINT'
            assert len(records) == 1, 'WHEEL_ROOT_RECORD_COUNT'
            record = records[0]
            assert record == expected_root + '/RECORD', 'WHEEL_ROOT_IDENTITY'
            metadata_path = expected_root + '/METADATA'
            assert metadata_path in names, 'WHEEL_ROOT_METADATA_MISSING'
            metadata = Parser().parsestr(archive.read(metadata_path).decode('utf-8'))
            metadata_names, metadata_versions = metadata.get_all('Name', []), metadata.get_all('Version', [])
            assert len(metadata_names) == len(metadata_versions) == 1, 'WHEEL_METADATA_IDENTITY'
            normalize = lambda value: re.sub(r'[-_.]+', '-', value).lower()
            details.update(metadata_name=metadata_names[0][:256], metadata_version=metadata_versions[0][:256],
                           metadata_values_truncated=len(metadata_names[0]) > 256 or len(metadata_versions[0]) > 256)
            assert normalize(metadata_names[0]) == normalize(distribution), 'WHEEL_METADATA_IDENTITY'
            assert metadata_versions[0] == version, 'WHEEL_METADATA_IDENTITY'
            rows = list(csv.reader(io.StringIO(archive.read(record).decode())))
            assert all(len(row) == 3 for row in rows), 'WHEEL_RECORD_ROW_SHAPE'
            assert len({row[0] for row in rows}) == len(rows), 'WHEEL_DUPLICATE_RECORD_ROW'
            for name, digest, size in rows:
                assert name in names, 'WHEEL_RECORD_UNKNOWN_MEMBER'
                data = archive.read(name)
                if digest:
                    algorithm, encoded = digest.split('=', 1)
                    assert algorithm == 'sha256', 'WHEEL_RECORD_DIGEST'
                    assert base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=') == encoded.encode(), 'WHEEL_RECORD_DIGEST'
                    assert len(data) == int(size), 'WHEEL_RECORD_MEMBER_SIZE'
                else:
                    assert name == record and not size, 'WHEEL_UNHASHED_MEMBER'
            assert {row[0] for row in rows} == set(names), 'WHEEL_RECORD_COVERAGE'
            details.update(root_record_rows=len(rows), all_members_root_record_verified=True)
            return names
    except BaseException as exc:
        details['error'] = str(exc)[:512] or type(exc).__name__
        raise
    finally:
        if diagnostics is not None:
            # Counts are full; record-path samples are bounded and include a truncation flag.
            target = Path(diagnostics)
            assert not target.exists(), 'WHEEL_DIAGNOSTICS_ALREADY_EXISTS'
            encoded = (json.dumps(details, indent=2) + '\n').encode()
            assert len(encoded) <= 40 * 1024, 'WHEEL_DIAGNOSTICS_CAP'
            with target.open('xb') as output:
                output.write(encoded)


def wheel_guard_fixtures(root, verified_row):
    """Keep the original eight real-wheel negative controls mutation-specific."""
    root = Path(root)
    root.mkdir(exist_ok=False)
    source = Path(verified_row['path'])
    wheel_members(source, verified_row['sha256'], verified_row['bytes'], root/'positive.json')
    assert source.stat().st_size <= 2*1024**2, 'Use a small verified backend wheel'
    with zipfile.ZipFile(source) as archive:
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]
    record = next(item.filename for item, _ in entries
                  if item.filename.endswith('.dist-info/RECORD')
                  and len(PurePosixPath(item.filename).parts) == 2)
    reasons = {'parent-path': 'WHEEL_UNSAFE_PATH', 'absolute-path': 'WHEEL_UNSAFE_PATH',
               'backslash-path': 'WHEEL_UNSAFE_PATH', 'duplicate': 'WHEEL_DUPLICATE_MEMBER',
               'symlink': 'WHEEL_SYMLINK_MEMBER', 'entrypoint-escape': 'WHEEL_UNSAFE_ENTRYPOINT',
               'unrecorded-member': 'WHEEL_RECORD_COVERAGE', 'record-hash': 'WHEEL_RECORD_DIGEST'}
    controls = []
    for kind, expected_reason in reasons.items():
        case = root/kind
        case.mkdir()
        # Preserve the real filename so identity checks cannot mask the intended mutation.
        output = case/source.name
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
            wheel_members(output, hashlib.sha256(data).hexdigest(), len(data), case/'diagnostic.json')
        except AssertionError as exc:
            assert str(exc) == expected_reason, 'WRONG_WHEEL_NEGATIVE_REJECTION_REASON'
            controls.append(dict(case=kind, rejected=True, reason=str(exc)))
        else:
            raise AssertionError('Wheel guard accepted '+kind)
    receipt = dict(real_verified_wheel_accepted=True, negative_controls=controls,
                   no_extraction_or_install=True, real_filename_preserved=True)
    (root/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


def backend_wheel_preflight(root, backend_rows):
    """Verify all four pinned backends and real setuptools vendor RECORD controls.

    ``root`` is a NEW owned evidence directory. Run before the first uv install.
    This function does not install, import a wheel, resolve, or download anything.
    The original eight small-wheel controls remain separately required.
    """
    root = Path(root)
    root.mkdir(exist_ok=False)
    assert len(backend_rows) == 4, 'EXACT_FOUR_BACKEND_ROWS_REQUIRED'
    fixed = json.loads(Path(__file__).with_name('shipping-assets.json').read_text())['fixed']
    expected = {row['filename']: row for row in fixed if row['name'] != 'uv'}
    assert len(expected) == 4, 'SEALED_FOUR_BACKEND_ASSETS_REQUIRED'
    input_rows = [dict(filename=Path(row['path']).name, sha256=row['sha256'],
                       bytes=row['bytes']) for row in backend_rows]
    with (root/'input-rows.json').open('x') as output:
        json.dump(input_rows, output, indent=2)
        output.write('\n')
    assert len({row['filename'] for row in input_rows}) == 4, 'DUPLICATE_BACKEND_ASSET_ROW'
    assert {row['filename'] for row in input_rows} == set(expected), 'BACKEND_ASSET_SET_MISMATCH'
    for row in input_rows:
        pinned = expected[row['filename']]
        assert row['sha256'] == pinned['sha256'] and row['bytes'] == pinned['bytes'], 'BACKEND_ASSET_PIN_MISMATCH'
    verified = []
    for position, row in enumerate(backend_rows):
        diagnostic = root/f'{position+1:02d}-wheel.json'
        names = wheel_members(row['path'], row['sha256'], row['bytes'], diagnostic)
        details = json.loads(diagnostic.read_text())
        verified.append(dict(filename=Path(row['path']).name, sha256=row['sha256'],
                             bytes=row['bytes'], member_count=len(names),
                             root_record_count=details['root_record_count'],
                             suffix_record_count=details['suffix_record_count'],
                             metadata_name=details['metadata_name'],
                             metadata_version=details['metadata_version']))
    candidates = [row for row in backend_rows
                  if Path(row['path']).name == 'setuptools-84.0.0-py3-none-any.whl']
    assert len(candidates) == 1, 'EXACT_SETUPOOLS_REAL_POSITIVE_REQUIRED'
    row = candidates[0]
    assert row['sha256'] == '51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670'
    assert row['bytes'] == 818216
    source = Path(row['path'])
    with zipfile.ZipFile(source) as archive:
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]
    actual_names = [info.filename for info, _ in entries]
    outer = 'setuptools-84.0.0.dist-info/RECORD'
    vendors = [name for name in actual_names if name.endswith('.dist-info/RECORD') and name != outer]
    assert len(actual_names) == 343 and len(vendors) == 12, 'REAL_SETUPOOLS_VENDOR_SHAPE'
    reasons = {'foreign-root': 'WHEEL_ROOT_IDENTITY', 'double-root': 'WHEEL_ROOT_RECORD_COUNT',
               'nested-vendor-hash': 'WHEEL_RECORD_DIGEST',
               'foreign-metadata-name': 'WHEEL_METADATA_IDENTITY',
               'foreign-metadata-version': 'WHEEL_METADATA_IDENTITY'}
    controls = []
    for kind, expected_reason in reasons.items():
        case = root/kind
        case.mkdir()
        output = case/source.name
        mutated = {}
        for info, data in entries:
            name = info.filename
            if kind == 'foreign-root' and name.startswith('setuptools-84.0.0.dist-info/'):
                name = name.replace('setuptools-84.0.0.dist-info/', 'foreign-84.0.0.dist-info/', 1)
            if kind == 'nested-vendor-hash' and name == vendors[0]:
                data += b'\nsynthetic-vendor-record-tamper'
            if name == 'setuptools-84.0.0.dist-info/METADATA':
                if kind == 'foreign-metadata-name':
                    assert data.count(b'Name: setuptools\n') == 1
                    data = data.replace(b'Name: setuptools\n', b'Name: foreign_backend\n', 1)
                elif kind == 'foreign-metadata-version':
                    assert data.count(b'Version: 84.0.0\n') == 1
                    data = data.replace(b'Version: 84.0.0\n', b'Version: 0.0.0\n', 1)
            mutated[name] = (info, data)
        if kind == 'double-root':
            extra = zipfile.ZipInfo('foreign-0.dist-info/RECORD')
            mutated[extra.filename] = (extra, b'')
        # Identity controls otherwise have a complete valid root manifest. The vendor
        # hash control deliberately keeps the original outer hash so it tests that hash.
        record = 'foreign-84.0.0.dist-info/RECORD' if kind == 'foreign-root' else outer
        if kind != 'nested-vendor-hash':
            stream = io.StringIO()
            writer = csv.writer(stream)
            for name, (_, data) in mutated.items():
                if name == record:
                    writer.writerow([name, '', ''])
                else:
                    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
                    writer.writerow([name, 'sha256='+digest, str(len(data))])
            info, _ = mutated[record]
            mutated[record] = (info, stream.getvalue().encode())
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, (info, data) in mutated.items():
                copied = zipfile.ZipInfo(name)
                copied.create_system = info.create_system
                copied.external_attr = info.external_attr
                archive.writestr(copied, data)
        data = output.read_bytes()
        try:
            wheel_members(output, hashlib.sha256(data).hexdigest(), len(data), case/'diagnostic.json')
        except AssertionError as exc:
            assert str(exc) == expected_reason, 'WRONG_BACKEND_NEGATIVE_REJECTION_REASON'
            controls.append(dict(case=kind, rejected=True, reason=str(exc)))
        else:
            raise AssertionError('Backend wheel guard accepted '+kind)
    receipt = dict(allFourBackendWheelsVerified=True, verified=verified,
                   realSetuptoolsPositive=True, setuptoolsMembers=343,
                   setuptoolsNestedRecords=12, fullOuterRecordVerified=True,
                   negative_controls=controls, originalEightControlsRemainRequired=True,
                   noDownloadInstallImportOrExtraction=True)
    (root/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


LEGACY_I3_BACKEND_PROBE = 'import hashlib, importlib.metadata as m, json, pathlib, sys\nimport setuptools.build_meta as backend\ndist=m.distribution("setuptools")\nprint(json.dumps({"backend_path":backend.__file__, "backend_sha256":hashlib.sha256(pathlib.Path(backend.__file__).read_bytes()).hexdigest(), "backend_source":pathlib.Path(backend.__file__).read_text(), "record":dist.read_text("RECORD")}))\nsys.path.insert(0, sys.argv[1])\nimport os\nos.chdir(sys.argv[1])\nrequirements=backend.get_requires_for_build_wheel()\npathlib.Path(sys.argv[2]).write_text(json.dumps(requirements))\n'
CORRECTED_BACKEND_PROBE = 'from pathlib import Path\nimport sys\noriginal_argv=tuple(sys.argv)\nif len(original_argv)!=3:\n    raise ValueError("PROBE_EXACT_SOURCE_OUTPUT_ARGS_REQUIRED")\nsource=Path(original_argv[1]);output_arg=Path(original_argv[2])\nif not source.is_absolute() or not source.is_dir() or not output_arg.is_absolute() or output_arg.exists() or output_arg.is_symlink():\n    raise ValueError("PROBE_OWNED_ABSOLUTE_NEW_OUTPUT_REQUIRED")\nsource=source.resolve();requirements_path=output_arg.resolve()\nif requirements_path.parent != Path(__file__).resolve().parent:\n    raise ValueError("PROBE_OUTPUT_OUTSIDE_OWNED_ROOT")\nimport hashlib, importlib.metadata as m, json, os\nimport setuptools.build_meta as backend\ndist=m.distribution("setuptools")\nprint(json.dumps({"backend_path":backend.__file__,"backend_sha256":hashlib.sha256(Path(backend.__file__).read_bytes()).hexdigest(),"backend_source":Path(backend.__file__).read_text(),"record":dist.read_text("RECORD")}))\nsys.path.insert(0,str(source));os.chdir(source)\nrequirements=backend.get_requires_for_build_wheel()\nargv_after_hook=tuple(sys.argv)\nif not isinstance(requirements,list) or any(not isinstance(x,str) for x in requirements) or requirements not in ([],["wheel"]):\n    raise ValueError("UNPRICED_DYNAMIC_BUILD_REQUIREMENTS")\nrequirements_path.write_text(json.dumps(requirements))\nrequirements_path.with_suffix(".argv.json").write_text(json.dumps({"source":str(source),"output":str(requirements_path),"beforeBackendImport":list(original_argv),"afterBackendHook":list(argv_after_hook),"requirements":requirements,"destinationWritten":requirements_path.is_file(),"argvNotRestoredOrRewrittenByProbe":True}))\n'

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

    def call(self, name, arguments, cwd=None, seconds=120, expected=0):
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
                           result=result, expected=expected).decode('utf-8')
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            data = log.read_bytes() if log.exists() else b''
            receipt = dict(name=name, arguments=arguments, expected=expected, returncode=result.get('returncode'),
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
        for position, row in enumerate(wheels):
            wheel_members(row['path'], row['sha256'], row['bytes'],
                          self.root/f'backend-repeat-{position+1:02d}.json')
        build = self.root/'build-env'
        self.call('build-venv', [self.uv, '--offline', 'venv', '--python', python, build])
        interpreter = build/'bin/python'
        self.call('build-backend-install', [self.uv, '--offline', 'pip', 'install',
                  '--python', interpreter, '--no-index', '--no-deps',
                  *[row['path'] for row in wheels]])
        probe = self.root/'backend_probe.py'
        probe.write_text(CORRECTED_BACKEND_PROBE)
        requirements_path = self.root/'backend-requirements.json'
        legacy = self.root/'backend_probe_prior_I3_negative.py'
        legacy.write_text(LEGACY_I3_BACKEND_PROBE)
        legacy_output = self.root/'prior-I3-control-requirements.json'
        assert not legacy_output.exists() and not requirements_path.exists()
        negative = self.call('actual-prior-I3-hook-argv-negative',
                             [interpreter, '-I', legacy, self.source, legacy_output],
                             cwd=self.source, expected=1)
        assert 'IndexError: list index out of range' in negative
        assert 'pathlib.Path(sys.argv[2]).write_text(json.dumps(requirements))' in negative
        assert not legacy_output.exists()
        malformed = self.call('actual-corrected-probe-missing-output-negative',
                              [interpreter, '-I', probe, self.source], cwd=self.source, expected=1)
        assert 'PROBE_EXACT_SOURCE_OUTPUT_ARGS_REQUIRED' in malformed
        assert not requirements_path.exists()
        self.call('backend-source-record-and-hook', [interpreter, '-I', probe,
                  self.source, requirements_path], cwd=self.source)
        requirements = json.loads(requirements_path.read_text())
        assert isinstance(requirements, list)
        # No automatic interpretation/resolution: the caller can install the
        # separately priced wheel only for these precisely accepted spellings.
        assert requirements in ([], ['wheel']), f'Unpriced dynamic build requirements: {requirements}'
        argv_receipt = json.loads(requirements_path.with_suffix('.argv.json').read_text())
        assert argv_receipt['beforeBackendImport']==[str(probe),str(self.source),str(requirements_path)]
        assert argv_receipt['afterBackendHook']==[str(probe),'egg_info']
        assert argv_receipt['requirements']==requirements and argv_receipt['destinationWritten'] is True
        assert argv_receipt['source']==str(self.source) and argv_receipt['output']==str(requirements_path)
        controls={'status':'PASS','realSetuptoolsBackendHookUsed':True,
                  'priorI3IndexErrorReproduced':True,'missingOutputArgumentRejectedBeforeHook':True,
                  'capturedBeforeAnyBackendImport':True,'actualArgvMutation':argv_receipt,
                  'requirementsFileWrittenAtCapturedOwnedPath':True,
                  'requirementsStrictlyUnchangedAllowedSet':requirements,
                  'legacyProbeSHA256':hashlib.sha256(LEGACY_I3_BACKEND_PROBE.encode()).hexdigest(),
                  'correctedProbeSHA256':hashlib.sha256(CORRECTED_BACKEND_PROBE.encode()).hexdigest()}
        (self.root/'backend-probe-controls.json').write_text(json.dumps(controls,indent=2)+'\n')
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
        assert set(('bambi/causal/__init__.py','bambi/causal/effects.py','bambi/causal/types.py','bambi/causal/validate.py','bambi/causal/plots.py')) <= set(names)
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
import bambi.causal
import bambi.causal.effects
import bambi.causal.types
import bambi.causal.validate
import bambi.causal.plots
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
 for module in (bambi,bambi.causal,bambi.causal.effects,bambi.causal.types,bambi.causal.validate,bambi.causal.plots):
  file=pathlib.Path(module.__file__).resolve()
  assert file.is_relative_to(consumer) and "site-packages" in file.parts
  member="bambi/__init__.py" if module is bambi else "bambi/causal/__init__.py" if module is bambi.causal else "bambi/causal/"+module.__name__.rsplit(".",1)[-1]+".py"
  assert file.read_bytes()==wheel.read(member)
print(json.dumps({"prefix":sys.prefix,"bambi":bambi.__file__,"causal":bambi.causal.__file__,"version":importlib.metadata.version("bambi"),"sys_path":sys.path}))
''')
        self.call('consumer-identity', [interpreter, '-I', probe, self.source, consumer, wheel])
        return interpreter
