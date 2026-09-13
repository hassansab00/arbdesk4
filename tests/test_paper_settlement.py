import copy
import paper_settlement as settlement
import paper_worker
import pytest
from paper_settlement import verify

def data():
    band={'condition_id':'condition','token_yes':'yes','token_no':'no'}
    gamma={'conditionId':'condition','closed':True,'umaResolutionStatus':'resolved',
        'clobTokenIds':'["yes","no"]','outcomes':'["Yes","No"]','outcomePrices':'["0","1"]'}
    clob={'condition_id':'condition','closed':True,'accepting_orders':False,
        'tokens':[{'token_id':'yes','winner':False},{'token_id':'no','winner':True}]}
    return band,gamma,clob

def test_final_venue_winner_must_agree_across_both_sources():
    assert verify(*data())=='no'

@pytest.mark.parametrize('change',[{'closed':False},{'umaResolutionStatus':'proposed'}, {'outcomePrices':'["0.5","0.5"]'}])
def test_provisional_price_or_void_never_becomes_a_binary_payout(change):
    band,gamma,clob=data();gamma.update(change)
    assert verify(band,gamma,clob) is None

def test_wrong_condition_or_reversed_token_identity_blocks_payment():
    band,gamma,clob=data();gamma['conditionId']='other'
    with pytest.raises(ValueError,match='identity'):verify(band,gamma,clob)
    band,gamma,clob=data();gamma['clobTokenIds']='["no","yes"]'
    with pytest.raises(ValueError,match='identity'):verify(band,gamma,clob)

def test_conflicting_winner_is_not_silently_paid():
    band,gamma,clob=data();clob['tokens'][0]['winner']=True
    with pytest.raises(ValueError,match='disagree'):verify(band,gamma,clob)


def test_closed_markets_build_evidence_without_an_open_position(monkeypatch):
    band,gamma,clob=data()
    band.update({'band_id':'band-1','market_id':'market-1'})
    writes=[];settles=[];logs=[]

    def all_rows(path,params,**kwargs):
        if path=='paper_positions': return []
        if path=='paper_resolution_evidence': return []
        if path=='markets': return [{'market_id':'market-1','resolution_date':'2026-09-12'}]
        if path=='bands': return [band]
        raise AssertionError(path)

    def rows(path,params=None):
        raise AssertionError(path)

    def venue(url,params):
        return [gamma] if 'gamma-api' in url else clob

    monkeypatch.setattr(settlement,'rest_all',all_rows)
    monkeypatch.setattr(settlement,'rest',rows)
    monkeypatch.setattr(settlement,'upsert',lambda table,rows,key:writes.extend(rows) or len(rows))
    monkeypatch.setattr(settlement,'rpc',lambda fn,args:settles.append(args) or 0)
    monkeypatch.setattr(settlement,'log_run',lambda *args:logs.append(args))
    monkeypatch.setattr(paper_worker,'public_json',venue)

    result=settlement.cycle(budget_seconds=30)
    assert result['evidence_captured']==1
    assert result['positions_settled']==0
    assert writes[0]['winning_token']=='no'
    assert settles==[{'p_band':'band-1','p_proof':writes[0]['proof_id']}]
    assert logs[0][1]=='ok'
