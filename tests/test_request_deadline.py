import pytest
import common


def test_engine_requests_stop_at_their_own_deadline_and_then_reset(monkeypatch):
    now, calls = [100.], []
    monkeypatch.setattr(common.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(common.requests, 'get', lambda *a, **k: calls.append(k))
    with pytest.raises(TimeoutError):
        with common.request_deadline(110.):
            common._get('https://example.test', timeout=90)
            now[0] = 110.
            common._get('https://example.test', timeout=90)
    assert len(calls) == 1 and calls[0]['timeout'] == 5.
    common._get('https://example.test', timeout=90)
    assert calls[-1]['timeout'] == 90


def test_tighter_timeouts_and_nested_deadlines_are_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(common.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(common.requests, 'post', lambda *a, **k: calls.append(k))
    with common.request_deadline(110.):
        with common.request_deadline(120.):
            common._post('https://example.test', timeout=(1., None))
        common._post('https://example.test', timeout=2.)
    assert [c['timeout'] for c in calls] == [(1., 5.), 2.]


def test_retry_wait_cannot_consume_the_capture_reserve(monkeypatch):
    waits = []
    monkeypatch.setattr(common.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(common.time, 'sleep', waits.append)
    monkeypatch.setattr(common, '_config', {'url': 'https://example.test', 'key': 'test'})
    class Response:
        status_code, text, headers = 429, 'limited', {'Retry-After': '60'}
    monkeypatch.setattr(common.requests, 'get', lambda *a, **k: Response())
    with common.request_deadline(110.), pytest.raises(TimeoutError):
        common.rest('anything')
    assert not waits


def test_other_threads_do_not_inherit_the_engine_request_budget():
    from concurrent.futures import ThreadPoolExecutor
    with common.request_deadline(123.):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(common._request_end.get).result() is None
