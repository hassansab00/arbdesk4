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
    for name in ('pipeline_daily.yml','pipeline_intraday.yml'):
        assert 'rpc("refresh_page_cache", timeout=270)' in (root/'.github/workflows'/name).read_text()
