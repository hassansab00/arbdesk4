import copy
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
