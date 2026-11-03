"""Void RPC receipts must acknowledge a write without decoding absent JSON."""
import json
import time

import pytest
import requests

import common
import ingest_trades


@pytest.mark.parametrize('job',['P0.4_trade_history',ingest_trades.BACKFILL_JOB])
def test_both_collectors_accept_a_successful_void_progress_receipt(monkeypatch,job):
    calls=[]
    response=requests.Response()
    response.status_code=204
    response._content=b''
    monkeypatch.setattr(common,'_cfg',lambda:{'url':'https://synthetic.test'})
    monkeypatch.setattr(common,'_headers',lambda:{})
    def post(url,**kwargs):
        calls.append((url,json.loads(kwargs['data'])))
        return response
    monkeypatch.setattr(common,'_post',post)
    ingest_trades.preserve_progress(job,{'since':'original'},time.monotonic()+10)
    assert calls==[('https://synthetic.test/rest/v1/rpc/log_ingest',
        {'p_job':job,'p_status':'attention','p_rows':0,
         'p_detail':{'since':'original','phase':'collection_started'}})]


@pytest.mark.parametrize('status,body,expected',[(200,b'{"new":3}',{'new':3}),(200,b'null',None)])
def test_non_void_json_responses_are_unchanged(monkeypatch,status,body,expected):
    response=requests.Response();response.status_code=status;response._content=body
    monkeypatch.setattr(common,'_cfg',lambda:{'url':'https://synthetic.test'})
    monkeypatch.setattr(common,'_headers',lambda:{})
    monkeypatch.setattr(common,'_post',lambda *a,**k:response)
    assert common.rpc('synthetic')==expected


@pytest.mark.parametrize('status',[200,500])
def test_invalid_non_void_body_and_http_failure_still_fail(monkeypatch,status):
    response=requests.Response();response.status_code=status;response._content=b'synthetic failure'
    monkeypatch.setattr(common,'_cfg',lambda:{'url':'https://synthetic.test'})
    monkeypatch.setattr(common,'_headers',lambda:{})
    monkeypatch.setattr(common,'_post',lambda *a,**k:response)
    with pytest.raises(ValueError if status==200 else requests.HTTPError):
        common.rpc('synthetic')
