"""Bounded existing ZIP inspection and complete1055 MAIN five-lane result contract; no extraction."""
import hashlib,io,json,stat,sys,unicodedata,zipfile
from pathlib import Path,PurePosixPath
LANES=('py312-cvm','py312-numba','py313-cvm','py313-numba','science-docs')
MAIN=json.loads(Path(__file__).with_name('plan.json').read_text())['source']
def validate_members(z):
    rows=z.infolist();assert 0<len(rows)<=4096
    assert sum(x.file_size for x in rows)<=256*1024**2
    seen=set()
    for x in rows:
        n=x.filename;p=PurePosixPath(n)
        assert n and not p.is_absolute() and '..'not in p.parts and '\\'not in n and ':'not in n
        assert not any(ord(c)<32 for c in n) and not(x.flag_bits&1)
        key=unicodedata.normalize('NFC',n).casefold();assert key not in seen;seen.add(key)
        mode=(x.external_attr>>16)&0o170000
        assert mode in (0,stat.S_IFREG,stat.S_IFDIR) and not stat.S_ISLNK(x.external_attr>>16)
        assert x.file_size<=32*1024**2 and x.compress_size<=96*1024**2
    return {x.filename:x for x in rows if not x.is_dir()}
def object_read(z,rows,name,optional=False):
    if optional and name not in rows:return None
    assert name in rows and rows[name].file_size<=4*1024**2,name
    return json.loads(z.read(name))
def backend_probe_proof(z,rows):
    prefix='inside/shipping/'
    control=object_read(z,rows,prefix+'backend-probe-controls.json',True)
    if control is None:return {'passed':False,'reason':'MISSING_BACKEND_PROBE_CONTROLS'}
    argv=object_read(z,rows,prefix+'backend-requirements.argv.json')
    requirements=object_read(z,rows,prefix+'backend-requirements.json')
    assert control['status']=='PASS' and control['realSetuptoolsBackendHookUsed'] and control['priorI3IndexErrorReproduced'] and control['missingOutputArgumentRejectedBeforeHook']
    assert control['capturedBeforeAnyBackendImport'] and control['requirementsFileWrittenAtCapturedOwnedPath']
    assert requirements in ([],['wheel']) and control['requirementsStrictlyUnchangedAllowedSet']==requirements
    assert argv==control['actualArgvMutation'] and argv['requirements']==requirements and argv['destinationWritten']
    assert argv['beforeBackendImport']==['/work/shipping/backend_probe.py','/work/src','/work/shipping/backend-requirements.json']
    assert argv['afterBackendHook']==['/work/shipping/backend_probe.py','egg_info']
    for filename,key in [('backend_probe_prior_I3_negative.py','legacyProbeSHA256'),('backend_probe.py','correctedProbeSHA256')]:
        member=prefix+filename;assert member in rows and rows[member].file_size<=65536
        assert hashlib.sha256(z.read(member)).hexdigest()==control[key]
    assert prefix+'prior-I3-control-requirements.json'not in rows
    checked=[]
    for name,expected,marker in [('actual-prior-I3-hook-argv-negative',1,'IndexError: list index out of range'),('actual-corrected-probe-missing-output-negative',1,'PROBE_EXACT_SOURCE_OUTPUT_ARGS_REQUIRED'),('backend-source-record-and-hook',0,None)]:
        matches=[n for n in rows if n.startswith(prefix)and n.endswith('-'+name+'.json')];assert len(matches)==1
        receipt=object_read(z,rows,matches[0]);assert receipt['expected']==expected and receipt['returncode']==expected and receipt['error']is None
        log=matches[0][:-5]+'.log';assert log in rows and rows[log].file_size<=2*1024**2
        raw=z.read(log);assert len(raw)==receipt['log_bytes']and hashlib.sha256(raw).hexdigest()==receipt['log_sha256']
        if marker is not None:assert marker in raw.decode()
        checked.append(name)
    return {'passed':True,'rawChildEvidenceVerified':checked,'requirements':requirements,'actualArgv':argv}

def editable_residual_proof(z,rows):
    import re
    prefix='inside/shipping/'
    proof=object_read(z,rows,prefix+'consumer-residual-proof.json',True)
    controls=object_read(z,rows,prefix+'editable-cache-controls.json',True)
    if proof is None or controls is None:return {'passed':False,'reason':'MISSING_REAL_EDITABLE_RESIDUAL_PROOF'}
    def hexsha(value):return isinstance(value,str)and re.fullmatch('[0-9a-f]{64}',value)is not None
    def valid_success(detail,root,expected_tag):
        assert detail['status']=='PASS'and detail['onlyKnownRegularPEP3147CachesRemoved']and detail['after']==[]
        assert detail['cacheTag']==expected_tag and re.fullmatch('cpython-3[0-9]+',expected_tag)
        known=detail['knownSources'];assert 0<len(known)<=8
        for source in known:
            p=PurePosixPath(source['path']);assert p.is_relative_to(root)and '..'not in p.parts and p.suffix=='.py'and p.name.startswith('__editable__')and 'bambi'in p.name
            assert hexsha(source['sha256'])and isinstance(source['bytes'],int)and source['bytes']>0
        before=detail['before'];assert before==detail['removed']and len(before)<=64
        assert len({r['path']for r in before})==len(before)
        assert detail['legacyGuardWouldFail']is bool(before)and detail['benignNoop']is(not before)
        for row in before:
            p=PurePosixPath(row['path']);assert p.is_relative_to(root)and '..'not in p.parts and p.parent.name=='__pycache__'
            exact=[r for r in known if p.parent==PurePosixPath(r['path']).parent/'__pycache__'and re.fullmatch(re.escape(PurePosixPath(r['path']).stem)+r'\.'+re.escape(expected_tag)+r'(?:\.opt-[012])?\.pyc',p.name)]
            assert len(exact)==1,'REMOVED_CACHE_NOT_EXACT_RECORD_SOURCE_TAG'
            assert row['classification']=='KNOWN_RECORD_EDITABLE_PEP3147_CACHE'and row['regular']is True and row['symlink']is False
            assert hexsha(row['sha256'])and isinstance(row['bytes'],int)and 0<row['bytes']<=2*1024**2
        return known
    def actual_child(name):
        found=[n for n in rows if n.startswith(prefix)and n.endswith('-'+name+'.json')];assert len(found)==1
        receipt=object_read(z,rows,found[0]);assert receipt['expected']==receipt['returncode']==0 and receipt['error']is None
        log=found[0][:-5]+'.log';raw=z.read(log);assert len(raw)==receipt['log_bytes']and hashlib.sha256(raw).hexdigest()==receipt['log_sha256']
        return receipt,raw
    relocation_receipt,raw=actual_child('consumer-relocation');relocation=json.loads(raw)
    root=PurePosixPath('/work/shipping/consumer-env');interpreter=str(root/'bin/python')
    assert relocation_receipt['arguments'][:3]==[interpreter,'-I','-c']
    assert relocation['prefix']==str(root)
    for key in ('purelib','platlib','scripts','data'):assert PurePosixPath(relocation['paths'][key]).is_relative_to(root)
    tag=relocation['cache_tag'];known=valid_success(proof,root,tag)
    assert controls['sourceBytesFromRealRecordEditableFinder']==known[0]
    assert controls['status']=='PASS'and controls['actualRelocatedInterpreterUsed']and controls['primaryRealCloneUVUninstallStillRequired']
    expected={'known-cache':'EXPECTED_SUCCESS','active-source':'EXPECTED_HOLD','symlink-cache':'EXPECTED_HOLD','foreign-source':'EXPECTED_HOLD','unknown-cache':'EXPECTED_HOLD','benign-noop':'EXPECTED_SUCCESS'}
    assert len(controls['controls'])==6 and {r['case']:r['outcome']for r in controls['controls']}==expected
    compilecode='import hashlib,json,pathlib,py_compile,sys;p=pathlib.Path(sys.argv[1]);c=pathlib.Path(py_compile.compile(str(p),doraise=True));print(json.dumps(dict(source=str(p),sourceSHA256=hashlib.sha256(p.read_bytes()).hexdigest(),cache=str(c),cacheSHA256=hashlib.sha256(c.read_bytes()).hexdigest(),cache_tag=sys.implementation.cache_tag)))'
    for row in controls['controls']:
        case=row['case'];member=prefix+'editable-cache-control-'+case+'.json';assert member in rows
        raw=z.read(member);assert hashlib.sha256(raw).hexdigest()==row['proofSHA256']
        detail=object_read(z,rows,member);controlroot=PurePosixPath('/work/shipping/editable-cache-controls')/case
        if case in ('known-cache','benign-noop'):
            controlknown=valid_success(detail,controlroot,tag)
            assert len(detail['before'])==(1 if case=='known-cache'else 0)
            assert controlknown[0]['sha256']==known[0]['sha256']and PurePosixPath(controlknown[0]['path']).name==PurePosixPath(known[0]['path']).name
        else:assert detail['status']=='FAIL'and detail['removed']==[]
        if case!='benign-noop':
            receipt,raw=actual_child('editable-cache-control-compile-'+case);compiled=json.loads(raw)
            source=controlroot/PurePosixPath(known[0]['path']).name
            assert receipt['arguments']==[interpreter,'-I','-B','-c',compilecode,str(source)]
            assert compiled['source']==str(source)and compiled['sourceSHA256']==known[0]['sha256']and compiled['cache_tag']==tag
            cache=PurePosixPath(compiled['cache']);assert cache.parent==source.parent/'__pycache__'and cache.name==source.stem+'.'+tag+'.pyc'and hexsha(compiled['cacheSHA256'])
            if case=='known-cache':assert detail['before'][0]['sha256']==compiled['cacheSHA256']
    containment_receipt,raw=actual_child('consumer-uninstall-target-containment');targets=json.loads(raw)
    assert containment_receipt['arguments']==[interpreter,'-I','/work/shipping/uninstall_targets.py',str(root)]
    assert targets['all_uninstall_targets_owned']and targets['editable_sources']==known
    uninstall_receipt,unused=actual_child('consumer-remove-editable')
    assert uninstall_receipt['arguments']==['/work/shipping/uv','--offline','pip','uninstall','--python',interpreter,'bambi']
    return {'passed':True,'originalI4FilenameCauseNotAssumed':True,'actualResidualCount':len(proof['before']),'knownCacheRemovedCount':len(proof['removed']),'actualLegacyGuardWouldFail':proof['legacyGuardWouldFail'],'benignNoop':proof['benignNoop'],'rawControlsVerified':6,'actualCompileChildReceiptsVerified':5,'actualRelocationCacheTag':tag}

def inspect_archive(path,identity,lane):
    p=Path(path);assert lane in LANES and p.stat().st_size==identity['expectedArchiveBytes']<=96*1024**2
    assert hashlib.sha256(p.read_bytes()).hexdigest()==identity['sha256'] and identity['digestVerified'] and identity['verifiedCompleteArchive']
    with zipfile.ZipFile(p)as z:
        rows=validate_members(z);summary=object_read(z,rows,'summary.json');clock=object_read(z,rows,'workflow-clock.json')
        assert summary['lane']==lane and summary['source']==MAIN and summary['started']==clock['created_epoch']
        phases=[object_read(z,rows,n)for n in rows if n.endswith('-phase.json')]
        stops=bool(phases)and all(x.get('confirmed_stopped')is True for x in phases)
        failure=object_read(z,rows,'first-failure.json',True)
        measured=summary['status']=='MEASUREMENT_FINISHED_REVIEW_REQUIRED'
        nonfixture=[x for x in phases if not x['phase'].startswith('f0-')]
        required=all(x.get('ok')is True for x in nonfixture)
        oom=[]
        for phase in phases:
            state=phase.get('final_state',{})
            if state.get('OOMKilled'):oom.append({'phase':phase['phase'],'intentional':phase['phase']=='f0-oom'})
        result={'lane':lane,'source':MAIN,'workflowCreatedEpoch':clock['created_epoch'],'measured':measured,'allOwnedPhaseStopReceipts':stops,'phaseCount':len(phases),'ordinaryRequiredPhasesPassed':required,'oom':oom,'firstFailure':failure or summary.get('first_failure'),'fullPass':False}
        formatting=object_read(z,rows,'inside/candidate-format-proof.json',True);baseline=object_read(z,rows,'inside/baseline-feature-absence.json',True);feature=object_read(z,rows,'inside/feature-results.json',True);shipping=object_read(z,rows,'inside/shipping-early-results.json',True)
        result.update(formatting=formatting,baselineFeatureAbsence=baseline,feature=feature,shipping=shipping)
        probe=backend_probe_proof(z,rows);result['backendProbeEvidence']=probe
        residual=editable_residual_proof(z,rows);result['editableResidualEvidence']=residual
        common=bool(residual['passed'] and probe['passed'] and formatting and formatting['status']=='PASS' and formatting['blackVersion']=='25.9.0' and formatting['allPythonASTIdentical'] and formatting['exactContributionPathUnionUnchanged'] and baseline and baseline['actualBaselineModuleAbsent'] and baseline['notMathematicalBugReproduction'] and feature and feature['status']=='PASS' and feature['allCasesExecuted'] and shipping and shipping.get('editableResidualProofPassed') is True and shipping.get('editableCacheControlsPassed') is True and shipping.get('backendProbeControlsPassed') is True and shipping['candidateWheelBuilt'] and shipping['cleanConsumerIdentityPassed'] and shipping['focusedAPIAndIndependentOraclePassed'] and shipping['uvOfflineOnly'] and shipping['wheelMatchesActualFormattedProductionBytes'])
        if lane!='science-docs':
            ids=object_read(z,rows,'inside/collected-nodeids.json',True);executed=object_read(z,rows,'inside/executed-collection.json',True);tests=object_read(z,rows,'inside/main-full-test-results.json',True)
            result.update(collectedNodeIDs=ids,executedNodeIDs=executed,tests=tests)
            result['fullPass']=bool(measured and stops and required and common and tests and ids and ids==executed and len(ids)==len(set(ids))==tests['collectedCount']==tests['completedCount'] and tests['pytestExit']==0 and not tests['failures'] and tests['skipSetUnchanged'] and not any(not x['intentional']for x in oom))
        else:
            site=object_read(z,rows,'inside/full-site-summary.json',True);hooks=object_read(z,rows,'inside/candidate-nine-path-hooks.json',True);nb=object_read(z,rows,'inside/candidate-notebook-identity.json',True)
            result.update(site=site,hooks=hooks,notebook=nb)
            result['fullPass']=bool(measured and stops and required and common and site and site['fullRenderCompleted'] and site['brokenLocalCount']==0 and hooks and hooks['exit']==0 and hooks['allFinalBytesUnchanged'] and nb and nb['codeCellsExecuted']>0 and nb['exactReviewedCellSourcesVerified'] and nb['outputPrivateDetailScanPassed'] and nb['actualPNGOutputs']>=9 and nb['actualGenuineFitCallsCompleted']==2 and nb['actualFitDimensionChecksPassed'] and not any(not x['intentional']for x in oom))
        return result

def adjudicate(lanes):
    assert set(lanes)==set(LANES)
    anchors={r['workflowCreatedEpoch']for r in lanes.values()};assert len(anchors)==1,'SHARED_CLOCK_MISMATCH'
    sets=[set(lanes[n].get('collectedNodeIDs')or[])for n in LANES if n!='science-docs']
    same=bool(sets[0])and all(x==sets[0]for x in sets)
    hashes=[r.get('formatting',{}).get('afterHashes')for r in lanes.values()]
    same_source=bool(hashes[0])and all(h==hashes[0]for h in hashes)
    candidate_pass=same and same_source and all(r['fullPass']for r in lanes.values())
    return {'allFiveActualLanesPresent':True,'actualFormattedPythonSourceHashesAgreeAcrossFiveJobs':same_source,'allFourCollectedNodeSetsEqual':same,'allOwnedPhasesStopped':all(r['allOwnedPhaseStopReceipts']for r in lanes.values()),'candidateRequiredValidationPassed':candidate_pass,'machineValidationPassed':candidate_pass,'actualCurrentMainCollectedCount':len(sets[0])if same else None,'freshVisualReview':'PENDING_REQUIRED_ACTUAL_FIGURE_AND_RENDERED_NOTEBOOK_INSPECTION','exactFinalDiffAndClaudeTestedPatchReview':'PENDING','noContributionPRorCommentAction':True}

def fixtures():
    def blob(name='inside/example.json',kind=stat.S_IFREG):
        o=io.BytesIO()
        with zipfile.ZipFile(o,'w')as z:
            i=zipfile.ZipInfo(name);i.external_attr=(kind|0o644)<<16;z.writestr(i,b'{"source":"public-synthetic"}')
        return o.getvalue()
    with zipfile.ZipFile(io.BytesIO(blob()))as z:assert len(validate_members(z))==1
    failed=[]
    for label,data in [('traversal',blob('../escape')),('absolute',blob('/escape')),('backslash',blob('inside\\escape')),('link',blob(kind=stat.S_IFLNK)),('colon',blob('drive:escape'))]:
        try:
            with zipfile.ZipFile(io.BytesIO(data))as z:validate_members(z)
        except AssertionError:failed.append(label)
        else:raise AssertionError('Bad ZIP accepted: '+label)
    for names in [('same','same'),('A','a'),('caf\u00e9','cafe\u0301')]:
        o=io.BytesIO()
        with zipfile.ZipFile(o,'w')as z:
            for name in names:z.writestr(name,b'{}')
        try:
            with zipfile.ZipFile(io.BytesIO(o.getvalue()))as z:validate_members(z)
        except AssertionError:failed.append('collision')
        else:raise AssertionError('ZIP collision accepted')
    # Exercise the real aggregate pass/failure/unequal-union/clock branches, not a surrogate.
    good={n:{'workflowCreatedEpoch':1,'collectedNodeIDs':['tests/test_public.py::test_ok'],'allOwnedPhaseStopReceipts':True,'fullPass':True,'formatting':{'afterHashes':{'synthetic.py':'same'}}}for n in LANES}
    assert adjudicate(good)['machineValidationPassed']
    wrong={n:dict(x)for n,x in good.items()};wrong['py312-cvm']['fullPass']=False;assert not adjudicate(wrong)['machineValidationPassed']
    wrong={n:dict(x)for n,x in good.items()};wrong['py313-numba']['collectedNodeIDs']=['different'];assert not adjudicate(wrong)['machineValidationPassed']
    wrong={n:dict(x)for n,x in good.items()};wrong['science-docs']['allOwnedPhaseStopReceipts']=False;assert not adjudicate(wrong)['allOwnedPhasesStopped']
    wrong={n:dict(x)for n,x in good.items()};wrong['science-docs']['workflowCreatedEpoch']=2
    try:adjudicate(wrong)
    except AssertionError:failed.append('clock')
    else:raise AssertionError('Mismatched workflow clocks accepted')
    # Read actual installed-pytest collections/reports, then exercise real archive decoding.
    e=Path('/work/evidence');ids=json.loads((e/'reader-pass-collected.json').read_text())
    actualReports=[json.loads(x)for x in(e/'reader-pass-reports.jsonl').read_text().splitlines()]
    assert len(ids)==1 and any(x['when']=='call'and x['outcome']=='passed'for x in actualReports)
    fixture={'summary.json':{'lane':'py312-cvm','source':MAIN,'started':1,'status':'MEASUREMENT_FINISHED_REVIEW_REQUIRED'},'workflow-clock.json':{'created_epoch':1},'baseline-phase.json':{'phase':'baseline','confirmed_stopped':True,'ok':True,'final_state':{'OOMKilled':False}},'inside/collected-nodeids.json':ids,'inside/executed-collection.json':ids,'inside/main-full-test-results.json':{'pytestExit':0,'collectedCount':1,'completedCount':1,'failures':[],'skipSetUnchanged':True,'syntheticControlNotFullProjectValidation':True}}
    def decode_control(label,values,lane="py312-cvm"):
        path=e/('reader-archive-control-'+label+'.zip')
        with zipfile.ZipFile(path,'w')as z:
            for name,value in values.items():z.writestr(name,value if isinstance(value,bytes)else json.dumps(value))
        identity={'expectedArchiveBytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'digestVerified':True,'verifiedCompleteArchive':True}
        return inspect_archive(path,identity,lane)
    fixture.update({'inside/candidate-format-proof.json':{'status':'PASS','blackVersion':'25.9.0','allPythonASTIdentical':True,'exactContributionPathUnionUnchanged':True,'afterHashes':{'synthetic.py':'same'},'syntheticControlNotProjectPass':True},'inside/baseline-feature-absence.json':{'actualBaselineModuleAbsent':True,'notMathematicalBugReproduction':True,'syntheticControlNotProjectPass':True},'inside/feature-results.json':{'status':'PASS','allCasesExecuted':True,'syntheticControlNotProjectPass':True},'inside/shipping-early-results.json':{'editableResidualProofPassed':True,'editableCacheControlsPassed':True,'backendProbeControlsPassed':True,'candidateWheelBuilt':True,'cleanConsumerIdentityPassed':True,'focusedAPIAndIndependentOraclePassed':True,'uvOfflineOnly':True,'wheelMatchesActualFormattedProductionBytes':True,'syntheticControlNotProjectPass':True}})
    import ast
    source=ast.parse(Path(__file__).with_name('build_consumer.py').read_text())
    literals={n.targets[0].id:n.value.value for n in source.body if isinstance(n,ast.Assign)and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)and isinstance(n.value,ast.Constant)and isinstance(n.value.value,str)}
    legacy=literals['LEGACY_I3_BACKEND_PROBE'];corrected=literals['CORRECTED_BACKEND_PROBE']
    argv={'beforeBackendImport':['/work/shipping/backend_probe.py','/work/src','/work/shipping/backend-requirements.json'],'afterBackendHook':['/work/shipping/backend_probe.py','egg_info'],'requirements':[],'destinationWritten':True}
    prefix='inside/shipping/'
    controls={'status':'PASS','realSetuptoolsBackendHookUsed':True,'priorI3IndexErrorReproduced':True,'missingOutputArgumentRejectedBeforeHook':True,'capturedBeforeAnyBackendImport':True,'requirementsFileWrittenAtCapturedOwnedPath':True,'requirementsStrictlyUnchangedAllowedSet':[],'actualArgvMutation':argv,'legacyProbeSHA256':hashlib.sha256(legacy.encode()).hexdigest(),'correctedProbeSHA256':hashlib.sha256(corrected.encode()).hexdigest(),'syntheticContractScaffoldingNotActualBackendHook':True}
    fixture.update({prefix+'backend-probe-controls.json':controls,prefix+'backend-requirements.json':[],prefix+'backend-requirements.argv.json':argv})
    # decode_control writes exact bytes for raw .py/.log members; JSON only for objects.
    fixture[prefix+'backend_probe_prior_I3_negative.py']=legacy.encode();fixture[prefix+'backend_probe.py']=corrected.encode()
    for index,(name,expected,text)in enumerate([('actual-prior-I3-hook-argv-negative',1,'IndexError: list index out of range'),('actual-corrected-probe-missing-output-negative',1,'PROBE_EXACT_SOURCE_OUTPUT_ARGS_REQUIRED'),('backend-source-record-and-hook',0,'synthetic success')],1):
        stem=prefix+f'{index:02d}-'+name;raw=text.encode();fixture[stem+'.log']=raw;fixture[stem+'.json']={'expected':expected,'returncode':expected,'error':None,'log_bytes':len(raw),'log_sha256':hashlib.sha256(raw).hexdigest()}
    known=[{'path':'/work/shipping/consumer-env/lib/python3.12/site-packages/__editable___bambi_finder.py','sha256':'a'*64,'bytes':123}]
    def synthetic_success(root,knownrows,removed):
        return {'status':'PASS','knownSources':knownrows,'cacheTag':'cpython-312','before':removed,'removed':removed,'after':[],'onlyKnownRegularPEP3147CachesRemoved':True,'legacyGuardWouldFail':bool(removed),'benignNoop':not removed,'syntheticContractScaffoldingNotActualConsumer':True}
    residual=synthetic_success('/work/shipping/consumer-env',known,[]);fixture[prefix+'consumer-residual-proof.json']=residual
    cachecontrols={'status':'PASS','sourceBytesFromRealRecordEditableFinder':known[0],'actualRelocatedInterpreterUsed':True,'primaryRealCloneUVUninstallStillRequired':True,'controls':[],'syntheticContractScaffoldingNotActualConsumer':True}
    index=30
    def synthetic_child(name,text,args):
        nonlocal index
        raw=text.encode();stem=prefix+f'{index:02d}-'+name;index+=1;fixture[stem+'.log']=raw
        fixture[stem+'.json']={'expected':0,'returncode':0,'error':None,'log_bytes':len(raw),'log_sha256':hashlib.sha256(raw).hexdigest(),'arguments':args}
    interpreter='/work/shipping/consumer-env/bin/python'
    compilecode='import hashlib,json,pathlib,py_compile,sys;p=pathlib.Path(sys.argv[1]);c=pathlib.Path(py_compile.compile(str(p),doraise=True));print(json.dumps(dict(source=str(p),sourceSHA256=hashlib.sha256(p.read_bytes()).hexdigest(),cache=str(c),cacheSHA256=hashlib.sha256(c.read_bytes()).hexdigest(),cache_tag=sys.implementation.cache_tag)))'
    for case,outcome in {'known-cache':'EXPECTED_SUCCESS','active-source':'EXPECTED_HOLD','symlink-cache':'EXPECTED_HOLD','foreign-source':'EXPECTED_HOLD','unknown-cache':'EXPECTED_HOLD','benign-noop':'EXPECTED_SUCCESS'}.items():
        root='/work/shipping/editable-cache-controls/'+case;source=root+'/__editable___bambi_finder.py';cache=root+'/__pycache__/__editable___bambi_finder.cpython-312.pyc'
        controlknown=[dict(known[0],path=source)]
        removed=[{'path':cache,'sha256':'b'*64,'bytes':456,'regular':True,'symlink':False,'classification':'KNOWN_RECORD_EDITABLE_PEP3147_CACHE'}]if case=='known-cache'else[]
        detail=synthetic_success(root,controlknown,removed)if outcome=='EXPECTED_SUCCESS'else{'status':'FAIL','removed':[],'syntheticContractScaffoldingNotActualConsumer':True}
        encoded=json.dumps(detail).encode();fixture[prefix+'editable-cache-control-'+case+'.json']=encoded
        cachecontrols['controls'].append({'case':case,'outcome':outcome,'proofSHA256':hashlib.sha256(encoded).hexdigest()})
        if case!='benign-noop':synthetic_child('editable-cache-control-compile-'+case,json.dumps({'source':source,'sourceSHA256':'a'*64,'cache':cache,'cacheSHA256':'b'*64,'cache_tag':'cpython-312'}),[interpreter,'-I','-B','-c',compilecode,source])
    fixture[prefix+'editable-cache-controls.json']=cachecontrols
    synthetic_child('consumer-relocation',json.dumps({'prefix':'/work/shipping/consumer-env','cache_tag':'cpython-312','paths':{'purelib':'/work/shipping/consumer-env/lib/python3.12/site-packages','platlib':'/work/shipping/consumer-env/lib/python3.12/site-packages','scripts':'/work/shipping/consumer-env/bin','data':'/work/shipping/consumer-env'}}),[interpreter,'-I','-c','synthetic relocation probe'])
    synthetic_child('consumer-uninstall-target-containment',json.dumps({'all_uninstall_targets_owned':True,'editable_sources':known}),[interpreter,'-I','/work/shipping/uninstall_targets.py','/work/shipping/consumer-env'])
    synthetic_child('consumer-remove-editable','synthetic uninstall success',['/work/shipping/uv','--offline','pip','uninstall','--python',interpreter,'bambi'])
    assert decode_control('actual-pytest-positive',fixture)['fullPass']
    wrong=dict(fixture);wrong.pop(prefix+'consumer-residual-proof.json');assert not decode_control('missing-editable-proof',wrong)['fullPass']
    for label,name,tag in [('unknown-removed-name','__editable___bambi_UNKNOWN.cpython-312.pyc','cpython-312'),('wrong-removed-tag','__editable___bambi_finder.cpython-313.pyc','cpython-312')]:
        mutated=json.loads(json.dumps(residual));row={'path':'/work/shipping/consumer-env/lib/python3.12/site-packages/__pycache__/'+name,'sha256':'b'*64,'bytes':456,'regular':True,'symlink':False,'classification':'KNOWN_RECORD_EDITABLE_PEP3147_CACHE'}
        mutated.update(before=[row],removed=[row],legacyGuardWouldFail=True,benignNoop=False,cacheTag=tag)
        wrong=dict(fixture);wrong[prefix+'consumer-residual-proof.json']=mutated
        try:decode_control(label,wrong)
        except AssertionError:failed.append(label)
        else:raise AssertionError('Tampered removed cache accepted')

    wrong=dict(fixture);wrong[prefix+'consumer-residual-proof.json']=dict(residual,status='FAIL')
    try:decode_control('failed-editable-proof',wrong)
    except AssertionError:failed.append('failed-editable-proof')
    else:raise AssertionError('Failed editable proof accepted')
    wrong=dict(fixture);wrong.pop(prefix+'backend-probe-controls.json');assert not decode_control('missing-backend-proof',wrong)['fullPass']
    wrong=dict(fixture);wrong[prefix+'backend-probe-controls.json']=dict(controls,status='FAIL')
    try:decode_control('failed-backend-proof',wrong)
    except AssertionError:failed.append('failed-backend-proof')
    else:raise AssertionError('Failed backend proof accepted')
    wrong=dict(fixture);wrong['inside/main-full-test-results.json']=dict(fixture['inside/main-full-test-results.json'],pytestExit=1,failures=[{'actualFailedFixture':True}])
    failReports=[json.loads(x)for x in(e/'fixture-reports.jsonl').read_text().splitlines()]
    assert any(x['outcome']=='failed'for x in failReports)
    assert not decode_control('failed-pytest',wrong)['fullPass']
    wrong=dict(fixture);wrong['inside/candidate-format-proof.json']=dict(fixture['inside/candidate-format-proof.json'],allPythonASTIdentical=False)
    assert not decode_control('semantic-format-change',wrong)['fullPass']
    wrong=dict(fixture);wrong['baseline-phase.json']=dict(fixture['baseline-phase.json'],confirmed_stopped=False)
    assert not decode_control('unconfirmed-stop',wrong)['allOwnedPhaseStopReceipts']
    actual_nb=e/'native-notebook-fixture.ipynb'
    if actual_nb.exists():
        nb=json.loads(actual_nb.read_text());code=[c for c in nb['cells']if c['cell_type']=='code']
        count=sum('image/png'in o.get('data',{})for c in code for o in c.get('outputs',[]))
        assert count>=1 and all(c['execution_count']is not None for c in code)
        docs=dict(fixture);docs['summary.json']=dict(fixture['summary.json'],lane='science-docs')
        docs.update({'inside/full-site-summary.json':{'fullRenderCompleted':True,'brokenLocalCount':0,'syntheticContractControlNotRealSite':True},'inside/candidate-nine-path-hooks.json':{'exit':0,'allFinalBytesUnchanged':True,'syntheticContractControlNotRealHooks':True},'inside/candidate-notebook-identity.json':{'codeCellsExecuted':len(code),'exactReviewedCellSourcesVerified':True,'outputPrivateDetailScanPassed':True,'actualPNGOutputs':9,'actualGenuineFitCallsCompleted':2,'actualFitDimensionChecksPassed':True,'actualFixtureNotebookPNGCount':count,'actualFixtureNotebookSHA256':hashlib.sha256(actual_nb.read_bytes()).hexdigest(),'syntheticContractScaffoldingNotRealCausalFitsOrNineContributionFigures':True}})
        assert decode_control('actual-jupyter-PNG-plus-synthetic-contract',docs,'science-docs')['fullPass']
        wrong=dict(docs);wrong['inside/candidate-notebook-identity.json']=dict(docs['inside/candidate-notebook-identity.json'],actualPNGOutputs=0)
        assert not decode_control('missing-PNG-output',wrong,'science-docs')['fullPass']
        wrong=dict(docs);wrong['inside/candidate-notebook-identity.json']=dict(docs['inside/candidate-notebook-identity.json'],actualGenuineFitCallsCompleted=0)
        assert not decode_control('missing-real-fits',wrong,'science-docs')['fullPass']
        wrong=dict(docs);wrong['inside/shipping-early-results.json']=dict(docs['inside/shipping-early-results.json'],cleanConsumerIdentityPassed=False)
        assert not decode_control('unclean-consumer',wrong,'science-docs')['fullPass']
    return {'status':'PASS','actualZIPValidationUsed':True,'actualAggregateContractUsed':True,'actualInstalledPytestEvidenceDecodedThroughZIP':True,'rejections':failed,'syntheticControlsOnlyNoScientificPass':True}
if __name__=='__main__':
    assert sys.argv[1:]==['fixtures']
    print(json.dumps(fixtures()))
