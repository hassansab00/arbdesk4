"""The seven sources learn only from older original calls whose labels arrived."""
import copy
import datetime as dt
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import ensemble_history as e


def record(day='2026-10-01',city='a',winner=1):
    return dict(city=city,date=day,checkpoint='noon',version='wxrd3:v3:fixed:awc-routine-v1:TEST',
        source_identity=city+'|TEST|awc-routine-v1|wxrd3:v3:fixed:awc-routine-v1:TEST',
        checkpoint_id=city+day,input_as_of=day+'T12:00:00Z',frozen_at=day+'T12:00:02Z',
        settled_at=day+'T23:59:00Z',label_received_at=day+'T23:59:01Z',winner=winner,
        row=dict(station='TEST',unit='C',n_bands=3,bands_lo=';20;22',bands_hi='20;22;',
                 current_models_sd_c=1.,**{'fc_'+m+'_c':21. if i==0 else 26. for i,m in enumerate(e.MODELS)}))


def history(city='a',winner=1):
    return [record(str(dt.date(2026,10,1)+dt.timedelta(days=i)),city,winner) for i in range(20)]


def test_closest_bucket_source_is_ranked_and_weighted_without_winner_take_all():
    state=e.fit(history());r=record('2026-10-25');c=e.context(r,state)
    assert c['ranking'][0]==e.MODELS[0]
    assert c['weights'][e.MODELS[0]]>c['weights'][e.MODELS[1]]
    assert sum(c['weights'].values())==pytest.approx(1)
    assert max(c['weights'].values())<=1/7+e.LEARNED_SHARE*(1-1/7)+1e-12
    assert c['local_dates']==20


def test_current_and_future_labels_never_change_earlier_weights():
    r=record('2026-10-15');before=e.context(r,e.fit(history()))
    changed=history()
    for old in changed:
        if old['date']>='2026-10-15':old['winner']=0
    assert e.context(r,e.fit(changed))==before
    r['winner']=0
    assert e.context(r,e.fit(history()))==before


def test_late_received_labels_and_wrong_contract_are_excluded():
    r=record('2026-10-25');rows=history()
    for old in rows:old['label_received_at']='2026-10-26T00:00:00Z'
    assert e.context(r,e.fit(rows))['local_dates']==0
    rows=history()
    for old in rows:old['version']='different';old['source_identity']='other'
    assert e.context(r,e.fit(rows))['pool_dates']==0


def test_each_city_has_its_own_rank_despite_shared_pool():
    state=e.fit(history('a',1)+history('b',2))
    a=e.context(record('2026-10-25','a'),state)
    b=e.context(record('2026-10-25','b'),state)
    assert a['weights'][e.MODELS[0]]>b['weights'][e.MODELS[0]]


def test_missing_forecasts_are_not_reconstructed_and_renormalize():
    r=record('2026-10-25');r['row']['fc_'+e.MODELS[0]+'_c']=None
    c=e.context(r,e.fit(history()))
    assert e.MODELS[0] not in c['weights'] and c['sources']==6
    assert sum(c['weights'].values())==pytest.approx(1)


def test_duplicate_calls_do_not_multiply_training_evidence():
    rows=history();new=copy.deepcopy(rows[0]);new['checkpoint_id']='later';new['frozen_at']='2026-10-01T13:00:00Z'
    assert e.fit(rows+[new])==e.fit(rows)
    bad=record();bad['settled_at']=bad['frozen_at']
    with pytest.raises(ValueError,match='chronology'):e.fit([bad])


def test_model_artifact_keeps_only_aggregates_and_refuses_pre_fit_decisions():
    rs=history();s=e.snapshot(rs,e.fit(rs));assert 'rows' not in s
    assert all(set(c)=={'source_losses','local_dates','pool_dates'} for c in s['cells'].values())
    out=e.context(record('2026-10-25'),s)
    assert out['weights'][e.MODELS[0]]>out['weights'][e.MODELS[1]]
    with pytest.raises(ValueError,match='unavailable'):e.context(record('2026-10-10'),s)


def test_sparse_source_history_keeps_uniform_prior():
    c=e.context(record('2026-10-25'),e.fit(history()[:3]))
    assert all(w==pytest.approx(1/7) for w in c['weights'].values())
