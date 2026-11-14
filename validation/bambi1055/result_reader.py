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
    prefix='inside/shipping/';root=PurePosixPath('/work/shipping/consumer-env');interpreter=str(root/'bin/python')
    proof=object_read(z,rows,prefix+'consumer-residual-proof.json',True)
    controls=object_read(z,rows,prefix+'orphan-import-controls.json',True)
    if proof is None or controls is None:return {'passed':False,'reason':'MISSING_ORPHAN_OR_CLEAN_IMPORT_PROOF'}
    def hexsha(value):return isinstance(value,str)and re.fullmatch('[0-9a-f]{64}',value)is not None
    def child(name):
        found=[n for n in rows if n.startswith(prefix)and n.endswith('-'+name+'.json')];assert len(found)==1
        receipt=object_read(z,rows,found[0]);assert receipt['expected']==receipt['returncode']==0 and receipt['error']is None
        log=found[0][:-5]+'.log';raw=z.read(log);assert len(raw)==receipt['log_bytes']and hashlib.sha256(raw).hexdigest()==receipt['log_sha256']
        return receipt,raw
    def retained(detail,expected_root,tag,count=None):
        assert detail['status']=='PASS'and detail['root']==str(expected_root)and detail['cacheTag']==tag
        assert re.fullmatch('cpython-3[0-9]+',tag)and detail['noFilesDeleted']is True and detail['removed']==[]
        assert detail['allResidualsInertOrphanCaches']and detail['before']==detail['after']
        inventory=detail['before'];assert len(inventory)<=64 and len({x['path']for x in inventory})==len(inventory)
        assert detail['retainedCount']==len(inventory)and detail['benignNoop']is(not inventory)
        if count is not None:assert len(inventory)==count
        for row in inventory:
            p=PurePosixPath(row['path']);assert p.is_relative_to(expected_root)and '..'not in p.parts and p.parent.name=='__pycache__'
            match=re.fullmatch(r'(__editable___bambi_[A-Za-z0-9_]+_finder)\.'+re.escape(tag)+r'(?:\.opt-[012])?\.pyc',p.name);assert match
            assert row['derivedSource']==str(p.parent.parent/(match.group(1)+'.py'))and row['sourceAbsent']is True
            assert row['classification']=='INERT_PEP3147_ORPHAN_RETAINED'and row['regular']is True and row['symlink']is False
            assert hexsha(row['sha256'])and isinstance(row['bytes'],int)and 0<row['bytes']<=2*1024**2
    relocation_receipt,raw=child('consumer-relocation');relocation=json.loads(raw)
    assert relocation_receipt['arguments'][:3]==[interpreter,'-I','-c']and relocation['prefix']==str(root)
    for key in ('purelib','platlib','scripts','data'):assert PurePosixPath(relocation['paths'][key]).is_relative_to(root)
    tag=relocation['cache_tag'];retained(proof,root,tag)
    containment_receipt,raw=child('consumer-uninstall-target-containment');targets=json.loads(raw)
    assert containment_receipt['arguments']==[interpreter,'-I','/work/shipping/uninstall_targets.py',str(root)]
    assert targets['all_uninstall_targets_owned']and targets['editable_sources']
    uninstall_receipt,unused=child('consumer-remove-editable')
    assert uninstall_receipt['arguments']==['/work/shipping/uv','--offline','pip','uninstall','--python',interpreter,'bambi']
    probe=prefix+'consumer_absence_probe.py';assert probe in rows and hashlib.sha256(z.read(probe)).hexdigest()=='00ab85b717e35e182f1e2beb67740f6536a07772bacf22777976ed1b8bcd2329'
    def absent(name,extra='',finder=''):
        receipt,raw=child(name);observed=json.loads(raw)
        assert receipt['arguments']==[interpreter,'-I','-B','/work/shipping/consumer_absence_probe.py','/work/src',str(root),extra,finder]
        assert observed['consumerPrefix']==str(root)and observed['cacheTag']==tag and observed['sourceCheckout']=='/work/src'
        assert observed['metadataAbsent']is True and observed['metadataPath']is None and observed['bambiFindSpecNone']is True and observed['finderFindSpecNone']is True
        assert observed['bambiAlreadyImported']is False and observed['noEditableMetaPath']is True and observed['sourceCheckoutExcluded']is True
        assert observed['finderName']==finder and observed['extraSearchPath']==extra
        assert not any('__editable__'in x for x in observed['metaPath'])
        assert not any(PurePosixPath(x).is_relative_to('/work/src')for x in observed['sysPath']if x)
        return observed
    before=absent('consumer-before-wheel-import-absence');assert object_read(z,rows,prefix+'consumer-import-absence.json')==before
    known=targets['editable_sources'][0]
    assert controls['status']=='PASS'and controls['actualFinderSource']==known and controls['noCacheDeletion']and controls['actualRealPrimaryUVUninstallAndAbsentImportProofRequired']
    expected={k:('EXPECTED_SUCCESS'if k in ('orphan-current','orphan-old','benign-noop')else'EXPECTED_HOLD')for k in ('orphan-current','orphan-old','active-source','active-pth','legacy-top-level','symlink-cache','foreign-root','wrong-tag','unknown-kind','benign-noop')}
    assert len(controls['controls'])==10 and {r['case']:r['outcome']for r in controls['controls']}==expected
    compilecode='import hashlib,json,pathlib,py_compile,sys;p=pathlib.Path(sys.argv[1]);c=pathlib.Path(py_compile.compile(str(p),doraise=True));print(json.dumps(dict(source=str(p),sourceSHA256=hashlib.sha256(p.read_bytes()).hexdigest(),cache=str(c),cacheSHA256=hashlib.sha256(c.read_bytes()).hexdigest(),cache_tag=sys.implementation.cache_tag)))'
    for control in controls['controls']:
        case=control['case'];member=prefix+'orphan-import-control-'+case+'.json';assert member in rows
        raw=z.read(member);assert hashlib.sha256(raw).hexdigest()==control['proofSHA256']and control['allFilesRetainedUnchanged']is True
        detail=json.loads(raw);controlroot=PurePosixPath('/work/shipping/orphan-import-controls')/case
        if case in ('orphan-current','orphan-old','benign-noop'):retained(detail,controlroot,tag,0 if case=='benign-noop'else 1)
        else:assert detail['status']=='FAIL'and detail['removed']==[]and detail['noFilesDeleted']is True
        if case!='benign-noop':
            basename=PurePosixPath(known['path']).name if case!='orphan-old'else'__editable___bambi_older_version_finder.py'
            source=controlroot/basename;receipt,raw=child('orphan-control-compile-'+case);compiled=json.loads(raw)
            assert receipt['arguments']==[interpreter,'-I','-B','-c',compilecode,str(source)]
            assert compiled==control['compiled']and compiled['source']==str(source)and compiled['sourceSHA256']==known['sha256']and compiled['cache_tag']==tag
            cache=PurePosixPath(compiled['cache']);assert cache.parent==source.parent/'__pycache__'and cache.name==source.stem+'.'+tag+'.pyc'and hexsha(compiled['cacheSHA256'])
            if case in ('orphan-current','orphan-old'):
                assert detail['before'][0]['sha256']==compiled['cacheSHA256'];absent('orphan-control-import-'+case,str(controlroot),source.stem)
    # Batch the remaining install/identity/two-consumer-test raw chain too.
    install_receipt,unused=child('consumer-install-wheel');args=install_receipt['arguments']
    assert len(args)==9 and args[:8]==['/work/shipping/uv','--offline','pip','install','--python',interpreter,'--no-index','--no-deps'],'EXACT_OFFLINE_WHEEL_INSTALL_ARGUMENTS'
    wheel=PurePosixPath(args[-1]);assert wheel.parent==PurePosixPath('/work/shipping/wheel-output')and wheel.suffix=='.whl'
    identity_member=prefix+'consumer_identity.py';assert identity_member in rows and hashlib.sha256(z.read(identity_member)).hexdigest()=='75577c9d13372b55c5269bccabdade08c911a2155c77715838143d7ad36afb79'
    identity_receipt,raw=child('consumer-identity');installed=json.loads(raw)
    assert identity_receipt['arguments']==[interpreter,'-I','-B','/work/shipping/consumer_identity.py','/work/src',str(root),str(wheel)]
    assert installed['prefix']==str(root)and installed['version']and PurePosixPath(installed['bambi']).is_relative_to(root)and PurePosixPath(installed['causal']).is_relative_to(root)
    assert not any(PurePosixPath(x).is_relative_to('/work/src')for x in installed['sys_path']if x)
    focused_receipt,unused=child('consumer-focused-api-and-independent-oracle')
    testroot='/work/shipping/api-consumer-tests';expected_ids=['test_standardized_mean_and_ordered_dose_response', 'test_native_categorical_intervention_keeps_training_contrasts']
    assert focused_receipt['arguments']==[interpreter,'-I','-B','-m','pytest','-c','/dev/null','--rootdir',testroot,'--import-mode=importlib','-vv',*[testroot+'/test_causal.py::'+n for n in expected_ids]]
    return {'passed':True,'contractChangeExplicitWholeApprovalRequired':True,'retainedOrphanCount':len(proof['before']),'removedCount':0,'actualPreWheelImportAbsent':True,'rawControlCount':10,'actualCompileReceipts':9,'actualOrphanImportReceipts':2}

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
        common=bool(residual['passed'] and probe['passed'] and formatting and formatting['status']=='PASS' and formatting['blackVersion']=='25.9.0' and formatting['allPythonASTIdentical'] and formatting['exactContributionPathUnionUnchanged'] and baseline and baseline['actualBaselineModuleAbsent'] and baseline['notMathematicalBugReproduction'] and feature and feature['status']=='PASS' and feature['allCasesExecuted'] and shipping and shipping.get('cleanImportAbsenceProofPassed') is True and shipping.get('retainedOrphanProofPassed') is True and shipping.get('orphanImportControlsPassed') is True and shipping.get('backendProbeControlsPassed') is True and shipping['candidateWheelBuilt'] and shipping['cleanConsumerIdentityPassed'] and shipping['focusedAPIAndIndependentOraclePassed'] and shipping['uvOfflineOnly'] and shipping['wheelMatchesActualFormattedProductionBytes'])
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
    fixture.update({'inside/candidate-format-proof.json':{'status':'PASS','blackVersion':'25.9.0','allPythonASTIdentical':True,'exactContributionPathUnionUnchanged':True,'afterHashes':{'synthetic.py':'same'},'syntheticControlNotProjectPass':True},'inside/baseline-feature-absence.json':{'actualBaselineModuleAbsent':True,'notMathematicalBugReproduction':True,'syntheticControlNotProjectPass':True},'inside/feature-results.json':{'status':'PASS','allCasesExecuted':True,'syntheticControlNotProjectPass':True},'inside/shipping-early-results.json':{'cleanImportAbsenceProofPassed':True,'retainedOrphanProofPassed':True,'orphanImportControlsPassed':True,'backendProbeControlsPassed':True,'candidateWheelBuilt':True,'cleanConsumerIdentityPassed':True,'focusedAPIAndIndependentOraclePassed':True,'uvOfflineOnly':True,'wheelMatchesActualFormattedProductionBytes':True,'syntheticControlNotProjectPass':True}})
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
    known=[{'path':'/work/shipping/consumer-env/lib/python3.12/site-packages/__editable___bambi_0_21_1_dev7_gbabde99af_d20261114_finder.py','sha256':'a'*64,'bytes':123}]
    def synthetic_retained(root,stem=None):
        inventory=[]
        if stem:
            path=root+'/__pycache__/'+stem+'.cpython-312.pyc'
            inventory=[{'path':path,'sha256':'b'*64,'bytes':456,'regular':True,'symlink':False,'derivedSource':root+'/'+stem+'.py','sourceAbsent':True,'classification':'INERT_PEP3147_ORPHAN_RETAINED'}]
        return {'status':'PASS','root':root,'cacheTag':'cpython-312','before':inventory,'after':inventory,'removed':[],'noFilesDeleted':True,'allResidualsInertOrphanCaches':True,'retainedCount':len(inventory),'benignNoop':not inventory,'syntheticContractScaffoldingNotActualConsumer':True}
    residual=synthetic_retained('/work/shipping/consumer-env');fixture[prefix+'consumer-residual-proof.json']=residual
    cachecontrols={'status':'PASS','actualFinderSource':known[0],'noCacheDeletion':True,'actualRealPrimaryUVUninstallAndAbsentImportProofRequired':True,'controls':[],'syntheticContractScaffoldingNotActualConsumer':True}
    index=30
    def synthetic_child(name,value,args):
        nonlocal index
        raw=json.dumps(value).encode();stem=prefix+f'{index:02d}-'+name;index+=1;fixture[stem+'.log']=raw
        fixture[stem+'.json']={'expected':0,'returncode':0,'error':None,'log_bytes':len(raw),'log_sha256':hashlib.sha256(raw).hexdigest(),'arguments':args}
    interpreter='/work/shipping/consumer-env/bin/python'
    probe_source=next(ast.literal_eval(n.value)for n in source.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='CONSUMER_ABSENCE_PROBE'for t in n.targets))
    fixture[prefix+'consumer_absence_probe.py']=probe_source.encode()
    def synthetic_absence(name,extra='',finder=''):
        observed={'consumerPrefix':'/work/shipping/consumer-env','metadataAbsent':True,'metadataPath':None,'bambiFindSpecNone':True,'finderFindSpecNone':True,'finderName':finder,'extraSearchPath':extra,'cacheTag':'cpython-312','bambiAlreadyImported':False,'noEditableMetaPath':True,'sourceCheckoutExcluded':True,'sourceCheckout':'/work/src','sysPath':['/work/shipping/consumer-env/lib/python3.12/site-packages'],'metaPath':['synthetic ordinary finder']}
        synthetic_child(name,observed,[interpreter,'-I','-B','/work/shipping/consumer_absence_probe.py','/work/src','/work/shipping/consumer-env',extra,finder]);return observed
    fixture[prefix+'consumer-import-absence.json']=synthetic_absence('consumer-before-wheel-import-absence')
    compilecode='import hashlib,json,pathlib,py_compile,sys;p=pathlib.Path(sys.argv[1]);c=pathlib.Path(py_compile.compile(str(p),doraise=True));print(json.dumps(dict(source=str(p),sourceSHA256=hashlib.sha256(p.read_bytes()).hexdigest(),cache=str(c),cacheSHA256=hashlib.sha256(c.read_bytes()).hexdigest(),cache_tag=sys.implementation.cache_tag)))'
    for case in ('orphan-current','orphan-old','active-source','active-pth','legacy-top-level','symlink-cache','foreign-root','wrong-tag','unknown-kind','benign-noop'):
        outcome='EXPECTED_SUCCESS'if case in ('orphan-current','orphan-old','benign-noop')else'EXPECTED_HOLD'
        root='/work/shipping/orphan-import-controls/'+case;basename='__editable___bambi_older_version_finder'if case=='orphan-old'else'__editable___bambi_0_21_1_dev7_gbabde99af_d20261114_finder'
        detail=synthetic_retained(root,None if case=='benign-noop'else basename)if outcome=='EXPECTED_SUCCESS'else{'status':'FAIL','removed':[],'noFilesDeleted':True,'syntheticContractScaffoldingNotActualConsumer':True}
        encoded=json.dumps(detail).encode();fixture[prefix+'orphan-import-control-'+case+'.json']=encoded
        compiled=None
        if case!='benign-noop':
            src=root+'/'+basename+'.py';cache=root+'/__pycache__/'+basename+'.cpython-312.pyc';compiled={'source':src,'sourceSHA256':'a'*64,'cache':cache,'cacheSHA256':'b'*64,'cache_tag':'cpython-312'}
            synthetic_child('orphan-control-compile-'+case,compiled,[interpreter,'-I','-B','-c',compilecode,src])
            if case in ('orphan-current','orphan-old'):synthetic_absence('orphan-control-import-'+case,root,basename)
        cachecontrols['controls'].append({'case':case,'outcome':outcome,'proofSHA256':hashlib.sha256(encoded).hexdigest(),'allFilesRetainedUnchanged':True,'compiled':compiled})
    fixture[prefix+'orphan-import-controls.json']=cachecontrols
    synthetic_child('consumer-relocation',{'prefix':'/work/shipping/consumer-env','cache_tag':'cpython-312','paths':{'purelib':'/work/shipping/consumer-env/lib/python3.12/site-packages','platlib':'/work/shipping/consumer-env/lib/python3.12/site-packages','scripts':'/work/shipping/consumer-env/bin','data':'/work/shipping/consumer-env'}},[interpreter,'-I','-c','synthetic relocation probe'])
    synthetic_child('consumer-uninstall-target-containment',{'all_uninstall_targets_owned':True,'editable_sources':known},[interpreter,'-I','/work/shipping/uninstall_targets.py','/work/shipping/consumer-env'])
    synthetic_child('consumer-remove-editable',{'syntheticUninstallSuccess':True},['/work/shipping/uv','--offline','pip','uninstall','--python',interpreter,'bambi'])
    wheel='/work/shipping/wheel-output/bambi-0.21.1-py3-none-any.whl'
    synthetic_child('consumer-install-wheel',{'syntheticInstall':True},['/work/shipping/uv','--offline','pip','install','--python',interpreter,'--no-index','--no-deps',wheel])
    consumer_method=next(n for n in ast.walk(source)if isinstance(n,ast.FunctionDef)and n.name=='consumer')
    identity_code=next(ast.literal_eval(n.args[0])for n in ast.walk(consumer_method)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr=='write_text'and n.args and isinstance(n.args[0],ast.Constant)and 'import bambi.causal.effects'in str(n.args[0].value))
    fixture[prefix+'consumer_identity.py']=identity_code.encode()
    synthetic_child('consumer-identity',{'prefix':'/work/shipping/consumer-env','version':'synthetic','bambi':'/work/shipping/consumer-env/lib/python3.12/site-packages/bambi/__init__.py','causal':'/work/shipping/consumer-env/lib/python3.12/site-packages/bambi/causal/__init__.py','sys_path':['/work/shipping/consumer-env/lib/python3.12/site-packages']},[interpreter,'-I','-B','/work/shipping/consumer_identity.py','/work/src','/work/shipping/consumer-env',wheel])
    testroot='/work/shipping/api-consumer-tests';consumer_ids=['test_standardized_mean_and_ordered_dose_response', 'test_native_categorical_intervention_keeps_training_contrasts']
    synthetic_child('consumer-focused-api-and-independent-oracle',{'syntheticTwoSelectorExit':0},[interpreter,'-I','-B','-m','pytest','-c','/dev/null','--rootdir',testroot,'--import-mode=importlib','-vv',*[testroot+'/test_causal.py::'+n for n in consumer_ids]])
    assert decode_control('actual-pytest-positive',fixture)['fullPass']
    wrong=dict(fixture);wrong.pop(prefix+'consumer-residual-proof.json');assert not decode_control('missing-orphan-proof',wrong)['fullPass']
    wrong=dict(fixture);install_name=next(n for n in wrong if n.endswith('-consumer-install-wheel.json'));wrong.pop(install_name)
    try:decode_control('missing-wheel-install-receipt',wrong)
    except AssertionError:failed.append('missing-wheel-install-receipt')
    else:raise AssertionError('Missing actual install receipt accepted')

    for label,path in [('active-residual','/work/shipping/consumer-env/__editable___bambi_0_21_1_dev7_gbabde99af_d20261114_finder.py'),('legacy-pyc','/work/shipping/consumer-env/__editable___bambi_0_21_1_dev7_gbabde99af_d20261114_finder.pyc'),('wrong-tag','/work/shipping/consumer-env/__pycache__/__editable___bambi_0_21_1_dev7_gbabde99af_d20261114_finder.cpython-313.pyc')]:
        mutated=json.loads(json.dumps(residual));row={'path':path,'sha256':'b'*64,'bytes':456,'regular':True,'symlink':False,'derivedSource':'/work/shipping/consumer-env/__editable___bambi_0_21_1_dev7_gbabde99af_d20261114_finder.py','sourceAbsent':True,'classification':'INERT_PEP3147_ORPHAN_RETAINED'}
        mutated.update(before=[row],after=[row],retainedCount=1,benignNoop=False);wrong=dict(fixture);wrong[prefix+'consumer-residual-proof.json']=mutated
        try:decode_control(label,wrong)
        except AssertionError:failed.append(label)
        else:raise AssertionError('Invalid retained cache accepted')
    wrong=dict(fixture);wrong[prefix+'consumer-import-absence.json']=dict(fixture[prefix+'consumer-import-absence.json'],metadataAbsent=False)
    try:decode_control('active-distribution',wrong)
    except AssertionError:failed.append('active-distribution')
    else:raise AssertionError('Active distribution accepted')
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
