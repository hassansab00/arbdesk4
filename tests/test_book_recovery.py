import datetime as dt
import json
from types import SimpleNamespace

import pytest

import book_recovery as b

NOW=dt.datetime(2026,10,24,12,tzinfo=dt.timezone.utc)
BAND=dict(band_id='band',condition_id='condition',token_yes='yes',token_no='no')


def book(token='yes',condition='condition'):
    return dict(asset_id=token,market=condition,
        bids=[dict(price='.30',size='20'),dict(price='.40',size='10')],
        asks=[dict(price='.55',size='20'),dict(price='.50',size='10')])


def wire(monkeypatch, responses, write=None):
    calls=[];rows=[];logs=[]
    def post(url,**kwargs):
        tokens=[item['token_id'] for item in json.loads(kwargs['data'])]
        calls.append(tokens)
        data=responses[len(calls)-1]
        if isinstance(data,Exception): raise data
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(b,'_post',post)
    monkeypatch.setattr(b,'insert',write or (lambda table,data:rows.extend(data)))
    monkeypatch.setattr(b,'log_run',lambda *args:logs.append(args))
    return calls,rows,logs


def test_failed_tokens_only_are_retried_and_full_depth_is_appended(monkeypatch):
    calls,rows,logs=wire(monkeypatch,[[book()],[book('no')]])
    result,detail=b.recover([BAND],{},NOW,dt.timedelta(hours=2))
    assert calls==[['no','yes'],['no']]
    assert detail['recovered']==1 and detail['failed']==0
    assert result==rows and logs[-1][1]=='ok'
    row=rows[0]
    assert row['best_bid']==.4 and row['best_ask']==.5
    assert row['ask_usd_1c']==5 and row['ask_total_usd']==16
    assert row['no_best_bid']==.4 and row['no_book']['asks'][0]['price']==.5
    assert len(row['raw_book']['asks'])==2


def test_fresh_complete_books_are_not_requested_or_rewritten(monkeypatch):
    calls,rows,logs=wire(monkeypatch,[])
    result,detail=b.recover([BAND],{'band':dict(observed_at=NOW.isoformat(),no_book={'asks':[]})},NOW,dt.timedelta(hours=2))
    assert not calls and not rows and not result and detail['needed']==0


@pytest.mark.parametrize('bad',[book('other'),book(condition='wrong'),dict(book(),asks=[] ,bids=[])])
def test_unmatched_or_empty_books_cannot_repair_a_snapshot(monkeypatch,bad):
    calls,rows,logs=wire(monkeypatch,[[bad,book('no')],[bad]])
    result,detail=b.recover([BAND],{},NOW,dt.timedelta(hours=2))
    assert not rows and not result and detail['failed']==1 and logs[-1][1]=='attention'


def test_no_missing_token_is_invented_from_the_yes_complement(monkeypatch):
    calls,rows,logs=wire(monkeypatch,[[book()],[]])
    result,detail=b.recover([BAND],{},NOW,dt.timedelta(hours=2))
    assert not result and not rows and detail['failed']==1


def test_unacknowledged_append_never_replaces_the_in_memory_book(monkeypatch):
    def failure(*args):raise RuntimeError('write failed')
    calls,rows,logs=wire(monkeypatch,[[book(),book('no')]],write=failure)
    result,detail=b.recover([BAND],{},NOW,dt.timedelta(hours=2))
    assert result==[] and detail['recovered']==0 and detail['failed']==1


def test_normalized_view_uses_its_exact_snapshot_for_no_side_status(monkeypatch):
    calls,rows,logs=wire(monkeypatch,[])
    reads=[]
    def read(table,params):
        reads.append((table,dict(params)))
        return [dict(snapshot_id=17,no_book={'asks':[{'price':.5,'size':10}]})]
    monkeypatch.setattr(b,'rest',read)
    result,detail=b.recover([BAND],{'band':dict(snapshot_id=17,observed_at=NOW.isoformat())},NOW,dt.timedelta(hours=2))
    assert not calls and not rows and not result and detail['needed']==0
    assert reads==[('book_snapshots',{'select':'snapshot_id,no_book','snapshot_id':'in.(17)'})]
