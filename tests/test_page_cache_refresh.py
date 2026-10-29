import pytest
import common
import page_cache_refresh as refresh


def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(refresh.time, 'monotonic', lambda:now[0])
    monkeypatch.setattr(refresh.time, 'sleep', lambda n:now.__setitem__(0,now[0]+n))


def test_short_requests_wait_for_a_real_completion_and_never_resubmit(monkeypatch):
    clock(monkeypatch)
    calls = []
    replies = iter([{'status':'queued'}, {'status':'waiting'}, {'status':'ok','detail':{'ms':{'mv':42}}}])
    def rpc(fn, params, timeout):
        calls.append((fn,params,timeout))
        return next(replies)
    assert refresh.refresh(270,request_id='same-request',rpc_fn=rpc) == {'mv':42}
    assert [c[0] for c in calls] == ['request_page_cache_refresh','page_cache_refresh_status','page_cache_refresh_status']
    assert all(c[1]=={'p_request_id':'same-request'} and c[2]<=30 for c in calls)


@pytest.mark.parametrize('receipt', [
    {'status':'error','detail':{'error':'broken view'}}, {'status':'missing'},
    {'status':'unknown'}, {'status':'ok','detail':{'ms':{'skipped':'already running'}}},
    {'status':'ok','detail':{'ms':None}},
])
def test_a_failure_or_skipped_refresh_never_claims_success(monkeypatch,receipt):
    clock(monkeypatch)
    with pytest.raises(RuntimeError):refresh.refresh(request_id='request',rpc_fn=lambda *a,**k:receipt)


def test_a_deadline_names_the_existing_request_instead_of_launching_another(monkeypatch):
    clock(monkeypatch)
    calls = []
    with pytest.raises(TimeoutError,match='existing-request'):
        refresh.refresh(11,request_id='existing-request',rpc_fn=lambda *a,**k:calls.append(a[0]) or {'status':'queued'})
    assert calls.count('request_page_cache_refresh') == 1


def test_common_routes_only_the_long_refresh_through_the_handoff(monkeypatch):
    monkeypatch.setattr(refresh,'refresh',lambda timeout,**kw:('acknowledged',timeout))
    assert common.rpc('refresh_page_cache',timeout=270) == ('acknowledged',270)


def test_completion_committed_at_the_deadline_gets_a_final_read(monkeypatch):
    clock(monkeypatch)
    calls = []
    def rpc(fn, params, timeout):
        calls.append(fn)
        if refresh.time.monotonic() >= 11:
            return {'status': 'ok', 'detail': {'ms': {'mv': 9}}}
        return {'status': 'queued'}
    assert refresh.refresh(11, request_id='completed-at-boundary', rpc_fn=rpc) == {'mv': 9}
    assert calls.count('request_page_cache_refresh') == 1
    assert calls[-1] == 'page_cache_refresh_status'


def test_ambiguous_submission_retries_with_the_identical_id(monkeypatch):
    import requests
    clock(monkeypatch)
    submissions = []
    def rpc(fn, params, timeout):
        if fn=='request_page_cache_refresh':
            submissions.append(params.copy())
            if len(submissions)==1:raise requests.ReadTimeout('response lost after submission')
            return {'status':'queued'}
        return {'status':'ok','detail':{'ms':{'mv':12}}}
    assert refresh.refresh(270,request_id='stable-id',rpc_fn=rpc)=={'mv':12}
    assert submissions==[{'p_request_id':'stable-id'}]*2


def test_permission_errors_are_not_retried_or_reported_as_success(monkeypatch):
    import requests
    from types import SimpleNamespace
    clock(monkeypatch)
    calls=[]
    def rpc(*args,**kw):
        calls.append(args)
        raise requests.HTTPError('permission denied',response=SimpleNamespace(status_code=403))
    with pytest.raises(RuntimeError,match='request-id'):
        refresh.refresh(request_id='request-id',rpc_fn=rpc)
    assert len(calls)==1


def test_the_real_rpc_entry_uses_only_short_request_and_status_endpoints(monkeypatch):
    import json
    from types import SimpleNamespace
    clock(monkeypatch)
    wire=common.rpc.__globals__
    monkeypatch.setitem(wire,'_cfg',lambda:{'url':'https://fixture.invalid'})
    monkeypatch.setitem(wire,'_headers',lambda:{'test':'fixture'})
    calls=[]
    def post(url,**kw):
        calls.append((url,json.loads(kw['data']),kw['timeout']))
        body={'status':'queued'} if url.endswith('/request_page_cache_refresh') else {'status':'ok','detail':{'ms':{'mv':8}}}
        return SimpleNamespace(status_code=200,json=lambda:body)
    monkeypatch.setitem(wire,'_post',post)
    assert common.rpc('refresh_page_cache',timeout=270)=={'mv':8}
    assert len(calls)==2 and all(c[2]<=15 for c in calls)
    assert calls[0][1]==calls[1][1]
    assert all(not c[0].endswith('/rpc/refresh_page_cache') for c in calls)


def test_install_and_migration_are_identical():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root/'sql/ad4_page_cache_requests.sql').read_text() == (root/'supabase/migrations/20261019083757_page_cache_background_receipts.sql').read_text()


def test_every_existing_refresh_caller_leaves_queue_and_execution_time():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    restore=(root/'tools/restore_edge_marks.py').read_text()
    assert "rpc('refresh_page_cache', {}, timeout=270)" in restore
    assert 'rpc("refresh_page_cache", timeout=270)' in (root/'.github/workflows/pipeline_daily.yml').read_text()
    intraday=(root/'.github/workflows/pipeline_intraday.yml').read_text()
    assert 'from page_cache_refresh import request' in intraday
    assert 'receipt = request()' in intraday
    assert 'rpc("refresh_page_cache", timeout=270)' not in intraday


@pytest.mark.parametrize('state',['queued','running','waiting'])
def test_submission_returns_acceptance_without_polling_or_claiming_completion(monkeypatch,state):
    clock(monkeypatch)
    calls=[]
    receipt={'request_id':'durable-id','status':state}
    def rpc(name,params,timeout):
        calls.append((name,params,timeout))
        return receipt
    assert refresh.request(request_id='durable-id',rpc_fn=rpc)==receipt
    assert calls==[('request_page_cache_refresh',{'p_request_id':'durable-id'},15)]
    assert refresh.time.monotonic()==0


def test_submission_retry_reuses_the_id_after_ambiguous_transport_failure(monkeypatch):
    clock(monkeypatch)
    calls=[]
    def rpc(name,params,timeout):
        calls.append((name,params))
        if len(calls)==1: raise refresh.requests.ReadTimeout('acknowledgement lost')
        return {'request_id':'stable-id','status':'queued'}
    assert refresh.request(request_id='stable-id',rpc_fn=rpc)['status']=='queued'
    assert calls==[('request_page_cache_refresh',{'p_request_id':'stable-id'})]*2


@pytest.mark.parametrize('status',[520,521])
def test_gateway_restart_retry_preserves_the_same_durable_request(monkeypatch,status):
    from types import SimpleNamespace
    clock(monkeypatch)
    calls=[]
    def rpc(name,params,timeout):
        calls.append((name,params.copy()))
        if len(calls)==1:
            raise refresh.requests.HTTPError('gateway unavailable',response=SimpleNamespace(status_code=status))
        return {'request_id':'stable-id','status':'queued'}
    assert refresh.request(request_id='stable-id',rpc_fn=rpc)['status']=='queued'
    assert calls==[('request_page_cache_refresh',{'p_request_id':'stable-id'})]*2


def test_persistent_gateway_failure_stays_a_failure_after_bounded_retries(monkeypatch):
    from types import SimpleNamespace
    clock(monkeypatch)
    calls=[]
    def rpc(name,params,timeout):
        calls.append(params.copy())
        raise refresh.requests.HTTPError('gateway unavailable',response=SimpleNamespace(status_code=521))
    with pytest.raises(RuntimeError,match='transport failed'):
        refresh.request(request_id='stable-id',rpc_fn=rpc)
    assert calls==[{'p_request_id':'stable-id'}]*3


@pytest.mark.parametrize('receipt',[
    None,{}, {'request_id':'different-id','status':'queued'},
    {'request_id':'id','status':'error'}, {'request_id':'id','status':'missing'},
    {'request_id':'id','status':'unknown'}, {'request_id':'id','status':'ok'},
    {'request_id':'id','status':'ok','detail':{'ms':{'skipped':'busy'}}},
])
def test_unacknowledged_or_failed_submissions_still_fail(monkeypatch,receipt):
    clock(monkeypatch)
    with pytest.raises(RuntimeError):
        refresh.request(request_id='id',rpc_fn=lambda *a,**k:receipt)


def test_submission_can_report_a_previously_completed_id(monkeypatch):
    clock(monkeypatch)
    receipt={'request_id':'id','status':'ok','detail':{'ms':{'mv':12}}}
    assert refresh.request(request_id='id',rpc_fn=lambda *a,**k:receipt)==receipt
