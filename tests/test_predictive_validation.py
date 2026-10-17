import datetime as dt
import predictive_validation as v

NOW=dt.datetime(2026,10,17,12,tzinfo=dt.timezone.utc)
CITIES=[dict(city_key='city',timezone='UTC',display_name='City')]


def call(id='first',**kw):
    return dict(dict(checkpoint_id=id,city_key='city',target_date='2026-10-16',checkpoint='noon',
        local_decision_time='2026-10-16T12:00:00',decided_at='2026-10-16T12:36:00Z',
        engine_version=id,probs={'a':.9,'b':.1}),**kw)


def outcome(id='first'):
    return dict(checkpoint_id=id,winner_band_id='b',settled_at='2026-10-17T01:00:00Z',banked_at='2026-10-17T02:00:00Z')


def test_original_miss_survives_perfect_later_engine_and_backfill_rejected():
    p=call(); later=call('later',decided_at='2026-10-16T12:55:00Z',probs={'a':.01,'b':.99})
    rows=v.collect([later,p],[outcome(),outcome('later')],[],CITIES,{},NOW)
    assert len(rows)==1 and rows[0]['metrics']['hit']==0
    for stamp in ['2026-10-16T11:59:00Z','2026-10-16T14:00:00Z','2026-10-17T02:00:00Z']:
        assert not v.collect([call(decided_at=stamp)],[outcome()],[],CITIES,{},NOW)
    assert not v.collect([call(frozen_at='2026-10-17T02:00:00Z')],[outcome()],[],CITIES,{},NOW)
    early=outcome();early['settled_at']='2026-10-16T12:00:00Z'
    assert not v.collect([p],[early],[],CITIES,{},NOW)[0]['settled']


def test_day_ahead_local_midnight_and_common_horizon_overall():
    da=dict(city_key='city',for_date='2026-10-16',called_at='2026-10-16T00:01:00Z',hit=True,predicted_pct=99)
    assert not v.collect([],[],[da],CITIES,{},NOW)
    da['called_at']='2026-10-15T23:59:00Z'
    rows=v.collect([],[],[da],CITIES,{},NOW)
    reports=v.reports(rows,CITIES,[],[],NOW)
    assert len(reports)==16
    overall=next(r for r in reports if r['checkpoint']=='overall' and r['scope']=='served')['report']
    assert overall['n']==0, 'one horizon cannot masquerade as complete overall coverage'
    assert next(r for r in reports if r['checkpoint']=='day_ahead' and r['scope']=='served')['report']['hits']==1


def test_missing_cities_are_retained_and_postpeak_does_not_inflate_overall():
    rows=[]
    for h in v.HORIZONS:
        rows.append(dict(city_key='city',target_date='2026-10-16',checkpoint=h,version='wxrd3:v3:x',settled=True,
            verified=True,top_prob=.9,metrics=dict(hit=int(h=='postpeak_1h'),log_loss=2,brier=1),baseline=None,raw=None))
    out=v.reports(rows,CITIES+[dict(city_key='empty',timezone='UTC')],[],[],NOW)
    overall=next(r['report'] for r in out if r['city_key']=='city' and r['checkpoint']=='overall' and r['scope']=='served')
    assert overall['hits']==0 and overall['n']==6 and overall['dates']==1
    assert all(r['report']['n']==0 for r in out if r['city_key']=='empty')
