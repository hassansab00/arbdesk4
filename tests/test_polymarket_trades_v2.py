"""Retired v1 cannot be repaired by a URL substitution: real v2 contracts."""
import datetime as dt

import pytest

import ingest_trades as it
import polymarket_trades as api
from test_ingest_trades import _wire, _wire_backfill, _trade, _Resp, NOW, SEEN


def envelope(rows, cursor=None):
    return dict(data=rows,pagination=dict(has_more=cursor is not None,next_cursor=cursor))


def test_snake_case_rows_keep_the_existing_transaction_deduplication_identity():
    r=dict(token_id='token',condition_id='condition',proxy_wallet='wallet',transaction_hash='hash',
           timestamp=100,price=.4,size=12,side='SELL')
    rows,unmatched=it.to_rows([r],{'token':dict(band_id='band',condition_id='condition',markets=dict(city_key='city'))},'receipt')
    assert unmatched==0
    assert rows[0]['transaction_hash']=='hash' and rows[0]['proxy_wallet']=='wallet'
    assert rows[0]['token_id']=='token' and rows[0]['condition_id']=='condition'


def test_short_and_empty_pages_are_not_terminal_without_a_null_cursor():
    calls=[]
    answers=[envelope([_trade(30)],'one'),envelope([],'two'),envelope([_trade(20)])]
    def fetch(url,params,timeout):
        calls.append(dict(params));return answers.pop(0)
    out=api.walk(['0xc1'],fetch=fetch)
    assert [r['timestamp'] for r in out]==[30,20]
    assert [c.get('cursor') for c in calls]==[None,'one','two']
    assert all(c['condition']=='0xc1' and c['taker_only']=='true' for c in calls)
    assert all(not {'offset','market','start','end'} & c.keys() for c in calls)


@pytest.mark.parametrize('answer',[
    [],dict(data=[],pagination={}),
    dict(data=[],pagination=dict(has_more=True,next_cursor=None)),
    dict(data=[],pagination=dict(has_more=False,next_cursor='one')),
    envelope([_trade(20,cond='wrong')]),envelope([_trade(20),_trade(30)]),
])
def test_invalid_protocol_or_cohort_never_qualifies_as_a_complete_tape(answer):
    with pytest.raises(ValueError):api.walk(['0xc1'],fetch=lambda *args:answer)


def test_a_repeated_cursor_refuses_instead_of_spinning_or_skipping():
    with pytest.raises(ValueError,match='repeated'):
        api.walk(['0xc1'],fetch=lambda *args:envelope([],'same'))


def test_empty_and_oversized_cohorts_cannot_accidentally_read_the_global_feed():
    for ids in ([],[f'condition-{i}' for i in range(21)]):
        with pytest.raises(ValueError):api.walk(ids,fetch=lambda *args:pytest.fail('network should not start'))


def test_failed_insert_cannot_advance_the_saved_page_cursor(monkeypatch):
    _wire(monkeypatch,2*it.BATCH,deep_batches={0})
    receipts=[]
    monkeypatch.setattr(it,'rpc',lambda name,payload=None:receipts.append((name,payload)))
    def fail(rows):raise RuntimeError('insert unavailable')
    monkeypatch.setattr(it,'insert_new',fail)
    out=it.main(now=NOW)
    assert receipts[0][0]=='log_ingest'
    assert receipts[0][1]['p_detail']['complete'] is False
    assert out['status']=='error' and out['cursor']=='' and out['api_continuation'] is None


def test_a_missing_durable_progress_receipt_prevents_all_trade_inserts(monkeypatch):
    _wire(monkeypatch,1)
    def fail(*args):raise RuntimeError('progress receipt unavailable')
    monkeypatch.setattr(it,'rpc',fail)
    monkeypatch.setattr(it,'insert_new',lambda rows:pytest.fail('no writes before progress receipt'))
    with pytest.raises(RuntimeError,match='progress'):it.main(now=NOW)


def test_first_ever_incomplete_cycle_keeps_an_unbounded_mark_after_partial_inserts():
    watermark=dt.datetime.fromtimestamp(10_000,dt.timezone.utc)
    assert it.plan(watermark,dict(complete=False,since=None,cursor='condition'))==(None,'condition')


def test_partial_discovery_backfill_keeps_the_market_and_opaque_cursor(monkeypatch):
    _wire_backfill(monkeypatch,{'m1':(SEEN,2)})
    continuation=dict(ids=['0xm100','0xm101'],chunk=0,cursor='next-page')
    def partial(*args,**kwargs):
        return api.Tape([_trade(SEEN.timestamp(),asset='m1-y0',cond='0xm100')],continuation=continuation),False
    monkeypatch.setattr(it,'fetch_window',partial)
    result=it.backfill(now=SEEN+dt.timedelta(hours=5))
    assert result['status']=='attention' and result['read']==0
    assert result['pending']==['m1'] and result['api_continuations']=={'m1':continuation}


def test_wide_window_chunks_keep_the_unfinished_chunk_not_just_its_cursor():
    ids=[f'condition-{i}' for i in range(25)]
    clock=[0.]
    def fetch(url,params,timeout):
        clock[0]+=1
        return envelope([])
    first=api.window(ids,0,100,fetch=fetch,wait_until=1,clock=lambda:clock[0])
    assert first.continuation==dict(ids=ids,chunk=1,cursor=None)
    cohorts=[]
    def finish(url,params,timeout):
        cohorts.append(params['condition']);return envelope([])
    final=api.window(ids,0,100,fetch=finish,continuation=first.continuation)
    assert final.continuation is None and cohorts==[','.join(ids[20:])]
