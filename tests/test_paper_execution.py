import datetime as dt
import pytest
from paper_execution import simulate

NOW=dt.datetime(2026,9,8,12,tzinfo=dt.timezone.utc)

def order(**changes):
    return {'token_id':'yes','action':'BUY','shares':'10','limit_price':'.55',
        'share_step':'.01','max_book_age_seconds':120,'expires_at':'2026-09-08T12:05:00Z',**changes}

def book(**changes):
    return {'token_id':'yes','snapshot_id':'real-capture-test','observed_at':'2026-09-08T11:59:30Z',
        'tick_size':'.01','min_order_size':'1','min_order_size_unit':'USDC','fee_rate':'.05',
        'tradeable':True,'asks':[{'price':'.50','size':'4'},{'price':'.55','size':'9'}],
        'bids':[{'price':'.48','size':'8'}],**changes}

def test_walks_depth_and_charges_each_level_once():
    r=simulate(order(),book(),now=NOW)
    assert r['status']=='filled'
    assert r['notional']=='5.30'
    assert r['fee']=='0.12425'
    assert [x['shares'] for x in r['fills']]==['4','6']

def test_partial_within_limit_cancels_remainder():
    r=simulate(order(limit_price='.50'),book(),now=NOW)
    assert (r['status'],r['shares'])==('partial','4')

def test_sell_uses_bid_not_ask():
    r=simulate(order(action='SELL',limit_price='.45'),book(),now=NOW)
    assert r['shares']=='8' and r['notional']=='3.84'

@pytest.mark.parametrize('changes',[{'observed_at':'2026-09-08T11:00:00Z'},
    {'observed_at':'2026-09-08T12:00:01Z'},{'observed_at':'2026-09-08T11:59:30'},
    {'token_id':'no'},{'tradeable':False},{'fee_rate':None},{'asks':3},
    {'asks':[{'price':'NaN','size':10}]},{'asks':[{'price':'.50','size':-5}]}])
def test_unusable_evidence_never_fills(changes):
    r=simulate(order(),book(**changes),now=NOW)
    assert r['status']=='rejected' and r['fills']==[]

def test_expired_approval_cannot_use_old_price():
    assert simulate(order(expires_at=NOW.isoformat()),book(),now=NOW)['status']=='expired'

def test_consumed_snapshot_does_not_replenish():
    r=simulate(order(limit_price='.50'),book(),now=NOW,consumed={'0.50':'4'})
    assert r['shares']=='0'

def test_minimum_uses_documented_unit():
    assert simulate(order(shares='2'),book(min_order_size='2'),now=NOW)['status']=='rejected'
    assert simulate(order(shares='2'),book(min_order_size='2',min_order_size_unit='shares'),now=NOW)['status']=='filled'

def test_quantity_and_price_must_respect_increments():
    assert simulate(order(shares='1.005'),book(),now=NOW)['status']=='rejected'
    assert simulate(order(limit_price='.501'),book(),now=NOW)['status']=='rejected'
