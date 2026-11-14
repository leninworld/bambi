"""Diagnostic only: original mathematical models/current PyMC, no product overlay."""

import hashlib

import inspect

import json

import time

from pathlib import Path

import arviz_stats as azs

import bambi as bmb

import matplotlib.pyplot as plt

import numpy as np

import pandas as pd

import pymc as pm

import seaborn as sns

import xarray as xr

from prior_checks import assert_scalar_prior, prior_reader_controls

E = Path('/work/evidence')

SETTINGS = dict(draws=1000, tune=4000, chains=4, cores=4, progressbar=False)

def save(name, data):
    (E/name).write_text(json.dumps(data, indent=2, default=str)+'\n')

def arrays():
    path = E/'same-arrays.npz'
    if path.exists():
        with np.load(path, allow_pickle=False) as a:
            return tuple(a[n].copy() for n in ('x', 'm', 'y'))
    rng = np.random.default_rng(42)
    x = rng.normal(size=75)
    m = 2+rng.normal(loc=.5*x, scale=.5, size=75)
    y = .3*x+rng.normal(loc=.6*m, scale=.5, size=75)
    np.savez(path, x=x, m=m, y=y)
    save('same-arrays.json', {n:{'dtype':str(a.dtype), 'shape':a.shape,
         'sha256':hashlib.sha256(a.tobytes()).hexdigest()} for n,a in zip(('x','m','y'),(x,m,y))})
    return x,m,y

def build():
    x,m,y = arrays()
    coords={'obs_id':np.arange(75)}
    with pm.Model(coords=coords) as joint:
        # Declared API port only: ConstantData -> Data. Values are never changed.
        xd=pm.Data('x',x,dims='obs_id'); md=pm.Data('m',m,dims='obs_id')
        yd=pm.Data('y',y,dims='obs_id')
        im=pm.Normal('im',np.float64(0),np.float64(10)); iy=pm.Normal('iy',np.float64(0),np.float64(10))
        a=pm.Normal('a',np.float64(0),np.float64(10)); b=pm.Normal('b',np.float64(0),np.float64(10)); cp=pm.Normal('cprime',np.float64(0),np.float64(10))
        sm=pm.HalfCauchy('sigma_m',np.float64(1)); sy=pm.HalfCauchy('sigma_y',np.float64(1))
        pm.Normal('m_likelihood',im+a*xd,sm,observed=md,dims='obs_id')
        pm.Normal('y_likelihood',iy+b*md+cp*xd,sy,observed=yd,dims='obs_id')
        pm.Deterministic('indirect effect',a*b)
        pm.Deterministic('total effect',a*b+cp)
    with pm.Model(coords=coords) as total:
        xd=pm.Data('_x',x,dims='obs_id')
        iy=pm.Normal('iy',np.float64(0),np.float64(1)); c=pm.Normal('c',np.float64(0),np.float64(1)); sy=pm.HalfCauchy('sigma_y',np.float64(1))
        pm.Normal('yy',iy+c*xd,sy,observed=y,dims='obs_id')
    df=pd.DataFrame(dict(x=x,m=m,y=y))
    def native(formula, names, scale):
        priors={n:bmb.Prior('Normal',mu=0,sigma=scale) for n in names}
        priors['sigma']=bmb.Prior('HalfCauchy',beta=1)
        model=bmb.Model(formula,df,priors=priors,center_predictors=False,auto_scale=False)
        model.build()
        return model
    bm=native('m ~ x',['Intercept','x'],10)
    by=native('y ~ m + x',['Intercept','m','x'],10)
    bt=native('y ~ x',['Intercept','x'],1)
    return joint,total,bm,by,bt

def prior(model, name, distribution, params):
    parameters = {'mu': params[0], 'sigma': params[1]} if distribution == 'normal' else {'beta': params[0]}
    assert_scalar_prior(model, name, distribution, parameters)

def point(model, values):
    result=model.initial_point()
    for name,value in values.items():
        target=name+'_log__' if name.startswith('sigma') else name
        assert target in result,(target,sorted(result))
        result[target]=np.asarray(np.log(value) if name.startswith('sigma') else value)
    assert set(result)=={n+'_log__' if n.startswith('sigma') else n for n in values}
    return result

def checked_stat(name, data, **kwargs):
    f=getattr(azs,name)
    value=f(data, **kwargs)
    assert isinstance(value,(xr.Dataset,xr.DataArray)), (name,type(value))
    return value

def early():
    save('prior-reader-controls.json', prior_reader_controls())
    signatures={n:str(inspect.signature(getattr(azs,n))) for n in ('rhat','ess','mcse','hdi')}
    # Actual installed dependencies, realistic chain/draw success and malformed-dimension failure.
    rng=np.random.default_rng(991)
    d=xr.Dataset({'v':(('chain','draw'),rng.normal(size=(4,1000))),
        'indirect effect':(('chain','draw'),rng.normal(size=(4,1000)))})
    for name,kw in [('rhat',{}),('ess',{'method':'bulk'}),('ess',{'method':'tail'}),('mcse',{'method':'mean'}),('hdi',{'prob':.95})]:
        result=checked_stat(name,d,**kw)
        assert np.isfinite(result.to_array().values).all() if isinstance(result,xr.Dataset) else np.isfinite(result.values).all()
        assert isinstance(result.to_dict(),dict)
    array_mcse=float(checked_stat('mcse',d['v'],method='mean'))
    array_hdi=checked_stat('hdi',d['v'],prob=.95)
    assert np.isfinite(array_mcse) and np.isfinite(array_hdi.values).all()
    assert isinstance(array_hdi.to_dict(),dict)
    failed=False
    try:checked_stat('rhat',xr.Dataset({'v':('wrong',np.arange(10.))}))
    except (ValueError,TypeError,KeyError):failed=True
    assert failed,'diagnostics accepted missing chain/draw dimensions'
    models=build();joint,total,bm,by,bt=models
    import watermark
    pm.model_to_graphviz(joint).render('/tmp/bambi717-early-graph',format='png',cleanup=True)
    assert Path('/tmp/bambi717-early-graph.png').stat().st_size>0
    sns.pairplot(pd.DataFrame({'a':rng.normal(size=30),'b':rng.normal(size=30)}))
    plt.savefig('/tmp/bambi717-early-pairplot.png');plt.close('all')
    sns.kdeplot(rng.normal(size=100));plt.savefig('/tmp/bambi717-early-kde.png');plt.close('all')
    assert Path('/tmp/bambi717-early-pairplot.png').stat().st_size>0 and Path('/tmp/bambi717-early-kde.png').stat().st_size>0
    for model,names,scale in [(joint,['im','iy','a','b','cprime'],10),(total,['iy','c'],1),
       (bm.backend.model,['Intercept','x'],10),(by.backend.model,['Intercept','m','x'],10),(bt.backend.model,['Intercept','x'],1)]:
        for n in names:prior(model,n,'normal',(0,scale))
        sigmas=['sigma_m','sigma_y'] if model is joint else ['sigma_y'] if model is total else ['sigma']
        for n in sigmas:prior(model,n,'halfcauchy',(1,))
        assert {rv.name for rv in model.free_RVs}==set(names+sigmas)
    np.testing.assert_array_equal(joint['m'].get_value(),arrays()[1])
    np.testing.assert_array_equal(joint['x'].get_value(),arrays()[0])
    np.testing.assert_array_equal(joint['y'].get_value(),arrays()[2])
    # Compare complete joint densities, including transforms/Jacobians, at diverse fixed points.
    logps=[m.compile_logp() for m in [joint,total,bm.backend.model,by.backend.model,bt.backend.model]]
    rows=[]
    for im,iy,a,b,cp,sm,sy in [(2,0,.5,.6,.3,.5,.5),(1.2,-.8,-.2,1.1,.9,.7,1.2),(-1,2,1.5,-.7,-.2,1.1,.3)]:
        j=logps[0](point(joint,dict(im=im,iy=iy,a=a,b=b,cprime=cp,sigma_m=sm,sigma_y=sy)))
        m=logps[2](point(bm.backend.model,dict(Intercept=im,x=a,sigma=sm)))
        y=logps[3](point(by.backend.model,dict(Intercept=iy,m=b,x=cp,sigma=sy)))
        t=logps[1](point(total,dict(iy=iy,c=cp,sigma_y=sy)))
        nt=logps[4](point(bt.backend.model,dict(Intercept=iy,x=cp,sigma=sy)))
        np.testing.assert_allclose(j,m+y,rtol=1e-11,atol=1e-8)
        np.testing.assert_allclose(t,nt,rtol=1e-11,atol=1e-8)
        rows.append({'joint':float(j),'native_sum':float(m+y),'total':float(t),'native_total':float(nt)})
    save('early-scientific-graphs.json',{'status':'PASS','installedAPI':signatures,'logDensityControls':rows,
        'earlyActualPostfitControls':{'DataArrayFloatMCSE':array_mcse,'DataArrayHDIToDict':True,'multivariableSpaceNameToDict':True,'dotPNG':True,'watermarkImport':bool(watermark),'seabornPairplotKDEPNG':True},
        'fixedObservedMediator':True,'centering':False,'autoScale':False,'actualSampling':False,
        'apiPort':'ConstantData->Data; original model mathematical specification/likelihoods/priors unchanged'})
    return models

if __name__=='__main__': early()
