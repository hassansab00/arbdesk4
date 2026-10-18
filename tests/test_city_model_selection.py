import datetime as dt
import copy

import city_model_selection as s

UTC=dt.timezone.utc
NOW=dt.datetime(2026,10,18,8,tzinfo=UTC)
VERSION='wxrd3:v3:test'


def pair(day, **kw):
    day=dt.date.fromisoformat(str(day))
    return dict(dict(city_key='city',target_date=str(day),checkpoint='prepeak_1h',timezone='UTC',
        model_version=VERSION,frozen_at=f'{day}T12:00:10Z',input_as_of=f'{day}T12:00:00Z',
        settled_at=f'{day+dt.timedelta(days=1)}T00:01:00Z',
        label_received_at=f'{day+dt.timedelta(days=1)}T00:02:00Z',input_sha256=str(day),
        raw_probs={'a':.8,'b':.2},baseline_probs={'a':.2,'b':.8},winning_band_id='b',
        predictive_context=dict(model_fit_through='2026-01-01',band_ids=['a','b'])),**kw)


def history():
    # Twenty older training dates; ten strictly later validation dates.
    return [pair(dt.date(2026,9,1)+dt.timedelta(days=i)) for i in range(20)]+[
        pair(dt.date(2026,10,7)+dt.timedelta(days=i)) for i in range(10)]


def cell(states,city='city',horizon='prepeak_1h'):
    return next(x for x in states if x['city_key']==city and x['checkpoint']==horizon)


def test_only_matched_original_causal_calls_enter_each_city_horizon():
    p=pair('2026-10-10'); later=dict(p,frozen_at='2026-10-10T12:02:00Z')
    assert s.eligible_rows([later,p],NOW.date(),VERSION)==[p]
    bad=[dict(p,model_version='other'),dict(p,checkpoint='morning'),
        dict(p,winning_band_id=None),
        dict(p,input_as_of='2026-10-10T13:00:00Z'),dict(p,frozen_at='2026-10-11T00:00:00Z'),
        dict(p,label_received_at='2026-10-18T00:00:00Z'),dict(p,settled_at=p['frozen_at']),
        dict(p,baseline_probs={'c':1}),dict(p,raw_probs={'a':float('nan'),'b':1}),
        dict(p,predictive_context=dict(p['predictive_context'],model_fit_through='2026-10-10'))]
    assert not s.eligible_rows(bad,NOW.date(),VERSION)
    # A winner outside both saved ladders counts as a miss for both, not a dropped hard day.
    outside=dict(p,winning_band_id='outside')
    assert s.eligible_rows([outside],NOW.date(),VERSION)
    assert s.score([outside],'wxrd3')['hit']==0


def test_thin_dates_hold_the_authorized_prior_for_every_city_and_horizon():
    states=s.fit([pair('2026-10-10')],['city','empty'],VERSION,NOW)
    assert len(states)==2*len(s.HORIZONS)
    for state in states:
        assert state['selected']==s.prior(state['checkpoint']) and state['decision']=='held'
        assert not s.usable(state,state['city_key'],state['checkpoint'],VERSION,NOW.date(),NOW)


def test_training_choice_must_win_unseen_dates_then_only_that_cell_switches():
    states=s.fit(history(),['city','empty'],VERSION,NOW)
    state=cell(states)
    assert state['selected']=='baseline' and state['decision']=='switched'
    assert state['train_dates']==20 and state['validation_dates']==10
    assert state['train_through']<state['validation_from']
    assert s.choose(state,'city','prepeak_1h',VERSION,NOW.date(),NOW)['selected']=='baseline'
    assert cell(states,'empty')['decision']=='held'
    assert cell(states,horizon='noon')['decision']=='held'
    assert not s.usable(state,'other','prepeak_1h',VERSION,NOW.date(),NOW)
    assert not s.usable(state,'city','noon',VERSION,NOW.date(),NOW)
    assert not s.usable(state,'city','prepeak_1h','new-version',NOW.date(),NOW)
    assert not s.usable(state,'city','prepeak_1h',VERSION,NOW.date(),NOW-dt.timedelta(seconds=1))
    assert not s.usable(state,'city','prepeak_1h',VERSION,dt.date(2026,10,22),NOW+dt.timedelta(days=4))


def test_winning_training_and_losing_later_dates_cannot_promote():
    rows=history()
    for r in rows[-10:]:r['winning_band_id']='a'
    state=cell(s.fit(rows,['city'],VERSION,NOW))
    assert state['selected']=='wxrd3' and not state['validation']['passed']


def test_same_night_cannot_reverse_an_already_validated_switch():
    first=cell(s.fit(history(),['city'],VERSION,NOW))
    reverse=copy.deepcopy(history())
    for r in reverse:r['winning_band_id']='a'
    second=cell(s.fit(reverse,['city'],VERSION,NOW+dt.timedelta(hours=1),[first]))
    assert second['anchor']=='wxrd3' and second['selected']=='baseline'
    assert 'switch limit' in second['reason']
    assert second['version']==first['version']


def test_log_loss_gain_does_not_overrule_hit_or_brier_regression():
    rows=history()[-10:]
    for i,r in enumerate(rows):
        r['raw_probs']={'a':.4,'b':.6} if i<6 else {'a':.9999,'b':.0001}
        r['baseline_probs']={'a':.51,'b':.49}
        r['winning_band_id']='b'
    evidence=s.validation(rows,'baseline','wxrd3')
    assert evidence['gain_lower95']>0
    assert evidence['candidate']['hit']<evidence['incumbent']['hit'] and not evidence['passed']
    for r in rows:
        r['raw_probs']={'a':.35,'b':.3,'c':.35}
        r['baseline_probs']={'a':.65,'b':.35,'c':0}
    evidence=s.validation(rows,'baseline','wxrd3')
    assert evidence['gain_lower95']>0 and evidence['candidate']['hit']==evidence['incumbent']['hit']
    assert evidence['candidate']['brier']>evidence['incumbent']['brier'] and not evidence['passed']


def test_live_horizon_uses_city_clock_and_a_measured_peak_capture_window():
    assert s.horizon_at('2026-10-18','Asia/Riyadh',12,dt.datetime(2026,10,18,8,36,tzinfo=UTC))=='prepeak_1h'
    assert s.horizon_at('2026-10-18','Asia/Riyadh',12,dt.datetime(2026,10,18,9,36,tzinfo=UTC))=='noon'
    assert s.horizon_at('2026-10-18','Asia/Riyadh',12,dt.datetime(2026,10,18,15,tzinfo=UTC)) is None
    assert s.horizon_at('2026-10-18','Asia/Riyadh',None,dt.datetime(2026,10,18,8,36,tzinfo=UTC)) is None
