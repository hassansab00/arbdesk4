"""Synthetic calls: reporting must not re-plan history using a later clock."""
import copy
import datetime as dt

import pytest
import predictive_validation as v

UTC=dt.timezone.utc
CITY={'city_key':'fixture','timezone':'America/Los_Angeles'}
NOW=dt.datetime(2001,6,4,tzinfo=UTC)


def saved(checkpoint='prepeak_1h', **kw):
    return dict(dict(checkpoint_id='original',city_key='fixture',target_date='2001-06-02',
        checkpoint=checkpoint,local_decision_time='2001-06-02T12:40:00',
        decided_at='2001-06-02T20:10:00Z',frozen_at='2001-06-02T20:10:00Z',
        engine_version='synthetic',probs={'cold':.8,'warm':.2}),**kw)


@pytest.mark.parametrize('receipt',[True,False])
@pytest.mark.parametrize('checkpoint,local,received',[
    ('prepeak_2h','11:40','19:10'),('prepeak_1h','12:40','20:10'),('postpeak_1h','14:40','22:10')])
def test_original_schedule_survives_changed_or_missing_monthly_peak(receipt,checkpoint,local,received):
    p=saved(checkpoint,local_decision_time=f'2001-06-02T{local}:00',
        decided_at=f'2001-06-02T{received}:00Z',frozen_at=f'2001-06-02T{received}:00Z' if receipt else None)
    later=dict(p,checkpoint_id='later',decided_at=p['decided_at'].replace(':10:',':20:'),
        probs={'cold':.01,'warm':.99})
    original=copy.deepcopy(p)
    facts=[dict(checkpoint_id=x['checkpoint_id'],winner_band_id='warm',
        settled_at='2001-06-03T08:00:00Z',banked_at='2001-06-03T09:00:00Z') for x in [p,later]]
    results=[v.collect([later,p],facts,[],[CITY],clock,NOW) for clock in
        [{('fixture',6):15},{('fixture',6):11},{}]]
    assert results[0]==results[1]==results[2]
    assert len(results[0])==1 and results[0][0]['metrics']['hit']==0
    assert results[0][0]['verified'] is receipt
    assert p==original


@pytest.mark.parametrize('change',[
    {'frozen_at':'2001-06-02T19:39:59Z'},
    {'frozen_at':'2001-06-02T20:55:01Z'},
    {'frozen_at':'2001-06-03T08:00:00Z'},
    {'decided_at':'2001-06-02T20:11:00Z'},
    {'checkpoint':'unknown'},
    {'local_decision_time':'2001-06-01T12:40:00'},
    {'local_decision_time':'2001-06-02T12:40:01'},
    {'local_decision_time':'2001-06-02T12:40:00-07:00'},
    {'checkpoint':'noon','local_decision_time':'2001-06-02T12:40:00'},
])
def test_saved_schedule_does_not_accept_late_future_or_malformed_calls(change):
    assert not v.collect([saved(**change)],[],[],[CITY],{},NOW)


def test_saved_schedule_uses_city_dst_and_receipt_boundaries():
    city=dict(CITY,timezone='Europe/Paris')
    p=saved('prepeak_1h',target_date='2001-10-28',local_decision_time='2001-10-28T12:40:00',
        decided_at='2001-10-28T11:40:00Z',frozen_at='2001-10-28T11:40:00Z')
    now=dt.datetime(2001,10,30,tzinfo=UTC)
    assert len(v.collect([p],[],[],[city],{},now))==1
    p['frozen_at']='2001-10-28T12:55:00Z'
    assert len(v.collect([p],[],[],[city],{},now))==1
    p['frozen_at']='2001-10-28T12:55:01Z'
    assert not v.collect([p],[],[],[city],{},now)
