"""Proven bounded exact-run metadata anchor, once per hosted job before source download."""
import datetime,hashlib,json,os,re,subprocess,time,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent
plan=json.loads((P/'plan.json').read_text());run=os.environ['GITHUB_RUN_ID'];lane=os.environ['G0_LANE']
assert re.fullmatch(r'[1-9][0-9]*',run) and lane in plan['lanes']
assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
assert os.environ.get('GITHUB_REPOSITORY')=='leninworld/bambi' and os.environ.get('GITHUB_ACTOR')=='leninworld'
assert os.environ.get('GITHUB_EVENT_NAME')=='push' and os.environ.get('GITHUB_RUN_ATTEMPT')=='1'
assert re.fullmatch(r'[1-9][0-9]*',os.environ.get('GITHUB_RUN_NUMBER',''))
assert os.environ['GITHUB_REF']=='refs/heads/'+plan['branch'] and plan['execution_enabled']
head=subprocess.check_output(['git','-C',str(P),'rev-parse','HEAD'],timeout=5).decode().strip();assert head==os.environ['GITHUB_SHA']
base=Path(os.environ['RUNNER_TEMP'])/('bambi1055-main-clock-'+run+'-'+lane)
assert not base.with_suffix('.json').exists() and not base.with_suffix('.started.json').exists()
base.with_suffix('.started.json').write_text(json.dumps({'run':run,'lane':lane,'actualReadStarted':time.time(),'noRetry':True,'seconds':15,'bytes':262144})+'\n')
request=urllib.request.Request(f'https://api.github.com/repos/leninworld/bambi/actions/runs/{run}',headers={'Accept':'application/vnd.github+json','User-Agent':'validation-controller','Authorization':'Bearer '+os.environ['GH_TOKEN']})
with urllib.request.urlopen(request,timeout=15)as response:
 raw=response.read(262145);assert len(raw)<=262144
 data=json.loads(raw)
assert data['id']==int(run) and data['head_sha']==head and data['head_branch']==plan['branch']
assert data['actor']['login']=='leninworld' and data['run_attempt']==1 and data['event']=='push'
assert data['path']==plan['workflow_path']
created=datetime.datetime.fromisoformat(data['created_at'].replace('Z','+00:00')).timestamp()
assert created<=time.time()<created+plan['target_seconds']-plan['cleanup_reserve_seconds']
base.with_suffix('.raw.json').write_bytes(raw)
base.with_suffix('.json').write_text(json.dumps({'created_epoch':created,'rawSHA256':hashlib.sha256(raw).hexdigest(),'run':int(run),'lane':lane,'head':head,'branch':plan['branch'],'queueCounts':True,'deadline':created+3000,'hardDeadline':created+3600,'metadataCalls':1,'sameWorkflowAnchorAllJobs':True},indent=2)+'\n')
