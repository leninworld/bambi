"""One measured-feature pricing rule, shared by hosted and inner full-suite guards."""
import math

def full_test_budget(historical_seconds,feature):
    elapsed=feature['elapsedSeconds'];ids=feature['collected']
    assert feature['status']=='PASS' and feature['allCasesExecuted']
    assert ids and len(ids)==len(set(ids))==feature['count']
    assert isinstance(elapsed,(int,float)) and not isinstance(elapsed,bool)
    assert math.isfinite(elapsed) and 0<elapsed<=420,'INVALID_ACTUAL_FEATURE_MEASUREMENT'
    assert isinstance(historical_seconds,(int,float)) and math.isfinite(historical_seconds) and historical_seconds>0
    seconds=math.ceil(1.5*historical_seconds+2*elapsed+60)
    return {'pytestSeconds':seconds,'entryFloorSeconds':seconds+180,'historicalOriginalSeconds':historical_seconds,'actualCompleteFeatureSeconds':elapsed,'actualFeatureCases':len(ids),'basis':'1.5x historical complete original +2x actual COMPLETE feature gate +60s; engineering predictor not proof; same-clock full entry guard mandatory'}

def minimum_shipping_entry(historical_seconds):
    assert isinstance(historical_seconds,(int,float)) and not isinstance(historical_seconds,bool)
    assert math.isfinite(historical_seconds) and historical_seconds>0
    return 300+420+math.ceil(1.5*historical_seconds+60)+180

def fixtures():
    row={'elapsedSeconds':120,'collected':['test.py::test_one'],'count':1,'status':'PASS','allCasesExecuted':True}
    assert full_test_budget(736,row)['pytestSeconds']==1404
    assert full_test_budget(736,row)['entryFloorSeconds']==1584
    assert minimum_shipping_entry(736)==2064
    assert minimum_shipping_entry(491)==1697
    for invalid in (0,-1,float('nan'),float('inf')):
        try:minimum_shipping_entry(invalid)
        except AssertionError:pass
        else:raise AssertionError('Invalid shipping reference accepted')
    rejected=[]
    for name,change in [('zero',{'elapsedSeconds':0}),('NaN',{'elapsedSeconds':float('nan')}),('infinite',{'elapsedSeconds':float('inf')}),('over-gate',{'elapsedSeconds':421}),('duplicate',{'collected':['x','x'],'count':2}),('incomplete',{'allCasesExecuted':False}),('failed',{'status':'FAIL'})]:
        try:full_test_budget(736,dict(row,**change))
        except AssertionError:rejected.append(name)
        else:raise AssertionError('Invalid full feature pricing accepted: '+name)
    return {'status':'PASS','actualSharedFunctionUsed':True,'rejected':rejected,'syntheticControlsNotProjectTimings':True}
