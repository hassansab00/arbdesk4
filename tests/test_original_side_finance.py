"""Independent cash arithmetic and original-bucket side invariants (synthetic)."""
import datetime as dt
from decimal import Decimal

import pytest

from original_side_finance import budget_order, derived_no_scenario, quote_metadata, settled_pnl

NOW = dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)


def book(price='0.4', size='100', rate='0'):
    return dict(token_id='own-side-token', quote_kind='direct', observed_at=NOW.isoformat(),
        received_at=NOW.isoformat(), snapshot_id='synthetic-private-book', tradeable=True,
        asks=[dict(price=price, size=size)], bids=[], fee_rate=rate, tick_size='.001',
        share_step='.01', min_order_size='5', min_order_size_unit='shares')


def test_original_bucket_hit_and_miss_do_not_rewrite_prediction():
    _, yes = budget_order(book(), '10', now=NOW)
    _, no = budget_order(book('.65'), '10', now=NOW)
    assert Decimal(yes['shares']) == 25
    assert Decimal(yes['profit_if_win']) == 15
    assert Decimal(yes['loss_if_lose']) == -10
    assert Decimal(no['shares']) == Decimal('15.38')
    assert Decimal(no['filled_cost']) == Decimal('9.997')
    assert Decimal(no['unspent_allocation']) == Decimal('.003')
    assert Decimal(settled_pnl(no, 1)) == Decimal('5.383')
    assert Decimal(settled_pnl(no, 0)) == Decimal('-9.997')
    assert settled_pnl(yes, None) is None
    assert Decimal(settled_pnl(yes, '.5')) == Decimal('2.5')


def test_fee_inclusive_and_multilevel_cost_are_independent_arithmetic():
    _, result = budget_order(book(rate='.05'), '10', now=NOW)
    # 24.27 * .40 + round(24.27*.05*.40*.60, 5) = 9.99924.
    assert Decimal(result['shares']) == Decimal('24.27')
    assert Decimal(result['filled_cost']) == Decimal('9.99924')
    b = book(size='10'); b['asks'].append(dict(price='.65', size='100'))
    _, result = budget_order(b, '10', now=NOW)
    assert Decimal(result['shares']) == Decimal('19.23')
    assert Decimal(result['filled_cost']) == Decimal('9.9995')
    assert sum(Decimal(f['fee']) for f in result['fills']) == 0


def test_partial_depth_leaves_cash_and_unknown_evidence_is_blocked():
    _, result = budget_order(book(size='5'), '10', now=NOW)
    assert Decimal(result['filled_cost']) == 2
    assert Decimal(result['unspent_allocation']) == 8
    for changes, reason in [({'quote_kind':'derived_scenario'}, 'direct_token'),
        ({'tradeable':False}, 'tradeable'), ({'asks':[]}, 'depth'),
        ({'observed_at':(NOW-dt.timedelta(seconds=121)).isoformat()}, 'stale'),
        ({'asks':[{'price':'.4005','size':'10'}]}, 'level')]:
        with pytest.raises(ValueError, match=reason):
            budget_order({**book(), **changes}, '10', now=NOW)
    with pytest.raises((KeyError, ValueError)):
        bad=book();del bad['fee_rate'];budget_order(bad,'10',now=NOW)
    with pytest.raises(ValueError, match='minimum'):
        budget_order(book(size='4.99'),'10',now=NOW)


def test_minimum_is_book_shares_and_fee_profile_is_explicit():
    raw={'min_order_size':'7.5','tick_size':'0.001'}
    meta=quote_metadata(raw,{'feesEnabled':False,'orderMinSize':'999'})
    assert meta['min_order_size']=='7.5' and meta['min_order_size_unit']=='shares'
    assert meta['share_step']=='.01' or meta['share_step']=='0.01'
    with pytest.raises(ValueError, match='unverified'):
        quote_metadata({'tick_size':'.00001','min_order_size':'5'},{'feesEnabled':False})
    with pytest.raises(ValueError, match='fee_schedule'):
        quote_metadata(raw,{'feesEnabled':True})


def test_derived_is_opt_in_bid_only_and_never_execution_evidence():
    saved=dict(best_bid='.35',best_ask='.40',received_at=NOW.isoformat(),observed_at=NOW.isoformat())
    assert derived_no_scenario(saved) is None
    scenario=derived_no_scenario(saved,enabled=True)
    assert Decimal(scenario['price'])==Decimal('.65')
    assert scenario['execution_eligible'] is False and scenario['net_pnl'] is None
    assert 'not a recorded NO ask' in scenario['label']
    assert derived_no_scenario({k:v for k,v in saved.items() if k!='best_bid'},enabled=True) is None


def test_fresh_public_books_use_direct_share_minimum_and_metadata_revision_keeps_depth(monkeypatch):
    import paper_worker as worker
    stamp=dt.datetime.now(dt.timezone.utc)
    raw=dict(asset_id='own-no',timestamp=str(int(stamp.timestamp()*1000)),
        bids=[dict(price='.3',size='100')],asks=[dict(price='.65',size='100')],tick_size='.001',min_order_size='7.5')
    market=dict(conditionId='own-condition',clobTokenIds=['own-yes','own-no'],outcomes=['Yes','No'],
        feesEnabled=False,orderMinSize='999',active=True,closed=False,acceptingOrders=True)
    monkeypatch.setattr(worker,'public_json',lambda url,params:raw if url.endswith('/book') else [market])
    monkeypatch.setattr(worker,'rest',lambda *a,**k:[dict(condition_id='own-condition')])
    receipts=[];monkeypatch.setattr(worker,'upsert',lambda *a:receipts.append(a))
    order=dict(band_id='original-band',token_id='own-no',side='NO')
    first=worker.capture_book(order)
    assert first['min_order_size']=='7.5' and first['min_order_size_unit']=='shares'
    market.update(feesEnabled=True,feeSchedule=dict(exponent=1,rate='.05'))
    second=worker.capture_book(order)
    assert first['depth_hash']==second['depth_hash'] and first['snapshot_id']!=second['snapshot_id']
    assert len(receipts)==2
    with pytest.raises(ValueError,match='token_identity'):
        worker.capture_book({**order,'side':'YES'})


def test_consumption_checks_old_and_new_snapshot_identity_and_never_silently_truncates(monkeypatch):
    import paper_worker as worker
    seen=[]
    def read(table,params):
        seen.append(params)
        return [dict(result=dict(fills=[dict(price='.65',shares='2')]))]
    monkeypatch.setattr(worker,'rest',read)
    order=dict(account_id='account',token_id='own-no',action='BUY')
    assert Decimal(worker.consumed_depth(order,'profile-id','legacy-depth')['0.65'])==2
    assert 'legacy-depth' in seen[0]['or'] and seen[0]['limit']==501
    monkeypatch.setattr(worker,'rest',lambda *args:[{}]*501)
    with pytest.raises(ValueError,match='truncated'):
        worker.consumed_depth(order,'profile-id','legacy-depth')
