import datetime as dt
import copy
import pytest
import wx_city_calibration as c


def data():
    end=dt.date(2026,10,17)
    rows=[]
    for n in range(34):
        day=end-dt.timedelta(days=34-n)
        for city in range(10):
            rows.append(dict(city_key=str(city),target_date=str(day),model_version='wxrd3:v3:x',
                frozen_at=f'{day}T10:00:00Z',input_as_of=f'{day}T09:59:00Z',input_sha256=f'{city}-{day}',
                settled_at=f'{day+dt.timedelta(days=1)}T00:00:00Z',label_received_at=f'{day+dt.timedelta(days=1)}T00:01:00Z',
                timezone='UTC',winning_band_id='b',raw_probs={'a':.7,'b':.3},
                predictive_context=dict(local_hour=10,clim_peak_hour=14,band_ids=['a','b'],model_fit_through='2026-08-01')))
    return rows


def test_distribution_bounds_and_zeros():
    p=c.transform({'a':0,'b':.7,'c':.3},['a','b','c'],dict(temperature=1.1,tilt=.02))
    assert p['a']==0 and sum(p.values())==pytest.approx(1)
    assert c.metrics({'a':.8,'b':.2},'missing')['brier']==pytest.approx(1.68)
    with pytest.raises(ValueError): c.transform({'a':1},['x'],c.IDENTITY)
    with pytest.raises(ValueError): c.transform({'a':1},['a'],dict(temperature=3,tilt=0))
    assert c.phase(dict(local_hour=12,clim_peak_hour=12))=='near_peak'
    assert c.phase(dict(local_hour=10,clim_peak_hour=12))=='before_peak'
    assert c.phase(dict(local_hour=14,clim_peak_hour=12))=='after_peak'
    assert c.seasonal_weight('2025-12-31','2026-01-01')>.99


def test_fit_validates_exact_bounded_step_and_rerun_cannot_step_twice():
    rows=data(); states=c.fit(rows,list(map(str,range(10))),'wxrd3:v3:x','2026-10-17')
    state=next(s for s in states if s['city_key']=='0' and s['phase']=='before_peak')
    assert state['decision']=='applied' and state['params']['temperature']==1.05
    assert abs(state['params']['tilt'])<=.025 and state['validation']['gain_lower90']>0
    assert state['train_dates']==20 and state['validation_dates']==13
    again=c.fit(rows,['0'],'wxrd3:v3:x','2026-10-17',states)[0]
    assert again['params']==state['params'] and again['version']==state['version']
    assert again['decision']=='revalidated'
    assert c.usable(state,'2026-10-18','wxrd3:v3:x')
    assert not c.usable(state,'2026-10-22','wxrd3:v3:x')
    assert not c.usable(state,'2026-10-18','other')


def test_late_labels_model_overlap_and_late_freezes_never_train():
    rows=data(); good=c.eligible_rows(rows,dt.date(2026,10,17),'wxrd3:v3:x')
    assert len(good)==330
    for key,value in [('label_received_at','2026-10-18T00:00:00Z'),('frozen_at','2026-10-18T00:00:00Z'),('input_as_of','2026-10-18T00:00:00Z')]:
        bad=copy.deepcopy(rows[0]);bad[key]=value
        assert not c.eligible_rows([bad],dt.date(2026,10,17),'wxrd3:v3:x')
    bad=copy.deepcopy(rows[0]);bad['predictive_context']['model_fit_through']=bad['target_date']
    assert not c.eligible_rows([bad],dt.date(2026,10,17),'wxrd3:v3:x')
    states=c.fit([],['jeddah','madrid'],'wxrd3:v3:x','2026-10-17')
    assert len(states)==6 and all(s['params']==c.IDENTITY and s['decision']=='held' for s in states)
