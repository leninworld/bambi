"""Root-owned build helper integration; run only in approved Docker science environment."""
import json,os,shutil,sys,time,zipfile
from pathlib import Path
from build_consumer import BuildConsumer,wheel_members,wheel_guard_fixtures
from inside import download,verify_source
W=Path('/work');P=Path('/packet');E=W/'evidence';root=W/'shipping';source=W/'src'
assets=json.loads((P/'shipping-assets.json').read_text());rows=assets['fixed']
backend=[];uvrow=None
for row in rows:
 local=dict(path=str(root/row['filename']),sha256=row['sha256'],bytes=row['bytes'])
 if row['name']=='uv':uvrow=local
 else:backend.append(local)
assert uvrow and len(backend)==4
# Real downloaded, externally SHA/size-verified small backend wheel plus malicious bounded copies.
wheel_guard_fixtures(root/'wheel-guard-fixtures',backend[-1])
names=wheel_members(uvrow['path'],uvrow['sha256'],uvrow['bytes'])
with zipfile.ZipFile(uvrow['path'])as archive:
 chosen=[n for n in names if n.endswith('/uv') and not n.endswith('/')]
 assert len(chosen)==1,'EXACT_UV_NATIVE_MEMBER_REQUIRED'
 binary=root/'uv'
 if binary.exists():assert binary.read_bytes()==archive.read(chosen[0])
 else:binary.write_bytes(archive.read(chosen[0]));binary.chmod(0o755)
b=BuildConsumer(root,binary,source,json.loads((P/'plan.json').read_text())['source'],os.environ['M0_TARGET_EPOCH'])
b.tools_and_git()
interpreter,requirements=b.backend(sys.executable,backend)
probe_controls=json.loads((root/'backend-probe-controls.json').read_text());assert probe_controls['status']=='PASS' and probe_controls['priorI3IndexErrorReproduced'] and probe_controls['requirementsFileWrittenAtCapturedOwnedPath']
if requirements:
 optional=assets['optionalWheel'];target=root/optional['filename']
 download({'browser_download_url':optional['url'],'size':optional['bytes'],'digest':'sha256:'+optional['sha256']},target)
 b.optional_wheel(interpreter,requirements,dict(path=str(target),sha256=optional['sha256'],bytes=optional['bytes']))
wheel=b.build(interpreter)
with zipfile.ZipFile(wheel)as archive:
 for name in ('bambi/causal/__init__.py','bambi/causal/effects.py','bambi/causal/types.py','bambi/causal/validate.py','bambi/causal/plots.py'):
  assert archive.read(name)==(source/name).read_bytes(),'BUILT_WHEEL_CHANGED_ACTUAL_FORMATTED_SOURCE'
prefix=Path(sys.prefix).resolve();files=[f for f in prefix.rglob('*')if f.is_file()and not f.is_symlink()]
source_bytes=sum(f.stat().st_size for f in files)
free=shutil.disk_usage(root).free;assert free>=int(source_bytes*1.25)+6*1024**3,'COMPLETE_CONSUMER_COPY_DISK_NOT_ADMITTED'
start=time.monotonic();consumer=b.consumer(prefix,wheel);copy_seconds=time.monotonic()-start
residual=json.loads((root/'consumer-residual-proof.json').read_text());cache_controls=json.loads((root/'editable-cache-controls.json').read_text())
assert residual['status']=='PASS' and residual['onlyKnownRegularPEP3147CachesRemoved'] and not residual['after']
assert cache_controls['status']=='PASS' and len(cache_controls['controls'])==6
contract=json.loads((P/'feature-validation-contract.json').read_text());probe=root/'api-consumer-tests';probe.mkdir()
test=probe/'test_causal.py';shutil.copyfile(source/'tests/test_causal.py',test)
# No source checkout on sys.path, no repository conftest/config; actual candidate tests are byte-identical.
ids=[str(test)+'::'+n for n in contract['consumerIDs']];assert len(ids)==2
b.call('consumer-focused-api-and-independent-oracle',[consumer,'-I','-m','pytest','-c','/dev/null','--rootdir',probe,'--import-mode=importlib','-vv',*ids],cwd=probe,seconds=180)
verify_source()
(E/'shipping-early-results.json').write_text(json.dumps({'editableResidualProofPassed':True,'editableCacheControlsPassed':True,'backendProbeControlsPassed':True,'candidateWheelBuilt':True,'wheelMatchesActualFormattedProductionBytes':True,'cleanConsumerIdentityPassed':True,'focusedAPIAndIndependentOraclePassed':True,'uvOfflineOnly':True,'dynamicBuildRequirements':requirements,'originalEnvironmentUnmodified':True,'sourceEnvironmentCopyBytes':source_bytes,'preCopyFreeDisk':free,'consumerAndCopyElapsedSeconds':copy_seconds,'focusedActualTestIDs':contract['consumerIDs'],'candidateWheelBytes':wheel.stat().st_size,'allRuntimeProofsRequired':True},indent=2)+'\n')
