"""Bounded existing ZIP inspection and complete MAIN five-lane result contract; no extraction."""
import hashlib,io,json,stat,sys,unicodedata,zipfile
from pathlib import Path,PurePosixPath
LANES=('py312-cvm','py312-numba','py313-cvm','py313-numba','science-docs')
MAIN='babde99afb10a2b542e6c993074313ac2139f476'
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
        if lane!='science-docs':
            ids=object_read(z,rows,'inside/collected-nodeids.json',True);executed=object_read(z,rows,'inside/executed-collection.json',True);tests=object_read(z,rows,'inside/main-full-test-results.json',True)
            result.update(collectedNodeIDs=ids,executedNodeIDs=executed,tests=tests)
            result['fullPass']=bool(measured and stops and required and tests and ids and ids==executed and len(ids)==len(set(ids))==tests['collectedCount']==tests['completedCount'] and tests['pytestExit']==0 and not tests['failures'] and tests['skipSetUnchanged'] and not any(not x['intentional']for x in oom))
        else:
            gate=object_read(z,rows,'inside/main-science-gate.json',True);science=object_read(z,rows,'inside/science-full-results.json',True);site=object_read(z,rows,'inside/full-site-summary.json',True);hooks=object_read(z,rows,'inside/candidate-four-path-hooks.json',True);nb=object_read(z,rows,'inside/candidate-notebook-identity.json',True);visual=object_read(z,rows,'inside/candidate-visual-output-proof.json',True)
            result.update(gate=gate,science=science,site=site,hooks=hooks,notebook=nb,visual=visual)
            result['fullPass']=bool(measured and stops and required and gate and gate['status']=='PASS' and science and science['fiveFullFits'] and science['chains']==4 and science['draws']==1000 and science['tune']==4000 and site and site['fullRenderCompleted'] and site['brokenLocalCount']==0 and hooks and hooks['exit']==0 and hooks['allFinalBytesUnchanged'] and nb and nb['actualCandidateCells']==26 and nb['exactReviewedCellSourcesVerified'] and nb['kernelspec']=='python3' and nb['outputPrivateDetailScanPassed'] and visual and visual['status']=='PASS' and visual['requiredRasterPlots']==9 and visual['requiredModelGraphs']==4 and not any(not x['intentional']for x in oom))
        return result

def adjudicate(lanes):
    assert set(lanes)==set(LANES)
    anchors={r['workflowCreatedEpoch']for r in lanes.values()};assert len(anchors)==1,'SHARED_CLOCK_MISMATCH'
    sets=[set(lanes[n].get('collectedNodeIDs')or[])for n in LANES if n!='science-docs']
    same=bool(sets[0])and all(x==sets[0]for x in sets)
    return {'allFiveActualLanesPresent':True,'allFourCollectedNodeSetsEqual':same,'allOwnedPhasesStopped':all(r['allOwnedPhaseStopReceipts']for r in lanes.values()),'machineValidationPassed':same and all(r['fullPass']for r in lanes.values()),'actualCurrentMainCollectedCount':len(sets[0])if same else None,'freshVisualReview':'PENDING_HUMAN_AGENT_ACTUAL_IMAGE_INSPECTION','exactFinalDiffAndClaudeTestedPatchReview':'PENDING','noContributionPRorCommentAction':True}

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
    good={n:{'workflowCreatedEpoch':1,'collectedNodeIDs':['tests/test_public.py::test_ok'],'allOwnedPhaseStopReceipts':True,'fullPass':True}for n in LANES}
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
    def decode_control(label,values):
        path=e/('reader-archive-control-'+label+'.zip')
        with zipfile.ZipFile(path,'w')as z:
            for name,value in values.items():z.writestr(name,json.dumps(value))
        identity={'expectedArchiveBytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'digestVerified':True,'verifiedCompleteArchive':True}
        return inspect_archive(path,identity,'py312-cvm')
    assert decode_control('actual-pytest-positive',fixture)['fullPass']
    wrong=dict(fixture);wrong['inside/main-full-test-results.json']=dict(fixture['inside/main-full-test-results.json'],pytestExit=1,failures=[{'actualFailedFixture':True}])
    failReports=[json.loads(x)for x in(e/'fixture-reports.jsonl').read_text().splitlines()]
    assert any(x['outcome']=='failed'for x in failReports)
    assert not decode_control('failed-pytest',wrong)['fullPass']
    wrong=dict(fixture);wrong['baseline-phase.json']=dict(fixture['baseline-phase.json'],confirmed_stopped=False)
    assert not decode_control('unconfirmed-stop',wrong)['allOwnedPhaseStopReceipts']
    return {'status':'PASS','actualZIPValidationUsed':True,'actualAggregateContractUsed':True,'actualInstalledPytestEvidenceDecodedThroughZIP':True,'rejections':failed,'syntheticControlsOnlyNoScientificPass':True}
if __name__=='__main__':
    assert sys.argv[1:]==['fixtures']
    print(json.dumps(fixtures()))
