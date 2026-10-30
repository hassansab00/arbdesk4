import copy
import pytest
import market_rules as rules
import contract_inputs as ci

URL='https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw&date=2026-11-01T04:00:00Z&hours=27'
M=dict(market_id='m',city_key='nyc',resolution_date='2026-10-31',event_slug='highest-temperature-in-nyc-on-october-31-2026',rules_text=None)


def test_actual_event_identity_and_nonempty_rules_are_mandatory():
    e=dict(slug=M['event_slug'],description=URL,markets=[{'description':URL}])
    assert rules.event_rules([e],M['event_slug'])==URL
    for p in [[dict(e,slug='other')],[e,e],[dict(e,description='',markets=[])]]:
        with pytest.raises(ValueError):rules.event_rules(p,M['event_slug'])
    with pytest.raises(ValueError,match='Conflicting'):
        rules.event_rules([dict(e,markets=[{'description':URL},{'description':URL.replace('KLGA','KJFK')}])],M['event_slug'])


def test_due_rules_are_saved_before_annotation_and_known_text_is_never_refetched():
    original=copy.deepcopy(M)
    calls=[]
    def write(name,body):
        calls.append((name,body));return dict(stored=True,rules_text=body['p_rules'],received_at='2026-10-30T17:00:00Z')
    known=dict(M,market_id='known',rules_text='retained')
    out,detail=rules.recover([M,known],{('nyc','2026-10-31')},fetch_event=lambda m:URL,write=write)
    assert out[0]['observation_contract']==ci.CONTRACT and out[0]['contract_station']=='KLGA'
    assert out[0]['rules_received_at']=='2026-10-30T17:00:00Z'
    assert out[1]==known and M==original
    assert len(calls)==detail['stored']==1
    assert calls[0][0]=='record_missing_market_rules'


def test_unavailable_rules_remain_unknown_and_the_failure_is_recorded():
    def fail(m):raise ValueError('Gamma unavailable')
    out,detail=rules.recover([M],{('nyc','2026-10-31')},fetch_event=fail)
    assert out==[M] and detail['stored']==0 and len(detail['errors'])==1
    assert ci.with_rules(out[0],None)['observation_contract']=='unknown'


def test_no_future_or_other_city_market_requests_and_the_cap_is_explicit():
    markets=[dict(M,market_id=str(i),city_key=str(i)) for i in range(40)]
    seen=[]
    out,detail=rules.recover(markets,{(str(i),'2026-10-31') for i in range(40)},
        fetch_event=lambda m:seen.append(m['market_id']) or URL,
        write=lambda n,p:dict(stored=True,rules_text=URL))
    assert len(seen)==rules.MAX_MARKETS==detail['requested']
    assert detail['stored']==rules.MAX_MARKETS
    assert detail['deferred']==40-rules.MAX_MARKETS
