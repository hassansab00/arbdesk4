import copy
import datetime as dt
from decimal import Decimal
import pytest
from paper_plans import prepare, command_key

NOW=dt.datetime(2026,9,12,12,tzinfo=dt.timezone.utc)

def fixture():
    account={'account_id':'desk','cash':100,'reserved_cash':0,'policy':{'cities':['london'],'max_plan_usd':10,'min_edge':'.03'}}
    inputs={bid:{'unit':'C','model_prob_yes':.45,'decision_evidence':{'band':{'band_id':bid,'band_label':f'{20+i}°C','band_lo':20+i,'band_hi':21+i,'token_yes':bid,'token_no':'no-'+bid},
        'market':{'city_key':'london','unit':'C','resolution_date':'2026-09-12'},'forecast':{'for_date':'2026-09-12','run_at':'2026-09-12T06:00:00Z'}}} for i,bid in enumerate(['a','b'])}
    signal={'signal_id':1,'band_id':'a','side':'YES','payload':{'cycle_id':'cycle','basket_group':'a:b','band_ids':['a','b'],'decision_at':NOW.isoformat(),'decision_inputs':inputs}}
    def capture(order):
        return {'token_id':order['token_id'],'observed_at':NOW.isoformat(),'tradeable':True,'snapshot_id':order['token_id'],
            'tick_size':'.01','fee_rate':'.05','min_order_size':1,'min_order_size_unit':'USDC',
            'asks':[{'price':'.30','size':'1000'}],'bids':[{'price':'.29','size':'1000'}]}
    return account,signal,capture

def test_basket_reserves_fees_with_equal_shares_and_keeps_original_decision():
    account,signal,capture=fixture()
    legs,evidence=prepare(account,signal,capture,now=NOW)
    assert legs[0]['shares']==legs[1]['shares']
    assert sum(Decimal(x['cash_ceiling']) for x in legs)<=10
    assert evidence['signal']==signal and len(evidence['quotes'])==2
    assert Decimal(evidence['net_edge_per_share'])==Decimal('.279')

def test_two_separate_leg_signals_share_one_account_command():
    account,signal,_=fixture()
    second=copy.deepcopy(signal);second.update(signal_id=2,band_id='b')
    assert command_key(account,signal)==command_key(account,second)
    assert command_key(account,signal)!=command_key({**account,'account_id':'other'},signal)

@pytest.mark.parametrize('change',[{'unit':'F'},{'model_prob_yes':None},{'model_prob_yes':-1}])
def test_bad_units_or_missing_probability_block_proposals(change):
    account,signal,capture=fixture();signal['payload']['decision_inputs']['a'].update(change)
    with pytest.raises((ValueError,TypeError,ArithmeticError)):
        prepare(account,signal,capture,now=NOW)

def test_stale_matching_forecast_cannot_authorize_new_entry():
    account,signal,capture=fixture()
    signal['payload']['decision_inputs']['b']['decision_evidence']['forecast']['run_at']='2026-09-10T06:00:00Z'
    with pytest.raises(ValueError,match='forecast'):
        prepare(account,signal,capture,now=NOW)

def test_no_complete_depth_never_presents_fully_executable_basket():
    account,signal,capture=fixture()
    def thin(order):
        book=capture(order);book['asks'][0]['size']='1';return book
    with pytest.raises(ValueError,match='not executable'):
        prepare(account,signal,thin,now=NOW)
