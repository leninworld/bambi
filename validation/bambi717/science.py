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
        im=pm.Normal('im',0,10); iy=pm.Normal('iy',0,10)
        a=pm.Normal('a',0,10); b=pm.Normal('b',0,10); cp=pm.Normal('cprime',0,10)
        sm=pm.HalfCauchy('sigma_m',1); sy=pm.HalfCauchy('sigma_y',1)
        pm.Normal('m_likelihood',im+a*xd,sm,observed=md,dims='obs_id')
        pm.Normal('y_likelihood',iy+b*md+cp*xd,sy,observed=yd,dims='obs_id')
        pm.Deterministic('indirect effect',a*b)
        pm.Deterministic('total effect',a*b+cp)
    with pm.Model(coords=coords) as total:
        xd=pm.Data('_x',x,dims='obs_id')
        iy=pm.Normal('iy',0,1); c=pm.Normal('c',0,1); sy=pm.HalfCauchy('sigma_y',1)
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
    rv=model.named_vars[name]
    assert type(rv.owner.op).__name__.lower().startswith(distribution), (name,type(rv.owner.op).__name__)
    assert tuple(rv.eval().shape)==(), (name,'not scalar')
    actual=[np.asarray(v.eval()) for v in rv.owner.inputs[-2:]]
    for got,want in zip(actual,params):np.testing.assert_allclose(got,want,rtol=0,atol=0)

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
        for n in sigmas:prior(model,n,'halfcauchy',(0,1))
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

def posterior(result):
    p=result['posterior']
    return p.dataset if hasattr(p,'dataset') else p

def validate_fit(name,result):
    p=posterior(result)
    assert dict(p.sizes)['chain']==4 and dict(p.sizes)['draw']==1000
    assert np.isfinite(p.to_array().values).all()
    stats=result['sample_stats'];stats=stats.dataset if hasattr(stats,'dataset') else stats
    assert int(stats['diverging'].sum())==0,(name,'divergences')
    r=checked_stat('rhat',p);bulk=checked_stat('ess',p,method='bulk');tail=checked_stat('ess',p,method='tail')
    assert float(r.to_array().max())<1.01,(name,'Rhat')
    assert float(bulk.to_array().min())>=400 and float(tail.to_array().min())>=400,(name,'ESS')
    save(name+'-diagnostics.json',{'rhat':r.to_dict(),'essBulk':bulk.to_dict(),'essTail':tail.to_dict(),
        'hdi95':checked_stat('hdi',p,prob=.95).to_dict(),'divergences':0})
    return p

def run():
    started=time.monotonic();joint,total,bm,by,bt=early();fits={}
    with joint:fits['reference-joint']=pm.sample(**SETTINGS,target_accept=.9,random_seed=417)
    with total:fits['reference-total']=pm.sample(**SETTINGS,target_accept=.9,random_seed=419)
    for name,model,seed in [('native-mediator',bm,421),('native-outcome',by,431),('native-total',bt,433)]:
        fits[name]=model.fit(inference_method='pymc',**SETTINGS,random_seed=seed,nuts={'target_accept':.9})
    post={n:validate_fit(n,r) for n,r in fits.items()}
    r=post['reference-joint'];m=post['native-mediator'];y=post['native-outcome']
    native=xr.Dataset({'im':m['Intercept'],'iy':y['Intercept'],'sigma_m':m['sigma'],'sigma_y':y['sigma'],
        'a':m['x'],'b':y['m'],'cprime':y['x'],
        'indirect effect':m['x']*y['m'],'total effect':m['x']*y['m']+y['x']})
    pairs=[(n,r[n],native[n]) for n in ['a','b','cprime','indirect effect','total effect']]
    pairs.append(('total-only-c',post['reference-total']['c'],post['native-total']['x']))
    checks=[]
    for name,ref,got in pairs:
        er=float(checked_stat('mcse',ref,method='mean'));eg=float(checked_stat('mcse',got,method='mean'))
        tolerance=4*np.sqrt(er*er+eg*eg);delta=abs(float(ref.mean())-float(got.mean()))
        assert delta<=tolerance,(name,delta,tolerance)
        checks.append({'quantity':name,'meanDifference':delta,'fourCombinedMCSE':tolerance,
            'referenceHDI95':checked_stat('hdi',ref,prob=.95).to_dict(),
            'nativeHDI95':checked_stat('hdi',got,prob=.95).to_dict()})
    x,mediator,outcome=arrays();sns.pairplot(pd.DataFrame(dict(x=x,m=mediator,y=outcome)))
    plt.savefig(E/'simulation-pairplot.png',dpi=110);plt.close('all')
    pm.model_to_graphviz(joint).render(str(E/'original-joint-graph'),format='png',cleanup=True)
    for label,model in [('mediator',bm),('outcome',by),('total',bt)]:
        pm.model_to_graphviz(model.backend.model).render(str(E/('native-'+label+'-graph')),format='png',cleanup=True)
    # Full four-chain traces for every free original parameter, not a shortened diagnostic run.
    fig,axes=plt.subplots(len(r.data_vars),1,figsize=(10,2*len(r.data_vars)),squeeze=False)
    for ax,(name,da) in zip(axes[:,0],r.data_vars.items()):
        assert da.ndim==2
        for chain in range(4):ax.plot(da.isel(chain=chain),alpha=.55,lw=.5)
        ax.set_title(name)
    fig.tight_layout();fig.savefig(E/'original-traces.png',dpi=100);plt.close(fig)
    fig,axes=plt.subplots(len(native.data_vars),1,figsize=(10,2*len(native.data_vars)),squeeze=False)
    for ax,(name,da) in zip(axes[:,0],native.data_vars.items()):
        for chain in range(4):ax.plot(da.isel(chain=chain),alpha=.55,lw=.5)
        ax.set_title(name)
    fig.tight_layout();fig.savefig(E/'native-traces.png',dpi=100);plt.close(fig)
    sns.pairplot(pd.DataFrame({n:r[n].values.reshape(-1) for n in ['a','b','cprime','indirect effect','total effect']}),corner=True)
    plt.savefig(E/'original-posterior-pairs.png',dpi=100);plt.close('all')
    fig,axes=plt.subplots(1,3,figsize=(14,4))
    for ax,n in zip(axes,['cprime','indirect effect','total effect']):
        sns.kdeplot(r[n].values.reshape(-1),ax=ax,label='reference');sns.kdeplot(native[n].values.reshape(-1),ax=ax,label='Bambi');ax.axvline(0,color='gray');ax.set_title(n);ax.legend()
        h=checked_stat('hdi',r[n],prob=.95).values.ravel();ax.axvspan(float(h.min()),float(h.max()),alpha=.15)
    fig.savefig(E/'effects-hdi95.png',dpi=110);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4))
    sns.kdeplot(post['reference-total']['c'].values.reshape(-1),ax=ax,label='total-only Normal(0,1) priors')
    sns.kdeplot(r['total effect'].values.reshape(-1),ax=ax,label='mediation total Normal(0,10) priors')
    ax.legend();fig.savefig(E/'total-model-prior-contrast.png',dpi=110);plt.close(fig)
    save('science-full-results.json',{'status':'PASS','fiveFullFits':True,'chains':4,'draws':1000,'tune':4000,
        'targetAccept':.9,'seconds':time.monotonic()-started,'comparisons':checks,
        'sameArrayManifest':json.loads((E/'same-arrays.json').read_text()),
        'zeroPointMassClaim':False,'candidateNotebookOrProductPatchValidated':False})
    print('Five mathematical reference/native fits and complete diagnostic protocol passed.')

if __name__=='__main__':
    import sys
    early() if sys.argv[1:] == ['early'] else run()
