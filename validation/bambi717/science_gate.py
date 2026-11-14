"""Real installed PyMC6 sampler/adaptation and split-ArviZ controls, gate only."""
import functools
import inspect
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pymc as pm
import bambi as bmb
import arviz_stats as azs
import xarray as xr
from pymc.step_methods.hmc.nuts import NUTS
from pymc.step_methods.hmc.base_hmc import BaseHMC
from pymc.step_methods.step_sizes import DualAverageAdaptation
from science import early
E=Path('/work/evidence')
early()
originals={}
records=[]
case={'name':None}
# Wrap the real constructor boundaries; call originals unchanged, never patch notebook.
def wrap(cls, name):
    original=cls.__init__;signature=inspect.signature(original);originals[cls]=original
    @functools.wraps(original)
    def capture(self,*args,**kwargs):
        bound=signature.bind(self,*args,**kwargs);bound.apply_defaults()
        row={'case':case['name'],'constructor':name,'actualClass':type(self).__name__}
        for field in ('target_accept','target'):
            if field in bound.arguments:row[field]=float(bound.arguments[field])
        result=original(self,*args,**kwargs)
        records.append(row)
        return result
    cls.__init__=capture
for cls,name in [(NUTS,'NUTS'),(BaseHMC,'BaseHMC'),(DualAverageAdaptation,'DualAverageAdaptation')]:wrap(cls,name)
rng=np.random.default_rng(601);data=pd.DataFrame({'x':rng.normal(size=16),'y':rng.normal(size=16)})
options=dict(draws=20,tune=20,chains=1,cores=1,random_seed=909,progressbar=False,compute_convergence_checks=False)
results=[]
def run(label,route,settings,required):
    case['name']=label;start=len(records);error=None;fit=None
    try:
        if route=='reference':
            with pm.Model():
                mu=pm.Normal('mu',0,1)
                pm.Normal('y',mu,1,observed=data['y'].to_numpy())
                fit=pm.sample(nuts_sampler='pymc',**options,**settings)
        else:
            model=bmb.Model('y ~ x',data,auto_scale=False,center_predictors=False)
            fit=model.fit(inference_method='pymc',**options,**settings)
    except Exception as exc:error={'type':type(exc).__name__,'message':str(exc)[:4096]}
    rows=records[start:]
    actual=[r for r in rows if r['constructor']=='BaseHMC' and r['actualClass']=='NUTS']
    adapt=[r for r in rows if r['constructor']=='DualAverageAdaptation']
    reached=bool(actual and adapt and all(r.get('target_accept')==.9 for r in actual) and all(r.get('target')==.9 for r in adapt))
    if required:
        assert error is None and reached,(label,error,rows)
        assert isinstance(fit,xr.DataTree)
        posterior=fit['posterior'].ds;stats=fit['sample_stats'].ds
        assert isinstance(posterior,xr.Dataset) and isinstance(stats,xr.Dataset)
        assert posterior.sizes['draw']==20 and posterior.sizes['chain']==1
        assert 'diverging' in stats
    if label=='misspelled-key':assert error is not None or not reached,'MISSPELLED_SETTING_REACHED_NUTS_TARGET'
    results.append({'case':label,'route':route,'error':error,'actualConstructors':rows,'target09ReachedRealNUTSAndAdaptation':reached,'fullScientificFit':False})
try:
    run('reference-positive','reference',{'nuts':{'target_accept':.9}},True)
    run('bambi-positive','bambi',{'nuts':{'target_accept':.9}},True)
    run('misspelled-key','reference',{'nuts':{'target_acceptr':.9}},False)
    run('old-top-level-informational','reference',{'target_accept':.9},False)
finally:
    for cls,original in originals.items():cls.__init__=original
    (E/'sampler-target-capture.json').write_text(json.dumps({'actualRealDependencyConstructors':True,'gateOnlyWrappersRestored':True,'cases':results,'constructorSources':{c.__name__:inspect.getsource(o) for c,o in originals.items()},'calibrationOnlyNotFullScience':True},indent=2)+'\n')
assert len(results)==4 and all(cls.__init__ is original for cls,original in originals.items())
# Real malformed group is not a posterior dataset; require missing group to reject.
try:xr.DataTree()['posterior'].ds
except KeyError:pass
else:raise AssertionError('Missing DataTree group accepted')
(E/'main-science-gate.json').write_text(json.dumps({'status':'PASS','actualPyMCNUTSAndAdaptationTarget09':True,'misspelledNegativeRecorded':True,'oldTopLevelBehaviourInformational':True,'DataTreeDsActual':True,'allPriorAndStatsEarlyControls':True,'noFullScientificPassClaim':True},indent=2)+'\n')
